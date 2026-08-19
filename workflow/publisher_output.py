import re


PUB_STATUS_PATTERN = re.compile(
    r"PUB_STATUS=(PASS|FAIL)\s+id=([^\s|]+)(?:\|\S+)?"
)


def publisher_dataset_id(dataset_id):
    """Return the dataset ID format emitted by esgpublish."""
    if "#" not in dataset_id:
        return dataset_id
    base, version = dataset_id.rsplit("#", 1)
    if not version.startswith("v"):
        version = f"v{version}"
    return f"{base}.{version}"


def parse_publication_statuses(output, datasets):
    """Map esgpublish PUB_STATUS lines back to registered dataset IDs."""
    aliases = {
        publisher_dataset_id(dataset_id): dataset_id
        for dataset_id, _, _ in datasets
    }
    statuses = {}
    unknown = []

    for match in PUB_STATUS_PATTERN.finditer(output or ""):
        publisher_status, emitted_id = match.groups()
        dataset_id = aliases.get(emitted_id)
        if dataset_id is None:
            unknown.append(emitted_id)
            continue
        statuses[dataset_id] = (
            "SUCCESS" if publisher_status == "PASS" else "FAILED"
        )

    return statuses, unknown
