#!/usr/bin/env python3

import argparse
import json
from pathlib import Path

import duckdb


DB = "/home/esguser/esgf-publisher-workflow/db/publications.duckdb"


def parse_drs(dataset_id):
    """
    Parse ESGF dataset ID:

    project.activity.institution.source.experiment.member.table.variable.grid.version
    """

    parts = dataset_id.split(".")

    if len(parts) != 10:
        raise ValueError(
            f"Unexpected DRS format ({len(parts)} fields): {dataset_id}"
        )

    return {
        "project": parts[0],
        "activity": parts[1],
        "institution": parts[2],
        "source": parts[3],
        "experiment": parts[4],
        "member": parts[5],
        "table": parts[6],
        "variable": parts[7],
        "grid": parts[8],
        "version": parts[9],
    }


def parse_mapfile(mapfile):

    files = []
    dataset_id = None

    with open(mapfile) as f:

        for line in f:

            line = line.strip()

            if not line:
                continue

            fields = [
                x.strip()
                for x in line.split("|")
            ]

            dataset = fields[0]
            filepath = fields[1]
            size = fields[2]

            metadata = {}

            for item in fields[3:]:

                if "=" in item:
                    key, value = item.split("=", 1)
                    metadata[key] = value

            if dataset_id is None:
                dataset_id = dataset

            elif dataset_id != dataset:
                raise ValueError(
                    f"Multiple dataset IDs in {mapfile}"
                )

            files.append(
                {
                    "file_path": filepath,
                    "file_size": int(size),
                    "checksum": metadata.get("checksum"),
                    "mod_time": metadata.get("mod_time"),
                }
            )

    return dataset_id, files


def get_campaign(conn, campaign_name):

    campaign = conn.execute(
        """
        SELECT
            name,
            project,
            activity,
            institution,
            mapfile_root,
            archive_root
        FROM campaigns
        WHERE name = ?
        """,
        [campaign_name],
    ).fetchone()

    if campaign is None:
        raise ValueError(
            f"Unknown campaign: {campaign_name}"
        )

    return {
        "name": campaign[0],
        "project": campaign[1],
        "activity": campaign[2],
        "institution": campaign[3],
        "mapfile_root": campaign[4],
        "archive_root": campaign[5],
    }


def register_dataset(conn, mapfile, campaign):

    dataset_id, files = parse_mapfile(mapfile)

    drs = parse_drs(dataset_id)

    # Validate campaign metadata
    for key in ["project", "activity", "institution"]:

        if drs[key] != campaign[key]:

            raise ValueError(
                f"{key} mismatch: "
                f"dataset={drs[key]} "
                f"campaign={campaign[key]}"
            )

    conn.execute(
        """
        INSERT OR IGNORE INTO datasets
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
            campaign["name"],
            drs["project"],
            drs["activity"],
            drs["institution"],
            json.dumps(drs),
            str(mapfile),
        ],
    )

    for f in files:

        conn.execute(
            """
            INSERT OR IGNORE INTO files
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
                dataset_id,
                f["file_path"],
                f["file_size"],
                f["checksum"],
                f["mod_time"],
            ],
        )

    print(
        f"Registered {dataset_id} "
        f"({len(files)} files)"
    )


def main():

    parser = argparse.ArgumentParser(
        description="Register ESGF mapfiles into DuckDB"
    )

    parser.add_argument(
        "campaign",
        help="Campaign name from campaigns table"
    )

    args = parser.parse_args()

    conn = duckdb.connect(DB)

    campaign = get_campaign(
        conn,
        args.campaign
    )

    root = Path(
        campaign["mapfile_root"]
    )

    if not root.exists():
        raise RuntimeError(
            f"Mapfile root does not exist: {root}"
        )

    mapfiles = list(
        root.rglob("*.map")
    )

    print(
        f"Campaign: {campaign['name']}"
    )

    print(
        f"Found {len(mapfiles)} mapfiles"
    )

    for mapfile in mapfiles:

        try:

            register_dataset(
                conn,
                mapfile,
                campaign
            )

        except Exception as e:

            print(
                f"FAILED {mapfile}: {e}"
            )

    conn.close()


if __name__ == "__main__":
    main()
