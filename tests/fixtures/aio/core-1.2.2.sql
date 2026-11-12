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
INSERT INTO "ai_budgets" VALUES('fixture-ai_budgets-period_key-1','fixture-ai_budgets-period_type-1','fixture-ai_budgets-timezone-1',1.5,'fixture-ai_budgets-currency-1',1.5,1,1,1,'2026-11-01T12:00:00+00:00');
INSERT INTO "ai_budgets" VALUES('fixture-ai_budgets-period_key-2','fixture-ai_budgets-period_type-2','fixture-ai_budgets-timezone-2',2.5,'fixture-ai_budgets-currency-2',2.5,2,2,2,'2026-11-02T12:00:00+00:00');
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
INSERT INTO "ai_cases" VALUES('fixture-ai_cases-id-1','fixture-ai_cases-task_type-1','fixture-ai_cases-reason_code-1','fixture-ai_cases-status-1','fixture-ai_cases-status_detail-1','{}','fixture-ai_cases-payload_hash-1','{}','[]','fixture-ai_cases-target_type-1','fixture-ai_cases-target_id-1','fixture-ai_cases-instance_id-1','fixture-ai_cases-source_module-1',1,'2026-11-01T12:00:00+00:00','2026-11-01T12:00:00+00:00','2026-11-01T12:00:00+00:00');
INSERT INTO "ai_cases" VALUES('fixture-ai_cases-id-2','fixture-ai_cases-task_type-2','fixture-ai_cases-reason_code-2','fixture-ai_cases-status-2','fixture-ai_cases-status_detail-2','{}','fixture-ai_cases-payload_hash-2','{}','[]','fixture-ai_cases-target_type-2','fixture-ai_cases-target_id-2','fixture-ai_cases-instance_id-2','fixture-ai_cases-source_module-2',2,'2026-11-02T12:00:00+00:00','2026-11-02T12:00:00+00:00','2026-11-02T12:00:00+00:00');
CREATE TABLE ai_consent_events (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    provider TEXT NOT NULL,
    granted INTEGER NOT NULL,
    consent_version TEXT NOT NULL,
    actor TEXT NOT NULL,
    text_nl TEXT NOT NULL
);
INSERT INTO "ai_consent_events" VALUES(1,'2026-11-01T12:00:00+00:00','fixture-ai_consent_events-provider-1',1,'fixture-ai_consent_events-consent_version-1','fixture-ai_consent_events-actor-1','fixture-ai_consent_events-text_nl-1');
INSERT INTO "ai_consent_events" VALUES(2,'2026-11-02T12:00:00+00:00','fixture-ai_consent_events-provider-2',2,'fixture-ai_consent_events-consent_version-2','fixture-ai_consent_events-actor-2','fixture-ai_consent_events-text_nl-2');
CREATE TABLE ai_evidence_index (
    evidence_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    summary TEXT NOT NULL,
    instance_id TEXT,
    source_module TEXT,
    created_at TEXT NOT NULL
);
INSERT INTO "ai_evidence_index" VALUES('fixture-ai_evidence_index-evidence_id-1','fixture-ai_evidence_index-kind-1','fixture-ai_evidence_index-summary-1','fixture-ai_evidence_index-instance_id-1','fixture-ai_evidence_index-source_module-1','2026-11-01T12:00:00+00:00');
INSERT INTO "ai_evidence_index" VALUES('fixture-ai_evidence_index-evidence_id-2','fixture-ai_evidence_index-kind-2','fixture-ai_evidence_index-summary-2','fixture-ai_evidence_index-instance_id-2','fixture-ai_evidence_index-source_module-2','2026-11-02T12:00:00+00:00');
CREATE TABLE ai_feedback (
    id INTEGER PRIMARY KEY,
    case_id TEXT NOT NULL REFERENCES ai_cases(id) ON DELETE CASCADE,
    proposal_id INTEGER,
    verdict TEXT NOT NULL,
    comment TEXT,
    actor TEXT NOT NULL,
    ts TEXT NOT NULL
);
INSERT INTO "ai_feedback" VALUES(1,'fixture-ai_cases-id-1',1,'fixture-ai_feedback-verdict-1','fixture-ai_feedback-comment-1','fixture-ai_feedback-actor-1','2026-11-01T12:00:00+00:00');
INSERT INTO "ai_feedback" VALUES(2,'fixture-ai_cases-id-2',2,'fixture-ai_feedback-verdict-2','fixture-ai_feedback-comment-2','fixture-ai_feedback-actor-2','2026-11-02T12:00:00+00:00');
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
INSERT INTO "ai_model_evaluations" VALUES(1,'2026-11-01T12:00:00+00:00','fixture-ai_model_evaluations-provider-1','fixture-ai_model_evaluations-model-1','fixture-ai_model_evaluations-benchmark_version-1',1,1,1,1.5,1.5,1.5,1.5,1,1.5,'[]');
INSERT INTO "ai_model_evaluations" VALUES(2,'2026-11-02T12:00:00+00:00','fixture-ai_model_evaluations-provider-2','fixture-ai_model_evaluations-model-2','fixture-ai_model_evaluations-benchmark_version-2',2,2,2,2.5,2.5,2.5,2.5,2,2.5,'[]');
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
INSERT INTO "ai_policy_results" VALUES(1,1,'fixture-ai_cases-id-1','fixture-ai_policy_results-mode-1','log_only','fixture-ai_policy_results-action-1',1,'[]','fixture-ai_policy_results-execution_status-1','{}','fixture-ai_policy_results-decided_by-1','2026-11-01T12:00:00+00:00','2026-11-01T12:00:00+00:00');
INSERT INTO "ai_policy_results" VALUES(2,2,'fixture-ai_cases-id-2','fixture-ai_policy_results-mode-2','await_approval','fixture-ai_policy_results-action-2',2,'[]','fixture-ai_policy_results-execution_status-2','{}','fixture-ai_policy_results-decided_by-2','2026-11-02T12:00:00+00:00','2026-11-02T12:00:00+00:00');
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
INSERT INTO "ai_proposals" VALUES(1,'fixture-ai_cases-id-1','fixture-ai_proposals-provider-1','fixture-ai_proposals-model-1','fixture-ai_proposals-mode-1','fixture-ai_proposals-cache_key-1',1,1,'{}','[]','2026-11-01T12:00:00+00:00');
INSERT INTO "ai_proposals" VALUES(2,'fixture-ai_cases-id-2','fixture-ai_proposals-provider-2','fixture-ai_proposals-model-2','fixture-ai_proposals-mode-2','fixture-ai_proposals-cache_key-2',2,2,'{}','[]','2026-11-02T12:00:00+00:00');
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
INSERT INTO "ai_provider_configs" VALUES('fixture-ai_provider_configs-provider-1','2026-11-01T12:00:00+00:00',1,'fixture-ai_provider_configs-last_test_model-1','fixture-ai_provider_configs-last_test_detail-1',1,'{}','2026-11-01T12:00:00+00:00','fixture-ai_provider_configs-models_error-1','2026-11-01T12:00:00+00:00');
INSERT INTO "ai_provider_configs" VALUES('fixture-ai_provider_configs-provider-2','2026-11-02T12:00:00+00:00',2,'fixture-ai_provider_configs-last_test_model-2','fixture-ai_provider_configs-last_test_detail-2',2,'{}','2026-11-02T12:00:00+00:00','fixture-ai_provider_configs-models_error-2','2026-11-02T12:00:00+00:00');
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
INSERT INTO "ai_usage" VALUES(1,'2026-11-01T12:00:00+00:00','fixture-ai_usage-period_month-1','fixture-ai_usage-period_day-1','fixture-ai_usage-provider-1','fixture-ai_usage-model-1','fixture-ai_usage-task_type-1','fixture-ai_usage-case_id-1','fixture-ai_usage-purpose-1','fixture-ai_usage-status-1',1,1,1,1.5,1.5,'fixture-ai_usage-currency-1','fixture-ai_usage-cost_source-1',1,'fixture-ai_usage-error-1');
INSERT INTO "ai_usage" VALUES(2,'2026-11-02T12:00:00+00:00','fixture-ai_usage-period_month-2','fixture-ai_usage-period_day-2','fixture-ai_usage-provider-2','fixture-ai_usage-model-2','fixture-ai_usage-task_type-2','fixture-ai_usage-case_id-2','fixture-ai_usage-purpose-2','fixture-ai_usage-status-2',2,2,2,2.5,2.5,'fixture-ai_usage-currency-2','fixture-ai_usage-cost_source-2',2,'fixture-ai_usage-error-2');
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
INSERT INTO "anime_mappings" VALUES('fixture-anime_mappings-provider-1','fixture-anime_mappings-series_key-1',1,1,1,1,1,'2026-11-01T12:00:00+00:00');
INSERT INTO "anime_mappings" VALUES('fixture-anime_mappings-provider-2','fixture-anime_mappings-series_key-2',2,2,2,2,2,'2026-11-02T12:00:00+00:00');
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
INSERT INTO "anime_routing_actions" VALUES(1,'fixture-anime_routing_actions-source_instance-1','fixture-anime_routing_actions-target_instance-1','series',1,1,1,'fixture-anime_routing_actions-title-1','reverted','[]','fixture-anime_routing_actions-detail-1','2026-11-01T12:00:00+00:00','2026-11-01T12:00:00+00:00');
INSERT INTO "anime_routing_actions" VALUES(2,'fixture-anime_routing_actions-source_instance-2','fixture-anime_routing_actions-target_instance-2','movie',2,2,2,'fixture-anime_routing_actions-title-2','dismissed','[]','fixture-anime_routing_actions-detail-2','2026-11-02T12:00:00+00:00','2026-11-02T12:00:00+00:00');
CREATE TABLE anime_routing_exclusions (
    source_instance TEXT NOT NULL,
    kind TEXT NOT NULL,
    external_id INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    created_by TEXT,
    PRIMARY KEY (source_instance, kind, external_id)
);
INSERT INTO "anime_routing_exclusions" VALUES('fixture-anime_routing_exclusions-source_instance-1','fixture-anime_routing_exclusions-kind-1',1,'2026-11-01T12:00:00+00:00','fixture-anime_routing_exclusions-created_by-1');
INSERT INTO "anime_routing_exclusions" VALUES('fixture-anime_routing_exclusions-source_instance-2','fixture-anime_routing_exclusions-kind-2',2,'2026-11-02T12:00:00+00:00','fixture-anime_routing_exclusions-created_by-2');
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
INSERT INTO "api_keys" VALUES(1,'rooktest-app','mgk_-CCx','ecea841aa6b6bd329ca2c00129e23d9f450f3b529c4d7cb5927ab472f0c79809','viewer','user:rooktest','2026-11-10T07:30:01+00:00',NULL,NULL,NULL);
INSERT INTO "api_keys" VALUES(2,'fixture-api_keys-name-2','fixture-api_keys-prefix-2','fixture-api_keys-key_hash-2','operator','fixture-api_keys-created_by-2','2026-11-02T12:00:00+00:00','2026-11-02T12:00:00+00:00','2026-11-02T12:00:00+00:00','2026-11-08T12:00:00+00:00');
INSERT INTO "api_keys" VALUES(3,'fixture-api_keys-name-3','fixture-api_keys-prefix-3','fixture-api_keys-key_hash-3','viewer','fixture-api_keys-created_by-3','2026-11-03T12:00:00+00:00','2026-11-03T12:00:00+00:00','2026-11-03T12:00:00+00:00','2026-11-08T12:00:00+00:00');
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
INSERT INTO "audio_claim_failures" VALUES(1,'fixture-audio_claim_failures-instance_id-1','fixture-audio_claim_failures-release_title_norm-1','fixture-audio_claim_failures-release_group-1','fixture-audio_claim_failures-claim-1','fixture-audio_claim_failures-observed_languages-1','2026-11-01T12:00:00+00:00');
INSERT INTO "audio_claim_failures" VALUES(2,'fixture-audio_claim_failures-instance_id-2','fixture-audio_claim_failures-release_title_norm-2','fixture-audio_claim_failures-release_group-2','fixture-audio_claim_failures-claim-2','fixture-audio_claim_failures-observed_languages-2','2026-11-02T12:00:00+00:00');
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
INSERT INTO "audit_events" VALUES(1,'2026-11-10T07:30:01+00:00','user:rooktest','auth.setup','user','rooktest',NULL,NULL,'Eerste beheerder aangemaakt',NULL,NULL,NULL);
INSERT INTO "audit_events" VALUES(2,'2026-11-10T07:30:01+00:00','user:rooktest','user.create','user','kijker',NULL,NULL,NULL,NULL,'{"role": "viewer"}',NULL);
INSERT INTO "audit_events" VALUES(3,'2026-11-10T07:30:01+00:00','user:rooktest','apikey.created','api_key','1',NULL,NULL,'API-sleutel ''rooktest-app'' (viewer) aangemaakt',NULL,'{"expires_at": null, "name": "rooktest-app", "role": "viewer"}',NULL);
INSERT INTO "audit_events" VALUES(4,'2026-11-04T12:00:00+00:00','fixture-audit_events-actor-4','fixture-audit_events-action-4','fixture-audit_events-target_type-4','fixture-audit_events-target_id-4','fixture-audit_events-instance_id-4','fixture-audit_events-correlation_id-4','fixture-audit_events-reason-4','{}','{}','fixture-audit_events-undo_hint-4');
INSERT INTO "audit_events" VALUES(5,'2026-11-05T12:00:00+00:00','fixture-audit_events-actor-5','fixture-audit_events-action-5','fixture-audit_events-target_type-5','fixture-audit_events-target_id-5','fixture-audit_events-instance_id-5','fixture-audit_events-correlation_id-5','fixture-audit_events-reason-5','{}','{}','fixture-audit_events-undo_hint-5');
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
INSERT INTO "av1_imports" VALUES(1,'fixture-av1_imports-source_instance-1','/data/media/fixture/av1_imports/file_path-1.mkv','fixture-av1_imports-sha256-1','{}','[]','2026-11-01T12:00:00+00:00','fixture-av1_imports-created_by-1');
INSERT INTO "av1_imports" VALUES(2,'fixture-av1_imports-source_instance-2','/data/media/fixture/av1_imports/file_path-2.mkv','fixture-av1_imports-sha256-2','{}','[]','2026-11-02T12:00:00+00:00','fixture-av1_imports-created_by-2');
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
INSERT INTO "av1_provenance_map" VALUES('fixture-av1_provenance_map-source_instance-1','job',1,'fixture-av1_provenance_map-mediaguard_ref-1','fixture-av1_provenance_map-instance_id-1',1,'fixture-av1_provenance_map-status-1','/data/media/fixture/av1_provenance_map/path-1.mkv','/data/media/fixture/av1_provenance_map/mapped_path-1.mkv',1,1762603200.0,1,1,'{}',1,'2026-11-01T12:00:00+00:00');
INSERT INTO "av1_provenance_map" VALUES('fixture-av1_provenance_map-source_instance-2','bestand',2,'fixture-av1_provenance_map-mediaguard_ref-2','fixture-av1_provenance_map-instance_id-2',2,'fixture-av1_provenance_map-status-2','/data/media/fixture/av1_provenance_map/path-2.mkv','/data/media/fixture/av1_provenance_map/mapped_path-2.mkv',2,1762603201.0,2,2,'{}',2,'2026-11-02T12:00:00+00:00');
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
INSERT INTO "backfill_attempts" VALUES(1,1,'fixture-backfill_attempts-instance_id-1',1,'2026-11-01T12:00:00+00:00','fixture-backfill_attempts-trigger-1','fixture-backfill_attempts-command-1',1,1,'grabbed','[]','[]','fixture-backfill_attempts-message-1','2026-11-01T12:00:00+00:00');
INSERT INTO "backfill_attempts" VALUES(2,2,'fixture-backfill_attempts-instance_id-2',2,'2026-11-02T12:00:00+00:00','fixture-backfill_attempts-trigger-2','fixture-backfill_attempts-command-2',2,2,'no_candidate','[]','[]','fixture-backfill_attempts-message-2','2026-11-02T12:00:00+00:00');
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
INSERT INTO "backfill_runs" VALUES(1,'fixture-backfill_runs-instance_id-1','2026-11-01T12:00:00+00:00','2026-11-08T12:00:00+00:00','fixture-backfill_runs-trigger-1',1,1,1,'succeeded','{}','fixture-backfill_runs-message-1');
INSERT INTO "backfill_runs" VALUES(2,'fixture-backfill_runs-instance_id-2','2026-11-02T12:00:00+00:00','2026-11-08T12:00:00+00:00','fixture-backfill_runs-trigger-2',2,2,2,'succeeded','{}','fixture-backfill_runs-message-2');
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
INSERT INTO "delivery_events" VALUES(1,1,'2026-11-01T12:00:00+00:00','fixture-delivery_events-stage-1','fixture-delivery_events-action-1','fixture-delivery_events-actor-1','fixture-delivery_events-detail_nl-1','{}');
INSERT INTO "delivery_events" VALUES(2,2,'2026-11-02T12:00:00+00:00','fixture-delivery_events-stage-2','fixture-delivery_events-action-2','fixture-delivery_events-actor-2','fixture-delivery_events-detail_nl-2','{}');
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
, stopped_at TEXT, stop_code TEXT, stop_reason_nl TEXT, located_at TEXT, seerr_retries INTEGER NOT NULL DEFAULT 0, seerr_sync_attempts INTEGER NOT NULL DEFAULT 0, profile_checked_at TEXT, requirements_checked_at TEXT);
INSERT INTO "delivery_items" VALUES(1,'fixture-delivery_items-item_key-1','fixture-delivery_items-source-1','[]','series',1,1,'fixture-delivery_items-title-1','fixture-delivery_items-instance_id-1',1,'[]','fixture-delivery_items-stage-1','delivered','fixture-delivery_items-detail_nl-1','fixture-delivery_items-next_step_nl-1','{}',1,'2026-11-01T12:00:00+00:00','2026-11-01T12:00:00+00:00',NULL,'2026-11-01T12:00:00+00:00','2026-11-01T12:00:00+00:00','2026-11-01T12:00:00+00:00','2026-11-01T12:00:00+00:00','2026-11-01T12:00:00+00:00','2026-11-01T12:00:00+00:00','2026-11-01T12:00:00+00:00','fixture-delivery_items-stop_code-1','fixture-delivery_items-stop_reason_nl-1','2026-11-01T12:00:00+00:00',1,1,'2026-11-01T12:00:00+00:00','2026-11-01T12:00:00+00:00');
INSERT INTO "delivery_items" VALUES(2,'fixture-delivery_items-item_key-2','fixture-delivery_items-source-2','[]','movie',2,2,'fixture-delivery_items-title-2','fixture-delivery_items-instance_id-2',2,'[]','fixture-delivery_items-stage-2','not_found','fixture-delivery_items-detail_nl-2','fixture-delivery_items-next_step_nl-2','{}',2,'2026-11-02T12:00:00+00:00','2026-11-02T12:00:00+00:00',NULL,'2026-11-02T12:00:00+00:00','2026-11-02T12:00:00+00:00','2026-11-02T12:00:00+00:00','2026-11-02T12:00:00+00:00','2026-11-02T12:00:00+00:00','2026-11-02T12:00:00+00:00','2026-11-02T12:00:00+00:00','fixture-delivery_items-stop_code-2','fixture-delivery_items-stop_reason_nl-2','2026-11-02T12:00:00+00:00',2,2,'2026-11-02T12:00:00+00:00','2026-11-02T12:00:00+00:00');
CREATE TABLE delivery_searches (
    id INTEGER PRIMARY KEY,
    item_id INTEGER,
    instance_id TEXT NOT NULL,
    ts TEXT NOT NULL,
    command TEXT NOT NULL,
    cost INTEGER NOT NULL DEFAULT 1
);
INSERT INTO "delivery_searches" VALUES(1,1,'fixture-delivery_searches-instance_id-1','2026-11-01T12:00:00+00:00','fixture-delivery_searches-command-1',1);
INSERT INTO "delivery_searches" VALUES(2,2,'fixture-delivery_searches-instance_id-2','2026-11-02T12:00:00+00:00','fixture-delivery_searches-command-2',2);
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
INSERT INTO "download_janitor_runs" VALUES(1,'2026-11-01T12:00:00+00:00','2026-11-01T12:00:00+00:00','fixture-download_janitor_runs-status-1',1,1,1,'{}');
INSERT INTO "download_janitor_runs" VALUES(2,'2026-11-02T12:00:00+00:00','2026-11-02T12:00:00+00:00','fixture-download_janitor_runs-status-2',2,2,2,'{}');
CREATE TABLE download_janitor_seen (
    path TEXT PRIMARY KEY,
    first_seen TEXT NOT NULL
);
INSERT INTO "download_janitor_seen" VALUES('/data/media/fixture/download_janitor_seen/path-1.mkv','fixture-download_janitor_seen-first_seen-1');
INSERT INTO "download_janitor_seen" VALUES('/data/media/fixture/download_janitor_seen/path-2.mkv','fixture-download_janitor_seen-first_seen-2');
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
INSERT INTO "download_janitor_trash" VALUES(1,'fixture-download_janitor_trash-root-1','/data/media/fixture/download_janitor_trash/original_path-1.mkv','/data/media/fixture/download_janitor_trash/trash_path-1.mkv',1,1,'fixture-download_janitor_trash-reason-1','fixture-download_janitor_trash-client-1','fixture-download_janitor_trash-download_id-1','2026-11-01T12:00:00+00:00','2026-11-01T12:00:00+00:00','2026-11-08T12:00:00+00:00');
INSERT INTO "download_janitor_trash" VALUES(2,'fixture-download_janitor_trash-root-2','/data/media/fixture/download_janitor_trash/original_path-2.mkv','/data/media/fixture/download_janitor_trash/trash_path-2.mkv',2,2,'fixture-download_janitor_trash-reason-2','fixture-download_janitor_trash-client-2','fixture-download_janitor_trash-download_id-2','2026-11-02T12:00:00+00:00','2026-11-02T12:00:00+00:00','2026-11-08T12:00:00+00:00');
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
INSERT INTO "dv_conversion_evidence" VALUES(1,1,1,'fixture-dv_conversion_evidence-source_profile-1','fixture-dv_conversion_evidence-el_type-1','fixture-dv_conversion_evidence-route-1','fixture-dv_conversion_evidence-method-1',1,1,1,'{}','{}','[]','fixture-dv_conversion_evidence-approved_by-1','2026-11-01T12:00:00+00:00','2026-11-01T12:00:00+00:00');
INSERT INTO "dv_conversion_evidence" VALUES(2,2,2,'fixture-dv_conversion_evidence-source_profile-2','fixture-dv_conversion_evidence-el_type-2','fixture-dv_conversion_evidence-route-2','fixture-dv_conversion_evidence-method-2',2,2,2,'{}','{}','[]','fixture-dv_conversion_evidence-approved_by-2','2026-11-02T12:00:00+00:00','2026-11-02T12:00:00+00:00');
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
INSERT INTO "dv_playback_confirmations" VALUES(1,'fixture-dv_playback_confirmations-client-1','fixture-dv_playback_confirmations-server-1','fixture-dv_playback_confirmations-container-1','fixture-dv_playback_confirmations-codec-1','fixture-dv_playback_confirmations-dv_profile-1','fixture-dv_playback_confirmations-note-1','fixture-dv_playback_confirmations-confirmed_by-1','2026-11-01T12:00:00+00:00');
INSERT INTO "dv_playback_confirmations" VALUES(2,'fixture-dv_playback_confirmations-client-2','fixture-dv_playback_confirmations-server-2','fixture-dv_playback_confirmations-container-2','fixture-dv_playback_confirmations-codec-2','fixture-dv_playback_confirmations-dv_profile-2','fixture-dv_playback_confirmations-note-2','fixture-dv_playback_confirmations-confirmed_by-2','2026-11-02T12:00:00+00:00');
CREATE TABLE dv_policy_versions (
    id INTEGER PRIMARY KEY,
    scope_type TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    values_json TEXT NOT NULL,
    changed_at TEXT NOT NULL,
    changed_by TEXT,
    reason TEXT
);
INSERT INTO "dv_policy_versions" VALUES(1,'fixture-dv_policy_versions-scope_type-1','fixture-dv_policy_versions-scope_id-1','{}','2026-11-01T12:00:00+00:00','fixture-dv_policy_versions-changed_by-1','fixture-dv_policy_versions-reason-1');
INSERT INTO "dv_policy_versions" VALUES(2,'fixture-dv_policy_versions-scope_type-2','fixture-dv_policy_versions-scope_id-2','{}','2026-11-02T12:00:00+00:00','fixture-dv_policy_versions-changed_by-2','fixture-dv_policy_versions-reason-2');
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
INSERT INTO "dv_tests" VALUES(1,'fixture-dv_tests-instance_id-1','fixture-dv_tests-kind-1','fixture-dv_tests-provider_id-1','/data/media/fixture/dv_tests/source_path-1.mkv','{}','fixture-dv_tests-status-1','fixture-dv_tests-state-1','{}',1,'{}','fixture-dv_tests-error-1',1,'2026-11-01T12:00:00+00:00','fixture-dv_tests-created_by-1','2026-11-01T12:00:00+00:00');
INSERT INTO "dv_tests" VALUES(2,'fixture-dv_tests-instance_id-2','fixture-dv_tests-kind-2','fixture-dv_tests-provider_id-2','/data/media/fixture/dv_tests/source_path-2.mkv','{}','fixture-dv_tests-status-2','fixture-dv_tests-state-2','{}',2,'{}','fixture-dv_tests-error-2',2,'2026-11-02T12:00:00+00:00','fixture-dv_tests-created_by-2','2026-11-02T12:00:00+00:00');
CREATE TABLE encoding_autopilot_skips (
    path TEXT PRIMARY KEY,
    code TEXT NOT NULL,
    reason_nl TEXT NOT NULL,
    retry_after TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
INSERT INTO "encoding_autopilot_skips" VALUES('/data/media/fixture/encoding_autopilot_skips/path-1.mkv','fixture-encoding_autopilot_skips-code-1','fixture-encoding_autopilot_skips-reason_nl-1','fixture-encoding_autopilot_skips-retry_after-1','2026-11-01T12:00:00+00:00');
INSERT INTO "encoding_autopilot_skips" VALUES('/data/media/fixture/encoding_autopilot_skips/path-2.mkv','fixture-encoding_autopilot_skips-code-2','fixture-encoding_autopilot_skips-reason_nl-2','fixture-encoding_autopilot_skips-retry_after-2','2026-11-02T12:00:00+00:00');
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
INSERT INTO "encoding_benchmarks" VALUES(1,'/data/media/fixture/encoding_benchmarks/source_path-1.mkv','{}','{}','fixture-encoding_benchmarks-status-1','fixture-encoding_benchmarks-error-1','2026-11-01T12:00:00+00:00','fixture-encoding_benchmarks-created_by-1');
INSERT INTO "encoding_benchmarks" VALUES(2,'/data/media/fixture/encoding_benchmarks/source_path-2.mkv','{}','{}','fixture-encoding_benchmarks-status-2','fixture-encoding_benchmarks-error-2','2026-11-02T12:00:00+00:00','fixture-encoding_benchmarks-created_by-2');
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
, lane TEXT);
INSERT INTO "encoding_jobs" VALUES(1,'fixture-encoding_jobs-encoder_job_id-1','fixture-encoding_jobs-instance_id-1','fixture-encoding_jobs-media_kind-1','fixture-encoding_jobs-provider_id-1','/data/media/fixture/encoding_jobs/source_path-1.mkv',1,'succeeded','klaar','{}','{}','{}','{}','{}','{}','{}',100.0,'fixture-encoding_jobs-error-1','fixture-encoding_jobs-skip_reason-1',1,1,1,1,'fixture-encoding_jobs-correlation_id-1','2026-11-01T12:00:00+00:00','fixture-encoding_jobs-created_by-1','2026-11-01T12:00:00+00:00','fixture-encoding_jobs-lane-1');
INSERT INTO "encoding_jobs" VALUES(2,'fixture-encoding_jobs-encoder_job_id-2','fixture-encoding_jobs-instance_id-2','fixture-encoding_jobs-media_kind-2','fixture-encoding_jobs-provider_id-2','/data/media/fixture/encoding_jobs/source_path-2.mkv',2,'failed','klaar','{}','{}','{}','{}','{}','{}','{}',100.0,'fixture-encoding_jobs-error-2','fixture-encoding_jobs-skip_reason-2',2,2,2,2,'fixture-encoding_jobs-correlation_id-2','2026-11-02T12:00:00+00:00','fixture-encoding_jobs-created_by-2','2026-11-02T12:00:00+00:00','fixture-encoding_jobs-lane-2');
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
INSERT INTO "encoding_plans" VALUES(1,'fixture-encoding_plans-instance_id-1','fixture-encoding_plans-media_kind-1','fixture-encoding_plans-provider_id-1','/data/media/fixture/encoding_plans/source_path-1.mkv','{}','{}',1,'fixture-encoding_plans-risk_class-1','2026-11-01T12:00:00+00:00','fixture-encoding_plans-created_by-1');
INSERT INTO "encoding_plans" VALUES(2,'fixture-encoding_plans-instance_id-2','fixture-encoding_plans-media_kind-2','fixture-encoding_plans-provider_id-2','/data/media/fixture/encoding_plans/source_path-2.mkv','{}','{}',2,'fixture-encoding_plans-risk_class-2','2026-11-02T12:00:00+00:00','fixture-encoding_plans-created_by-2');
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
INSERT INTO "hook_decisions" VALUES(1,'2026-11-01T12:00:00+00:00','fixture-hook_decisions-instance_id-1','fixture-hook_decisions-hook-1','fixture-hook_decisions-release_title-1','fixture-hook_decisions-download_id-1','fixture-hook_decisions-decision-1','fixture-hook_decisions-reason_code-1','fixture-hook_decisions-message-1',1,1.5,'{}');
INSERT INTO "hook_decisions" VALUES(2,'2026-11-02T12:00:00+00:00','fixture-hook_decisions-instance_id-2','fixture-hook_decisions-hook-2','fixture-hook_decisions-release_title-2','fixture-hook_decisions-download_id-2','fixture-hook_decisions-decision-2','fixture-hook_decisions-reason_code-2','fixture-hook_decisions-message-2',2,2.5,'{}');
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
INSERT INTO "identity_known_hashes" VALUES('fixture-identity_known_hashes-hash_type-1','fixture-identity_known_hashes-hash-1',1,'fixture-identity_known_hashes-instance_id-1','fixture-identity_known_hashes-series_key-1',1,1,1,'fixture-identity_known_hashes-source-1','2026-11-01T12:00:00+00:00');
INSERT INTO "identity_known_hashes" VALUES('fixture-identity_known_hashes-hash_type-2','fixture-identity_known_hashes-hash-2',2,'fixture-identity_known_hashes-instance_id-2','fixture-identity_known_hashes-series_key-2',2,2,2,'fixture-identity_known_hashes-source-2','2026-11-02T12:00:00+00:00');
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
INSERT INTO "identity_results" VALUES(1,'fixture-identity_results-instance_id-1','fixture-identity_results-kind-1','fixture-identity_results-provider_id-1','/data/media/fixture/identity_results/file_path-1.mkv','verified',1.5,1,'[]','[]','[]','fixture-identity_results-reason_nl-1','fixture-identity_results-source-1','2026-11-01T12:00:00+00:00','fixture-identity_results-created_by-1',1);
INSERT INTO "identity_results" VALUES(2,'fixture-identity_results-instance_id-2','fixture-identity_results-kind-2','fixture-identity_results-provider_id-2','/data/media/fixture/identity_results/file_path-2.mkv','probable',2.5,2,'[]','[]','[]','fixture-identity_results-reason_nl-2','fixture-identity_results-source-2','2026-11-02T12:00:00+00:00','fixture-identity_results-created_by-2',2);
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
INSERT INTO "import_events" VALUES(1,'fixture-import_events-instance_id-1','fixture-import_events-event_type-1',0,'fixture-import_events-download_id-1',1,1,1,'/data/media/fixture/import_events/file_path-1.mkv','fixture-import_events-idempotency_key-1',1,'{}','2026-11-01T12:00:00+00:00');
INSERT INTO "import_events" VALUES(2,'fixture-import_events-instance_id-2','fixture-import_events-event_type-2',0,'fixture-import_events-download_id-2',2,2,2,'/data/media/fixture/import_events/file_path-2.mkv','fixture-import_events-idempotency_key-2',2,'{}','2026-11-02T12:00:00+00:00');
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
INSERT INTO "instance_cutover_state" VALUES('sonarr-series','fixture-instance_cutover_state-phase-1','fixture-instance_cutover_state-source_url-1','fixture-instance_cutover_state-seerr_kind-1',1,'{}','{}','2026-11-01T12:00:00+00:00','2026-11-01T12:00:00+00:00','fixture-instance_cutover_state-notes-1','2026-11-01T12:00:00+00:00','fixture-instance_cutover_state-updated_by-1');
INSERT INTO "instance_cutover_state" VALUES('sonarr-anime','fixture-instance_cutover_state-phase-2','fixture-instance_cutover_state-source_url-2','fixture-instance_cutover_state-seerr_kind-2',2,'{}','{}','2026-11-02T12:00:00+00:00','2026-11-02T12:00:00+00:00','fixture-instance_cutover_state-notes-2','2026-11-02T12:00:00+00:00','fixture-instance_cutover_state-updated_by-2');
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
INSERT INTO "instance_indexer_assignments" VALUES('sonarr-series',1,'fixture-instance_indexer_assignments-name-1','fixture-instance_indexer_assignments-implementation-1','fixture-instance_indexer_assignments-protocol-1',1,1,1,1,'[]','2026-11-01T12:00:00+00:00');
INSERT INTO "instance_indexer_assignments" VALUES('sonarr-anime',2,'fixture-instance_indexer_assignments-name-2','fixture-instance_indexer_assignments-implementation-2','fixture-instance_indexer_assignments-protocol-2',2,2,2,2,'[]','2026-11-02T12:00:00+00:00');
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
INSERT INTO "instance_migration_checkpoints" VALUES(1,'fixture-instance_migration_checkpoints-instance_id-1','fixture-instance_migration_checkpoints-phase-1','fixture-instance_migration_checkpoints-kind-1','fixture-instance_migration_checkpoints-source_key-1','fixture-instance_migration_checkpoints-target_ref-1','{}','2026-11-01T12:00:00+00:00');
INSERT INTO "instance_migration_checkpoints" VALUES(2,'fixture-instance_migration_checkpoints-instance_id-2','fixture-instance_migration_checkpoints-phase-2','fixture-instance_migration_checkpoints-kind-2','fixture-instance_migration_checkpoints-source_key-2','fixture-instance_migration_checkpoints-target_ref-2','{}','2026-11-02T12:00:00+00:00');
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
INSERT INTO "instance_ownership" VALUES(1,'tv','fixture-instance_ownership-canonical_id-1','fixture-instance_ownership-edition-1','fixture-instance_ownership-target_profile-1','sonarr-series',1,'/data/media/fixture/instance_ownership/root_path-1.mkv',0,1,'fixture-instance_ownership-reason-1','2026-11-01T12:00:00+00:00','fixture-instance_ownership-created_by-1');
INSERT INTO "instance_ownership" VALUES(2,'anime','fixture-instance_ownership-canonical_id-2','fixture-instance_ownership-edition-2','fixture-instance_ownership-target_profile-2','sonarr-anime',2,'/data/media/fixture/instance_ownership/root_path-2.mkv',0,2,'fixture-instance_ownership-reason-2','2026-11-02T12:00:00+00:00','fixture-instance_ownership-created_by-2');
CREATE TABLE instance_profiles (
    instance_id TEXT NOT NULL REFERENCES instances(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN ('quality','language','custom_format')),
    provider_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    data_json TEXT NOT NULL DEFAULT '{}',
    synced_at TEXT NOT NULL,
    PRIMARY KEY (instance_id, kind, provider_id)
);
INSERT INTO "instance_profiles" VALUES('sonarr-series','quality',1,'fixture-instance_profiles-name-1','{}','2026-11-01T12:00:00+00:00');
INSERT INTO "instance_profiles" VALUES('sonarr-anime','language',2,'fixture-instance_profiles-name-2','{}','2026-11-02T12:00:00+00:00');
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
INSERT INTO "instance_root_mappings" VALUES(1,'sonarr-series','/data/media/fixture/instance_root_mappings/root_path-1.mkv','/data/media/fixture/instance_root_mappings/core_path-1.mkv','fixture-instance_root_mappings-library_label-1',1,1,1,1,'2026-11-01T12:00:00+00:00');
INSERT INTO "instance_root_mappings" VALUES(2,'sonarr-anime','/data/media/fixture/instance_root_mappings/root_path-2.mkv','/data/media/fixture/instance_root_mappings/core_path-2.mkv','fixture-instance_root_mappings-library_label-2',2,2,2,2,'2026-11-02T12:00:00+00:00');
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
INSERT INTO "instance_seerr_routing" VALUES('sonarr-series','sonarr',1,'fixture-instance_seerr_routing-role-1',1,1,1,'fixture-instance_seerr_routing-expected_root_folder-1','fixture-instance_seerr_routing-notes-1','2026-11-01T12:00:00+00:00','fixture-instance_seerr_routing-updated_by-1');
INSERT INTO "instance_seerr_routing" VALUES('sonarr-anime','radarr',2,'fixture-instance_seerr_routing-role-2',2,2,2,'fixture-instance_seerr_routing-expected_root_folder-2','fixture-instance_seerr_routing-notes-2','2026-11-02T12:00:00+00:00','fixture-instance_seerr_routing-updated_by-2');
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
INSERT INTO "instances" VALUES('sonarr-series','sonarr','tv','Sonarr Series','http://mediaguard-sonarr-series:8989',NULL,'unknown',0,0,0,'open',NULL,NULL,NULL,NULL,NULL,NULL,'[]',NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,'standard',NULL,'tv',10,'2026-11-10T07:30:01+00:00','2026-11-10T07:30:01+00:00',NULL);
INSERT INTO "instances" VALUES('sonarr-anime','sonarr','anime','Sonarr Anime','http://mediaguard-sonarr-anime:8989',NULL,'unknown',0,0,0,'open',NULL,NULL,NULL,NULL,NULL,NULL,'[]',NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,'anime',NULL,'tv-anime',20,'2026-11-10T07:30:01+00:00','2026-11-10T07:30:01+00:00',NULL);
INSERT INTO "instances" VALUES('radarr-films','radarr','movie','Radarr Films','http://mediaguard-radarr-films:7878',NULL,'unknown',0,0,0,'open',NULL,NULL,NULL,NULL,NULL,NULL,'[]',NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,'movies',30,'2026-11-10T07:30:01+00:00','2026-11-10T07:30:01+00:00',NULL);
INSERT INTO "instances" VALUES('radarr-animefilms','radarr','anime_movie','Radarr Animefilms','http://mediaguard-radarr-animefilms:7878',NULL,'unknown',0,0,0,'open',NULL,NULL,NULL,NULL,NULL,NULL,'[]',NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,'movies-anime',40,'2026-11-10T07:30:01+00:00','2026-11-10T07:30:01+00:00',NULL);
INSERT INTO "instances" VALUES('fixture-instances-id-5','sonarr','tv','fixture-instances-name-5','http://fixture.invalid',NULL,'offline',1,5,1,'open','fixture-instances-version-5','fixture-instances-upstream_app_name-5',5,'2026-11-05T12:00:00+00:00','2026-11-05T12:00:00+00:00','fixture-instances-last_error-5','[]','{}','2026-11-05T12:00:00+00:00','fixture-instances-suspended_by-5',NULL,'2026-11-05T12:00:00+00:00',5,5,'fixture-instances-default_root_folder-5','fixture-instances-default_monitor-5','fixture-instances-default_monitor_new_items-5','fixture-instances-default_series_type-5','fixture-instances-default_minimum_availability-5','fixture-instances-download_category-5',5,'2026-11-05T12:00:00+00:00','2026-11-05T12:00:00+00:00','2026-11-05T12:00:00+00:00');
INSERT INTO "instances" VALUES('fixture-instances-id-6','radarr','anime','fixture-instances-name-6','http://fixture.invalid',NULL,'offline',1,6,1,'closed','fixture-instances-version-6','fixture-instances-upstream_app_name-6',6,'2026-11-06T12:00:00+00:00','2026-11-06T12:00:00+00:00','fixture-instances-last_error-6','[]','{}','2026-11-06T12:00:00+00:00','fixture-instances-suspended_by-6',NULL,'2026-11-06T12:00:00+00:00',6,6,'fixture-instances-default_root_folder-6','fixture-instances-default_monitor-6','fixture-instances-default_monitor_new_items-6','fixture-instances-default_series_type-6','fixture-instances-default_minimum_availability-6','fixture-instances-download_category-6',6,'2026-11-06T12:00:00+00:00','2026-11-06T12:00:00+00:00','2026-11-06T12:00:00+00:00');
CREATE TABLE job_logs (
    id INTEGER PRIMARY KEY,
    job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    ts TEXT NOT NULL,
    level TEXT NOT NULL,
    message TEXT NOT NULL
);
INSERT INTO "job_logs" VALUES(1,1,'2026-11-01T12:00:00+00:00','fixture-job_logs-level-1','fixture-job_logs-message-1');
INSERT INTO "job_logs" VALUES(2,2,'2026-11-02T12:00:00+00:00','fixture-job_logs-level-2','fixture-job_logs-message-2');
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
INSERT INTO "jobs" VALUES(1,'fixture.voorbeeld','succeeded',NULL,NULL,NULL,100,'{"fixture": true}',NULL,NULL,0.0,0,3,NULL,'bf8d38c9ced8466da81e4522374482ba',NULL,'2026-11-10T07:30:01+00:00',NULL,'2026-11-08T12:00:00+00:00','rooktest',NULL,NULL,0);
INSERT INTO "jobs" VALUES(2,'fixture.voorbeeld','cancelled',NULL,NULL,NULL,100,'{"fixture": true}',NULL,NULL,0.0,0,3,NULL,'27854617c9aa43a4be54cfbe826a9f11',NULL,'2026-11-10T07:30:01+00:00',NULL,'2026-11-08T12:00:00+00:00','rooktest',NULL,NULL,0);
INSERT INTO "jobs" VALUES(3,'fixture-jobs-job_type-3','succeeded','fixture-jobs-instance_id-3','fixture-jobs-target_type-3','fixture-jobs-target_id-3',3,'{}','{}','fixture-jobs-error-3',3.5,1,3,'fixture-jobs-idempotency_key-3','fixture-jobs-correlation_id-3','fixture-jobs-run_after-3','2026-11-03T12:00:00+00:00','2026-11-03T12:00:00+00:00','2026-11-03T12:00:00+00:00','fixture-jobs-created_by-3','2026-11-03T12:00:00+00:00','fixture-jobs-resolved_reason-3',3);
INSERT INTO "jobs" VALUES(4,'fixture-jobs-job_type-4','cancelled','fixture-jobs-instance_id-4','fixture-jobs-target_type-4','fixture-jobs-target_id-4',4,'{}','{}','fixture-jobs-error-4',4.5,1,4,'fixture-jobs-idempotency_key-4','fixture-jobs-correlation_id-4','fixture-jobs-run_after-4','2026-11-04T12:00:00+00:00','2026-11-04T12:00:00+00:00','2026-11-04T12:00:00+00:00','fixture-jobs-created_by-4','2026-11-04T12:00:00+00:00','fixture-jobs-resolved_reason-4',4);
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
INSERT INTO "lib_episodes" VALUES(1,'fixture-lib_episodes-instance_id-1',1,1,1,1,1,1,1,'fixture-lib_episodes-title-1','fixture-lib_episodes-air_date-1','2026-11-01T12:00:00+00:00',1,0,1,1,'2026-11-01T12:00:00+00:00');
INSERT INTO "lib_episodes" VALUES(2,'fixture-lib_episodes-instance_id-2',2,2,2,2,2,2,2,'fixture-lib_episodes-title-2','fixture-lib_episodes-air_date-2','2026-11-02T12:00:00+00:00',2,0,2,2,'2026-11-02T12:00:00+00:00');
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
INSERT INTO "lib_files" VALUES(1,'fixture-lib_files-instance_id-1','episode',1,1,'/data/media/fixture/lib_files/path-1.mkv','/data/media/fixture/lib_files/relative_path-1.mkv',1,'fixture-lib_files-quality-1',1,'[]','fixture-lib_files-release_group-1','{}','fixture-lib_files-date_added-1','2026-11-01T12:00:00+00:00');
INSERT INTO "lib_files" VALUES(2,'fixture-lib_files-instance_id-2','movie',2,2,'/data/media/fixture/lib_files/path-2.mkv','/data/media/fixture/lib_files/relative_path-2.mkv',2,'fixture-lib_files-quality-2',2,'[]','fixture-lib_files-release_group-2','{}','fixture-lib_files-date_added-2','2026-11-02T12:00:00+00:00');
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
INSERT INTO "lib_items" VALUES(1,'sonarr-series','series',1,'fixture-lib_items-title-1','fixture-lib_items-sort_title-1',1,1,1,'fixture-lib_items-imdb_id-1',1,'fixture-lib_items-series_type-1','fixture-lib_items-status-1',1,'fixture-lib_items-monitor_new_items-1','fixture-lib_items-minimum_availability-1',0,1,1,'/data/media/fixture/lib_items/root_folder_path-1.mkv','/data/media/fixture/lib_items/path-1.mkv','[]',0,1,1,1,1,'fixture-lib_items-network-1','fixture-lib_items-in_cinemas-1','fixture-lib_items-digital_release-1','fixture-lib_items-physical_release-1','2026-11-01T12:00:00+00:00','fixture-lib_items-poster_url-1','2026-11-01T12:00:00+00:00','2026-11-01T12:00:00+00:00','fixture-lib_items-sync_fingerprint-1');
INSERT INTO "lib_items" VALUES(2,'sonarr-anime','movie',2,'fixture-lib_items-title-2','fixture-lib_items-sort_title-2',2,2,2,'fixture-lib_items-imdb_id-2',2,'fixture-lib_items-series_type-2','fixture-lib_items-status-2',2,'fixture-lib_items-monitor_new_items-2','fixture-lib_items-minimum_availability-2',0,2,2,'/data/media/fixture/lib_items/root_folder_path-2.mkv','/data/media/fixture/lib_items/path-2.mkv','[]',0,2,2,2,2,'fixture-lib_items-network-2','fixture-lib_items-in_cinemas-2','fixture-lib_items-digital_release-2','fixture-lib_items-physical_release-2','2026-11-02T12:00:00+00:00','fixture-lib_items-poster_url-2','2026-11-02T12:00:00+00:00','2026-11-02T12:00:00+00:00','fixture-lib_items-sync_fingerprint-2');
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
INSERT INTO "lib_seasons" VALUES('fixture-lib_seasons-instance_id-1',1,1,1,1,1,1,1,'2026-11-01T12:00:00+00:00');
INSERT INTO "lib_seasons" VALUES('fixture-lib_seasons-instance_id-2',2,2,2,2,2,2,2,'2026-11-02T12:00:00+00:00');
CREATE TABLE library_sync_runs (
    id INTEGER PRIMARY KEY,
    instance_id TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    counts_json TEXT NOT NULL DEFAULT '{}',
    error TEXT
);
INSERT INTO "library_sync_runs" VALUES(1,'fixture-library_sync_runs-instance_id-1','2026-11-01T12:00:00+00:00','2026-11-01T12:00:00+00:00','fixture-library_sync_runs-status-1','{}','fixture-library_sync_runs-error-1');
INSERT INTO "library_sync_runs" VALUES(2,'fixture-library_sync_runs-instance_id-2','2026-11-02T12:00:00+00:00','2026-11-02T12:00:00+00:00','fixture-library_sync_runs-status-2','{}','fixture-library_sync_runs-error-2');
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
INSERT INTO "login_history" VALUES(1,1,'rooktest','2026-11-10T07:30:01+00:00','testclient','testclient',1,'password','Eerste beheerder aangemaakt');
INSERT INTO "login_history" VALUES(2,2,'fixture-login_history-username-2','2026-11-02T12:00:00+00:00','fixture-login_history-ip-2','fixture-login_history-user_agent-2',2,'fixture-login_history-method-2','fixture-login_history-reason-2');
INSERT INTO "login_history" VALUES(3,3,'fixture-login_history-username-3','2026-11-03T12:00:00+00:00','fixture-login_history-ip-3','fixture-login_history-user_agent-3',3,'fixture-login_history-method-3','fixture-login_history-reason-3');
INSERT INTO "login_history" VALUES(4,1,'rooktest','2026-11-10T07:30:01+00:00','testclient','testclient',1,'password',NULL);
CREATE TABLE mediaserver_actions (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    kind TEXT NOT NULL,              -- refresh|playback_test|hold|resume|discover|error
    path TEXT,
    transaction_id INTEGER,
    ok INTEGER NOT NULL DEFAULT 1,
    detail_nl TEXT
);
INSERT INTO "mediaserver_actions" VALUES(1,'2026-11-01T12:00:00+00:00','fixture-mediaserver_actions-kind-1','/data/media/fixture/mediaserver_actions/path-1.mkv',1,1,'fixture-mediaserver_actions-detail_nl-1');
INSERT INTO "mediaserver_actions" VALUES(2,'2026-11-02T12:00:00+00:00','fixture-mediaserver_actions-kind-2','/data/media/fixture/mediaserver_actions/path-2.mkv',2,2,'fixture-mediaserver_actions-detail_nl-2');
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
INSERT INTO "mediaserver_pending" VALUES(1,'/data/media/fixture/mediaserver_pending/path-1.mkv','fixture-mediaserver_pending-kind-1',1,'2026-11-01T12:00:00+00:00',NULL,1,'ok','fixture-mediaserver_pending-detail_nl-1','2026-11-08T12:00:00+00:00','2026-11-01T12:00:00+00:00');
INSERT INTO "mediaserver_pending" VALUES(2,'/data/media/fixture/mediaserver_pending/path-2.mkv','fixture-mediaserver_pending-kind-2',2,'2026-11-02T12:00:00+00:00',NULL,2,'ok','fixture-mediaserver_pending-detail_nl-2','2026-11-08T12:00:00+00:00','2026-11-02T12:00:00+00:00');
INSERT INTO "mediaserver_pending" VALUES(3,'/data/media/fixture/replacement_transactions/final_path-3.mkv','replace',3,'2026-11-03T12:00:00+00:00','2099-01-01T00:00:00+00:00',0,NULL,NULL,NULL,'2026-11-03T12:00:00+00:00');
CREATE TABLE mediaserver_watch (
    path TEXT PRIMARY KEY,
    title TEXT,
    user_name TEXT,
    last_seen TEXT NOT NULL
);
INSERT INTO "mediaserver_watch" VALUES('/data/media/fixture/mediaserver_watch/path-1.mkv','fixture-mediaserver_watch-title-1','fixture-mediaserver_watch-user_name-1','fixture-mediaserver_watch-last_seen-1');
INSERT INTO "mediaserver_watch" VALUES('/data/media/fixture/mediaserver_watch/path-2.mkv','fixture-mediaserver_watch-title-2','fixture-mediaserver_watch-user_name-2','fixture-mediaserver_watch-last_seen-2');
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
INSERT INTO "migration_cutovers" VALUES(1,1,'fixture-source_instances-id-1','sonarr-series','cutover','fixture-migration_cutovers-phase-1','completed','fixture-migration_cutovers-confirmed_by-1','fixture-migration_cutovers-confirmed_name-1','{}','{}','{}','{}','{}','{}','fixture-migration_cutovers-error-1','2026-11-01T12:00:00+00:00','2026-11-01T12:00:00+00:00','2026-11-01T12:00:00+00:00','2026-11-01T12:00:00+00:00');
INSERT INTO "migration_cutovers" VALUES(2,2,'fixture-source_instances-id-2','sonarr-anime','activate_search','fixture-migration_cutovers-phase-2','rolled_back','fixture-migration_cutovers-confirmed_by-2','fixture-migration_cutovers-confirmed_name-2','{}','{}','{}','{}','{}','{}','fixture-migration_cutovers-error-2','2026-11-02T12:00:00+00:00','2026-11-02T12:00:00+00:00','2026-11-02T12:00:00+00:00','2026-11-02T12:00:00+00:00');
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
INSERT INTO "migration_mapping_items" VALUES(1,1,'fixture-migration_mapping_items-source_id-1','series','fixture-migration_mapping_items-external_id-1','fixture-migration_mapping_items-title-1','fixture-migration_mapping_items-target_instance_id-1','fixture-migration_mapping_items-flag-1','fixture-migration_mapping_items-message_nl-1');
INSERT INTO "migration_mapping_items" VALUES(2,2,'fixture-migration_mapping_items-source_id-2','movie','fixture-migration_mapping_items-external_id-2','fixture-migration_mapping_items-title-2','fixture-migration_mapping_items-target_instance_id-2','fixture-migration_mapping_items-flag-2','fixture-migration_mapping_items-message_nl-2');
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
INSERT INTO "migration_mappings" VALUES(1,'fixture-source_instances-id-1','sonarr-series','proposed','auto',1.5,1.5,'[]','[]','[]','fixture-migration_mappings-confirmed_by-1','2026-11-01T12:00:00+00:00','2026-11-01T12:00:00+00:00');
INSERT INTO "migration_mappings" VALUES(2,'fixture-source_instances-id-2','sonarr-anime','confirmed','native',2.5,2.5,'[]','[]','[]','fixture-migration_mappings-confirmed_by-2','2026-11-02T12:00:00+00:00','2026-11-02T12:00:00+00:00');
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
INSERT INTO "migration_reports" VALUES(1,1,'preflight','fixture-migration_reports-source_id-1','fixture-migration_reports-target_instance_id-1','go','{}','[]','2026-11-01T12:00:00+00:00','fixture-migration_reports-created_by-1');
INSERT INTO "migration_reports" VALUES(2,2,'verify','fixture-migration_reports-source_id-2','fixture-migration_reports-target_instance_id-2','warn','{}','[]','2026-11-02T12:00:00+00:00','fixture-migration_reports-created_by-2');
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
INSERT INTO "migration_runs" VALUES(1,'fixture-migration_runs-name-1','completed','{}','{}','2026-11-01T12:00:00+00:00','fixture-migration_runs-created_by-1','2026-11-01T12:00:00+00:00');
INSERT INTO "migration_runs" VALUES(2,'fixture-migration_runs-name-2','cancelled','{}','{}','2026-11-02T12:00:00+00:00','fixture-migration_runs-created_by-2','2026-11-02T12:00:00+00:00');
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
INSERT INTO "migration_shadow_states" VALUES(1,'fixture-migration_shadow_states-target_instance_id-1','fixture-migration_shadow_states-source_id-1','fixture-migration_shadow_states-mode-1','{}','2026-11-01T12:00:00+00:00','2026-11-01T12:00:00+00:00');
INSERT INTO "migration_shadow_states" VALUES(2,'fixture-migration_shadow_states-target_instance_id-2','fixture-migration_shadow_states-source_id-2','fixture-migration_shadow_states-mode-2','{}','2026-11-02T12:00:00+00:00','2026-11-02T12:00:00+00:00');
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
INSERT INTO "migration_steps" VALUES(1,'fixture-migration_steps-step-1','fixture-migration_steps-scope-1','succeeded',1,1,'{}','fixture-migration_steps-error-1','2026-11-01T12:00:00+00:00','2026-11-01T12:00:00+00:00','2026-11-01T12:00:00+00:00');
INSERT INTO "migration_steps" VALUES(2,'fixture-migration_steps-step-2','fixture-migration_steps-scope-2','skipped',2,2,'{}','fixture-migration_steps-error-2','2026-11-02T12:00:00+00:00','2026-11-02T12:00:00+00:00','2026-11-02T12:00:00+00:00');
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
INSERT INTO "missing_diagnostics" VALUES(1,1,'fixture-missing_diagnostics-instance_id-1','2026-11-01T12:00:00+00:00','fixture-missing_diagnostics-primary_code-1','{}','{}',1);
INSERT INTO "missing_diagnostics" VALUES(2,2,'fixture-missing_diagnostics-instance_id-2','2026-11-02T12:00:00+00:00','fixture-missing_diagnostics-primary_code-2','{}','{}',2);
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
INSERT INTO "notifications" VALUES(1,'2026-11-10T07:30:01+00:00','warning','Rooktest: melding van de vorige versie','Blijft na de update staan.',NULL,NULL,0,1,'fixture:melding',NULL);
INSERT INTO "notifications" VALUES(2,'2026-11-10T07:30:01+00:00','info','Rooktest: info','Gelezen melding.',NULL,NULL,1,1,'fixture:info',NULL);
INSERT INTO "notifications" VALUES(3,'2026-11-03T12:00:00+00:00','warning','fixture-notifications-title-3','fixture-notifications-body-3','fixture-notifications-link-3','fixture-notifications-instance_id-3',3,3,'fixture-notifications-dedupe_key-3','2026-11-03T12:00:00+00:00');
INSERT INTO "notifications" VALUES(4,'2026-11-04T12:00:00+00:00','error','fixture-notifications-title-4','fixture-notifications-body-4','fixture-notifications-link-4','fixture-notifications-instance_id-4',4,4,'fixture-notifications-dedupe_key-4','2026-11-04T12:00:00+00:00');
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
INSERT INTO "notify_channels" VALUES(1,'ntfy','Telefoon (ntfy)',0,'{"server": "https://ntfy.sh", "topic": "mediaguard-CtxwQAeyzx1y86iXxv4H9ZFM"}',1,NULL,NULL,'2026-11-10T07:30:01+00:00','2026-11-10T07:30:01+00:00');
INSERT INTO "notify_channels" VALUES(2,'pushover','fixture-notify_channels-name-2',0,'{}',2,'2026-11-02T12:00:00+00:00','fixture-notify_channels-last_error-2','2026-11-02T12:00:00+00:00','2026-11-02T12:00:00+00:00');
INSERT INTO "notify_channels" VALUES(3,'telegram','fixture-notify_channels-name-3',0,'{}',3,'2026-11-03T12:00:00+00:00','fixture-notify_channels-last_error-3','2026-11-03T12:00:00+00:00','2026-11-03T12:00:00+00:00');
CREATE TABLE notify_digest_items (
    id INTEGER PRIMARY KEY,
    notification_id INTEGER,
    category TEXT NOT NULL,
    severity TEXT NOT NULL,
    title TEXT NOT NULL,
    created_at TEXT NOT NULL
);
INSERT INTO "notify_digest_items" VALUES(1,1,'waiting','warning','Rooktest: melding van de vorige versie','2026-11-10T07:30:01+00:00');
INSERT INTO "notify_digest_items" VALUES(2,2,'waiting','info','Rooktest: info','2026-11-10T07:30:01+00:00');
INSERT INTO "notify_digest_items" VALUES(3,3,'fixture-notify_digest_items-category-3','fixture-notify_digest_items-severity-3','fixture-notify_digest_items-title-3','2026-11-03T12:00:00+00:00');
INSERT INTO "notify_digest_items" VALUES(4,4,'fixture-notify_digest_items-category-4','fixture-notify_digest_items-severity-4','fixture-notify_digest_items-title-4','2026-11-04T12:00:00+00:00');
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
INSERT INTO "notify_digests" VALUES(1,'daily','fixture-notify_digests-period_start-1','fixture-notify_digests-period_end-1','{}','fixture-notify_digests-text_nl-1','2026-11-01T12:00:00+00:00');
INSERT INTO "notify_digests" VALUES(2,'weekly','fixture-notify_digests-period_start-2','fixture-notify_digests-period_end-2','{}','fixture-notify_digests-text_nl-2','2026-11-02T12:00:00+00:00');
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
INSERT INTO "notify_outbox" VALUES(1,1,1,'fixture-notify_outbox-category-1','fixture-notify_outbox-title-1','fixture-notify_outbox-body-1','fixture-notify_outbox-link-1','fixture-notify_outbox-priority-1','/data/media/fixture/notify_outbox/attachment_path-1.mkv','sent',1,'2026-11-08T12:00:00+00:00','fixture-notify_outbox-last_error-1','2026-11-01T12:00:00+00:00','2026-11-01T12:00:00+00:00');
INSERT INTO "notify_outbox" VALUES(2,2,2,'fixture-notify_outbox-category-2','fixture-notify_outbox-title-2','fixture-notify_outbox-body-2','fixture-notify_outbox-link-2','fixture-notify_outbox-priority-2','/data/media/fixture/notify_outbox/attachment_path-2.mkv','dropped',2,'2026-11-08T12:00:00+00:00','fixture-notify_outbox-last_error-2','2026-11-02T12:00:00+00:00','2026-11-02T12:00:00+00:00');
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
INSERT INTO "offsite_copies" VALUES(1,'fixture-offsite_copies-bundle_name-1','fixture-offsite_copies-target_kind-1','fixture-offsite_copies-target-1','ok',1,'fixture-offsite_copies-sha256-1','fixture-offsite_copies-detail-1','2026-11-01T12:00:00+00:00');
INSERT INTO "offsite_copies" VALUES(2,'fixture-offsite_copies-bundle_name-2','fixture-offsite_copies-target_kind-2','fixture-offsite_copies-target-2','failed',2,'fixture-offsite_copies-sha256-2','fixture-offsite_copies-detail-2','2026-11-02T12:00:00+00:00');
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
INSERT INTO "optimization_items" VALUES(1,1,1,'fixture-optimization_items-instance_id-1','fixture-optimization_items-media_kind-1','fixture-optimization_items-provider_id-1','fixture-optimization_items-title-1','/data/media/fixture/optimization_items/path-1.mkv',1,'fixture-optimization_items-action-1','fixture-optimization_items-bucket-1','fixture-optimization_items-reason_nl-1',1,1,'{}','fixture-optimization_items-status-1','{}','2026-11-01T12:00:00+00:00','2026-11-01T12:00:00+00:00');
INSERT INTO "optimization_items" VALUES(2,2,2,'fixture-optimization_items-instance_id-2','fixture-optimization_items-media_kind-2','fixture-optimization_items-provider_id-2','fixture-optimization_items-title-2','/data/media/fixture/optimization_items/path-2.mkv',2,'fixture-optimization_items-action-2','fixture-optimization_items-bucket-2','fixture-optimization_items-reason_nl-2',2,2,'{}','fixture-optimization_items-status-2','{}','2026-11-02T12:00:00+00:00','2026-11-02T12:00:00+00:00');
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
INSERT INTO "optimization_plans" VALUES(1,'completed','{}','{}','{}',1,1,'fixture-optimization_plans-confirmed_by-1','2026-11-01T12:00:00+00:00','2026-11-01T12:00:00+00:00','fixture-optimization_plans-created_by-1','2026-11-01T12:00:00+00:00');
INSERT INTO "optimization_plans" VALUES(2,'cancelled','{}','{}','{}',2,2,'fixture-optimization_plans-confirmed_by-2','2026-11-02T12:00:00+00:00','2026-11-02T12:00:00+00:00','fixture-optimization_plans-created_by-2','2026-11-02T12:00:00+00:00');
CREATE TABLE outbox_events (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    topic TEXT NOT NULL,
    instance_id TEXT,
    payload TEXT NOT NULL,
    delivered INTEGER NOT NULL DEFAULT 0
);
INSERT INTO "outbox_events" VALUES(1,'2026-11-01T12:00:00+00:00','fixture-outbox_events-topic-1','fixture-outbox_events-instance_id-1','{}',1);
INSERT INTO "outbox_events" VALUES(2,'2026-11-02T12:00:00+00:00','fixture-outbox_events-topic-2','fixture-outbox_events-instance_id-2','{}',1);
CREATE TABLE policy_profile_history (
    id INTEGER PRIMARY KEY,
    profile_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    config_json TEXT NOT NULL,
    changed_at TEXT NOT NULL,
    changed_by TEXT
);
INSERT INTO "policy_profile_history" VALUES(1,'fixture-policy_profile_history-profile_id-1',1,'{}','2026-11-01T12:00:00+00:00','fixture-policy_profile_history-changed_by-1');
INSERT INTO "policy_profile_history" VALUES(2,'fixture-policy_profile_history-profile_id-2',2,'{}','2026-11-02T12:00:00+00:00','fixture-policy_profile_history-changed_by-2');
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
INSERT INTO "policy_profiles" VALUES('anime-dual-first','Anime: Dual Audio eerst','Dual Audio (EN+JA) > Engels > Japans (fallback, blijft zoeken). Audio weegt zwaarder dan resolutie.',1,'{"audio": {"anime_preference": "dual_first", "mode": "anime", "nl_audio": "off", "nl_subtitles_wanted": true, "subtitle_preference": ["nl", "en"]}, "blocked_groups": [], "max_resolution": null, "max_size_gb": null, "min_custom_format_score": null, "min_resolution": null, "prefer_hdr": false, "prefer_smaller": false, "preferred_groups": [], "ranking_order": ["audio", "source", "resolution", "custom_format", "group"], "reject_false_audio_claims": true, "trash_score_set": "default"}',1,'2026-11-10T07:30:01+00:00','system');
INSERT INTO "policy_profiles" VALUES('standaard','Standaard: originele audio + NL ondertitels','Originele audio behouden, Nederlandse ondertitels gewenst.',1,'{"audio": {"anime_preference": "dual_first", "mode": "original", "nl_audio": "off", "nl_subtitles_wanted": true, "subtitle_preference": ["nl", "en"]}, "blocked_groups": [], "max_resolution": null, "max_size_gb": null, "min_custom_format_score": null, "min_resolution": null, "prefer_hdr": false, "prefer_smaller": false, "preferred_groups": [], "ranking_order": ["audio", "source", "resolution", "custom_format", "group"], "reject_false_audio_claims": true, "trash_score_set": "default"}',1,'2026-11-10T07:30:01+00:00','system');
INSERT INTO "policy_profiles" VALUES('films-4k','Films 4K','Resolutie eerst (2160p), daarna HDR en bron. Originele audio.',1,'{"audio": {"anime_preference": "dual_first", "mode": "original", "nl_audio": "off", "nl_subtitles_wanted": true, "subtitle_preference": ["nl", "en"]}, "blocked_groups": [], "max_resolution": null, "max_size_gb": null, "min_custom_format_score": null, "min_resolution": null, "prefer_hdr": true, "prefer_smaller": false, "preferred_groups": [], "ranking_order": ["resolution", "hdr", "source", "audio", "custom_format", "group"], "reject_false_audio_claims": true, "trash_score_set": "default"}',1,'2026-11-10T07:30:01+00:00','system');
INSERT INTO "policy_profiles" VALUES('nl-audio-subs','Nederlandse audio/ondertitels','Nederlands gesproken heeft voorkeur, anders origineel met NL subs.',1,'{"audio": {"anime_preference": "dual_first", "mode": "original", "nl_audio": "preferred", "nl_subtitles_wanted": true, "subtitle_preference": ["nl", "en"]}, "blocked_groups": [], "max_resolution": null, "max_size_gb": null, "min_custom_format_score": null, "min_resolution": null, "prefer_hdr": false, "prefer_smaller": false, "preferred_groups": [], "ranking_order": ["audio", "source", "resolution", "custom_format", "group"], "reject_false_audio_claims": true, "trash_score_set": "default"}',1,'2026-11-10T07:30:01+00:00','system');
INSERT INTO "policy_profiles" VALUES('kids-nl-audio','Kinderen: Nederlandse audio verplicht','Alleen releases met Nederlands gesproken audio.',1,'{"audio": {"anime_preference": "dual_first", "mode": "original", "nl_audio": "required", "nl_subtitles_wanted": false, "subtitle_preference": ["nl", "en"]}, "blocked_groups": [], "max_resolution": null, "max_size_gb": null, "min_custom_format_score": null, "min_resolution": null, "prefer_hdr": false, "prefer_smaller": false, "preferred_groups": [], "ranking_order": ["audio", "source", "resolution", "custom_format", "group"], "reject_false_audio_claims": true, "trash_score_set": "default"}',1,'2026-11-10T07:30:01+00:00','system');
INSERT INTO "policy_profiles" VALUES('hdr-dv','HDR / Dolby Vision','HDR/DV-releases eerst; DV-conversie volgt het aparte DV-beleid.',1,'{"audio": {"anime_preference": "dual_first", "mode": "original", "nl_audio": "off", "nl_subtitles_wanted": true, "subtitle_preference": ["nl", "en"]}, "blocked_groups": [], "max_resolution": null, "max_size_gb": null, "min_custom_format_score": null, "min_resolution": null, "prefer_hdr": true, "prefer_smaller": false, "preferred_groups": [], "ranking_order": ["hdr", "resolution", "source", "audio", "custom_format", "group"], "reject_false_audio_claims": true, "trash_score_set": "default"}',1,'2026-11-10T07:30:01+00:00','system');
INSERT INTO "policy_profiles" VALUES('opslag','Opslagbewust','Maximaal 1080p, kleinere releases bij gelijke kwaliteit.',1,'{"audio": {"anime_preference": "dual_first", "mode": "original", "nl_audio": "off", "nl_subtitles_wanted": true, "subtitle_preference": ["nl", "en"]}, "blocked_groups": [], "max_resolution": 1080, "max_size_gb": null, "min_custom_format_score": null, "min_resolution": null, "prefer_hdr": false, "prefer_smaller": true, "preferred_groups": [], "ranking_order": ["audio", "resolution", "source", "size", "custom_format", "group"], "reject_false_audio_claims": true, "trash_score_set": "default"}',1,'2026-11-10T07:30:01+00:00','system');
INSERT INTO "policy_profiles" VALUES('fixture-policy_profiles-id-8','fixture-policy_profiles-name_nl-8','fixture-policy_profiles-description_nl-8',8,'{}',8,'2026-11-08T12:00:00+00:00','fixture-policy_profiles-updated_by-8');
INSERT INTO "policy_profiles" VALUES('fixture-policy_profiles-id-9','fixture-policy_profiles-name_nl-9','fixture-policy_profiles-description_nl-9',9,'{}',9,'2026-11-01T12:00:00+00:00','fixture-policy_profiles-updated_by-9');
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
INSERT INTO "post_import_analysis" VALUES(1,'fixture-post_import_analysis-instance_id-1','fixture-post_import_analysis-kind-1','fixture-post_import_analysis-provider_id-1','/data/media/fixture/post_import_analysis/file_path-1.mkv','fixture-post_import_analysis-identity_status-1','{}','{}',1,'fixture-post_import_analysis-reason_nl-1','2026-11-01T12:00:00+00:00');
INSERT INTO "post_import_analysis" VALUES(2,'fixture-post_import_analysis-instance_id-2','fixture-post_import_analysis-kind-2','fixture-post_import_analysis-provider_id-2','/data/media/fixture/post_import_analysis/file_path-2.mkv','fixture-post_import_analysis-identity_status-2','{}','{}',2,'fixture-post_import_analysis-reason_nl-2','2026-11-02T12:00:00+00:00');
CREATE TABLE provisioning_config (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    updated_by TEXT
);
INSERT INTO "provisioning_config" VALUES('fixture-provisioning_config-key-1','{}','2026-11-01T12:00:00+00:00','fixture-provisioning_config-updated_by-1');
INSERT INTO "provisioning_config" VALUES('fixture-provisioning_config-key-2','{}','2026-11-02T12:00:00+00:00','fixture-provisioning_config-updated_by-2');
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
INSERT INTO "provisioning_items" VALUES('fixture-provisioning_items-instance_id-1','fixture-provisioning_items-section-1','fixture-provisioning_items-item_key-1',1,'mediaguard','fixture-provisioning_items-secret_hash-1','2026-11-01T12:00:00+00:00','2026-11-01T12:00:00+00:00','fixture-provisioning_items-updated_by-1');
INSERT INTO "provisioning_items" VALUES('fixture-provisioning_items-instance_id-2','fixture-provisioning_items-section-2','fixture-provisioning_items-item_key-2',2,'user','fixture-provisioning_items-secret_hash-2','2026-11-02T12:00:00+00:00','2026-11-02T12:00:00+00:00','fixture-provisioning_items-updated_by-2');
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
INSERT INTO "provisioning_optimize" VALUES('fixture-provisioning_optimize-instance_id-1','optimized',1,'{}','{}','fixture-provisioning_optimize-error-1','2026-11-01T12:00:00+00:00','2026-11-01T12:00:00+00:00','2026-11-01T12:00:00+00:00','2026-11-01T12:00:00+00:00','fixture-provisioning_optimize-updated_by-1');
INSERT INTO "provisioning_optimize" VALUES('fixture-provisioning_optimize-instance_id-2','cleaned',2,'{}','{}','fixture-provisioning_optimize-error-2','2026-11-02T12:00:00+00:00','2026-11-02T12:00:00+00:00','2026-11-02T12:00:00+00:00','2026-11-02T12:00:00+00:00','fixture-provisioning_optimize-updated_by-2');
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
INSERT INTO "provisioning_runs" VALUES(1,'plan','fixture-provisioning_runs-actor-1','succeeded','[]','[]','{}','[]','2026-11-01T12:00:00+00:00','2026-11-01T12:00:00+00:00');
INSERT INTO "provisioning_runs" VALUES(2,'apply','fixture-provisioning_runs-actor-2','partial','[]','[]','{}','[]','2026-11-02T12:00:00+00:00','2026-11-02T12:00:00+00:00');
CREATE TABLE provisioning_snapshots (
    id INTEGER PRIMARY KEY,
    instance_id TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'pre_optimize',
    data_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    created_by TEXT
);
INSERT INTO "provisioning_snapshots" VALUES(1,'fixture-provisioning_snapshots-instance_id-1','fixture-provisioning_snapshots-kind-1','{}','2026-11-01T12:00:00+00:00','fixture-provisioning_snapshots-created_by-1');
INSERT INTO "provisioning_snapshots" VALUES(2,'fixture-provisioning_snapshots-instance_id-2','fixture-provisioning_snapshots-kind-2','{}','2026-11-02T12:00:00+00:00','fixture-provisioning_snapshots-created_by-2');
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
INSERT INTO "provisioning_status" VALUES('fixture-provisioning_status-instance_id-1','fixture-provisioning_status-section-1','fixture-provisioning_status-status-1','fixture-provisioning_status-message_nl-1','{}','[]','2026-11-01T12:00:00+00:00');
INSERT INTO "provisioning_status" VALUES('fixture-provisioning_status-instance_id-2','fixture-provisioning_status-section-2','fixture-provisioning_status-status-2','fixture-provisioning_status-message_nl-2','{}','[]','2026-11-02T12:00:00+00:00');
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
INSERT INTO "quarantine_items" VALUES(1,1,'fixture-quarantine_items-instance_id-1','fixture-quarantine_items-media_kind-1','fixture-quarantine_items-provider_id-1','/data/media/fixture/quarantine_items/original_path-1.mkv','/data/media/fixture/quarantine_items/quarantine_path-1.mkv',1,'fixture-quarantine_items-sha256-1',1,1,'fixture-quarantine_items-method-1','restored','fixture-quarantine_items-reason-1','2026-11-01T12:00:00+00:00','2026-11-01T12:00:00+00:00','2026-11-01T12:00:00+00:00',1);
INSERT INTO "quarantine_items" VALUES(2,2,'fixture-quarantine_items-instance_id-2','fixture-quarantine_items-media_kind-2','fixture-quarantine_items-provider_id-2','/data/media/fixture/quarantine_items/original_path-2.mkv','/data/media/fixture/quarantine_items/quarantine_path-2.mkv',2,'fixture-quarantine_items-sha256-2',2,2,'fixture-quarantine_items-method-2','discarded','fixture-quarantine_items-reason-2','2026-11-02T12:00:00+00:00','2026-11-02T12:00:00+00:00','2026-11-02T12:00:00+00:00',1);
CREATE TABLE quarantine_release_checks (
    item_id INTEGER PRIMARY KEY REFERENCES quarantine_items(id) ON DELETE CASCADE,
    status TEXT NOT NULL CHECK (status IN ('passed','failed')),
    checks_json TEXT NOT NULL DEFAULT '[]',
    checked_at TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 1
);
INSERT INTO "quarantine_release_checks" VALUES(1,'passed','[]','2026-11-01T12:00:00+00:00',1);
INSERT INTO "quarantine_release_checks" VALUES(2,'failed','[]','2026-11-02T12:00:00+00:00',2);
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
INSERT INTO "release_provenance" VALUES(1,'fixture-release_provenance-instance_id-1','fixture-release_provenance-event_type-1','fixture-release_provenance-download_id-1','fixture-release_provenance-release_title-1','fixture-release_provenance-release_group-1','fixture-release_provenance-quality-1',1,'[]','fixture-release_provenance-indexer-1',1,'[]',1,1,'[]','[]',1,'/data/media/fixture/release_provenance/file_path-1.mkv','fixture-release_provenance-idempotency_key-1','2026-11-01T12:00:00+00:00');
INSERT INTO "release_provenance" VALUES(2,'fixture-release_provenance-instance_id-2','fixture-release_provenance-event_type-2','fixture-release_provenance-download_id-2','fixture-release_provenance-release_title-2','fixture-release_provenance-release_group-2','fixture-release_provenance-quality-2',2,'[]','fixture-release_provenance-indexer-2',2,'[]',2,2,'[]','[]',2,'/data/media/fixture/release_provenance/file_path-2.mkv','fixture-release_provenance-idempotency_key-2','2026-11-02T12:00:00+00:00');
CREATE TABLE replacement_journal (
    id INTEGER PRIMARY KEY,
    transaction_id INTEGER NOT NULL REFERENCES replacement_transactions(id),
    ts TEXT NOT NULL,
    step TEXT NOT NULL,
    phase TEXT NOT NULL,                 -- begin | done | error | recover
    detail_json TEXT NOT NULL DEFAULT '{}'
);
INSERT INTO "replacement_journal" VALUES(1,1,'2026-11-01T12:00:00+00:00','fixture-replacement_journal-step-1','fixture-replacement_journal-phase-1','{}');
INSERT INTO "replacement_journal" VALUES(2,2,'2026-11-02T12:00:00+00:00','fixture-replacement_journal-step-2','fixture-replacement_journal-phase-2','{}');
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
INSERT INTO "replacement_transactions" VALUES(1,'replace','fixture-replacement_transactions-instance_id-1','fixture-replacement_transactions-media_kind-1','fixture-replacement_transactions-provider_id-1','/data/media/fixture/replacement_transactions/library_path-1.mkv','/data/media/fixture/replacement_transactions/final_path-1.mkv','/data/media/fixture/replacement_transactions/staged_path-1.mkv',1,'fixture-replacement_transactions-staged_sha256-1','{}','/data/media/fixture/replacement_transactions/temp_path-1.mkv',1,'/data/media/fixture/replacement_transactions/quarantine_path-1.mkv','fixture-replacement_transactions-quarantine_method-1','fixture-replacement_transactions-original_sha256-1',1,1,1,'committed','fixture-replacement_transactions-error-1','[]','fixture-replacement_transactions-zfs_snapshot-1','{}','fixture-replacement_transactions-correlation_id-1','2026-11-01T12:00:00+00:00','fixture-replacement_transactions-created_by-1','2026-11-01T12:00:00+00:00','2026-11-01T12:00:00+00:00');
INSERT INTO "replacement_transactions" VALUES(2,'restore','fixture-replacement_transactions-instance_id-2','fixture-replacement_transactions-media_kind-2','fixture-replacement_transactions-provider_id-2','/data/media/fixture/replacement_transactions/library_path-2.mkv','/data/media/fixture/replacement_transactions/final_path-2.mkv','/data/media/fixture/replacement_transactions/staged_path-2.mkv',2,'fixture-replacement_transactions-staged_sha256-2','{}','/data/media/fixture/replacement_transactions/temp_path-2.mkv',2,'/data/media/fixture/replacement_transactions/quarantine_path-2.mkv','fixture-replacement_transactions-quarantine_method-2','fixture-replacement_transactions-original_sha256-2',2,2,2,'committed','fixture-replacement_transactions-error-2','[]','fixture-replacement_transactions-zfs_snapshot-2','{}','fixture-replacement_transactions-correlation_id-2','2026-11-02T12:00:00+00:00','fixture-replacement_transactions-created_by-2','2026-11-02T12:00:00+00:00','2026-11-02T12:00:00+00:00');
INSERT INTO "replacement_transactions" VALUES(3,'replace','fixture-replacement_transactions-instance_id-3','fixture-replacement_transactions-media_kind-3','fixture-replacement_transactions-provider_id-3','/data/media/fixture/replacement_transactions/library_path-3.mkv','/data/media/fixture/replacement_transactions/final_path-3.mkv','/data/media/fixture/replacement_transactions/staged_path-3.mkv',3,'fixture-replacement_transactions-staged_sha256-3','{}','/data/media/fixture/replacement_transactions/temp_path-3.mkv',3,'/data/media/fixture/replacement_transactions/quarantine_path-3.mkv','fixture-replacement_transactions-quarantine_method-3','fixture-replacement_transactions-original_sha256-3',3,3,3,'committed','fixture-replacement_transactions-error-3','[]','fixture-replacement_transactions-zfs_snapshot-3','{}','fixture-replacement_transactions-correlation_id-3','2026-11-03T12:00:00+00:00','fixture-replacement_transactions-created_by-3','2026-11-03T12:00:00+00:00','2026-11-03T12:00:00+00:00');
CREATE TABLE schema_migrations(version TEXT PRIMARY KEY, applied_at TEXT NOT NULL);
INSERT INTO "schema_migrations" VALUES('001_core','2026-11-10T07:30:01+00:00');
INSERT INTO "schema_migrations" VALUES('002_jobs_maintenance','2026-11-10T07:30:01+00:00');
INSERT INTO "schema_migrations" VALUES('003_notifications_merge','2026-11-10T07:30:01+00:00');
INSERT INTO "schema_migrations" VALUES('004_api_keys','2026-11-10T07:30:01+00:00');
INSERT INTO "schema_migrations" VALUES('020_instances','2026-11-10T07:30:01+00:00');
INSERT INTO "schema_migrations" VALUES('021_library','2026-11-10T07:30:01+00:00');
INSERT INTO "schema_migrations" VALUES('022_wanted','2026-11-10T07:30:01+00:00');
INSERT INTO "schema_migrations" VALUES('023_seerr','2026-11-10T07:30:01+00:00');
INSERT INTO "schema_migrations" VALUES('024_anime_routing','2026-11-10T07:30:01+00:00');
INSERT INTO "schema_migrations" VALUES('025_provisioning_config','2026-11-10T07:30:01+00:00');
INSERT INTO "schema_migrations" VALUES('026_provisioning_items','2026-11-10T07:30:01+00:00');
INSERT INTO "schema_migrations" VALUES('027_provisioning_runs','2026-11-10T07:30:01+00:00');
INSERT INTO "schema_migrations" VALUES('028_provisioning_optimize','2026-11-10T07:30:01+00:00');
INSERT INTO "schema_migrations" VALUES('029_delivery','2026-11-10T07:30:01+00:00');
INSERT INTO "schema_migrations" VALUES('036_delivery_control','2026-11-10T07:30:01+00:00');
INSERT INTO "schema_migrations" VALUES('040_identity','2026-11-10T07:30:01+00:00');
INSERT INTO "schema_migrations" VALUES('041_policy','2026-11-10T07:30:01+00:00');
INSERT INTO "schema_migrations" VALUES('042_hooks','2026-11-10T07:30:01+00:00');
INSERT INTO "schema_migrations" VALUES('043_dolby_vision','2026-11-10T07:30:01+00:00');
INSERT INTO "schema_migrations" VALUES('060_recovery','2026-11-10T07:30:01+00:00');
INSERT INTO "schema_migrations" VALUES('061_encoding','2026-11-10T07:30:01+00:00');
INSERT INTO "schema_migrations" VALUES('062_optimization','2026-11-10T07:30:01+00:00');
INSERT INTO "schema_migrations" VALUES('063_release_checks','2026-11-10T07:30:01+00:00');
INSERT INTO "schema_migrations" VALUES('064_av1_autopilot','2026-11-10T07:30:01+00:00');
INSERT INTO "schema_migrations" VALUES('065_performance','2026-11-10T07:30:01+00:00');
INSERT INTO "schema_migrations" VALUES('066_identity_engine_version','2026-11-10T07:30:01+00:00');
INSERT INTO "schema_migrations" VALUES('067_optimization_auto_review','2026-11-10T07:30:01+00:00');
INSERT INTO "schema_migrations" VALUES('068_storage_ledger','2026-11-10T07:30:01+00:00');
INSERT INTO "schema_migrations" VALUES('069_upgrade_reasons','2026-11-10T07:30:01+00:00');
INSERT INTO "schema_migrations" VALUES('070_encoding_lane','2026-11-10T07:30:01+00:00');
INSERT INTO "schema_migrations" VALUES('078_download_janitor','2026-11-10T07:30:01+00:00');
INSERT INTO "schema_migrations" VALUES('079_mediaserver','2026-11-10T07:30:01+00:00');
INSERT INTO "schema_migrations" VALUES('080_migration','2026-11-10T07:30:01+00:00');
INSERT INTO "schema_migrations" VALUES('090_notify_security','2026-11-10T07:30:01+00:00');
INSERT INTO "schema_migrations" VALUES('100_ai_advisor','2026-11-10T07:30:01+00:00');
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
, deleted_at TEXT, profile_id INTEGER);
INSERT INTO "seerr_requests" VALUES(1,1,'fixture-seerr_requests-media_type-1',1,1,1,1,1,1,'fixture-seerr_requests-instance_id-1',1,1,'fixture-seerr_requests-title-1','fixture-seerr_requests-requested_by-1','[]','2026-11-01T12:00:00+00:00','2026-11-01T12:00:00+00:00','2026-11-01T12:00:00+00:00','2026-11-01T12:00:00+00:00','fixture-seerr_requests-link_status-1','2026-11-01T12:00:00+00:00',1);
INSERT INTO "seerr_requests" VALUES(2,2,'fixture-seerr_requests-media_type-2',2,2,2,2,2,2,'fixture-seerr_requests-instance_id-2',2,2,'fixture-seerr_requests-title-2','fixture-seerr_requests-requested_by-2','[]','2026-11-02T12:00:00+00:00','2026-11-02T12:00:00+00:00','2026-11-02T12:00:00+00:00','2026-11-02T12:00:00+00:00','fixture-seerr_requests-link_status-2','2026-11-02T12:00:00+00:00',2);
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
INSERT INTO "settings_values" VALUES('global','','backup.retention_count','21','2026-11-10T07:30:01+00:00','user:rooktest');
INSERT INTO "settings_values" VALUES('global','','ui.theme','"light"','2026-11-10T07:30:01+00:00','user:rooktest');
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
INSERT INTO "source_backups" VALUES(1,'fixture-source_instances-id-1','existing','fixture-source_backups-backup_name-1','/data/media/fixture/source_backups/path-1.mkv',1,'fixture-source_backups-sha256-1','fixture-source_backups-app-1','fixture-source_backups-app_version-1',1,1,'{}','2026-11-01T12:00:00+00:00','fixture-source_backups-created_by-1');
INSERT INTO "source_backups" VALUES(2,'fixture-source_instances-id-2','created','fixture-source_backups-backup_name-2','/data/media/fixture/source_backups/path-2.mkv',2,'fixture-source_backups-sha256-2','fixture-source_backups-app-2','fixture-source_backups-app_version-2',2,2,'{}','2026-11-02T12:00:00+00:00','fixture-source_backups-created_by-2');
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
INSERT INTO "source_instances" VALUES('fixture-source_instances-id-1','live','sonarr','fixture-source_instances-name-1','http://fixture.invalid','fixture-source_instances-version-1','fixture-source_instances-app_name-1','fixture-source_instances-instance_name-1','fixture-source_instances-database_type-1','fixture-source_instances-status-1','fixture-source_instances-last_error-1','{}',1,'2026-11-01T12:00:00+00:00','fixture-source_instances-created_by-1','2026-11-01T12:00:00+00:00');
INSERT INTO "source_instances" VALUES('fixture-source_instances-id-2','backup','radarr','fixture-source_instances-name-2','http://fixture.invalid','fixture-source_instances-version-2','fixture-source_instances-app_name-2','fixture-source_instances-instance_name-2','fixture-source_instances-database_type-2','fixture-source_instances-status-2','fixture-source_instances-last_error-2','{}',1,'2026-11-02T12:00:00+00:00','fixture-source_instances-created_by-2','2026-11-02T12:00:00+00:00');
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
INSERT INTO "storage_ledger_events" VALUES(1,'fixture-storage_ledger_events-instance_id-1',1,'fixture-storage_ledger_events-at-1','grabbed','episode',1,1,1,'fixture-storage_ledger_events-quality-1','fixture-storage_ledger_events-release_group-1','fixture-storage_ledger_events-indexer-1','fixture-storage_ledger_events-download_id-1','fixture-storage_ledger_events-source_title-1','fixture-storage_ledger_events-reason-1',1,'{}',0);
INSERT INTO "storage_ledger_events" VALUES(2,'fixture-storage_ledger_events-instance_id-2',2,'fixture-storage_ledger_events-at-2','imported','movie',2,2,2,'fixture-storage_ledger_events-quality-2','fixture-storage_ledger_events-release_group-2','fixture-storage_ledger_events-indexer-2','fixture-storage_ledger_events-download_id-2','fixture-storage_ledger_events-source_title-2','fixture-storage_ledger_events-reason-2',2,'{}',0);
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
INSERT INTO "stuck_import_actions" VALUES(1,'fixture-stuck_import_actions-instance_id-1',1,'fixture-stuck_import_actions-download_id-1','fixture-stuck_import_actions-title-1','fixture-stuck_import_actions-reason-1','2026-11-01T12:00:00+00:00','fixture-stuck_import_actions-action-1','2026-11-01T12:00:00+00:00','fixture-stuck_import_actions-detail-1');
INSERT INTO "stuck_import_actions" VALUES(2,'fixture-stuck_import_actions-instance_id-2',2,'fixture-stuck_import_actions-download_id-2','fixture-stuck_import_actions-title-2','fixture-stuck_import_actions-reason-2','2026-11-02T12:00:00+00:00','fixture-stuck_import_actions-action-2','2026-11-02T12:00:00+00:00','fixture-stuck_import_actions-detail-2');
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
INSERT INTO "subtitle_downloads" VALUES(1,'2026-11-01T12:00:00+00:00','/data/media/fixture/subtitle_downloads/video_path-1.mkv','/data/media/fixture/subtitle_downloads/srt_path-1.mkv','fixture-subtitle_downloads-provider-1','fixture-subtitle_downloads-file_id-1',1,'fixture-subtitle_downloads-status-1','fixture-subtitle_downloads-detail_nl-1');
INSERT INTO "subtitle_downloads" VALUES(2,'2026-11-02T12:00:00+00:00','/data/media/fixture/subtitle_downloads/video_path-2.mkv','/data/media/fixture/subtitle_downloads/srt_path-2.mkv','fixture-subtitle_downloads-provider-2','fixture-subtitle_downloads-file_id-2',2,'fixture-subtitle_downloads-status-2','fixture-subtitle_downloads-detail_nl-2');
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
INSERT INTO "subtitle_status" VALUES('fixture-subtitle_status-key-1','fixture-subtitle_status-instance_id-1','fixture-subtitle_status-media_kind-1',1,'fixture-subtitle_status-title-1',1,'fixture-subtitle_status-source-1','2026-11-01T12:00:00+00:00','2026-11-01T12:00:00+00:00');
INSERT INTO "subtitle_status" VALUES('fixture-subtitle_status-key-2','fixture-subtitle_status-instance_id-2','fixture-subtitle_status-media_kind-2',2,'fixture-subtitle_status-title-2',2,'fixture-subtitle_status-source-2','2026-11-02T12:00:00+00:00','2026-11-02T12:00:00+00:00');
CREATE TABLE system_state (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
INSERT INTO "system_state" VALUES('safe_track_defaults_r2','{"at": "2026-11-10T07:30:01+00:00", "flipped": []}','2026-11-10T07:30:01+00:00');
INSERT INTO "system_state" VALUES('mediaserver_started_at','"2026-11-10T07:30:01+00:00"','2026-11-10T07:30:01+00:00');
INSERT INTO "system_state" VALUES('notify.default_created','true','2026-11-10T07:30:01+00:00');
INSERT INTO "system_state" VALUES('app.version','{"first_seen": "2026-11-10T07:30:01+00:00", "fresh_install": true, "previous": null, "version": "1.2.2"}','2026-11-10T07:30:01+00:00');
INSERT INTO "system_state" VALUES('app.version_history','[{"at": "2026-11-10T07:30:01+00:00", "version": "1.2.2"}]','2026-11-10T07:30:01+00:00');
INSERT INTO "system_state" VALUES('behaviour_notices_sent','{"1.1.0:quarantine.purge_target_percent": "n.v.t.", "1.2.0:encoder.anime_route": "n.v.t.", "1.2.0:encoder.max_cpu_uren": "n.v.t."}','2026-11-10T07:30:01+00:00');
INSERT INTO "system_state" VALUES('fixture-system_state-key-7','{}','2026-11-07T12:00:00+00:00');
INSERT INTO "system_state" VALUES('fixture-system_state-key-8','{}','2026-11-08T12:00:00+00:00');
INSERT INTO "system_state" VALUES('migration_aftercare:1','{"audit_verdict": "go", "audited": true, "pruned": true, "removed_indexers": []}','2026-11-08T12:00:00+00:00');
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
INSERT INTO "trash_custom_formats" VALUES(1,'fixture-trash_custom_formats-trash_id-1','fixture-trash_custom_formats-name-1','{}','{}','fixture-trash_custom_formats-source-1','2026-11-01T12:00:00+00:00','fixture-trash_custom_formats-imported_by-1');
INSERT INTO "trash_custom_formats" VALUES(2,'fixture-trash_custom_formats-trash_id-2','fixture-trash_custom_formats-name-2','{}','{}','fixture-trash_custom_formats-source-2','2026-11-02T12:00:00+00:00','fixture-trash_custom_formats-imported_by-2');
CREATE TABLE user_preferences (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    key TEXT NOT NULL,
    value_json TEXT NOT NULL,
    PRIMARY KEY (user_id, key)
);
INSERT INTO "user_preferences" VALUES(1,'fixture-user_preferences-key-1','{}');
INSERT INTO "user_preferences" VALUES(2,'fixture-user_preferences-key-2','{}');
CREATE TABLE user_totp (
    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    enabled INTEGER NOT NULL DEFAULT 0,
    recovery_hashes_json TEXT NOT NULL DEFAULT '[]',
    last_counter INTEGER,
    created_at TEXT NOT NULL,
    enabled_at TEXT
);
INSERT INTO "user_totp" VALUES(1,0,'[]',1,'2026-11-01T12:00:00+00:00','2026-11-01T12:00:00+00:00');
INSERT INTO "user_totp" VALUES(2,0,'[]',2,'2026-11-02T12:00:00+00:00','2026-11-02T12:00:00+00:00');
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
INSERT INTO "users" VALUES(1,'rooktest','scrypt$h0iP3N4jiCQXTQJBRBAflA==$lKUo6ebYWMTWoEnM5/9cPIaz+j1v5XoQhBEFtfQHlEY=','admin','nl-NL',0,'2026-11-10T07:30:01+00:00','2026-11-10T07:30:01+00:00');
INSERT INTO "users" VALUES(2,'kijker','scrypt$MSPznqKWkWZtK0Rw6rpBWQ==$5+2MHJvPOhzRxdmIlCc73w2CYhyIxE5xKhi8S5V7/BE=','viewer','nl-NL',0,'2026-11-10T07:30:01+00:00',NULL);
INSERT INTO "users" VALUES(3,'fixture-users-username-3','fixture-geen-wachtwoord','viewer','fixture-users-locale-3',3,'2026-11-03T12:00:00+00:00','2026-11-03T12:00:00+00:00');
INSERT INTO "users" VALUES(4,'fixture-users-username-4','fixture-geen-wachtwoord','operator','fixture-users-locale-4',4,'2026-11-04T12:00:00+00:00','2026-11-04T12:00:00+00:00');
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
INSERT INTO "wanted_items" VALUES(1,'fixture-wanted_items-instance_id-1','episode',1,1,'missing','fixture-wanted_items-title-1','fixture-wanted_items-item_title-1',1,1,1,'2026-11-01T12:00:00+00:00','resolved',1,1,'2026-11-01T12:00:00+00:00',NULL,'fixture-wanted_items-last_outcome-1','fixture-wanted_items-last_reason_code-1',0,'fixture-wanted_items-ignored_reason-1','2026-11-01T12:00:00+00:00','2026-11-01T12:00:00+00:00','2026-11-01T12:00:00+00:00','fixture-wanted_items-resolution-1');
INSERT INTO "wanted_items" VALUES(2,'fixture-wanted_items-instance_id-2','movie',2,2,'cutoff','fixture-wanted_items-title-2','fixture-wanted_items-item_title-2',2,2,2,'2026-11-02T12:00:00+00:00','ignored',2,2,'2026-11-02T12:00:00+00:00',NULL,'fixture-wanted_items-last_outcome-2','fixture-wanted_items-last_reason_code-2',0,'fixture-wanted_items-ignored_reason-2','2026-11-02T12:00:00+00:00','2026-11-02T12:00:00+00:00','2026-11-02T12:00:00+00:00','fixture-wanted_items-resolution-2');
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
INSERT INTO "wanted_overrides" VALUES(1,1,'fixture-wanted_overrides-instance_id-1','fixture-wanted_overrides-kind-1','{}','fixture-wanted_overrides-explanation_nl-1','fixture-wanted_overrides-created_by-1','2026-11-01T12:00:00+00:00','2026-11-01T12:00:00+00:00',1,'2026-11-01T12:00:00+00:00','{}');
INSERT INTO "wanted_overrides" VALUES(2,2,'fixture-wanted_overrides-instance_id-2','fixture-wanted_overrides-kind-2','{}','fixture-wanted_overrides-explanation_nl-2','fixture-wanted_overrides-created_by-2','2026-11-02T12:00:00+00:00','2026-11-02T12:00:00+00:00',2,'2026-11-02T12:00:00+00:00','{}');
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
CREATE INDEX idx_delivery_searches ON delivery_searches(instance_id, ts);
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
CREATE INDEX idx_ej_lane_status ON encoding_jobs(lane, status);
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
INSERT INTO "sqlite_sequence" VALUES('api_keys',3);
COMMIT;
