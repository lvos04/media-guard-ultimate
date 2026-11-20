-- Een hangende reservering bindt de afmelding aan het bestand op het oorspronkelijke pad.
ALTER TABLE download_janitor_purge_state ADD COLUMN source_file TEXT;

-- Bij een oude clientboeking ontbreekt het bewijs van afmelding. De beheerder kan
-- de inhoud terugzetten, ook als een eerdere verwijdering al was onderbroken.
INSERT INTO download_janitor_purge_state(trash_id, manual_only)
SELECT id, 1 FROM download_janitor_trash
WHERE client IS NOT NULL AND purged_at IS NULL AND restored_at IS NULL
ON CONFLICT(trash_id) DO UPDATE SET manual_only = 1;
