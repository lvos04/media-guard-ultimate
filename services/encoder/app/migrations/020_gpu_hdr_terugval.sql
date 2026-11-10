-- HDR10 via de GPU: als metadata na de GPU-encode niet klopt (of terugzetten faalt) gaat
-- dezelfde job één keer terug naar de CPU. Deze vlag voorkomt een lus en houdt de
-- baan-herverdeling weg bij zo'n job.
ALTER TABLE job ADD COLUMN gpu_hdr_teruggevallen INTEGER NOT NULL DEFAULT 0;
