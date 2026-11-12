#!/usr/bin/env python3
"""Upgradebewijs — HARDE EIS van de gebruiker (contract 1.3 §2.3 regel 7b en §12, C1.3-MGU-03).

Kijkgeschiedenis en MediaGuard-voortgang blijven altijd beschikbaar. Elke MediaGuard-release (ook de
eerste uit de nieuwe publieke repo) bewijst daarom een upgrade op een **gevulde database van de
vorige versie** zonder gegevensverlies (``docs/platform-v2/B03_DELTAVERZOEKEN.md`` §1). Zonder groen
bewijs geen release (``release.yml``: ``publish`` hangt af van de job ``upgradebewijs``) en geen
update op de NAS.

Opdrachten (alleen de standaardbibliotheek; draait ook in het image zelf, Python 3.12–3.14):

``vul --db <pad> --profiel <core|encode>``
    Vult elke tabel van een database met herkenbare, inerte rijen (``fixture-<tabel>-<kolom>-<n>``):
    afgeronde taken, gearchiveerde instanties, uitgeschakelde kanalen. Zo heeft elk domein van de
    voortgang inhoud en kan elk verlies gezien worden. ``scripts/make-aio-fixture.py`` gebruikt dit
    voor de core-fixture; de poort vult er de AV1-database mee (die maakt de vorige release zelf).
``meet --map <config> --uit <json>``
    Meet alle live SQLite-databases onder de map (geen back-ups): per tabel kolommen, sleutel,
    aantal rijen, inhoudshash en de rijen zelf.
``vergelijk --voor <json> --na <json> [--uitzonderingen <json>] [--rapport <md>]``
    Elk verschil in de kolommen van vóór is een fout (rij weg, waarde anders, tabel/kolom/database
    weg), tenzij het in het uitzonderingenbestand staat met een reden; het rapport toont die.
    Nieuwe rijen, kolommen en tabellen mogen.
``poort --vorige <image@digest> --nieuw <image> --versie <x.y.z>``
    De hele poort met Docker: de vorige release starten op de gevulde fixture (+ AV1-database vullen
    en opnieuw starten), meten, de nieuwe release starten op dezelfde mappen (health, versie,
    inloggen met de oude beheerder, readback via de API), meten en vergelijken, en terugzetten
    bewijzen: de vorige image op de snapshot van vóór de upgrade start en geeft alles terug.
    Beide secties tonen de vergelijking per tabel. Open werk (een overdracht naar de mediaserver
    die nog loopt) telt de poort apart: de fixture noemt het aantal (``open_overdrachten``); het
    moet er vóór de upgrade zijn en na de upgrade en na het terugzetten nog open staan.
    Alleen ``docker``; geen root op de host nodig (bestanden van 568 regelt een hulpcontainer).

Uitgang 0 = bewijs groen, 1 = verlies of fout, 2 = verkeerd gebruik.
"""

from __future__ import annotations

import argparse
import contextlib
import fnmatch
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "aio"
UITZONDERINGEN = FIXTURES / "upgradebewijs-uitzonderingen.json"
APP_UID = 568
NU = "2026-11-08T12:00:00+00:00"  # vaste tijd in de fixture (geen echte tijd: reproduceerbaar)
#: wachttijd van de afspeeltest bij open werk in de fixture: nooit bereikt, zodat geen proef (ook
#: de contracttest met een echte Emby niet) die test uitvoert of iets bij de mediaserver opvraagt
TOEKOMST = "2099-01-01T00:00:00+00:00"

# ------------------------------------------------------------------------------------- vullen
#: tabellen die nooit gevuld worden: geheimen (versleuteld met een sleutel buiten de fixture),
#: sessies en aanmeldcodes (tokens), vergrendelingen (runtime) en de schemaboekhouding zelf
OVERSLAAN = {
    "core": {"schema_migrations", "sessions", "login_challenges", "locks", "secrets",
             "settings_values"},
    "encode": {"schema_versie", "geheim", "instelling", "noodstop", "afstelling"},
}
#: vaste waarden per (profiel, tabel, kolom): afgeronde/inerte toestanden zodat de diensten na de
#: start niets met de fixture gaan doen (geen netwerk, geen encodes, geen zoekopdrachten)
VAST: dict[tuple[str, str], dict[str, Any]] = {
    ("core", "jobs"): {"status": ["succeeded", "cancelled"], "attempts": 1},
    ("core", "instances"): {"archived": 1, "suspended": 1, "status": "offline",
                            "url": "http://fixture.invalid", "public_url": None,
                            "webhook_notification_id": None},
    ("core", "wanted_items"): {"status": ["resolved", "ignored"], "next_search_at": None,
                               "keep_monitoring": 0},
    ("core", "encoding_jobs"): {"status": ["succeeded", "failed"], "encoder_status": "klaar",
                                "progress": 100.0},
    ("core", "optimization_plans"): {"status": ["completed", "cancelled"]},
    ("core", "quarantine_items"): {"status": ["restored", "discarded"], "pinned": 1},
    ("core", "replacement_transactions"): {"state": "committed"},
    # afgerond én open werk (HARDE EIS: ook een overdracht die nog loopt blijft bewaard): de open
    # overdracht is al ververst en wacht op de afspeeltest (TOEKOMST: blijft inert)
    ("core", "mediaserver_pending"): {"result": ["ok", None], "done_at": [NU, None],
                                      "test_after": [None, TOEKOMST]},
    ("core", "notify_channels"): {"enabled": 0, "config_json": "{}"},
    ("core", "notify_outbox"): {"status": ["sent", "dropped"], "next_attempt_at": NU},
    ("core", "notify_digests"): {"kind": ["daily", "weekly"]},
    ("core", "delivery_items"): {"state": ["delivered", "not_found"], "next_action_at": None},
    ("core", "backfill_runs"): {"status": "succeeded", "finished_at": NU},
    ("core", "backfill_attempts"): {"status": ["grabbed", "no_candidate"]},
    ("core", "provisioning_runs"): {"status": ["succeeded", "partial"]},
    ("core", "migration_runs"): {"status": ["completed", "cancelled"]},
    ("core", "migration_steps"): {"status": ["succeeded", "skipped"]},
    ("core", "migration_cutovers"): {"status": ["completed", "rolled_back"]},
    ("core", "source_instances"): {"archived": 1, "url": "http://fixture.invalid"},
    ("core", "outbox_events"): {"delivered": 1},
    ("core", "anime_routing_actions"): {"status": ["reverted", "dismissed"]},
    ("core", "download_janitor_trash"): {"restored_at": NU},
    ("core", "seerr_requests"): {"next_sync_at": None},
    ("core", "users"): {"role": ["viewer", "operator"], "password_hash": "fixture-geen-wachtwoord"},
    ("core", "api_keys"): {"revoked_at": NU},
    ("core", "ai_proposals"): {"status": "afgewezen"},
    ("encode", "job"): {"status": ["klaar", "gecommit"], "automatische_retry_pogingen": 1},
    ("encode", "job_poging"): {"status": "klaar"},
    ("encode", "library"): {"actief": 0,
                            "pad": ["/data/fixture-library-1", "/data/fixture-library-2"]},
    ("encode", "bestand"): {"aanwezig": 0},
    ("encode", "scan_run"): {"status": "klaar"},
    ("encode", "trial_run"): {"status": ["klaar", "mislukt"]},
    ("encode", "besluit"): {"status": "afgehandeld"},
    ("encode", "vervang_transactie"): {"status": "vervangen"},
    ("encode", "ai_analyse"): {"modus": "schaduw", "uitgevoerd": 1},
    ("encode", "worker_config"): {"baan": ["cpu", "gpu"]},
}
#: verwijzingen zonder FOREIGN KEY in het schema, die de code wel volgt: (profiel, tabel) ->
#: {kolom: (oudertabel, ouderkolom)}. Zonder deze lijst krijgt zo'n kolom een losse tekst en
#: verwijst een voltooide overstap naar een bron die niet bestaat (S-P2-610: de nazorg faalde
#: daarop met ``assert row is not None``); in een echte database kan dat niet.
VERWIJST: dict[tuple[str, str], dict[str, tuple[str, str]]] = {
    ("core", "migration_cutovers"): {"source_id": ("source_instances", "id"),
                                     "target_instance_id": ("instances", "id")},
}
#: kolommen zonder "json" in de naam die de code wel als JSON leest
JSON_NAMEN = {"payload", "result", "old_value", "new_value", "details", "data", "context",
              "evidence", "metrics", "params", "args", "changes", "before", "after", "meta",
              "metadata", "response", "request"}
_CHECK_IN = re.compile(r"\b(\w+)\s+IN\s*\(([^()]*)\)", re.I)
_CHECK = re.compile(r"CHECK\s*\(", re.I)


def _checks(sql: str) -> dict[str, list[Any]]:
    """``kolom IN (...)``-lijsten uit de CHECK-beperkingen van een CREATE TABLE."""
    uit: dict[str, list[Any]] = {}
    for m in _CHECK.finditer(sql):
        diepte, j = 1, m.end()
        while diepte and j < len(sql):
            diepte += {"(": 1, ")": -1}.get(sql[j], 0)
            j += 1
        for kol, lijst in _CHECK_IN.findall(sql[m.end():j - 1]):
            waarden: list[Any] = []
            for deel in lijst.split(","):
                deel = deel.strip()
                waarden.append(deel[1:-1] if deel.startswith("'") else int(deel))
            uit[kol] = waarden
    return uit


def _tabellen(conn: sqlite3.Connection) -> list[str]:
    rijen = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE "
                         "'sqlite_%' ORDER BY name").fetchall()
    return [r[0] for r in rijen]


def _volgorde(conn: sqlite3.Connection, tabellen: list[str], profiel: str = "") -> list[str]:
    """Ouders vóór kinderen (vreemde sleutels en de verwijzingen uit ``VERWIJST``)."""
    ouders = {t: ({r[2] for r in conn.execute(f'PRAGMA foreign_key_list("{t}")')}
                  | {o for o, _k in VERWIJST.get((profiel, t), {}).values()}) - {t}
              for t in tabellen}
    uit: list[str] = []
    rest = list(tabellen)
    while rest:
        klaar = [t for t in rest if not (ouders[t] & set(rest))]
        if not klaar:  # kringverwijzing: in naamvolgorde
            klaar = rest[:1]
        for t in klaar:
            uit.append(t)
            rest.remove(t)
    return uit


def _waarde(profiel: str, tabel: str, kol: str, soort: str, i: int, standaard: str | None,
            checks: dict[str, list[Any]]) -> Any:
    vast = VAST.get((profiel, tabel), {})
    if kol in vast:
        v = vast[kol]
        return v[i % len(v)] if isinstance(v, list) else v
    if kol in checks:
        return checks[kol][i % len(checks[kol])]
    naam, t = kol.lower(), soort.upper()
    if "json" in naam or naam in JSON_NAMEN or (standaard or "").strip("'") in ("{}", "[]"):
        return "[]" if (standaard or "").strip("'") == "[]" else "{}"
    if naam.endswith(("_at", "_op", "_utc", "_tijd", "datum")) or naam in ("ts", "tijd", "mtime"):
        return f"2026-11-0{1 + i % 8}T12:00:00+00:00" if "INT" not in t and "REAL" not in t \
            else 1762603200 + i
    if "INT" in t:
        if naam.startswith(("is_", "has_", "heeft_")) or naam in (
                "enabled", "actief", "ingeschakeld", "aanwezig", "suspended", "archived"):
            return 0
        return 1 + i
    if any(x in t for x in ("REAL", "FLOA", "DOUB", "NUM")):
        return 1.5 + i
    if "BLOB" in t:
        return f"fixture-{i}".encode()
    if "pad" in naam or "path" in naam:
        return f"/data/media/fixture/{tabel}/{kol}-{i + 1}.mkv"
    return f"fixture-{tabel}-{kol}-{i + 1}"


def vul(db: Path, profiel: str, rijen: int = 2) -> dict[str, Any]:
    """Vult elke (niet uitgezonderde) tabel aan tot minstens ``rijen`` rijen. Geeft per tabel het
    aantal toegevoegde rijen en wat niet lukte (met reden; nooit stil)."""
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA foreign_keys=ON")
    toegevoegd: dict[str, int] = {}
    mislukt: dict[str, str] = {}
    try:
        for tabel in _volgorde(conn, _tabellen(conn), profiel):
            if tabel in OVERSLAAN.get(profiel, set()):
                continue
            sql = conn.execute("SELECT sql FROM sqlite_master WHERE name=?", (tabel,)).fetchone()[0]
            checks = _checks(sql)
            info = conn.execute(f'PRAGMA table_info("{tabel}")').fetchall()
            fks = {r[3]: (r[2], r[4]) for r in conn.execute(f'PRAGMA foreign_key_list("{tabel}")')}
            fks.update(VERWIJST.get((profiel, tabel), {}))
            pk = [r[1] for r in info if r[5]]
            auto = len(pk) == 1 and next(r[2] for r in info if r[1] == pk[0]).upper() == "INTEGER"
            al = conn.execute(f'SELECT COUNT(*) FROM "{tabel}"').fetchone()[0]
            n = 0
            fout = ""
            for i in range(al, al + rijen):
                kolommen: list[str] = []
                waarden: list[Any] = []
                for _cid, kol, soort, _nn, standaard, _pk in info:
                    if auto and kol == pk[0]:
                        continue
                    if kol in fks:
                        ouder, ouderkol = fks[kol]
                        ouderkol = ouderkol or "rowid"
                        refs = [r[0] for r in conn.execute(
                            f'SELECT "{ouderkol}" FROM "{ouder}" ORDER BY rowid')]
                        if not refs:
                            continue  # ouder leeg (uitgezonderd): kolom leeg laten als dat mag
                        waarden.append(refs[i % len(refs)])
                    else:
                        waarden.append(_waarde(profiel, tabel, kol, soort or "", i, standaard,
                                               checks))
                    kolommen.append(kol)
                lijst = ", ".join(f'"{k}"' for k in kolommen)
                vraag = ", ".join("?" for _ in kolommen)
                try:
                    conn.execute(f'INSERT INTO "{tabel}" ({lijst}) VALUES ({vraag})', waarden)
                    n += 1
                except sqlite3.DatabaseError as exc:
                    fout = str(exc)
                    break
            conn.commit()
            if n:
                toegevoegd[tabel] = n
            if fout and not n:
                mislukt[tabel] = fout
    finally:
        conn.close()
    return {"profiel": profiel, "toegevoegd": toegevoegd, "niet_gevuld": mislukt}


# ------------------------------------------------------------------------------------- meten
def _waarde_json(v: Any) -> Any:
    if isinstance(v, bytes):
        return {"blob_sha256": hashlib.sha256(v).hexdigest()}
    if isinstance(v, float) and v != v:  # NaN
        return "NaN"
    return v


def meet_db(pad: Path) -> dict[str, Any]:
    """Meting van één database (op een kopie, zodat meten nooit iets verandert)."""
    with tempfile.TemporaryDirectory(prefix="mg-meet-") as tmp:
        kopie = Path(tmp) / "db"
        shutil.copy2(pad, kopie)
        for staart in ("-wal", "-shm"):
            if Path(f"{pad}{staart}").exists():
                shutil.copy2(f"{pad}{staart}", f"{kopie}{staart}")
        conn = sqlite3.connect(kopie)
        try:
            integriteit = conn.execute("PRAGMA integrity_check").fetchone()[0]
            tabellen: dict[str, Any] = {}
            for tabel in _tabellen(conn):
                info = conn.execute(f'PRAGMA table_info("{tabel}")').fetchall()
                kolommen = [r[1] for r in info]
                sleutel = [r[1] for r in sorted((r for r in info if r[5]), key=lambda r: r[5])]
                lijst = ", ".join(f'"{k}"' for k in kolommen)
                rijen: dict[str, list[Any]] = {}
                zonder: list[list[Any]] = []
                for rij in conn.execute(f'SELECT {lijst} FROM "{tabel}"'):
                    waarden = [_waarde_json(v) for v in rij]
                    if sleutel:
                        k = json.dumps([waarden[kolommen.index(s)] for s in sleutel],
                                       ensure_ascii=False)
                        rijen[k] = waarden
                    else:
                        zonder.append(waarden)
                inhoud = json.dumps([sorted(rijen.items()), sorted(map(json.dumps, zonder))],
                                    ensure_ascii=False, sort_keys=True)
                tabellen[tabel] = {
                    "kolommen": kolommen, "sleutel": sleutel,
                    "rijen": len(rijen) + len(zonder),
                    "inhoud_sha256": hashlib.sha256(inhoud.encode()).hexdigest(),
                    "per_sleutel": rijen, "zonder_sleutel": zonder,
                }
        finally:
            conn.close()
    return {"integriteit": integriteit, "tabellen": tabellen}


def live_databases(map_: Path) -> list[Path]:
    """Alle SQLite-databases onder de map, behalve back-ups (die zijn kopieën)."""
    uit = []
    for p in sorted(map_.rglob("*.db")):
        rel = p.relative_to(map_).as_posix()
        if "/backups/" in f"/{rel}" or "voor-update" in rel:
            continue
        uit.append(p)
    return uit


def meet(map_: Path) -> dict[str, Any]:
    return {"databases": {p.relative_to(map_).as_posix(): meet_db(p) for p in live_databases(map_)}}


#: databases die de poort vóór de upgrade altijd moet meten (core: de fixture; encode: de vorige
#: release maakt hem zelf en de poort vult hem)
VERPLICHT_VOOR = ("core/mediaguard.db", "encode/app.db")
#: core-tabellen die scripts/make-aio-fixture.py na het tellen leegmaakt (aanmeldtokens) of die
#: alleen runtime zijn: hun aantal in ``rows_per_table`` zegt niets over de dump
RUNTIME_CORE = frozenset({"sessions", "login_challenges", "locks"})


def dekking(voor: dict[str, Any], fixture: dict[str, Any],
            verplicht: tuple[str, ...] = VERPLICHT_VOOR) -> list[str]:
    """Fouten als de meting vóór de upgrade niet bevat wat de fixture gevuld heeft. Zonder deze
    controle vergelijkt de poort een lege of halve meting en meldt hij 'niets verloren' (nooit
    groen bij een fout: een verkeerd pad of een niet-gevulde database is een rode poort)."""
    fouten: list[str] = []
    dbs = voor.get("databases", {})
    for db in verplicht:
        if db not in dbs:
            fouten.append(f"meting vóór: {db} ontbreekt (verkeerde map of de vorige release maakte "
                          "hem niet); zonder meting is er niets te bewijzen")
    core = dbs.get("core/mediaguard.db", {}).get("tabellen", {})
    if "core/mediaguard.db" in dbs:
        for tabel, n in sorted(fixture.get("rows_per_table", {}).items()):
            if not n or tabel in RUNTIME_CORE:
                continue
            gemeten = core.get(tabel, {}).get("rijen", 0)
            if gemeten < n:
                fouten.append(f"meting vóór: core/mediaguard.db {tabel} heeft {gemeten} rij(en), "
                              f"de fixture {n}: de vorige release verloor al gegevens of de "
                              "meting is onvolledig")
    if "encode/app.db" in dbs and not any(
            t.get("rijen", 0) for t in dbs["encode/app.db"].get("tabellen", {}).values()):
        fouten.append("meting vóór: encode/app.db is leeg (niet gevuld)")
    verwacht = int(fixture.get("open_overdrachten") or 0)
    if "core/mediaguard.db" in dbs and open_werk(voor) < verwacht:
        fouten.append(f"meting vóór: {open_werk(voor)} open overdracht(en) in mediaserver_pending, "
                      f"de fixture heeft er {verwacht}: de vorige release sloot ze al of de meting "
                      "is onvolledig; zonder open werk vóór de upgrade is over open werk niets "
                      "bewezen")
    return fouten


#: open werk dat de poort apart telt: (database, tabel, kolom die leeg is zolang het werk loopt)
OPEN_WERK = ("core/mediaguard.db", "mediaserver_pending", "done_at")


def open_werk(meting: dict[str, Any]) -> int:
    """Aantal open overdrachten naar de mediaserver (``done_at`` leeg) in een meting."""
    db, tabel, kolom = OPEN_WERK
    t = meting.get("databases", {}).get(db, {}).get("tabellen", {}).get(tabel)
    if not t or kolom not in t["kolommen"]:
        return 0
    i = t["kolommen"].index(kolom)
    return sum(1 for rij in [*t["per_sleutel"].values(), *t["zonder_sleutel"]] if rij[i] is None)


def open_werk_regel(voor: dict[str, Any], na: dict[str, Any]) -> tuple[str, list[str]]:
    """Regel voor het rapport en de fouten: open werk van vóór moet er na nog open staan (de
    vergelijking per rij ziet elke wijziging ook; dit maakt het open werk zichtbaar)."""
    a, b = open_werk(voor), open_werk(na)
    regel = f"Open overdrachten (mediaserver_pending zonder done_at): vóór {a}, na {b}."
    fout = f"open overdracht(en): vóór {a}, na {b}: open werk is dichtgezet of verdwenen"
    return regel, ([] if b >= a else [fout])


# ------------------------------------------------------------------------------------- vergelijken
def _uitzondering(uitz: dict[str, Any], db: str) -> dict[str, Any]:
    for patroon, regel in uitz.get("databases", {}).items():
        if fnmatch.fnmatch(db, patroon):
            return dict(regel)
    return {}


def vergelijk(voor: dict[str, Any], na: dict[str, Any],
              uitz: dict[str, Any] | None = None) -> tuple[list[str], list[str], list[str]]:
    """(fouten, toegepaste uitzonderingen, overzichtsregels). Alleen de kolommen van vóór tellen."""
    uitz = uitz or {}
    fouten: list[str] = []
    toegepast: list[str] = []
    overzicht: list[str] = []
    if not voor.get("databases"):
        # niets gemeten = niets bewezen; nooit 'niets verloren'
        fouten.append("geen enkele database gemeten vóór de upgrade: er is niets bewezen")
    for db, m_voor in sorted(voor.get("databases", {}).items()):
        regel_db = _uitzondering(uitz, db)
        if regel_db.get("hele_database"):
            toegepast.append(f"{db}: hele database vluchtig — {regel_db.get('reden', '?')}")
            continue
        m_na = na["databases"].get(db)
        if m_na is None:
            fouten.append(f"{db}: database is weg na de upgrade")
            continue
        if m_na["integriteit"] != "ok":
            fouten.append(f"{db}: integrity_check = {m_na['integriteit']}")
        for tabel, t_voor in sorted(m_voor["tabellen"].items()):
            regel = regel_db.get("tabellen", {}).get(tabel, {})
            reden = regel.get("reden", "?")
            t_na = m_na["tabellen"].get(tabel)
            if t_na is None:
                if regel.get("tabel_mag_weg"):
                    toegepast.append(f"{db} {tabel}: tabel weg — {reden}")
                else:
                    fouten.append(f"{db} {tabel}: tabel is weg ({t_voor['rijen']} rijen)")
                continue
            weg_kol = [k for k in t_voor["kolommen"] if k not in t_na["kolommen"]]
            vluchtig = set(regel.get("kolommen", []))
            for k in weg_kol:
                if k in vluchtig:
                    toegepast.append(f"{db} {tabel}.{k}: kolom weg — {reden}")
                else:
                    fouten.append(f"{db} {tabel}: kolom {k} is weg")
            idx = [(i, t_na["kolommen"].index(k)) for i, k in enumerate(t_voor["kolommen"])
                   if k in t_na["kolommen"] and k not in vluchtig]
            verloren: list[str] = []
            gewijzigd: list[str] = []
            per_rij = regel.get("rijen", [])
            if t_voor["sleutel"] and t_voor["sleutel"] == t_na["sleutel"]:
                for sleutel, rij in t_voor["per_sleutel"].items():
                    rij_na = t_na["per_sleutel"].get(sleutel)
                    if rij_na is None:
                        verloren.append(sleutel)
                        continue
                    kol = [t_voor["kolommen"][i] for i, j in idx if rij[i] != rij_na[j]]
                    eerste = str(json.loads(sleutel)[0])
                    vrij = {k for r in per_rij if fnmatch.fnmatch(eerste, r.get("sleutel", ""))
                            for k in r.get("kolommen", [])}
                    if vrij & set(kol):
                        patronen = sorted(r.get("sleutel", "") for r in per_rij
                                          if fnmatch.fnmatch(eerste, r.get("sleutel", "")))
                        toegepast.append(f"{db} {tabel}: {', '.join(sorted(vrij & set(kol)))} "
                                         f"bij sleutel {' / '.join(patronen)} niet vergeleken — "
                                         f"{reden}")
                    kol = [k for k in kol if k not in vrij]
                    if kol:
                        gewijzigd.append(f"{sleutel} ({', '.join(kol)})")
            else:  # zonder (gelijke) sleutel: elke rij van vóór moet er nog zijn (multiset)
                alle_voor = list(t_voor["per_sleutel"].values()) + t_voor["zonder_sleutel"]
                alle_na = list(t_na["per_sleutel"].values()) + t_na["zonder_sleutel"]
                pool: dict[str, int] = {}
                for rij in alle_na:
                    k = json.dumps([rij[j] for _i, j in idx])
                    pool[k] = pool.get(k, 0) + 1
                for rij in alle_voor:
                    k = json.dumps([rij[i] for i, _j in idx])
                    if pool.get(k):
                        pool[k] -= 1
                    else:
                        verloren.append(k[:120])
            nieuw = max(0, t_na["rijen"] - (t_voor["rijen"] - len(verloren)))
            if verloren:
                if regel.get("rijen_vluchtig"):
                    toegepast.append(f"{db} {tabel}: {len(verloren)} rij(en) weg — {reden}")
                else:
                    fouten.append(f"{db} {tabel}: {len(verloren)} rij(en) verloren, bv. "
                                  f"{verloren[0][:120]}")
            if gewijzigd:
                fouten.append(f"{db} {tabel}: {len(gewijzigd)} rij(en) gewijzigd, bv. "
                              f"{gewijzigd[0][:160]}")
            if vluchtig & set(t_voor["kolommen"]):
                toegepast.append(f"{db} {tabel}: kolommen {', '.join(sorted(vluchtig))} niet "
                                 f"vergeleken — {reden}")
            overzicht.append(f"| {db} | {tabel} | {t_voor['rijen']} | {t_na['rijen']} | "
                             f"{len(verloren)} | {len(gewijzigd)} | {nieuw} |")
    return fouten, sorted(set(toegepast)), overzicht


def rapport(titel: str, fouten: list[str], toegepast: list[str], overzicht: list[str],
            extra: list[str] | None = None) -> str:
    regels = [f"## {titel}", "", "**Uitkomst:** " + ("❌ verlies of fout" if fouten else
                                                      "✅ niets verloren"), ""]
    if fouten:
        regels += ["### Fouten", *[f"- {f}" for f in fouten], ""]
    if toegepast:
        regels += ["### Uitzonderingen (met reden, uit het uitzonderingenbestand)",
                   *[f"- {t}" for t in toegepast], ""]
    gevuld = [r for r in overzicht if r.split("|")[3].strip() != "0"]
    regels += ["### Tabellen met inhoud vóór de upgrade", "",
               "| database | tabel | vóór | na | verloren | gewijzigd | nieuw |",
               "|---|---|---|---|---|---|---|", *gevuld, "",
               f"Tabellen zonder inhoud vóór de upgrade: {len(overzicht) - len(gevuld)}.", ""]
    for regel in extra or []:
        regels += [regel, ""]
    return "\n".join(regels)


# ------------------------------------------------------------------------------------- docker-poort
def _docker(*args: str, check: bool = True, timeout: float | None = None) -> str:
    out = subprocess.run(["docker", *args], capture_output=True, text=True, check=False,
                         timeout=timeout)
    if check and out.returncode != 0:
        raise RuntimeError(f"docker {' '.join(args[:3])}…: {out.stderr.strip()[-800:]}")
    return out.stdout.strip()


def _http(url: str, body: dict[str, Any] | None = None,
          headers: dict[str, str] | None = None) -> tuple[int, Any, list[str]]:
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method="POST" if body is not None else "GET",
                                 headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310 - eigen container
            raw, status = resp.read(), resp.status
            cookies = resp.headers.get_all("Set-Cookie") or []
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode(errors="replace"), []
    try:
        return status, json.loads(raw), cookies
    except ValueError:
        return status, raw.decode(errors="replace"), cookies


class Poort:
    """De Docker-stappen. Alle bestanden onder ``werk`` zijn van 568 (zoals op TrueNAS); lezen en
    schrijven doet een hulpcontainer van de vorige image, zodat de host geen root nodig heeft."""

    def __init__(self, args: argparse.Namespace) -> None:
        self.a = args
        self.werk = Path(args.werkmap).resolve()
        self.naam = f"mg-upgradebewijs-{os.getpid()}"
        self.basis = f"http://127.0.0.1:{args.poort}"
        self.log: list[str] = []

    def stap(self, tekst: str) -> None:
        print(f"[upgradebewijs] {tekst}", flush=True)
        self.log.append(tekst)

    def hulp(self, *cmd: str, als: str = "0") -> str:
        """Python-opdracht in een hulpcontainer (vorige image), met de werkmap en dit script."""
        return _docker("run", "--rm", "--user", als, "--network", "none",
                       "--entrypoint", "/opt/venv/bin/python",
                       "-v", f"{self.werk}:/mnt", "-v", f"{Path(__file__).resolve().parent}:/s:ro",
                       self.a.vorige, *cmd, timeout=600)

    def shell(self, script: str) -> str:
        return _docker("run", "--rm", "--user", "0", "--network", "none", "--entrypoint", "sh",
                       "-v", f"{self.werk}:/mnt", self.a.vorige, "-c", script, timeout=600)

    def start(self, image: str, config: str = "config") -> None:
        _docker("run", "-d", "--name", self.naam, "-p", f"127.0.0.1:{self.a.poort}:30080",
                "-e", "TZ=UTC", "-e", "MG_HOST=127.0.0.1",
                "-v", f"{self.werk / config}:/config", "-v", f"{self.werk / 'work'}:/work",
                "-v", f"{self.werk / 'data'}:/data", image)
        deadline = time.monotonic() + self.a.timeout
        laatste = ""
        while time.monotonic() < deadline:
            staat = _docker("inspect", "-f", "{{.State.Status}} {{if .State.Health}}"
                            "{{.State.Health.Status}}{{end}}", self.naam)
            if staat.startswith(("exited", "dead")):
                raise RuntimeError(f"container gestopt: {staat}\n{self.logs()}")
            try:
                code, body, _ = _http(f"{self.basis}/api/v1/health")
            except OSError as exc:
                code, body = 0, str(exc)
            laatste = f"docker={staat} health={code} {str(body)[:120]}"
            if staat.endswith(" healthy") and code == 200:
                return
            time.sleep(3)
        raise RuntimeError(f"niet gezond binnen {int(self.a.timeout)} s: {laatste}\n{self.logs()}")

    def logs(self) -> str:
        out = subprocess.run(["docker", "logs", "--tail", "120", self.naam], capture_output=True,
                             text=True, check=False)
        return out.stdout + out.stderr

    def stop(self) -> None:
        subprocess.run(["docker", "stop", "-t", str(self.a.stop_timeout), self.naam],
                       capture_output=True, check=False)
        code = _docker("inspect", "-f", "{{.State.ExitCode}}", self.naam, check=False)
        _docker("rm", "-f", self.naam, check=False)
        if code != "0":
            raise RuntimeError(f"geen nette stop (exitcode {code})")

    def readback(self, fixture: dict[str, Any], versie: str | None) -> list[str]:
        """Inloggen met de oude beheerder, instellingen en de voortgang terugvinden via de API."""
        problemen: list[str] = []
        code, health, _ = _http(f"{self.basis}/api/v1/health")
        if versie and (code != 200 or health.get("version") != versie):
            problemen.append(f"health: {code} {health}; verwacht versie {versie}")
        _, setup, _ = _http(f"{self.basis}/api/v1/auth/setup-status")
        if not isinstance(setup, dict) or setup.get("needs_setup") is not False:
            problemen.append(f"core vraagt om installatie: oude database niet gebruikt ({setup})")
        code, login, cookies = _http(f"{self.basis}/api/v1/auth/login",
                                     {"username": fixture["user"], "password": fixture["password"]})
        if code != 200:
            return [*problemen, f"inloggen met de oude beheerder mislukt: {code} {login}"]
        kop = {"Cookie": "; ".join(c.split(";", 1)[0] for c in cookies)}
        sleutels = ",".join(fixture.get("settings", {}))
        if sleutels:
            _, eff, _ = _http(f"{self.basis}/api/v1/settings/effective?keys={sleutels}",
                              headers=kop)
            gevonden = {e["key"]: e["value"] for e in eff} if isinstance(eff, list) else {}
            for k, v in fixture["settings"].items():
                if gevonden.get(k) != v:
                    problemen.append(f"instelling {k}: {gevonden.get(k)!r} i.p.v. {v!r}")
        for route, merktekens in fixture.get("readback", {}).items():
            code, body, _ = _http(f"{self.basis}{route}", headers=kop)
            tekst = json.dumps(body, ensure_ascii=False)
            ontbreekt = [m for m in merktekens if m not in tekst]
            if code != 200 or ontbreekt:
                problemen.append(f"readback {route}: {code}, ontbreekt {ontbreekt[:3]}")
        return problemen

    def meet(self, config: str, uit: str) -> dict[str, Any]:
        self.hulp("/s/upgradebewijs.py", "meet", "--map", f"/mnt/{config}", "--uit", f"/mnt/{uit}")
        meting: dict[str, Any] = json.loads((self.werk / uit).read_text("utf-8"))
        return meting

    def run(self) -> tuple[list[str], str]:
        fixture_sql, fixture = kies_fixture(Path(self.a.fixtures), self.a.vorige_versie)
        uitz = json.loads(Path(self.a.uitzonderingen).read_text("utf-8"))
        self.stap(f"fixture {fixture_sql.name}: {fixture['version']}, "
                  f"{len(fixture['schema_versions'])} migraties")
        if self.werk.exists() and any(self.werk.iterdir()):
            raise RuntimeError(f"werkmap {self.werk} is niet leeg")
        for d in ("config/core", "work", "data/media", "data/downloads"):
            (self.werk / d).mkdir(parents=True, exist_ok=True)
        herstel(fixture_sql, self.werk / "config" / "core" / "mediaguard.db")
        self.shell(f"chown -R {APP_UID}:{APP_UID} /mnt/config /mnt/work /mnt/data")
        fouten: list[str] = []
        # 1. vorige release op de fixture; de AV1-database maakt hij zelf, daarna vullen
        self.stap(f"vorige release starten: {self.a.vorige}")
        self.start(self.a.vorige)
        self.stop()
        uit = self.hulp("/s/upgradebewijs.py", "vul", "--db", "/mnt/config/encode/app.db",
                        "--profiel", "encode", als=str(APP_UID))
        self.stap(f"AV1-database gevuld: {uit}")
        try:
            gevuld = json.loads(uit.splitlines()[-1]).get("toegevoegd", {}) if uit else {}
        except ValueError:
            gevuld = {}
        if not gevuld:
            raise RuntimeError(f"AV1-database niet gevuld (uitkomst: {uit[:300]!r}): zonder "
                               "gevulde database is er niets te bewijzen")
        # 2. vorige release opnieuw: accepteert de gevulde databases en vindt alles terug
        self.start(self.a.vorige)
        fouten += [f"vorige release: {p}" for p in self.readback(fixture, None)]
        self.stop()
        voor = self.meet("config", "meting-voor.json")
        # zonder volledige meting vóór is 'niets verloren' leeg: dan is de poort rood
        fouten += dekking(voor, fixture)
        # zoals een ZFS-snapshot vóór de update (terugzetten = vorige image + deze snapshot)
        self.shell("cp -a /mnt/config /mnt/config-snapshot")
        # 3. nieuwe release op dezelfde mappen
        self.stap(f"nieuwe release starten: {self.a.nieuw}")
        self.start(self.a.nieuw)
        fouten += [f"nieuwe release: {p}" for p in self.readback(fixture, self.a.versie)]
        self.stop()
        na = self.meet("config", "meting-na.json")
        f_upg, toegepast, overzicht = vergelijk(voor, na, uitz)
        regel_upg, open_upg = open_werk_regel(voor, na)
        f_upg += open_upg
        fouten += [f"upgrade: {f}" for f in f_upg]
        tekst = rapport(f"Upgradebewijs {fixture['version']} → {self.a.versie}", f_upg, toegepast,
                        overzicht, [regel_upg])
        # 4. terugzetten = vorige image + snapshot van vóór de update (contract §2.3 regel 7a)
        self.stap("terugzetten: vorige release op de snapshot van vóór de update")
        self.start(self.a.vorige, config="config-snapshot")
        fouten += [f"terugzetten: {p}" for p in self.readback(fixture, None)]
        self.stop()
        terug = self.meet("config-snapshot", "meting-terug.json")
        # dezelfde vergelijking per tabel als bij de upgrade, en ook zo in het rapport (S-P2-610:
        # het overzicht en de toegepaste uitzonderingen werden hier eerder weggegooid)
        f_terug, t_terug, o_terug = vergelijk(voor, terug, uitz)
        regel_terug, open_terug = open_werk_regel(voor, terug)
        f_terug += open_terug
        fouten += [f"terugzetten: {f}" for f in f_terug]
        tekst += "\n" + rapport("Terugzetten (vorige image + snapshot)", f_terug, t_terug, o_terug,
                                [regel_terug])
        backups = self.shell("ls -1 /mnt/config/core/backups/voor-update 2>/dev/null | head -5 "
                             "|| true")
        sets = backups or "geen (niets te migreren)"
        tekst += f"\nBack-upsets van vóór de update in core: {sets}\n"
        return fouten, tekst

    def opruimen(self) -> None:
        _docker("rm", "-f", self.naam, check=False)
        if not self.a.bewaar and self.werk.exists():
            with contextlib.suppress(RuntimeError, subprocess.TimeoutExpired):
                self.shell("rm -rf /mnt/config /mnt/config-snapshot /mnt/work /mnt/data "
                           "/mnt/meting-*.json")
            shutil.rmtree(self.werk, ignore_errors=True)


# ------------------------------------------------------------------------------- vorige release
_VERSIE = re.compile(r"(\d+)\.(\d+)\.(\d+)")
_RELEASE_TAG = re.compile(r"(\d+\.\d+\.\d+)-aio")  # kandidaat-tags (…-aio-kandidaat-…) tellen niet


def _versie(tekst: str) -> tuple[int, int, int]:
    m = _VERSIE.fullmatch(tekst)
    if not m:
        raise ValueError(f"geen versie x.y.z: {tekst!r}")
    return int(m[1]), int(m[2]), int(m[3])


def _tekst(v: tuple[int, int, int]) -> str:
    return ".".join(map(str, v))


def controleer_volgorde(vorige: dict[str, Any], tags: list[str], digest_gemeten: str | None,
                        versie: str | None = None) -> list[str]:
    """Is ``vorige-release.json`` echt de direct vorige gepubliceerde release (HARDE EIS: 'een
    gevulde database van de vorige versie', niet 'een eerdere')?

    - de image-tag is ``<versie>-aio`` en die release-tag staat in de registry;
    - de gepinde digest is de digest van die tag (niet een ander image);
    - met ``versie`` (release): vorige < versie, en er is geen gepubliceerde release tussen vorige
      en versie (vorige = de hoogste gepubliceerde onder versie; ook bij een patch op een oudere
      lijn);
    - zonder ``versie`` (elke push): vorige = de hoogste gepubliceerde release, zodat een
      vergeten bijwerking na een release direct rood is.
    """
    fouten: list[str] = []
    try:
        v_vorige = _versie(str(vorige.get("versie", "")))
    except ValueError as exc:
        return [f"vorige-release.json: {exc}"]
    image = str(vorige.get("image", ""))
    if not image.endswith(f":{vorige['versie']}-aio"):
        fouten.append(f"vorige-release.json: image {image!r} is niet de tag {vorige['versie']}-aio")
    gepubliceerd = sorted({_versie(m[1]) for t in tags if (m := _RELEASE_TAG.fullmatch(t))})
    if v_vorige not in gepubliceerd:
        fouten.append(f"vorige release {vorige['versie']}-aio staat niet in de registry "
                      "(niet gepubliceerd of niet leesbaar)")
    if digest_gemeten is None:
        fouten.append(f"digest van {vorige['versie']}-aio niet te lezen: niet vast te stellen of "
                      "het gepinde image de vorige release is")
    elif digest_gemeten != vorige.get("digest"):
        fouten.append(f"gepinde digest {vorige.get('digest')} is niet de digest van "
                      f"{vorige['versie']}-aio in de registry ({digest_gemeten})")
    if versie is not None:
        try:
            v_nieuw = _versie(versie)
        except ValueError as exc:
            return [*fouten, f"--versie: {exc}"]
        if v_vorige >= v_nieuw:
            fouten.append(f"vorige release {vorige['versie']} is niet ouder dan {versie}")
        lager = [v for v in gepubliceerd if v < v_nieuw]
        if lager and lager[-1] != v_vorige:
            fouten.append(f"vorige-release.json noemt {vorige['versie']}, maar de direct vorige "
                          f"gepubliceerde release vóór {versie} is "
                          f"{_tekst(lager[-1])}: zet die versie, digest en fixture")
    elif gepubliceerd and gepubliceerd[-1] != v_vorige:
        fouten.append(f"vorige-release.json noemt {vorige['versie']}, maar de laatst "
                      f"gepubliceerde release is {_tekst(gepubliceerd[-1])}: zet die "
                      "versie, digest en fixture (scripts/make-aio-fixture.py op de releasecommit)")
    return fouten


def _registry_get(url: str, headers: dict[str, str]) -> tuple[bytes, Any]:
    """GET met herhaling (2/4/8 s); een blijvende fout wordt doorgegeven (dan rood, nooit groen)."""
    laatste: Exception | None = None
    for wacht in (0, 2, 4, 8):
        time.sleep(wacht)
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310 - vaste registry
                return resp.read(), resp.headers
        except urllib.error.HTTPError as exc:
            if exc.code < 500 and exc.code != 429:
                raise
            laatste = exc
        except OSError as exc:
            laatste = exc
    assert laatste is not None
    raise laatste


def registry_meting(image: str) -> tuple[list[str], str | None]:
    """Tags van de image-repository en de digest van de tag uit ``image`` (anoniem lezen; de
    pakketten zijn publiek, contract §2.1). Alleen ghcr.io."""
    m = re.fullmatch(r"ghcr\.io/([a-z0-9._/-]+):([A-Za-z0-9._-]+)", image)
    if not m:
        raise ValueError(f"alleen ghcr.io/<eigenaar>/<naam>:<tag>: {image!r}")
    repo, tag = m[1], m[2]
    body, _h = _registry_get(f"https://ghcr.io/token?scope=repository:{repo}:pull", {})
    token = json.loads(body)["token"]
    kop = {"Authorization": f"Bearer {token}"}
    tags: list[str] = []
    url: str | None = f"https://ghcr.io/v2/{repo}/tags/list?n=1000"
    while url:
        body, h = _registry_get(url, kop)
        tags += json.loads(body).get("tags") or []
        volgende = re.search(r'<([^>]+)>;\s*rel="next"', h.get("Link") or "")
        url = f"https://ghcr.io{volgende[1]}" if volgende else None
    accept = ", ".join(("application/vnd.oci.image.index.v1+json",
                        "application/vnd.docker.distribution.manifest.list.v2+json",
                        "application/vnd.oci.image.manifest.v1+json",
                        "application/vnd.docker.distribution.manifest.v2+json"))
    digest: str | None = None
    if tag in tags:
        body, h = _registry_get(f"https://ghcr.io/v2/{repo}/manifests/{tag}",
                                {**kop, "Accept": accept})
        digest = h.get("Docker-Content-Digest") or f"sha256:{hashlib.sha256(body).hexdigest()}"
    return tags, digest


def kies_fixture(map_: Path, versie: str) -> tuple[Path, dict[str, Any]]:
    """De fixture van precies de vorige release (``core-<versie>.json``): de HARDE EIS vraagt de
    direct vorige uitgebrachte versie, niet 'een eerdere'."""
    meta = map_ / f"core-{versie}.json"
    if not meta.is_file():
        raise SystemExit(f"geen fixture {meta.name} voor de vorige release {versie}: maak hem met "
                         "scripts/make-aio-fixture.py op de releasecommit van die versie")
    return meta.with_suffix(".sql"), json.loads(meta.read_text("utf-8"))


def herstel(sql: Path, db: Path) -> None:
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db)
    try:
        conn.executescript(sql.read_text("utf-8"))
        conn.commit()
    finally:
        conn.close()


# ------------------------------------------------------------------------------------- main
def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=(__doc__ or "").split("\n", 1)[0])
    sub = p.add_subparsers(dest="opdracht", required=True)
    v = sub.add_parser("vul")
    v.add_argument("--db", required=True, type=Path)
    v.add_argument("--profiel", choices=sorted(OVERSLAAN), required=True)
    v.add_argument("--rijen", type=int, default=2)
    m = sub.add_parser("meet")
    m.add_argument("--map", required=True, type=Path)
    m.add_argument("--uit", required=True, type=Path)
    c = sub.add_parser("vergelijk")
    c.add_argument("--voor", required=True, type=Path)
    c.add_argument("--na", required=True, type=Path)
    c.add_argument("--uitzonderingen", type=Path, default=UITZONDERINGEN)
    c.add_argument("--rapport", type=Path)
    o = sub.add_parser("volgorde", help="is vorige-release.json de direct vorige gepubliceerde "
                       "release (registry: tag en digest)?")
    o.add_argument("--vorige-release", type=Path,
                   default=ROOT / "deploy" / "upgradebewijs" / "vorige-release.json")
    o.add_argument("--versie", help="versie van de kandidaat (release); zonder: elke push")
    g = sub.add_parser("poort")
    g.add_argument("--vorige", required=True, help="image van de vorige release (met @sha256)")
    g.add_argument("--vorige-versie", required=True)
    g.add_argument("--nieuw", required=True, help="kandidaat-image van deze release")
    g.add_argument("--versie", required=True, help="versie van de kandidaat")
    g.add_argument("--fixtures", default=str(FIXTURES))
    g.add_argument("--uitzonderingen", default=str(UITZONDERINGEN))
    g.add_argument("--werkmap", default=str(Path(tempfile.gettempdir()) / f"mg-upg-{os.getpid()}"))
    g.add_argument("--poort", type=int, default=30080)
    g.add_argument("--timeout", type=float, default=600)
    g.add_argument("--stop-timeout", type=int, default=90)
    g.add_argument("--rapport", type=Path)
    g.add_argument("--bewaar", action="store_true", help="werkmap laten staan")
    a = p.parse_args(argv)
    if a.opdracht == "vul":
        print(json.dumps(vul(a.db, a.profiel, a.rijen), ensure_ascii=False))
        return 0
    if a.opdracht == "meet":
        a.uit.write_text(json.dumps(meet(a.map), ensure_ascii=False), encoding="utf-8")
        return 0
    if a.opdracht == "volgorde":
        vorige = json.loads(a.vorige_release.read_text("utf-8"))
        try:
            tags, digest = registry_meting(str(vorige.get("image", "")))
        except (OSError, ValueError, KeyError) as exc:
            fouten = [f"registry niet te lezen ({exc}): niet vast te stellen of dit de vorige "
                      "release is; er wordt niets als bewezen gemeld"]
        else:
            fouten = controleer_volgorde(vorige, tags, digest, a.versie)
        if fouten:
            print("\n".join(f"❌ {f}" for f in fouten), flush=True)
            return 1
        print(f"vorige release {vorige['versie']}-aio ({vorige['digest'][:19]}…) is de direct "
              f"vorige gepubliceerde release" + (f" vóór {a.versie}" if a.versie else ""))
        return 0
    if a.opdracht == "vergelijk":
        uitz = json.loads(a.uitzonderingen.read_text("utf-8")) if a.uitzonderingen else {}
        fouten, toegepast, overzicht = vergelijk(json.loads(a.voor.read_text("utf-8")),
                                                 json.loads(a.na.read_text("utf-8")), uitz)
        tekst = rapport("Upgradebewijs", fouten, toegepast, overzicht)
    else:
        if not re.fullmatch(r"\S+@sha256:[0-9a-f]{64}", a.vorige):
            p.error("--vorige moet een image met @sha256-digest zijn (de echte vorige release)")
        poort = Poort(a)
        try:
            fouten, tekst = poort.run()
        except (RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
            fouten, tekst = [str(exc)], f"## Upgradebewijs\n\n❌ afgebroken: {exc}\n"
        finally:
            poort.opruimen()
        tekst += "\n" + "\n".join(f"- ❌ {f}" for f in fouten) if fouten else ""
    print(tekst, flush=True)
    for doel in (a.rapport, os.environ.get("GITHUB_STEP_SUMMARY")):
        if doel:
            with open(doel, "a", encoding="utf-8") as fh:
                fh.write(tekst + "\n")
    return 1 if fouten else 0


if __name__ == "__main__":
    sys.exit(main())
