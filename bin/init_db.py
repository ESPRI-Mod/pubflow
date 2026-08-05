#!/usr/bin/env python3

import duckdb

DB = "/home/esguser/esgf-publisher-workflow/db/publications.duckdb"
SCHEMA = "/home/esguser/esgf-publisher-workflow/db/schema.sql"


conn = duckdb.connect(DB)

with open(SCHEMA) as f:
    conn.execute(f.read())

conn.close()

print("Database initialized")
