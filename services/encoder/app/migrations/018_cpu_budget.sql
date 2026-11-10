-- MediaGuard: maximaal CPU-gebruik voor AV1 (percentage van de beschikbare cores). Core zet
-- deze waarde via de brug (PUT /api/mediaguard/v1/settings/resources). 100 = geen extra
-- begrenzing (oude gedrag).
ALTER TABLE instelling ADD COLUMN av1_cpu_procent INTEGER NOT NULL DEFAULT 100;
