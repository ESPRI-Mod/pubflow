import json


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
    """
    Parse an ESGF mapfile.

    Assumptions:
    - one mapfile represents one dataset
    - each line represents one file
    """

    dataset_id = None
    files = []

    with open(mapfile) as f:

        for line in f:

            line = line.strip()

            if not line:
                continue

            fields = [
                x.strip()
                for x in line.split("|")
            ]

            if len(fields) < 3:
                raise ValueError(
                    f"Malformed mapfile line in {mapfile}: {line}"
                )

            dataset = fields[0]
            filepath = fields[1]
            size = fields[2]

            metadata = {}

            for item in fields[3:]:

                if "=" in item:
                    key, value = item.split("=", 1)

                    metadata[key.strip()] = value.strip()

            if dataset_id is None:

                dataset_id = dataset

            elif dataset_id != dataset:

                raise ValueError(
                    f"Multiple dataset IDs found in {mapfile}"
                )

            files.append(
                {
                    "file_path": filepath,
                    "file_size": int(size),
                    "checksum": metadata.get("checksum"),
                    "mod_time": metadata.get("mod_time"),
                }
            )

    if dataset_id is None:
        raise ValueError(
            f"No dataset found in {mapfile}"
        )

    return dataset_id, files


def register_dataset(
        conn,
        campaign_name,
        campaign,
        mapfile
):
    """
    Register one dataset from one mapfile.
    """

    dataset_id, files = parse_mapfile(
        mapfile
    )

    drs = parse_drs(
        dataset_id
    )

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

    for file in files:
        conn.execute(
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
                dataset_id,
                file["file_path"],
                file["file_size"],
                file["checksum"],
                file["mod_time"],
            ],
        )

    return {
        "dataset_id": dataset_id,
        "files": len(files),
    }
