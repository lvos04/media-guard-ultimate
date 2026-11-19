-- Een eerdere uitvoering van 151 kende nog geen generatie. Vul die eenmalig aan.
CREATE TABLE IF NOT EXISTS outbox_generation (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    generation BLOB NOT NULL CHECK (length(generation) = 16)
);
INSERT INTO outbox_generation (singleton, generation)
SELECT 1, randomblob(16) WHERE NOT EXISTS (SELECT 1 FROM outbox_generation);
