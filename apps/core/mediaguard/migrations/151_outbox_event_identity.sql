-- Behoud bestaande rijen en hun ID's. SQLite mag een eenmaal uitgegeven ID daarna
-- niet opnieuw toekennen, ook niet als de outbox door retentie helemaal leeg is.
CREATE TABLE outbox_events_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    topic TEXT NOT NULL,
    instance_id TEXT,
    payload TEXT NOT NULL,
    delivered INTEGER NOT NULL DEFAULT 0
);
INSERT INTO outbox_events_new(id, ts, topic, instance_id, payload, delivered)
SELECT id, ts, topic, instance_id, payload, delivered FROM outbox_events;
DROP TABLE outbox_events;
ALTER TABLE outbox_events_new RENAME TO outbox_events;
CREATE INDEX idx_outbox_ts ON outbox_events(ts);

CREATE TABLE outbox_retention (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    deleted_through INTEGER NOT NULL,
    revision INTEGER NOT NULL
);
INSERT INTO outbox_retention VALUES (1, 0, 0);

-- Veroudering en verwijdering zijn één transactie, ook bij gaten door een oudere
-- tijdstempel op een hoger ID. De revisie stijgt ook bij verwijderen onder de grens.
CREATE TRIGGER outbox_retention_after_delete AFTER DELETE ON outbox_events
BEGIN
    UPDATE outbox_retention
    SET deleted_through = MAX(deleted_through, OLD.id), revision = revision + 1
    WHERE singleton = 1;
END;
