import subprocess, os
from datetime import datetime
from pathlib import Path

from workflow.database import connect, update_dataset_status
from workflow.config import get_publisher_config


def get_log_file(dataset_id):
    publisher = get_publisher_config()

    directory = Path(
        publisher["logging"]["directory"]
    )

    directory.mkdir(
        parents=True,
        exist_ok=True
    )

    safe_id = dataset_id.replace(
        "/",
        "_"
    )

    return directory / f"{safe_id}.log"


def get_campaign_datasets(campaign):
    conn = connect()

    rows = conn.execute(
        """
        SELECT dataset_id,
               mapfile,
               publication_status

        FROM datasets

        WHERE campaign = ?

        ORDER BY dataset_id
        """,
        [campaign],
    ).fetchall()

    conn.close()

    return rows


def build_publish_command(mapfile):
    publisher = get_publisher_config()

    command = [
        publisher["executable"]
    ]

    command.extend(
        publisher.get(
            "arguments",
            []
        )
    )

    command.extend(
        [
            "--map",
            str(mapfile)
        ]
    )

    return command


def dry_run_campaign(
        campaign,
        limit=None
):
    datasets = get_campaign_datasets(
        campaign
    )

    if limit:
        datasets = datasets[:limit]

    print()
    print(
        f"Campaign: {campaign}"
    )

    print(
        f"Datasets selected: {len(datasets)}"
    )

    print()

    for dataset_id, mapfile, status in datasets:
        command = build_publish_command(
            mapfile
        )

        print(
            "[DRY-RUN]"
        )

        print(
            f"Dataset: {dataset_id}"
        )

        print(
            f"Status : {status}"
        )

        print(
            "Command:"
        )

        print(
            " ".join(command)
        )

        print()


def create_attempt(
        conn,
        dataset_id,
):
    conn.execute(
        """
        INSERT INTO publication_attempts
        (dataset_id,
         started_at,
         status)

        VALUES (?, ?, ?)
        """,
        [
            dataset_id,
            datetime.now(),
            "RUNNING",
        ],
    )


def finish_attempt(
        conn,
        dataset_id,
        status,
        exit_code,
        log_file=None,
        error_message=None,
):
    conn.execute(
        """
        UPDATE publication_attempts

        SET finished_at   = ?,
            status        = ?,
            exit_code     = ?,
            log_file      = ?,
            error_message = ?

        WHERE dataset_id = ?

          AND finished_at IS NULL
        """,
        [
            datetime.now(),
            status,
            exit_code,
            log_file,
            error_message,
            dataset_id,
        ],
    )


def publish_dataset(
        dataset_id,
        mapfile,
):
    conn = connect()

    create_attempt(
        conn,
        dataset_id,
    )

    conn.commit()

    command = build_publish_command(
        mapfile
    )

    log_file = get_log_file(
        dataset_id
    )

    print(
        "Running:",
        " ".join(command)
    )

    try:

        with open(log_file, "w") as log:

            result = subprocess.run(
                command,
                stdout=log,
                stderr=subprocess.STDOUT,
                text=True,
            )

        if result.returncode == 0:

            status = "SUCCESS"

        else:

            status = "FAILED"

        update_dataset_status(
            conn,
            dataset_id,
            status,
        )

        finish_attempt(
            conn,
            dataset_id,
            status,
            result.returncode,
            str(log_file),
        )

        conn.commit()

        return status


    except Exception as exc:

        update_dataset_status(
            conn,
            dataset_id,
            "FAILED",
        )

        finish_attempt(
            conn,
            dataset_id,
            "FAILED",
            -1,
            str(log_file),
            str(exc),
        )

        conn.commit()

        return "FAILED"


    finally:

        conn.close()


def publish_campaign(
        campaign,
        limit=None,
):
    datasets = get_campaign_datasets(
        campaign
    )

    if limit:
        datasets = datasets[:limit]

    for dataset_id, mapfile, status in datasets:

        if status in ("SUCCESS","RUNNING",):
            print(
                f"Skipping already published {dataset_id}"
            )
            continue

        result = publish_dataset(
            dataset_id,
            mapfile,
        )

        print(
            f"{dataset_id}: {result}"
        )
