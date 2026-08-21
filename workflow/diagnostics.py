import csv
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from workflow.config import get_publisher_config
from workflow.database import connect
from workflow.diagnostic_parser import ParsedDiagnostic, parse_server_diagnostic
from workflow.diagnostic_reporting import sync_diagnostics_to_grist
from workflow.executor import (
    build_publish_command,
    record_publication_result,
    rewrite_mapfile,
)
from workflow.publisher_output import (
    parse_publication_statuses,
    publisher_dataset_id,
)


DIAGNOSTICS_SCHEMA = """
CREATE TABLE IF NOT EXISTS diagnostic_attempts
(
    diagnostic_id VARCHAR PRIMARY KEY,
    diagnostic_run_id VARCHAR NOT NULL,
    dataset_id VARCHAR NOT NULL,
    campaign VARCHAR NOT NULL,
    started_at TIMESTAMP,
    finished_at TIMESTAMP,
    outcome VARCHAR NOT NULL,
    publisher_status VARCHAR,
    exit_code INTEGER,
    http_status INTEGER,
    error_type VARCHAR,
    schema_url VARCHAR,
    rejected_value VARCHAR,
    suggested_value VARCHAR,
    summary VARCHAR,
    server_instance VARCHAR,
    log_file VARCHAR,
    stac_file VARCHAR
)
"""


CSV_FIELDS = (
    "diagnostic_id",
    "diagnostic_run_id",
    "dataset_id",
    "campaign",
    "started_at",
    "finished_at",
    "outcome",
    "publisher_status",
    "exit_code",
    "http_status",
    "error_type",
    "schema_url",
    "rejected_value",
    "suggested_value",
    "summary",
    "server_instance",
    "log_file",
    "stac_file",
)


def get_failed_datasets(campaign, limit=None):
    conn = connect()
    try:
        query = """
            SELECT dataset_id, mapfile, publication_status
            FROM datasets
            WHERE campaign = ?
              AND publication_status = 'FAILED'
            ORDER BY dataset_id
        """
        params = [campaign]
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)
        return conn.execute(query, params).fetchall()
    finally:
        conn.close()


def _diagnostic_command(mapfile):
    command = build_publish_command(mapfile)
    map_index = command.index("--map")
    options = []
    if "--verbose" not in command:
        options.append("--verbose")
    if "--save-stac" not in command:
        options.append("--save-stac")
    command[map_index:map_index] = options
    return command


def _safe_filename(dataset_id):
    return dataset_id.replace("/", "_").replace("#", ".v")


def _copy_stac_file(temp_dir, dataset_id, destination_dir):
    expected = Path(temp_dir) / f"{publisher_dataset_id(dataset_id)}.json"
    if not expected.is_file():
        return None
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / expected.name
    shutil.copy2(expected, destination)
    return str(destination)


def _persist_diagnostic(conn, row):
    conn.execute(
        """
        INSERT INTO diagnostic_attempts
        (
            diagnostic_id, diagnostic_run_id, dataset_id, campaign,
            started_at, finished_at, outcome, publisher_status,
            exit_code, http_status, error_type, schema_url, rejected_value,
            suggested_value, summary, server_instance, log_file, stac_file
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [row.get(name) for name in (
            "diagnostic_id", "diagnostic_run_id", "dataset_id", "campaign",
            "started_at", "finished_at", "outcome", "publisher_status",
            "exit_code", "http_status", "error_type", "schema_url",
            "rejected_value", "suggested_value", "summary",
            "server_instance", "log_file", "stac_file",
        )],
    )


def _write_csv(path, rows):
    with open(path, "w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows({name: row.get(name) for name in CSV_FIELDS} for row in rows)


def _run_one(dataset, campaign, run_id, run_dir, persist_stac_item):
    dataset_id, mapfile, _ = dataset
    diagnostic_id = str(uuid4())
    started_at = datetime.now()
    log_file = run_dir / f"{_safe_filename(dataset_id)}.log"
    parsed = ParsedDiagnostic()
    publisher_status = None
    exit_code = -1
    stac_file = None

    try:
        with tempfile.TemporaryDirectory(prefix="pubflow-diagnostic-") as temp_dir:
            effective_mapfile = Path(temp_dir) / Path(mapfile).name
            rewrite_mapfile(mapfile, effective_mapfile)
            command = _diagnostic_command(effective_mapfile)
            completed = subprocess.run(
                command,
                cwd=temp_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                check=False,
            )
            exit_code = completed.returncode
            output = completed.stdout or ""
            log_file.write_text(output)
            statuses, _ = parse_publication_statuses(output, [dataset])
            publisher_status = statuses.get(dataset_id)
            parsed = parse_server_diagnostic(output)
            if persist_stac_item:
                stac_file = _copy_stac_file(
                    temp_dir,
                    dataset_id,
                    run_dir / "stac",
                )

        if publisher_status == "SUCCESS":
            outcome = "RECOVERED"
            summary = "Previously failed dataset published successfully."
        elif publisher_status == "FAILED" and parsed.http_status is not None:
            outcome = "DIAGNOSED"
            summary = parsed.summary
        elif publisher_status == "FAILED":
            outcome = "UNCLASSIFIED"
            summary = parsed.summary
        else:
            outcome = "EXECUTION_ERROR"
            summary = "No recognizable PUB_STATUS was emitted; see diagnostic log."
    except Exception as exc:
        outcome = "EXECUTION_ERROR"
        summary = f"{type(exc).__name__}: {exc}"
        with open(log_file, "a") as stream:
            stream.write(summary + "\n")

    finished_at = datetime.now()
    row = {
        "diagnostic_id": diagnostic_id,
        "diagnostic_run_id": run_id,
        "dataset_id": dataset_id,
        "campaign": campaign,
        "started_at": started_at,
        "finished_at": finished_at,
        "outcome": outcome,
        "publisher_status": publisher_status,
        "exit_code": exit_code,
        "http_status": parsed.http_status,
        "error_type": parsed.error_type,
        "schema_url": parsed.schema_url,
        "rejected_value": parsed.rejected_value,
        "suggested_value": parsed.suggested_value,
        "summary": summary,
        "server_instance": parsed.server_instance,
        "log_file": str(log_file),
        "stac_file": stac_file,
    }

    conn = connect()
    try:
        conn.execute(DIAGNOSTICS_SCHEMA)
        if publisher_status in ("SUCCESS", "FAILED"):
            record_publication_result(
                conn,
                dataset_id,
                run_id,
                publisher_status,
                exit_code,
                log_file,
                None if publisher_status == "SUCCESS" else summary,
            )
        _persist_diagnostic(conn, row)
        conn.commit()
    finally:
        conn.close()

    return row


def run_diagnostics(
        campaign,
        limit=None,
        persist_stac_item=False,
        sync_grist=True,
):
    if limit is not None and limit <= 0:
        raise ValueError("limit must be greater than zero")

    datasets = get_failed_datasets(campaign, limit=limit)
    run_id = f"diagnostics_{campaign}_{datetime.now().strftime('%Y%m%d%H%M%S')}"
    publisher = get_publisher_config()
    run_dir = (
        Path(publisher["logging"]["directory"])
        / campaign
        / "diagnostics"
        / run_id
    )
    run_dir.mkdir(parents=True, exist_ok=True)

    conn = connect()
    try:
        conn.execute(DIAGNOSTICS_SCHEMA)
    finally:
        conn.close()

    rows = []
    for position, dataset in enumerate(datasets, start=1):
        print(f"[{position}/{len(datasets)}] Diagnosing {dataset[0]}")
        row = _run_one(
            dataset,
            campaign,
            run_id,
            run_dir,
            persist_stac_item,
        )
        print(f"  {row['outcome']}: {row['summary']}")
        rows.append(row)

    csv_file = run_dir / "diagnostics.csv"
    _write_csv(csv_file, rows)

    grist_error = None
    if sync_grist and rows:
        try:
            sync_diagnostics_to_grist(rows)
        except Exception as exc:
            grist_error = f"{type(exc).__name__}: {exc}"

    return {
        "run_id": run_id,
        "selected": len(datasets),
        "recovered": sum(row["outcome"] == "RECOVERED" for row in rows),
        "diagnosed": sum(row["outcome"] == "DIAGNOSED" for row in rows),
        "unclassified": sum(row["outcome"] == "UNCLASSIFIED" for row in rows),
        "execution_errors": sum(row["outcome"] == "EXECUTION_ERROR" for row in rows),
        "output_directory": str(run_dir),
        "csv_file": str(csv_file),
        "grist_synced": sync_grist and bool(rows) and grist_error is None,
        "grist_error": grist_error,
    }
