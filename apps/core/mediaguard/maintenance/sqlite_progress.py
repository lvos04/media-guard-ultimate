"""Inhoudsbewijs voor een opvolger wanneer SQLite de oude WAL al heeft hergebruikt."""

import hashlib
import json
import sqlite3
from collections.abc import Iterable


def row_bytes(row: Iterable) -> bytes:
    return json.dumps(tuple(row), default=lambda blob: {"blob": blob.hex()}).encode() + b"\n"


def _digest(row: Iterable) -> str:
    return hashlib.sha256(row_bytes(row)).hexdigest()


def _table_rows(db: sqlite3.Connection, name: str, without_rowid: int,
                position: dict) -> tuple[list[str], Iterable[tuple]]:
    table = '"' + name.replace('"', '""') + '"'
    columns = [r[0].lower() for r in db.execute("SELECT name FROM pragma_table_xinfo(?)", (name,))]
    if without_rowid:
        sql = f"SELECT * FROM {table} NOT INDEXED"  # noqa: S608
        return columns, db.execute(sql) if position["rijen"] else ()
    rowid = next(r for r in ("rowid", "_rowid_", "oid") if r not in columns)
    sql = f"SELECT {rowid},* FROM {table} NOT INDEXED"  # noqa: S608
    if not position["rijen"]:
        return [rowid, *columns], ()
    limit = position["hoogste"]
    rows = db.execute(sql) if limit is None else db.execute(sql + f" WHERE {rowid} <= ?", (limit,))
    return [rowid, *columns], rows


def _table_proof(db: sqlite3.Connection, name: str, without_rowid: int, position: dict) -> dict:
    columns, rows = _table_rows(db, name, without_rowid, position)
    progress = {"job": "percent", "jobs": "progress"}.get(name)
    if progress in columns and not without_rowid:
        index = columns.index(progress)
        tasks = {}
        for row in rows:
            value = row[index]
            if type(value) not in (int, float):
                value = _digest((value,))
            tasks[str(row[0])] = [_digest(row[:index] + row[index + 1:]), value]
        return {"taken": tasks}
    if name in ("outbox_events", "outbox_source", "outbox_cursor_history", "outbox_deletions"):
        return {"rijen": {str(r[0]): _digest(r) for r in rows}}
    if name == "outbox_retention" and {"deleted_through", "revision"} <= set(columns):
        through, revision = columns.index("deleted_through"), columns.index("revision")
        fixed = [i for i in range(len(columns)) if i not in (through, revision)]
        return {"retentie": [[r[through], r[revision], _digest(r[i] for i in fixed)] for r in rows]}
    if name == "sqlite_sequence":
        return {"tellers": {r[-2]: r[-1] for r in rows}}
    digest = hashlib.sha256()
    for row in rows:
        digest.update(row_bytes(row))
    return {"hash": digest.hexdigest()}


def content_proof(db: sqlite3.Connection, tables: dict) -> dict:
    """Bewaar alleen hashes, rijposities en tellers. Nieuwe tabellen en rijen mogen volgen."""
    schema = {f"{row[0]}:{row[1]}": _digest(row) for row in db.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_schema ORDER BY type,name")}
    types = dict(db.execute("SELECT name,wr FROM pragma_table_list WHERE schema='main'"))
    return {
        "instellingen": [db.execute(f"PRAGMA {p}").fetchone()[0]
                         for p in ("user_version", "application_id", "encoding")],
        "schema": schema,
        "tabellen": {name: _table_proof(db, name, types[name], pos)
                     for name, pos in tables.items() if name in types},
    }


def contains_commit(db: sqlite3.Connection, old: dict, positions: dict, *,
                    replayed: bool = False, advanced_tasks: bool = False) -> bool:
    new = content_proof(db, positions)
    if (new["instellingen"] != old["instellingen"]
            or not old["schema"].items() <= new["schema"].items()
            or new["tabellen"].keys() != old["tabellen"].keys()):
        return False
    retired = {}
    revisions = set()
    retention = old["tabellen"].get("outbox_retention", {}).get("retentie")
    if retention:
        current = new["tabellen"]["outbox_retention"]["retentie"]
        if len(retention) != 1 or len(current) != 1:
            return False
        through, revision, fingerprint = retention[0]
        end, latest, actual = current[0]
        if actual != fingerprint or end < through or latest < revision:
            return False
        if latest > revision and "table:outbox_deletions" in new["schema"]:
            deletions = db.execute("SELECT event_id, revision FROM outbox_deletions "
                                   "WHERE revision > ? AND revision <= ?", (revision, latest))
            retired = {str(event_id): rev for event_id, rev in deletions}
            # Elk vervallen revisiekenmerk hoort bij een expliciet gemeten verwijdering.
            revisions = {str(rev - 1) for rev in retired.values()}
    origins = old["tabellen"].get("outbox_source", {}).get("rijen", {})
    sources = new["tabellen"].get("outbox_source", {}).get("rijen", {})
    for name, before in old["tabellen"].items():
        after = new["tabellen"][name]
        if before == after:
            continue
        if "taken" in before:
            for key, (identity, progress) in before["taken"].items():
                task = after["taken"].get(key)
                if task is None or task[0] != identity:
                    return False
                same = type(task[1]) is type(progress) and task[1] == progress
                if not same and not (
                    (replayed or advanced_tasks) and type(task[1]) in (int, float)
                    and type(progress) in (int, float)
                    and task[1] > progress
                ):
                    return False
        elif "tellers" in before:
            if any(after["tellers"].get(k, -1) < v for k, v in before["tellers"].items()):
                return False
        elif "rijen" in before:
            for key, digest in before["rijen"].items():
                observed = after["rijen"].get(key)
                if observed == digest:
                    continue
                # Bind een verwijdering ook aan de bij het klaarzetten gemeten bronidentiteit.
                retired_event = observed is None and key in retired
                if (name == "outbox_events" and (retired_event or (replayed and observed))
                        and key in origins and sources.get(key) == origins[key]):
                    continue
                if observed is None and name == "outbox_cursor_history" and key in revisions:
                    continue
                return False
        elif name != "outbox_retention" or not retention or not retired:
            return False
    return True
