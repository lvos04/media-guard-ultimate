-- Fase 2: migratiewizard (DEEL C1, R3, ADR-008). Bron-instanties zijn de OUDE
-- productie-Sonarr/Radarr van de gebruiker (live via URL+API-sleutel) of een
-- geüploade native back-up (.zip). API-sleutels staan uitsluitend in de secrets-store
-- onder 'source:<id>:apikey'.

CREATE TABLE source_instances (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('live','backup')),
    app TEXT CHECK (app IN ('sonarr','radarr')),
    name TEXT NOT NULL,
    url TEXT,
    version TEXT,
    app_name TEXT,
    instance_name TEXT,
    database_type TEXT,
    status TEXT NOT NULL DEFAULT 'unknown',
    last_error TEXT,
    detected_json TEXT NOT NULL DEFAULT '{}',
    archived INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    created_by TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE source_backups (
    id INTEGER PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES source_instances(id),
    origin TEXT NOT NULL CHECK (origin IN ('existing','created','upload')),
    backup_name TEXT NOT NULL,
    path TEXT NOT NULL,
    size INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    app TEXT,
    app_version TEXT,
    schema_version INTEGER,
    verified INTEGER NOT NULL DEFAULT 0,
    verify_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    created_by TEXT,
    UNIQUE (source_id, sha256)
);

CREATE TABLE migration_runs (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active','completed','cancelled')),
    options_json TEXT NOT NULL DEFAULT '{}',
    accepted_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    created_by TEXT,
    updated_at TEXT NOT NULL
);

-- Persistente stap-status (state machine). scope = '' (hele run), 'source:<id>' of
-- een doel-instance-id. Overleeft refresh en herstart.
CREATE TABLE migration_steps (
    run_id INTEGER NOT NULL REFERENCES migration_runs(id) ON DELETE CASCADE,
    step TEXT NOT NULL,
    scope TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending','queued','running','succeeded','warning','failed',
                          'blocked','skipped')),
    job_id INTEGER,
    attempts INTEGER NOT NULL DEFAULT 0,
    result_json TEXT NOT NULL DEFAULT '{}',
    error TEXT,
    started_at TEXT,
    finished_at TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (run_id, step, scope)
);

-- Bron -> doel mappingmatrix (R3). Eén doel per bron; nooit automatisch samenvoegen.
CREATE TABLE migration_mappings (
    run_id INTEGER NOT NULL REFERENCES migration_runs(id) ON DELETE CASCADE,
    source_id TEXT NOT NULL REFERENCES source_instances(id),
    target_instance_id TEXT REFERENCES instances(id),
    status TEXT NOT NULL DEFAULT 'proposed'
        CHECK (status IN ('proposed','confirmed','excluded')),
    mode TEXT NOT NULL DEFAULT 'auto' CHECK (mode IN ('auto','native','api')),
    confidence REAL,
    anime_score REAL,
    reasons_json TEXT NOT NULL DEFAULT '[]',
    warnings_json TEXT NOT NULL DEFAULT '[]',
    path_map_json TEXT NOT NULL DEFAULT '[]',
    confirmed_by TEXT,
    confirmed_at TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (run_id, source_id)
);

-- Item-niveau signalen uit de mappingpreview (anime in niet-anime-bron, duplicaten).
CREATE TABLE migration_mapping_items (
    id INTEGER PRIMARY KEY,
    run_id INTEGER NOT NULL REFERENCES migration_runs(id) ON DELETE CASCADE,
    source_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('series','movie')),
    external_id TEXT NOT NULL,
    title TEXT,
    target_instance_id TEXT,
    flag TEXT NOT NULL,
    message_nl TEXT NOT NULL,
    UNIQUE (run_id, source_id, kind, external_id, flag)
);

-- R5: idempotente, herstartbare voortgang per doelinstantie. Bevat id-koppelingen
-- bron -> doel (tags, profielen, indexers, series, ...) en fasevlaggen.
CREATE TABLE instance_migration_checkpoints (
    run_id INTEGER NOT NULL REFERENCES migration_runs(id) ON DELETE CASCADE,
    instance_id TEXT NOT NULL,
    phase TEXT NOT NULL,
    kind TEXT NOT NULL,
    source_key TEXT NOT NULL,
    target_ref TEXT,
    data_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL,
    PRIMARY KEY (run_id, instance_id, phase, kind, source_key)
);

-- Oorspronkelijke 'enabled'-standen die tijdens de schaduwfase op het doel uit staan.
CREATE TABLE migration_shadow_states (
    run_id INTEGER NOT NULL REFERENCES migration_runs(id) ON DELETE CASCADE,
    target_instance_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    mode TEXT NOT NULL,
    state_json TEXT NOT NULL,
    applied_at TEXT NOT NULL,
    lifted_at TEXT,
    PRIMARY KEY (run_id, target_instance_id)
);

CREATE TABLE migration_reports (
    id INTEGER PRIMARY KEY,
    run_id INTEGER NOT NULL REFERENCES migration_runs(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN ('preflight','verify','scan','readiness','post_cutover',
                                       'import_preview','path_preview')),
    source_id TEXT,
    target_instance_id TEXT,
    verdict TEXT NOT NULL CHECK (verdict IN ('go','warn','no_go','info')),
    summary_json TEXT NOT NULL DEFAULT '{}',
    mismatches_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    created_by TEXT
);
CREATE INDEX idx_migration_reports ON migration_reports(run_id, kind, target_instance_id, id);

-- Cutover-registratie per doelinstantie: exacte vorige staat van de oude instantie,
-- fasen (crash-veilig) en rollback.
CREATE TABLE migration_cutovers (
    id INTEGER PRIMARY KEY,
    run_id INTEGER NOT NULL REFERENCES migration_runs(id) ON DELETE CASCADE,
    source_id TEXT NOT NULL,
    target_instance_id TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'cutover' CHECK (kind IN ('cutover','activate_search')),
    phase TEXT NOT NULL DEFAULT 'requested',
    status TEXT NOT NULL DEFAULT 'running'
        CHECK (status IN ('running','completed','failed','rolling_back','rolled_back')),
    confirmed_by TEXT NOT NULL,
    confirmed_name TEXT NOT NULL,
    source_state_json TEXT,
    source_live_json TEXT,
    delta_json TEXT,
    lift_json TEXT,
    seerr_json TEXT,
    rollback_json TEXT,
    error TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    rolled_back_at TEXT,
    updated_at TEXT NOT NULL
);
CREATE INDEX idx_migration_cutovers ON migration_cutovers(run_id, target_instance_id, id);

-- P8: import van de oude standalone AV1-app (read-only uit een kopie van haar SQLite).
CREATE TABLE av1_imports (
    id INTEGER PRIMARY KEY,
    source_instance TEXT NOT NULL,
    file_path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    counts_json TEXT NOT NULL DEFAULT '{}',
    path_map_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    created_by TEXT
);

CREATE TABLE av1_provenance_map (
    source_instance TEXT NOT NULL,
    source_kind TEXT NOT NULL CHECK (source_kind IN ('job','bestand')),
    source_id INTEGER NOT NULL,
    mediaguard_ref TEXT,
    instance_id TEXT,
    lib_file_id INTEGER,
    status TEXT NOT NULL,
    path TEXT,
    mapped_path TEXT,
    size INTEGER,
    mtime REAL,
    device INTEGER,
    inode INTEGER,
    data_json TEXT NOT NULL DEFAULT '{}',
    import_id INTEGER NOT NULL REFERENCES av1_imports(id),
    imported_at TEXT NOT NULL,
    PRIMARY KEY (source_instance, source_kind, source_id)
);
CREATE INDEX idx_av1_provenance_ref ON av1_provenance_map(mediaguard_ref);
