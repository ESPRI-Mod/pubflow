import json
from pathlib import Path

from esgvoc.apps.drs.generator import DrsGenerator


def parse_drs(dataset_id, generator):
    """Parse a dataset ID using an ESGVOC DRS generator."""
    parts = dataset_id.split(".")
    drs_parts = generator.directory_specs.parts

    if len(parts) != len(drs_parts):
        raise ValueError(
            f"Unexpected DRS format: dataset ID contains "
            f"{len(parts)} components, but ESGVOC defines "
            f"{len(drs_parts)} DRS components: {dataset_id}"
        )

    return {
        part.source_collection: value
        for value, part in zip(parts, drs_parts)
    }


def parse_mapfile(mapfile, include_files=False):
    """Parse an ESGF mapfile into a dataset ID and optional file metadata."""
    dataset_id = None
    files = []

    with open(mapfile) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue

            fields = [x.strip() for x in line.split("|")]
            if len(fields) < 3:
                raise ValueError(
                    f"Malformed mapfile line in {mapfile}: {line}"
                )

            dataset = fields[0]

            if dataset_id is None:
                dataset_id = dataset
            elif dataset_id != dataset:
                raise ValueError(
                    f"Multiple dataset IDs found in {mapfile}"
                )

            if include_files:
                metadata = {}
                for item in fields[3:]:
                    if "=" in item:
                        key, value = item.split("=", 1)
                        metadata[key.strip()] = value.strip()

                files.append({
                    "file_path": fields[1],
                    "file_size": int(fields[2]),
                    "checksum": metadata.get("checksum"),
                    "mod_time": metadata.get("mod_time"),
                })

    if dataset_id is None:
        raise ValueError(f"No dataset found in {mapfile}")

    return dataset_id, files


DRS_FIELDS = [
    "project",
    "activity",
    "institution",
    "source",
    "experiment",
    "member",
    "table",
    "variable",
    "grid",
    "version",
]


def build_drs_path(dataset_id, depth, generator):
    """Build a DRS path up to the requested component."""
    if depth not in DRS_FIELDS:
        raise ValueError(
            f"Invalid archival depth: {depth}. "
            f"Expected one of: {', '.join(DRS_FIELDS)}"
        )

    drs = parse_drs(dataset_id, generator)
    depth_index = DRS_FIELDS.index(depth)

    return Path(*(drs[field] for field in DRS_FIELDS[:depth_index + 1]))


def register_dataset(
        conn,
        campaign_name,
        campaign,
        mapfile,
        drs_generator,
        register_files=False,
):
    """Register one dataset from one mapfile."""
    dataset_id, files = parse_mapfile(
        mapfile,
        include_files=register_files,
    )
    drs = parse_drs(dataset_id, drs_generator)

    conn.execute(
        """
        INSERT
        OR IGNORE INTO datasets
        (
            dataset_id,
            campaign,
            project,
            activity,
            institution,
            drs,
            mapfile
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            dataset_id,
            campaign_name,
            campaign["project"],
            campaign["activity"],
            campaign["institution"],
            json.dumps(drs),
            str(mapfile),
        ],
    )

    if register_files:
        conn.executemany(
            """
            INSERT
            OR IGNORE INTO files
            (
                dataset_id,
                file_path,
                file_size,
                checksum,
                mod_time
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                (
                    dataset_id,
                    file["file_path"],
                    file["file_size"],
                    file["checksum"],
                    file["mod_time"],
                )
                for file in files
            ],
        )

    return {
        "dataset_id": dataset_id,
        "files": len(files),
    }