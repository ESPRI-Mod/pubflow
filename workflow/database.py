from pathlib import Path
import duckdb

DB_PATH = Path(
    "/home/esguser/esgf-publisher-workflow/db/publications.duckdb"
)


def connect():
    return duckdb.connect(str(DB_PATH))


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

        SET publication_status = ?

        WHERE dataset_id = ?
        """,
        [
            status,
            dataset_id,
        ],
    )
