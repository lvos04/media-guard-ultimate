"""De eerste AV1-migratie met desgewenst één actieve taak voor de terugzetproeven."""

import sqlite3
from pathlib import Path


def maak_av1_v1(db: sqlite3.Connection, percent: int | None = None) -> None:
    sql = Path(__file__).resolve().parents[3] / "services/encoder/app/migrations/001_init.sql"
    db.executescript(sql.read_text())
    db.executescript("""
        CREATE TABLE schema_versie (versie INTEGER PRIMARY KEY, naam TEXT, toegepast_op TEXT);
        INSERT INTO schema_versie VALUES (1, '001_init.sql', 'proef');
    """)
    if percent is not None:
        db.executescript("""
            INSERT INTO profiel(id,naam,inst) VALUES (1,'proef','{}');
            INSERT INTO library(id,naam,pad,profiel_id) VALUES (1,'proef','/fixture',1);
            INSERT INTO bestand(id,library_id,pad,grootte,mtime,categorie,gescand_op)
              VALUES (1,1,'film',1,0,'video',CURRENT_TIMESTAMP);
            INSERT INTO besluit(id,bestand_id,actie,reden,payload,gemaakt_op)
              VALUES (1,1,'hercoderen','proef','{}',CURRENT_TIMESTAMP);
        """)
        db.execute("INSERT INTO job(besluit_id,bestand_id,baan,status,percent) "
                   "VALUES (1,1,'cpu','bezig',?)", (percent,))
        db.commit()
