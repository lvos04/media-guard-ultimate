-- Episode Identity Engine (E1): resultaten met bewijs, bekende hashes, animemappings.

CREATE TABLE identity_results (
    id INTEGER PRIMARY KEY,
    instance_id TEXT NOT NULL,
    kind TEXT NOT NULL,                 -- episode_file | movie_file | episode | movie
    provider_id TEXT NOT NULL,
    file_path TEXT,
    status TEXT NOT NULL CHECK (status IN
        ('verified','probable','uncertain','mismatch','unresolved')),
    confidence REAL NOT NULL DEFAULT 0,
    actionable INTEGER NOT NULL DEFAULT 0,
    expected_json TEXT NOT NULL DEFAULT '[]',
    actual_json TEXT NOT NULL DEFAULT '[]',
    evidence_json TEXT NOT NULL DEFAULT '[]',
    reason_nl TEXT,
    source TEXT NOT NULL,                -- pre_import | post_import | api | optimization | user
    created_at TEXT NOT NULL,
    created_by TEXT
);
CREATE INDEX idx_identity_item ON identity_results(instance_id, kind, provider_id, id);
CREATE INDEX idx_identity_status ON identity_results(status);

-- Bekende bestandshashes (uit eerder geverifieerde imports of AniDB/Shoko-adapter).
CREATE TABLE identity_known_hashes (
    hash_type TEXT NOT NULL,             -- ed2k | sha256 | crc32 | oshash
    hash TEXT NOT NULL,
    size INTEGER,
    instance_id TEXT,
    series_key TEXT,                     -- bv. tvdb:81189
    season INTEGER,
    episode INTEGER,
    absolute INTEGER,
    source TEXT NOT NULL,
    verified_at TEXT NOT NULL,
    PRIMARY KEY (hash_type, hash)
);

-- Cache van TheXEM/AniDB-mappings (scene/absolute ↔ tvdb).
CREATE TABLE anime_mappings (
    provider TEXT NOT NULL,              -- thexem | anidb | manual
    series_key TEXT NOT NULL,            -- tvdb:<id>
    scene_season INTEGER,
    scene_episode INTEGER,
    absolute INTEGER,
    tvdb_season INTEGER,
    tvdb_episode INTEGER,
    fetched_at TEXT NOT NULL
);
CREATE INDEX idx_anime_map_abs ON anime_mappings(series_key, absolute);
CREATE INDEX idx_anime_map_scene ON anime_mappings(series_key, scene_season, scene_episode);
