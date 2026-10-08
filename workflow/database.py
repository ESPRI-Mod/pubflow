import os
import threading
from pathlib import Path
import duckdb

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def get_database_path():
    configured = os.environ.get(
        "PUBFLOW_DB_PATH",
        str(PROJECT_ROOT / "db" / "publications.duckdb"),
    )
    return Path(os.path.expandvars(configured)).expanduser().resolve()


DB_PATH = get_database_path()
_MIGRATED_DATABASES = set()
_MIGRATION_LOCK = threading.Lock()


def connect():
    conn = duckdb.connect(str(DB_PATH))
    database_key = str(DB_PATH)
    if database_key not in _MIGRATED_DATABASES:
        with _MIGRATION_LOCK:
            if database_key not in _MIGRATED_DATABASES:
                if _ensure_compatible_schema(conn):
                    _MIGRATED_DATABASES.add(database_key)
    return conn


def _ensure_compatible_schema(conn):
    """Apply additive migrations needed by current workflow code."""
    tables = {
        row[0]
        for row in conn.execute("SHOW TABLES").fetchall()
    }
    if "datasets" in tables:
        conn.execute(
            "ALTER TABLE datasets ADD COLUMN IF NOT EXISTS "
            "mapfile_checksum VARCHAR"
        )
        conn.execute(
            "ALTER TABLE datasets ADD COLUMN IF NOT EXISTS "
            "registration_status VARCHAR DEFAULT 'ACTIVE'"
        )
        conn.execute(
            "UPDATE datasets SET registration_status = 'ACTIVE' "
            "WHERE registration_status IS NULL"
        )
        conn.execute(
            "ALTER TABLE datasets ADD COLUMN IF NOT EXISTS "
            "retired_at TIMESTAMP"
        )
        conn.execute(
            "ALTER TABLE datasets ADD COLUMN IF NOT EXISTS "
            "archive_status VARCHAR DEFAULT 'PENDING'"
        )
        conn.execute(
            "UPDATE datasets SET archive_status = 'PENDING' "
            "WHERE archive_status IS NULL"
        )
        invalid_dataset_states = conn.execute(
            """
            SELECT dataset_id, registration_status,
                   publication_status, archive_status
            FROM datasets
            WHERE registration_status NOT IN ('ACTIVE', 'RETIRED')
               OR publication_status NOT IN ('PENDING', 'SUCCESS', 'FAILED')
               OR archive_status NOT IN ('PENDING', 'SUCCESS')
            LIMIT 5
            """
        ).fetchall()
        if invalid_dataset_states:
            raise RuntimeError(
                "Database contains invalid dataset states; repair before "
                f"continuing: {invalid_dataset_states}"
            )
        conn.execute(
            "ALTER TABLE datasets ADD COLUMN IF NOT EXISTS "
            "publication_claim_id VARCHAR"
        )
        conn.execute(
            "ALTER TABLE datasets ADD COLUMN IF NOT EXISTS "
            "publication_claimed_at TIMESTAMP"
        )
    if "publication_attempts" in tables:
        conn.execute(
            "ALTER TABLE publication_attempts ADD COLUMN IF NOT EXISTS "
            "attempt_id VARCHAR"
        )
        conn.execute(
            "UPDATE publication_attempts SET attempt_id = uuid()::VARCHAR "
            "WHERE attempt_id IS NULL"
        )
        conn.execute(
            "ALTER TABLE publication_attempts ALTER attempt_id "
            "SET DEFAULT (uuid()::VARCHAR)"
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS publication_attempt_id_idx "
            "ON publication_attempts(attempt_id)"
        )
    if "datasets" in tables:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS archive_tasks
            (
                task_id VARCHAR PRIMARY KEY,
                dataset_id VARCHAR NOT NULL,
                campaign VARCHAR NOT NULL,
                mapfile_checksum VARCHAR NOT NULL,
                source_mapfile VARCHAR NOT NULL,
                archive_path VARCHAR NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                completed_at TIMESTAMP,
                status VARCHAR NOT NULL DEFAULT 'PENDING',
                error_message VARCHAR
            )
            """
        )
    return "datasets" in tables and "publication_attempts" in tables


def get_pending_datasets(conn, campaign, limit=None):
    query = """
            SELECT dataset_id,
                   mapfile

            FROM datasets

            WHERE campaign = ?
              AND registration_status = 'ACTIVE'
              AND publication_status = 'PENDING'

            ORDER BY dataset_id \
            """

    params = [campaign]

    if limit:
        query += " LIMIT ?"
        params.append(limit)

    return conn.execute(
        query,
        params
    ).fetchall()


def update_dataset_status(
        conn,
        dataset_id,
        status,
):
    conn.execute(
        """
        UPDATE datasets

        SET publication_status = ?,
            publication_claim_id = NULL,
            publication_claimed_at = NULL

        WHERE dataset_id = ?
        """,
        [
            status,
            dataset_id,
        ],
    )


def clear_dataset_claim(conn, dataset_id, claim_id=None):
    query = """
        UPDATE datasets
        SET publication_claim_id = NULL,
            publication_claimed_at = NULL
        WHERE dataset_id = ?
    """
    params = [dataset_id]
    if claim_id is not None:
        query += " AND publication_claim_id = ?"
        params.append(claim_id)
    conn.execute(query, params)


def retry_failed_datasets(
        conn,
        campaign,
        limit=None,
):
    query = """
            SELECT dataset_id
            FROM datasets
            WHERE campaign = ?
              AND registration_status = 'ACTIVE'
              AND publication_status = 'FAILED'
            ORDER BY dataset_id \
            """
    params = [campaign]
    if limit is not None:
        query += " LIMIT ?"
        params.append(limit)
    rows = conn.execute(
        query,
        params,
    ).fetchall()
    for (dataset_id,) in rows:
        conn.execute(
            """
            UPDATE datasets
            SET publication_status = 'PENDING'
            WHERE dataset_id = ?
            """,
            [dataset_id],
        )
    conn.commit()
    return len(rows)
