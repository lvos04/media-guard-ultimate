-- MediaGuard-brug (packages/contracts/encoder-bridge.md). Uitsluitend
-- toevoegingen: bestaande jobs, besluiten en verificaties blijven ongewijzigd en
-- reconstrueerbaar. Kolommen met prefix mg_ zijn leeg voor native jobs.
ALTER TABLE job ADD COLUMN mg_bron TEXT;
ALTER TABLE job ADD COLUMN mg_correlation_id TEXT;
ALTER TABLE job ADD COLUMN mg_media_ref TEXT;
ALTER TABLE job ADD COLUMN mg_policy TEXT;
ALTER TABLE job ADD COLUMN mg_inst TEXT;
ALTER TABLE job ADD COLUMN mg_vervangbaar INTEGER NOT NULL DEFAULT 1
    CHECK (mg_vervangbaar IN (0, 1));
ALTER TABLE job ADD COLUMN mg_snapshot TEXT;
ALTER TABLE job ADD COLUMN mg_staged_pad TEXT;
ALTER TABLE job ADD COLUMN mg_staged_grootte INTEGER;
ALTER TABLE job ADD COLUMN mg_staged_sha256 TEXT;
ALTER TABLE job ADD COLUMN mg_verificatie TEXT;
ALTER TABLE job ADD COLUMN mg_waarschuwingen TEXT;
ALTER TABLE job ADD COLUMN mg_afgehandeld_op TEXT;

CREATE INDEX idx_job_mg_correlatie ON job(mg_correlation_id);

-- P4: per-title proefencodes. Iedere meting bewaart tool-, encoder- en
-- configuratieversies zodat een beslissing later exact te herhalen is.
CREATE TABLE trial_run (
    id INTEGER PRIMARY KEY,
    bron_pad TEXT NOT NULL,
    bron_grootte INTEGER,
    bron_mtime REAL,
    kwaliteitsprofiel TEXT NOT NULL,
    dv_mode TEXT,
    status TEXT NOT NULL CHECK (status IN ('bezig','klaar','mislukt')),
    samples TEXT NOT NULL DEFAULT '[]',
    aanbeveling TEXT,
    tool_versies TEXT NOT NULL DEFAULT '{}',
    regel_versie TEXT NOT NULL,
    fout TEXT,
    gemaakt_op TEXT NOT NULL,
    klaar_op TEXT
);

CREATE TABLE encoding_trials (
    id INTEGER PRIMARY KEY,
    run_id INTEGER NOT NULL REFERENCES trial_run(id) ON DELETE CASCADE,
    sample_index INTEGER NOT NULL,
    sample_start REAL NOT NULL,
    sample_duur REAL NOT NULL,
    encoder TEXT NOT NULL,
    crf INTEGER,
    preset INTEGER,
    film_grain INTEGER NOT NULL DEFAULT 0,
    bron_bytes INTEGER,
    grootte INTEGER,
    bitrate_kbps REAL,
    encode_seconden REAL,
    status TEXT NOT NULL CHECK (status IN ('klaar','mislukt')),
    fout TEXT,
    gemaakt_op TEXT NOT NULL
);
CREATE INDEX idx_encoding_trials_run ON encoding_trials(run_id, encoder, crf);

CREATE TABLE quality_measurements (
    id INTEGER PRIMARY KEY,
    trial_id INTEGER NOT NULL REFERENCES encoding_trials(id) ON DELETE CASCADE,
    metriek TEXT NOT NULL CHECK (metriek IN ('vmaf','ssim','psnr')),
    waarde REAL NOT NULL,
    meetmethode TEXT NOT NULL,
    tool_versie TEXT,
    gemaakt_op TEXT NOT NULL
);
CREATE INDEX idx_quality_measurements_trial ON quality_measurements(trial_id);
