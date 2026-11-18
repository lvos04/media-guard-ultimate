"""Start een echte kern op een wegwerpdatabase van een toekomstige release."""

import os
import runpy
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path

from mediaguard.db import Database

with tempfile.TemporaryDirectory(prefix="mg-e2e-schema-") as folder:
    os.environ["MG_DATA_DIR"] = folder
    db = Database(Path(folder) / "mediaguard.db")
    db.initialize()
    db.close()
    with closing(sqlite3.connect(db.path)) as conn, conn:
        conn.execute("INSERT INTO schema_migrations VALUES ('999_toekomstig', 'fixture')")
    runpy.run_module("mediaguard", run_name="__main__")
