import subprocess
import tempfile
import sys
from datetime import datetime
from pathlib import Path

from workflow.config import get_publisher_config, get_active_esg_config
from workflow.database import connect, update_dataset_status, retry_failed_datasets
from workflow.result import PublicationResult
from workflow.summary import PublicationSummary


def create_run_id(campaign):
    timestamp = datetime.today().strftime("%Y%m%d%H%M%S")
    return f"{campaign}_{timestamp}"


def get_run_log_file(campaign, run_id):
    publisher = get_publisher_config()
    directory = Path(
        publisher["logging"]["directory"]
    ) / campaign

    directory.mkdir(
        parents=True,
        exist_ok=True,
    )

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


def get_mapfile_path_mappings():
    publisher = get_publisher_config()

    return publisher.get(
        "mapfile_path_mappings",
        [],
    )


def rewrite_mapfile(
        source_mapfile,
        destination_mapfile,
):
    """
    Create a temporary mapfile with configured filesystem
    path mappings applied to file paths.

    The source mapfile is never modified.
    """

    mappings = get_mapfile_path_mappings()

    with open(source_mapfile) as source, open(
            destination_mapfile,
            "w",
    ) as destination:

        for line in source:
            stripped = line.rstrip("\n")

            if not stripped:
                destination.write(line)
                continue

            fields = stripped.split("|")

            if len(fields) >= 2:
                file_path = fields[1].strip()

                for mapping in mappings:
                    source_root = mapping["from"]
                    target_root = mapping["to"]

                    if file_path == source_root:
                        file_path = target_root
                        break

                    if file_path.startswith(
                            source_root.rstrip("/") + "/"
                    ):
                        file_path = (
                                target_root.rstrip("/")
                                + file_path[len(source_root):]
                        )
                        break

                fields[1] = f" {file_path} "

            destination.write(
                "|".join(fields) + "\n"
            )


def build_publish_command(mapfile):
    publisher = get_publisher_config()
    command = [publisher["executable"]]
    command.extend(publisher.get("arguments", []))
    profile, esg_config = get_active_esg_config()
    command.extend(["--config", str(esg_config),])
    command.extend(["--map", str(mapfile),])
    return command


def create_attempt(conn, dataset_id, run_id,):
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
    datasets = get_campaign_datasets(campaign, limit=limit)
    total = len(datasets)
    print()
    print(f"Campaign: {campaign}")
    print(f"Datasets selected: {total}")
    print(f"Batch size: {batch_size}")
    print()
    mappings = get_mapfile_path_mappings()
    if mappings:
        print("Mapfile path mappings:")
        for mapping in mappings:
            print(f"  {mapping['from']} -> {mapping['to']}")
        print()
    for batch_start in range(
            0,
            total,
            batch_size,
    ):
        batch = datasets[batch_start:batch_start + batch_size]
        batch_number = (batch_start // batch_size) + 1
        print(f"=== Batch {batch_number} "
            f"({len(batch)} datasets) ===")
        for dataset_id, mapfile, status in batch:
            print(f"[DRY-RUN] {dataset_id}")
            print(f"  status : {status}")
            print(f"  source mapfile: {mapfile}")
            with tempfile.TemporaryDirectory(
                    prefix="pubflow-dry-run-"
            ) as temp_dir:
                effective_mapfile = (
                        Path(temp_dir)
                        / Path(mapfile).name
                )
                rewrite_mapfile(
                    mapfile,
                    effective_mapfile,
                )
                original_paths = []
                rewritten_paths = []
                with open(mapfile) as original:
                    for line in original:
                        fields = line.rstrip("\n").split("|")
                        if len(fields) >= 2:
                            original_paths.append(
                                fields[1].strip()
                            )
                with open(effective_mapfile) as rewritten:
                    for line in rewritten:
                        fields = line.rstrip("\n").split("|")
                        if len(fields) >= 2:
                            rewritten_paths.append(
                                fields[1].strip()
                            )
                changed = 0
                for original_path, rewritten_path in zip(
                        original_paths,
                        rewritten_paths,
                ):
                    if original_path != rewritten_path:
                        changed += 1
                print(
                    f"  effective mapfile: "
                    f"{effective_mapfile}"
                )
                print(
                    f"  paths rewritten: "
                    f"{changed}/{len(original_paths)}"
                )
                if changed:
                    print("  example:")
                    for original_path, rewritten_path in zip(
                            original_paths,
                            rewritten_paths,
                    ):
                        if original_path != rewritten_path:
                            print(
                                f"    {original_path}"
                            )
                            print(
                                f"      -> {rewritten_path}"
                            )
                            break
                command = build_publish_command(
                    effective_mapfile
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
    try:
        with tempfile.TemporaryDirectory(
                prefix="pubflow-"
        ) as temp_dir:
            effective_mapfile = (
                    Path(temp_dir)
                    / Path(mapfile).name
            )
            rewrite_mapfile(
                mapfile,
                effective_mapfile,
            )
            command = build_publish_command(
                effective_mapfile
            )
            with open(
                    log_file,
                    "a",
            ) as log:
                log.write(
                    "\n" + "-" * 70 + "\n"
                )
                log.write(
                    f"Dataset: {dataset_id}\n"
                )
                log.write(
                    f"Original mapfile: {mapfile}\n"
                )
                log.write(
                    f"Effective mapfile: {effective_mapfile}\n"
                )
                log.write(
                    f"Command: {' '.join(command)}\n"
                )
                log.write(
                    "-" * 70 + "\n"
                )
                result = subprocess.run(
                    command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                )
                log.write(result.stdout)
        if result.returncode == 0:
            status = "SUCCESS"
            error_message = None
        else:
            status = "FAILED"
            error_message = extract_error_message(
                result.stdout
            )

            if error_message is None:
                error_message = (
                    f"esgpublish exited with code "
                    f"{result.returncode}"
                )
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
            error_message,
        )
        conn.commit()
        return PublicationResult(
            dataset_id=dataset_id,
            status=status,
            exit_code=result.returncode,
            log_file=str(log_file),
            error_message=error_message,
        )
    except Exception as exc:
        error_message = (
            f"{type(exc).__name__}: {exc}"
        )
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
            error_message,
        )
        conn.commit()
        return PublicationResult(
            dataset_id=dataset_id,
            status="FAILED",
            exit_code=-1,
            log_file=str(log_file),
            error_message=error_message,
        )
    finally:
        conn.close()


def extract_error_message(output):
    if not output:
        return None
    lines = output.splitlines()
    error_lines = [
        line.strip()
        for line in lines
        if " ERROR " in line
           or line.startswith("ERROR")
    ]
    if error_lines:
        return error_lines[-1]
    fail_lines = [
        line.strip()
        for line in lines
        if "PUB_STATUS=FAIL" in line
    ]
    if fail_lines:
        return fail_lines[-1]
    return None

def publish_campaign(
        campaign,
        limit=None,
        batch_size=50,
):
    run_id = create_run_id(campaign)

    summary = PublicationSummary(
        campaign=campaign,
        run_id=run_id,
    )

    log_file = get_run_log_file(
        campaign,
        run_id,
    )
    profile, esg_config = get_active_esg_config()
    success_count = 0
    failed_count = 0
    processed_count = 0

    with open(log_file, "w") as log:
        log.write("=" * 70 + "\n")
        log.write("ESGF Publisher Workflow\n")
        log.write(f"Campaign: {campaign}\n")
        log.write(f"Run ID: {run_id}\n")
        log.write(f"Started: {datetime.now()}\n")
        log.write(f"Batch size: {batch_size}\n")
        log.write(f"ESG publisher profile: {profile}\n")
        log.write(f"ESG publisher config: {esg_config}\n")
        if limit is not None:
            log.write(f"Limit: {limit}\n")
        mappings = get_mapfile_path_mappings()
        if mappings:
            log.write(
                "\nMapfile path mappings:\n"
            )

            for mapping in mappings:
                log.write( f"  {mapping['from']} -> "
                    f"{mapping['to']}\n")

        log.write(
            "=" * 70 + "\n\n"
        )

    total_processed = 0
    batch_number = 0

    while True:
        remaining = None

        if limit is not None:
            remaining = (
                    limit - total_processed
            )

            if remaining <= 0:
                break

        current_batch_size = batch_size

        if remaining is not None:
            current_batch_size = min(
                batch_size,
                remaining,
            )

        datasets = get_campaign_datasets(
            campaign,
            limit=current_batch_size,
        )

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
            result = publish_dataset(
                dataset_id,
                mapfile,
                run_id,
                log_file,
            )
            summary.add_result(result)
            processed_count += 1
            total_processed += 1

            if result.status == "SUCCESS":
                print(
                    f"SUCCESS {result.dataset_id}"
                )
                success_count += 1

            else:
                print(
                    f"FAILED {result.dataset_id} "
                    f"(exit code: {result.exit_code})"
                )

                if result.error_message:
                    print(
                        f"Error: {result.error_message}"
                    )

                print(
                    f"Log: {result.log_file}"
                )

                failed_count += 1

        print()
        print(
            f"Batch {batch_number} complete"
        )

    print()
    print("=" * 60)
    print("Publication run complete")
    print("=" * 60)
    print(
        f"Campaign: {summary.campaign}"
    )
    print(
        f"Run ID: {summary.run_id}"
    )
    print()
    print(
        f"Total:   {summary.total}"
    )
    print(
        f"Success: {summary.success}"
    )
    print(
        f"Failed:  {summary.failed}"
    )
    if summary.failures:
        print()
        print("Failed datasets:")
        for failure in summary.failures:
            print(f"  - {failure.dataset_id} "
                f"(exit code {failure.exit_code})")

    print()
    print(f"Total datasets processed: "
        f"{total_processed}")
    with open(log_file, "a") as log:
        log.write("\n" + "=" * 70 + "\n")
        log.write("RUN COMPLETE\n")
        log.write("=" * 70 + "\n")
        log.write(f"Campaign: {campaign}\n")
        log.write(f"Run ID: {run_id}\n")
        log.write(f"Finished: {datetime.now()}\n")
        log.write(f"Processed: {processed_count}\n")
        log.write(f"SUCCESS:   {success_count}\n")
        log.write(f"FAILED:    {failed_count}\n")
        log.write("=" * 70 + "\n")

    print("Triggering asynchronous Grist sync...")
    try:
        sync_log = trigger_grist_sync(
            campaign,
            run_id,
        )
        print(
            f"Grist sync started "
            f"(log: {sync_log})"
        )
    except Exception as exc:
        print(
            f"WARNING: Could not trigger "
            f"Grist sync: {exc}"
        )

def trigger_grist_sync(campaign, run_id):
    publisher = get_publisher_config()
    log_dir = (
            Path(publisher["logging"]["directory"])
            / campaign
    )
    log_dir.mkdir(
        parents=True,
        exist_ok=True,
    )
    sync_log = (
            log_dir
            / f"{run_id}-grist-sync.log"
    )
    with open(sync_log, "a") as log:
        subprocess.Popen(
            [
                sys.executable,
                "-m",
                "pubflow.cli",
                "grist",
                "sync",
            ],
            stdout=log,
            stderr=log,
            start_new_session=True,
        )
    return sync_log


def retry_campaign(campaign, limit=None):
    conn = connect()
    try:
        count = retry_failed_datasets(conn, campaign, limit=limit,)
        return count
    finally:
        conn.close()