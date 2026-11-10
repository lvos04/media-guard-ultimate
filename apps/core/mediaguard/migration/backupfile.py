"""Native Sonarr/Radarr-back-ups (.zip): validatie, veilige extractie, read-only
inspectie en het bouwen van een SCHADUWKOPIE voor restore in een doel-fork.

Het originele zipbestand wordt nooit gewijzigd. Alle bewerkingen gebeuren op een kopie
in een eigen tijdelijke map; bestandsnamen uit de zip worden nooit als pad gebruikt
(zip-slip onmogelijk), en de uitgepakte grootte is begrensd (zip-bom).

Kolommen die in de schaduwkopie worden uitgezet (Sonarr v3/v4 en Radarr v3-v5
gebruiken dezelfde namen; alleen aanwezige kolommen worden aangeraakt):

- ``Indexers.EnableRss`` -> 0, ``Indexers.EnableAutomaticSearch`` -> 0
  (``EnableInteractiveSearch`` blijft ongewijzigd: handmatig zoeken grijpt nooit vanzelf)
- ``DownloadClients.Enable`` -> 0
- ``ImportLists``: ``EnableAutomaticAdd`` (Sonarr), ``Enabled``/``EnableAuto`` (Radarr),
  ``SearchForMissingEpisodes`` (Sonarr v4), ``SearchOnAdd`` (Radarr) -> 0
- ``Config``-sleutel ``rsssyncinterval`` -> ``'0'`` (0 = RSS-sync uit)

Padherschrijving (optioneel, per prefix): ``RootFolders.Path``, ``Series.Path`` /
``Movies.Path``, ``ImportLists.RootFolderPath``, ``RemotePathMappings.LocalPath``.
Relatieve bestandspaden (EpisodeFiles/MovieFiles.RelativePath) en downloadcategorieën
blijven ongewijzigd.

``config.xml`` in de kopie krijgt de API-sleutel, poort en URL-base van de DOEL-instantie,
zodat de fork na restore bereikbaar blijft met zijn eigen sleutel.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import re
import shutil
import sqlite3
import tempfile
import zipfile
from collections.abc import Iterator
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit

DB_NAMES = {"sonarr.db": "sonarr", "radarr.db": "radarr"}
SHADOW_COLUMNS: dict[str, tuple[str, ...]] = {
    "Indexers": ("EnableRss", "EnableAutomaticSearch"),
    "DownloadClients": ("Enable",),
    "ImportLists": ("EnableAutomaticAdd", "Enabled", "EnableAuto", "SearchForMissingEpisodes",
                    "SearchOnAdd"),
}
SHADOW_CONFIG = {"rsssyncinterval": "0"}
_VERSION_IN_NAME = re.compile(r"_v(\d+(?:\.\d+){1,3})_")


class BackupInvalid(ValueError):
    """Bestand is geen bruikbare Sonarr/Radarr-back-up."""


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _member_safe(name: str) -> bool:
    if not name or "\\" in name or name.startswith("/") or re.match(r"^[A-Za-z]:", name):
        return False
    return ".." not in PurePosixPath(name).parts


def _copy_member(zf: zipfile.ZipFile, info: zipfile.ZipInfo, dest: Path, limit: int) -> int:
    written = 0
    with zf.open(info) as src, dest.open("wb") as out:
        for chunk in iter(lambda: src.read(1024 * 1024), b""):
            written += len(chunk)
            if written > limit:
                raise BackupInvalid("Uitgepakte back-up is groter dan toegestaan (zip-bom?)")
            out.write(chunk)
    return written


@contextlib.contextmanager
def extracted(zip_path: Path, max_uncompressed: int) -> Iterator[dict[str, Any]]:
    """Valideert de zip en pakt alleen de database + config.xml uit naar een eigen
    tijdelijke map (vaste bestandsnamen)."""
    try:
        zf = zipfile.ZipFile(zip_path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise BackupInvalid(f"Geen geldig zipbestand: {exc}") from exc
    tmp = Path(tempfile.mkdtemp(prefix="mg-backup-", dir=zip_path.parent))
    try:
        with zf:
            infos = zf.infolist()
            unsafe = [i.filename for i in infos if not _member_safe(i.filename)]
            if unsafe:
                raise BackupInvalid(f"Onveilige paden in zip geweigerd: {unsafe[:3]}")
            if sum(i.file_size for i in infos) > max_uncompressed:
                raise BackupInvalid("Uitgepakte back-up is groter dan toegestaan")
            bad = zf.testzip()
            if bad is not None:
                raise BackupInvalid(f"Zip beschadigd (CRC-fout in {bad})")
            dbs = [i for i in infos if PurePosixPath(i.filename).name.lower() in DB_NAMES
                   and len(PurePosixPath(i.filename).parts) == 1]
            if len(dbs) != 1:
                raise BackupInvalid("Zip bevat geen sonarr.db of radarr.db (geen Sonarr/Radarr-"
                                    "back-up, of een PostgreSQL-installatie)")
            db_info = dbs[0]
            app = DB_NAMES[PurePosixPath(db_info.filename).name.lower()]
            db_path = tmp / "source.db"
            _copy_member(zf, db_info, db_path, max_uncompressed)
            cfg = next((i for i in infos if i.filename.lower() == "config.xml"), None)
            config_xml = None
            if cfg is not None:
                if cfg.file_size > 1024 * 1024:
                    raise BackupInvalid("config.xml is onrealistisch groot")
                config_xml = zf.read(cfg).decode("utf-8", errors="replace")
            yield {"app": app, "db_path": db_path, "db_member": db_info.filename,
                   "config_xml": config_xml, "members": [i.filename for i in infos]}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def open_ro(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def tables(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')}


REQUIRED_TABLES = {"sonarr": {"Series", "Episodes", "Indexers", "DownloadClients", "RootFolders",
                              "QualityProfiles", "Tags"},
                   "radarr": {"Movies", "Indexers", "DownloadClients", "RootFolders",
                              "QualityProfiles", "Tags"}}
COUNT_TABLES = ("Series", "Episodes", "EpisodeFiles", "Movies", "MovieFiles", "Indexers",
                "DownloadClients", "ImportLists", "Tags", "QualityProfiles", "CustomFormats",
                "RootFolders", "RemotePathMappings", "History")


def config_values(config_xml: str | None) -> dict[str, str]:
    """Leest eenvoudige <Sleutel>waarde</Sleutel>-paren (regex, geen XML-entiteiten)."""
    if not config_xml:
        return {}
    return {m.group(1): m.group(2).strip() for m in
            re.finditer(r"<([A-Za-z]+)>([^<]{0,500})</\1>", config_xml)}


def inspect_backup(zip_path: Path, max_uncompressed: int,
                   name: str | None = None) -> dict[str, Any]:
    """Volledige read-only controle ('restore-test op geïsoleerde kopie'): zip-integriteit,
    database aanwezig, SQLite integrity_check, schema, tellingen, config.xml."""
    with extracted(zip_path, max_uncompressed) as ex:
        app = ex["app"]
        try:
            conn = open_ro(ex["db_path"])
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            names = tables(conn)
        except sqlite3.DatabaseError as exc:
            raise BackupInvalid(f"Database in back-up is geen geldige SQLite: {exc}") from exc
        try:
            missing = REQUIRED_TABLES[app] - names
            if missing:
                raise BackupInvalid(f"Geen {app}-database: tabellen ontbreken "
                                    f"({', '.join(sorted(missing))})")
            counts = {t: int(conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0])
                      for t in COUNT_TABLES if t in names}
            schema_version = None
            if "VersionInfo" in names:
                schema_version = conn.execute("SELECT MAX(Version) FROM VersionInfo").fetchone()[0]
        finally:
            conn.close()
        cfg = config_values(ex["config_xml"])
        version = None
        m = _VERSION_IN_NAME.search(name or zip_path.name)
        if m:
            version = m.group(1)
        return {"app": app, "integrity": integrity, "ok": integrity == "ok",
                "schema_version": schema_version, "counts": counts, "app_version": version,
                "has_config_xml": ex["config_xml"] is not None,
                "instance_name": cfg.get("InstanceName"), "url_base": cfg.get("UrlBase"),
                "port": cfg.get("Port"), "members": ex["members"]}


# -- paden ---------------------------------------------------------------------------------
def normalize_rules(rules: list[dict[str, str]] | None) -> list[tuple[str, str]]:
    out = []
    for rule in rules or []:
        src = str(rule.get("from", "")).strip()
        dst = str(rule.get("to", "")).strip()
        if not src.startswith("/") or not dst.startswith("/"):
            raise ValueError("Padregels moeten absolute paden zijn (beginnend met '/')")
        out.append((src.rstrip("/") or "/", dst.rstrip("/") or "/"))
    return sorted(out, key=lambda r: len(r[0]), reverse=True)


def remap(path: str | None, rules: list[tuple[str, str]]) -> str | None:
    if not path:
        return path
    trailing = path.endswith("/") and len(path) > 1
    bare = path.rstrip("/") or "/"
    for src, dst in rules:
        if bare == src or bare.startswith(src.rstrip("/") + "/"):
            rest = bare[len(src):].lstrip("/")
            new = (dst.rstrip("/") + "/" + rest) if rest else dst
            return new + ("/" if trailing and not new.endswith("/") else "")
    return path


def rule_for(path: str, rules: list[tuple[str, str]]) -> str | None:
    bare = path.rstrip("/") or "/"
    for src, _ in rules:
        if bare == src or bare.startswith(src.rstrip("/") + "/"):
            return src
    return None


# -- schaduwkopie --------------------------------------------------------------------------
def _patch_db(db_path: Path, rules: list[tuple[str, str]]) -> dict[str, Any]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    state: dict[str, Any] = {"indexers": {}, "download_clients": {}, "import_lists": {},
                             "config": {}}
    remapped: dict[str, int] = {}
    key_of = {"Indexers": "indexers", "DownloadClients": "download_clients",
              "ImportLists": "import_lists"}
    try:
        names = tables(conn)
        conn.execute("BEGIN")
        for table, wanted in SHADOW_COLUMNS.items():
            if table not in names:
                continue
            cols = [c for c in wanted if c in columns(conn, table)]
            if not cols:
                continue
            for row in conn.execute(f'SELECT Id, Name, {",".join(cols)} FROM "{table}"'):
                state[key_of[table]][str(row["Name"])] = {
                    "id": int(row["Id"]), **{_camel(c): bool(row[c]) for c in cols}}
            conn.execute(f'UPDATE "{table}" SET ' + ",".join(f"{c}=0" for c in cols))
        if "Config" in names:
            for key, value in SHADOW_CONFIG.items():
                row = conn.execute("SELECT Value FROM Config WHERE lower(Key)=?",
                                   (key,)).fetchone()
                state["config"][key] = None if row is None else row["Value"]
                if row is None:
                    conn.execute("INSERT INTO Config(Key, Value) VALUES (?,?)", (key, value))
                else:
                    conn.execute("UPDATE Config SET Value=? WHERE lower(Key)=?", (value, key))
        if rules:
            targets = [("RootFolders", "Path"), ("Series", "Path"), ("Movies", "Path"),
                       ("ImportLists", "RootFolderPath"), ("RemotePathMappings", "LocalPath"),
                       # Radarr v4+: collecties hebben een eigen rootmap; niet herschreven geeft
                       # health-fout 'Missing root folder for movie collection' (gemeten, 6.4)
                       ("Collections", "RootFolderPath")]
            for table, col in targets:
                if table not in names or col not in columns(conn, table):
                    continue
                n = 0
                for row in conn.execute(f'SELECT Id, "{col}" AS p FROM "{table}"').fetchall():
                    new = remap(row["p"], rules)
                    if new != row["p"]:
                        conn.execute(f'UPDATE "{table}" SET "{col}"=? WHERE Id=?',
                                     (new, row["Id"]))
                        n += 1
                remapped[f"{table}.{col}"] = n
        conn.execute("COMMIT")
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        conn.close()
    if integrity != "ok":
        raise BackupInvalid(f"Schaduwkopie faalt integrity_check: {integrity}")
    state["remapped"] = remapped
    return state


def _camel(col: str) -> str:
    return col[0].lower() + col[1:]


def rewrite_config_xml(xml: str, api_key: str, target_url: str) -> str:
    parts = urlsplit(target_url)
    port = parts.port or (443 if parts.scheme == "https" else 80)
    values = {"ApiKey": api_key, "Port": str(port), "UrlBase": parts.path.rstrip("/"),
              "EnableSsl": "False"}
    out = xml
    for key, value in values.items():
        pattern = re.compile(rf"<{key}>[^<]*</{key}>")
        if pattern.search(out):
            out = pattern.sub(f"<{key}>{value}</{key}>", out)
        else:
            out = out.replace("</Config>", f"  <{key}>{value}</{key}>\n</Config>")
    return out


def build_shadow_zip(src_zip: Path, dest_zip: Path, *, rules: list[tuple[str, str]],
                     target_api_key: str, target_url: str,
                     max_uncompressed: int) -> dict[str, Any]:
    """Maakt van een KOPIE van de back-up een schaduw-back-up voor het doel."""
    before = sha256_file(src_zip)
    dest_zip.parent.mkdir(parents=True, exist_ok=True)
    with extracted(src_zip, max_uncompressed) as ex:
        state = _patch_db(ex["db_path"], rules)
        xml = ex["config_xml"] or "<Config>\n</Config>\n"
        new_xml = rewrite_config_xml(xml, target_api_key, target_url)
        tmp_zip = dest_zip.with_suffix(".partial")
        with zipfile.ZipFile(tmp_zip, "w", compression=zipfile.ZIP_DEFLATED) as out:
            out.write(ex["db_path"], arcname=ex["db_member"])
            out.writestr("config.xml", new_xml)
        os.replace(tmp_zip, dest_zip)
    os.chmod(dest_zip, 0o600)
    if sha256_file(src_zip) != before:  # pragma: no cover - verdedigingslinie
        raise BackupInvalid("Originele back-up is onverwacht gewijzigd")
    state["original_sha256"] = before
    state["shadow_sha256"] = sha256_file(dest_zip)
    return state
