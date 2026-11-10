-- Externe meldingen (M1), overzichten (M2), herstelbundel buiten de NAS (M11),
-- tweestapsverificatie en sessiebeheer (M12).

CREATE TABLE notify_channels (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('ntfy','pushover','telegram','discord','slack',
                                       'webhook','email')),
    name TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 0,
    config_json TEXT NOT NULL DEFAULT '{}',   -- niet-geheime velden; geheimen in secrets
    auto_created INTEGER NOT NULL DEFAULT 0,
    last_ok_at TEXT,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE notify_outbox (
    id INTEGER PRIMARY KEY,
    channel_id INTEGER NOT NULL REFERENCES notify_channels(id) ON DELETE CASCADE,
    notification_id INTEGER,
    category TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL DEFAULT '',
    link TEXT,
    priority TEXT NOT NULL DEFAULT 'default',
    attachment_path TEXT,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending','sent','failed','dropped')),
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TEXT NOT NULL,
    last_error TEXT,
    created_at TEXT NOT NULL,
    sent_at TEXT
);
CREATE INDEX idx_notify_outbox_due ON notify_outbox(status, next_attempt_at);

-- meldingen die in het dag-/weekoverzicht horen
CREATE TABLE notify_digest_items (
    id INTEGER PRIMARY KEY,
    notification_id INTEGER,
    category TEXT NOT NULL,
    severity TEXT NOT NULL,
    title TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX idx_notify_digest_items_ts ON notify_digest_items(created_at);

CREATE TABLE notify_digests (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('daily','weekly')),
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,
    summary_json TEXT NOT NULL,
    text_nl TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (kind, period_start)
);

CREATE TABLE offsite_copies (
    id INTEGER PRIMARY KEY,
    bundle_name TEXT NOT NULL,
    target_kind TEXT NOT NULL,
    target TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('ok','failed')),
    size INTEGER,
    sha256 TEXT,
    detail TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX idx_offsite_copies_ts ON offsite_copies(created_at);

CREATE TABLE user_totp (
    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    enabled INTEGER NOT NULL DEFAULT 0,
    recovery_hashes_json TEXT NOT NULL DEFAULT '[]',
    last_counter INTEGER,
    created_at TEXT NOT NULL,
    enabled_at TEXT
);

CREATE TABLE login_challenges (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    attempts INTEGER NOT NULL DEFAULT 0,
    ip TEXT,
    user_agent TEXT,
    expires_at TEXT NOT NULL
);

CREATE TABLE login_history (
    id INTEGER PRIMARY KEY,
    user_id INTEGER,
    username TEXT NOT NULL,
    ts TEXT NOT NULL,
    ip TEXT,
    user_agent TEXT,
    success INTEGER NOT NULL,
    method TEXT NOT NULL DEFAULT 'password',
    reason TEXT
);
CREATE INDEX idx_login_history_user ON login_history(user_id, ts);

ALTER TABLE sessions ADD COLUMN last_seen_at TEXT;
ALTER TABLE sessions ADD COLUMN ip TEXT;
ALTER TABLE sessions ADD COLUMN user_agent TEXT;
