-- B1: Seerr-aanvragen gekoppeld aan instantie-items.

CREATE TABLE seerr_requests (
    id INTEGER PRIMARY KEY,
    seerr_request_id INTEGER NOT NULL UNIQUE,
    media_type TEXT NOT NULL,
    tmdb_id INTEGER,
    tvdb_id INTEGER,
    request_status INTEGER,
    media_status INTEGER,
    is4k INTEGER NOT NULL DEFAULT 0,
    server_id INTEGER,
    instance_id TEXT,
    provider_id INTEGER,
    lib_item_id INTEGER,
    title TEXT,
    requested_by TEXT,
    seasons_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT,
    updated_at TEXT,
    synced_at TEXT NOT NULL,
    available_at TEXT,
    link_status TEXT NOT NULL DEFAULT 'unlinked'
);
CREATE INDEX idx_seerr_requests_instance ON seerr_requests(instance_id, provider_id);
