import csv
from pathlib import Path

from esgvoc.apps.drs.generator import DrsGenerator

from workflow.campaign import get_campaign
from workflow.database import connect
from workflow.registry import parse_drs


def get_archivable_datasets(
        campaign,
        limit=None,
):
    conn = connect()

    query = """
            SELECT dataset_id,
                   mapfile,
                   archive_status
            FROM datasets
            WHERE campaign = ?
              AND publication_status = 'SUCCESS'
              AND archive_status = 'PENDING'
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


def get_archive_path(
        dataset_id,
        mapfile,
        campaign,
):
    """
    Build the destination path for a dataset's mapfile.

    The campaign archive_root already represents the fixed
    project/activity/institution part of the DRS.

    The remaining DRS hierarchy is generated from the
    ESGVOC directory specification until archive_depth.
    """

    mapping = parse_drs(dataset_id)

    generator = DrsGenerator(
        mapping["mip_era"].lower()
    )

    parts = generator.directory_specs.parts

    archive_root = Path(
        campaign["archive_root"]
    )

    depth = campaign["archive_depth"]

    # The archive root corresponds to the first three
    # DRS components: project/activity/institution.
    root_components = [
        mapping["mip_era"],
        mapping["activity_id"],
        mapping["institution_id"],
    ]

    # Validate that the campaign configuration matches
    # the dataset's DRS prefix.
    expected_root = [
        campaign["project"],
        campaign["activity"],
        campaign["institution"],
    ]

    if root_components != expected_root:
        raise ValueError(
            f"Dataset {dataset_id} does not match campaign "
            f"DRS prefix: expected {expected_root}, "
            f"got {root_components}"
        )

    selected = []

    root_length = len(root_components)

    for part in parts[root_length:]:
        name = part.source_collection

        if name not in mapping:
            raise ValueError(
                f"DRS component '{name}' is missing from "
                f"dataset mapping for {dataset_id}"
            )

        selected.append(
            mapping[name]
        )

        if name == depth:
            break

    else:
        raise ValueError(
            f"Archive depth '{depth}' was not found in the "
            f"ESGVOC DRS specification for {dataset_id}"
        )

    return (
            archive_root
            / Path(*selected)
            / ".mapfiles"
            / Path(mapfile).name
    )


def generate_archive_tasks(
        campaign_name,
        output,
        limit=None,
):
    campaign = get_campaign(
        campaign_name
    )

    rows = get_archivable_datasets(
        campaign_name,
        limit,
    )

    output = Path(
        output
    )

    output.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    count = 0

    with open(
            output,
            "w",
            newline="",
    ) as f:
        writer = csv.writer(f)

        writer.writerow(
            [
                "dataset_id",
                "mapfile",
                "archive_path",
            ]
        )

        for dataset_id, mapfile, archive_status in rows:
            archive_path = get_archive_path(
                dataset_id,
                mapfile,
                campaign,
            )

            writer.writerow(
                [
                    dataset_id,
                    mapfile,
                    str(archive_path),
                ]
            )

            count += 1

    return count