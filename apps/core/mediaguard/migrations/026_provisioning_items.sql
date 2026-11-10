-- Eigenaarschap per item in een instantie (of in Prowlarr/Seerr: instance_id = '').
-- ownership: mediaguard (door MediaGuard aangemaakt/beheerd), user (gebruiker beheert;
-- nooit aanraken), migrated (uit de migratie; inloggegevens blijven, alleen afgesproken
-- velden zoals categorie worden beheerd).
CREATE TABLE provisioning_items (
    instance_id TEXT NOT NULL,
    section TEXT NOT NULL,
    item_key TEXT NOT NULL,
    remote_id INTEGER,
    ownership TEXT NOT NULL DEFAULT 'mediaguard'
        CHECK (ownership IN ('mediaguard','user','migrated')),
    secret_hash TEXT,
    applied_at TEXT,
    updated_at TEXT NOT NULL,
    updated_by TEXT,
    PRIMARY KEY (instance_id, section, item_key)
);
