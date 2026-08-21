from workflow.grist import (
    add_records_batched,
    get_records,
    update_records_batched,
)


GRIST_BATCH_SIZE = 500


def sync_diagnostics_to_grist(rows):
    """Upsert diagnostic results into a Grist table named Diagnostics."""
    table_id = "Diagnostics"
    existing = get_records(table_id)
    existing_by_id = {
        record.get("fields", {}).get("diagnostic_id"): record["id"]
        for record in existing.get("records", [])
        if record.get("fields", {}).get("diagnostic_id")
    }
    creates = []
    updates = []

    field_names = (
        "diagnostic_id",
        "diagnostic_run_id",
        "dataset_id",
        "campaign",
        "started_at",
        "finished_at",
        "outcome",
        "publisher_status",
        "exit_code",
        "http_status",
        "error_type",
        "schema_url",
        "rejected_value",
        "suggested_value",
        "summary",
        "server_instance",
        "log_file",
        "stac_file",
    )

    for row in rows:
        fields = {name: row.get(name) for name in field_names}
        record_id = existing_by_id.get(row["diagnostic_id"])
        if record_id is None:
            creates.append({"fields": fields})
        else:
            updates.append({"id": record_id, "fields": fields})

    if creates:
        add_records_batched(table_id, creates, batch_size=GRIST_BATCH_SIZE)
    if updates:
        update_records_batched(table_id, updates, batch_size=GRIST_BATCH_SIZE)

    return len(rows)
