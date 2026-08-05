#!/usr/bin/env python3

import yaml
import duckdb
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent

DB_PATH = BASE_DIR / "db" / "publications.duckdb"
CAMPAIGNS_FILE = BASE_DIR / "config" / "campaigns.yml"


def load_campaigns():
    with open(CAMPAIGNS_FILE, "r") as f:
        config = yaml.safe_load(f)

    campaigns = config.get("campaigns", {})

    conn = duckdb.connect(str(DB_PATH))

    for name, campaign in campaigns.items():
        conn.execute(
            """
            INSERT INTO campaigns (
                name,
                project,
                activity,
                institution,
                mapfile_root,
                archive_root
            )
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT (name) DO UPDATE SET
                project = excluded.project,
                activity = excluded.activity,
                institution = excluded.institution,
                mapfile_root = excluded.mapfile_root,
                archive_root = excluded.archive_root
            """,
            [
                name,
                campaign["project"],
                campaign["activity"],
                campaign["institution"],
                campaign["mapfile_root"],
                campaign["archive_root"],
            ],
        )

    conn.close()

    print(f"Loaded {len(campaigns)} campaigns")


if __name__ == "__main__":
    load_campaigns()
