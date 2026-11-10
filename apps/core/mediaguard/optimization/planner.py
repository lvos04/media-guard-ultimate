"""Library Optimization Engine (C3): classificatie, dry-run, prognose, uitvoering.

Pijplijn ``DISCOVER → INVENTORY → IDENTITY → AUDIO → QUALITY → POLICY → PLAN →
SEARCH/ENCODE/REPAIR → STAGE → VERIFY → COMMIT → AUDIT``. Deze module doet DISCOVER t/m
PLAN deterministisch en zonder schrijfacties; uitvoering (``run_batch``) delegeert naar
de arr-zoekopdrachten en de encoding-bridge, die zelf weer via recovery committen.

Prioriteit (C3): 1 bevestigd verkeerd/beschadigd, 2 verkeerd gekoppeld/dubbel/ontbrekend,
3 audiotaal, 4 kwaliteit, 5 AV1, 6 monitoren.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from mediaguard.audio.evaluate import AudioPolicy, evaluate_languages
from mediaguard.db import dumps, loads, utcnow
from mediaguard.identity import service as identity_service
from mediaguard.optimization.provider import LibraryItem, get_provider
from mediaguard.policy import profiles
from mediaguard.settings_registry import SettingDef

if TYPE_CHECKING:
    from mediaguard.context import AppContext

CATEGORY = "Optimalisatie"
BUCKETS = {"keep": "behouden", "repair": "vervangen", "redownload": "zoeken",
           "language-upgrade": "zoeken", "quality-upgrade": "zoeken", "av1": "AV1",
           "verify": "onzeker", "uncertain": "onzeker", "skip": "overslaan"}
BUCKET_ORDER = ("behouden", "vervangen", "zoeken", "AV1", "onzeker", "overslaan")
ACTIONABLE = {"repair", "redownload", "language-upgrade", "quality-upgrade", "av1", "verify"}
SEARCH_ACTIONS = {"redownload", "language-upgrade", "quality-upgrade"}


def setting_defs() -> list[SettingDef]:
    scopes = ("global", "media_type", "instance")
    return [
        SettingDef("optimization.max_active_downloads", "int", 3, CATEGORY,
                   "Max. actieve downloads", "Max active downloads", minimum=0, maximum=100,
                   scopes=scopes),
        SettingDef("optimization.max_gb_per_day", "int", 200, CATEGORY, "Max. GB per dag",
                   "Max GB per day", minimum=0, maximum=100000, unit="GB", scopes=scopes),
        SettingDef("optimization.downloads_need_confirmation", "bool", False, CATEGORY,
                   "Betere versies pas na akkoord downloaden",
                   "Ask before downloading better versions",
                   help_nl="Standaard uit: MediaGuard downloadt betere versies zelf, binnen de "
                           "limieten hieronder (actieve downloads, GB per dag, zoekopdrachten "
                           "per uur)."),
        SettingDef("optimization.max_searches_per_hour", "int", 30, CATEGORY,
                   "Max. zoekopdrachten per uur (indexerquota)", "Max searches per hour",
                   minimum=0, maximum=10000, scopes=scopes),
        SettingDef("optimization.max_concurrent_encodes", "int", 1, CATEGORY,  # vervallen
                   "Max. gelijktijdige AV1-encodes (CPU/GPU)", "Max concurrent encodes",
                   minimum=0, maximum=16),
        SettingDef("optimization.staging_reserve_gb", "int", 100, CATEGORY,
                   "Stagingreserve (GB)", "Staging reserve (GB)", minimum=0, maximum=100000,
                   unit="GB"),
        SettingDef("optimization.window_enabled", "bool", False, CATEGORY,
                   "Alleen binnen tijdvenster uitvoeren", "Only run within time window"),
        SettingDef("optimization.window", "time_window", {"start": "01:00", "end": "07:00"},
                   CATEGORY, "Tijdvenster", "Time window",
                   help_nl="Mag over middernacht lopen (bijv. 23:00–06:00)."),
        SettingDef("optimization.batch_size", "int", 25, CATEGORY, "Batchgrootte",
                   "Batch size", minimum=1, maximum=1000, advanced=True),
        SettingDef("optimization.estimated_av1_saving_percent", "int", 40, CATEGORY,
                   "Verwachte AV1-besparing H.264 (%)", "Expected AV1 saving H.264 (%)",
                   help_nl="Alleen voor de prognose; de werkelijke winst volgt uit proefencodes.",
                   minimum=1, maximum=90),
        SettingDef("optimization.estimated_av1_saving_percent_hevc", "int", 20, CATEGORY,
                   "Verwachte AV1-besparing HEVC (%)", "Expected AV1 saving HEVC (%)",
                   minimum=1, maximum=90),
        SettingDef("optimization.av1_threshold_2160_kbps", "int", 20000, CATEGORY,
                   "AV1-drempel 2160p (kbps)", "AV1 threshold 2160p (kbps)", minimum=0,
                   maximum=200000, scopes=scopes),
        SettingDef("optimization.av1_threshold_1080_kbps", "int", 8000, CATEGORY,
                   "AV1-drempel 1080p (kbps)", "AV1 threshold 1080p (kbps)", minimum=0,
                   maximum=200000, scopes=scopes),
        SettingDef("optimization.av1_threshold_720_kbps", "int", 4000, CATEGORY,
                   "AV1-drempel 720p (kbps)", "AV1 threshold 720p (kbps)", minimum=0,
                   maximum=200000, scopes=scopes),
        SettingDef("optimization.dry_run_valid_hours", "int", 24, CATEGORY,
                   "Geldigheid dry-run (uren)", "Dry-run validity (hours)",
                   help_nl="Daarna eerst opnieuw een dry-run maken.", minimum=1, maximum=720),
    ]


@dataclass
class Classified:
    item: LibraryItem
    action: str
    priority: int
    reason_nl: str
    est_bytes: int = 0
    est_saving: int = 0

    @property
    def bucket(self) -> str:
        return BUCKETS[self.action]


def _chain(item: LibraryItem) -> dict[str, str]:
    return profiles.build_chain(instance_id=item.instance_id, media_type=item.media_type,
                                series_id=item.series_id, movie_id=item.movie_id,
                                season=item.season)


def classify(ctx: AppContext, item: LibraryItem) -> Classified:  # noqa: C901 - beslisboom
    chain = _chain(item)
    if item.protected:
        return Classified(item, "skip", 9, "Beschermd item (handmatig uitgesloten)")
    if not item.monitored:
        return Classified(item, "skip", 9, "Niet gemonitord")
    if not item.has_file:
        return Classified(item, "redownload", 2, "Ontbreekt: zoeken naar geschikte release",
                          est_bytes=item.size or 0)
    ident = item.identity_status
    actionable = item.identity_actionable
    if ident is None and item.instance_id:
        latest = identity_service.latest(ctx, item.instance_id, item.media_kind,
                                         item.provider_id)
        if latest:
            ident, actionable = latest["status"], bool(latest["actionable"])
    if ident == "mismatch" and actionable:
        return Classified(item, "redownload", 1, "Bewezen verkeerde aflevering (sterk bewijs): "
                          "juiste release zoeken; huidig bestand blijft tot vervanging",
                          est_bytes=item.size or 0)
    if item.corrupt:
        return Classified(item, "redownload", 1, "Beschadigd bestand: opnieuw zoeken",
                          est_bytes=item.size or 0)
    if item.duplicate:
        return Classified(item, "repair", 2, "Dubbel geïmporteerd of verkeerd gekoppeld: "
                          "handmatig herstel")
    if ident in ("mismatch", "uncertain"):
        return Classified(item, "verify", 2, f"Identiteit {ident}: eerst verifiëren, niets "
                          "vervangen op zwak bewijs")
    if not item.size or not item.path:
        return Classified(item, "uncertain", 8, "Onvoldoende bestandsgegevens (pad/grootte)")

    eff = profiles.effective_policy(ctx, chain)
    if item.audio_languages:
        verdict = evaluate_languages(item.audio_languages, item.subtitle_languages,
                                     AudioPolicy.from_dict(eff["config"]["audio"]))
        if not verdict.acceptable or verdict.keep_searching:
            return Classified(item, "language-upgrade", 3,
                              f"Audio: {verdict.tier_label_nl} — "
                              + ("; ".join(verdict.reasons) or "voorkeur niet gehaald"),
                              est_bytes=item.size or 0)
    if item.cutoff_unmet:
        return Classified(item, "quality-upgrade", 4, "Kwaliteitscutoff niet gehaald",
                          est_bytes=item.size or 0)

    codec = (item.codec or "").lower()
    if codec == "av1":
        return Classified(item, "keep", 6, "Al AV1")
    dr = item.dynamic_range.upper()
    if "HDR10PLUS" in dr.replace("+", "PLUS").replace(" ", "") and \
            ctx.config.value("dv.hdr10plus_mode") == "skip":  # convert: encoder bewijst route
        return Classified(item, "keep", 6, "HDR10+: bron behouden (beleid overslaan)")
    if "DV" in dr.split() or dr.startswith("DV") or "DOVI" in dr:
        from mediaguard.dv import policy as dv_policy
        dchain = dv_policy.scope_chain(
            library=item.instance_id, profile=eff["profile_id"],
            series=f"{item.instance_id}:{item.series_id}" if item.series_id else None,
            movie=f"{item.instance_id}:{item.movie_id}" if item.movie_id else None)
        pol = dv_policy.effective(ctx, dchain)
        if not pol.allow or pol.mode == "keep_original":
            return Classified(item, "keep", 6, "Dolby Vision: origineel behouden (DV-beleid)")
    if (item.nlink or 1) > 1 and ctx.config.value(
            "recovery.hardlink_policy", {"instance": item.instance_id}) == "skip":
        return Classified(item, "skip", 9, f"{item.nlink} hardlinks (seeding): overslaan")
    height = item.height or 0
    cls = "2160" if height > 1200 else ("1080" if height > 800 else "720")
    threshold = int(ctx.config.value(f"optimization.av1_threshold_{cls}_kbps", chain))
    if item.bitrate_kbps is not None and item.bitrate_kbps <= threshold:
        return Classified(item, "keep", 6, f"{item.bitrate_kbps} kbps onder drempel "
                          f"{threshold}: hercoderen levert te weinig op")
    pct_key = "optimization.estimated_av1_saving_percent_hevc" if codec in (
        "hevc", "x265", "h265") else "optimization.estimated_av1_saving_percent"
    pct = int(ctx.config.value(pct_key))
    saving = int((item.size or 0) * pct / 100)
    note = "" if item.bitrate_kbps is not None else " (bitrate onbekend; plan controleert)"
    return Classified(item, "av1", 5, f"AV1-kandidaat {codec or 'onbekend'} {cls}p, "
                      f"geschatte besparing {pct}%{note}", est_bytes=item.size or 0,
                      est_saving=saving)


def forecast(ctx: AppContext, rows: list[Classified]) -> dict[str, Any]:
    buckets = {b: {"count": 0, "bytes": 0} for b in BUCKET_ORDER}
    for r in rows:
        buckets[r.bucket]["count"] += 1
        buckets[r.bucket]["bytes"] += int(r.item.size or 0)
    av1 = [r for r in rows if r.action == "av1"]
    search = [r for r in rows if r.action in SEARCH_ACTIONS]
    max_enc = max(1, _parallel_encodes(ctx))
    largest = sorted((int(r.item.size or 0) for r in av1), reverse=True)[:max_enc]
    staging = int(sum(largest) * 1.1)
    reserve = int(ctx.config.value("optimization.staging_reserve_gb")) * 1024**3
    gb_day = int(ctx.config.value("optimization.max_gb_per_day"))
    dl_bytes = sum(r.est_bytes for r in search)
    return {
        "items": len(rows), "buckets": buckets,
        "bytes_total": sum(int(r.item.size or 0) for r in rows),
        "av1_bytes": sum(int(r.item.size or 0) for r in av1),
        "estimated_savings_bytes": sum(r.est_saving for r in av1),
        "staging_needed_bytes": staging, "staging_reserve_bytes": reserve,
        "staging_total_needed_bytes": staging + reserve,
        "download_bytes_estimate": dl_bytes,
        "download_days_estimate": round(dl_bytes / (gb_day * 1024**3), 1) if gb_day else None,
        "note_nl": "Prognose op basis van bitrate/codec; werkelijke besparing volgt uit "
                   "proefencodes. Geen exacte kwaliteitsclaim zonder eigen meting.",
    }


def dry_run(ctx: AppContext, filters: dict[str, Any], actor: str,
            limit: int | None = None) -> dict[str, Any]:
    provider = get_provider(ctx)
    rows: list[Classified] = []
    for item in provider.items(ctx, filters):
        rows.append(classify(ctx, item))
        if limit and len(rows) >= limit:
            break
    rows.sort(key=lambda r: (r.priority, -r.est_saving, r.item.instance_id, r.item.provider_id))
    fc = forecast(ctx, rows)
    summary = {b: fc["buckets"][b]["count"] for b in BUCKET_ORDER}
    now = utcnow()
    with ctx.db.transaction() as conn:
        cur = conn.execute(
            "INSERT INTO optimization_plans(status,filters_json,summary_json,forecast_json,"
            "item_count,created_at,created_by,updated_at) VALUES ('dry_run',?,?,?,?,?,?,?)",
            (dumps(filters), dumps(summary), dumps(fc), len(rows), now, actor, now))
        plan_id = int(cur.lastrowid or 0)
        conn.executemany(
            "INSERT INTO optimization_items(plan_id,priority,instance_id,media_kind,provider_id,"
            "title,path,size,action,bucket,reason_nl,est_bytes,est_saving_bytes,context_json,"
            "status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [(plan_id, r.priority, r.item.instance_id, r.item.media_kind, r.item.provider_id,
              r.item.title, r.item.path, r.item.size, r.action, r.bucket, r.reason_nl,
              r.est_bytes, r.est_saving,
              dumps({"series_id": r.item.series_id, "movie_id": r.item.movie_id,
                     "episode_ids": r.item.episode_ids, "media_type": r.item.media_type}),
              "planned" if r.action in ACTIONABLE else "not_applicable") for r in rows])
    return get_plan(ctx, plan_id) or {}


def get_plan(ctx: AppContext, plan_id: int | None = None) -> dict[str, Any] | None:
    row = ctx.db.fetch_one("SELECT * FROM optimization_plans WHERE id=?", (plan_id,)) \
        if plan_id else ctx.db.fetch_one("SELECT * FROM optimization_plans ORDER BY id DESC "
                                         "LIMIT 1")
    if row is None:
        return None
    d = dict(row)
    d["summary"] = loads(d.pop("summary_json"), {})
    d["filters"] = loads(d.pop("filters_json"), {})
    d["forecast"] = loads(d.pop("forecast_json"), {})
    d["progress"] = {r["status"]: r["n"] for r in ctx.db.fetch_all(
        "SELECT status, COUNT(*) AS n FROM optimization_items WHERE plan_id=? GROUP BY status",
        (d["id"],))}
    d["display"] = display(ctx, d)
    return d


STATUS_NL = {"dry_run": "voorbeeld", "running": "loopt", "paused": "gepauzeerd",
             "completed": "klaar", "cancelled": "gestopt", "expired": "verlopen"}
ITEM_STATUS_NL = {"planned": "gepland", "not_applicable": "niets te doen",
                  "auto_identity": "MediaGuard controleert zelf", "kept_info": "blijft staan",
                  "search_sent": "zoekopdracht verstuurd", "encode_submitted": "naar AV1",
                  "encode_skipped": "AV1 overgeslagen", "blocked": "geblokkeerd",
                  "failed": "mislukt", "awaiting_download_confirmation":
                  "wacht op jouw akkoord voor downloaden"}


def downloads_confirmed(ctx: AppContext, plan_id: int) -> bool:
    """Zonder interactie (standaard): betere versies gaan vanzelf, begrensd door de quota
    per dag/uur. Alleen als de gebruiker expliciet om akkoord vraagt, wacht het plan."""
    if not bool(ctx.config.value("optimization.downloads_need_confirmation")):
        return True
    return bool(ctx.state_get(f"optimization_downloads_ok:{plan_id}"))


def _gb(n: float) -> str:
    tb = n / 1024**4
    return f"{tb:.1f} TB".replace(".", ",") if tb >= 1 else \
        f"{n / 1024**3:.0f} GB"


def weeks_nl(days: float | None) -> str | None:
    """'± 3 weken' i.p.v. een precieze datum die bij elke meting verspringt (R12)."""
    if days is None:
        return None
    if days < 2:
        return "± 1 dag"
    if days < 14:
        return f"± {round(days)} dagen"
    if days < 120:
        return f"± {round(days / 7)} weken"
    return f"± {round(days / 30)} maanden"


def display(ctx: AppContext, plan: dict[str, Any]) -> dict[str, Any]:
    """Rapport 2 / N16: leesbare samenvatting. AV1 (lokaal) en downloaden (betere versies)
    apart, met een eigen akkoord voor downloaden; prognose uit het echte tempo."""
    fc = plan.get("forecast") or {}
    saving = int(fc.get("estimated_savings_bytes") or 0)
    dl = int(fc.get("download_bytes_estimate") or 0)
    marks = ",".join("?" for _ in SEARCH_ACTIONS)
    counts = ctx.db.fetch_one(
        "SELECT SUM(CASE WHEN action='av1' THEN 1 ELSE 0 END) AS av1, "
        f"SUM(CASE WHEN action IN ({marks}) THEN 1 ELSE 0 END) AS search "  # noqa: S608
        "FROM optimization_items WHERE plan_id=?", (*SEARCH_ACTIONS, int(plan["id"])))
    av1_count = int((counts["av1"] if counts else 0) or 0)
    search_count = int((counts["search"] if counts else 0) or 0)
    # echt tempo: AV1-encodes die de laatste 7 dagen geplaatst zijn
    row = ctx.db.fetch_one(
        "SELECT COUNT(*) AS n FROM encoding_jobs WHERE status='committed' AND "
        "updated_at>=datetime('now','-7 days')")
    per_day = (int(row["n"]) if row else 0) / 7
    left = int((plan.get("progress") or {}).get("planned") or 0)
    eta_days = round(left / per_day) if per_day else None
    return {
        "status_nl": STATUS_NL.get(str(plan.get("status")), str(plan.get("status"))),
        "can_start": plan.get("status") == "dry_run",
        "savings_nl": (f"Bespaart ± {_gb(saving)}" if saving > 0 else
                       f"Kost ± {_gb(-saving)} extra ruimte (betere versies zijn groter dan "
                       "wat AV1 bespaart)" if saving < 0 else "Nog geen besparing te schatten"),
        "eta_weeks_nl": weeks_nl(eta_days),
        "av1": {"count": av1_count, "savings_bytes": saving},
        "downloads": {"bytes": dl, "searches": search_count,
            "days": fc.get("download_days_estimate"),
            "nl": (f"Betere versies downloaden: ± {_gb(dl)}, ± "
                   f"{fc.get('download_days_estimate')} dagen") if dl else None,
            "confirmed": downloads_confirmed(ctx, int(plan["id"])),
            "needs_confirmation": bool(dl) and not downloads_confirmed(ctx, int(plan["id"]))},
        "pace_per_day": round(per_day, 1),
        "eta_days": eta_days,
        "eta_nl": (f"Bij het huidige tempo (± {per_day:.0f} per dag) klaar over ± {eta_days} "
                   "dagen") if eta_days else "Nog geen tempo bekend",
        "progress_nl": {ITEM_STATUS_NL.get(k, k): v
                        for k, v in (plan.get("progress") or {}).items()},
    }


def items(ctx: AppContext, plan_id: int, *, bucket: str | None = None, offset: int = 0,
          limit: int = 200) -> list[dict[str, Any]]:
    sql = "SELECT * FROM optimization_items WHERE plan_id=?"
    params: list[Any] = [plan_id]
    if bucket:
        sql += " AND bucket=?"
        params.append(bucket)
    return ctx.db.fetch_dicts(sql + " ORDER BY priority, id LIMIT ? OFFSET ?",
                              [*params, limit, offset])


def export(ctx: AppContext, plan_id: int, fmt: str) -> str:
    rows = items(ctx, plan_id, limit=10**9)
    if fmt == "json":
        return dumps({"plan": get_plan(ctx, plan_id), "items": rows})
    buf = io.StringIO()
    cols = ["id", "priority", "instance_id", "media_kind", "provider_id", "title", "path",
            "size", "action", "bucket", "reason_nl", "est_bytes", "est_saving_bytes", "status"]
    writer = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    writer.writeheader()
    for r in rows:
        # CSV-injectie voorkomen (bestandsnamen zijn onbetrouwbaar)
        writer.writerow({k: ("'" + v if isinstance(v, str) and v[:1] in "=+-@\t\r" else v)
                         for k, v in r.items()})
    return buf.getvalue()


def in_window(ctx: AppContext, now: datetime | None = None) -> bool:
    if not ctx.config.value("optimization.window_enabled"):
        return True
    win = ctx.config.value("optimization.window")
    t = (now or datetime.now()).strftime("%H:%M")
    start, end = win["start"], win["end"]
    return start <= t < end if start <= end else (t >= start or t < end)


def plan_valid(ctx: AppContext, plan: dict[str, Any]) -> bool:
    hours = int(ctx.config.value("optimization.dry_run_valid_hours"))
    created = datetime.fromisoformat(plan["created_at"])
    return datetime.now(UTC) - created <= timedelta(hours=hours)


def _parallel_encodes(ctx: AppContext) -> int:
    """Eén plek voor 'encodes tegelijk': Instellingen → Systeem & prestaties (CPU + GPU).
    De oude optimization.max_concurrent_encodes wordt niet meer gebruikt."""
    from mediaguard.encoding.resources import parallel_encodes

    return max(1, parallel_encodes(ctx))
