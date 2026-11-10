BEGIN TRANSACTION;
CREATE TABLE ai_budgets (
    period_key TEXT PRIMARY KEY,
    period_type TEXT NOT NULL,
    timezone TEXT NOT NULL,
    limit_amount REAL,
    currency TEXT NOT NULL,
    spent REAL NOT NULL DEFAULT 0,
    calls INTEGER NOT NULL DEFAULT 0,
    cache_hits INTEGER NOT NULL DEFAULT 0,
    retries INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);
CREATE TABLE ai_cases (
    id TEXT PRIMARY KEY,
    task_type TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    status TEXT NOT NULL,
    status_detail TEXT,
    payload_json TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    minimization_json TEXT NOT NULL DEFAULT '{}',
    evidence_ids_json TEXT NOT NULL DEFAULT '[]',
    target_type TEXT,
    target_id TEXT,
    instance_id TEXT,
    source_module TEXT,
    analysis_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_analyzed_at TEXT
);
CREATE TABLE ai_consent_events (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    provider TEXT NOT NULL,
    granted INTEGER NOT NULL,
    consent_version TEXT NOT NULL,
    actor TEXT NOT NULL,
    text_nl TEXT NOT NULL
);
CREATE TABLE ai_evidence_index (
    evidence_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    summary TEXT NOT NULL,
    instance_id TEXT,
    source_module TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE ai_feedback (
    id INTEGER PRIMARY KEY,
    case_id TEXT NOT NULL REFERENCES ai_cases(id) ON DELETE CASCADE,
    proposal_id INTEGER,
    verdict TEXT NOT NULL,
    comment TEXT,
    actor TEXT NOT NULL,
    ts TEXT NOT NULL
);
CREATE TABLE ai_model_evaluations (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    benchmark_version TEXT NOT NULL,
    total INTEGER NOT NULL,
    correct INTEGER NOT NULL,
    schema_valid INTEGER NOT NULL,
    accuracy REAL NOT NULL,
    baseline_accuracy REAL NOT NULL,
    improvement REAL NOT NULL,
    threshold REAL NOT NULL,
    passed INTEGER NOT NULL,
    cost REAL NOT NULL DEFAULT 0,
    details_json TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE ai_policy_results (
    id INTEGER PRIMARY KEY,
    proposal_id INTEGER NOT NULL REFERENCES ai_proposals(id) ON DELETE CASCADE,
    case_id TEXT NOT NULL REFERENCES ai_cases(id) ON DELETE CASCADE,
    mode TEXT NOT NULL,
    decision TEXT NOT NULL CHECK (decision IN ('log_only','await_approval','auto_execute','blocked')),
    action TEXT,
    executable INTEGER NOT NULL DEFAULT 0,
    reasons_json TEXT NOT NULL DEFAULT '[]',
    execution_status TEXT NOT NULL DEFAULT 'none',
    execution_result_json TEXT,
    decided_by TEXT,
    created_at TEXT NOT NULL,
    executed_at TEXT
);
CREATE TABLE ai_proposals (
    id INTEGER PRIMARY KEY,
    case_id TEXT NOT NULL REFERENCES ai_cases(id) ON DELETE CASCADE,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    mode TEXT NOT NULL,
    cache_key TEXT NOT NULL,
    from_cache INTEGER NOT NULL DEFAULT 0,
    valid INTEGER NOT NULL,
    proposal_json TEXT,
    rejection_reasons_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL
);
CREATE TABLE ai_provider_configs (
    provider TEXT PRIMARY KEY,
    last_test_at TEXT,
    last_test_ok INTEGER NOT NULL DEFAULT 0,
    last_test_model TEXT,
    last_test_detail TEXT,
    last_test_latency_ms INTEGER,
    models_json TEXT,
    models_fetched_at TEXT,
    models_error TEXT,
    updated_at TEXT NOT NULL
);
CREATE TABLE ai_usage (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    period_month TEXT NOT NULL,
    period_day TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    task_type TEXT,
    case_id TEXT,
    purpose TEXT NOT NULL,
    status TEXT NOT NULL,
    attempt INTEGER NOT NULL DEFAULT 1,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    reserved_cost REAL NOT NULL DEFAULT 0,
    cost REAL NOT NULL DEFAULT 0,
    currency TEXT NOT NULL,
    cost_source TEXT,
    latency_ms INTEGER,
    error TEXT
);
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
CREATE TABLE anime_routing_actions (
    id INTEGER PRIMARY KEY,
    source_instance TEXT NOT NULL,
    target_instance TEXT,
    kind TEXT NOT NULL CHECK (kind IN ('series','movie')),
    provider_id INTEGER NOT NULL,
    target_provider_id INTEGER,
    external_id INTEGER,
    title TEXT,
    status TEXT NOT NULL CHECK (status IN
        ('moved','suggested','needs_review','failed','reverted','dismissed')),
    evidence_json TEXT NOT NULL DEFAULT '[]',
    detail TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (source_instance, kind, provider_id)
);
CREATE TABLE anime_routing_exclusions (
    source_instance TEXT NOT NULL,
    kind TEXT NOT NULL,
    external_id INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    created_by TEXT,
    PRIMARY KEY (source_instance, kind, external_id)
);
CREATE TABLE api_keys (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    prefix TEXT NOT NULL,
    key_hash TEXT NOT NULL UNIQUE,
    role TEXT NOT NULL CHECK (role IN ('viewer', 'operator')),
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    last_used_at TEXT,
    expires_at TEXT,
    revoked_at TEXT
);
INSERT INTO "api_keys" VALUES(1,'rooktest-app','mgk_5op7','330cf84b3fdd92174af1e621e5e6582e7ebf3450ffae31a27bd3b5d6876f0a00','viewer','user:rooktest','2026-11-08T22:59:25+00:00',NULL,NULL,NULL);
CREATE TABLE audio_claim_failures (
    id INTEGER PRIMARY KEY,
    instance_id TEXT NOT NULL,
    release_title_norm TEXT NOT NULL,
    release_group TEXT,
    claim TEXT NOT NULL,
    observed_languages TEXT NOT NULL,
    detected_at TEXT NOT NULL,
    UNIQUE (instance_id, release_title_norm, claim)
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
INSERT INTO "audit_events" VALUES(1,'2026-11-08T22:59:25+00:00','user:rooktest','auth.setup','user','rooktest',NULL,NULL,'Eerste beheerder aangemaakt',NULL,NULL,NULL);
INSERT INTO "audit_events" VALUES(2,'2026-11-08T22:59:25+00:00','user:rooktest','user.create','user','kijker',NULL,NULL,NULL,NULL,'{"role": "viewer"}',NULL);
INSERT INTO "audit_events" VALUES(3,'2026-11-08T22:59:25+00:00','user:rooktest','apikey.created','api_key','1',NULL,NULL,'API-sleutel ''rooktest-app'' (viewer) aangemaakt',NULL,'{"expires_at": null, "name": "rooktest-app", "role": "viewer"}',NULL);
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
CREATE TABLE backfill_attempts (
    id INTEGER PRIMARY KEY,
    wanted_id INTEGER NOT NULL REFERENCES wanted_items(id) ON DELETE CASCADE,
    instance_id TEXT NOT NULL,
    run_id INTEGER,
    ts TEXT NOT NULL,
    trigger TEXT NOT NULL,
    command TEXT,
    command_id INTEGER,
    counts_quota INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL
        CHECK (status IN ('pending','grabbed','no_candidate','error','candidates')),
    candidates_json TEXT NOT NULL DEFAULT '[]',
    rejections_json TEXT NOT NULL DEFAULT '[]',
    message TEXT,
    finished_at TEXT
);
CREATE TABLE backfill_runs (
    id INTEGER PRIMARY KEY,
    instance_id TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    trigger TEXT NOT NULL,
    dry_run INTEGER NOT NULL DEFAULT 0,
    planned INTEGER NOT NULL DEFAULT 0,
    issued INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'running',
    quota_json TEXT NOT NULL DEFAULT '{}',
    message TEXT
);
CREATE TABLE delivery_events (
    id INTEGER PRIMARY KEY,
    item_id INTEGER NOT NULL REFERENCES delivery_items(id) ON DELETE CASCADE,
    ts TEXT NOT NULL,
    stage TEXT NOT NULL,
    action TEXT NOT NULL,
    actor TEXT NOT NULL,
    detail_nl TEXT NOT NULL,
    data_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE delivery_items (
    id INTEGER PRIMARY KEY,
    item_key TEXT NOT NULL UNIQUE,           -- 'series:tvdb:123' / 'movie:tmdb:456'
    source TEXT NOT NULL,                    -- 'seerr' | 'arr'
    seerr_request_ids_json TEXT NOT NULL DEFAULT '[]',
    media_type TEXT NOT NULL CHECK (media_type IN ('series','movie')),
    tmdb_id INTEGER,
    tvdb_id INTEGER,
    title TEXT,
    instance_id TEXT,
    provider_id INTEGER,
    requested_seasons_json TEXT NOT NULL DEFAULT '[]',
    stage TEXT NOT NULL DEFAULT 'requested',
    state TEXT NOT NULL DEFAULT 'active'
        CHECK (state IN ('active','waiting_release','delivered','not_found')),
    detail_nl TEXT,
    next_step_nl TEXT,
    progress_json TEXT NOT NULL DEFAULT '{}',
    attempts INTEGER NOT NULL DEFAULT 0,
    first_search_at TEXT,
    last_action_at TEXT,
    next_action_at TEXT,
    requested_at TEXT,
    delivered_at TEXT,
    seerr_synced_at TEXT,
    notified_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
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
CREATE TABLE download_janitor_seen (
    path TEXT PRIMARY KEY,
    first_seen TEXT NOT NULL
);
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
CREATE TABLE dv_conversion_evidence (
    id INTEGER PRIMARY KEY,
    dv_test_id INTEGER,
    encoding_job_id INTEGER,
    source_profile TEXT,
    el_type TEXT,
    route TEXT,
    method TEXT,
    rpu_preserved INTEGER,
    all_picture_info_preserved INTEGER,
    passed INTEGER NOT NULL,
    metadata_before_json TEXT NOT NULL DEFAULT '{}',
    metadata_after_json TEXT NOT NULL DEFAULT '{}',
    checks_json TEXT NOT NULL DEFAULT '[]',
    approved_by TEXT,
    approved_at TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE dv_playback_confirmations (
    id INTEGER PRIMARY KEY,
    client TEXT NOT NULL,
    server TEXT NOT NULL,
    container TEXT NOT NULL,
    codec TEXT NOT NULL,
    dv_profile TEXT NOT NULL,
    note TEXT,
    confirmed_by TEXT,
    confirmed_at TEXT NOT NULL,
    UNIQUE (client, server, container, codec, dv_profile)
);
CREATE TABLE dv_policy_versions (
    id INTEGER PRIMARY KEY,
    scope_type TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    values_json TEXT NOT NULL,
    changed_at TEXT NOT NULL,
    changed_by TEXT,
    reason TEXT
);
CREATE TABLE dv_tests (
    id INTEGER PRIMARY KEY,
    instance_id TEXT,
    kind TEXT,
    provider_id TEXT,
    source_path TEXT NOT NULL,
    scope_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL,                -- UI-status (Proefconversie, Gecontroleerd, ...)
    state TEXT NOT NULL,                 -- planned | running | verified | failed | kept | unsupported
    decision_json TEXT NOT NULL DEFAULT '{}',
    encoding_job_id INTEGER,
    verification_json TEXT,
    error TEXT,
    use_cpu INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    created_by TEXT,
    updated_at TEXT NOT NULL
);
CREATE TABLE encoding_autopilot_skips (
    path TEXT PRIMARY KEY,
    code TEXT NOT NULL,
    reason_nl TEXT NOT NULL,
    retry_after TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE encoding_benchmarks (
    id INTEGER PRIMARY KEY,
    source_path TEXT NOT NULL,
    request_json TEXT NOT NULL,
    result_json TEXT,
    status TEXT NOT NULL,
    error TEXT,
    created_at TEXT NOT NULL,
    created_by TEXT
);
CREATE TABLE encoding_jobs (
    id INTEGER PRIMARY KEY,
    encoder_job_id TEXT,
    instance_id TEXT,
    media_kind TEXT,
    provider_id TEXT,
    source_path TEXT NOT NULL,
    trial_only INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,                -- submitted|queued|running|verifying|awaiting_commit|
                                         -- awaiting_activation|handed_off|committed|rejected|
                                         -- failed|skipped|cancelled
    encoder_status TEXT,
    policy_json TEXT NOT NULL,
    dv_decision_json TEXT NOT NULL DEFAULT '{}',
    source_snapshot_json TEXT,
    staged_output_json TEXT,
    verification_json TEXT,
    core_verification_json TEXT,
    decision_json TEXT,
    progress REAL NOT NULL DEFAULT 0,
    error TEXT,
    skip_reason TEXT,
    transaction_id INTEGER,
    dv_test_id INTEGER,
    plan_id INTEGER,
    tracker_job_id INTEGER,
    correlation_id TEXT,
    created_at TEXT NOT NULL,
    created_by TEXT,
    updated_at TEXT NOT NULL
);
CREATE TABLE encoding_plans (
    id INTEGER PRIMARY KEY,
    instance_id TEXT,
    media_kind TEXT,
    provider_id TEXT,
    source_path TEXT NOT NULL,
    plan_json TEXT NOT NULL,
    gates_json TEXT NOT NULL,
    approved_for_execution INTEGER NOT NULL DEFAULT 0,
    risk_class TEXT,
    created_at TEXT NOT NULL,
    created_by TEXT
);
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
, engine_version INTEGER NOT NULL DEFAULT 1);
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
CREATE TABLE instance_profiles (
    instance_id TEXT NOT NULL REFERENCES instances(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN ('quality','language','custom_format')),
    provider_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    data_json TEXT NOT NULL DEFAULT '{}',
    synced_at TEXT NOT NULL,
    PRIMARY KEY (instance_id, kind, provider_id)
);
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
INSERT INTO "instances" VALUES('sonarr-series','sonarr','tv','Sonarr Series','http://mediaguard-sonarr-series:8989',NULL,'unknown',0,0,0,'open',NULL,NULL,NULL,NULL,NULL,NULL,'[]',NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,'standard',NULL,'tv',10,'2026-11-08T22:59:25+00:00','2026-11-08T22:59:25+00:00',NULL);
INSERT INTO "instances" VALUES('sonarr-anime','sonarr','anime','Sonarr Anime','http://mediaguard-sonarr-anime:8989',NULL,'unknown',0,0,0,'open',NULL,NULL,NULL,NULL,NULL,NULL,'[]',NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,'anime',NULL,'tv-anime',20,'2026-11-08T22:59:25+00:00','2026-11-08T22:59:25+00:00',NULL);
INSERT INTO "instances" VALUES('radarr-films','radarr','movie','Radarr Films','http://mediaguard-radarr-films:7878',NULL,'unknown',0,0,0,'open',NULL,NULL,NULL,NULL,NULL,NULL,'[]',NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,'movies',30,'2026-11-08T22:59:25+00:00','2026-11-08T22:59:25+00:00',NULL);
INSERT INTO "instances" VALUES('radarr-animefilms','radarr','anime_movie','Radarr Animefilms','http://mediaguard-radarr-animefilms:7878',NULL,'unknown',0,0,0,'open',NULL,NULL,NULL,NULL,NULL,NULL,'[]',NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,'movies-anime',40,'2026-11-08T22:59:25+00:00','2026-11-08T22:59:25+00:00',NULL);
CREATE TABLE job_logs (
    id INTEGER PRIMARY KEY,
    job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    ts TEXT NOT NULL,
    level TEXT NOT NULL,
    message TEXT NOT NULL
);
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
, resolved_at TEXT, resolved_reason TEXT, auto_retries INTEGER NOT NULL DEFAULT 0);
INSERT INTO "jobs" VALUES(1,'fixture.voorbeeld','succeeded',NULL,NULL,NULL,100,'{"fixture": true}',NULL,NULL,0.0,0,3,NULL,'2a5d8ec8148b49769ba8c167b7b45b79',NULL,'2026-11-08T22:59:25+00:00',NULL,'2026-11-08T12:00:00+00:00','rooktest',NULL,NULL,0);
INSERT INTO "jobs" VALUES(2,'fixture.voorbeeld','cancelled',NULL,NULL,NULL,100,'{"fixture": true}',NULL,NULL,0.0,0,3,NULL,'dd6492f4de7b4d93b0a518e2dde34569',NULL,'2026-11-08T22:59:25+00:00',NULL,'2026-11-08T12:00:00+00:00','rooktest',NULL,NULL,0);
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
    removed_at TEXT, sync_fingerprint TEXT,
    UNIQUE (instance_id, kind, provider_id)
);
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
CREATE TABLE library_sync_runs (
    id INTEGER PRIMARY KEY,
    instance_id TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    counts_json TEXT NOT NULL DEFAULT '{}',
    error TEXT
);
CREATE TABLE locks (
    resource TEXT PRIMARY KEY,
    owner TEXT NOT NULL,
    purpose TEXT NOT NULL,
    acquired_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
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
CREATE TABLE mediaserver_actions (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    kind TEXT NOT NULL,              -- refresh|playback_test|hold|resume|discover|error
    path TEXT,
    transaction_id INTEGER,
    ok INTEGER NOT NULL DEFAULT 1,
    detail_nl TEXT
);
CREATE TABLE mediaserver_pending (
    transaction_id INTEGER PRIMARY KEY,
    path TEXT NOT NULL,
    kind TEXT NOT NULL,              -- replace|restore
    encoding_job_id INTEGER,
    refreshed_at TEXT,
    test_after TEXT,                 -- afspeeltest pas na deze tijd (server moet scannen)
    attempts INTEGER NOT NULL DEFAULT 0,
    result TEXT,                     -- ok|transcode|failed|not_found|skipped
    detail_nl TEXT,
    done_at TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE mediaserver_watch (
    path TEXT PRIMARY KEY,
    title TEXT,
    user_name TEXT,
    last_seen TEXT NOT NULL
);
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
CREATE TABLE missing_diagnostics (
    id INTEGER PRIMARY KEY,
    wanted_id INTEGER NOT NULL REFERENCES wanted_items(id) ON DELETE CASCADE,
    instance_id TEXT NOT NULL,
    ts TEXT NOT NULL,
    primary_code TEXT NOT NULL,
    reasons_json TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    interactive INTEGER NOT NULL DEFAULT 0
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
, count INTEGER NOT NULL DEFAULT 1, dedupe_key TEXT, resolved_at TEXT);
INSERT INTO "notifications" VALUES(1,'2026-11-08T22:59:25+00:00','warning','Rooktest: melding van de vorige versie','Blijft na de update staan.',NULL,NULL,0,1,'fixture:melding',NULL);
INSERT INTO "notifications" VALUES(2,'2026-11-08T22:59:25+00:00','info','Rooktest: info','Gelezen melding.',NULL,NULL,1,1,'fixture:info',NULL);
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
INSERT INTO "notify_channels" VALUES(1,'ntfy','Telefoon (ntfy)',0,'{"server": "https://ntfy.sh", "topic": "mediaguard-wfwWlkhaPsCj16BRUJtiWdR7"}',1,NULL,NULL,'2026-11-08T22:59:25+00:00','2026-11-08T22:59:25+00:00');
CREATE TABLE notify_digest_items (
    id INTEGER PRIMARY KEY,
    notification_id INTEGER,
    category TEXT NOT NULL,
    severity TEXT NOT NULL,
    title TEXT NOT NULL,
    created_at TEXT NOT NULL
);
INSERT INTO "notify_digest_items" VALUES(1,1,'waiting','warning','Rooktest: melding van de vorige versie','2026-11-08T22:59:25+00:00');
INSERT INTO "notify_digest_items" VALUES(2,2,'waiting','info','Rooktest: info','2026-11-08T22:59:25+00:00');
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
CREATE TABLE optimization_items (
    id INTEGER PRIMARY KEY,
    plan_id INTEGER NOT NULL REFERENCES optimization_plans(id) ON DELETE CASCADE,
    priority INTEGER NOT NULL,
    instance_id TEXT,
    media_kind TEXT,
    provider_id TEXT,
    title TEXT,
    path TEXT,
    size INTEGER,
    action TEXT NOT NULL,
    bucket TEXT NOT NULL,
    reason_nl TEXT NOT NULL,
    est_bytes INTEGER NOT NULL DEFAULT 0,
    est_saving_bytes INTEGER NOT NULL DEFAULT 0,
    context_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'planned',
    result_json TEXT,
    started_at TEXT,
    finished_at TEXT
);
CREATE TABLE optimization_plans (
    id INTEGER PRIMARY KEY,
    status TEXT NOT NULL CHECK (status IN
        ('dry_run','running','paused','completed','cancelled','expired')),
    filters_json TEXT NOT NULL DEFAULT '{}',
    summary_json TEXT NOT NULL DEFAULT '{}',
    forecast_json TEXT NOT NULL DEFAULT '{}',
    item_count INTEGER NOT NULL DEFAULT 0,
    run_job_id INTEGER,
    confirmed_by TEXT,
    confirmed_at TEXT,
    created_at TEXT NOT NULL,
    created_by TEXT,
    updated_at TEXT NOT NULL
);
CREATE TABLE outbox_events (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    topic TEXT NOT NULL,
    instance_id TEXT,
    payload TEXT NOT NULL,
    delivered INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE policy_profile_history (
    id INTEGER PRIMARY KEY,
    profile_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    config_json TEXT NOT NULL,
    changed_at TEXT NOT NULL,
    changed_by TEXT
);
CREATE TABLE policy_profiles (
    id TEXT PRIMARY KEY,
    name_nl TEXT NOT NULL,
    description_nl TEXT NOT NULL DEFAULT '',
    builtin INTEGER NOT NULL DEFAULT 0,
    config_json TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL,
    updated_by TEXT
);
INSERT INTO "policy_profiles" VALUES('anime-dual-first','Anime: Dual Audio eerst','Dual Audio (EN+JA) > Engels > Japans (fallback, blijft zoeken). Audio weegt zwaarder dan resolutie.',1,'{"audio": {"anime_preference": "dual_first", "mode": "anime", "nl_audio": "off", "nl_subtitles_wanted": true, "subtitle_preference": ["nl", "en"]}, "blocked_groups": [], "max_resolution": null, "max_size_gb": null, "min_custom_format_score": null, "min_resolution": null, "prefer_hdr": false, "prefer_smaller": false, "preferred_groups": [], "ranking_order": ["audio", "source", "resolution", "custom_format", "group"], "reject_false_audio_claims": true, "trash_score_set": "default"}',1,'2026-11-08T22:59:25+00:00','system');
INSERT INTO "policy_profiles" VALUES('standaard','Standaard: originele audio + NL ondertitels','Originele audio behouden, Nederlandse ondertitels gewenst.',1,'{"audio": {"anime_preference": "dual_first", "mode": "original", "nl_audio": "off", "nl_subtitles_wanted": true, "subtitle_preference": ["nl", "en"]}, "blocked_groups": [], "max_resolution": null, "max_size_gb": null, "min_custom_format_score": null, "min_resolution": null, "prefer_hdr": false, "prefer_smaller": false, "preferred_groups": [], "ranking_order": ["audio", "source", "resolution", "custom_format", "group"], "reject_false_audio_claims": true, "trash_score_set": "default"}',1,'2026-11-08T22:59:25+00:00','system');
INSERT INTO "policy_profiles" VALUES('films-4k','Films 4K','Resolutie eerst (2160p), daarna HDR en bron. Originele audio.',1,'{"audio": {"anime_preference": "dual_first", "mode": "original", "nl_audio": "off", "nl_subtitles_wanted": true, "subtitle_preference": ["nl", "en"]}, "blocked_groups": [], "max_resolution": null, "max_size_gb": null, "min_custom_format_score": null, "min_resolution": null, "prefer_hdr": true, "prefer_smaller": false, "preferred_groups": [], "ranking_order": ["resolution", "hdr", "source", "audio", "custom_format", "group"], "reject_false_audio_claims": true, "trash_score_set": "default"}',1,'2026-11-08T22:59:25+00:00','system');
INSERT INTO "policy_profiles" VALUES('nl-audio-subs','Nederlandse audio/ondertitels','Nederlands gesproken heeft voorkeur, anders origineel met NL subs.',1,'{"audio": {"anime_preference": "dual_first", "mode": "original", "nl_audio": "preferred", "nl_subtitles_wanted": true, "subtitle_preference": ["nl", "en"]}, "blocked_groups": [], "max_resolution": null, "max_size_gb": null, "min_custom_format_score": null, "min_resolution": null, "prefer_hdr": false, "prefer_smaller": false, "preferred_groups": [], "ranking_order": ["audio", "source", "resolution", "custom_format", "group"], "reject_false_audio_claims": true, "trash_score_set": "default"}',1,'2026-11-08T22:59:25+00:00','system');
INSERT INTO "policy_profiles" VALUES('kids-nl-audio','Kinderen: Nederlandse audio verplicht','Alleen releases met Nederlands gesproken audio.',1,'{"audio": {"anime_preference": "dual_first", "mode": "original", "nl_audio": "required", "nl_subtitles_wanted": false, "subtitle_preference": ["nl", "en"]}, "blocked_groups": [], "max_resolution": null, "max_size_gb": null, "min_custom_format_score": null, "min_resolution": null, "prefer_hdr": false, "prefer_smaller": false, "preferred_groups": [], "ranking_order": ["audio", "source", "resolution", "custom_format", "group"], "reject_false_audio_claims": true, "trash_score_set": "default"}',1,'2026-11-08T22:59:25+00:00','system');
INSERT INTO "policy_profiles" VALUES('hdr-dv','HDR / Dolby Vision','HDR/DV-releases eerst; DV-conversie volgt het aparte DV-beleid.',1,'{"audio": {"anime_preference": "dual_first", "mode": "original", "nl_audio": "off", "nl_subtitles_wanted": true, "subtitle_preference": ["nl", "en"]}, "blocked_groups": [], "max_resolution": null, "max_size_gb": null, "min_custom_format_score": null, "min_resolution": null, "prefer_hdr": true, "prefer_smaller": false, "preferred_groups": [], "ranking_order": ["hdr", "resolution", "source", "audio", "custom_format", "group"], "reject_false_audio_claims": true, "trash_score_set": "default"}',1,'2026-11-08T22:59:25+00:00','system');
INSERT INTO "policy_profiles" VALUES('opslag','Opslagbewust','Maximaal 1080p, kleinere releases bij gelijke kwaliteit.',1,'{"audio": {"anime_preference": "dual_first", "mode": "original", "nl_audio": "off", "nl_subtitles_wanted": true, "subtitle_preference": ["nl", "en"]}, "blocked_groups": [], "max_resolution": 1080, "max_size_gb": null, "min_custom_format_score": null, "min_resolution": null, "prefer_hdr": false, "prefer_smaller": true, "preferred_groups": [], "ranking_order": ["audio", "resolution", "source", "size", "custom_format", "group"], "reject_false_audio_claims": true, "trash_score_set": "default"}',1,'2026-11-08T22:59:25+00:00','system');
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
CREATE TABLE provisioning_config (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    updated_by TEXT
);
CREATE TABLE provisioning_items (
    instance_id TEXT NOT NULL,
    section TEXT NOT NULL,
    item_key TEXT NOT NULL,
    remote_id INTEGER,
    ownership TEXT NOT NULL DEFAULT 'mediaguard'
        CHECK (ownership IN ('mediaguard','user','migrated')),
    secret_hash TEXT,
    applied_at TEXT,
    updated_at TEXT NOT NULL,
    updated_by TEXT,
    PRIMARY KEY (instance_id, section, item_key)
);
CREATE TABLE provisioning_optimize (
    instance_id TEXT PRIMARY KEY,
    phase TEXT NOT NULL CHECK (phase IN ('optimized','cleaned','rolled_back','failed')),
    snapshot_id INTEGER REFERENCES provisioning_snapshots(id),
    old_profiles_json TEXT NOT NULL DEFAULT '{}',
    result_json TEXT NOT NULL DEFAULT '{}',
    error TEXT,
    optimized_at TEXT,
    cleaned_at TEXT,
    rolled_back_at TEXT,
    updated_at TEXT NOT NULL,
    updated_by TEXT
);
CREATE TABLE provisioning_runs (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('plan','apply','drift','startup','optimize',
                                       'optimize_rollback','cleanup','rename')),
    actor TEXT,
    status TEXT NOT NULL DEFAULT 'running'
        CHECK (status IN ('running','succeeded','partial','failed')),
    sections_json TEXT NOT NULL DEFAULT '[]',
    instances_json TEXT NOT NULL DEFAULT '[]',
    summary_json TEXT NOT NULL DEFAULT '{}',
    changes_json TEXT NOT NULL DEFAULT '[]',
    started_at TEXT NOT NULL,
    finished_at TEXT
);
CREATE TABLE provisioning_snapshots (
    id INTEGER PRIMARY KEY,
    instance_id TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'pre_optimize',
    data_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    created_by TEXT
);
CREATE TABLE provisioning_status (
    instance_id TEXT NOT NULL,
    section TEXT NOT NULL,
    status TEXT NOT NULL,
    message_nl TEXT,
    changes INTEGER NOT NULL DEFAULT 0,
    detail_json TEXT NOT NULL DEFAULT '[]',
    checked_at TEXT NOT NULL,
    PRIMARY KEY (instance_id, section)
);
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
CREATE TABLE quarantine_release_checks (
    item_id INTEGER PRIMARY KEY REFERENCES quarantine_items(id) ON DELETE CASCADE,
    status TEXT NOT NULL CHECK (status IN ('passed','failed')),
    checks_json TEXT NOT NULL DEFAULT '[]',
    checked_at TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 1
);
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
CREATE TABLE replacement_journal (
    id INTEGER PRIMARY KEY,
    transaction_id INTEGER NOT NULL REFERENCES replacement_transactions(id),
    ts TEXT NOT NULL,
    step TEXT NOT NULL,
    phase TEXT NOT NULL,                 -- begin | done | error | recover
    detail_json TEXT NOT NULL DEFAULT '{}'
);
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
CREATE TABLE schema_migrations(version TEXT PRIMARY KEY, applied_at TEXT NOT NULL);
INSERT INTO "schema_migrations" VALUES('001_core','2026-11-08T22:59:25+00:00');
INSERT INTO "schema_migrations" VALUES('002_jobs_maintenance','2026-11-08T22:59:25+00:00');
INSERT INTO "schema_migrations" VALUES('003_notifications_merge','2026-11-08T22:59:25+00:00');
INSERT INTO "schema_migrations" VALUES('004_api_keys','2026-11-08T22:59:25+00:00');
INSERT INTO "schema_migrations" VALUES('020_instances','2026-11-08T22:59:25+00:00');
INSERT INTO "schema_migrations" VALUES('021_library','2026-11-08T22:59:25+00:00');
INSERT INTO "schema_migrations" VALUES('022_wanted','2026-11-08T22:59:25+00:00');
INSERT INTO "schema_migrations" VALUES('023_seerr','2026-11-08T22:59:25+00:00');
INSERT INTO "schema_migrations" VALUES('024_anime_routing','2026-11-08T22:59:25+00:00');
INSERT INTO "schema_migrations" VALUES('025_provisioning_config','2026-11-08T22:59:25+00:00');
INSERT INTO "schema_migrations" VALUES('026_provisioning_items','2026-11-08T22:59:25+00:00');
INSERT INTO "schema_migrations" VALUES('027_provisioning_runs','2026-11-08T22:59:25+00:00');
INSERT INTO "schema_migrations" VALUES('028_provisioning_optimize','2026-11-08T22:59:25+00:00');
INSERT INTO "schema_migrations" VALUES('029_delivery','2026-11-08T22:59:25+00:00');
INSERT INTO "schema_migrations" VALUES('040_identity','2026-11-08T22:59:25+00:00');
INSERT INTO "schema_migrations" VALUES('041_policy','2026-11-08T22:59:25+00:00');
INSERT INTO "schema_migrations" VALUES('042_hooks','2026-11-08T22:59:25+00:00');
INSERT INTO "schema_migrations" VALUES('043_dolby_vision','2026-11-08T22:59:25+00:00');
INSERT INTO "schema_migrations" VALUES('060_recovery','2026-11-08T22:59:25+00:00');
INSERT INTO "schema_migrations" VALUES('061_encoding','2026-11-08T22:59:25+00:00');
INSERT INTO "schema_migrations" VALUES('062_optimization','2026-11-08T22:59:25+00:00');
INSERT INTO "schema_migrations" VALUES('063_release_checks','2026-11-08T22:59:25+00:00');
INSERT INTO "schema_migrations" VALUES('064_av1_autopilot','2026-11-08T22:59:25+00:00');
INSERT INTO "schema_migrations" VALUES('065_performance','2026-11-08T22:59:25+00:00');
INSERT INTO "schema_migrations" VALUES('066_identity_engine_version','2026-11-08T22:59:25+00:00');
INSERT INTO "schema_migrations" VALUES('067_optimization_auto_review','2026-11-08T22:59:25+00:00');
INSERT INTO "schema_migrations" VALUES('068_storage_ledger','2026-11-08T22:59:25+00:00');
INSERT INTO "schema_migrations" VALUES('069_upgrade_reasons','2026-11-08T22:59:25+00:00');
INSERT INTO "schema_migrations" VALUES('078_download_janitor','2026-11-08T22:59:25+00:00');
INSERT INTO "schema_migrations" VALUES('079_mediaserver','2026-11-08T22:59:25+00:00');
INSERT INTO "schema_migrations" VALUES('080_migration','2026-11-08T22:59:25+00:00');
INSERT INTO "schema_migrations" VALUES('090_notify_security','2026-11-08T22:59:25+00:00');
INSERT INTO "schema_migrations" VALUES('100_ai_advisor','2026-11-08T22:59:25+00:00');
CREATE TABLE secrets (
    name TEXT PRIMARY KEY,
    ciphertext BLOB NOT NULL,
    updated_at TEXT NOT NULL,
    updated_by TEXT
);
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
CREATE TABLE sessions (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    csrf_token TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
, last_seen_at TEXT, ip TEXT, user_agent TEXT);
CREATE TABLE settings_values (
    scope_type TEXT NOT NULL,
    scope_id TEXT NOT NULL DEFAULT '',
    key TEXT NOT NULL,
    value_json TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    updated_by TEXT,
    PRIMARY KEY (scope_type, scope_id, key)
);
INSERT INTO "settings_values" VALUES('global','','backup.retention_count','21','2026-11-08T22:59:25+00:00','user:rooktest');
INSERT INTO "settings_values" VALUES('global','','ui.theme','"light"','2026-11-08T22:59:25+00:00','user:rooktest');
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
CREATE TABLE storage_ledger_events (
    id INTEGER PRIMARY KEY,
    instance_id TEXT NOT NULL,
    history_id INTEGER NOT NULL,
    at TEXT NOT NULL,
    event TEXT NOT NULL CHECK (event IN ('grabbed','imported','deleted')),
    media_kind TEXT NOT NULL CHECK (media_kind IN ('episode','movie')),
    owner_id INTEGER,
    item_id INTEGER,
    size INTEGER,
    quality TEXT,
    release_group TEXT,
    indexer TEXT,
    download_id TEXT,
    source_title TEXT,
    reason TEXT, cf_score INTEGER, custom_formats_json TEXT, is_repack INTEGER NOT NULL DEFAULT 0,
    UNIQUE (instance_id, history_id)
);
CREATE TABLE stuck_import_actions (
    id INTEGER PRIMARY KEY,
    instance_id TEXT NOT NULL,
    queue_id INTEGER NOT NULL,
    download_id TEXT,
    title TEXT,
    reason TEXT,
    first_seen_at TEXT NOT NULL,
    action TEXT,                 -- NULL = nog aan het wachten | removed_blocklisted | failed
    acted_at TEXT,
    detail TEXT,
    UNIQUE(instance_id, download_id)
);
CREATE TABLE subtitle_downloads (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    video_path TEXT NOT NULL,
    srt_path TEXT NOT NULL,
    provider TEXT NOT NULL,
    file_id TEXT,
    size INTEGER,
    status TEXT NOT NULL,            -- saved|skipped|failed
    detail_nl TEXT
);
CREATE TABLE subtitle_status (
    key TEXT PRIMARY KEY,            -- '<instance>:episode:<id>' of '<instance>:movie:<id>' of 'bazarr:...'
    instance_id TEXT,
    media_kind TEXT,
    provider_id INTEGER,
    title TEXT NOT NULL,
    nl_missing INTEGER NOT NULL DEFAULT 1,
    source TEXT NOT NULL,            -- bazarr|opensubtitles|file
    searched_at TEXT,
    updated_at TEXT NOT NULL
);
CREATE TABLE system_state (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
INSERT INTO "system_state" VALUES('safe_track_defaults_r2','{"at": "2026-11-08T22:59:25+00:00", "flipped": []}','2026-11-08T22:59:25+00:00');
INSERT INTO "system_state" VALUES('mediaserver_started_at','"2026-11-08T22:59:25+00:00"','2026-11-08T22:59:25+00:00');
INSERT INTO "system_state" VALUES('notify.default_created','true','2026-11-08T22:59:25+00:00');
CREATE TABLE trash_custom_formats (
    id INTEGER PRIMARY KEY,
    trash_id TEXT,
    name TEXT NOT NULL,
    spec_json TEXT NOT NULL,
    scores_json TEXT NOT NULL DEFAULT '{}',
    source TEXT NOT NULL,                -- upload | url:<host>
    imported_at TEXT NOT NULL,
    imported_by TEXT,
    UNIQUE (name)
);
CREATE TABLE user_preferences (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    key TEXT NOT NULL,
    value_json TEXT NOT NULL,
    PRIMARY KEY (user_id, key)
);
CREATE TABLE user_totp (
    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    enabled INTEGER NOT NULL DEFAULT 0,
    recovery_hashes_json TEXT NOT NULL DEFAULT '[]',
    last_counter INTEGER,
    created_at TEXT NOT NULL,
    enabled_at TEXT
);
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
INSERT INTO "users" VALUES(1,'rooktest','scrypt$L6ExyXFf3HLFr3N6ClQC9g==$evEqD0aDg6RcHStYANLxalxfVdi/j/fEiPG1gzEdtcE=','admin','nl-NL',0,'2026-11-08T22:59:25+00:00','2026-11-08T22:59:25+00:00');
INSERT INTO "users" VALUES(2,'kijker','scrypt$hwM8asFdxTroUC83/PnWsA==$UVm2G9U/F6y1UbPYm29i/NfPh1d8FWxZyX8eIhieSKI=','viewer','nl-NL',0,'2026-11-08T22:59:25+00:00',NULL);
CREATE TABLE wanted_items (
    id INTEGER PRIMARY KEY,
    instance_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('episode','movie')),
    provider_id INTEGER NOT NULL,
    item_provider_id INTEGER NOT NULL,
    reason TEXT NOT NULL CHECK (reason IN ('missing','cutoff')),
    title TEXT NOT NULL,
    item_title TEXT,
    season_number INTEGER,
    episode_number INTEGER,
    absolute_episode_number INTEGER,
    air_date_utc TEXT,
    status TEXT NOT NULL DEFAULT 'wanted'
        CHECK (status IN ('wanted','searching','unavailable','ignored','resolved')),
    priority INTEGER NOT NULL DEFAULT 0,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_searched_at TEXT,
    next_search_at TEXT,
    last_outcome TEXT,
    last_reason_code TEXT,
    keep_monitoring INTEGER NOT NULL DEFAULT 0,
    ignored_reason TEXT,
    first_seen_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    resolved_at TEXT,
    resolution TEXT,
    UNIQUE (instance_id, kind, provider_id, reason)
);
CREATE TABLE wanted_overrides (
    id INTEGER PRIMARY KEY,
    wanted_id INTEGER NOT NULL REFERENCES wanted_items(id) ON DELETE CASCADE,
    instance_id TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'relaxed_rules',
    relaxations_json TEXT NOT NULL,
    explanation_nl TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    used_at TEXT,
    result_json TEXT
);
CREATE INDEX idx_audit_ts ON audit_events(ts);
CREATE INDEX idx_audit_instance ON audit_events(instance_id);
CREATE INDEX idx_jobs_status ON jobs(status, priority, run_after);
CREATE INDEX idx_jobs_failed ON jobs(status, resolved_at, finished_at);
CREATE INDEX idx_jobs_type_target ON jobs(job_type, instance_id, target_id, status);
CREATE INDEX idx_job_logs_job ON job_logs(job_id, id);
CREATE INDEX idx_outbox_ts ON outbox_events(ts);
CREATE INDEX idx_notifications_read ON notifications(read, severity);
CREATE INDEX idx_notifications_dedupe ON notifications(dedupe_key, read);
CREATE INDEX idx_ownership_canonical ON instance_ownership(canonical_id);
CREATE INDEX idx_lib_items_tvdb ON lib_items(tvdb_id);
CREATE INDEX idx_lib_items_tmdb ON lib_items(tmdb_id);
CREATE INDEX idx_lib_items_title ON lib_items(sort_title);
CREATE INDEX idx_lib_episodes_series ON lib_episodes(instance_id, series_provider_id);
CREATE INDEX idx_lib_episodes_air ON lib_episodes(air_date_utc);
CREATE INDEX idx_lib_files_owner ON lib_files(instance_id, owner_provider_id);
CREATE INDEX idx_library_sync_runs ON library_sync_runs(instance_id, id);
CREATE INDEX idx_wanted_status ON wanted_items(instance_id, status, next_search_at);
CREATE INDEX idx_diag_wanted ON missing_diagnostics(wanted_id, id);
CREATE INDEX idx_backfill_runs ON backfill_runs(instance_id, id);
CREATE INDEX idx_attempts_instance_ts ON backfill_attempts(instance_id, ts);
CREATE INDEX idx_attempts_wanted ON backfill_attempts(wanted_id, id);
CREATE INDEX idx_overrides_wanted ON wanted_overrides(wanted_id, active);
CREATE INDEX idx_seerr_requests_instance ON seerr_requests(instance_id, provider_id);
CREATE INDEX idx_provisioning_runs ON provisioning_runs(kind, id);
CREATE INDEX idx_provisioning_snapshots ON provisioning_snapshots(instance_id, id);
CREATE INDEX idx_delivery_state ON delivery_items(state, next_action_at);
CREATE INDEX idx_delivery_location ON delivery_items(instance_id, provider_id);
CREATE INDEX idx_delivery_events_item ON delivery_events(item_id, id);
CREATE INDEX idx_identity_item ON identity_results(instance_id, kind, provider_id, id);
CREATE INDEX idx_identity_status ON identity_results(status);
CREATE INDEX idx_anime_map_abs ON anime_mappings(series_key, absolute);
CREATE INDEX idx_anime_map_scene ON anime_mappings(series_key, scene_season, scene_episode);
CREATE INDEX idx_prov_download ON release_provenance(instance_id, download_id);
CREATE INDEX idx_prov_series ON release_provenance(instance_id, series_id);
CREATE INDEX idx_prov_movie ON release_provenance(instance_id, movie_id);
CREATE INDEX idx_import_events_item ON import_events(instance_id, series_id, movie_id);
CREATE INDEX idx_hook_decisions_ts ON hook_decisions(instance_id, ts);
CREATE INDEX idx_pia_item ON post_import_analysis(instance_id, kind, provider_id);
CREATE INDEX idx_rt_state ON replacement_transactions(state);
CREATE INDEX idx_rt_item ON replacement_transactions(instance_id, media_kind, provider_id);
CREATE INDEX idx_rj_tx ON replacement_journal(transaction_id, id);
CREATE INDEX idx_qi_status ON quarantine_items(status, created_at);
CREATE INDEX idx_ej_status ON encoding_jobs(status);
CREATE INDEX idx_ej_item ON encoding_jobs(instance_id, media_kind, provider_id);
CREATE INDEX idx_oi_plan ON optimization_items(plan_id, status, priority);
CREATE INDEX idx_lib_episodes_inst_air ON lib_episodes(instance_id, air_date_utc);
CREATE INDEX idx_oi_status ON optimization_items(status, plan_id);
CREATE INDEX idx_stuck_import_seen ON stuck_import_actions(action, first_seen_at);
CREATE INDEX idx_storage_ledger_at ON storage_ledger_events(at);
CREATE INDEX idx_storage_ledger_item ON storage_ledger_events(instance_id, item_id, event);
CREATE INDEX idx_storage_ledger_dl ON storage_ledger_events(download_id);
CREATE INDEX idx_djt_open ON download_janitor_trash(purged_at, restored_at, moved_at);
CREATE INDEX idx_mediaserver_actions_ts ON mediaserver_actions(ts);
CREATE INDEX idx_subtitle_status_missing ON subtitle_status(nl_missing);
CREATE INDEX idx_migration_reports ON migration_reports(run_id, kind, target_instance_id, id);
CREATE INDEX idx_migration_cutovers ON migration_cutovers(run_id, target_instance_id, id);
CREATE INDEX idx_av1_provenance_ref ON av1_provenance_map(mediaguard_ref);
CREATE INDEX idx_notify_outbox_due ON notify_outbox(status, next_attempt_at);
CREATE INDEX idx_notify_digest_items_ts ON notify_digest_items(created_at);
CREATE INDEX idx_offsite_copies_ts ON offsite_copies(created_at);
CREATE INDEX idx_login_history_user ON login_history(user_id, ts);
CREATE INDEX idx_ai_cases_hash ON ai_cases(payload_hash);
CREATE INDEX idx_ai_cases_status ON ai_cases(status);
CREATE INDEX idx_ai_proposals_case ON ai_proposals(case_id);
CREATE INDEX idx_ai_proposals_cache ON ai_proposals(cache_key, valid);
CREATE INDEX idx_ai_policy_case ON ai_policy_results(case_id);
CREATE INDEX idx_ai_usage_month ON ai_usage(period_month);
CREATE INDEX idx_ai_usage_day ON ai_usage(period_day);
DELETE FROM "sqlite_sequence";
INSERT INTO "sqlite_sequence" VALUES('api_keys',1);
COMMIT;
