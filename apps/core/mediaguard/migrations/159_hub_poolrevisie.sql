-- De revisie hoort bij de keuze van het pooladres en overleeft een herstart.
-- Bestaande adressen en versleutelde geheimen blijven bij de upgrade behouden.
CREATE TABLE hub_pool_revision (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    revision TEXT NOT NULL
);
INSERT INTO hub_pool_revision VALUES (1, lower(hex(randomblob(16))));

-- Na wissen is er geen binding. Ook opnieuw kiezen van hetzelfde adres is een nieuwe keuze.
CREATE TRIGGER hub_poolrevisie_bij_toevoegen AFTER INSERT ON settings_values
WHEN NEW.scope_type = 'global' AND NEW.scope_id = '' AND NEW.key = 'system.pool_hub_url'
BEGIN
    UPDATE hub_pool_revision SET revision = lower(hex(randomblob(16)));
END;

CREATE TRIGGER hub_poolrevisie_bij_wijzigen AFTER UPDATE OF value_json ON settings_values
WHEN NEW.scope_type = 'global' AND NEW.scope_id = '' AND NEW.key = 'system.pool_hub_url'
    AND NEW.value_json <> OLD.value_json
BEGIN
    UPDATE hub_pool_revision SET revision = lower(hex(randomblob(16)));
END;
