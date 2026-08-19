#!/usr/bin/env python3
"""Restore PubFlow publication state from Grist into a rebuilt DuckDB.

Grist is strictly read-only. Dataset statuses and publication attempts are
written only after every dataset referenced by Grist has been found locally.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

try:
    import duckdb
except ImportError as exc:  # pragma: no cover
    raise SystemExit("Missing dependency: install it with 'python -m pip install duckdb'.") from exc

DATASET_FIELDS = (
    "dataset_id", "campaign", "publication_status", "last_attempt_status",
    "finished_at", "log_file",
)
FAILURE_FIELDS = (
    "dataset_id", "campaign", "run_id", "started_at", "finished_at", "status",
    "exit_code", "log_file", "error_message",
)
ATTEMPT_TABLE = "publication_attempts"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path, help="Path to the rebuilt DuckDB database")
    parser.add_argument("--dry-run", action="store_true", help="Validate and report without changing DuckDB")
    parser.add_argument("--datasets-table", default="Datasets", help="Grist datasets table name (default: Datasets)")
    parser.add_argument("--failures-table", default="Failures", help="Grist failures table name (default: Failures)")
    return parser.parse_args()


def required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Required environment variable {name} is not set")
    return value


def grist_records(base_url: str, doc_id: str, api_key: str, table: str) -> list[dict[str, Any]]:
    """Read all records from a Grist table without making any write request."""
    url = (
        f"{base_url.rstrip('/')}/api/docs/{urllib.parse.quote(doc_id, safe='')}"
        f"/tables/{urllib.parse.quote(table, safe='')}/records"
    )
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise RuntimeError(f"Grist returned HTTP {exc.code} for table {table!r}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Could not read Grist table {table!r}: {exc}") from exc

    records = payload.get("records")
    if not isinstance(records, list):
        raise RuntimeError(f"Unexpected Grist response for table {table!r}: missing records list")
    result: list[dict[str, Any]] = []
    for record in records:
        fields = record.get("fields", {})
        if isinstance(fields, dict):
            result.append(fields)
    return result


def quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def table_columns(con: Any, table: str) -> dict[str, dict[str, Any]]:
    rows = con.execute(f"PRAGMA table_info({quote_ident(table)})").fetchall()
    if not rows:
        raise RuntimeError(f"Required DuckDB table {table!r} does not exist")
    return {
        row[1]: {"type": row[2], "notnull": bool(row[3]), "default": row[4], "pk": bool(row[5])}
        for row in rows
    }


def normalize_id(value: Any) -> str:
    return "" if value is None else str(value).strip()


def duplicate_ids(records: Iterable[dict[str, Any]]) -> list[str]:
    counts = Counter(normalize_id(row.get("dataset_id")) for row in records)
    return sorted(key for key, count in counts.items() if key and count > 1)


def print_examples(label: str, values: Iterable[str], limit: int = 20) -> None:
    values = list(values)
    if not values:
        return
    print(f"{label} ({len(values)}):")
    for value in values[:limit]:
        print(f"  - {value}")
    if len(values) > limit:
        print(f"  ... and {len(values) - limit} more")


def value_for_duckdb(value: Any, duckdb_type: str) -> Any:
    """Normalize Grist values for the destination DuckDB column type."""
    if value == "" or value is None:
        return None

    column_type = duckdb_type.upper()
    if "TIMESTAMP" in column_type and isinstance(value, (int, float)):
        # Grist Date/DateTime cells are Unix seconds. Return an aware Python
        # datetime for TIMESTAMPTZ and a naive UTC datetime for TIMESTAMP.
        converted = datetime.fromtimestamp(value, tz=timezone.utc)
        return converted if "WITH TIME ZONE" in column_type or "TIMESTAMPTZ" in column_type else converted.replace(
            tzinfo=None)
    if column_type in {"TINYINT", "SMALLINT", "INTEGER", "BIGINT", "HUGEINT", "UTINYINT", "USMALLINT", "UINTEGER",
                       "UBIGINT"}:
        if isinstance(value, float) and value.is_integer():
            return int(value)
    return value


def attempt_exists(con: Any, columns: list[str], row: dict[str, Any]) -> bool:
    """Compare through DuckDB so parameters receive the table column types."""
    predicates = " AND ".join(
        f"{quote_ident(column)} IS NOT DISTINCT FROM ?" for column in columns
    )
    sql = f"SELECT 1 FROM {quote_ident(ATTEMPT_TABLE)} WHERE {predicates} LIMIT 1"
    return con.execute(sql, [row[column] for column in columns]).fetchone() is not None


def main() -> int:
    args = parse_args()
    if not args.db.is_file():
        raise RuntimeError(f"DuckDB database does not exist: {args.db}")

    base_url = required_env("GRIST_BASE_URL")
    doc_id = required_env("GRIST_DOC_ID")
    api_key = required_env("GRIST_API_KEY")

    print("Reading Grist (read-only)...")
    grist_datasets = grist_records(base_url, doc_id, api_key, args.datasets_table)
    grist_failures = grist_records(base_url, doc_id, api_key, args.failures_table)
    print(f"Grist datasets: {len(grist_datasets)}")
    print(f"Grist failures: {len(grist_failures)}")

    missing_dataset_ids = [i for i, row in enumerate(grist_datasets, 1) if not normalize_id(row.get("dataset_id"))]
    missing_failure_ids = [i for i, row in enumerate(grist_failures, 1) if not normalize_id(row.get("dataset_id"))]
    if missing_dataset_ids or missing_failure_ids:
        print_examples("Dataset-table rows with blank dataset_id", map(str, missing_dataset_ids))
        print_examples("Failure-table rows with blank dataset_id", map(str, missing_failure_ids))
        raise RuntimeError("Grist contains blank dataset_id values; no changes were made")

    duplicates = duplicate_ids(grist_datasets)
    if duplicates:
        print_examples("Duplicate dataset IDs in Grist Datasets", duplicates)
        raise RuntimeError("Grist Datasets must contain one row per dataset; no changes were made")

    con = duckdb.connect(str(args.db), read_only=args.dry_run)
    try:
        dataset_schema = table_columns(con, "datasets")
        attempt_schema = table_columns(con, ATTEMPT_TABLE)
        if "dataset_id" not in dataset_schema:
            raise RuntimeError("DuckDB datasets table has no dataset_id column")

        local_ids = {normalize_id(row[0]) for row in con.execute("SELECT dataset_id FROM datasets").fetchall()}
        dataset_ids = {normalize_id(row["dataset_id"]) for row in grist_datasets}
        failure_ids = {normalize_id(row["dataset_id"]) for row in grist_failures}
        referenced_ids = dataset_ids | failure_ids
        missing_local = sorted(referenced_ids - local_ids)
        local_not_in_grist = sorted(local_ids - dataset_ids)

        print(f"Local datasets: {len(local_ids)}")
        print(f"Matched Grist dataset IDs: {len(dataset_ids & local_ids)}")
        print_examples("Grist-referenced IDs missing locally", missing_local)
        print_examples("Local IDs absent from Grist Datasets (informational)", local_not_in_grist)
        if missing_local:
            raise RuntimeError("Not all Grist dataset IDs exist locally; no changes were made")

        if "publication_status" not in dataset_schema:
            raise RuntimeError("DuckDB datasets table has no publication_status column")

        statuses = Counter(str(row.get("publication_status") or "<NULL>") for row in grist_datasets)
        print("Dataset statuses to restore: " + ", ".join(f"{key}={value}" for key, value in sorted(statuses.items())))

        # Only columns present in PRAGMA output are eligible for insertion.
        attempt_columns = [column for column in FAILURE_FIELDS if column in attempt_schema]
        if "dataset_id" not in attempt_columns:
            raise RuntimeError("DuckDB publication_attempts table has no dataset_id column")
        unsupported = [column for column in FAILURE_FIELDS if column not in attempt_schema]
        if unsupported:
            print("Failure fields unsupported by local schema (skipped): " + ", ".join(unsupported))

        omitted_required = [
            column for column, info in attempt_schema.items()
            if column not in attempt_columns and info["notnull"] and info["default"] is None
        ]
        if omitted_required:
            raise RuntimeError(
                "publication_attempts has required columns Grist cannot populate: "
                + ", ".join(omitted_required)
            )

        failure_rows = [
            {
                column: value_for_duckdb(row.get(column), attempt_schema[column]["type"])
                for column in attempt_columns
            }
            for row in grist_failures
        ]
        new_failure_rows: list[dict[str, Any]] = []
        # Track exact duplicate Grist rows locally; query DuckDB for typed matches.
        seen_source_keys: set[str] = set()
        for row in failure_rows:
            source_key = json.dumps(row, sort_keys=True, default=str)
            if source_key not in seen_source_keys and not attempt_exists(con, attempt_columns, row):
                new_failure_rows.append(row)
            seen_source_keys.add(source_key)

        print(f"Existing/duplicate publication attempts skipped: {len(failure_rows) - len(new_failure_rows)}")
        print(f"New publication attempts to insert: {len(new_failure_rows)}")
        print(f"Dataset statuses to update: {len(grist_datasets)}")
        if args.dry_run:
            print("DRY RUN complete: validation passed; no changes were made.")
            return 0

        con.execute("BEGIN TRANSACTION")
        try:
            for row in grist_datasets:
                con.execute(
                    "UPDATE datasets SET publication_status = ? WHERE dataset_id = ?",
                    [
                        value_for_duckdb(row.get("publication_status"), dataset_schema["publication_status"]["type"]),
                        normalize_id(row["dataset_id"]),
                    ],
                )
            if new_failure_rows:
                names = ", ".join(quote_ident(column) for column in attempt_columns)
                placeholders = ", ".join("?" for _ in attempt_columns)
                sql = f"INSERT INTO {quote_ident(ATTEMPT_TABLE)} ({names}) VALUES ({placeholders})"
                con.executemany(sql, [[row[column] for column in attempt_columns] for row in new_failure_rows])
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise

        print(f"Restore complete: updated {len(grist_datasets)} datasets; inserted {len(new_failure_rows)} attempts.")
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, duckdb.Error) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
