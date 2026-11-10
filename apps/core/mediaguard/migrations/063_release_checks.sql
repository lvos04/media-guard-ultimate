-- Vrijgavecontrole vóór het opruimen van een origineel uit quarantaine.
CREATE TABLE quarantine_release_checks (
    item_id INTEGER PRIMARY KEY REFERENCES quarantine_items(id) ON DELETE CASCADE,
    status TEXT NOT NULL CHECK (status IN ('passed','failed')),
    checks_json TEXT NOT NULL DEFAULT '[]',
    checked_at TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 1
);
