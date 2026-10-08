import csv
import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import duckdb

import workflow.database as database
from workflow.stac_reconciliation import (
    check_stac_item,
    reconcile_campaign,
    stac_item_url,
    write_reconciliation_report,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATASET_ID = "CMIP6Plus.TIPMIP.IPSL.MODEL.exp.r1i1p1f1.Amon.tas.gr#20260318"
ITEM_ID = "CMIP6Plus.TIPMIP.IPSL.MODEL.exp.r1i1p1f1.Amon.tas.gr.v20260318"


def response(status, payload=None):
    content = json.dumps(payload).encode() if payload is not None else b""

    def raise_for_status():
        if status >= 400:
            import requests
            raise requests.HTTPError(f"HTTP {status}")

    return SimpleNamespace(
        status_code=status,
        content=content,
        json=lambda: payload,
        raise_for_status=raise_for_status,
    )


def make_database(path, publication_status="SUCCESS"):
    conn = duckdb.connect(str(path))
    conn.execute((PROJECT_ROOT / "db" / "schema.sql").read_text())
    conn.execute(
        """
        INSERT INTO campaigns
        (name, project, activity, institution, mapfile_root)
        VALUES ('campaign', 'CMIP6Plus', 'TIPMIP', 'IPSL', '/maps')
        """
    )
    conn.execute(
        """
        INSERT INTO datasets
        (dataset_id, campaign, project, activity, institution, mapfile,
         publication_status)
        VALUES (?, 'campaign', 'CMIP6Plus', 'TIPMIP', 'IPSL', '/maps/a.map', ?)
        """,
        [DATASET_ID, publication_status],
    )
    conn.close()


def test_stac_item_url_converts_publisher_version_and_escapes_components():
    assert stac_item_url("CMIP6 Plus", "item/id") == (
        "https://search.east.esgf.io/collections/CMIP6%20Plus/items/item%2Fid"
    )


def test_check_classifies_matching_item_as_present():
    dataset = {
        "dataset_id": DATASET_ID,
        "campaign": "campaign",
        "project": "CMIP6Plus",
        "publication_status": "SUCCESS",
        "latest_success": None,
    }
    payload = {
        "type": "Feature",
        "id": ITEM_ID,
        "collection": "CMIP6Plus",
    }
    with patch(
        "workflow.stac_reconciliation.requests.get",
        return_value=response(200, payload),
    ):
        result = check_stac_item(dataset, 15, 0, 600)

    assert result["outcome"] == "PRESENT"
    assert result["response_item_id"] == ITEM_ID


def test_reconcile_persists_absent_result(tmp_path, monkeypatch):
    database_path = tmp_path / "stac.duckdb"
    make_database(database_path)
    monkeypatch.setattr(database, "DB_PATH", database_path)
    database._MIGRATED_DATABASES.discard(str(database_path))
    with patch(
        "workflow.stac_reconciliation.requests.get",
        return_value=response(404),
    ):
        result = reconcile_campaign(
            "campaign",
            scope="successful",
            concurrency=1,
            retries=0,
            grace_seconds=0,
        )

    assert result["counts"] == {"ABSENT": 1}
    conn = duckdb.connect(str(database_path))
    assert conn.execute(
        "SELECT stac_status, stac_http_status FROM datasets"
    ).fetchone() == ("ABSENT", 404)
    assert conn.execute(
        "SELECT outcome, item_id FROM stac_reconciliation_attempts"
    ).fetchone() == ("ABSENT", ITEM_ID)
    conn.close()


def test_recent_success_waits_without_calling_api():
    dataset = {
        "dataset_id": DATASET_ID,
        "campaign": "campaign",
        "project": "CMIP6Plus",
        "publication_status": "SUCCESS",
        "latest_success": datetime.now(),
    }
    with patch("workflow.stac_reconciliation.requests.get") as request:
        result = check_stac_item(dataset, 15, 0, 600)

    assert result["outcome"] == "WAITING"
    request.assert_not_called()


def test_all_campaigns_selects_every_active_dataset(tmp_path, monkeypatch):
    database_path = tmp_path / "all-campaigns.duckdb"
    make_database(database_path)
    conn = duckdb.connect(str(database_path))
    conn.execute(
        """
        INSERT INTO campaigns
        (name, project, activity, institution, mapfile_root)
        VALUES ('second', 'CMIP6Plus', 'TIPMIP', 'IPSL', '/maps')
        """
    )
    conn.execute(
        """
        INSERT INTO datasets
        (dataset_id, campaign, project, activity, institution, mapfile,
         publication_status)
        VALUES ('second.dataset#1', 'second', 'CMIP6Plus', 'TIPMIP', 'IPSL',
                '/maps/b.map', 'FAILED')
        """
    )
    conn.close()
    monkeypatch.setattr(database, "DB_PATH", database_path)
    database._MIGRATED_DATABASES.discard(str(database_path))
    with patch(
        "workflow.stac_reconciliation.requests.get",
        return_value=response(404),
    ):
        result = reconcile_campaign(
            None,
            concurrency=1,
            retries=0,
            grace_seconds=0,
        )

    assert result["selected"] == 2
    assert result["comparisons"] == {
        "FAILED/ABSENT": 1,
        "SUCCESS/ABSENT": 1,
    }
    assert result["classifications"] == {"CONSISTENT": 1, "DRIFT": 1}


def test_report_contains_drift_and_api_errors_only(tmp_path):
    checked_at = datetime.now()
    base = {
        "campaign": "campaign",
        "collection_id": "CMIP6Plus",
        "item_id": "item",
        "http_status": None,
        "request_url": "https://example.invalid/item",
        "finished_at": checked_at,
        "error_message": None,
    }
    result = {
        "results": [
            {
                **base,
                "dataset_id": "consistent",
                "publication_status": "SUCCESS",
                "outcome": "PRESENT",
            },
            {
                **base,
                "dataset_id": "drift",
                "publication_status": "FAILED",
                "outcome": "PRESENT",
            },
            {
                **base,
                "dataset_id": "api-error",
                "publication_status": "SUCCESS",
                "outcome": "ERROR",
                "error_message": "timeout",
            },
            {
                **base,
                "dataset_id": "waiting",
                "publication_status": "SUCCESS",
                "outcome": "WAITING",
            },
        ],
    }
    report = tmp_path / "mismatches.csv"

    assert write_reconciliation_report(result, report) == 2
    with open(report, newline="") as stream:
        rows = list(csv.DictReader(stream))

    assert [(row["dataset_id"], row["classification"]) for row in rows] == [
        ("drift", "DRIFT"),
        ("api-error", "API_ERROR"),
    ]
