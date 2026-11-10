-- Noodstop vanuit MediaGuard (rapport 6, N1): de engine bewaart de stand zelf, zodat ook na
-- een herstart geen nieuwe encode start en lopende encodes gepauzeerd blijven.
CREATE TABLE IF NOT EXISTS noodstop (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    actief INTEGER NOT NULL DEFAULT 0,
    reden TEXT,
    sinds TEXT,
    bijgewerkt TEXT
);
INSERT OR IGNORE INTO noodstop(id, actief) VALUES (1, 0);

-- Een job die door de noodstop is stilgezet (SIGSTOP), zodat de UI 'gepauzeerd (noodstop)'
-- kan tonen en de vastloperbewaking hem niet als fout ziet.
ALTER TABLE job ADD COLUMN noodstop_gepauzeerd INTEGER NOT NULL DEFAULT 0;

-- Vastlopers per plek (rapport 6, N2): telling per worker en per bron, zodat een plek
-- die steeds vastloopt automatisch wordt teruggeschaald en een bron niet een derde keer
-- op dezelfde manier wordt geprobeerd.
CREATE TABLE IF NOT EXISTS vastloper (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL,
    bestand_id INTEGER,
    baan TEXT NOT NULL,
    worker_id TEXT,
    encoder TEXT,
    percent REAL,
    tijd TEXT NOT NULL,
    diagnose TEXT
);
CREATE INDEX IF NOT EXISTS idx_vastloper_tijd ON vastloper(tijd);
CREATE INDEX IF NOT EXISTS idx_vastloper_bestand ON vastloper(bestand_id);
ALTER TABLE job ADD COLUMN route_wissel INTEGER NOT NULL DEFAULT 0;
