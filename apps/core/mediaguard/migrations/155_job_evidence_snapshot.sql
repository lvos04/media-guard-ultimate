-- Bewaar de bij de upgrade zichtbare jobtoestand. De runner kan dezelfde job
-- hervatten zonder het bestaande bewijs te overschrijven. Invoer en aanmaaktijd
-- staan al onveranderlijk op de job. Alleen toegestane resultaattellers kopiëren.
CREATE TABLE job_evidence_snapshot (
    job_id INTEGER PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
    status TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    result TEXT,
    audit_cutoff INTEGER NOT NULL
);

WITH source AS (
    SELECT id, status, started_at, finished_at,
           CASE WHEN job_type = 'library.sync' AND json_valid(result) THEN result END AS counts
    FROM jobs
)
INSERT INTO job_evidence_snapshot(job_id, status, started_at, finished_at, result, audit_cutoff)
SELECT id, status, started_at, finished_at,
       CASE WHEN counts IS NOT NULL THEN json_object(
           'episodes', CASE WHEN json_type(counts, '$.episodes') = 'integer' THEN counts ->> '$.episodes' END,
           'movies', CASE WHEN json_type(counts, '$.movies') = 'integer' THEN counts ->> '$.movies' END,
           'failed_items', CASE WHEN json_type(counts, '$.failed_items') = 'integer' THEN counts ->> '$.failed_items' END
       ) END,
       (SELECT COALESCE(MAX(id), 0) FROM audit_events)
FROM source;
