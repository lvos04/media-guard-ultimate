-- Meldingen samenvoegen (rapport 4 nov, M3): gelijke ongelezen meldingen worden één regel
-- met een teller; 'dedupe_key' bepaalt wat gelijk is, 'resolved_at' = vanzelf opgelost.
ALTER TABLE notifications ADD COLUMN count INTEGER NOT NULL DEFAULT 1;
ALTER TABLE notifications ADD COLUMN dedupe_key TEXT;
ALTER TABLE notifications ADD COLUMN resolved_at TEXT;
CREATE INDEX idx_notifications_dedupe ON notifications(dedupe_key, read);
