-- Fork-hooks: releaseprovenance, import-events (idempotent), hookbeslissingen, analyses.

CREATE TABLE release_provenance (
    id INTEGER PRIMARY KEY,
    instance_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    download_id TEXT,
    release_title TEXT,
    release_group TEXT,
    quality TEXT,
    custom_format_score INTEGER,
    custom_formats_json TEXT NOT NULL DEFAULT '[]',
    indexer TEXT,
    size INTEGER,
    languages_json TEXT NOT NULL DEFAULT '[]',
    series_id INTEGER,
    movie_id INTEGER,
    episode_ids_json TEXT NOT NULL DEFAULT '[]',
    episodes_json TEXT NOT NULL DEFAULT '[]',
    file_id INTEGER,
    file_path TEXT,
    idempotency_key TEXT NOT NULL,
    received_at TEXT NOT NULL,
    UNIQUE (instance_id, idempotency_key)
);
CREATE INDEX idx_prov_download ON release_provenance(instance_id, download_id);
CREATE INDEX idx_prov_series ON release_provenance(instance_id, series_id);
CREATE INDEX idx_prov_movie ON release_provenance(instance_id, movie_id);

CREATE TABLE import_events (
    id INTEGER PRIMARY KEY,
    instance_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    is_upgrade INTEGER NOT NULL DEFAULT 0,
    download_id TEXT,
    series_id INTEGER,
    movie_id INTEGER,
    file_id INTEGER,
    file_path TEXT,
    idempotency_key TEXT NOT NULL,
    analysis_job_id INTEGER,
    payload_json TEXT NOT NULL,
    received_at TEXT NOT NULL,
    UNIQUE (instance_id, idempotency_key)
);
CREATE INDEX idx_import_events_item ON import_events(instance_id, series_id, movie_id);

CREATE TABLE hook_decisions (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    instance_id TEXT NOT NULL,
    hook TEXT NOT NULL,                  -- pre_grab | pre_import
    release_title TEXT,
    download_id TEXT,
    decision TEXT NOT NULL,
    reason_code TEXT,
    message TEXT,
    score_adjustment INTEGER NOT NULL DEFAULT 0,
    duration_ms REAL,
    details_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX idx_hook_decisions_ts ON hook_decisions(instance_id, ts);

CREATE TABLE post_import_analysis (
    id INTEGER PRIMARY KEY,
    instance_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    provider_id TEXT NOT NULL,
    file_path TEXT,
    identity_status TEXT,
    audio_json TEXT NOT NULL DEFAULT '{}',
    av1_json TEXT NOT NULL DEFAULT '{}',
    upgrade_wanted INTEGER NOT NULL DEFAULT 0,
    reason_nl TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX idx_pia_item ON post_import_analysis(instance_id, kind, provider_id);
