-- AV1-banen (verbeterpunt 6): per job de baan (cpu/gpu) waarop de encoder hem uitvoert. Core
-- begrenst de wachtrij per baan, zodat CPU-werk (Dolby Vision, HDR-herkansingen) de GPU niet
-- meer uithongert. NULL = nog onbekend; de eerstvolgende statuspoll vult hem in.
ALTER TABLE encoding_jobs ADD COLUMN lane TEXT;
CREATE INDEX idx_ej_lane_status ON encoding_jobs(lane, status);
