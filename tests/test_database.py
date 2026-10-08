from pathlib import Path

import duckdb

import workflow.database as database


def test_connect_migrates_existing_publication_schema(tmp_path, monkeypatch):
    database_path = tmp_path / "legacy.duckdb"
    conn = duckdb.connect(str(database_path))
    conn.execute(
        """
        CREATE TABLE datasets (
            dataset_id VARCHAR PRIMARY KEY,
            campaign VARCHAR,
            publication_status VARCHAR
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE publication_attempts (
            dataset_id VARCHAR,
            run_id VARCHAR,
            status VARCHAR
        )
        """
    )
    conn.execute(
        "INSERT INTO publication_attempts VALUES ('dataset', 'run', 'SUCCESS')"
    )
    conn.close()

    monkeypatch.setattr(database, "DB_PATH", Path(database_path))
    database._MIGRATED_DATABASES.discard(str(database_path))
    migrated = database.connect()
    try:
        dataset_columns = {
            row[1]
            for row in migrated.execute("PRAGMA table_info('datasets')").fetchall()
        }
        attempt = migrated.execute(
            "SELECT attempt_id FROM publication_attempts"
        ).fetchone()
        migrated.execute(
            """
            INSERT INTO publication_attempts (dataset_id, run_id, status)
            VALUES ('dataset-2', 'run-2', 'SUCCESS')
            """
        )
        generated = migrated.execute(
            "SELECT attempt_id FROM publication_attempts WHERE dataset_id = 'dataset-2'"
        ).fetchone()
    finally:
        migrated.close()

    assert {
        "mapfile_checksum",
        "registration_status",
        "retired_at",
        "stac_status",
        "stac_checked_at",
        "stac_http_status",
        "publication_claim_id",
        "publication_claimed_at",
    } <= dataset_columns
    assert attempt[0]
    assert generated[0]
    assert attempt[0] != generated[0]
