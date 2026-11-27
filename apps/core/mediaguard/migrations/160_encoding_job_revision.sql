-- Ook twee metingen binnen dezelfde seconde zijn afzonderlijke taakversies.
ALTER TABLE encoding_jobs ADD COLUMN revision INTEGER NOT NULL DEFAULT 0;

CREATE TRIGGER encoding_job_revision AFTER UPDATE ON encoding_jobs
WHEN NEW.revision = OLD.revision
BEGIN
    UPDATE encoding_jobs SET revision = OLD.revision + 1 WHERE id = NEW.id;
END;
