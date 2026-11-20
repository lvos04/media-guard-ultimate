"""Vergelijk een nieuwe herstelset vóór migratie en een databasekopie erna."""

import os
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from mediaguard import db as database
from mediaguard.maintenance import preupdate

with TemporaryDirectory() as werk:
    root = Path(werk)
    pad = root / "core" / "mediaguard.db"
    historische_sql = (
        Path(os.environ["HISTORIE"]) / "154/apps/core/mediaguard/migrations"
    )
    with (
        patch.object(database, "MIGRATIONS_DIR", historische_sql),
        closing(database.Database(pad)) as db,
    ):
        db.initialize()
        (oud_materiaal,) = db.fetch_one("SELECT cursor_key FROM outbox_retention")
        assert isinstance(oud_materiaal, bytes) and len(oud_materiaal) == 32
        db.execute(
            "INSERT INTO outbox_events(ts,topic,payload) VALUES (?,?,?)",
            ("2026-11-18T00:00:00Z", "library.synced", "{}"),
        )
    archief = preupdate.snapshot_before_start(
        root,
        core_migrations=database.MIGRATIONS_DIR,
        encoder_migrations=None,
        app_version="proef",
        migrate_core=True,
    )
    assert archief is not None and not preupdate.verify(archief)
    kopie = root / "na-migratie.db"
    with closing(database.Database(pad)) as db:
        assert (
            db.fetch_one("SELECT typeof(cursor_key) FROM outbox_retention")[0] == "text"
        )
        assert db.fetch_one("SELECT COUNT(*) FROM outbox_events")[0] == 1
        db.backup_to(kopie)
    nieuw_materiaal = (root / "secrets/core/outbox.key").read_bytes()
    assert len(nieuw_materiaal) == 32
    assert not any(
        sleutel in bestand.read_bytes()
        for sleutel in (oud_materiaal, nieuw_materiaal)
        for bestand in (pad, kopie)
    )
    if oud_materiaal in (archief / "mediaguard.db").read_bytes():
        print(
            "Nieuwe geverifieerde herstelset van migratie 156 bevat de oude sleutel onversleuteld. Live database en latere kopie bevatten geen sleutelmateriaal."
        )
        raise SystemExit(1)
    print(
        "Nieuwe geverifieerde herstelset, live database en latere kopie bevatten geen sleutelmateriaal."
    )
