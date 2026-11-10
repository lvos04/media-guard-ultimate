-- Mediaserver (Jellyfin/Plex) en Nederlandse ondertitels (rapport 5: M3, M8)

-- Wat MediaGuard bij de mediaserver deed: refresh na overdracht, afspeeltest, pauze tijdens
-- kijken. Leesbaar in Instellingen → Koppelingen.
CREATE TABLE mediaserver_actions (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    kind TEXT NOT NULL,              -- refresh|playback_test|hold|resume|discover|error
    path TEXT,
    transaction_id INTEGER,
    ok INTEGER NOT NULL DEFAULT 1,
    detail_nl TEXT
);
CREATE INDEX idx_mediaserver_actions_ts ON mediaserver_actions(ts);

-- Overdrachten die nog een refresh/afspeeltest moeten krijgen.
CREATE TABLE mediaserver_pending (
    transaction_id INTEGER PRIMARY KEY,
    path TEXT NOT NULL,
    kind TEXT NOT NULL,              -- replace|restore
    encoding_job_id INTEGER,
    refreshed_at TEXT,
    test_after TEXT,                 -- afspeeltest pas na deze tijd (server moet scannen)
    attempts INTEGER NOT NULL DEFAULT 0,
    result TEXT,                     -- ok|transcode|failed|not_found|skipped
    detail_nl TEXT,
    done_at TEXT,
    created_at TEXT NOT NULL
);

-- Recent bekeken bestanden (MediaGuard-pad): niet omzetten zolang dit vers is.
CREATE TABLE mediaserver_watch (
    path TEXT PRIMARY KEY,
    title TEXT,
    user_name TEXT,
    last_seen TEXT NOT NULL
);

-- Nederlandse ondertitels per item (bron: Bazarr of eigen controle).
CREATE TABLE subtitle_status (
    key TEXT PRIMARY KEY,            -- '<instance>:episode:<id>' of '<instance>:movie:<id>' of 'bazarr:...'
    instance_id TEXT,
    media_kind TEXT,
    provider_id INTEGER,
    title TEXT NOT NULL,
    nl_missing INTEGER NOT NULL DEFAULT 1,
    source TEXT NOT NULL,            -- bazarr|opensubtitles|file
    searched_at TEXT,
    updated_at TEXT NOT NULL
);
CREATE INDEX idx_subtitle_status_missing ON subtitle_status(nl_missing);

-- Door MediaGuard zelf opgehaalde ondertitels (OpenSubtitles): journaal, nooit overschrijven.
CREATE TABLE subtitle_downloads (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    video_path TEXT NOT NULL,
    srt_path TEXT NOT NULL,
    provider TEXT NOT NULL,
    file_id TEXT,
    size INTEGER,
    status TEXT NOT NULL,            -- saved|skipped|failed
    detail_nl TEXT
);
