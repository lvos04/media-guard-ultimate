-- Ook afgesloten en onderbroken registraties reserveren hun pad. Oude dubbele
-- verwijzingen moeten bij een upgrade behouden blijven voor de opruimcontrole.
CREATE INDEX idx_djt_trash_path ON download_janitor_trash(trash_path);

-- Een geboekte terugplaatsing kan na onderbreking aan dezelfde inode worden afgemaakt.
ALTER TABLE download_janitor_purge_state ADD COLUMN restore_file TEXT;
