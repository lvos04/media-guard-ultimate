-- Automatische anime-routering (sonarr-series → sonarr-anime, radarr-films → radarr-animefilms).
CREATE TABLE anime_routing_actions (
    id INTEGER PRIMARY KEY,
    source_instance TEXT NOT NULL,
    target_instance TEXT,
    kind TEXT NOT NULL CHECK (kind IN ('series','movie')),
    provider_id INTEGER NOT NULL,
    target_provider_id INTEGER,
    external_id INTEGER,
    title TEXT,
    status TEXT NOT NULL CHECK (status IN
        ('moved','suggested','needs_review','failed','reverted','dismissed')),
    evidence_json TEXT NOT NULL DEFAULT '[]',
    detail TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (source_instance, kind, provider_id)
);

-- 'Hier laten': deze titel nooit meer automatisch verplaatsen.
CREATE TABLE anime_routing_exclusions (
    source_instance TEXT NOT NULL,
    kind TEXT NOT NULL,
    external_id INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    created_by TEXT,
    PRIMARY KEY (source_instance, kind, external_id)
);
