import subprocess
from datetime import datetime
from pathlib import Path
from datetime import datetime
from workflow.config import get_publisher_config
from workflow.database import connect, update_dataset_status


def create_run_id(campaign):
    timestamp = datetime.today().strftime("%Y%m%d%H%M%S")
    return f"{campaign}_{timestamp}"


def get_run_log_file(campaign, run_id):
    publisher = get_publisher_config()
    directory = (Path(publisher["logging"]["directory"]) / campaign)
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{run_id}.log"


def get_campaign_datasets(
        campaign,
        limit=None,
):
    conn = connect()
    query = """
            SELECT dataset_id,
                   mapfile,
                   publication_status
            FROM datasets
            WHERE campaign = ?
              AND publication_status = 'PENDING'

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


def create_attempt(conn, dataset_id, run_id):
    conn.execute(
        """
        INSERT INTO publication_attempts
        (dataset_id,
         run_id,
         started_at,
         status)

        VALUES (?, ?, ?, ?)
        """,
        [
            dataset_id,
            run_id,
            datetime.now(),
            "RUNNING",
        ],
    )


def finish_attempt(
        conn,
        dataset_id,
        run_id,
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
          AND run_id = ?
        """,
        [
            datetime.now(),
            status,
            exit_code,
            log_file,
            error_message,
            dataset_id,
            run_id,
        ],
    )


def dry_run_campaign(
        campaign,
        limit=None,
        batch_size=50,
):
    datasets = get_campaign_datasets(
        campaign,
        limit=limit,
    )

    total = len(datasets)

    print()
    print(
        f"Campaign: {campaign}"
    )
    print(
        f"Datasets selected: {total}"
    )
    print(
        f"Batch size: {batch_size}"
    )
    print()

    for batch_start in range(
            0,
            total,
            batch_size,
    ):
        batch = datasets[
            batch_start:batch_start + batch_size
        ]

        batch_number = (
                               batch_start // batch_size
                       ) + 1

        print(
            f"=== Batch {batch_number} "
            f"({len(batch)} datasets) ==="
        )

        for dataset_id, mapfile, status in batch:
            command = build_publish_command(
                mapfile
            )

            print(
                f"[DRY-RUN] {dataset_id}"
            )

            print(
                f"  status : {status}"
            )

            print(
                f"  command: {' '.join(command)}"
            )

        print()


def publish_dataset(
        dataset_id,
        mapfile,
        run_id,
        log_file,
):
    conn = connect()

    create_attempt(
        conn,
        dataset_id,
        run_id,
    )

    conn.commit()

    command = build_publish_command(
        mapfile
    )

    try:

        with open(
                log_file,
                "a"
        ) as log:

            log.write(
                "\n"
                + "-" * 70
                + "\n"
            )

            log.write(
                f"Dataset: {dataset_id}\n"
            )

            log.write(
                f"Mapfile: {mapfile}\n"
            )

            log.write(
                f"Command: {' '.join(command)}\n"
            )

            log.write(
                "-" * 70
                + "\n"
            )

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
            run_id,
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
            run_id,
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
        batch_size=50,
):
    run_id = create_run_id(campaign)
    log_file = get_run_log_file(campaign, run_id)
    success_count = 0
    failed_count = 0
    processed_count = 0
    with open(log_file, "w") as log:
        log.write(
            "=" * 70 + "\n"
        )
        log.write("ESGF Publisher Workflow\n")
        log.write(f"Campaign: {campaign}\n")
        log.write(f"Run ID: {run_id}\n")
        log.write(f"Started: {datetime.now()}\n")
        log.write(f"Batch size: {batch_size}\n")

        if limit is not None:
            log.write(f"Limit: {limit}\n")
        log.write("=" * 70 + "\n\n")
    total_processed = 0
    batch_number = 0
    while True:
        remaining = None
        if limit is not None:
            remaining = (limit - total_processed)
            if remaining <= 0:
                break
        current_batch_size = batch_size
        if remaining is not None:
            current_batch_size = min(batch_size, remaining)
        datasets = get_campaign_datasets(campaign,
                                         limit=current_batch_size)
        if not datasets:
            break
        batch_number += 1
        print()
        print(
            f"=== Batch {batch_number} "
            f"({len(datasets)} datasets) ==="
        )
        print()
        for dataset_id, mapfile, status in datasets:
            result = publish_dataset(dataset_id, mapfile,
                                     run_id, log_file)
            processed_count += 1
            if result == "SUCCESS":
                success_count += 1
            elif result == "FAILED":
                failed_count += 1
            print(f"{dataset_id}: {result}")
            total_processed += 1
        print()
        print(f"Batch {batch_number} complete")
    print()
    print(
        f"Total datasets processed: "
        f"{total_processed}"
    )
    with open(
            log_file,
            "a",
    ) as log:
        log.write(
            "\n"
            + "=" * 70
            + "\n"
        )
        log.write(
            "RUN COMPLETE\n"
        )
        log.write(
            "=" * 70
            + "\n"
        )
        log.write(
            f"Campaign: {campaign}\n"
        )
        log.write(
            f"Run ID: {run_id}\n"
        )
        log.write(
            f"Finished: {datetime.now()}\n"
        )
        log.write(
            "\n"
        )
        log.write(
            f"Processed: {processed_count}\n"
        )
        log.write(
            f"SUCCESS:   {success_count}\n"
        )
        log.write(
            f"FAILED:    {failed_count}\n"
        )
        log.write(
            "=" * 70
            + "\n"
        )
