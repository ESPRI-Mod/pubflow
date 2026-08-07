from pathlib import Path
import shutil

from workflow.config import get_publisher_config
from workflow.database import connect
from workflow.campaign import get_campaign


def get_pending_datasets(campaign, limit=None):
    conn = connect()
    query = """
            SELECT dataset_id, \
                   mapfile
            FROM datasets
            WHERE campaign = ?
              AND publication_status = 'PENDING'
            ORDER BY dataset_id \
            """
    params = [campaign]
    if limit:
        query += " LIMIT ?"
        params.append(limit)
    rows = conn.execute(
        query,
        params,
    ).fetchall()
    conn.close()
    return rows


def validate_mapfile(mapfile):
    """
    Validate that a mapfile exists and is readable.
    """
    path = Path(mapfile)
    if not path.exists():
        return False, "Mapfile does not exist"
    if not path.is_file():
        return False, "Mapfile is not a file"
    try:
        with open(path) as f:
            first_line = f.readline()
        if not first_line:
            return False, "Mapfile is empty"
    except Exception as exc:
        return False, f"Cannot read mapfile: {exc}"
    return True, None


def validate_files(mapfile):
    """
    Validate that all files referenced by a mapfile exist.
    """
    failures = []
    with open(mapfile) as f:
        for line_number, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            fields = [
                x.strip()
                for x in line.split("|")
            ]
            filepath = fields[1]
            if not Path(filepath).exists():
                failures.append(
                    f"Line {line_number}: missing file {filepath}"
                )
    if failures:
        return False, failures
    return True, None


def validate_publisher():
    publisher = get_publisher_config()
    executable = publisher["executable"]
    if shutil.which(executable) is None:
        return (False,
                f"Publisher executable not found: {executable}")
    return True, None


def validate_dataset(dataset_id, mapfile):
    results = []
    ok, message = validate_mapfile(mapfile)
    if not ok:
        results.append(message)
        return False, results
    ok, message = validate_files(mapfile)
    if not ok:
        results.extend(message)
    return len(results) == 0, results


def validate_campaign(campaign, limit=None):
    campaign_config = get_campaign(campaign)
    datasets = get_pending_datasets(campaign_config["name"],limit)
    print()
    print(f"Campaign: {campaign_config["name"]}")
    print(f"Datasets checked: {len(datasets)}")
    print()
    passed = 0
    failed = 0
    for dataset_id, mapfile in datasets:
        ok, errors = validate_dataset(dataset_id,mapfile)
        if ok:
            passed += 1
            print(f"PASS {dataset_id}")
        else:
            failed += 1
            print()
            print(f"FAIL {dataset_id}")
            for error in errors:
                print(f"  - {error}")
    publisher_ok, publisher_error = (validate_publisher())
    if not publisher_ok:
        print()
        print("Publisher check FAILED:")
        print(publisher_error)
    print()
    print("Summary")
    print("-------")
    print(f"PASS: {passed}")
    print(f"FAIL: {failed}")
    return failed == 0 and publisher_ok
