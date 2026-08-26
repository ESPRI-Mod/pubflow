import os
import shutil
from pathlib import Path

from workflow.config import get_publisher_config
from workflow.publisher_output import publisher_dataset_id


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def get_stac_items_directory():
    publisher = get_publisher_config()
    configured = os.environ.get(
        "PUBFLOW_STAC_ITEMS_DIR",
        publisher.get("stac_items", {}).get("directory", "stac-items"),
    )
    directory = Path(os.path.expandvars(str(configured))).expanduser()
    if not directory.is_absolute():
        directory = PROJECT_ROOT / directory
    return directory.resolve()


def generated_stac_path(staging_directory, dataset_id):
    path = (
        Path(staging_directory)
        / f"{publisher_dataset_id(dataset_id)}.json"
    )
    return path if path.is_file() else None


def retained_stac_path(dataset_id):
    return (
        get_stac_items_directory()
        / f"{publisher_dataset_id(dataset_id)}.json"
    )


def reconcile_stac_item(staging_directory, dataset_id, status):
    """Retain failed items and remove stale items after a successful retry."""
    destination = retained_stac_path(dataset_id)
    if status == "SUCCESS":
        if destination.is_file() and not destination.is_symlink():
            destination.unlink()
            return "removed", None
        return "absent", None

    if status != "FAILED":
        return "ignored", None

    source = generated_stac_path(staging_directory, dataset_id)
    if source is None:
        return "missing", None

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    shutil.copy2(source, temporary)
    temporary.replace(destination)
    return "retained", str(destination)

