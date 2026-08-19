#!/usr/bin/env python3

import yaml

from workflow.database import connect

CAMPAIGNS_FILE = (
    "config/campaigns.yml"
)


def load_campaigns():
    with open(CAMPAIGNS_FILE) as f:
        config = yaml.safe_load(f)
    conn = connect()
    for name, campaign in config["campaigns"].items():
        archive = campaign.get("archive", {})
        archive_root = None
        archive_depth = None
        if archive.get("enabled", False):
            archive_root = archive.get("root")
            archive_depth = archive.get("depth")
        conn.execute(
            """
            INSERT OR REPLACE INTO campaigns
            (
                name,
                project,
                activity,
                institution,
                mapfile_root,
                archive_root,
                archive_depth
            )

            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                name,
                campaign["project"],
                campaign["activity"],
                campaign["institution"],
                campaign["mapfile_root"],
                archive_root,
                archive_depth
            ],
        )

        print(
            f"Loaded campaign: {name}"
        )

    conn.close()


def get_campaign(name):
    """
    Retrieve a campaign from DuckDB.
    """

    conn = connect()

    row = conn.execute(
        """
        SELECT name,
               project,
               activity,
               institution,
               mapfile_root,
               archive_root,
               archive_depth

        FROM campaigns

        WHERE name = ?
        """,
        [name],
    ).fetchone()
    conn.close()
    if row is None:
        raise ValueError(f"Unknown campaign: {name}")
    return {
        "name": row[0],
        "project": row[1],
        "activity": row[2],
        "institution": row[3],
        "mapfile_root": row[4],
        "archive_root": row[5],
        "archive_depth": row[6],
    }
