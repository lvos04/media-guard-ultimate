-- Automatische inrichting: centrale gewenste staat (één JSON-document per sectie).
-- Geheimen (downloadclient-wachtwoorden, indexer-/Prowlarr-sleutels) staan NIET hier maar
-- versleuteld in 'secrets' onder downloadclient:<naam>:*, indexer:<naam>:apikey,
-- prowlarr:apikey.
CREATE TABLE provisioning_config (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    updated_by TEXT
);
