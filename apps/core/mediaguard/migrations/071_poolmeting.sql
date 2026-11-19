-- Een bronwijziging trekt in dezelfde transactie de nieuwste meetopdracht in.
-- Dit geldt ook voor een wissel terug naar het oude adres en voor andere werkers.
-- De laatst bekende poolinformatie blijft beschikbaar voor de bewaking.
CREATE TRIGGER poolbron_instelling_toegevoegd AFTER INSERT ON settings_values
WHEN NEW.scope_type = 'global' AND NEW.scope_id = ''
 AND NEW.key IN ('recovery.truenas_url', 'recovery.truenas_verify_tls')
BEGIN
    DELETE FROM system_state WHERE key = 'truenas_pool_request_v1';
END;

CREATE TRIGGER poolbron_instelling_gewijzigd AFTER UPDATE OF value_json ON settings_values
WHEN NEW.scope_type = 'global' AND NEW.scope_id = '' AND NEW.value_json <> OLD.value_json
 AND NEW.key IN ('recovery.truenas_url', 'recovery.truenas_verify_tls')
BEGIN
    DELETE FROM system_state WHERE key = 'truenas_pool_request_v1';
END;

CREATE TRIGGER poolbron_instelling_gewist AFTER DELETE ON settings_values
WHEN OLD.scope_type = 'global' AND OLD.scope_id = ''
 AND OLD.key IN ('recovery.truenas_url', 'recovery.truenas_verify_tls')
BEGIN
    DELETE FROM system_state WHERE key = 'truenas_pool_request_v1';
END;

CREATE TRIGGER poolbron_sleutel_toegevoegd AFTER INSERT ON secrets
WHEN NEW.name = 'truenas:apikey'
BEGIN
    DELETE FROM system_state WHERE key = 'truenas_pool_request_v1';
END;

CREATE TRIGGER poolbron_sleutel_gewijzigd AFTER UPDATE OF ciphertext ON secrets
WHEN NEW.name = 'truenas:apikey' AND NEW.ciphertext <> OLD.ciphertext
BEGIN
    DELETE FROM system_state WHERE key = 'truenas_pool_request_v1';
END;

CREATE TRIGGER poolbron_sleutel_gewist AFTER DELETE ON secrets
WHEN OLD.name = 'truenas:apikey'
BEGIN
    DELETE FROM system_state WHERE key = 'truenas_pool_request_v1';
END;
