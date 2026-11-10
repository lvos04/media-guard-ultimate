-- Recovery-worker (G2/P2): vervangtransacties met duurzaam journaal + quarantaine.

CREATE TABLE replacement_transactions (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('replace','restore')),
    instance_id TEXT,
    media_kind TEXT,
    provider_id TEXT,
    library_path TEXT NOT NULL,
    final_path TEXT NOT NULL,
    staged_path TEXT NOT NULL,
    staged_size INTEGER,
    staged_sha256 TEXT,
    source_snapshot_json TEXT NOT NULL,
    temp_path TEXT,
    quarantine_item_id INTEGER,
    quarantine_path TEXT,
    quarantine_method TEXT,              -- link | copy
    original_sha256 TEXT,
    hardlink_break_confirmed INTEGER NOT NULL DEFAULT 0,
    encoding_job_id INTEGER,
    restore_of_item_id INTEGER,
    state TEXT NOT NULL,
    error TEXT,
    warnings_json TEXT NOT NULL DEFAULT '[]',
    zfs_snapshot TEXT,
    refresh_json TEXT,                   -- arr-commando na commit (RescanSeries/RefreshMovie)
    correlation_id TEXT,
    created_at TEXT NOT NULL,
    created_by TEXT,
    updated_at TEXT NOT NULL,
    finished_at TEXT
);
CREATE INDEX idx_rt_state ON replacement_transactions(state);
CREATE INDEX idx_rt_item ON replacement_transactions(instance_id, media_kind, provider_id);

-- Append-only journaal: elke stap vóór én na uitvoering (crash replay).
CREATE TABLE replacement_journal (
    id INTEGER PRIMARY KEY,
    transaction_id INTEGER NOT NULL REFERENCES replacement_transactions(id),
    ts TEXT NOT NULL,
    step TEXT NOT NULL,
    phase TEXT NOT NULL,                 -- begin | done | error | recover
    detail_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX idx_rj_tx ON replacement_journal(transaction_id, id);

CREATE TABLE quarantine_items (
    id INTEGER PRIMARY KEY,
    transaction_id INTEGER NOT NULL,
    instance_id TEXT,
    media_kind TEXT,
    provider_id TEXT,
    original_path TEXT NOT NULL,
    quarantine_path TEXT NOT NULL,
    size INTEGER,
    sha256 TEXT,
    inode INTEGER,
    device INTEGER,
    method TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN
        ('pending','held','restorable','restored','purged','discarded')),
    reason TEXT,
    created_at TEXT NOT NULL,
    restored_at TEXT,
    purged_at TEXT,
    pinned INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX idx_qi_status ON quarantine_items(status, created_at);
