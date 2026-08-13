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
