-- Opslaglogboek: wat kwam erbij en waar ging de ruimte heen (verzoek gebruiker, 6 nov).
-- Kopie van de relevante Sonarr/Radarr-historie (grab, import, verwijderd) zodat de pagina
-- 'Opslag & downloads' snel en zonder netwerkverkeer kan rekenen. Bewaard ± 40 dagen.
CREATE TABLE storage_ledger_events (
    id INTEGER PRIMARY KEY,
    instance_id TEXT NOT NULL,
    history_id INTEGER NOT NULL,
    at TEXT NOT NULL,
    event TEXT NOT NULL CHECK (event IN ('grabbed','imported','deleted')),
    media_kind TEXT NOT NULL CHECK (media_kind IN ('episode','movie')),
    owner_id INTEGER,
    item_id INTEGER,
    size INTEGER,
    quality TEXT,
    release_group TEXT,
    indexer TEXT,
    download_id TEXT,
    source_title TEXT,
    reason TEXT,
    UNIQUE (instance_id, history_id)
);
CREATE INDEX idx_storage_ledger_at ON storage_ledger_events(at);
CREATE INDEX idx_storage_ledger_item ON storage_ledger_events(instance_id, item_id, event);
CREATE INDEX idx_storage_ledger_dl ON storage_ledger_events(download_id);
