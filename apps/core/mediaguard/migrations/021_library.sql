-- B1: bibliotheekcache per instantie. Bron blijft de fork (B3); dit is een leescache.

CREATE TABLE lib_items (
    id INTEGER PRIMARY KEY,
    instance_id TEXT NOT NULL REFERENCES instances(id),
    kind TEXT NOT NULL CHECK (kind IN ('series','movie')),
    provider_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    sort_title TEXT,
    year INTEGER,
    tvdb_id INTEGER,
    tmdb_id INTEGER,
    imdb_id TEXT,
    tvmaze_id INTEGER,
    series_type TEXT,
    status TEXT,
    monitored INTEGER NOT NULL DEFAULT 0,
    monitor_new_items TEXT,
    minimum_availability TEXT,
    is_available INTEGER,
    quality_profile_id INTEGER,
    language_profile_id INTEGER,
    root_folder_path TEXT,
    path TEXT,
    tags_json TEXT NOT NULL DEFAULT '[]',
    has_file INTEGER,
    file_id INTEGER,
    size_on_disk INTEGER NOT NULL DEFAULT 0,
    episode_count INTEGER,
    episode_file_count INTEGER,
    network TEXT,
    in_cinemas TEXT,
    digital_release TEXT,
    physical_release TEXT,
    added_at TEXT,
    poster_url TEXT,
    synced_at TEXT NOT NULL,
    removed_at TEXT,
    UNIQUE (instance_id, kind, provider_id)
);
CREATE INDEX idx_lib_items_tvdb ON lib_items(tvdb_id);
CREATE INDEX idx_lib_items_tmdb ON lib_items(tmdb_id);
CREATE INDEX idx_lib_items_title ON lib_items(sort_title);

CREATE TABLE lib_seasons (
    instance_id TEXT NOT NULL,
    series_provider_id INTEGER NOT NULL,
    season_number INTEGER NOT NULL,
    monitored INTEGER NOT NULL DEFAULT 0,
    episode_count INTEGER,
    episode_file_count INTEGER,
    total_episode_count INTEGER,
    size_on_disk INTEGER,
    synced_at TEXT NOT NULL,
    PRIMARY KEY (instance_id, series_provider_id, season_number)
);

CREATE TABLE lib_episodes (
    id INTEGER PRIMARY KEY,
    instance_id TEXT NOT NULL,
    provider_id INTEGER NOT NULL,
    series_provider_id INTEGER NOT NULL,
    season_number INTEGER NOT NULL,
    episode_number INTEGER NOT NULL,
    absolute_episode_number INTEGER,
    scene_season_number INTEGER,
    scene_episode_number INTEGER,
    title TEXT,
    air_date TEXT,
    air_date_utc TEXT,
    monitored INTEGER NOT NULL DEFAULT 0,
    has_file INTEGER NOT NULL DEFAULT 0,
    episode_file_id INTEGER,
    unverified_scene_numbering INTEGER NOT NULL DEFAULT 0,
    synced_at TEXT NOT NULL,
    UNIQUE (instance_id, provider_id)
);
CREATE INDEX idx_lib_episodes_series ON lib_episodes(instance_id, series_provider_id);
CREATE INDEX idx_lib_episodes_air ON lib_episodes(air_date_utc);

CREATE TABLE lib_files (
    id INTEGER PRIMARY KEY,
    instance_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('episode','movie')),
    provider_id INTEGER NOT NULL,
    owner_provider_id INTEGER NOT NULL,
    path TEXT,
    relative_path TEXT,
    size INTEGER,
    quality TEXT,
    quality_cutoff_not_met INTEGER NOT NULL DEFAULT 0,
    languages_json TEXT NOT NULL DEFAULT '[]',
    release_group TEXT,
    media_info_json TEXT NOT NULL DEFAULT '{}',
    date_added TEXT,
    synced_at TEXT NOT NULL,
    UNIQUE (instance_id, kind, provider_id)
);
CREATE INDEX idx_lib_files_owner ON lib_files(instance_id, owner_provider_id);

CREATE TABLE library_sync_runs (
    id INTEGER PRIMARY KEY,
    instance_id TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    counts_json TEXT NOT NULL DEFAULT '{}',
    error TEXT
);
CREATE INDEX idx_library_sync_runs ON library_sync_runs(instance_id, id);
