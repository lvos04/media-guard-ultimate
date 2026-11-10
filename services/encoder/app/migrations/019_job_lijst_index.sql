-- Snelle joblijst (GET /api/jobs): sorteren op prioriteit en id zonder de hele tabel te
-- sorteren, met en zonder statusfilter.
CREATE INDEX IF NOT EXISTS idx_job_lijst_status ON job(status, prioriteit DESC, id);
CREATE INDEX IF NOT EXISTS idx_job_lijst ON job(prioriteit DESC, id);
