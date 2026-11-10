-- Opgeloste/genegeerde fouten tellen niet meer mee (badge, 'Actie nodig'); automatische
-- herkansing van tijdelijke fouten; indexen voor logs en events (onderhoud/retentie).
ALTER TABLE jobs ADD COLUMN resolved_at TEXT;
ALTER TABLE jobs ADD COLUMN resolved_reason TEXT;
ALTER TABLE jobs ADD COLUMN auto_retries INTEGER NOT NULL DEFAULT 0;
CREATE INDEX idx_jobs_failed ON jobs(status, resolved_at, finished_at);
CREATE INDEX idx_jobs_type_target ON jobs(job_type, instance_id, target_id, status);
CREATE INDEX idx_job_logs_job ON job_logs(job_id, id);
CREATE INDEX idx_outbox_ts ON outbox_events(ts);
CREATE INDEX idx_notifications_read ON notifications(read, severity);
