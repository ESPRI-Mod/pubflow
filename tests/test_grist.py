from unittest.mock import patch

import pytest
import requests

from workflow.grist import add_records_batched, payload_size


def payload_too_large():
    response = requests.Response()
    response.status_code = 413
    return requests.HTTPError(response=response)


def test_batches_are_limited_by_serialized_payload_size():
    records = [
        {"fields": {"dataset_id": str(index), "error_message": "x" * 80}}
        for index in range(6)
    ]
    max_bytes = payload_size(records[:2])

    with patch("workflow.grist.add_records") as sender:
        count = add_records_batched(
            "Failures",
            records,
            batch_size=500,
            max_payload_bytes=max_bytes,
        )

    batches = [call.args[1] for call in sender.call_args_list]
    assert count == len(records)
    assert len(batches) == 3
    assert [record for batch in batches for record in batch] == records
    assert all(payload_size(batch) <= max_bytes for batch in batches)


def test_http_413_is_retried_with_smaller_batches():
    records = [{"fields": {"dataset_id": str(index)}} for index in range(5)]
    accepted = []

    def sender(_table_id, batch):
        if len(batch) > 2:
            raise payload_too_large()
        accepted.extend(batch)

    with patch("workflow.grist.add_records", side_effect=sender):
        count = add_records_batched(
            "Failures",
            records,
            batch_size=500,
            max_payload_bytes=10_000,
        )

    assert count == len(records)
    assert accepted == records


def test_single_record_413_has_actionable_error():
    record = {"fields": {"error_message": "x" * 100}}

    with (
        patch("workflow.grist.add_records", side_effect=payload_too_large()),
        pytest.raises(RuntimeError, match="single Grist record"),
    ):
        add_records_batched(
            "Failures",
            [record],
            batch_size=500,
            max_payload_bytes=10_000,
        )
