#!/usr/bin/env python3

import os
from pathlib import Path

import duckdb

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB = Path(os.path.expandvars(os.environ.get(
    "PUBFLOW_DB_PATH",
    str(PROJECT_ROOT / "db" / "publications.duckdb"),
))).expanduser().resolve()
SCHEMA = PROJECT_ROOT / "db" / "schema.sql"

DB.parent.mkdir(parents=True, exist_ok=True)
conn = duckdb.connect(str(DB))

with open(SCHEMA) as f:
    conn.execute(f.read())

conn.close()

print("Database initialized")
