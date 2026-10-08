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
    return "datasets" in tables and "publication_attempts" in tables


def get_pending_datasets(conn, campaign, limit=None):
    query = """
            SELECT dataset_id,
                   mapfile

            FROM datasets

            WHERE campaign = ?
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
