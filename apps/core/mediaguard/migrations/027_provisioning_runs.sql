CREATE TABLE provisioning_runs (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('plan','apply','drift','startup','optimize',
                                       'optimize_rollback','cleanup','rename')),
    actor TEXT,
    status TEXT NOT NULL DEFAULT 'running'
        CHECK (status IN ('running','succeeded','partial','failed')),
    sections_json TEXT NOT NULL DEFAULT '[]',
    instances_json TEXT NOT NULL DEFAULT '[]',
    summary_json TEXT NOT NULL DEFAULT '{}',
    changes_json TEXT NOT NULL DEFAULT '[]',
    started_at TEXT NOT NULL,
    finished_at TEXT
);
CREATE INDEX idx_provisioning_runs ON provisioning_runs(kind, id);

-- Laatste bekende status per instantie per sectie (ok/drift/missing/error/needs_input/
-- skipped). instance_id = '' voor globale secties (prowlarr, seerr).
CREATE TABLE provisioning_status (
    instance_id TEXT NOT NULL,
    section TEXT NOT NULL,
    status TEXT NOT NULL,
    message_nl TEXT,
    changes INTEGER NOT NULL DEFAULT 0,
    detail_json TEXT NOT NULL DEFAULT '[]',
    checked_at TEXT NOT NULL,
    PRIMARY KEY (instance_id, section)
);
