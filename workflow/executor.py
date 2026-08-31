import subprocess
import tempfile
import sys
from datetime import datetime
from pathlib import Path

from workflow.config import get_publisher_config, get_active_esg_config
from workflow.database import connect, update_dataset_status, retry_failed_datasets
from workflow.result import PublicationResult
from workflow.summary import PublicationSummary
from workflow.publisher_output import parse_publication_statuses
from workflow.stac_items import reconcile_stac_item


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
        exclude_dataset_ids=None,
):
    conn = connect()
    query = """
            SELECT dataset_id,
                   mapfile,
                   publication_status
            FROM datasets
            WHERE campaign = ?
              AND publication_status = 'PENDING'
            """

    params = [campaign]

    excluded = sorted(exclude_dataset_ids or [])
    if excluded:
        placeholders = ", ".join("?" for _ in excluded)
        query += f" AND dataset_id NOT IN ({placeholders})"
        params.extend(excluded)

    query += " ORDER BY dataset_id"

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


def build_publish_command(mapfile, save_stac=False):
    publisher = get_publisher_config()
    command = [publisher["executable"]]
    command.extend(publisher.get("arguments", []))
    profile, esg_config = get_active_esg_config()
    command.extend(["--config", str(esg_config),])
    if save_stac and "--save-stac" not in command:
        command.append("--save-stac")
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


def record_publication_result(
        conn,
        dataset_id,
        run_id,
        status,
        exit_code,
        log_file,
        error_message=None,
):
    create_attempt(conn, dataset_id, run_id)
    update_dataset_status(conn, dataset_id, status)
    finish_attempt(
        conn,
        dataset_id,
        run_id,
        status,
        exit_code,
        str(log_file),
        error_message,
    )


def record_no_status_attempt(
        conn,
        dataset_id,
        run_id,
        exit_code,
        log_file,
        error_message,
):
    """Record an ambiguous attempt without changing the PENDING dataset."""
    create_attempt(conn, dataset_id, run_id)
    finish_attempt(
        conn,
        dataset_id,
        run_id,
        "NO_STATUS",
        exit_code,
        str(log_file),
        error_message,
    )


def dry_run_campaign(
        campaign,
        limit=None,
        batch_size=50,
):
    if batch_size <= 0:
        raise ValueError("batch_size must be greater than zero")

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
        with tempfile.TemporaryDirectory(
                prefix="pubflow-dry-run-"
        ) as temp_dir:
            for dataset_id, mapfile, status in batch:
                print(f"[DRY-RUN] {dataset_id}")
                print(f"  status : {status}")
                print(f"  source mapfile: {mapfile}")
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
            command = build_publish_command(temp_dir, save_stac=True)
            print(
                f"  batch command: {' '.join(command)}"
            )
        print()


def publish_batch(
        datasets,
        run_id,
        log_file,
        batch_number,
        record_missing_status=False,
):
    conn = connect()
    results = []
    try:
        with tempfile.TemporaryDirectory(
                prefix="pubflow-"
        ) as temp_dir:
            temp_root = Path(temp_dir)
            map_directory = temp_root / "mapfiles"
            stac_staging_directory = temp_root / "stac-output"
            map_directory.mkdir()
            stac_staging_directory.mkdir()
            staged_datasets = []
            staged_names = set()

            for dataset_id, mapfile, status in datasets:
                effective_mapfile = map_directory / Path(mapfile).name
                try:
                    if effective_mapfile.name in staged_names:
                        raise ValueError(
                            "Duplicate mapfile basename in batch: "
                            f"{effective_mapfile.name}"
                        )
                    rewrite_mapfile(mapfile, effective_mapfile)
                    staged_names.add(effective_mapfile.name)
                    staged_datasets.append((dataset_id, mapfile, status))
                except Exception as exc:
                    error_message = f"{type(exc).__name__}: {exc}"
                    record_publication_result(
                        conn,
                        dataset_id,
                        run_id,
                        "FAILED",
                        -1,
                        log_file,
                        error_message,
                    )
                    results.append(PublicationResult(
                        dataset_id=dataset_id,
                        status="FAILED",
                        exit_code=-1,
                        log_file=str(log_file),
                        error_message=error_message,
                    ))

            conn.commit()

            if not staged_datasets:
                return results

            command = build_publish_command(
                map_directory,
                save_stac=True,
            )
            with open(
                    log_file,
                    "a",
            ) as log:
                log.write(
                    "\n" + "-" * 70 + "\n"
                )
                log.write(
                    f"Batch: {batch_number}\n"
                )
                log.write(f"Datasets: {len(staged_datasets)}\n")
                for dataset_id, mapfile, _ in staged_datasets:
                    log.write(f"  {dataset_id}: {mapfile}\n")
                log.write(f"Effective directory: {map_directory}\n")
                log.write(f"STAC staging directory: {stac_staging_directory}\n")
                log.write(
                    f"Command: {' '.join(command)}\n"
                )
                log.write(
                    "-" * 70 + "\n"
                )
                result = subprocess.run(
                    command,
                    cwd=stac_staging_directory,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                )
                output = result.stdout or ""
                log.write(output)
                log.write(f"\nPublisher exit code: {result.returncode}\n")
                log.write(f"Publisher output length: {len(output)}\n")

            statuses, unknown_ids = parse_publication_statuses(
                output,
                staged_datasets,
            )
            error_message = extract_error_message(output)

            if unknown_ids:
                with open(log_file, "a") as log:
                    for unknown_id in unknown_ids:
                        log.write(
                            f"WARNING: PUB_STATUS for unknown dataset: "
                            f"{unknown_id}\n"
                        )

            if (
                not statuses
                and not results
                and record_missing_status
                and len(staged_datasets) == 1
            ):
                dataset_id = staged_datasets[0][0]
                no_status_error = error_message or (
                    "esgpublish returned no recognizable PUB_STATUS line "
                    f"(exit code {result.returncode}, "
                    f"output length {len(output)})"
                )
                record_no_status_attempt(
                    conn,
                    dataset_id,
                    run_id,
                    result.returncode,
                    log_file,
                    no_status_error,
                )
                with open(log_file, "a") as log:
                    log.write(
                        f"DEFERRED {dataset_id}: {no_status_error}\n"
                    )
                conn.commit()
                return []

            for dataset_id, status in statuses.items():
                with open(log_file, "a") as log:
                    try:
                        stac_action, stac_path = reconcile_stac_item(
                            stac_staging_directory,
                            dataset_id,
                            status,
                        )
                        location = f" at {stac_path}" if stac_path else ""
                        log.write(
                            f"STAC item {dataset_id}: "
                            f"{stac_action}{location}\n"
                        )
                    except Exception as exc:
                        log.write(
                            f"WARNING: Could not reconcile STAC item "
                            f"{dataset_id}: {type(exc).__name__}: {exc}\n"
                        )
                dataset_error = error_message if status == "FAILED" else None
                record_publication_result(
                    conn,
                    dataset_id,
                    run_id,
                    status,
                    result.returncode,
                    log_file,
                    dataset_error,
                )
                results.append(PublicationResult(
                    dataset_id=dataset_id,
                    status=status,
                    exit_code=result.returncode,
                    log_file=str(log_file),
                    error_message=dataset_error,
                ))

            conn.commit()
            return results
    except Exception as exc:
        error_message = (
            f"{type(exc).__name__}: {exc}"
        )
        completed_ids = {item.dataset_id for item in results}
        for dataset_id, _, _ in datasets:
            if dataset_id in completed_ids:
                continue
            record_publication_result(
                conn,
                dataset_id,
                run_id,
                "FAILED",
                -1,
                log_file,
                error_message,
            )
            results.append(PublicationResult(
                dataset_id=dataset_id,
                status="FAILED",
                exit_code=-1,
                log_file=str(log_file),
                error_message=error_message,
            ))
        conn.commit()
        return results
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
    if batch_size <= 0:
        raise ValueError("batch_size must be greater than zero")

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
    total_considered = 0
    batch_number = 0
    deferred_dataset_ids = set()

    while True:
        remaining = None

        if limit is not None:
            remaining = (
                    limit - total_considered
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
            exclude_dataset_ids=deferred_dataset_ids,
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

        results = publish_batch(
            datasets,
            run_id,
            log_file,
            batch_number,
        )

        if not results:
            isolated_dataset = datasets[0]
            print(
                "No recognizable PUB_STATUS lines; retrying the first "
                "dataset in isolation"
            )
            with open(log_file, "a") as log:
                log.write(
                    "No recognizable PUB_STATUS lines for batch "
                    f"{batch_number}; isolating {isolated_dataset[0]}\n"
                )

            results = publish_batch(
                [isolated_dataset],
                run_id,
                log_file,
                f"{batch_number}-isolation",
                record_missing_status=True,
            )

            if not results:
                dataset_id = isolated_dataset[0]
                deferred_dataset_ids.add(dataset_id)
                total_considered += 1
                print(
                    f"DEFERRED {dataset_id}: no recognizable PUB_STATUS; "
                    "it remains PENDING and will be skipped for this run"
                )
                print()
                print(f"Batch {batch_number} complete")
                continue

        for result in results:
            summary.add_result(result)
            processed_count += 1
            total_processed += 1
            total_considered += 1

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

        unattempted_count = len(datasets) - len(results)
        if unattempted_count:
            print(
                f"PENDING {unattempted_count} datasets not reached "
                "by esgpublish; they will be included in the next batch"
            )

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
    print(f"Datasets deferred: {len(deferred_dataset_ids)}")
    if deferred_dataset_ids:
        for dataset_id in sorted(deferred_dataset_ids):
            print(f"  - {dataset_id} (remains PENDING)")
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
        log.write(f"DEFERRED:  {len(deferred_dataset_ids)}\n")
        for dataset_id in sorted(deferred_dataset_ids):
            log.write(f"  {dataset_id}\n")
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
