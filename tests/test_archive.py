import csv
import hashlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import duckdb

import workflow.database as database
from workflow.archive import (
    generate_archive_tasks,
    get_archive_path,
    import_archive_results,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def make_archivable_database(path, mapfile):
    checksum = hashlib.sha256(mapfile.read_bytes()).hexdigest()
    conn = duckdb.connect(str(path))
    conn.execute((PROJECT_ROOT / "db" / "schema.sql").read_text())
    conn.execute(
        "INSERT INTO campaigns VALUES (?, ?, ?, ?, ?, ?, ?)",
        ["campaign", "CMIP6", "CMIP", "IPSL", ".", "/archive", "experiment_id"],
    )
    conn.execute(
        """
        INSERT INTO datasets
        (dataset_id, campaign, project, activity, institution, mapfile,
         mapfile_checksum, publication_status)
        VALUES ('dataset', 'campaign', 'CMIP6', 'CMIP', 'IPSL', ?, ?, 'SUCCESS')
        """,
        [str(mapfile), checksum],
    )
    conn.close()
    return checksum


def test_archive_path_uses_campaign_generator_and_configured_depth(tmp_path):
    field_names = [
        "mip_era",
        "activity_id",
        "institution_id",
        "source_id",
        "experiment_id",
        "version",
    ]
    generator = SimpleNamespace(
        directory_specs=SimpleNamespace(
            parts=[
                SimpleNamespace(source_collection=name)
                for name in field_names
            ]
        )
    )
    campaign = {
        "project": "CMIP6",
        "activity": "CMIP",
        "institution": "IPSL",
        "archive_root": str(tmp_path),
        "archive_depth": "experiment_id",
    }
    mapping = {
        "mip_era": "CMIP6",
        "activity_id": "CMIP",
        "institution_id": "IPSL",
        "source_id": "MODEL",
        "experiment_id": "historical",
        "version": "v1",
    }

    with (
        patch("workflow.archive.DrsGenerator", return_value=generator) as factory,
        patch("workflow.archive.parse_drs", return_value=mapping) as parse,
    ):
        result = get_archive_path(
            "dataset-id",
            "/maps/dataset.map",
            campaign,
        )

    factory.assert_called_once_with("cmip6")
    parse.assert_called_once_with("dataset-id", generator)
    assert result == (
        Path(tmp_path)
        / "MODEL"
        / "historical"
        / ".mapfiles"
        / "dataset.map"
    )


def test_archive_results_are_bound_to_persisted_task_identity(tmp_path, monkeypatch):
    database_path = tmp_path / "archive.duckdb"
    mapfile = tmp_path / "dataset.map"
    mapfile.write_text("dataset | /data/file.nc | 1\n")
    checksum = make_archivable_database(database_path, mapfile)
    monkeypatch.setattr(database, "DB_PATH", database_path)
    database._MIGRATED_DATABASES.discard(str(database_path))
    monkeypatch.setattr(
        "workflow.archive.get_campaign",
        lambda name: {
            "name": name,
            "project": "CMIP6",
            "activity": "CMIP",
            "institution": "IPSL",
            "archive_root": "/archive",
            "archive_depth": "experiment_id",
        },
    )
    archive_path = "/archive/dataset.map"
    monkeypatch.setattr(
        "workflow.archive.get_archive_path",
        lambda *args: Path(archive_path),
    )
    task_file = tmp_path / "tasks.csv"

    assert generate_archive_tasks(
        "campaign",
        task_file,
        verify_stac=False,
    ) == 1
    with open(task_file, newline="") as stream:
        task = next(csv.DictReader(stream))
    assert task["mapfile_checksum"] == checksum
    assert task["task_id"]

    result_file = tmp_path / "results.csv"
    with open(result_file, "w", newline="") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=[*task, "status", "error_message"],
        )
        writer.writeheader()
        writer.writerow({**task, "status": "SUCCESS", "error_message": ""})

    counts = import_archive_results(result_file)
    assert counts["SUCCESS"] == 1
    conn = duckdb.connect(str(database_path))
    assert conn.execute(
        "SELECT archive_status FROM datasets WHERE dataset_id = 'dataset'"
    ).fetchone()[0] == "SUCCESS"
    assert conn.execute(
        "SELECT status FROM archive_tasks WHERE task_id = ?",
        [task["task_id"]],
    ).fetchone()[0] == "SUCCESS"
    conn.close()


def test_archive_import_rejects_tampered_task(tmp_path, monkeypatch):
    database_path = tmp_path / "archive-invalid.duckdb"
    mapfile = tmp_path / "dataset.map"
    mapfile.write_text("dataset | /data/file.nc | 1\n")
    make_archivable_database(database_path, mapfile)
    monkeypatch.setattr(database, "DB_PATH", database_path)
    database._MIGRATED_DATABASES.discard(str(database_path))
    conn = database.connect()
    conn.execute(
        """
        INSERT INTO archive_tasks
        (task_id, dataset_id, campaign, mapfile_checksum,
         source_mapfile, archive_path)
        VALUES ('task', 'dataset', 'campaign', 'expected', ?, '/archive/path')
        """,
        [str(mapfile)],
    )
    conn.close()
    result_file = tmp_path / "tampered.csv"
    with open(result_file, "w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=[
            "task_id", "dataset_id", "mapfile_checksum",
            "archive_path", "status",
        ])
        writer.writeheader()
        writer.writerow({
            "task_id": "task",
            "dataset_id": "dataset",
            "mapfile_checksum": "tampered",
            "archive_path": "/archive/path",
            "status": "SUCCESS",
        })

    counts = import_archive_results(result_file)
    assert counts["INVALID_TASK"] == 1
    conn = duckdb.connect(str(database_path))
    assert conn.execute(
        "SELECT archive_status FROM datasets WHERE dataset_id = 'dataset'"
    ).fetchone()[0] == "PENDING"
    conn.close()


def test_archive_generation_is_blocked_when_stac_item_is_absent(
        tmp_path,
        monkeypatch,
):
    database_path = tmp_path / "archive-blocked.duckdb"
    mapfile = tmp_path / "dataset.map"
    mapfile.write_text("dataset | /data/file.nc | 1\n")
    make_archivable_database(database_path, mapfile)
    monkeypatch.setattr(database, "DB_PATH", database_path)
    database._MIGRATED_DATABASES.discard(str(database_path))
    monkeypatch.setattr(
        "workflow.archive.get_campaign",
        lambda name: {
            "name": name,
            "project": "CMIP6",
            "activity": "CMIP",
            "institution": "IPSL",
            "archive_root": "/archive",
            "archive_depth": "experiment_id",
        },
    )
    monkeypatch.setattr(
        "workflow.archive.reconcile_campaign",
        lambda *args, **kwargs: {
            "results": [{"dataset_id": "dataset", "outcome": "ABSENT"}],
        },
    )
    output = tmp_path / "tasks.csv"

    with pytest.raises(ValueError, match="STAC reconciliation.*ABSENT=1"):
        generate_archive_tasks("campaign", output)

    assert not output.exists()
