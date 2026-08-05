CREATE TABLE IF NOT EXISTS campaigns (
    name VARCHAR PRIMARY KEY,
    project VARCHAR,
    activity VARCHAR,
    institution VARCHAR,
    mapfile_root VARCHAR,
    archive_root VARCHAR
);


CREATE TABLE IF NOT EXISTS datasets (
    dataset_id VARCHAR PRIMARY KEY,

    campaign VARCHAR,

    project VARCHAR,
    activity VARCHAR,
    institution VARCHAR,

    drs JSON,

    mapfile VARCHAR,

    publication_status VARCHAR DEFAULT 'PENDING',
    archive_status VARCHAR DEFAULT 'PENDING',

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);


CREATE TABLE IF NOT EXISTS files (
    dataset_id VARCHAR,
    file_path VARCHAR,

    file_size BIGINT,

    checksum VARCHAR,
    mod_time VARCHAR,

    PRIMARY KEY(dataset_id, file_path),

    FOREIGN KEY(dataset_id)
        REFERENCES datasets(dataset_id)
);


CREATE TABLE IF NOT EXISTS publication_attempts (
    dataset_id VARCHAR,

    started_at TIMESTAMP,
    finished_at TIMESTAMP,

    status VARCHAR,
    exit_code INTEGER,

    log_file VARCHAR,
    error_message VARCHAR
);
