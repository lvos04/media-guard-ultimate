-- Eén generatie voor doelinstellingen en inloggegevens. Ook terugzetten naar
-- dezelfde effectieve waarden vereist daarna een nieuwe kopie.
CREATE TABLE offsite_target_generation (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    generation TEXT NOT NULL
);
INSERT INTO offsite_target_generation VALUES (1, lower(hex(randomblob(16))));

CREATE TABLE offsite_verification (
    copy_id INTEGER PRIMARY KEY REFERENCES offsite_copies(id) ON DELETE CASCADE,
    generation TEXT NOT NULL,
    key_sha256 TEXT NOT NULL,
    key_size INTEGER NOT NULL
);

CREATE TRIGGER offsite_setting_insert AFTER INSERT ON settings_values
WHEN NEW.scope_type = 'global' AND NEW.key LIKE 'backup.offsite.%'
     AND NEW.key NOT IN ('backup.offsite.keep', 'backup.offsite.email_channel')
BEGIN
    UPDATE offsite_target_generation SET generation = lower(hex(randomblob(16)));
END;

CREATE TRIGGER offsite_setting_update AFTER UPDATE ON settings_values
WHEN NEW.scope_type = 'global' AND NEW.key LIKE 'backup.offsite.%'
     AND NEW.key NOT IN ('backup.offsite.keep', 'backup.offsite.email_channel')
BEGIN
    UPDATE offsite_target_generation SET generation = lower(hex(randomblob(16)));
END;

CREATE TRIGGER offsite_setting_delete AFTER DELETE ON settings_values
WHEN OLD.scope_type = 'global' AND OLD.key LIKE 'backup.offsite.%'
     AND OLD.key NOT IN ('backup.offsite.keep', 'backup.offsite.email_channel')
BEGIN
    UPDATE offsite_target_generation SET generation = lower(hex(randomblob(16)));
END;

CREATE TRIGGER offsite_secret_insert AFTER INSERT ON secrets
WHEN NEW.name IN ('backup.offsite.webdav_password', 'backup.offsite.s3_secret_key')
BEGIN
    UPDATE offsite_target_generation SET generation = lower(hex(randomblob(16)));
END;

CREATE TRIGGER offsite_secret_update AFTER UPDATE ON secrets
WHEN NEW.name IN ('backup.offsite.webdav_password', 'backup.offsite.s3_secret_key')
BEGIN
    UPDATE offsite_target_generation SET generation = lower(hex(randomblob(16)));
END;

CREATE TRIGGER offsite_secret_delete AFTER DELETE ON secrets
WHEN OLD.name IN ('backup.offsite.webdav_password', 'backup.offsite.s3_secret_key')
BEGIN
    UPDATE offsite_target_generation SET generation = lower(hex(randomblob(16)));
END;
