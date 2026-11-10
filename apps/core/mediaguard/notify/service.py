"""Externe meldingen: kanalen, voorkeuren per soort melding, uitgaande wachtrij met
herhalingen, en dag-/weekoverzichten.

Elke nieuwe melding in de tabel ``notifications`` (via ``api.system.notify``) komt hier
binnen. Per soort (fouten, wacht op jou, AV1 klaar, downloads, opslag, beveiliging) kiest
de gebruiker: direct, in het dagoverzicht of nooit. Directe meldingen gaan via de
uitgaande wachtrij (herhalen met oplopende wachttijd); boven het uurmaximum per kanaal
schuiven ze door naar het overzicht, zodat een storm nooit tientallen pushberichten geeft.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from mediaguard.db import dumps, loads, utcnow
from mediaguard.notify import channels as ch
from mediaguard.settings_registry import REGISTRY, SettingDef

if TYPE_CHECKING:
    from mediaguard.context import AppContext

LOGGER = logging.getLogger(__name__)
CATEGORY = "Meldingen"
CATEGORIES = ("errors", "waiting", "av1_done", "downloads", "storage", "security")
CATEGORY_LABELS_NL = {"errors": "Fouten", "waiting": "Wacht op jou", "av1_done": "AV1 klaar",
                      "downloads": "Downloads", "storage": "Opslag",
                      "security": "Beveiliging"}
CHOICES = ("direct", "digest", "never")
CHOICE_LABELS_NL = {"direct": "Direct", "digest": "Dagoverzicht", "never": "Nooit"}
DEFAULT_PREF = {"errors": "direct", "security": "direct", "waiting": "digest",
                "av1_done": "digest", "downloads": "digest",
                # opslag direct: het dagelijkse opslagoverzicht moet de telefoon halen
                "storage": "direct"}
BACKOFF_MINUTES = (1, 2, 5, 15, 30, 60)
REPEAT_HOURS = 6  # dezelfde (opgehoogde) melding hooguit eens per 6 uur opnieuw pushen


def register_settings() -> None:
    defs = [SettingDef(f"notify.pref.{cat}", "enum", DEFAULT_PREF[cat], CATEGORY,
                       f"{CATEGORY_LABELS_NL[cat]}: wanneer melden",
                       f"{cat}: when to notify", choices=CHOICES,
                       choice_labels_nl=dict(CHOICE_LABELS_NL),
                       help_nl="Direct = meteen een pushbericht; Dagoverzicht = in de "
                               "samenvatting van de volgende ochtend; Nooit = alleen in de app.")
            for cat in CATEGORIES]
    defs += [
        SettingDef("notify.digest.daily", "bool", True, CATEGORY, "Dagoverzicht sturen",
                   "Send daily summary",
                   help_nl="Elke ochtend één bericht met wat MediaGuard gisteren deed."),
        SettingDef("notify.digest.time", "string", "08:00", CATEGORY, "Tijd van het overzicht",
                   "Summary time", help_nl="Lokale tijd (UU:MM)."),
        SettingDef("notify.digest.weekly", "bool", True, CATEGORY, "Weekoverzicht sturen",
                   "Send weekly summary"),
        SettingDef("notify.digest.weekday", "int", 1, CATEGORY, "Dag van het weekoverzicht",
                   "Weekly summary day", minimum=1, maximum=7,
                   help_nl="1 = maandag … 7 = zondag."),
        SettingDef("notify.timezone", "string", "Europe/Amsterdam", CATEGORY, "Tijdzone",
                   "Time zone", advanced=True),
        SettingDef("notify.rate_per_hour", "int", 20, CATEGORY,
                   "Maximaal aantal pushberichten per uur", "Max pushes per hour",
                   minimum=1, maximum=200, advanced=True,
                   help_nl="Daarboven gaan meldingen naar het dagoverzicht."),
        SettingDef("notify.base_url", "string", "", CATEGORY, "Adres van MediaGuard in links",
                   "MediaGuard URL for links", advanced=True,
                   help_nl="Bijvoorbeeld https://mediaguard.example.nl — dan openen "
                           "pushberichten direct de juiste pagina. Leeg = geen link."),
    ]
    for d in defs:
        if d.key not in {x.key for x in REGISTRY.all()}:
            REGISTRY.register(d)


# -- indeling ---------------------------------------------------------------------------------
def categorize(severity: str, title: str, link: str | None, dedupe_key: str | None = None) -> str:
    t = (title or "").lower()
    lk = (link or "").lower()
    key = (dedupe_key or "").lower()
    if key.startswith("update:"):  # nieuwe MediaGuard-versie: geen opslagmelding
        return "waiting"
    if key.startswith("security.") or "aanmeld" in t or "inlog" in t or "2fa" in t \
            or "beveiliging" in lk:
        return "security"
    if any(w in t for w in ("schijf", "opslag", "vrije ruimte", "ruimte vol")) \
            or lk.startswith("/systeem"):
        return "storage"
    if severity in ("success",) and ("av1" in t or lk.startswith("/av1")
                                     or "omgezet" in t or "bespaard" in t):
        return "av1_done"
    if "download" in t or "import" in t or lk.startswith("/ontbrekend"):
        return "downloads" if severity not in ("error", "critical") else "errors"
    if severity in ("error", "critical"):
        return "errors"
    if "wacht op jou" in t or "#todo" in lk or severity == "warning":
        return "waiting"
    return "av1_done" if lk.startswith("/av1") else "waiting"


def preference(ctx: AppContext, category: str) -> str:
    try:
        return str(ctx.config.value(f"notify.pref.{category}"))
    except Exception:  # noqa: BLE001 - instellingen nog niet geregistreerd
        return DEFAULT_PREF.get(category, "digest")


# -- kanalen ----------------------------------------------------------------------------------
def list_channels(ctx: AppContext, *, enabled_only: bool = False) -> list[dict[str, Any]]:
    sql = "SELECT * FROM notify_channels" + (" WHERE enabled=1" if enabled_only else "")
    out = []
    for row in ctx.db.fetch_dicts(sql + " ORDER BY id"):
        row["config"] = loads(row.pop("config_json"), {})
        out.append(row)
    return out


def get_channel(ctx: AppContext, channel_id: int) -> dict[str, Any] | None:
    rows = [c for c in list_channels(ctx) if int(c["id"]) == channel_id]
    return rows[0] if rows else None


def _validate(kind: str, config: dict[str, Any]) -> dict[str, Any]:
    if kind not in ch.KINDS:
        raise ValueError(f"Onbekend kanaal: {kind}")
    allowed = {n for n, secret, _r in ch.FIELDS[kind] if not secret}
    clean = {k: v for k, v in config.items() if k in allowed and v not in (None, "")}
    if kind == "ntfy":
        clean["server"] = str(clean.get("server") or "https://ntfy.sh").rstrip("/")
        topic = str(clean.get("topic") or "")
        if topic and not all(c.isalnum() or c in "-_" for c in topic):
            raise ValueError("Een ntfy-onderwerp mag alleen letters, cijfers, - en _ bevatten")
    if kind == "email":
        clean["port"] = int(clean.get("port") or 587)
        if str(clean.get("security") or "starttls") not in ("starttls", "ssl", "none"):
            raise ValueError("Beveiliging moet starttls, ssl of none zijn")
        clean["security"] = str(clean.get("security") or "starttls")
    for key in ("server", "url"):
        val = str(clean.get(key) or "")
        if val and not val.startswith(("https://", "http://")):
            raise ValueError(f"{key} moet met http:// of https:// beginnen")
    return clean


def create_channel(ctx: AppContext, kind: str, name: str, config: dict[str, Any],
                   secret_values: dict[str, str], *, enabled: bool = True,
                   auto_created: bool = False) -> dict[str, Any]:
    clean = _validate(kind, config)
    now = utcnow()
    cid = ctx.db.execute_insert(
        "INSERT INTO notify_channels(kind,name,enabled,config_json,auto_created,created_at,"
        "updated_at) VALUES (?,?,?,?,?,?,?)",
        (kind, name.strip() or ch.KIND_LABELS_NL[kind], int(enabled), dumps(clean),
         int(auto_created), now, now))
    _store_secrets(ctx, cid, kind, secret_values)
    channel = get_channel(ctx, cid)
    assert channel is not None
    return channel


def update_channel(ctx: AppContext, channel_id: int, *, name: str | None = None,
                   enabled: bool | None = None, config: dict[str, Any] | None = None,
                   secret_values: dict[str, str] | None = None) -> dict[str, Any]:
    channel = get_channel(ctx, channel_id)
    if channel is None:
        raise KeyError(channel_id)
    sets: list[str] = ["updated_at=?"]
    args: list[Any] = [utcnow()]
    if name is not None:
        sets.append("name=?")
        args.append(name.strip() or channel["name"])
    if enabled is not None:
        sets.append("enabled=?")
        args.append(int(enabled))
    if config is not None:
        sets.append("config_json=?")
        args.append(dumps(_validate(channel["kind"], {**channel["config"], **config})))
    ctx.db.execute(f"UPDATE notify_channels SET {', '.join(sets)} WHERE id=?",  # noqa: S608
                   (*args, channel_id))
    if secret_values:
        _store_secrets(ctx, channel_id, channel["kind"], secret_values)
    updated = get_channel(ctx, channel_id)
    assert updated is not None
    return updated


def delete_channel(ctx: AppContext, channel_id: int) -> None:
    channel = get_channel(ctx, channel_id)
    if channel is None:
        raise KeyError(channel_id)
    for name, secret, _r in ch.FIELDS[channel["kind"]]:
        if secret:
            ctx.secrets.delete(ch.secret_name(channel_id, name))
    ctx.db.execute("DELETE FROM notify_outbox WHERE channel_id=?", (channel_id,))
    ctx.db.execute("DELETE FROM notify_channels WHERE id=?", (channel_id,))


def _store_secrets(ctx: AppContext, channel_id: int, kind: str,
                   values: dict[str, str]) -> None:
    allowed = {n for n, secret, _r in ch.FIELDS[kind] if secret}
    for key, value in (values or {}).items():
        if key not in allowed:
            continue
        if value:
            ctx.secrets.set(ch.secret_name(channel_id, key), str(value), actor="notify")
        else:
            ctx.secrets.delete(ch.secret_name(channel_id, key))


def public_channel(ctx: AppContext, channel: dict[str, Any]) -> dict[str, Any]:
    """Voor de UI: nooit geheimen, wel of ze ingesteld zijn (+ QR voor ntfy)."""
    out = {k: channel[k] for k in ("id", "kind", "name", "enabled", "auto_created",
                                   "last_ok_at", "last_error", "created_at")}
    out["enabled"] = bool(out["enabled"])
    out["auto_created"] = bool(out["auto_created"])
    out["kind_nl"] = ch.KIND_LABELS_NL[channel["kind"]]
    out["config"] = channel["config"]
    out["secrets_set"] = {n: ctx.secrets.has(ch.secret_name(int(channel["id"]), n))
                          for n, secret, _r in ch.FIELDS[channel["kind"]] if secret}
    if channel["kind"] == "ntfy":
        url = f"{channel['config'].get('server', 'https://ntfy.sh')}/" \
              f"{channel['config'].get('topic', '')}"
        out["subscribe_url"] = url
        out["qr_svg"] = qr_data_uri(url)
    return out


def qr_data_uri(text: str) -> str | None:
    try:
        import segno
    except ImportError:  # pragma: no cover - zit in de image
        return None
    return str(segno.make(text, error="m").svg_data_uri(scale=6, border=2, dark="#111111",
                                                         light="#ffffff"))


def ensure_default_ntfy(ctx: AppContext) -> dict[str, Any] | None:
    """Zero-config: maak eenmalig een privé ntfy-onderwerp aan. Het kanaal staat uit tot de
    gebruiker zich met de QR-code heeft geabonneerd en op 'Aanzetten' tikt — zo gaat er
    nooit iets naar buiten zonder dat iemand het ontvangt."""
    if ctx.db.fetch_one("SELECT 1 FROM notify_channels LIMIT 1") is not None:
        return None
    if ctx.state_get("notify.default_created"):
        return None
    topic = "mediaguard-" + secrets.token_urlsafe(18).replace("-", "x").replace("_", "y")
    channel = create_channel(ctx, "ntfy", "Telefoon (ntfy)",
                             {"server": "https://ntfy.sh", "topic": topic}, {},
                             enabled=False, auto_created=True)
    ctx.state_set("notify.default_created", True)
    return channel


# -- binnenkomende meldingen ------------------------------------------------------------------
def on_notification(ctx: AppContext, notification_id: int, severity: str, title: str,
                    body: str = "", link: str | None = None, *, is_new: bool = True,
                    dedupe_key: str | None = None, category: str | None = None) -> str:
    """Bepaal wat er met een melding buiten de app gebeurt. Faalt nooit naar buiten."""
    try:
        return _route(ctx, notification_id, severity, title, body, link, is_new=is_new,
                      dedupe_key=dedupe_key, category=category)
    except Exception:  # noqa: BLE001 - een melding mag het werk nooit breken
        LOGGER.exception("melding %s niet doorgezet", notification_id)
        return "error"


def _route(ctx: AppContext, notification_id: int, severity: str, title: str, body: str,
           link: str | None, *, is_new: bool, dedupe_key: str | None,
           category: str | None) -> str:
    cat = category or categorize(severity, title, link, dedupe_key)
    pref = preference(ctx, cat)
    if pref == "never":
        return "never"
    if not is_new:
        recent = (datetime.now(UTC) - timedelta(hours=REPEAT_HOURS)).isoformat(
            timespec="seconds")
        if ctx.db.fetch_one("SELECT 1 FROM notify_outbox WHERE notification_id=? AND "
                            "created_at>=?", (notification_id, recent)) is not None:
            return "repeat_suppressed"
        if ctx.db.fetch_one("SELECT 1 FROM notify_digest_items WHERE notification_id=? AND "
                            "created_at>=?", (notification_id, recent)) is not None:
            return "repeat_suppressed"
    if pref == "digest":
        _to_digest(ctx, notification_id, cat, severity, title)
        return "digest"
    channels = list_channels(ctx, enabled_only=True)
    if not channels:
        _to_digest(ctx, notification_id, cat, severity, title)
        return "no_channel"
    limit = int(ctx.config.value("notify.rate_per_hour"))
    hour_ago = (datetime.now(UTC) - timedelta(hours=1)).isoformat(timespec="seconds")
    queued = 0
    for channel in channels:
        n = ctx.db.fetch_one("SELECT COUNT(*) AS n FROM notify_outbox WHERE channel_id=? AND "
                             "created_at>=?", (channel["id"], hour_ago))
        if n and int(n["n"]) >= limit:
            continue
        enqueue(ctx, int(channel["id"]), cat, title, body, link,
                priority="high" if severity in ("error", "critical") else "default",
                notification_id=notification_id)
        queued += 1
    if not queued:
        _to_digest(ctx, notification_id, cat, severity, title)
        return "rate_limited"
    wake(ctx)
    return "direct"


def _to_digest(ctx: AppContext, notification_id: int | None, category: str, severity: str,
               title: str) -> None:
    ctx.db.execute("INSERT INTO notify_digest_items(notification_id,category,severity,title,"
                   "created_at) VALUES (?,?,?,?,?)",
                   (notification_id, category, severity, title, utcnow()))


def enqueue(ctx: AppContext, channel_id: int, category: str, title: str, body: str = "",
            link: str | None = None, *, priority: str = "default",
            notification_id: int | None = None, attachment: Path | None = None) -> int:
    now = utcnow()
    return ctx.db.execute_insert(
        "INSERT INTO notify_outbox(channel_id,notification_id,category,title,body,link,priority,"
        "attachment_path,next_attempt_at,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (channel_id, notification_id, category, title[:300], body[:4000], link, priority,
         str(attachment) if attachment else None, now, now))


def link_url(ctx: AppContext, link: str | None) -> str | None:
    base = str(ctx.config.value("notify.base_url") or "").rstrip("/")
    if not link:
        return None
    if link.startswith(("http://", "https://")):
        return link
    return f"{base}{link}" if base else None


# -- verzenden --------------------------------------------------------------------------------
async def dispatch(ctx: AppContext, *, limit: int = 30) -> dict[str, int]:
    """Verstuur wat klaarstaat; herhaal met oplopende wachttijd, geef het na 6x op."""
    now = utcnow()
    rows = ctx.db.fetch_dicts(
        "SELECT o.*, c.kind, c.enabled FROM notify_outbox o JOIN notify_channels c ON "
        "c.id=o.channel_id WHERE o.status='pending' AND o.next_attempt_at<=? "
        "ORDER BY o.id LIMIT ?", (now, limit))
    stats = {"sent": 0, "failed": 0, "retry": 0}
    for row in rows:
        channel = get_channel(ctx, int(row["channel_id"]))
        if channel is None:
            continue
        msg = ch.Message(title=row["title"], body=row["body"] or "", link=row["link"],
                         priority=row["priority"],
                         attachment=Path(row["attachment_path"]) if row["attachment_path"]
                         else None)
        try:
            await ch.send(ctx, channel, msg, link_url(ctx, row["link"]))
        except ch.ChannelError as exc:
            attempts = int(row["attempts"]) + 1
            give_up = not exc.retry or attempts >= len(BACKOFF_MINUTES)
            wait = BACKOFF_MINUTES[min(attempts, len(BACKOFF_MINUTES)) - 1]
            ctx.db.execute(
                "UPDATE notify_outbox SET attempts=?, last_error=?, status=?, next_attempt_at=? "
                "WHERE id=?",
                (attempts, str(exc), "failed" if give_up else "pending",
                 (datetime.now(UTC) + timedelta(minutes=wait)).isoformat(timespec="seconds"),
                 row["id"]))
            ctx.db.execute("UPDATE notify_channels SET last_error=?, updated_at=? WHERE id=?",
                           (str(exc), utcnow(), row["channel_id"]))
            stats["failed" if give_up else "retry"] += 1
            continue
        ctx.db.execute("UPDATE notify_outbox SET status='sent', sent_at=?, attempts=attempts+1,"
                       " last_error=NULL WHERE id=?", (utcnow(), row["id"]))
        ctx.db.execute("UPDATE notify_channels SET last_ok_at=?, last_error=NULL WHERE id=?",
                       (utcnow(), row["channel_id"]))
        stats["sent"] += 1
    return stats


async def send_test(ctx: AppContext, channel_id: int) -> dict[str, Any]:
    channel = get_channel(ctx, channel_id)
    if channel is None:
        raise KeyError(channel_id)
    msg = ch.Message(title="MediaGuard: testbericht",
                     body="Dit kanaal werkt. Hier krijg je voortaan meldingen van MediaGuard.",
                     link="/instellingen/meldingen")
    try:
        await ch.send(ctx, channel, msg, link_url(ctx, msg.link))
    except ch.ChannelError as exc:
        ctx.db.execute("UPDATE notify_channels SET last_error=?, updated_at=? WHERE id=?",
                       (str(exc), utcnow(), channel_id))
        return {"ok": False, "error": str(exc)}
    ctx.db.execute("UPDATE notify_channels SET last_ok_at=?, last_error=NULL WHERE id=?",
                   (utcnow(), channel_id))
    return {"ok": True}


def wake(ctx: AppContext) -> None:
    event = ctx.services.get("notify_wake")
    if isinstance(event, asyncio.Event):
        event.set()


# -- overzichten ------------------------------------------------------------------------------
def _tz(ctx: AppContext) -> ZoneInfo:
    try:
        return ZoneInfo(str(ctx.config.value("notify.timezone") or "Europe/Amsterdam"))
    except Exception:  # noqa: BLE001 - onbekende tijdzone: Amsterdam
        return ZoneInfo("Europe/Amsterdam")


def _size(blob: str | None) -> int:
    data = loads(blob, {}) if blob else {}
    try:
        return int((data or {}).get("size") or 0)
    except (TypeError, ValueError):
        return 0


def period_stats(ctx: AppContext, start: str, end: str) -> dict[str, Any]:
    """Cijfers voor [start, end) in UTC-ISO."""
    encoded, saved = 0, 0
    for row in ctx.db.fetch_dicts(
            "SELECT source_snapshot_json, staged_output_json FROM encoding_jobs WHERE "
            "status='committed' AND updated_at>=? AND updated_at<?", (start, end)):
        encoded += 1
        saved += max(0, _size(row["source_snapshot_json"]) - _size(row["staged_output_json"]))
    delivered = _count(ctx, "SELECT COUNT(*) AS n FROM delivery_items WHERE "
                       "delivered_at>=? AND delivered_at<?", (start, end))
    errors = _count(ctx, "SELECT COUNT(*) AS n FROM notifications WHERE severity IN "
                    "('error','critical') AND ts>=? AND ts<?", (start, end))
    try:
        from mediaguard import attention

        waiting = int(attention.summary(ctx)["count"])
    except Exception:  # noqa: BLE001
        waiting = 0
    items = ctx.db.fetch_dicts(
        "SELECT category, severity, title, COUNT(*) AS n FROM notify_digest_items WHERE "
        "created_at>=? AND created_at<? GROUP BY category, title ORDER BY MAX(id) DESC LIMIT 15",
        (start, end))
    return {"encoded": encoded, "saved_bytes": saved, "delivered": delivered,
            "errors": errors, "waiting": waiting,
            "items": [{"category": i["category"], "category_nl":
                       CATEGORY_LABELS_NL.get(i["category"], i["category"]),
                       "severity": i["severity"], "title": i["title"], "count": int(i["n"])}
                      for i in items]}


def _count(ctx: AppContext, sql: str, args: tuple[Any, ...]) -> int:
    try:
        row = ctx.db.fetch_one(sql, args)
    except Exception:  # noqa: BLE001 - tabel (nog) niet aanwezig
        return 0
    return int(row["n"]) if row else 0


def gb_nl(n: int) -> str:
    # Overal dezelfde eenheid als de web-UI en de instellingen: 1 GB = 1024³ bytes
    # (rapport 7, N5: meldingen zeiden 689 GB waar de app 642 GB toonde voor hetzelfde).
    gb = n / 1024**3
    text = f"{gb:.1f}" if gb < 100 else f"{gb:.0f}"
    return text.replace(".", ",") + " GB"


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def summary_text(stats: dict[str, Any], label: str) -> str:
    parts = [_plural(stats["encoded"], "bestand", "bestanden") + " omgezet naar AV1"
             + (f" ({gb_nl(stats['saved_bytes'])} bespaard)" if stats["saved_bytes"] else ""),
             _plural(stats["delivered"], "aanvraag", "aanvragen") + " geleverd",
             _plural(stats["waiting"], "ding wacht", "dingen wachten") + " op jou"]
    if stats["errors"]:
        parts.append(_plural(stats["errors"], "fout", "fouten"))
    return f"{label}: " + ", ".join(parts) + "."


def _bounds(ctx: AppContext, kind: str, local_day: date) -> tuple[str, str]:
    tz = _tz(ctx)
    end_local = datetime(local_day.year, local_day.month, local_day.day, tzinfo=tz)
    start_local = end_local - (timedelta(days=7) if kind == "weekly" else timedelta(days=1))
    return (start_local.astimezone(UTC).isoformat(timespec="seconds"),
            end_local.astimezone(UTC).isoformat(timespec="seconds"))


def build_digest(ctx: AppContext, kind: str, local_day: date) -> dict[str, Any]:
    start, end = _bounds(ctx, kind, local_day)
    stats = period_stats(ctx, start, end)
    label = "Afgelopen week" if kind == "weekly" else "Gisteren"
    text = summary_text(stats, label)
    lines = [text]
    with contextlib.suppress(Exception):  # opslaglogboek: wat kwam erbij (6 nov)
        from mediaguard.storage_ledger import digest_line

        storage = digest_line(ctx, start, end)
        if storage:
            stats["storage"] = storage
            lines.append(f"• Opslag: {storage['text_nl']}")
    with contextlib.suppress(Exception):  # vangnet niet-geïmporteerde downloads (6 nov)
        from mediaguard.downloads.recovery import digest_line as recovery_line

        rec = recovery_line(ctx, start)
        if rec:
            stats["download_recovery"] = rec
            lines.append(f"• Downloads: {rec}")
    for item in stats["items"][:10]:
        suffix = f" ({item['count']}×)" if item["count"] > 1 else ""
        lines.append(f"• {item['category_nl']}: {item['title']}{suffix}")
    return {"kind": kind, "period_start": start, "period_end": end, "stats": stats,
            "text_nl": text, "body_nl": "\n".join(lines[1:]),
            "title_nl": "MediaGuard weekoverzicht" if kind == "weekly"
            else "MediaGuard dagoverzicht"}


def store_and_send_digest(ctx: AppContext, digest: dict[str, Any], *, send: bool = True) -> bool:
    """Bewaar het overzicht (eenmalig per periode) en zet het klaar voor alle kanalen."""
    try:
        ctx.db.execute_insert(
            "INSERT INTO notify_digests(kind,period_start,period_end,summary_json,text_nl,"
            "created_at) VALUES (?,?,?,?,?,?)",
            (digest["kind"], digest["period_start"], digest["period_end"],
             dumps({**digest["stats"], "body_nl": digest["body_nl"]}), digest["text_nl"],
             utcnow()))
    except Exception:  # noqa: BLE001 - UNIQUE: al gemaakt
        return False
    if send:
        for channel in list_channels(ctx, enabled_only=True):
            enqueue(ctx, int(channel["id"]), "digest", digest["title_nl"],
                    digest["text_nl"] + ("\n" + digest["body_nl"] if digest["body_nl"] else ""),
                    "/activiteit?tab=gebeurd", priority="low")
        wake(ctx)
    return True


def due_digests(ctx: AppContext, now: datetime | None = None) -> list[dict[str, Any]]:
    """Welke overzichten nu verstuurd moeten worden (na de ingestelde tijd)."""
    tz = _tz(ctx)
    local = (now or datetime.now(UTC)).astimezone(tz)
    try:
        hh, mm = (int(x) for x in str(ctx.config.value("notify.digest.time")).split(":", 1))
    except ValueError:
        hh, mm = 8, 0
    if (local.hour, local.minute) < (hh, mm):
        return []
    out = []
    today = local.date()
    if bool(ctx.config.value("notify.digest.daily")):
        out.append(build_digest(ctx, "daily", today))
    if bool(ctx.config.value("notify.digest.weekly")) and \
            local.isoweekday() == int(ctx.config.value("notify.digest.weekday")):
        out.append(build_digest(ctx, "weekly", today))
    existing = {(r["kind"], r["period_start"]) for r in ctx.db.fetch_dicts(
        "SELECT kind, period_start FROM notify_digests ORDER BY id DESC LIMIT 20")}
    return [d for d in out if (d["kind"], d["period_start"]) not in existing]


def today_so_far(ctx: AppContext) -> dict[str, Any]:
    tz = _tz(ctx)
    local = datetime.now(UTC).astimezone(tz)
    start = datetime(local.year, local.month, local.day, tzinfo=tz).astimezone(UTC)
    stats = period_stats(ctx, start.isoformat(timespec="seconds"),
                         datetime.now(UTC).isoformat(timespec="seconds"))
    return {"stats": stats, "text_nl": summary_text(stats, "Vandaag tot nu toe")}


def history(ctx: AppContext, limit: int = 30) -> list[dict[str, Any]]:
    out = []
    for row in ctx.db.fetch_dicts("SELECT * FROM notify_digests ORDER BY period_start DESC, "
                                  "id DESC LIMIT ?", (limit,)):
        summary = loads(row["summary_json"], {})
        out.append({"id": row["id"], "kind": row["kind"], "period_start": row["period_start"],
                    "period_end": row["period_end"], "text_nl": row["text_nl"],
                    "stats": summary, "created_at": row["created_at"]})
    return out


# -- achtergrond ------------------------------------------------------------------------------
async def loop(ctx: AppContext) -> None:
    event = asyncio.Event()
    ctx.services["notify_wake"] = event
    await asyncio.sleep(5)
    while True:
        with contextlib.suppress(Exception):
            await dispatch(ctx)
        with contextlib.suppress(Exception):
            for digest in due_digests(ctx):
                store_and_send_digest(ctx, digest)
        with contextlib.suppress(Exception):
            from mediaguard.notify import offsite

            await offsite.maybe_run(ctx)
        from mediaguard import taskwatch

        taskwatch.ok(ctx, "notify")
        event.clear()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(event.wait(), timeout=60)


def outbox(ctx: AppContext, limit: int = 30) -> list[dict[str, Any]]:
    rows = ctx.db.fetch_dicts(
        "SELECT o.id, o.channel_id, c.name AS channel_name, o.category, o.title, o.status, "
        "o.attempts, o.last_error, o.created_at, o.sent_at FROM notify_outbox o LEFT JOIN "
        "notify_channels c ON c.id=o.channel_id ORDER BY o.id DESC LIMIT ?", (limit,))
    for r in rows:
        r["category_nl"] = CATEGORY_LABELS_NL.get(r["category"], "Overzicht")
    return rows

