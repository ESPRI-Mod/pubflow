CREATE TABLE IF NOT EXISTS campaigns
(
    name VARCHAR PRIMARY KEY,
    project VARCHAR NOT NULL,
    activity VARCHAR NOT NULL,
    institution VARCHAR NOT NULL,
    mapfile_root VARCHAR NOT NULL,
    archive_root VARCHAR,
    archive_depth VARCHAR
);

CREATE TABLE IF NOT EXISTS datasets
(
    dataset_id VARCHAR PRIMARY KEY,
    campaign VARCHAR NOT NULL,
    project VARCHAR NOT NULL,
    activity VARCHAR NOT NULL,
    institution VARCHAR NOT NULL,
    drs JSON,
    mapfile VARCHAR NOT NULL,
    mapfile_checksum VARCHAR,
    registration_status VARCHAR NOT NULL DEFAULT 'ACTIVE'
        CHECK (registration_status IN ('ACTIVE', 'RETIRED')),
    retired_at TIMESTAMP,
    publication_status VARCHAR NOT NULL DEFAULT 'PENDING'
        CHECK (publication_status IN ('PENDING', 'SUCCESS', 'FAILED')),
    publication_claim_id VARCHAR,
    publication_claimed_at TIMESTAMP,
    archive_status VARCHAR NOT NULL DEFAULT 'PENDING'
        CHECK (archive_status IN ('PENDING', 'SUCCESS')),
    archive_completed_at TIMESTAMP,
    stac_status VARCHAR NOT NULL DEFAULT 'UNCHECKED'
        CHECK (stac_status IN (
            'UNCHECKED', 'WAITING', 'PRESENT', 'ABSENT', 'MISMATCH', 'ERROR'
        )),
    stac_checked_at TIMESTAMP,
    stac_http_status INTEGER,
    registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (campaign) REFERENCES campaigns(name)
);

CREATE TABLE IF NOT EXISTS files
(
    dataset_id VARCHAR,
    file_path VARCHAR,
    file_size BIGINT,
    checksum VARCHAR,
    mod_time VARCHAR,
    PRIMARY KEY (dataset_id, file_path)
);

CREATE TABLE IF NOT EXISTS publication_attempts
(
    attempt_id VARCHAR PRIMARY KEY DEFAULT (uuid()::VARCHAR),
    dataset_id VARCHAR,
    run_id VARCHAR,
    started_at TIMESTAMP,
    finished_at TIMESTAMP,
    status VARCHAR,
    exit_code INTEGER,
    log_file VARCHAR,
    error_message VARCHAR
);

CREATE TABLE IF NOT EXISTS diagnostic_attempts
(
    diagnostic_id VARCHAR PRIMARY KEY,
    diagnostic_run_id VARCHAR NOT NULL,
    dataset_id VARCHAR NOT NULL,
    campaign VARCHAR NOT NULL,
    started_at TIMESTAMP,
    finished_at TIMESTAMP,
    outcome VARCHAR NOT NULL,
    publisher_status VARCHAR,
    exit_code INTEGER,
    http_status INTEGER,
    error_type VARCHAR,
    schema_url VARCHAR,
    rejected_value VARCHAR,
    suggested_value VARCHAR,
    summary VARCHAR,
    server_instance VARCHAR,
    log_file VARCHAR,
    stac_file VARCHAR
);

CREATE TABLE IF NOT EXISTS archive_tasks
(
    task_id VARCHAR PRIMARY KEY,
    dataset_id VARCHAR NOT NULL,
    campaign VARCHAR NOT NULL,
    mapfile_checksum VARCHAR NOT NULL,
    source_mapfile VARCHAR NOT NULL,
    archive_path VARCHAR NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP,
    status VARCHAR NOT NULL DEFAULT 'PENDING'
        CHECK (status IN (
            'PENDING', 'SUCCESS', 'ALREADY_EXISTS', 'CONFLICT', 'FAILED'
        )),
    error_message VARCHAR
);

CREATE TABLE IF NOT EXISTS stac_reconciliation_attempts
(
    check_id VARCHAR PRIMARY KEY,
    run_id VARCHAR NOT NULL,
    dataset_id VARCHAR NOT NULL,
    campaign VARCHAR NOT NULL,
    collection_id VARCHAR NOT NULL,
    item_id VARCHAR NOT NULL,
    request_url VARCHAR NOT NULL,
    started_at TIMESTAMP NOT NULL,
    finished_at TIMESTAMP NOT NULL,
    outcome VARCHAR NOT NULL CHECK (
        outcome IN ('WAITING', 'PRESENT', 'ABSENT', 'MISMATCH', 'ERROR')
    ),
    publication_status VARCHAR NOT NULL,
    http_status INTEGER,
    response_item_id VARCHAR,
    response_collection VARCHAR,
    response_hash VARCHAR,
    error_message VARCHAR
);
