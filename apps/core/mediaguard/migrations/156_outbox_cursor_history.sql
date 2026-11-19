-- Eerdere installaties hadden een HMAC-sleutel in deze kolom. Wis ook de oude celinhoud.
-- Een onbruikbare oude sleutel geeft geen toestemming om leesposities uit te geven.
PRAGMA secure_delete=ON;
UPDATE outbox_retention SET cursor_key = CASE
    WHEN typeof(cursor_key) = 'blob' AND length(cursor_key) = 32 THEN 'core/outbox.key'
    WHEN cursor_key = 'core/outbox.key' THEN cursor_key
    ELSE NULL END;

-- Een publiek toevalskenmerk onderscheidt gelijke revisienummers na terugzetten.
-- Bewaar een oude revisie alleen zolang ten minste één van haar posities nog geldig kan zijn.
CREATE TABLE outbox_cursor_history (
    revision INTEGER PRIMARY KEY,
    witness BLOB NOT NULL CHECK (typeof(witness) = 'blob' AND length(witness) = 16),
    last_position INTEGER
);
CREATE INDEX idx_cursor_history_position ON outbox_cursor_history(last_position);
INSERT INTO outbox_cursor_history(revision, witness)
SELECT revision, randomblob(16) FROM outbox_retention;

CREATE TRIGGER outbox_cursor_after_deletion AFTER INSERT ON outbox_deletions
BEGIN
    UPDATE outbox_cursor_history
    SET last_position = (SELECT seq FROM sqlite_sequence WHERE name = 'outbox_events')
    WHERE revision = NEW.revision - 1;
    DELETE FROM outbox_cursor_history WHERE last_position <= NEW.event_id;
    INSERT INTO outbox_cursor_history(revision, witness) VALUES (NEW.revision, randomblob(16));
END;
