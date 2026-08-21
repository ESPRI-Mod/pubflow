# ESGF Publisher Workflow

`pubflow` is a lightweight workflow manager for publishing **ESGF datasets** from mapfiles. It provides:

- A persistent **DuckDB database** for tracking campaigns, datasets, files, publication attempts, and archival state.

- A **Typer-based command-line interface** for managing publication workflows.

- Configurable **batch publication**, retries, and dry-run support.

- Multiple **ESGF publisher configuration profiles** for different deployment environments.

- **CSV and Grist** status export capabilities.

- A **decoupled archival workflow** for computing centres where the publisher lacks direct write access to the final
  storage location.

The workflow is designed to keep orchestration concerns separate from the underlying `esgpublish` application and from
the computing centres hosting the published data.

---

## Overview

The workflow is divided into registration, publication, status export, and archival stages:

```text

                         Publisher VM

                              |

             +----------------+----------------+

             |                                 |

             v                                 v

        Register                         Publish

             |                                 |

             v                                 v

           DuckDB <---------------- Publication status

             |

       +-----+------+

       |            |

       v            v

     Export       Grist

                    |

                    v

              Dashboard

```

Archival is deliberately decoupled:

```text

Publisher VM

    |

    v

Generate archive tasks

    |

    v

archive_tasks.csv

    |

    | portable transfer

    v

Computing Centre

    |

    v

bin/archive.py

    |

    v

archive_results.csv

```

> **Note:** The publisher does **not** need write access to the final archive filesystem.

---

## Installation

The project is a Python application that exposes the `pubflow` command.

After installing the package in the appropriate environment:

```bash

pubflow --help

```

### Main Commands

| Command | Description |

|---|---|

| `pubflow register` | Register datasets and files from mapfiles |

| `pubflow publish` | Publish datasets |

| `pubflow run-diagnostics` | Re-run failed datasets individually and capture server diagnostics |

| `pubflow validate` | Validate registered datasets |

| `pubflow export` | Export database state to CSV |

| `pubflow grist` | Synchronize workflow status with Grist |

| `pubflow archive` | Generate portable archive tasks |

> **Note:** Running `pubflow` without a command displays the version.

---

## Configuration

Pubflow uses several configuration files, each with a deliberately separate responsibility.

```text

config/

├── campaigns.yml

└── publisher.yml

~/.esg/

├── esg.yaml.EASTINT

├── esg.yaml.WESTINT

├── esg.yaml.EAST

└── esg.yaml.WEST

```

### Campaign Configuration

Campaigns are defined in:

```text

config/campaigns.yml

```

A campaign contains the project/activity metadata and the locations required by the workflow:

```yaml

campaigns:

  tipmip-cnrm:

    project: CMIP6Plus

    activity: TIPMIP

    institution: CNRM-CERFACS

    mapfile_root: /modfs/esgf/topublish/CNRM-CERFACS/.mapfiles

    archive:

      enabled: true

      root: /mnt/scality/WCRP/CMIP6Plus/TIPMIP/CNRM-CERFACS

      depth: experiment_id

```

Campaigns therefore retain both:

- `project`

- `activity`

These fields are also exported to Grist and are used by the dashboard to filter campaigns.

---

## Publisher Configuration

Publisher execution is configured separately in:

```text

config/publisher.yml

```

For example:

```yaml

publisher:

  executable: esgpublish

  arguments:

    - --no-xarray

  batch:

    size: 50

  execution:

    dry_run: false

  logging:

    directory: logs

  retry:

    enabled: true

    max_attempts: 3

  mapfile_path_mappings:

    - from: /ccc/work/cont003/cmip6/cmip6

      to: /mnt/tgcc/

esg:

  config:

    active: EAST-int

    profiles:

      EAST-int:

        path: ~/.esg/esg.yaml.EASTINT

      WEST-int:

        path: ~/.esg/esg.yaml.WESTINT

      EAST-prod:

        path: ~/.esg/esg.yaml.EAST

      WEST-prod:

        path: ~/.esg/esg.yaml.WEST

```

The publisher configuration intentionally contains only the generic `esgpublish` execution settings.

The actual ESG publisher configuration is selected through an **ESG configuration profile**.

When a dataset is published, Pubflow effectively constructs:

```bash

esgpublish \

    --no-xarray \

    --config ~/.esg/esg.yaml.EASTINT \

    --map <mapfile>

```

This keeps the publisher command configuration independent from the campaign definitions.

### ESG Configuration Profiles

The profile system allows the same Pubflow installation to target different ESG publisher environments.

For example:

```text

EAST-int

WEST-int

EAST-prod

WEST-prod

```

The active profile is selected in `publisher.yml`:

```yaml

esg:

  config:

    active: EAST-int

```

Before publication, Pubflow verifies that:

1. The selected profile exists.

2. The configured ESG configuration file exists.

An invalid profile or missing configuration therefore fails early rather than producing a less useful publisher error.

> **Note:** Pubflow does not currently interpret or modify the contents of the `esg.yaml` files. They remain
configuration files owned by the ESG publisher environment.

---

## Authentication and EGI Check-in Tokens

Authentication is deliberately handled by `esgpublish`.

The ESG publisher stores its authentication token in the location configured by the selected ESG configuration, for
example:

```text

~/.esgf-publisher.json

```

When the token expires, `esgpublish` can initiate the EGI Check-in authentication flow and display the URL that must be
visited to renew authentication.

Pubflow does **not** currently attempt to manage or renew these tokens.

This is intentional:

- `esgpublish` remains responsible for authentication.

- Pubflow does not need to understand the token format.

- Authentication behaviour remains consistent with standalone `esgpublish`.

- Manual renewal remains possible when required.

> **Operational note:** A publication process may pause while waiting for manual authentication. For long-running
campaigns, running Pubflow inside `tmux` or another persistent terminal session is recommended.

---

## Database

Pubflow uses **DuckDB** to maintain persistent workflow state.

The database tracks:

- Campaigns

- Datasets

- Files

- Publication attempts

- Archive status

The schema is defined in:

```text

db/schema.sql

```

### Initialization

Initialize the database with:

```bash

python bin/init_db.py

```

Load campaign definitions with:

```bash

python workflow/campaign.py

```

---

## Registration

Registration scans a campaign's mapfile directory and registers datasets and their files in DuckDB.

```bash

pubflow register tipmip-cnrm

```

Example output:

```text

Found 2446 mapfiles

Registered CMIP6Plus.... (12 files)

Registered CMIP6Plus.... (8 files)

...

Completed: 2445 succeeded, 1 failed

```

> **Note:** Registration **does not publish anything**.

Registration is therefore safe to run before a publication campaign begins.

---

## Publishing

Publish datasets for a campaign:

```bash

pubflow publish tipmip-cnrm

```

### Controlled testing

Use a limit when testing:

```bash

pubflow publish tipmip-cnrm --limit 10

```

Publication is performed in batches, with one `esgpublish` directory invocation
per batch:

```bash

pubflow publish tipmip-cnrm --batch-size 50

```

The default batch size is 50. Set `--batch-size 1` to retain the previous
one-invocation-per-dataset behavior.

`PUB_STATUS=PASS` and `PUB_STATUS=FAIL` messages are recorded per dataset. If
the publisher stops at the first failure, mapfiles it did not reach remain
`PENDING` and are selected for the next batch. The executor tracks each
reported publication attempt and updates the corresponding dataset state in
DuckDB.

### Retries

Retries can be configured in `publisher.yml`:

```yaml

publisher:

  retry:

    enabled: true

    max_attempts: 3

```

### Dry Run

The publisher execution mode can be controlled through:

```yaml

publisher:

  execution:

    dry_run: false

```

---

## Publication Logging

Each publication run receives a unique run identifier and an associated log file.

Run information includes:

- Campaign

- Run ID

- Start time

- End time

- Batch information

- Mapfile path mappings

- Dataset successes

- Dataset failures

- Exit codes

- Error messages

- Publication summary

The configured log directory is:

```yaml

publisher:

  logging:

    directory: logs

```

Publication failures are also persisted in DuckDB and exported to Grist.

---

## Failure Diagnostics

Run diagnostics after a publication campaign to retry its currently failed
datasets one at a time and capture the detailed response returned by the
transaction service:

```bash

pubflow run-diagnostics tipmip-cnrm

```

This is a real publication attempt, not a dry run. A dataset which now emits
`PUB_STATUS=PASS` is recorded as `SUCCESS`; a dataset which still fails remains
`FAILED`. If the publisher emits no recognizable status, Pubflow records an
execution error without changing the dataset's publication status.

Each run creates a directory below
`logs/<campaign>/diagnostics/<diagnostic-run-id>/` containing one full publisher
log per dataset and a concise `diagnostics.csv`. Results are also appended to
the DuckDB `diagnostic_attempts` table. Existing publication records are not
removed or replaced.

Pubflow always invokes the publisher with both `--verbose` and `--save-stac`.
The generated item is used from an isolated temporary directory and is deleted
after the attempt by default. To retain a copy alongside the diagnostic logs:

```bash

pubflow run-diagnostics tipmip-cnrm --persist-stac-item

```

The installed EAST publisher must expose the transaction service response in
verbose output for structured HTTP/STAC validation details to be extracted. It
must also support saving STAC from its EGI transaction client. Without those
publisher changes, the command still preserves the complete verbose log and
classifies the failure as unclassified.

Grist synchronization is enabled by default. Use `--no-sync-grist` to keep the
run local, or create the `Diagnostics` table described below before enabling
synchronization. A Grist failure is reported as a warning and does not discard
the DuckDB, CSV, or log results.

Useful limiting form for the first production run:

```bash

pubflow run-diagnostics tipmip-cnrm --limit 5 --no-sync-grist

```

---

## Validation

Validate registered datasets without triggering publication:

```bash

pubflow validate tipmip-cnrm

```

Validation does not invoke the publication workflow.

---

## Export

Export the current database state to CSV:

```bash

pubflow export tipmip-cnrm

```

CSV export is useful for inspecting workflow state outside the Pubflow environment or for downstream processing.

---

## Grist Integration

Pubflow can synchronize campaign, dataset, and failure information with **Grist**:

```bash

pubflow grist sync

```

The Grist document contains three main workflow tables and an optional
diagnostics table:

```text

Campaigns

Datasets

Failures

Diagnostics

```

### Campaigns

The campaign table contains:

| Field | Description |

|---|---|

| campaign | Campaign name |

| project | Project identifier |

| activity | Activity identifier |

| total | Total datasets |

| published | Successfully published datasets |

| failed | Failed datasets |

| pending | Datasets still pending |

The dashboard can therefore filter campaigns by:

- Project

- Activity

This allows campaigns belonging to different projects or activities to be monitored from the same Grist document.

### Datasets

| Field | Description |

|---|---|

| dataset_id | Dataset identifier |

| campaign | Associated campaign |

| publication_status | Current publication status |

| last_attempt_status | Status of the last publication attempt |

| finished_at | Timestamp of the last attempt |

| log_file | Associated log file |

Typical publication statuses are:

```text

SUCCESS

FAILED

PENDING

```

### Failures

| Field | Description |

|---|---|

| dataset_id | Dataset identifier |

| campaign | Associated campaign |

| run_id | Publication run identifier |

| started_at | Start timestamp |

| finished_at | Finish timestamp |

| status | Publication status |

| exit_code | Publisher exit code |

| log_file | Associated log file |

| error_message | Error information |

### Diagnostics

Create a Grist table with table ID `Diagnostics` and these columns before using
the default diagnostic synchronization:

| Field | Description |

|---|---|

| diagnostic_id | Unique diagnostic attempt identifier |

| diagnostic_run_id | Identifier shared by one diagnostic run |

| dataset_id | Dataset identifier |

| campaign | Associated campaign |

| started_at | Attempt start timestamp |

| finished_at | Attempt finish timestamp |

| outcome | `RECOVERED`, `DIAGNOSED`, `UNCLASSIFIED`, or `EXECUTION_ERROR` |

| publisher_status | Recognized publisher status, when present |

| exit_code | Publisher process exit code |

| http_status | Transaction service HTTP status |

| error_type | RFC problem type returned by the service |

| schema_url | STAC schema implicated by validation |

| rejected_value | Value rejected by schema validation |

| suggested_value | Closest accepted enum value, when identifiable |

| summary | Condensed diagnostic message |

| server_instance | Server-side problem instance identifier |

| log_file | Full local verbose log path |

| stac_file | Retained local STAC JSON path, when requested and available |

> **Note:** Grist credentials are supplied via **environment variables** and are not stored in the repository.

### Grist Dashboard

The Grist document contains a custom dashboard widget providing:

- Overall dataset KPIs

- Published/failed/pending counts

- Campaign-level publication progress

- Project filtering

- Activity filtering

- Campaign progress bars

The dashboard is intended as an operational view of the DuckDB workflow state rather than as a replacement for the
database.

---

## Archival Workflow

Archival is **separated from publication**.

The publisher VM may lack write access to the final storage location. Instead, Pubflow generates a portable list of
archive operations.

### Eligibility

A dataset becomes eligible for archival when:

```text

publication_status = SUCCESS

archive_status = PENDING

```

### Generate Archive Tasks

```bash

pubflow archive tipmip-cnrm

```

Use a limit for testing:

```bash

pubflow archive tipmip-cnrm --limit 10

```

The command generates a CSV containing:

| Column | Description |

|---|---|

| dataset_id | Dataset identifier |

| mapfile | Path to the mapfile |

| archive_path | Destination archive path |

Example:

```csv

dataset_id,mapfile,archive_path

CMIP6Plus.TIPMIP.CNRM-CERFACS.CNRM-ESM2-1.esm-piControl.r1i1p2f2.AERmon.cdnc.gr.v20231218,/modfs/esgf/topublish/CNRM-CERFACS/.mapfiles/....map,/mnt/scality/WCRP/CMIP6Plus/TIPMIP/CNRM-CERFACS/CNRM-ESM2-1/esm-piControl/.mapfiles/....map

```

> **Note:** The archive path is dynamically generated from the dataset's DRS and the campaign's configured archive
depth.

---

## Archive Depth

The `archive.depth` setting determines how deep the archive destination should be created within the project's **DRS
hierarchy**.

For example:

```yaml

archive:

  depth: experiment_id

```

can produce:

```text

/mnt/scality/WCRP/CMIP6Plus/TIPMIP/CNRM-CERFACS/

    CNRM-ESM2-1/

        esm-piControl/

            .mapfiles/

```

The DRS is interpreted using `esgvoc`.

The workflow does not hard-code project-specific DRS fields, allowing different projects to use their own vocabulary and
DRS definitions.

---

## DRS Handling

The workflow uses **`esgvoc`** for DRS interpretation.

For example, for the `cmip6plus` project, ESGVOC provides DRS components including:

- `mip_era`

- `activity_id`

- `institution_id`

- `source_id`

- `experiment_id`

- `member_id`

- `table_id`

- `variable_id`

- `grid_label`

- `version`

> **Note:** Pubflow does not contain a CMIP6Plus-specific hard-coded `parse_drs()` implementation. This allows different
projects to use their own vocabulary and DRS definitions.

---

## Standalone Archive Executor

The generated **archive task CSV** is designed to be portable.

It can be transferred to a computing centre and executed independently:

```bash

python bin/archive.py archive_tasks.csv

```

### Requirements

The archive executor does **not** require:

- DuckDB

- Grist credentials

- Publisher credentials

- Access to the publisher VM

- The Pubflow workflow database

It reads the task CSV and copies each mapfile to its specified archive destination.

### Results CSV

Request a separate results CSV with:

```bash

python bin/archive.py archive_tasks.csv \

    --results archive_results.csv

```

Result format:

| Column | Description |

|---|---|

| dataset_id | Dataset identifier |

| mapfile | Path to the mapfile |

| archive_path | Destination archive path |

| status | Status of the operation |

| error_message | Error message, if any |

Example:

```csv

TEST.DATASET,/source/test.map,/archive/.mapfiles/test.map,SUCCESS,

```

The current executor uses Python's `shutil.copy2()` and creates the destination directory when necessary.

---

## Repository Structure

```text

esgf-publisher-workflow/

│

├── config/

│   ├── campaigns.yml

│   └── publisher.yml

│

├── db/

│   └── schema.sql

│

├── logs/

│

├── pubflow/

│   └── cli.py

│

├── workflow/

│   ├── archive.py

│   ├── campaign.py

│   ├── config.py

│   ├── database.py

│   ├── exporter.py

│   ├── executor.py

│   ├── grist.py

│   └── registry.py

│

├── bin/

│   ├── init_db.py

│   ├── publisher.py

│   └── archive.py

│

└── pyproject.toml

```

> **Note:** The exact contents may evolve as the workflow develops.

---

## Environment Variables

External credentials and connection information are supplied via environment variables.

### Pubflow paths

Repository-owned paths are resolved from the installed project location, so
commands do not depend on the current working directory. Deployments can
override them when required:

```bash

export PUBFLOW_DB_PATH=/path/to/publications.duckdb

export PUBFLOW_CONFIG_DIR=/path/to/config

export PUBFLOW_CAMPAIGNS_FILE=/path/to/campaigns.yml

```

Relative logging and campaign mapfile paths are resolved from the project
root. User-relative paths such as `~/.esg/esg.yaml.EASTINT` and environment
variables in configured paths are expanded automatically.

### Grist

```bash

export GRIST_BASE_URL=...

export GRIST_API_KEY=...

export GRIST_DOC_ID=...

```

These variables should **not** be committed to the repository.

For permanent local configuration, they can be added to the appropriate shell configuration, such as:

```text

~/.bashrc

```

---

## Development Philosophy

Pubflow intentionally separates responsibilities between the workflow manager, ESG publisher, and computing centre.

### Pubflow

Pubflow is responsible for:

- Campaign configuration

- Dataset registration

- Publication orchestration

- Batch management

- Retry handling

- Publication state tracking

- Logging

- Status export

- Grist synchronisation

- Archive task generation

- ESG publisher profile selection

### `esgpublish`

The ESG publisher remains responsible for:

- Dataset extraction

- ESG metadata generation

- ESG publication

- STAC interaction

- EGI Check-in authentication

- Token management

### Computing Centre

The computing centre is responsible for:

- Executing archive tasks

- Writing mapfiles to the final archive location

- Returning archive results

This separation keeps Pubflow lightweight and avoids duplicating functionality already provided by the ESG publisher.

---

## Current Status

### Implemented Features

- Campaign configuration

- Project/activity metadata

- DuckDB database

- Dataset registration

- Publication workflow

- Batch publication

- Publication retries

- Dry-run configuration

- Publication logging

- Validation workflow

- CSV export

- Grist synchronisation

- Grist operational dashboard

- Project/activity filtering in Grist

- `pubflow` CLI

- ESGVOC-based DRS parsing

- Configurable archive depth

- Portable archive task generation

- Standalone archive executor

- Archive result CSV

- Multiple ESG publisher configuration profiles

- Runtime `--config` selection for `esgpublish`

- Publisher-side mapfile path mappings

---

## Planned Improvements

Potential future improvements include:

- Safe handling of existing archive destination files

- Resumable archive operations

- Explicit archive conflict detection

- Import of archive results into the workflow database

- More detailed Grist campaign/run visualisations

- Additional operational monitoring for long-running publication processes

- Optional inspection of selected ESG publisher configuration values

Authentication/token management is **not currently planned for Pubflow**, as this functionality is already handled by
`esgpublish`.

---

## Typical Workflow

A standard publication campaign follows these steps:

```bash

# Initialize the database

python bin/init_db.py

# Load campaign definitions

python workflow/campaign.py

# Register datasets

pubflow register tipmip-cnrm

# Optionally validate datasets

pubflow validate tipmip-cnrm

# Test a small publication batch

pubflow publish tipmip-cnrm --limit 10

# Publish the campaign

pubflow publish tipmip-cnrm

# Export status

pubflow export tipmip-cnrm

# Synchronize status to Grist

pubflow grist sync

# Generate archive tasks

pubflow archive tipmip-cnrm

# Transfer archive_tasks.csv to the computing centre

# Execute archive tasks there

python bin/archive.py archive_tasks.csv \

    --results archive_results.csv

```

For long-running publication campaigns, running Pubflow inside a persistent terminal session such as `tmux` is
recommended.
