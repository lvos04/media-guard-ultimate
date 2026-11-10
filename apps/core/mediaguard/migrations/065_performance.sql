-- Prestaties: datumvragen per instantie (kalender, 'komende 7 dagen'), dashboard-filter op
-- mislukte optimalisatie-items, en vastgelopen imports die MediaGuard zelf herstelt.
CREATE INDEX idx_lib_episodes_inst_air ON lib_episodes(instance_id, air_date_utc);
CREATE INDEX idx_oi_status ON optimization_items(status, plan_id);

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
CREATE INDEX idx_stuck_import_seen ON stuck_import_actions(action, first_seen_at);

-- Wijzigingsdetectie bij de bibliotheeksync: alleen series waarvan de statistieken
-- veranderden worden opnieuw opgehaald (plus dagelijks een volledige ronde).
ALTER TABLE lib_items ADD COLUMN sync_fingerprint TEXT;
