-- Dolby Vision (DEEL Q): policy-versies, proefconversies, bewijs, afspeelbevestigingen.
-- De scoped waarden zelf staan in settings_values (dv.*); hier de versiegeschiedenis.

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
