-- Meerdere mediaservers tegelijk (Δ-MG-DUAL, contract 1.3 §2.3 regel 2, C1.3-MGU-04, ADR-0022).
--
-- Alleen toevoegen, niets verplaatsen of wissen (HARDE EIS, contract 1.3 §2.3 regel 7b): de
-- bestaande koppeling (mediaserver.kind/url, geheim mediaserver.api_key, padvertaling) blijft
-- waar hij stond en is server 1. Deze tabel bevat alleen de servers die erbij komen (id >= 2),
-- bijvoorbeeld Jelly Leon naast Emby. Een terugzet naar de vorige image negeert de nieuwe tabel en
-- kolom en vindt de Emby-koppeling ongewijzigd terug.
CREATE TABLE mediaserver_servers (
    id INTEGER PRIMARY KEY,          -- >= 2 (1 = de bestaande koppeling); de code kiest het id
    kind TEXT NOT NULL CHECK (kind IN ('emby', 'jellyfin', 'plex')),
    label TEXT NOT NULL,
    url TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    position INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    created_by TEXT
);

-- Verversen, afspeeltest en NL-ondertitelcontrole per server en per overdracht. De bestaande
-- rij in mediaserver_pending blijft de samenvatting per overdracht (zoals in 1.2.2 gelezen door de
-- web-UI, de Android-app en de AV1-vergelijking); deze tabel geeft de uitkomst per server.
CREATE TABLE mediaserver_server_results (
    transaction_id INTEGER NOT NULL,
    server_id INTEGER NOT NULL,
    refreshed_at TEXT,
    test_after TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    result TEXT,                     -- ok|transcode|failed|not_found|skipped
    detail_nl TEXT,
    nl_subtitles TEXT,               -- visible|missing|not_expected|unknown
    done_at TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (transaction_id, server_id)
);
CREATE INDEX idx_mediaserver_server_results_open ON mediaserver_server_results(done_at);

-- Welke server een actie betreft (NULL = rij van vóór deze versie of geen server, bv. 'discover').
ALTER TABLE mediaserver_actions ADD COLUMN server_id INTEGER;
