-- Bestaande registraties blijven open. Alleen een begonnen verwijdering heeft extra toestand.
CREATE TABLE download_janitor_purge_state (
    trash_id INTEGER PRIMARY KEY REFERENCES download_janitor_trash(id) ON DELETE CASCADE,
    manual_only INTEGER NOT NULL DEFAULT 0,
    partial INTEGER NOT NULL DEFAULT 0
);
