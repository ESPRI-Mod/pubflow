import os
import json
import requests
from datetime import date, datetime

DEFAULT_BATCH_SIZE = 100
DEFAULT_MAX_PAYLOAD_BYTES = 750 * 1024


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


def get_max_payload_bytes():
    configured = os.environ.get("GRIST_MAX_PAYLOAD_BYTES")
    if configured is None:
        return DEFAULT_MAX_PAYLOAD_BYTES
    try:
        value = int(configured)
    except ValueError as exc:
        raise ValueError(
            "GRIST_MAX_PAYLOAD_BYTES must be an integer"
        ) from exc
    if value <= 0:
        raise ValueError(
            "GRIST_MAX_PAYLOAD_BYTES must be greater than zero"
        )
    return value


def payload_size(records):
    payload = json_safe({"records": records})
    return len(
        json.dumps(
            payload,
            allow_nan=False,
        ).encode("utf-8")
    )


def iter_payload_batches(records, batch_size, max_payload_bytes):
    """Yield batches constrained by both record count and JSON byte size."""
    batch = []
    for record in records:
        candidate = batch + [record]
        if batch and (
            len(candidate) > batch_size
            or payload_size(candidate) > max_payload_bytes
        ):
            yield batch
            batch = [record]
        else:
            batch = candidate
    if batch:
        yield batch


def send_batch_with_413_split(table_id, batch, sender):
    """Send a batch, recursively splitting it when Grist returns HTTP 413."""
    try:
        sender(table_id, batch)
        return
    except requests.HTTPError as exc:
        response = exc.response
        if response is None or response.status_code != 413:
            raise
        if len(batch) == 1:
            raise RuntimeError(
                f"A single Grist record for table {table_id} exceeds "
                f"the server payload limit ({payload_size(batch)} bytes)"
            ) from exc

    middle = len(batch) // 2
    send_batch_with_413_split(table_id, batch[:middle], sender)
    send_batch_with_413_split(table_id, batch[middle:], sender)


def send_records_batched(
        table_id,
        records,
        sender,
        batch_size,
        max_payload_bytes=None,
):
    if batch_size <= 0:
        raise ValueError("batch_size must be greater than zero")
    if max_payload_bytes is None:
        max_payload_bytes = get_max_payload_bytes()
    if max_payload_bytes <= 0:
        raise ValueError("max_payload_bytes must be greater than zero")

    for batch in iter_payload_batches(
            records,
            batch_size,
            max_payload_bytes,
    ):
        send_batch_with_413_split(table_id, batch, sender)

    return len(records)


def add_records_batched(
        table_id,
        records,
        batch_size=DEFAULT_BATCH_SIZE,
        max_payload_bytes=None,
):
    """
    Add records to a Grist table in batches.

    Returns the number of records successfully added.
    """

    return send_records_batched(
        table_id,
        records,
        add_records,
        batch_size,
        max_payload_bytes,
    )


def update_records_batched(
        table_id,
        records,
        batch_size=DEFAULT_BATCH_SIZE,
        max_payload_bytes=None,
):
    """
    Update records in a Grist table in batches.

    Returns the number of records successfully updated.
    """

    return send_records_batched(
        table_id,
        records,
        update_records,
        batch_size,
        max_payload_bytes,
    )
