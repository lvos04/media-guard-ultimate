"""Opslag van gevolgde aanvragen en hun tijdlijn."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from mediaguard.db import dumps, loads, utcnow

if TYPE_CHECKING:
    from mediaguard.context import AppContext

STAGE_NL = {
    "requested": "Aangevraagd",
    "adding": "Wordt toegevoegd aan Sonarr/Radarr",
    "searching": "Wordt gezocht",
    "downloading": "Wordt gedownload",
    "importing": "Wordt geïmporteerd",
    "checking": "Wordt gecontroleerd",
    "waiting_release": "Nog niet verschenen",
    "not_found": "Nog nergens te vinden",
    "delivered": "Geleverd",
    "stopped": "Gestopt",
}
STOP_NL = {
    "user": "Gestopt met de knop Stop",
    "seerr_deleted": "Aanvraag is in Seerr verwijderd of afgewezen; MediaGuard volgt hem niet "
                     "meer",
    "removed": "Titel is uit Sonarr/Radarr verwijderd; MediaGuard vraagt hem niet opnieuw aan",
    "migrated": "Gemigreerde titel, geen aanvraag; MediaGuard volgt hem niet als aanvraag",
}
# zo lang bewaren: het zoekquotum kijkt hooguit een dag terug
SEARCH_KEEP_DAYS = 7


def item_key(media_type: str, tmdb_id: Any, tvdb_id: Any) -> str | None:
    if media_type == "series" and tvdb_id:
        return f"series:tvdb:{int(tvdb_id)}"
    if media_type == "movie" and tmdb_id:
        return f"movie:tmdb:{int(tmdb_id)}"
    return None


def upsert(ctx: AppContext, *, media_type: str, tmdb_id: Any, tvdb_id: Any, title: str | None,
           source: str, seerr_request_id: int | None = None, instance_id: str | None = None,
           provider_id: int | None = None, seasons: list[int] | None = None,
           requested_at: str | None = None) -> int | None:
    """Nieuwe aanvraag vastleggen of een bestaande aanvullen (seizoenen, Seerr-ID's)."""
    key = item_key(media_type, tmdb_id, tvdb_id)
    if key is None:
        return None
    now = utcnow()
    row = ctx.db.fetch_one("SELECT * FROM delivery_items WHERE item_key=?", (key,))
    if row is None:
        item_id = ctx.db.execute_insert(
            "INSERT INTO delivery_items(item_key,source,seerr_request_ids_json,media_type,"
            "tmdb_id,tvdb_id,title,instance_id,provider_id,requested_seasons_json,"
            "requested_at,next_action_at,created_at,updated_at) VALUES "
            "(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (key, source, dumps([seerr_request_id] if seerr_request_id else []), media_type,
             tmdb_id, tvdb_id, title, instance_id, provider_id, dumps(sorted(set(seasons or []))),
             requested_at or now, now, now, now))
        event(ctx, item_id, "requested", "aangevraagd", "svc:delivery",
              "Aangevraagd via Seerr" if source == "seerr" else
              "Toegevoegd in Sonarr/Radarr")
        return item_id
    ids = set(loads(row["seerr_request_ids_json"], []) or [])
    new_seasons = set(loads(row["requested_seasons_json"], []) or []) | set(seasons or [])
    reopened = False
    if seerr_request_id and seerr_request_id not in ids:
        ids.add(seerr_request_id)
        reopened = True
    ctx.db.execute(
        "UPDATE delivery_items SET seerr_request_ids_json=?, requested_seasons_json=?, "
        "title=COALESCE(title, ?), instance_id=COALESCE(instance_id, ?), "
        "provider_id=COALESCE(provider_id, ?), updated_at=? WHERE id=?",
        (dumps(sorted(ids)), dumps(sorted(new_seasons)), title, instance_id, provider_id, now,
         row["id"]))
    if reopened:
        # extra aanvraag (bv. nieuw seizoen) of opnieuw aangevraagd na stoppen: opnieuw volgen,
        # met nieuwe tellers (anders meteen 'na 30 dagen nergens te vinden') en de aangevraagde
        # seizoenen opnieuw op 'bewaken' (het profiel blijft zoals het is). Na een stop telt
        # 'stond er al eens' niet meer: een nieuwe aanvraag mag hem opnieuw laten toevoegen.
        ctx.db.execute(
            "UPDATE delivery_items SET state=CASE WHEN state='waiting_release' AND stopped_at "
            "IS NULL THEN state ELSE 'active' END, stage=CASE WHEN stopped_at IS NOT NULL "
            "THEN 'requested' ELSE stage END, next_action_at=?, stopped_at=NULL, "
            "stop_code=NULL, stop_reason_nl=NULL, attempts=0, first_search_at=NULL, "
            "notified_at=NULL, seerr_retries=0, seerr_synced_at=NULL, seerr_sync_attempts=0, "
            "requirements_checked_at=NULL, located_at=CASE WHEN stopped_at IS NOT NULL THEN "
            "NULL ELSE located_at END WHERE id=?", (now, row["id"]))
        if row["state"] == "delivered" or row["stopped_at"]:
            event(ctx, int(row["id"]), "requested", "opnieuw_aangevraagd", "svc:delivery",
                  "Nieuwe aanvraag via Seerr; MediaGuard volgt hem weer")
    return int(row["id"])


def stop(ctx: AppContext, item_id: int, code: str, actor: str,
         reason_nl: str | None = None) -> None:
    """Niet meer volgen (knop Stop, verwijderd in Seerr of uit Sonarr/Radarr)."""
    text = reason_nl or STOP_NL.get(code, "Gestopt")
    update(ctx, item_id, stopped_at=utcnow(), stop_code=code, stop_reason_nl=text,
           stage="stopped", detail_nl=text, next_step_nl="MediaGuard doet hier niets meer mee",
           next_action_at=None)
    event(ctx, item_id, "stopped", "gestopt", actor, text)


def resume(ctx: AppContext, item_id: int, actor: str,
           detail_nl: str = "Nu opnieuw geprobeerd") -> None:
    """Weer volgen en meteen opnieuw proberen (knop Nu opnieuw)."""
    update(ctx, item_id, stopped_at=None, stop_code=None, stop_reason_nl=None,
           seerr_retries=0, next_action_at=utcnow(), stage="requested", attempts=0,
           first_search_at=None, notified_at=None, requirements_checked_at=None,
           located_at=None)
    event(ctx, item_id, "requested", "opnieuw", actor, detail_nl)


def search_budget(ctx: AppContext, instance_id: str, default: int = 50) -> int:
    """Hoeveel zoekacties er nog in het quotum van deze instantie passen."""
    try:
        from mediaguard.wanted.backfill import quota_status

        return max(0, int(quota_status(ctx, instance_id).get("remaining", default)))
    except Exception:  # noqa: BLE001 - quotum onbekend (bv. oude database): niet blokkeren
        return default


def record_search(ctx: AppContext, instance_id: str, command: str, cost: int,
                  item_id: int | None = None) -> None:
    """Zoekactie meetellen in het zoekquotum (verbeterlijst 36)."""
    from datetime import UTC, datetime, timedelta

    ctx.db.execute("INSERT INTO delivery_searches(item_id,instance_id,ts,command,cost) "
                   "VALUES (?,?,?,?,?)", (item_id, instance_id, utcnow(), command,
                                          max(0, int(cost))))
    cutoff = (datetime.now(UTC) - timedelta(days=SEARCH_KEEP_DAYS)).isoformat(
        timespec="seconds")
    ctx.db.execute("DELETE FROM delivery_searches WHERE instance_id=? AND ts<?",
                   (instance_id, cutoff))


def event(ctx: AppContext, item_id: int, stage: str, action: str, actor: str, detail_nl: str,
          data: dict[str, Any] | None = None) -> None:
    last = ctx.db.fetch_one("SELECT stage, action, detail_nl FROM delivery_events WHERE "
                            "item_id=? ORDER BY id DESC LIMIT 1", (item_id,))
    if last and (last["stage"], last["action"], last["detail_nl"]) == (stage, action, detail_nl):
        return  # zelfde stand: tijdlijn niet volschrijven
    ctx.db.execute(
        "INSERT INTO delivery_events(item_id,ts,stage,action,actor,detail_nl,data_json) "
        "VALUES (?,?,?,?,?,?,?)", (item_id, utcnow(), stage, action, actor, detail_nl,
                                   dumps(data or {})))


def update(ctx: AppContext, item_id: int, **fields: Any) -> None:
    if not fields:
        return
    fields["updated_at"] = utcnow()
    cols = ", ".join(f"{k}=?" for k in fields)
    ctx.db.execute(f"UPDATE delivery_items SET {cols} WHERE id=?",  # noqa: S608 - vaste kolommen
                   (*fields.values(), item_id))


def get(ctx: AppContext, item_id: int) -> dict[str, Any] | None:
    row = ctx.db.fetch_one("SELECT * FROM delivery_items WHERE id=?", (item_id,))
    return with_library_ids(ctx, [_public(dict(row))])[0] if row else None


def with_library_ids(ctx: AppContext, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Link naar de titel (aanvragenpagina, punt 26): MediaGuard's eigen bibliotheek-ID, want de
    detailpagina verwacht dat, niet het Sonarr/Radarr-ID."""
    per_instance: dict[str, set[int]] = {}
    for r in rows:
        if r.get("instance_id") and r.get("provider_id"):
            per_instance.setdefault(str(r["instance_id"]), set()).add(int(r["provider_id"]))
    found: dict[tuple[str, int, str], int] = {}
    for iid, pids in per_instance.items():
        ids = sorted(pids)
        marks = ",".join("?" for _ in ids)
        for lib in ctx.db.fetch_all(
                f"SELECT id, kind, provider_id FROM lib_items WHERE instance_id=? AND "  # noqa: S608
                f"removed_at IS NULL AND provider_id IN ({marks})", (iid, *ids)):
            found[(iid, int(lib["provider_id"]), str(lib["kind"]))] = int(lib["id"])
    for r in rows:
        pid = r.get("provider_id")
        key = (str(r.get("instance_id")), int(pid or 0), str(r.get("media_type")))
        r["library_id"] = found.get(key) if pid else None
    return rows


def public_state(row: dict[str, Any]) -> str:
    return "stopped" if row.get("stopped_at") else str(row["state"])


def _public(row: dict[str, Any]) -> dict[str, Any]:
    row["seerr_request_ids"] = loads(row.pop("seerr_request_ids_json"), [])
    row["requested_seasons"] = loads(row.pop("requested_seasons_json"), [])
    row["progress"] = loads(row.pop("progress_json"), {})
    row["state"] = public_state(row)
    row["stage_nl"] = STAGE_NL.get(row["stage"], row["stage"])
    return row


STATE_SQL = "CASE WHEN stopped_at IS NOT NULL THEN 'stopped' ELSE state END"


def list_items(ctx: AppContext, *, state: str | None = None, limit: int = 300) -> list[dict]:
    sql = "SELECT * FROM delivery_items"
    params: list[Any] = []
    if state:
        sql += f" WHERE {STATE_SQL}=?"
        params.append(state)
    sql += (f" ORDER BY CASE {STATE_SQL} WHEN 'active' THEN 0 WHEN 'not_found' THEN 1 "
            "WHEN 'waiting_release' THEN 2 WHEN 'delivered' THEN 3 ELSE 4 END, "
            "COALESCE(requested_at, created_at) DESC LIMIT ?")
    return with_library_ids(ctx, [_public(dict(r)) for r in ctx.db.fetch_dicts(
        sql, (*params, limit))])


def events(ctx: AppContext, item_id: int, limit: int = 200) -> list[dict[str, Any]]:
    rows = ctx.db.fetch_dicts("SELECT * FROM delivery_events WHERE item_id=? ORDER BY id DESC "
                              "LIMIT ?", (item_id, limit))
    for r in rows:
        r["data"] = loads(r.pop("data_json"), {})
    return rows


def counts(ctx: AppContext) -> dict[str, int]:
    out = {"active": 0, "waiting_release": 0, "delivered": 0, "not_found": 0, "stopped": 0}
    for r in ctx.db.fetch_all(f"SELECT {STATE_SQL} AS s, COUNT(*) AS n FROM "  # noqa: S608
                              "delivery_items GROUP BY s"):
        out[r["s"]] = int(r["n"])
    out["total"] = sum(out.values())
    return out
