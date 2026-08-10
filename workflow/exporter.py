import csv

from workflow.database import connect
from workflow.grist import add_records, get_records, update_records


def get_campaign_status_rows():
    conn = connect()
    rows = conn.execute(
        """
        WITH latest_attempt AS (SELECT dataset_id,
                                       status AS    last_attempt_status,
                                       finished_at,
                                       log_file,
                                       ROW_NUMBER() OVER (
                    PARTITION BY dataset_id
                    ORDER BY started_at DESC
                ) AS rn
                                FROM publication_attempts)

        SELECT d.dataset_id,
               d.campaign,
               d.publication_status,
               a.last_attempt_status,
               a.finished_at,
               a.log_file

        FROM datasets d

                 LEFT JOIN latest_attempt a
                           ON d.dataset_id = a.dataset_id
                               AND a.rn = 1

        ORDER BY d.campaign, d.dataset_id
        """
    ).fetchall()
    conn.close()
    return rows


def get_campaign_summary_rows():
    conn = connect()
    rows = conn.execute(
        """
        SELECT campaign,
               COUNT(*) AS total,

               SUM(
                       CASE
                           WHEN publication_status = 'PUBLISHED'
                               THEN 1
                           ELSE 0
                           END
               )        AS published,

               SUM(
                       CASE
                           WHEN publication_status = 'FAILED'
                               THEN 1
                           ELSE 0
                           END
               )        AS failed,

               SUM(
                       CASE
                           WHEN publication_status = 'PENDING'
                               THEN 1
                           ELSE 0
                           END
               )        AS pending

        FROM datasets

        GROUP BY campaign

        ORDER BY campaign
        """
    ).fetchall()
    conn.close()
    return rows


def get_failure_rows(campaign=None):
    conn = connect()
    query = """
            SELECT p.dataset_id, \
                   d.campaign, \
                   p.run_id, \
                   p.started_at, \
                   p.finished_at, \
                   p.status, \
                   p.exit_code, \
                   p.log_file, \
                   p.error_message

            FROM publication_attempts p

                     JOIN datasets d
                          ON p.dataset_id = d.dataset_id

            WHERE p.status = 'FAILED' \
            """
    params = []
    if campaign:
        query += " AND d.campaign = ?"
        params.append(campaign)
    query += """
        ORDER BY p.started_at DESC
    """
    rows = conn.execute(
        query,
        params,
    ).fetchall()
    conn.close()
    return rows


def export_campaign_status(filename):
    rows = get_campaign_status_rows()
    with open(
            filename,
            "w",
            newline="",
    ) as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "dataset_id",
                "campaign",
                "publication_status",
                "last_attempt_status",
                "finished_at",
                "log_file",
            ]
        )
        writer.writerows(rows)


def export_campaign_summary(filename):
    rows = get_campaign_summary_rows()
    with open(
            filename,
            "w",
            newline="",
    ) as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "campaign",
                "total",
                "published",
                "failed",
                "pending",
            ]
        )
        writer.writerows(rows)


def export_failures(filename, campaign=None):
    rows = get_failure_rows(campaign)
    with open(
            filename,
            "w",
            newline="",
    ) as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "dataset_id",
                "campaign",
                "run_id",
                "started_at",
                "finished_at",
                "status",
                "exit_code",
                "log_file",
                "error_message",
            ]
        )
        writer.writerows(rows)


def campaign_status_records():
    rows = get_campaign_status_rows()
    return [
        {
            "dataset_id": row[0],
            "campaign": row[1],
            "publication_status": row[2],
            "last_attempt_status": row[3],
            "finished_at": row[4],
            "log_file": row[5],
        }
        for row in rows
    ]


def campaign_summary_records():
    rows = get_campaign_summary_rows()
    return [
        {
            "campaign": row[0],
            "total": row[1],
            "published": row[2],
            "failed": row[3],
            "pending": row[4],
        }
        for row in rows
    ]


def failure_records(campaign=None):
    rows = get_failure_rows(campaign)
    return [
        {
            "dataset_id": row[0],
            "campaign": row[1],
            "run_id": row[2],
            "started_at": row[3],
            "finished_at": row[4],
            "status": row[5],
            "exit_code": row[6],
            "log_file": row[7],
            "error_message": row[8],
        }
        for row in rows
    ]


def sync_campaigns_to_grist(
        rows,
):
    table_id = "Campaigns"

    existing = get_records(
        table_id
    )

    existing_by_campaign = {}

    for record in existing.get(
            "records",
            [],
    ):
        fields = record.get(
            "fields",
            {},
        )

        campaign = fields.get(
            "campaign"
        )

        if campaign:
            existing_by_campaign[
                campaign
            ] = record["id"]

    creates = []
    updates = []

    for row in rows:
        fields = {
            "campaign": row["campaign"],
            "total": row["total"],
            "published": row["published"],
            "failed": row["failed"],
            "pending": row["pending"],
        }

        record_id = existing_by_campaign.get(
            row["campaign"]
        )

        if record_id is None:
            creates.append(
                {
                    "fields": fields,
                }
            )
        else:
            updates.append(
                {
                    "id": record_id,
                    "fields": fields,
                }
            )

    if creates:
        add_records(
            table_id,
            creates,
        )

    if updates:
        update_records(
            table_id,
            updates,
        )


def sync_datasets_to_grist(
        rows,
):
    table_id = "Datasets"

    existing = get_records(
        table_id
    )

    existing_by_dataset = {}

    for record in existing.get(
            "records",
            [],
    ):
        fields = record.get(
            "fields",
            {},
        )

        dataset_id = fields.get(
            "dataset_id"
        )

        if dataset_id:
            existing_by_dataset[
                dataset_id
            ] = record["id"]

    creates = []
    updates = []

    for row in rows:
        fields = {
            "dataset_id": row["dataset_id"],
            "campaign": row["campaign"],
            "publication_status": row[
                "publication_status"
            ],
            "last_attempt_status": row[
                "last_attempt_status"
            ],
            "finished_at": row[
                "finished_at"
            ],
            "log_file": row["log_file"],
        }

        record_id = existing_by_dataset.get(
            row["dataset_id"]
        )

        if record_id is None:
            creates.append(
                {
                    "fields": fields,
                }
            )
        else:
            updates.append(
                {
                    "id": record_id,
                    "fields": fields,
                }
            )

    if creates:
        add_records(
            table_id,
            creates,
        )

    if updates:
        update_records(
            table_id,
            updates,
        )


def sync_failures_to_grist(
        rows,
):
    table_id = "Failures"

    existing = get_records(
        table_id
    )

    existing_by_key = {}

    for record in existing.get(
            "records",
            [],
    ):
        fields = record.get(
            "fields",
            {},
        )

        key = (
            fields.get("dataset_id"),
            fields.get("run_id"),
        )

        existing_by_key[
            key
        ] = record["id"]

    creates = []
    updates = []

    for row in rows:
        fields = {
            "dataset_id": row["dataset_id"],
            "campaign": row["campaign"],
            "run_id": row["run_id"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
            "status": row["status"],
            "exit_code": row["exit_code"],
            "log_file": row["log_file"],
            "error_message": row["error_message"],
        }

        key = (
            row["dataset_id"],
            row["run_id"],
        )

        record_id = existing_by_key.get(
            key
        )

        if record_id is None:
            creates.append(
                {
                    "fields": fields,
                }
            )
        else:
            updates.append(
                {
                    "id": record_id,
                    "fields": fields,
                }
            )

    if creates:
        add_records(
            table_id,
            creates,
        )

    if updates:
        update_records(
            table_id,
            updates,
        )


def get_campaign_records():
    conn = connect()

    rows = conn.execute(
        """
        SELECT campaign,
               COUNT(*) AS total,
               SUM(
                       CASE
                           WHEN publication_status = 'SUCCESS'
                               THEN 1
                           ELSE 0
                           END
               )        AS published,
               SUM(
                       CASE
                           WHEN publication_status = 'FAILED'
                               THEN 1
                           ELSE 0
                           END
               )        AS failed,
               SUM(
                       CASE
                           WHEN publication_status = 'PENDING'
                               THEN 1
                           ELSE 0
                           END
               )        AS pending
        FROM datasets
        GROUP BY campaign
        ORDER BY campaign
        """
    ).fetchall()

    conn.close()

    return [
        {
            "campaign": row[0],
            "total": row[1],
            "published": row[2],
            "failed": row[3],
            "pending": row[4],
        }
        for row in rows
    ]


def get_dataset_records():
    conn = connect()

    rows = conn.execute(
        """
        SELECT d.dataset_id,
               d.campaign,
               d.publication_status,
               p.status AS last_attempt_status,
               p.finished_at,
               p.log_file
        FROM datasets d
                 LEFT JOIN publication_attempts p
                           ON d.dataset_id = p.dataset_id
                               AND p.finished_at = (SELECT MAX(p2.finished_at)
                                                    FROM publication_attempts p2
                                                    WHERE p2.dataset_id = d.dataset_id)
        ORDER BY d.dataset_id
        """
    ).fetchall()

    conn.close()

    return [
        {
            "dataset_id": row[0],
            "campaign": row[1],
            "publication_status": row[2],
            "last_attempt_status": row[3],
            "finished_at": row[4],
            "log_file": row[5],
        }
        for row in rows
    ]


def get_failure_records():
    conn = connect()

    rows = conn.execute(
        """
        SELECT p.dataset_id,
               d.campaign,
               p.run_id,
               p.started_at,
               p.finished_at,
               p.status,
               p.exit_code,
               p.log_file,
               p.error_message
        FROM publication_attempts p
                 JOIN datasets d
                      ON d.dataset_id = p.dataset_id
        WHERE p.status = 'FAILED'
        ORDER BY p.started_at DESC
        """
    ).fetchall()

    conn.close()

    return [
        {
            "dataset_id": row[0],
            "campaign": row[1],
            "run_id": row[2],
            "started_at": row[3],
            "finished_at": row[4],
            "status": row[5],
            "exit_code": row[6],
            "log_file": row[7],
            "error_message": row[8],
        }
        for row in rows
    ]


def sync_to_grist():
    campaign_rows = get_campaign_records()
    dataset_rows = get_dataset_records()
    failure_rows = get_failure_records()

    sync_campaigns_to_grist(
        campaign_rows
    )

    sync_datasets_to_grist(
        dataset_rows
    )

    sync_failures_to_grist(
        failure_rows
    )

    return {
        "campaigns": len(campaign_rows),
        "datasets": len(dataset_rows),
        "failures": len(failure_rows),
    }