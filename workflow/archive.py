#!/usr/bin/env python3
import csv
import hashlib
from collections import Counter
from pathlib import Path
from uuid import uuid4

from esgvoc.apps.drs.generator import DrsGenerator

from workflow.campaign import get_campaign
from workflow.database import connect
from workflow.registry import parse_drs
from workflow.stac_reconciliation import reconcile_campaign


def get_archivable_datasets(campaign, limit=None):
    conn = connect()

    query = """
            SELECT dataset_id, mapfile, mapfile_checksum, archive_status
            FROM datasets
            WHERE campaign = ?
              AND registration_status = 'ACTIVE'
              AND publication_status = 'SUCCESS'
              AND archive_status = 'PENDING'
            ORDER BY dataset_id \
            """
    params = [campaign]

    if limit is not None:
        query += " LIMIT ?"
        params.append(limit)

    rows = conn.execute(query, params).fetchall()
    conn.close()
    return rows


def get_archive_path(dataset_id, mapfile, campaign):
    """
    Build the archive destination from the campaign archive root
    and the configured ESGVOC DRS depth.
    """
    generator = DrsGenerator(campaign["project"].lower())
    mapping = parse_drs(dataset_id, generator)
    parts = generator.directory_specs.parts

    archive_root = Path(campaign["archive_root"])
    depth = campaign["archive_depth"]

    root_names = [part.source_collection for part in parts[:3]]
    root_components = [mapping[name] for name in root_names]
    expected_root = [
        campaign["project"],
        campaign["activity"],
        campaign["institution"],
    ]

    if root_components != expected_root:
        raise ValueError(
            f"Dataset {dataset_id} does not match campaign DRS prefix: "
            f"expected {expected_root}, got {root_components}"
        )

    selected = []

    for part in parts[len(root_names):]:
        name = part.source_collection

        if name not in mapping:
            raise ValueError(
                f"DRS component '{name}' is missing from dataset mapping "
                f"for {dataset_id}"
            )

        selected.append(mapping[name])

        if name == depth:
            break
    else:
        raise ValueError(
            f"Archive depth '{depth}' was not found in the ESGVOC "
            f"DRS specification for {dataset_id}"
        )

    return archive_root / Path(*selected) / ".mapfiles" / Path(mapfile).name


def generate_archive_tasks(
        campaign_name,
        output,
        limit=None,
        verify_stac=True,
):
    campaign = get_campaign(campaign_name)
    rows = get_archivable_datasets(campaign_name, limit)

    if verify_stac and rows:
        reconciliation = reconcile_campaign(
            campaign_name,
            scope="successful",
            dataset_ids=[row[0] for row in rows],
        )
        blocked = [
            result
            for result in reconciliation["results"]
            if result["outcome"] != "PRESENT"
        ]
        if blocked:
            counts = Counter(result["outcome"] for result in blocked)
            details = ", ".join(
                f"{outcome}={count}"
                for outcome, count in sorted(counts.items())
            )
            examples = ", ".join(
                result["dataset_id"] for result in blocked[:5]
            )
            raise ValueError(
                "Archive generation blocked by STAC reconciliation "
                f"({details}). Example datasets: {examples}"
            )

    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)

    tasks = []
    for dataset_id, mapfile, mapfile_checksum, _ in rows:
        checksum = mapfile_checksum or hashlib.sha256(
            Path(mapfile).read_bytes()
        ).hexdigest()
        tasks.append({
            "task_id": str(uuid4()),
            "dataset_id": dataset_id,
            "mapfile": mapfile,
            "mapfile_checksum": checksum,
            "archive_path": str(get_archive_path(dataset_id, mapfile, campaign)),
        })

    temporary = output.with_suffix(output.suffix + ".tmp")
    conn = connect()
    try:
        with open(temporary, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=[
                "task_id",
                "dataset_id",
                "mapfile",
                "mapfile_checksum",
                "archive_path",
            ])
            writer.writeheader()
            writer.writerows(tasks)
        conn.execute("BEGIN")
        if tasks:
            conn.executemany(
                """
                INSERT INTO archive_tasks
                (task_id, dataset_id, campaign, mapfile_checksum,
                 source_mapfile, archive_path)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        task["task_id"],
                        task["dataset_id"],
                        campaign_name,
                        task["mapfile_checksum"],
                        task["mapfile"],
                        task["archive_path"],
                    )
                    for task in tasks
                ],
            )
        conn.execute("COMMIT")
        temporary.replace(output)
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        temporary.unlink(missing_ok=True)
        raise
    finally:
        conn.close()

    return len(rows)


def import_archive_results(results_file):
    results_file = Path(results_file)

    if not results_file.exists():
        raise FileNotFoundError(
            f"Results file does not exist: {results_file}"
        )

    counts = {
        "SUCCESS": 0,
        "ALREADY_EXISTS": 0,
        "CONFLICT": 0,
        "FAILED": 0,
        "UNKNOWN_DATASET": 0,
        "INVALID_TASK": 0,
        "UNKNOWN": 0,
    }

    conn = connect()

    try:
        conn.execute("BEGIN")
        with open(results_file, newline="") as f:
            reader = csv.DictReader(f)
            required = {
                "task_id", "dataset_id", "mapfile_checksum",
                "archive_path", "status",
            }
            missing = required - set(reader.fieldnames or [])

            if missing:
                raise ValueError(
                    f"Missing required columns: {', '.join(sorted(missing))}"
                )

            for row in reader:
                dataset_id = row["dataset_id"]
                status = row["status"]
                task = conn.execute(
                    """
                    SELECT dataset_id, mapfile_checksum, archive_path, status
                    FROM archive_tasks
                    WHERE task_id = ?
                    """,
                    [row["task_id"]],
                ).fetchone()
                if task is None or task[:3] != (
                    dataset_id,
                    row["mapfile_checksum"],
                    row["archive_path"],
                ):
                    counts["INVALID_TASK"] += 1
                    print(f"INVALID ARCHIVE TASK: {row['task_id']}")
                    continue

                if status in ("SUCCESS", "ALREADY_EXISTS"):
                    updated = conn.execute(
                        """
                        UPDATE datasets
                        SET archive_status       = 'SUCCESS',
                            archive_completed_at = CURRENT_TIMESTAMP
                        WHERE dataset_id = ?
                          AND registration_status = 'ACTIVE'
                          AND publication_status = 'SUCCESS'
                        RETURNING dataset_id
                        """,
                        [dataset_id],
                    ).fetchone()

                    if updated is None:
                        counts["UNKNOWN_DATASET"] += 1
                        print(f"UNKNOWN DATASET: {dataset_id}")
                    else:
                        counts[status] += 1

                    conn.execute(
                        """
                        UPDATE archive_tasks
                        SET status = ?, completed_at = CURRENT_TIMESTAMP,
                            error_message = ?
                        WHERE task_id = ?
                        """,
                        [status, row.get("error_message", ""), row["task_id"]],
                    )

                elif status in ("CONFLICT", "FAILED"):
                    counts[status] += 1
                    conn.execute(
                        """
                        UPDATE archive_tasks
                        SET status = ?, completed_at = CURRENT_TIMESTAMP,
                            error_message = ?
                        WHERE task_id = ?
                        """,
                        [status, row.get("error_message", ""), row["task_id"]],
                    )
                else:
                    counts["UNKNOWN"] += 1
                    print(f"UNKNOWN STATUS: {dataset_id}: {status}")
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()

    return counts
