-- B1: instantie-registry (R1/R2/R5). Native Sonarr/Radarr-ID's zijn alleen uniek binnen
-- een instantie; alle media-tabellen gebruiken daarom (instance_id, provider_id).

CREATE TABLE instances (
    id TEXT PRIMARY KEY,
    app TEXT NOT NULL CHECK (app IN ('sonarr','radarr')),
    media_type TEXT NOT NULL CHECK (media_type IN ('tv','anime','movie','anime_movie')),
    name TEXT NOT NULL,
    url TEXT NOT NULL,
    public_url TEXT,
    status TEXT NOT NULL DEFAULT 'unknown'
        CHECK (status IN ('healthy','degraded','offline','unknown')),
    suspended INTEGER NOT NULL DEFAULT 0,
    shadow_mode INTEGER NOT NULL DEFAULT 0,
    archived INTEGER NOT NULL DEFAULT 0,
    hook_fail_policy TEXT NOT NULL DEFAULT 'open' CHECK (hook_fail_policy IN ('open','closed')),
    version TEXT,
    upstream_app_name TEXT,
    api_key_valid INTEGER,
    last_check_at TEXT,
    last_status_change_at TEXT,
    last_error TEXT,
    health_json TEXT NOT NULL DEFAULT '[]',
    suspend_state_json TEXT,
    suspended_at TEXT,
    suspended_by TEXT,
    webhook_notification_id INTEGER,
    webhook_registered_at TEXT,
    default_quality_profile_id INTEGER,
    default_language_profile_id INTEGER,
    default_root_folder TEXT,
    default_monitor TEXT,
    default_monitor_new_items TEXT,
    default_series_type TEXT,
    default_minimum_availability TEXT,
    download_category TEXT,
    sort_order INTEGER NOT NULL DEFAULT 100,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    archived_at TEXT
);

-- Cache van profielen per instantie (quality/language/custom format) voor UI en Seerr-checks.
CREATE TABLE instance_profiles (
    instance_id TEXT NOT NULL REFERENCES instances(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN ('quality','language','custom_format')),
    provider_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    data_json TEXT NOT NULL DEFAULT '{}',
    synced_at TEXT NOT NULL,
    PRIMARY KEY (instance_id, kind, provider_id)
);

-- Single ownership (R2.3). Expliciete registratie/override per canoniek ID + doelprofiel.
CREATE TABLE instance_ownership (
    id INTEGER PRIMARY KEY,
    media_type TEXT NOT NULL CHECK (media_type IN ('tv','anime','movie','anime_movie')),
    canonical_id TEXT NOT NULL,
    edition TEXT NOT NULL DEFAULT '',
    target_profile TEXT NOT NULL DEFAULT '',
    instance_id TEXT NOT NULL REFERENCES instances(id),
    provider_id INTEGER,
    root_path TEXT,
    is_override INTEGER NOT NULL DEFAULT 0,
    allow_multi INTEGER NOT NULL DEFAULT 0,
    reason TEXT,
    created_at TEXT NOT NULL,
    created_by TEXT,
    UNIQUE (canonical_id, media_type, edition, target_profile)
);
CREATE INDEX idx_ownership_canonical ON instance_ownership(canonical_id);

CREATE TABLE instance_root_mappings (
    id INTEGER PRIMARY KEY,
    instance_id TEXT NOT NULL REFERENCES instances(id) ON DELETE CASCADE,
    root_path TEXT NOT NULL,
    core_path TEXT,
    library_label TEXT,
    provider_root_folder_id INTEGER,
    accessible INTEGER,
    free_space INTEGER,
    total_space INTEGER,
    synced_at TEXT,
    UNIQUE (instance_id, root_path)
);

CREATE TABLE instance_indexer_assignments (
    instance_id TEXT NOT NULL REFERENCES instances(id) ON DELETE CASCADE,
    indexer_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    implementation TEXT,
    protocol TEXT,
    enable_rss INTEGER NOT NULL DEFAULT 0,
    enable_automatic_search INTEGER NOT NULL DEFAULT 0,
    enable_interactive_search INTEGER NOT NULL DEFAULT 0,
    priority INTEGER,
    tags_json TEXT NOT NULL DEFAULT '[]',
    synced_at TEXT NOT NULL,
    PRIMARY KEY (instance_id, indexer_id)
);

CREATE TABLE instance_seerr_routing (
    instance_id TEXT PRIMARY KEY REFERENCES instances(id) ON DELETE CASCADE,
    seerr_kind TEXT NOT NULL CHECK (seerr_kind IN ('sonarr','radarr')),
    seerr_server_id INTEGER,
    role TEXT NOT NULL DEFAULT 'default',
    expected_is_default INTEGER NOT NULL DEFAULT 0,
    expected_is_4k INTEGER NOT NULL DEFAULT 0,
    expected_profile_id INTEGER,
    expected_root_folder TEXT,
    notes TEXT,
    updated_at TEXT NOT NULL,
    updated_by TEXT
);

CREATE TABLE instance_cutover_state (
    instance_id TEXT PRIMARY KEY REFERENCES instances(id) ON DELETE CASCADE,
    phase TEXT NOT NULL DEFAULT 'none',
    source_url TEXT,
    seerr_kind TEXT,
    seerr_server_id INTEGER,
    seerr_previous_json TEXT,
    seerr_applied_json TEXT,
    seerr_applied_at TEXT,
    seerr_rolled_back_at TEXT,
    notes TEXT,
    updated_at TEXT NOT NULL,
    updated_by TEXT
);
