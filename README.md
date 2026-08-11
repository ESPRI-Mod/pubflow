# ESGF Publisher Workflow

`pubflow` is a lightweight workflow manager for publishing **ESGF datasets** from mapfiles. It provides:

- A persistent **DuckDB database** for tracking datasets and publication attempts.
- A **Typer-based command-line interface** for managing workflows.
- **CSV/Grist** status export capabilities.
- A **decoupled archival workflow** for computing centers where the publisher lacks direct write access to the final
  storage location.

---

## Overview

The workflow is divided into two main stages:

```
Publisher VM
    |
    +-- Register
    |       |
    +-- Publish
            |
            v
    DuckDB status
            |
    +-------+-------+
    v               v
 Export          Grist
    |
    v
archive_tasks.csv
    | (portable)
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

After installing the package in your environment, run:

```bash
pubflow --help
```

### Main Commands

| Command            | Description                  |
| ------------------ | ---------------------------- |
| `pubflow register` | Register datasets            |
| `pubflow publish`  | Publish datasets             |
| `pubflow validate` | Validate datasets            |
| `pubflow export`   | Export database state to CSV |
| `pubflow grist`    | Synchronize with Grist       |
| `pubflow archive`  | Generate archive tasks       |

> **Note:** Running `pubflow` without a command displays the version.

---

## Configuration

Campaigns are defined in `config/campaigns.yml`. A campaign includes the necessary information to locate and publish its
mapfiles:

```yaml
campaigns:
  tipmip-cnrm:
    project: CMIP6Plus
    activity: TIPMIP
    institution: CNRM-CERFACS
    mapfile_root: /modfs/esgf/topublish/CNRM-CERFACS/.mapfiles
    publisher:
      executable: esgpublish
      arguments: []
    archive:
      enabled: true
      root: /mnt/scality/WCRP/CMIP6Plus/TIPMIP/CNRM-CERFACS
      depth: experiment_id
```

### Archive Depth

The `archive.depth` setting determines how deep the archive destination should be created within the project’s **DRS
hierarchy**. For example:

```yaml
depth: experiment_id
```

Produces:

```
/mnt/scality/WCRP/CMIP6Plus/TIPMIP/CNRM-CERFACS/
    CNRM-ESM2-1/
        esm-piControl/
            .mapfiles/
```

> **Note:** The **DRS** is interpreted using `esgvoc`. The workflow does **not** hard-code project-specific DRS fields,
allowing flexibility for different projects.

---

## Database

The workflow uses **DuckDB** to maintain persistent state, tracking:

- Campaigns
- Datasets
- Files
- Publication attempts
- Archive status

### Schema

The database schema is defined in `db/schema.sql`.

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

Scan a campaign’s mapfile directory and register datasets/files in **DuckDB**:

```bash
pubflow register tipmip-cnrm
```

### Output Example

```
Found 2446 mapfiles
Registered CMIP6Plus.... (12 files)
Registered CMIP6Plus.... (8 files)
...
Completed: 2445 succeeded, 1 failed
```

> **Note:** Registration **does not** publish anything.

---

## Publishing

Publish datasets for a campaign:

```bash
pubflow publish tipmip-cnrm
```

Use a **limit** for testing or controlled batches:

```bash
pubflow publish tipmip-cnrm --limit 10
```

The executor tracks publication attempts and records their status in the database.

---

## Validation

Validate registered datasets without triggering publication:

```bash
pubflow validate tipmip-cnrm
```

---

## Export

Export the current database state to **CSV**:

```bash
pubflow export tipmip-cnrm
```

Useful for inspecting publication status outside the workflow environment.

---

## Grist Integration

Synchronize campaign, dataset, and failure information with **Grist**:

```bash
pubflow grist --help
```

### Synchronization Command

```bash
pubflow grist sync
```

### Exported Information

#### Campaigns

| Field     | Description        |
| --------- | ------------------ |
| campaign  | Campaign name      |
| total     | Total datasets     |
| published | Published datasets |
| failed    | Failed datasets    |
| pending   | Pending datasets   |

#### Datasets

| Field               | Description                |
| ------------------- | -------------------------- |
| dataset ID          | Dataset identifier         |
| campaign            | Associated campaign        |
| publication status  | Current publication status |
| last attempt status | Status of last attempt     |
| finished timestamp  | Timestamp of last attempt  |
| log file            | Path to log file           |

#### Failures

| Field            | Description            |
| ---------------- | ---------------------- |
| run ID           | Run identifier         |
| campaign         | Associated campaign    |
| dataset ID       | Dataset identifier     |
| start timestamp  | Start time of the run  |
| finish timestamp | Finish time of the run |
| status           | Run status             |
| exit code        | Exit code of the run   |
| log file         | Path to log file       |
| error message    | Error message (if any) |

> **Note:** Grist credentials are supplied via **environment variables** and **not** stored in the repository.

---

## Archival Workflow

Archival is **separated** from publication. The publisher VM may lack write access to the final storage. Instead,
`pubflow` generates a **portable list** of archive operations.

### Eligibility

A dataset becomes eligible for archival when:

- `publication_status = SUCCESS`
- `archive_status = PENDING`

### Generate Archive Tasks

```bash
pubflow archive tipmip-cnrm
```

Use a **limit** for testing:

```bash
pubflow archive tipmip-cnrm --limit 10
```

The command generates a **CSV** with the following columns:

| Column        | Description              |
| ------------- | ------------------------ |
| dataset\_id   | Dataset identifier       |
| mapfile       | Path to the mapfile      |
| archive\_path | Destination archive path |

**Example:**

```csv
dataset_id,mapfile,archive_path
CMIP6Plus.TIPMIP.CNRM-CERFACS.CNRM-ESM2-1.esm-piControl.r1i1p2f2.AERmon.cdnc.gr.v20231218,/modfs/esgf/topublish/CNRM-CERFACS/.mapfiles/....map,/mnt/scality/WCRP/CMIP6Plus/TIPMIP/CNRM-CERFACS/CNRM-ESM2-1/esm-piControl/.mapfiles/....map
```

> **Note:** The archive path is **dynamically generated** from the dataset’s DRS and the campaign’s configured archive
depth.

---

## DRS Handling

The workflow uses **`esgvoc`** for DRS interpretation. For example, for the `cmip6plus` project, `ESGVOC` provides the
following DRS components:

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

> **Note:** The workflow does **not** contain a CMIP6Plus-specific hard-coded `parse_drs()` implementation. This allows
different projects to use their own vocabulary and DRS definitions.

---

## Standalone Archive Executor

The generated **archive task CSV** is designed to be **portable**. It can be transferred to a computing center and
executed independently:

```bash
python bin/archive.py archive_tasks.csv
```

### Requirements

The executor **does not** require:

- DuckDB
- Grist credentials
- Publisher credentials
- Access to the publisher VM
- The `pubflow` workflow database

It reads the task CSV and copies each mapfile to its specified archive destination.

### Results CSV

Request a separate results CSV with:

```bash
python bin/archive.py archive_tasks.csv --results archive_results.csv
```

**Result Format:**

| Column         | Description              |
| -------------- | ------------------------ |
| dataset\_id    | Dataset identifier       |
| mapfile        | Path to the mapfile      |
| archive\_path  | Destination archive path |
| status         | Status of the operation  |
| error\_message | Error message (if any)   |

**Example:**

```csv
TEST.DATASET,/source/test.map,/archive/.mapfiles/test.map,SUCCESS,
```

> **Note:** The current executor uses Python’s `shutil.copy2()` and creates the destination directory when necessary.

---

## Repository Structure

```
esgf-publisher-workflow/
│
├── config/
│   └── campaigns.yml
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

External credentials and connection information are supplied via **environment variables**.

### Grist Configuration

```bash
export GRIST_BASE_URL=...
export GRIST_API_KEY=...
export GRIST_DOC_ID=...
```

> **Note:** These variables **should not** be committed to the repository. For permanent local configuration, add them
to your shell configuration (e.g., `~/.bashrc`).

---

## Development Philosophy

The workflow **intentionally separates** responsibilities:

### Publisher

- Registering datasets
- Publishing datasets
- Recording publication state
- Generating status information
- Generating archive tasks

### Computing Center

- Executing archive tasks
- Writing mapfiles to the final archive location
- Returning archive results

> **Note:** This separation allows archival to operate even when the publisher cannot directly access the target
filesystem. The **archive CSV** acts as the interface between the two environments.

---

## Current Status

### Implemented Features

- Campaign configuration
- DuckDB database
- Dataset registration
- Publication workflow
- Validation workflow
- CSV export
- Grist synchronization
- `pubflow` CLI
- ESGVOC-based DRS parsing
- Configurable archive depth
- Archive task generation
- Standalone archive executor
- Archive result CSV

### Planned Improvements

- Safe handling of existing destination files
- Dry-run mode
- Resumable archive operations
- Explicit conflict detection
- Import of archive results into the workflow database

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

# Publish datasets
pubflow publish tipmip-cnrm

# Validate datasets
pubflow validate tipmip-cnrm

# Export status
pubflow export tipmip-cnrm

# Synchronize status to Grist
pubflow grist sync

# Generate archive tasks
pubflow archive tipmip-cnrm

# Transfer archive_tasks.csv to the computing center
# Execute archive tasks there
python bin/archive.py archive_tasks.csv --results archive_results.csv
```