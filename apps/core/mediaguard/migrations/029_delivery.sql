-- Leveringsgarantie: elke aanvraag (Seerr of direct in Sonarr/Radarr) wordt gevolgd tot hij
-- volgens de eisen in de bibliotheek staat. Eén rij per titel (extern ID), plus een tijdlijn.

CREATE TABLE delivery_items (
    id INTEGER PRIMARY KEY,
    item_key TEXT NOT NULL UNIQUE,           -- 'series:tvdb:123' / 'movie:tmdb:456'
    source TEXT NOT NULL,                    -- 'seerr' | 'arr'
    seerr_request_ids_json TEXT NOT NULL DEFAULT '[]',
    media_type TEXT NOT NULL CHECK (media_type IN ('series','movie')),
    tmdb_id INTEGER,
    tvdb_id INTEGER,
    title TEXT,
    instance_id TEXT,
    provider_id INTEGER,
    requested_seasons_json TEXT NOT NULL DEFAULT '[]',
    stage TEXT NOT NULL DEFAULT 'requested',
    state TEXT NOT NULL DEFAULT 'active'
        CHECK (state IN ('active','waiting_release','delivered','not_found')),
    detail_nl TEXT,
    next_step_nl TEXT,
    progress_json TEXT NOT NULL DEFAULT '{}',
    attempts INTEGER NOT NULL DEFAULT 0,
    first_search_at TEXT,
    last_action_at TEXT,
    next_action_at TEXT,
    requested_at TEXT,
    delivered_at TEXT,
    seerr_synced_at TEXT,
    notified_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX idx_delivery_state ON delivery_items(state, next_action_at);
CREATE INDEX idx_delivery_location ON delivery_items(instance_id, provider_id);

CREATE TABLE delivery_events (
    id INTEGER PRIMARY KEY,
    item_id INTEGER NOT NULL REFERENCES delivery_items(id) ON DELETE CASCADE,
    ts TEXT NOT NULL,
    stage TEXT NOT NULL,
    action TEXT NOT NULL,
    actor TEXT NOT NULL,
    detail_nl TEXT NOT NULL,
    data_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX idx_delivery_events_item ON delivery_events(item_id, id);
