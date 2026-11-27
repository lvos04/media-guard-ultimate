-- Alleen de nieuw gekozen Hub mag het pooltoken ontvangen. Ook een tussentijdse
-- adres- of tokenwisseling trekt een uitstaande Hub-meting atomair in.
CREATE TRIGGER hub_pooladres_toegevoegd AFTER INSERT ON settings_values
WHEN NEW.scope_type = 'global' AND NEW.scope_id = '' AND NEW.key = 'system.pool_hub_url'
BEGIN
    DELETE FROM secrets WHERE name = 'hub:poolstatus';
    DELETE FROM system_state WHERE key = 'truenas_pool_request_v1'
        AND NOT EXISTS (SELECT 1 FROM secrets WHERE name = 'truenas:apikey');
END;

CREATE TRIGGER hub_pooladres_gewijzigd AFTER UPDATE OF value_json ON settings_values
WHEN NEW.scope_type = 'global' AND NEW.scope_id = '' AND NEW.key = 'system.pool_hub_url'
    AND NEW.value_json <> OLD.value_json
BEGIN
    DELETE FROM secrets WHERE name = 'hub:poolstatus';
    DELETE FROM system_state WHERE key = 'truenas_pool_request_v1'
        AND NOT EXISTS (SELECT 1 FROM secrets WHERE name = 'truenas:apikey');
END;

CREATE TRIGGER hub_pooladres_gewist AFTER DELETE ON settings_values
WHEN OLD.scope_type = 'global' AND OLD.scope_id = '' AND OLD.key = 'system.pool_hub_url'
BEGIN
    DELETE FROM secrets WHERE name = 'hub:poolstatus';
    DELETE FROM system_state WHERE key = 'truenas_pool_request_v1'
        AND NOT EXISTS (SELECT 1 FROM secrets WHERE name = 'truenas:apikey');
END;

CREATE TRIGGER hub_pooltoken_toegevoegd AFTER INSERT ON secrets
WHEN NEW.name = 'hub:poolstatus'
BEGIN
    DELETE FROM system_state WHERE key = 'truenas_pool_request_v1'
        AND NOT EXISTS (SELECT 1 FROM secrets WHERE name = 'truenas:apikey');
END;

CREATE TRIGGER hub_pooltoken_gewijzigd AFTER UPDATE OF ciphertext ON secrets
WHEN NEW.name = 'hub:poolstatus' AND NEW.ciphertext <> OLD.ciphertext
BEGIN
    DELETE FROM system_state WHERE key = 'truenas_pool_request_v1'
        AND NOT EXISTS (SELECT 1 FROM secrets WHERE name = 'truenas:apikey');
END;

CREATE TRIGGER hub_pooltoken_gewist AFTER DELETE ON secrets
WHEN OLD.name = 'hub:poolstatus'
BEGIN
    DELETE FROM system_state WHERE key = 'truenas_pool_request_v1'
        AND NOT EXISTS (SELECT 1 FROM secrets WHERE name = 'truenas:apikey');
END;
