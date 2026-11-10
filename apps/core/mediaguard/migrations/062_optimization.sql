-- Library Optimization Engine (C3): dry-run-plannen, items, uitvoering.

CREATE TABLE optimization_plans (
    id INTEGER PRIMARY KEY,
    status TEXT NOT NULL CHECK (status IN
        ('dry_run','running','paused','completed','cancelled','expired')),
    filters_json TEXT NOT NULL DEFAULT '{}',
    summary_json TEXT NOT NULL DEFAULT '{}',
    forecast_json TEXT NOT NULL DEFAULT '{}',
    item_count INTEGER NOT NULL DEFAULT 0,
    run_job_id INTEGER,
    confirmed_by TEXT,
    confirmed_at TEXT,
    created_at TEXT NOT NULL,
    created_by TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE optimization_items (
    id INTEGER PRIMARY KEY,
    plan_id INTEGER NOT NULL REFERENCES optimization_plans(id) ON DELETE CASCADE,
    priority INTEGER NOT NULL,
    instance_id TEXT,
    media_kind TEXT,
    provider_id TEXT,
    title TEXT,
    path TEXT,
    size INTEGER,
    action TEXT NOT NULL,
    bucket TEXT NOT NULL,
    reason_nl TEXT NOT NULL,
    est_bytes INTEGER NOT NULL DEFAULT 0,
    est_saving_bytes INTEGER NOT NULL DEFAULT 0,
    context_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'planned',
    result_json TEXT,
    started_at TEXT,
    finished_at TEXT
);
CREATE INDEX idx_oi_plan ON optimization_items(plan_id, status, priority);
