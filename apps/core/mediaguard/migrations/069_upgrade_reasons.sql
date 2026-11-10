-- Waarom werd er geüpgraded? Custom-format-score en -namen per historie-record, zodat
-- oud ↔ nieuw vergeleken kan worden (bibliotheek → Upgrades, opslagoverzicht 'waarom').
ALTER TABLE storage_ledger_events ADD COLUMN cf_score INTEGER;
ALTER TABLE storage_ledger_events ADD COLUMN custom_formats_json TEXT;
ALTER TABLE storage_ledger_events ADD COLUMN is_repack INTEGER NOT NULL DEFAULT 0;
