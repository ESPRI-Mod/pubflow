import os
import requests
from datetime import date, datetime

DEFAULT_BATCH_SIZE = 100


def json_safe(value):
    if isinstance(value, datetime):
        return value.isoformat()

    if isinstance(value, date):
        return value.isoformat()

    if isinstance(value, dict):
        return {
            key: json_safe(item)
            for key, item in value.items()
        }

    if isinstance(value, list):
        return [
            json_safe(item)
            for item in value
        ]

    if isinstance(value, tuple):
        return [
            json_safe(item)
            for item in value
        ]

    return value


def get_grist_config():
    api_key = os.environ.get("GRIST_API_KEY")

    base_url = os.environ.get(
        "GRIST_BASE_URL",
        "https://docs.getgrist.com",
    )

    doc_id = os.environ.get("GRIST_DOC_ID")

    if not api_key:
        raise RuntimeError(
            "GRIST_API_KEY is not set"
        )

    if not doc_id:
        raise RuntimeError(
            "GRIST_DOC_ID is not set"
        )

    return (
        base_url.rstrip("/"),
        doc_id,
        api_key,
    )


def grist_request(
        method,
        endpoint,
        **kwargs,
):
    base_url, doc_id, api_key = (
        get_grist_config()
    )

    url = (
        f"{base_url}"
        f"/api/docs/{doc_id}"
        f"{endpoint}"
    )

    headers = {
        "Authorization": (
            f"Bearer {api_key}"
        ),
        "Content-Type": "application/json",
    }

    if "json" in kwargs:
        kwargs["json"] = json_safe(
            kwargs["json"]
        )

    response = requests.request(
        method,
        url,
        headers=headers,
        timeout=30,
        **kwargs,
    )

    response.raise_for_status()

    return response.json()


def check_connection():
    return grist_request(
        "GET",
        "",
    )


def list_tables():
    return grist_request(
        "GET",
        "/tables",
    )


def get_table_columns(
        table_id,
):
    return grist_request(
        "GET",
        f"/tables/{table_id}/columns",
    )


def get_records(
        table_id,
):
    return grist_request(
        "GET",
        f"/tables/{table_id}/records",
    )


def add_records(
        table_id,
        records,
):
    return grist_request(
        "POST",
        f"/tables/{table_id}/records",
        json={
            "records": records,
        },
    )


def update_records(
        table_id,
        records,
):
    return grist_request(
        "PATCH",
        f"/tables/{table_id}/records",
        json={
            "records": records,
        },
    )


def add_records_batched(
        table_id,
        records,
        batch_size=DEFAULT_BATCH_SIZE,
):
    """
    Add records to a Grist table in batches.

    Returns the number of records successfully added.
    """

    if batch_size <= 0:
        raise ValueError(
            "batch_size must be greater than zero"
        )

    total = len(records)

    for start in range(0, total, batch_size):
        batch = records[
            start:start + batch_size
        ]

        add_records(
            table_id,
            batch,
        )

    return total


def update_records_batched(
        table_id,
        records,
        batch_size=DEFAULT_BATCH_SIZE,
):
    """
    Update records in a Grist table in batches.

    Returns the number of records successfully updated.
    """

    if batch_size <= 0:
        raise ValueError(
            "batch_size must be greater than zero"
        )

    total = len(records)

    for start in range(0, total, batch_size):
        batch = records[
            start:start + batch_size
        ]

        update_records(
            table_id,
            batch,
        )

    return total