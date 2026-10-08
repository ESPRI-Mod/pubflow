import json
import hashlib
from pathlib import Path

def parse_drs(dataset_id, generator):
    """Parse a dataset ID using an ESGVOC DRS generator."""
    parts = dataset_id.split(".")
    drs_parts = generator.directory_specs.parts

    if len(parts) == len(drs_parts) - 1 and "#" in parts[-1]:
        grid, version = parts[-1].split("#", 1)
        parts[-1:] = [grid, f"v{version}"]

    if len(parts) != len(drs_parts):
        raise ValueError(
            f"Unexpected DRS format: dataset ID contains "
            f"{len(parts)} components, but ESGVOC defines "
            f"{len(drs_parts)} DRS components: {dataset_id}"
        )

    return {
        drs_part.source_collection: value
        for value, drs_part in zip(parts, drs_parts)
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
    mapfile_checksum = hashlib.sha256(Path(mapfile).read_bytes()).hexdigest()

    conn.execute(
        """
        INSERT INTO datasets
        (
            dataset_id,
            campaign,
            project,
            activity,
            institution,
            drs,
            mapfile,
            mapfile_checksum
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (dataset_id) DO UPDATE SET
            campaign = EXCLUDED.campaign,
            project = EXCLUDED.project,
            activity = EXCLUDED.activity,
            institution = EXCLUDED.institution,
            drs = EXCLUDED.drs,
            mapfile = EXCLUDED.mapfile,
            publication_status = CASE
                WHEN datasets.mapfile_checksum IS NULL
                  OR datasets.mapfile_checksum = EXCLUDED.mapfile_checksum
                THEN datasets.publication_status
                ELSE 'PENDING'
            END,
            publication_claim_id = CASE
                WHEN datasets.mapfile_checksum IS NULL
                  OR datasets.mapfile_checksum = EXCLUDED.mapfile_checksum
                THEN datasets.publication_claim_id
                ELSE NULL
            END,
            publication_claimed_at = CASE
                WHEN datasets.mapfile_checksum IS NULL
                  OR datasets.mapfile_checksum = EXCLUDED.mapfile_checksum
                THEN datasets.publication_claimed_at
                ELSE NULL
            END,
            archive_status = CASE
                WHEN datasets.mapfile_checksum IS NULL
                  OR datasets.mapfile_checksum = EXCLUDED.mapfile_checksum
                THEN datasets.archive_status
                ELSE 'PENDING'
            END,
            archive_completed_at = CASE
                WHEN datasets.mapfile_checksum IS NULL
                  OR datasets.mapfile_checksum = EXCLUDED.mapfile_checksum
                THEN datasets.archive_completed_at
                ELSE NULL
            END,
            mapfile_checksum = EXCLUDED.mapfile_checksum
        """,
        [
            dataset_id,
            campaign_name,
            campaign["project"],
            campaign["activity"],
            campaign["institution"],
            json.dumps(drs),
            str(mapfile),
            mapfile_checksum,
        ],
    )

    if register_files:
        conn.execute(
            "DELETE FROM files WHERE dataset_id = ?",
            [dataset_id],
        )
        conn.executemany(
            """
            INSERT INTO files
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


def reconcile_campaign_datasets(conn, campaign_name, current_dataset_ids):
    """Remove campaign datasets absent from its authoritative registration."""
    current_dataset_ids = set(current_dataset_ids)
    existing_dataset_ids = {
        row[0]
        for row in conn.execute(
            "SELECT dataset_id FROM datasets WHERE campaign = ?",
            [campaign_name],
        ).fetchall()
    }
    stale_dataset_ids = existing_dataset_ids - current_dataset_ids

    if stale_dataset_ids:
        parameters = [(dataset_id,) for dataset_id in stale_dataset_ids]
        conn.executemany(
            "DELETE FROM files WHERE dataset_id = ?",
            parameters,
        )
        conn.executemany(
            "DELETE FROM datasets WHERE dataset_id = ?",
            parameters,
        )

    return {
        "current": len(current_dataset_ids),
        "removed": len(stale_dataset_ids),
    }
