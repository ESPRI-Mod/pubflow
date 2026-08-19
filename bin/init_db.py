#!/usr/bin/env python3

import duckdb

#DB = "/home/esguser/esgf-publisher-workflow/db/publications.duckdb"
#SCHEMA = "/home/esguser/esgf-publisher-workflow/db/schema.sql"
DB="/Users/atefbennasser/Documents/Codex/2026-08-18/https-github-com-espri-mod-pubflow/work/pubflow/db/publications.duckdb"
SCHEMA="/Users/atefbennasser/Documents/Codex/2026-08-18/https-github-com-espri-mod-pubflow/work/pubflow/db/schema.sql"

conn = duckdb.connect(DB)

with open(SCHEMA) as f:
    conn.execute(f.read())

conn.close()

print("Database initialized")
