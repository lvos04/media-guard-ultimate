-- Leveringsgarantie, tweede ronde (verbeterlijst 7, 11, 12, 36).
-- Stoppen per aanvraag, Seerr hooguit een paar keer opnieuw laten proberen, eisen maar één
-- keer afdwingen (nooit een profiel overschrijven dat de eigenaar later zelf koos) en
-- zoekacties van de levering laten meetellen in het zoekquotum.

ALTER TABLE delivery_items ADD COLUMN stopped_at TEXT;
-- stop_code: user | seerr_deleted | removed; located_at: eerste keer gevonden in Sonarr/Radarr
ALTER TABLE delivery_items ADD COLUMN stop_code TEXT;
ALTER TABLE delivery_items ADD COLUMN stop_reason_nl TEXT;
ALTER TABLE delivery_items ADD COLUMN located_at TEXT;
ALTER TABLE delivery_items ADD COLUMN seerr_retries INTEGER NOT NULL DEFAULT 0;
ALTER TABLE delivery_items ADD COLUMN seerr_sync_attempts INTEGER NOT NULL DEFAULT 0;
ALTER TABLE delivery_items ADD COLUMN profile_checked_at TEXT;
ALTER TABLE delivery_items ADD COLUMN requirements_checked_at TEXT;

-- bestaande titels zijn al eens nagelopen: profiel en bewaken niet opnieuw afdwingen
UPDATE delivery_items SET located_at = updated_at WHERE provider_id IS NOT NULL;
UPDATE delivery_items SET profile_checked_at = updated_at, requirements_checked_at = updated_at;

-- deleted_at: niet meer in Seerr; profile_id: in Seerr zelf gekozen kwaliteitsprofiel
ALTER TABLE seerr_requests ADD COLUMN deleted_at TEXT;
ALTER TABLE seerr_requests ADD COLUMN profile_id INTEGER;

CREATE TABLE delivery_searches (
    id INTEGER PRIMARY KEY,
    item_id INTEGER,
    instance_id TEXT NOT NULL,
    ts TEXT NOT NULL,
    command TEXT NOT NULL,
    cost INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX idx_delivery_searches ON delivery_searches(instance_id, ts);
