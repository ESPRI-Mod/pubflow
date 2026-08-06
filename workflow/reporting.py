from workflow.database import connect


def list_campaigns():
    conn = connect()
    rows = conn.execute(
        """
        SELECT name,
               project,
               activity,
               institution,
               mapfile_root

        FROM campaigns

        ORDER BY name
        """
    ).fetchall()
    conn.close()
    return rows


def campaign_summary():
    conn = connect()
    rows = conn.execute(
        """
        SELECT campaign,
               COUNT(*) AS datasets

        FROM datasets

        GROUP BY campaign

        ORDER BY campaign
        """
    ).fetchall()
    conn.close()
    return rows


def publication_summary():
    conn = connect()

    rows = conn.execute(
        """
        SELECT publication_status,
               COUNT(*)

        FROM datasets

        GROUP BY publication_status

        ORDER BY publication_status
        """
    ).fetchall()

    conn.close()

    return rows


def failed_publications():
    conn = connect()

    rows = conn.execute(
        """
        SELECT dataset_id,
               publication_status,
               mapfile

        FROM datasets

        WHERE publication_status != 'PENDING'

        AND publication_status != 'SUCCESS'

        ORDER BY dataset_id
        """
    ).fetchall()

    conn.close()

    return rows
