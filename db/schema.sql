CREATE TABLE IF NOT EXISTS campaigns
(
    name
    VARCHAR
    PRIMARY
    KEY,

    project
    VARCHAR
    NOT
    NULL,
    activity
    VARCHAR
    NOT
    NULL,
    institution
    VARCHAR
    NOT
    NULL,

    mapfile_root
    VARCHAR
    NOT
    NULL,

    archive_root
    VARCHAR
);


CREATE TABLE IF NOT EXISTS datasets
(

    dataset_id
    VARCHAR
    PRIMARY
    KEY,

    campaign
    VARCHAR
    NOT
    NULL,

    project
    VARCHAR
    NOT
    NULL,
    activity
    VARCHAR
    NOT
    NULL,
    institution
    VARCHAR
    NOT
    NULL,

    drs
    JSON,

    mapfile
    VARCHAR
    NOT
    NULL,

    publication_status
    VARCHAR
    NOT
    NULL
    DEFAULT
    'PENDING',

    archive_status
    VARCHAR
    NOT
    NULL
    DEFAULT
    'PENDING',

    registered_at
    TIMESTAMP
    DEFAULT
    CURRENT_TIMESTAMP,

    FOREIGN
    KEY
(
    campaign
)
    REFERENCES campaigns
(
    name
)
    );


CREATE TABLE IF NOT EXISTS files
(

    dataset_id
    VARCHAR,

    file_path
    VARCHAR,

    file_size
    BIGINT,

    checksum
    VARCHAR,

    mod_time
    VARCHAR,

    PRIMARY
    KEY
(
    dataset_id,
    file_path
),
    FOREIGN KEY
(
    dataset_id
)
    REFERENCES datasets
(
    dataset_id
)
    );


CREATE TABLE IF NOT EXISTS publication_attempts
(

    dataset_id
    VARCHAR,

    started_at
    TIMESTAMP,

    finished_at
    TIMESTAMP,

    status
    VARCHAR,

    exit_code
    INTEGER,

    log_file
    VARCHAR,

    error_message
    VARCHAR,

    FOREIGN
    KEY
(
    dataset_id
)
    REFERENCES datasets
(
    dataset_id
)
    );