-- Kern: gebruikers, sessies, geheimen, instellingen, audit, jobs, outbox, locks, notificaties.

CREATE TABLE users (
    id INTEGER PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('admin','operator','viewer')),
    locale TEXT NOT NULL DEFAULT 'nl-NL',
    disabled INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    last_login_at TEXT
);

CREATE TABLE sessions (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    csrf_token TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

-- Versleutelde geheimen (Fernet). Waarde verlaat de backend nooit.
CREATE TABLE secrets (
    name TEXT PRIMARY KEY,
    ciphertext BLOB NOT NULL,
    updated_at TEXT NOT NULL,
    updated_by TEXT
);

-- Scoped instellingen. scope_type: global|media_type|instance|series|movie|season|episode|file|job|library|profile
CREATE TABLE settings_values (
    scope_type TEXT NOT NULL,
    scope_id TEXT NOT NULL DEFAULT '',
    key TEXT NOT NULL,
    value_json TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    updated_by TEXT,
    PRIMARY KEY (scope_type, scope_id, key)
);

CREATE TABLE audit_events (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    target_type TEXT,
    target_id TEXT,
    instance_id TEXT,
    correlation_id TEXT,
    reason TEXT,
    old_value TEXT,
    new_value TEXT,
    undo_hint TEXT
);
CREATE INDEX idx_audit_ts ON audit_events(ts);
CREATE INDEX idx_audit_instance ON audit_events(instance_id);

-- Persistente jobs met state machine. idempotency_key voorkomt dubbele uitvoering bij replay.
CREATE TABLE jobs (
    id INTEGER PRIMARY KEY,
    job_type TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN
        ('queued','running','succeeded','failed','retry_scheduled','cancelled','paused')),
    instance_id TEXT,
    target_type TEXT,
    target_id TEXT,
    priority INTEGER NOT NULL DEFAULT 100,
    payload TEXT NOT NULL DEFAULT '{}',
    result TEXT,
    error TEXT,
    progress REAL NOT NULL DEFAULT 0,
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    idempotency_key TEXT UNIQUE,
    correlation_id TEXT,
    run_after TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    created_by TEXT
);
CREATE INDEX idx_jobs_status ON jobs(status, priority, run_after);

CREATE TABLE job_logs (
    id INTEGER PRIMARY KEY,
    job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    ts TEXT NOT NULL,
    level TEXT NOT NULL,
    message TEXT NOT NULL
);

CREATE TABLE outbox_events (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    topic TEXT NOT NULL,
    instance_id TEXT,
    payload TEXT NOT NULL,
    delivered INTEGER NOT NULL DEFAULT 0
);

-- Samenwerkende locks met lease (bijv. per mediabestand: download/import/encode/recovery).
CREATE TABLE locks (
    resource TEXT PRIMARY KEY,
    owner TEXT NOT NULL,
    purpose TEXT NOT NULL,
    acquired_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE TABLE notifications (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    severity TEXT NOT NULL CHECK (severity IN ('info','success','warning','error')),
    title TEXT NOT NULL,
    body TEXT,
    link TEXT,
    instance_id TEXT,
    read INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE user_preferences (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    key TEXT NOT NULL,
    value_json TEXT NOT NULL,
    PRIMARY KEY (user_id, key)
);

CREATE TABLE system_state (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
