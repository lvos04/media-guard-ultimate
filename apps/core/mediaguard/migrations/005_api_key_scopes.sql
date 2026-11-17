-- Rechten (scopes) op API-sleutels (S-P3-600, C1.3-MGU-05). NULL = het oude gedrag: de rol bepaalt
-- alles (de sleutels van Media Source Optimizer en de Android-app blijven precies zo werken). Anders
-- een JSON-lijst met rechten, alleen op rol viewer (apikeys.create_key). Voegt alleen een kolom toe:
-- geen bestaande rij wijzigt.
ALTER TABLE api_keys ADD COLUMN scopes TEXT NULL;
