import json
from esgvoc.apps.drs.generator import DrsGenerator


def parse_drs(dataset_id):
    """
    Parse an ESGF dataset ID using the DRS specification provided
    by ESGVOC.

    The dataset ID components are mapped to the ordered DRS parts
    defined by ESGVOC rather than using hardcoded facet names.
    """
    parts = dataset_id.split(".")
    if not parts:
        raise ValueError(
            f"Invalid dataset ID: {dataset_id}"
        )

    # The project identifier is the first DRS component and is
    # also what ESGVOC expects when selecting the project.
    project_id = parts[0].lower()
    generator = DrsGenerator(
        project_id
    )
    drs_parts = generator.directory_specs.parts
    if len(parts) != len(drs_parts):
        raise ValueError(
            f"Unexpected DRS format: dataset ID contains "
            f"{len(parts)} components, but ESGVOC defines "
            f"{len(drs_parts)} DRS components for {project_id}: "
            f"{dataset_id}"
        )
    mapping = {}
    for value, drs_part in zip(parts, drs_parts):
        mapping[drs_part.source_collection] = value
    return mapping


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


def build_drs_path(
        dataset_id,
        depth,
):
    drs = parse_drs(dataset_id)
    if depth not in DRS_FIELDS:
        raise ValueError(
            f"Invalid archival depth: {depth}. "
            f"Expected one of: {', '.join(DRS_FIELDS)}"
        )
    depth_index = DRS_FIELDS.index(depth)

    return Path(
        *(
            drs[field]
            for field in DRS_FIELDS[:depth_index + 1]
        )
    )

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
