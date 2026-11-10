-- Encoder-bridge (core-kant): getrackte encoderjobs, per-titelplannen, benchmarks.

CREATE TABLE encoding_jobs (
    id INTEGER PRIMARY KEY,
    encoder_job_id TEXT,
    instance_id TEXT,
    media_kind TEXT,
    provider_id TEXT,
    source_path TEXT NOT NULL,
    trial_only INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,                -- submitted|queued|running|verifying|awaiting_commit|
                                         -- awaiting_activation|handed_off|committed|rejected|
                                         -- failed|skipped|cancelled
    encoder_status TEXT,
    policy_json TEXT NOT NULL,
    dv_decision_json TEXT NOT NULL DEFAULT '{}',
    source_snapshot_json TEXT,
    staged_output_json TEXT,
    verification_json TEXT,
    core_verification_json TEXT,
    decision_json TEXT,
    progress REAL NOT NULL DEFAULT 0,
    error TEXT,
    skip_reason TEXT,
    transaction_id INTEGER,
    dv_test_id INTEGER,
    plan_id INTEGER,
    tracker_job_id INTEGER,
    correlation_id TEXT,
    created_at TEXT NOT NULL,
    created_by TEXT,
    updated_at TEXT NOT NULL
);
CREATE INDEX idx_ej_status ON encoding_jobs(status);
CREATE INDEX idx_ej_item ON encoding_jobs(instance_id, media_kind, provider_id);

CREATE TABLE encoding_plans (
    id INTEGER PRIMARY KEY,
    instance_id TEXT,
    media_kind TEXT,
    provider_id TEXT,
    source_path TEXT NOT NULL,
    plan_json TEXT NOT NULL,
    gates_json TEXT NOT NULL,
    approved_for_execution INTEGER NOT NULL DEFAULT 0,
    risk_class TEXT,
    created_at TEXT NOT NULL,
    created_by TEXT
);

CREATE TABLE encoding_benchmarks (
    id INTEGER PRIMARY KEY,
    source_path TEXT NOT NULL,
    request_json TEXT NOT NULL,
    result_json TEXT,
    status TEXT NOT NULL,
    error TEXT,
    created_at TEXT NOT NULL,
    created_by TEXT
);
