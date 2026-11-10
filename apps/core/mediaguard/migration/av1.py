"""P8: import van de oude standalone AV1-app (services/encoder-schema) — alleen-lezen.

De SQLite van de oude app wordt eerst naar /config/backups/av1/ GEKOPIEERD (inclusief
eventuele -wal/-shm), en alleen de kopie wordt geopend (``mode=ro``). Het origineel wordt
nooit geopend voor schrijven, dus ook nooit door een WAL-checkpoint aangeraakt.

Resultaat: ``av1_provenance_map`` met de sleutel ``source_instance + source_kind +
source_id -> mediaguard_ref`` (``<instance_id>:<episode|movie>:<provider_file_id>``),
plus pad/grootte/mtime/device/inode en de oorspronkelijke conversie-uitkomst, zodat
eerdere AV1-conversies traceerbaar blijven. Bestanden die al AV1 zijn zonder bekende
herkomst krijgen status ``av1_unknown_source`` (C1: aparte bewijssporen).
"""

from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mediaguard.db import dumps, utcnow
from mediaguard.migration.backupfile import normalize_rules, remap, sha256_file

if TYPE_CHECKING:
    from mediaguard.context import AppContext

REQUIRED = {"bestand", "job", "library"}
JOB_STATUS = {"klaar": "converted", "overgeslagen": "skipped", "mislukt": "failed",
              "afgebroken": "cancelled", "wacht": "queued", "bezig": "in_progress",
              "verifieren": "in_progress"}


def _copy_db(ctx: AppContext, src: Path) -> tuple[Path, str]:
    digest = sha256_file(src)
    dest_dir = ctx.settings.backup_dir / "av1" / digest[:16]
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / "av1.db"
    if not dest.exists():
        shutil.copy2(src, dest)
        for suffix in ("-wal", "-shm"):
            side = Path(str(src) + suffix)
            if side.exists():
                shutil.copy2(side, Path(str(dest) + suffix))
    return dest, digest


def _lib_lookup(ctx: AppContext, path: str) -> dict[str, Any] | None:
    row = ctx.db.fetch_one("SELECT id, instance_id, kind, provider_id FROM lib_files WHERE "
                           "path=? ORDER BY id LIMIT 1", (path,))
    return dict(row) if row else None


def _is_av1(probe: str | None) -> bool:
    if not probe:
        return False
    try:
        data = json.loads(probe)
    except ValueError:
        return "av1" in probe.lower()
    streams = data.get("streams") if isinstance(data, dict) else None
    return any(str(s.get("codec_name", "")).lower() == "av1" for s in streams or []
               if isinstance(s, dict) and s.get("codec_type", "video") == "video")


def import_av1_db(ctx: AppContext, db_file: Path, *, source_instance: str,
                  path_map: list[dict[str, str]] | None, actor: str) -> dict[str, Any]:
    if not db_file.is_file():
        raise ValueError(f"AV1-database niet gevonden: {db_file}")
    rules = normalize_rules(path_map)
    copy, digest = _copy_db(ctx, db_file)
    conn = sqlite3.connect(f"file:{copy}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        try:
            names = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
        except sqlite3.DatabaseError as exc:
            conn.close()
            shutil.rmtree(copy.parent, ignore_errors=True)
            raise ValueError(f"Geen geldige SQLite-database: {exc}") from exc
        if not names >= REQUIRED:
            raise ValueError("Geen database van de AV1-app (tabellen bestand/job/library "
                             "ontbreken)")
        jcols = {r[1] for r in conn.execute("PRAGMA table_info(job)")}
        has_tx = "vervang_transactie" in names
        has_ver = "verificatie" in names
        has_save = "besparing" in names
        now = utcnow()
        import_id = ctx.db.execute_insert(
            "INSERT INTO av1_imports(source_instance,file_path,sha256,path_map_json,created_at,"
            "created_by) VALUES (?,?,?,?,?,?)",
            (source_instance, str(db_file), digest, dumps(path_map or []), now, actor))
        counts = {"jobs": 0, "files": 0, "matched": 0, "unmatched": 0, "av1_unknown_source": 0}
        converted_files: set[int] = set()
        rows: list[tuple[Any, ...]] = []

        def add(kind: str, sid: int, status: str, path: str | None, size: Any, mtime: Any,
                device: Any, inode: Any, data: dict[str, Any]) -> None:
            mapped = remap(path, rules) if path else None
            hit = _lib_lookup(ctx, mapped) if mapped else None
            ref = f"{hit['instance_id']}:{hit['kind']}:{hit['provider_id']}" if hit else None
            counts["matched" if hit else "unmatched"] += 1
            rows.append((source_instance, kind, sid, ref, hit["instance_id"] if hit else None,
                         hit["id"] if hit else None, status, path, mapped, size, mtime, device,
                         inode, dumps(data), import_id, now))

        select = ["j.id", "j.status", "j.bestand_id", "j.baan", "j.bron_grootte",
                  "j.bron_mtime", "j.bron_device", "j.bron_inode", "j.resultaat_grootte",
                  "j.fout", "j.klaar_op", "b.pad", "b.grootte", "b.mtime"]
        select += [f"j.{c}" for c in ("mg_media_ref", "mg_inst") if c in jcols]
        for j in conn.execute(f"SELECT {', '.join(select)} FROM job j JOIN bestand b "
                              "ON b.id = j.bestand_id ORDER BY j.id"):
            data: dict[str, Any] = {"status_raw": j["status"], "lane": j["baan"],
                                    "result_size": j["resultaat_grootte"],
                                    "source_size": j["bron_grootte"], "error": j["fout"],
                                    "finished_at": j["klaar_op"]}
            if has_tx:
                tx = conn.execute("SELECT status, resultaat_sha256, toelichting FROM "
                                  "vervang_transactie WHERE job_id=?", (j["id"],)).fetchone()
                if tx:
                    data["replacement"] = dict(tx)
            if has_ver:
                v = conn.execute("SELECT geslaagd, toelichting FROM verificatie WHERE job_id=?",
                                 (j["id"],)).fetchone()
                if v:
                    data["verification"] = dict(v)
            if has_save:
                s = conn.execute("SELECT SUM(bytes_voor - bytes_na) FROM besparing WHERE "
                                 "job_id=?", (j["id"],)).fetchone()[0]
                data["saved_bytes"] = s
            status = JOB_STATUS.get(str(j["status"]), str(j["status"]))
            if status == "converted":
                converted_files.add(int(j["bestand_id"]))
            add("job", int(j["id"]), status, j["pad"], j["bron_grootte"] or j["grootte"],
                j["bron_mtime"] or j["mtime"], j["bron_device"], j["bron_inode"], data)
            counts["jobs"] += 1
        for b in conn.execute("SELECT id, pad, grootte, mtime, categorie, probe FROM bestand"):
            if int(b["id"]) in converted_files or not _is_av1(b["probe"]):
                continue
            add("bestand", int(b["id"]), "av1_unknown_source", b["pad"], b["grootte"],
                b["mtime"], None, None, {"category": b["categorie"]})
            counts["files"] += 1
            counts["av1_unknown_source"] += 1
    finally:
        conn.close()
    with ctx.db.transaction() as tx:
        tx.executemany(
            "INSERT INTO av1_provenance_map(source_instance,source_kind,source_id,mediaguard_ref,"
            "instance_id,lib_file_id,status,path,mapped_path,size,mtime,device,inode,data_json,"
            "import_id,imported_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT("
            "source_instance,source_kind,source_id) DO UPDATE SET "
            "mediaguard_ref=excluded.mediaguard_ref, instance_id=excluded.instance_id, "
            "lib_file_id=excluded.lib_file_id, status=excluded.status, path=excluded.path, "
            "mapped_path=excluded.mapped_path, size=excluded.size, mtime=excluded.mtime, "
            "device=excluded.device, inode=excluded.inode, data_json=excluded.data_json, "
            "import_id=excluded.import_id, imported_at=excluded.imported_at", rows)
        tx.execute("UPDATE av1_imports SET counts_json=? WHERE id=?", (dumps(counts), import_id))
    return {"import_id": import_id, "sha256": digest, "counts": counts}
