-- Policyprofielen (MediaGuard), TRaSH custom formats, onjuiste audioclaims.

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

CREATE TABLE policy_profile_history (
    id INTEGER PRIMARY KEY,
    profile_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    config_json TEXT NOT NULL,
    changed_at TEXT NOT NULL,
    changed_by TEXT
);

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

-- 'Dual Audio' in de naam maar feitelijk alleen Japans: leer ervan (geen zoekloops).
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
