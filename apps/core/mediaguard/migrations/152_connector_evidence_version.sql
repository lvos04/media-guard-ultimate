-- Historische records behouden een onbekende versie. Nieuwe auditregels en jobs
-- leggen de versie vast bij de bestaande schrijfactie.
ALTER TABLE audit_events ADD COLUMN evidence_app_version TEXT;
ALTER TABLE jobs ADD COLUMN evidence_app_version TEXT;
