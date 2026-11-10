-- Gebruikersrapport (zondag): anime op de GPU werd de helft van de keren afgekeurd op VMAF.
-- route_leer onthoudt per serie (map) hoe vaak de GPU de kwaliteit niet haalde, en per
-- 'bronband' (codec + resolutie + bitrateband) hoe vaak AV1 geen winst gaf, zodat
-- MediaGuard de route zelf wisselt in plaats van steeds dezelfde fout te herhalen.
CREATE TABLE IF NOT EXISTS route_leer (
    sleutel TEXT PRIMARY KEY,
    soort TEXT NOT NULL,          -- 'gpu_vmaf' | 'geen_winst'
    aantal INTEGER NOT NULL DEFAULT 0,
    eerste TEXT,
    laatste TEXT,
    reden TEXT
);
-- Lange CPU-jobs die overdag plaatsmaakten voor kortere: alleen nog 's nachts starten.
ALTER TABLE job ADD COLUMN alleen_nacht INTEGER NOT NULL DEFAULT 0;
-- Job is van route gewisseld (GPU → CPU) na een kwaliteitsmeting: geen fout.
ALTER TABLE job ADD COLUMN omgeleid INTEGER NOT NULL DEFAULT 0;
