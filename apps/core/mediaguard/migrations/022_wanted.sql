-- B1: wanted, Root Cause Engine (D2) en Backfill Scheduler (D3), per instantie.

CREATE TABLE wanted_items (
    id INTEGER PRIMARY KEY,
    instance_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('episode','movie')),
    provider_id INTEGER NOT NULL,
    item_provider_id INTEGER NOT NULL,
    reason TEXT NOT NULL CHECK (reason IN ('missing','cutoff')),
    title TEXT NOT NULL,
    item_title TEXT,
    season_number INTEGER,
    episode_number INTEGER,
    absolute_episode_number INTEGER,
    air_date_utc TEXT,
    status TEXT NOT NULL DEFAULT 'wanted'
        CHECK (status IN ('wanted','searching','unavailable','ignored','resolved')),
    priority INTEGER NOT NULL DEFAULT 0,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_searched_at TEXT,
    next_search_at TEXT,
    last_outcome TEXT,
    last_reason_code TEXT,
    keep_monitoring INTEGER NOT NULL DEFAULT 0,
    ignored_reason TEXT,
    first_seen_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    resolved_at TEXT,
    resolution TEXT,
    UNIQUE (instance_id, kind, provider_id, reason)
);
CREATE INDEX idx_wanted_status ON wanted_items(instance_id, status, next_search_at);

CREATE TABLE missing_diagnostics (
    id INTEGER PRIMARY KEY,
    wanted_id INTEGER NOT NULL REFERENCES wanted_items(id) ON DELETE CASCADE,
    instance_id TEXT NOT NULL,
    ts TEXT NOT NULL,
    primary_code TEXT NOT NULL,
    reasons_json TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    interactive INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX idx_diag_wanted ON missing_diagnostics(wanted_id, id);

CREATE TABLE backfill_runs (
    id INTEGER PRIMARY KEY,
    instance_id TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    trigger TEXT NOT NULL,
    dry_run INTEGER NOT NULL DEFAULT 0,
    planned INTEGER NOT NULL DEFAULT 0,
    issued INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'running',
    quota_json TEXT NOT NULL DEFAULT '{}',
    message TEXT
);
CREATE INDEX idx_backfill_runs ON backfill_runs(instance_id, id);

CREATE TABLE backfill_attempts (
    id INTEGER PRIMARY KEY,
    wanted_id INTEGER NOT NULL REFERENCES wanted_items(id) ON DELETE CASCADE,
    instance_id TEXT NOT NULL,
    run_id INTEGER,
    ts TEXT NOT NULL,
    trigger TEXT NOT NULL,
    command TEXT,
    command_id INTEGER,
    counts_quota INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL
        CHECK (status IN ('pending','grabbed','no_candidate','error','candidates')),
    candidates_json TEXT NOT NULL DEFAULT '[]',
    rejections_json TEXT NOT NULL DEFAULT '[]',
    message TEXT,
    finished_at TEXT
);
CREATE INDEX idx_attempts_instance_ts ON backfill_attempts(instance_id, ts);
CREATE INDEX idx_attempts_wanted ON backfill_attempts(wanted_id, id);

-- Expliciete per-item overrides ('Opnieuw met versoepelde regels'). Nooit stilzwijgend.
CREATE TABLE wanted_overrides (
    id INTEGER PRIMARY KEY,
    wanted_id INTEGER NOT NULL REFERENCES wanted_items(id) ON DELETE CASCADE,
    instance_id TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'relaxed_rules',
    relaxations_json TEXT NOT NULL,
    explanation_nl TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    used_at TEXT,
    result_json TEXT
);
CREATE INDEX idx_overrides_wanted ON wanted_overrides(wanted_id, active);
