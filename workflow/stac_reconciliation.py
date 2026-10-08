import hashlib
import csv
import json
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from urllib.parse import quote
from uuid import uuid4
from pathlib import Path

import requests

from workflow.database import connect
from workflow.publisher_output import publisher_dataset_id


EAST_STAC_BASE_URL = "https://search.east.esgf.io"
DEFAULT_TIMEOUT_SECONDS = 15
DEFAULT_RETRIES = 2
DEFAULT_CONCURRENCY = 8
DEFAULT_GRACE_SECONDS = 600
VALID_SCOPES = {"all", "successful", "failed", "pending"}
REPORT_FIELDS = (
    "classification", "outcome", "publication_status", "dataset_id",
    "campaign", "collection_id", "item_id", "http_status", "request_url",
    "checked_at", "error_message",
)


def stac_item_url(collection_id, item_id):
    return (
        f"{EAST_STAC_BASE_URL}/collections/{quote(collection_id, safe='')}"
        f"/items/{quote(item_id, safe='')}"
    )


def _response_hash(response):
    return hashlib.sha256(response.content or b"").hexdigest()


def check_stac_item(dataset, timeout_seconds, retries, grace_seconds):
    dataset_id = dataset["dataset_id"]
    collection_id = dataset["project"]
    item_id = publisher_dataset_id(dataset_id)
    request_url = stac_item_url(collection_id, item_id)
    started_at = datetime.now()
    latest_success = dataset["latest_success"]

    if (
        dataset["publication_status"] == "SUCCESS"
        and latest_success is not None
        and (started_at - latest_success).total_seconds() < grace_seconds
    ):
        return {
            **dataset,
            "collection_id": collection_id,
            "item_id": item_id,
            "request_url": request_url,
            "started_at": started_at,
            "finished_at": datetime.now(),
            "outcome": "WAITING",
            "http_status": None,
            "response_item_id": None,
            "response_collection": None,
            "response_hash": None,
            "error_message": "Publication is still within the STAC ingestion grace period",
        }

    last_error = None
    for attempt in range(retries + 1):
        try:
            response = requests.get(request_url, timeout=timeout_seconds)
            if response.status_code == 404:
                outcome = "ABSENT"
                payload = None
            elif response.status_code == 200:
                payload = response.json()
                response_item_id = payload.get("id") if isinstance(payload, dict) else None
                response_collection = (
                    payload.get("collection") if isinstance(payload, dict) else None
                )
                outcome = (
                    "PRESENT"
                    if isinstance(payload, dict)
                    and response_item_id == item_id
                    and response_collection == collection_id
                    and payload.get("type") == "Feature"
                    else "MISMATCH"
                )
                return {
                    **dataset,
                    "collection_id": collection_id,
                    "item_id": item_id,
                    "request_url": request_url,
                    "started_at": started_at,
                    "finished_at": datetime.now(),
                    "outcome": outcome,
                    "http_status": response.status_code,
                    "response_item_id": response_item_id,
                    "response_collection": response_collection,
                    "response_hash": _response_hash(response),
                    "error_message": None if outcome == "PRESENT" else (
                        "STAC response identity or type does not match the request"
                    ),
                }
            elif response.status_code == 429 or response.status_code >= 500:
                response.raise_for_status()
            else:
                return {
                    **dataset,
                    "collection_id": collection_id,
                    "item_id": item_id,
                    "request_url": request_url,
                    "started_at": started_at,
                    "finished_at": datetime.now(),
                    "outcome": "ERROR",
                    "http_status": response.status_code,
                    "response_item_id": None,
                    "response_collection": None,
                    "response_hash": _response_hash(response),
                    "error_message": f"Unexpected HTTP status {response.status_code}",
                }
            if outcome == "ABSENT":
                return {
                    **dataset,
                    "collection_id": collection_id,
                    "item_id": item_id,
                    "request_url": request_url,
                    "started_at": started_at,
                    "finished_at": datetime.now(),
                    "outcome": "ABSENT",
                    "http_status": 404,
                    "response_item_id": None,
                    "response_collection": None,
                    "response_hash": _response_hash(response),
                    "error_message": None,
                }
        except (requests.RequestException, ValueError, json.JSONDecodeError) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            if attempt < retries:
                time.sleep(min(2 ** attempt, 4))

    return {
        **dataset,
        "collection_id": collection_id,
        "item_id": item_id,
        "request_url": request_url,
        "started_at": started_at,
        "finished_at": datetime.now(),
        "outcome": "ERROR",
        "http_status": None,
        "response_item_id": None,
        "response_collection": None,
        "response_hash": None,
        "error_message": last_error or "STAC request failed",
    }


def _select_datasets(campaign, scope, limit, dataset_ids=None):
    if scope not in VALID_SCOPES:
        raise ValueError(f"Unknown STAC reconciliation scope: {scope}")
    conn = connect()
    try:
        query = """
            SELECT d.dataset_id, d.campaign, d.project, d.publication_status,
                   MAX(CASE WHEN p.status = 'SUCCESS' THEN p.finished_at END)
                       AS latest_success
            FROM datasets d
            LEFT JOIN publication_attempts p ON p.dataset_id = d.dataset_id
            WHERE d.registration_status = 'ACTIVE'
        """
        params = []
        if campaign is not None:
            query += " AND d.campaign = ?"
            params.append(campaign)
        status_by_scope = {
            "successful": "SUCCESS",
            "failed": "FAILED",
            "pending": "PENDING",
        }
        if scope in status_by_scope:
            query += " AND d.publication_status = ?"
            params.append(status_by_scope[scope])
        if dataset_ids is not None:
            identifiers = sorted(set(dataset_ids))
            if not identifiers:
                return []
            placeholders = ", ".join("?" for _ in identifiers)
            query += f" AND d.dataset_id IN ({placeholders})"
            params.extend(identifiers)
        query += """
            GROUP BY d.dataset_id, d.campaign, d.project, d.publication_status
            ORDER BY d.dataset_id
        """
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)
        rows = conn.execute(query, params).fetchall()
    finally:
        conn.close()
    return [
        {
            "dataset_id": row[0],
            "campaign": row[1],
            "project": row[2],
            "publication_status": row[3],
            "latest_success": row[4],
        }
        for row in rows
    ]


def _persist_results(run_id, results):
    if not results:
        return
    conn = connect()
    try:
        conn.execute("BEGIN")
        for result in results:
            conn.execute(
                """
                INSERT INTO stac_reconciliation_attempts
                (check_id, run_id, dataset_id, campaign, collection_id,
                 item_id, request_url, started_at, finished_at, outcome,
                 publication_status, http_status, response_item_id,
                 response_collection, response_hash, error_message)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    str(uuid4()), run_id, result["dataset_id"], result["campaign"],
                    result["collection_id"], result["item_id"], result["request_url"],
                    result["started_at"], result["finished_at"], result["outcome"],
                    result["publication_status"], result["http_status"],
                    result["response_item_id"], result["response_collection"],
                    result["response_hash"], result["error_message"],
                ],
            )
            conn.execute(
                """
                UPDATE datasets
                SET stac_status = ?, stac_checked_at = ?, stac_http_status = ?
                WHERE dataset_id = ?
                """,
                [
                    result["outcome"], result["finished_at"],
                    result["http_status"], result["dataset_id"],
                ],
            )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def classify_result(result):
    outcome = result["outcome"]
    publication_status = result["publication_status"]
    if outcome == "ERROR":
        return "API_ERROR"
    if outcome == "WAITING":
        return "WAITING"
    if outcome == "MISMATCH":
        return "DRIFT"
    if (
        (publication_status == "SUCCESS" and outcome == "PRESENT")
        or (publication_status in ("FAILED", "PENDING") and outcome == "ABSENT")
    ):
        return "CONSISTENT"
    return "DRIFT"


def write_reconciliation_report(result, output):
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    rows = []
    for item in result["results"]:
        classification = classify_result(item)
        if classification in ("CONSISTENT", "WAITING"):
            continue
        rows.append({
            "classification": classification,
            "outcome": item["outcome"],
            "publication_status": item["publication_status"],
            "dataset_id": item["dataset_id"],
            "campaign": item["campaign"],
            "collection_id": item["collection_id"],
            "item_id": item["item_id"],
            "http_status": item["http_status"],
            "request_url": item["request_url"],
            "checked_at": item["finished_at"].isoformat(),
            "error_message": item["error_message"],
        })
    try:
        with open(temporary, "w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=REPORT_FIELDS)
            writer.writeheader()
            writer.writerows(rows)
        temporary.replace(output)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return len(rows)


def reconcile_campaign(
        campaign,
        scope="all",
        limit=None,
        concurrency=DEFAULT_CONCURRENCY,
        timeout_seconds=DEFAULT_TIMEOUT_SECONDS,
        retries=DEFAULT_RETRIES,
        grace_seconds=DEFAULT_GRACE_SECONDS,
        dataset_ids=None,
):
    if concurrency <= 0 or timeout_seconds <= 0 or retries < 0 or grace_seconds < 0:
        raise ValueError("Invalid STAC reconciliation execution settings")
    datasets = _select_datasets(campaign, scope, limit, dataset_ids=dataset_ids)
    run_scope = campaign or "all-campaigns"
    run_id = f"stac_{run_scope}_{datetime.now().strftime('%Y%m%d%H%M%S')}_{uuid4().hex[:12]}"
    results = []
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = {
            executor.submit(
                check_stac_item,
                dataset,
                timeout_seconds,
                retries,
                grace_seconds,
            ): dataset["dataset_id"]
            for dataset in datasets
        }
        for future in as_completed(futures):
            results.append(future.result())
    results.sort(key=lambda item: item["dataset_id"])
    _persist_results(run_id, results)
    comparisons = Counter(
        f"{item['publication_status']}/{item['outcome']}"
        for item in results
    )
    classifications = Counter(classify_result(item) for item in results)
    return {
        "run_id": run_id,
        "selected": len(datasets),
        "counts": dict(Counter(item["outcome"] for item in results)),
        "comparisons": dict(comparisons),
        "classifications": dict(classifications),
        "results": results,
    }
