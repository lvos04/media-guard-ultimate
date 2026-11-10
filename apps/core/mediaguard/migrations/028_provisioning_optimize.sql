-- Optimaliseren na migratie: volledige momentopname van de gemigreerde configuratie
-- (providerbodies, profielen, custom formats, naamgeving, mediabeheer, profieltoewijzing)
-- zodat 'Terugzetten naar gemigreerde instellingen' exact kan herstellen. Geheime
-- veldwaarden staan niet in data_json maar in 'secrets' (verwijzing {"$secret": naam}).
CREATE TABLE provisioning_snapshots (
    id INTEGER PRIMARY KEY,
    instance_id TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'pre_optimize',
    data_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    created_by TEXT
);
CREATE INDEX idx_provisioning_snapshots ON provisioning_snapshots(instance_id, id);

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
