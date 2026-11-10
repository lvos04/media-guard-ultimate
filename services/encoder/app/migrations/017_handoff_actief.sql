-- Handoff (MediaGuard): een gestaged resultaat dat op de commit van core wacht
-- (wacht_op_commit) bewaakt het bestand net zo goed als een lopende job. Een
-- tweede job voor hetzelfde bestand zou anders een nieuwe encode starten terwijl
-- core het eerste resultaat nog activeert.
-- Migraties draaien bij opstart, vóór de planner: 'bezig'/'verifieren' heeft dan
-- geen levend proces meer en zou door weesherstel toch opnieuw gaan wachten.
UPDATE job
SET status='overgeslagen',
    fout='dubbele job veilig overgeslagen; een gestaged resultaat wacht op MediaGuard',
    foutsoort='DuplicateJob',
    proces_pid=NULL,
    worker_id=NULL,
    cache_reservering=0,
    klaar_op=CURRENT_TIMESTAMP
WHERE status IN ('wacht','bezig','verifieren')
  AND EXISTS (
      SELECT 1 FROM job AS gestaged
      WHERE gestaged.bestand_id=job.bestand_id
        AND gestaged.status='wacht_op_commit'
  );

-- Meerdere gestagede resultaten voor één bestand: alleen de oudste blijft; de
-- stagingmap van de rest ruimt het stagingonderhoud op.
UPDATE job
SET status='overgeslagen',
    fout='dubbel gestaged resultaat veilig overgeslagen; oudste blijft behouden',
    foutsoort='DuplicateJob',
    mg_staged_pad=NULL,
    klaar_op=CURRENT_TIMESTAMP
WHERE status='wacht_op_commit'
  AND id NOT IN (
      SELECT MIN(oudste.id) FROM job AS oudste
      WHERE oudste.status='wacht_op_commit'
      GROUP BY oudste.bestand_id
  );

DROP INDEX IF EXISTS idx_job_een_actief_per_bestand;
CREATE UNIQUE INDEX idx_job_een_actief_per_bestand
ON job(bestand_id)
WHERE status IN ('wacht','bezig','verifieren','wacht_op_commit');
