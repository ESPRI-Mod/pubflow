import json
from pathlib import Path


def _validate_pattern(pattern):
    if not pattern or Path(pattern).name != pattern or ".." in pattern:
        raise ValueError("pattern must be a filename pattern, not a path")


def is_generated_stac_item(path):
    """Identify publisher-generated STAC Items without trusting the filename alone."""
    if path.is_symlink() or not path.is_file():
        return False
    try:
        with open(path) as stream:
            item = json.load(stream)
    except (OSError, json.JSONDecodeError):
        return False

    item_id = item.get("id")
    return (
        item.get("type") == "Feature"
        and isinstance(item.get("stac_version"), str)
        and isinstance(item.get("stac_extensions"), list)
        and isinstance(item_id, str)
        and path.name == f"{item_id}.json"
    )


def cleanup_stac_items(directory=".", pattern="CMIP*.json", delete=False):
    """Find, and optionally delete, generated STAC Items in one directory."""
    _validate_pattern(pattern)
    root = Path(directory).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"directory does not exist: {root}")

    candidates = [
        path
        for path in sorted(root.glob(pattern))
        if is_generated_stac_item(path)
    ]
    if delete:
        for path in candidates:
            path.unlink()

    return {
        "directory": str(root),
        "pattern": pattern,
        "count": len(candidates),
        "deleted": len(candidates) if delete else 0,
        "files": [str(path) for path in candidates],
    }

