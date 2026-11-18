-- Eén rij per bestand en scan: de bijdrage van dat bestand aan de scantelling.
-- Alleen toevoegen; oudere code negeert de tabel. De telling is nooit lager dan de som.
CREATE TABLE IF NOT EXISTS scan_bijdrage (
    scan_id INTEGER NOT NULL REFERENCES scan_run(id) ON DELETE CASCADE,
    bestand_id INTEGER NOT NULL REFERENCES bestand(id) ON DELETE CASCADE,
    nieuw INTEGER NOT NULL DEFAULT 0,
    gewijzigd INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (scan_id, bestand_id)
);
