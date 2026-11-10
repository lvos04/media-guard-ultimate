-- Opruimer voor downloadmappen: prullenbak (terug te zetten tot de bewaartermijn), wanneer
-- een onbekende map voor het eerst gezien is, en een korte historie per ronde.
CREATE TABLE download_janitor_trash (
    id INTEGER PRIMARY KEY,
    root TEXT NOT NULL,
    original_path TEXT NOT NULL,
    trash_path TEXT NOT NULL,
    size INTEGER NOT NULL DEFAULT 0,
    freed INTEGER NOT NULL DEFAULT 0,      -- bytes zonder andere hardlink (echt vrij te maken)
    reason TEXT NOT NULL,
    client TEXT,
    download_id TEXT,
    moved_at TEXT NOT NULL,
    purged_at TEXT,
    restored_at TEXT
);
CREATE INDEX idx_djt_open ON download_janitor_trash(purged_at, restored_at, moved_at);

CREATE TABLE download_janitor_seen (
    path TEXT PRIMARY KEY,
    first_seen TEXT NOT NULL
);

CREATE TABLE download_janitor_runs (
    id INTEGER PRIMARY KEY,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    trashed INTEGER NOT NULL DEFAULT 0,
    purged INTEGER NOT NULL DEFAULT 0,
    freed_bytes INTEGER NOT NULL DEFAULT 0,
    summary_json TEXT NOT NULL DEFAULT '{}'
);
