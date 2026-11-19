-- Een handtekening bindt de leespositie aan de uitgegeven retentierevisie.
ALTER TABLE outbox_retention ADD COLUMN cursor_key BLOB;
UPDATE outbox_retention SET cursor_key = randomblob(32) WHERE singleton = 1;

-- Voor elke oude cursor telt de grootste verwijdering sinds zijn eigen revisie.
-- Een latere verwijdering op een hoger ID vervangt alle lagere grenspunten.
CREATE TABLE outbox_deletions (
    revision INTEGER PRIMARY KEY,
    event_id INTEGER NOT NULL UNIQUE
);
DROP TRIGGER outbox_retention_after_delete;
CREATE TRIGGER outbox_retention_after_delete AFTER DELETE ON outbox_events
BEGIN
    UPDATE outbox_retention
    SET deleted_through = MAX(deleted_through, OLD.id), revision = revision + 1
    WHERE singleton = 1;
    DELETE FROM outbox_deletions WHERE event_id <= OLD.id;
    INSERT INTO outbox_deletions(revision, event_id)
    SELECT revision, OLD.id FROM outbox_retention WHERE singleton = 1;
END;

-- Een installatie die 151 al gebruikte heeft numerieke bron-ID's uitgegeven.
-- Die rijen behouden hun identiteit. Iedere latere insert krijgt binnen dezelfde
-- transactie eigen entropie, ook na het terugzetten van een databasekopie.
CREATE TABLE IF NOT EXISTS outbox_source (
    event_id INTEGER PRIMARY KEY REFERENCES outbox_events(id) ON DELETE CASCADE,
    birth BLOB NOT NULL
);
CREATE TRIGGER outbox_source_after_insert AFTER INSERT ON outbox_events
BEGIN
    INSERT INTO outbox_source(event_id, birth) VALUES (NEW.id, CAST(X'01' || randomblob(16) AS BLOB));
END;
