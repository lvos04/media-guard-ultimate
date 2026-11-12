"""Transacties aanmaken (core-rol), herstelbaarheid, retentie. Uitvoering gebeurt
uitsluitend door de recovery-rol via jobs ``recovery.*``/``quarantine.*``."""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mediaguard import jobs
from mediaguard.db import dumps, loads, utcnow
from mediaguard.settings_registry import SettingDef

if TYPE_CHECKING:
    from mediaguard.context import AppContext

CATEGORY = "Quarantaine & herstel"
HEARTBEAT_KEY = "recovery_heartbeat"


def setting_defs() -> list[SettingDef]:
    lib_scopes = ("global", "media_type", "instance", "library")
    return [
        SettingDef("recovery.hardlink_policy", "enum", "skip", CATEGORY,
                   "Bestanden met hardlinks (seeding)", "Hardlinked files",
                   choices=("skip", "break_with_confirmation"),
                   choice_labels_nl={"skip": "Overslaan (standaard)",
                                     "break_with_confirmation":
                                         "Hardlink verbreken na bevestiging per bestand"},
                   help_nl="Hardlinks worden nooit stil verbroken. Verbreken kost extra ruimte: "
                           "de seedende kopie blijft bestaan.", scopes=lib_scopes),
        SettingDef("library.write_enabled", "bool", False, "Veiligheid",
                   "MediaGuard mag bibliotheekbestanden vervangen",
                   "MediaGuard may replace library files",
                   help_nl="Staat uit tot je de overstap (cutover) hebt gedaan of bij een verse "
                           "installatie 'Bibliotheek vrijgeven' kiest. Tot dan worden geen "
                           "bestanden in je bibliotheek vervangen of teruggezet; analyses, "
                           "proefencodes en dry-runs werken wel.",
                   effect_nl="Aanzetten gebeurt automatisch bij een geslaagde overstap."),
        SettingDef("recovery.min_free_gb", "int", 20, CATEGORY, "Minimale vrije ruimte (GB)",
                   "Minimum free space (GB)",
                   help_nl="Reserve die na kopiëren vrij moet blijven op doel en quarantaine.",
                   minimum=0, maximum=100000, unit="GB"),
        SettingDef("recovery.quarantine_location", "enum", "library_root", CATEGORY,
                   "Locatie quarantaine", "Quarantine location",
                   choices=("library_root", "central"),
                   choice_labels_nl={"library_root": "In dezelfde dataset als de bibliotheek "
                                                     "(snel, hardlink)",
                                     "central": "Centrale quarantainemap (kopie)"},
                   help_nl="Binnen dezelfde dataset wordt het origineel met een hardlink "
                           "veiliggesteld (geen kopie nodig); anders kopie + sha256-controle."),
        SettingDef("recovery.refresh_arr_after_commit", "bool", True, CATEGORY,
                   "Sonarr/Radarr laten herscannen na vervanging",
                   "Rescan Sonarr/Radarr after replacement"),
        SettingDef("recovery.zfs_snapshot_enabled", "bool", False, CATEGORY,
                   "ZFS-snapshot vóór vervanging", "ZFS snapshot before replacement",
                   help_nl="Extra vangnet via de TrueNAS-API; vervangt de quarantaine niet."),
        SettingDef("recovery.truenas_url", "string", "", CATEGORY, "TrueNAS-API-URL",
                   "TrueNAS API URL", help_nl="Bijv. https://truenas.local. API-key via "
                   "geheim 'truenas:apikey'.", advanced=True),
        SettingDef("recovery.truenas_verify_tls", "bool", True, CATEGORY,
                   "TLS-certificaat TrueNAS controleren", "Verify TrueNAS TLS", advanced=True),
        SettingDef("recovery.zfs_dataset", "string", "", CATEGORY, "Dataset voor snapshots",
                   "Dataset for snapshots", help_nl="Bijv. voorbeeldpool/media", advanced=True),
        SettingDef("quarantine.mode", "enum", "retain", CATEGORY,
                   "Originelen na vervanging", "Originals after replacement",
                   choices=("retain", "immediate_after_check"),
                   choice_labels_nl={
                       "retain": "Bewaren volgens bewaartermijn (standaard)",
                       "immediate_after_check": "Direct verwijderen zodra het nieuwe bestand "
                                                "de controle heeft doorstaan"},
                   help_nl="'Direct verwijderen' maakt de ruimte binnen minuten vrij. Je kunt "
                           "dan niet meer terugzetten; opnieuw downloaden blijft mogelijk. Het "
                           "origineel verdwijnt nooit vóórdat de vrijgavecontrole slaagt.",
                   effect_nl="Geldt voor nieuwe en bestaande gecontroleerde originelen."),
        SettingDef("quarantine.safety_net_hours", "int", 48, CATEGORY,
                   "Vangnet bij direct verwijderen (uur)", "Safety net in direct mode (hours)",
                   help_nl="Ook bij 'Direct verwijderen' blijft een vervangen origineel zo lang "
                           "verborgen bewaard, zodat je het nog kunt terugzetten als de "
                           "AV1-versie tegenvalt. 0 = echt direct verwijderen.",
                   minimum=0, maximum=720, unit="uur"),
        SettingDef("quarantine.safety_net_gb", "int", 500, CATEGORY,
                   "Maximale omvang vangnet (GB)", "Safety net size (GB)",
                   help_nl="Komt het vangnet (inclusief originelen die nog gecontroleerd "
                           "worden) boven dit maximum, of zou de volgende vervanging erboven "
                           "uitkomen, dan ruimt MediaGuard alvast de oudste gecontroleerde "
                           "originelen op tot ruim eronder (zie 'Opruimen tot'). Originelen "
                           "die nog niet gecontroleerd zijn blijven altijd staan.",
                   minimum=0, maximum=1000000, unit="GB"),
        SettingDef("quarantine.purge_target_percent", "int", 85, CATEGORY,
                   "Opruimen tot (% van het maximum)", "Purge down to (% of the cap)",
                   help_nl="Bij een vol vangnet of volle quarantaine ruimt MediaGuard op tot "
                           "dit percentage van het maximum, niet tot net eronder. Zo hoeft de "
                           "AV1-wachtrij niet bij elk groot origineel (een 4K-remux is 40-60 "
                           "GB) opnieuw even te wachten.",
                   minimum=50, maximum=100, unit="%"),
        SettingDef("quarantine.retention_days", "int", 7, CATEGORY,
                   "Bewaartermijn originelen (dagen)", "Original retention (days)",
                   help_nl="Zo lang blijft een origineel minimaal herstelbaar na vervanging. "
                           "Daarna wordt het opgeruimd, maar alleen als de vrijgavecontrole "
                           "van het nieuwe bestand is geslaagd. 0 = direct na de controle.",
                   minimum=0, maximum=3650, unit="dagen"),
        SettingDef("quarantine.auto_purge", "bool", True, CATEGORY,
                   "Verlopen originelen automatisch opruimen", "Auto-purge expired originals",
                   help_nl="Nooit binnen dezelfde transactie, nooit vastgezette items en nooit "
                           "zonder geslaagde vrijgavecontrole."),
        SettingDef("quarantine.max_size_gb", "int", 500, CATEGORY,
                   "Maximale omvang quarantaine (GB)", "Maximum quarantine size (GB)",
                   help_nl="Boven dit maximum worden eerst de oudste gecontroleerde originelen "
                           "opgeruimd (na de minimale leeftijd). Lukt dat niet, dan pauzeert "
                           "de AV1-wachtrij in plaats van de schijf vol te laten lopen.",
                   minimum=1, maximum=1000000, unit="GB"),
        SettingDef("quarantine.min_age_hours", "int", 24, CATEGORY,
                   "Minimale bewaartijd bij vol quotum (uur)",
                   "Minimum age before quota purge (hours)",
                   help_nl="Ook als het maximum is bereikt, blijft een origineel minstens zo "
                           "lang staan, zodat je fouten nog kunt opmerken.",
                   minimum=0, maximum=8760, unit="uur"),
        SettingDef("quarantine.release_check_full_decode", "bool", True, CATEGORY,
                   "Volledige decodecontrole vóór opruimen",
                   "Full decode check before purge",
                   help_nl="Decodeert het nieuwe bestand volledig op fouten voordat het "
                           "origineel weg mag. Kost CPU-tijd, draait met lage prioriteit."),
        SettingDef("quarantine.release_check_timeout_minutes", "int", 240, CATEGORY,
                   "Maximale duur decodecontrole (minuten)", "Decode check timeout (minutes)",
                   minimum=5, maximum=1440, unit="min", advanced=True),
        SettingDef("integrity.release_check_before_purge", "bool", True, "Veiligheid",
                   "Vrijgavecontrole vereist vóór automatisch opruimen",
                   "Release check required before automatic purge",
                   help_nl="Altijd aan. Handmatig verwijderen kan wel, met extra bevestiging.",
                   integrity_locked=True),
        SettingDef("integrity.recovery_verify_checksums", "bool", True, "Veiligheid",
                   "Checksums controleren bij kopiëren en activeren",
                   "Verify checksums on copy and activation",
                   help_nl="Altijd aan. Kan niet worden uitgezet.", integrity_locked=True),
    ]


def _quarantine_writable(ctx: AppContext) -> bool:
    qdir = ctx.settings.quarantine_dir
    return qdir.is_dir() and os.access(qdir, os.W_OK)


def mark_heartbeat(ctx: AppContext) -> None:
    # Schrijfbaarheid gemeten door de recovery-container zelf: core heeft de quarantainemap
    # bewust alleen-lezen gekoppeld en kan dit dus niet beoordelen.
    ctx.state_set(HEARTBEAT_KEY, {"ts": utcnow(), "pid": os.getpid(), "role": ctx.settings.role,
                                  "quarantine_writable": _quarantine_writable(ctx)})


def readiness(ctx: AppContext) -> dict[str, Any]:
    beat = ctx.state_get(HEARTBEAT_KEY)
    alive = False
    if beat:
        age = datetime.now(UTC) - datetime.fromisoformat(beat["ts"])
        alive = age < timedelta(seconds=180)
    qdir = ctx.settings.quarantine_dir
    if beat and "quarantine_writable" in beat:
        writable = bool(beat["quarantine_writable"])
    else:
        writable = _quarantine_writable(ctx)
    reasons = []
    if not alive:
        reasons.append("Recovery-worker meldt zich niet (heartbeat ontbreekt)")
    if not writable and ctx.config.value("recovery.quarantine_location") == "central":
        reasons.append("Quarantainemap niet schrijfbaar")
    locked = not ctx.config.value("library.write_enabled")
    if locked:
        reasons.append("Bibliotheek nog vergrendeld: doe eerst de overstap of kies "
                       "'Bibliotheek vrijgeven'")
    return {"ready": not reasons, "library_locked": locked, "heartbeat": beat,
            "quarantine_dir": str(qdir),
            "quarantine_writable": writable, "reasons": reasons}


def create_transaction(
    ctx: AppContext, *, library_path: str, staged_path: str,
    source_snapshot: dict[str, Any], staged_size: int | None = None,
    staged_sha256: str | None = None, instance_id: str | None = None,
    media_kind: str | None = None, provider_id: str | int | None = None,
    encoding_job_id: int | None = None, hardlink_break_confirmed: bool = False,
    refresh: dict[str, Any] | None = None, actor: str = "system",
    correlation_id: str | None = None, kind: str = "replace",
    restore_of_item_id: int | None = None, final_path: str | None = None,
) -> tuple[int, int]:
    """Maak een transactie + recovery-job. Geeft (transaction_id, job_id)."""
    for key in ("size", "mtime_ns", "inode", "device"):
        if key not in source_snapshot:
            raise ValueError(f"source_snapshot mist '{key}'")
    lib = Path(library_path)
    if final_path is None:
        staged_suffix = Path(staged_path).suffix
        final_path = str(lib.with_suffix(staged_suffix)) if staged_suffix and \
            staged_suffix.lower() != lib.suffix.lower() else library_path
    now = utcnow()
    txn_id = ctx.db.execute_insert(
        "INSERT INTO replacement_transactions(kind,instance_id,media_kind,provider_id,"
        "library_path,final_path,staged_path,staged_size,staged_sha256,source_snapshot_json,"
        "hardlink_break_confirmed,encoding_job_id,restore_of_item_id,state,refresh_json,"
        "correlation_id,created_at,created_by,updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,'pending',?,?,?,?,?)",
        (kind, instance_id, media_kind, None if provider_id is None else str(provider_id),
         library_path, final_path, staged_path, staged_size, staged_sha256,
         dumps(source_snapshot), int(hardlink_break_confirmed), encoding_job_id,
         restore_of_item_id, dumps(refresh or {}), correlation_id or uuid.uuid4().hex, now,
         actor, now))
    job_id = jobs.enqueue(ctx.db, f"recovery.{kind}", {"transaction_id": txn_id},
                          instance_id=instance_id, target_type="transaction", target_id=txn_id,
                          idempotency_key=f"recovery.{kind}:{txn_id}", max_attempts=1,
                          created_by=actor, correlation_id=correlation_id, priority=10)
    runner = ctx.services.get("jobs")
    if runner is not None:
        runner.wake()
    return txn_id, job_id


def create_restore(ctx: AppContext, item_id: int, actor: str) -> tuple[int, int]:
    item = ctx.db.fetch_one("SELECT * FROM quarantine_items WHERE id=?", (item_id,))
    if item is None:
        raise LookupError("Quarantaine-item niet gevonden")
    if item["status"] != "restorable":
        raise ValueError(f"Item is niet herstelbaar (status {item['status']})")
    origin = ctx.db.fetch_one("SELECT * FROM replacement_transactions WHERE id=?",
                              (item["transaction_id"],))
    if origin is None or origin["state"] != "committed":
        raise ValueError("Bijbehorende transactie is niet afgerond")
    current = Path(origin["final_path"])
    try:
        st = os.stat(current)
    except FileNotFoundError:
        current = Path(item["original_path"])
        try:
            st = os.stat(current)
        except FileNotFoundError as exc:
            from mediaguard.recovery.release_check import wrong_file

            if wrong_file(dict(origin), dict(item)):
                # verbeterlijst 12: een verkeerd bestand dat Sonarr/Radarr na het
                # veiligstellen weghaalden. Terugzetten op de lege plek (er wordt niets
                # vervangen); daarna laat MediaGuard Sonarr/Radarr herscannen.
                return create_transaction(
                    ctx, kind="restore", library_path=item["original_path"],
                    final_path=item["original_path"], staged_path=item["quarantine_path"],
                    staged_size=item["size"], staged_sha256=item["sha256"],
                    source_snapshot={"path": item["original_path"], "size": 0, "mtime_ns": 0,
                                     "inode": 0, "device": 0, "nlink": 0, "empty": True},
                    instance_id=item["instance_id"], media_kind=item["media_kind"],
                    provider_id=item["provider_id"], restore_of_item_id=item_id,
                    refresh=loads(origin["refresh_json"], {}), actor=actor)
            raise ValueError("Op de oorspronkelijke plek staat geen bestand meer om te "
                             "vervangen; het bewaarde bestand staat in "
                             f"{item['quarantine_path']}") from exc
    snapshot = {"path": str(current), "size": st.st_size, "mtime_ns": st.st_mtime_ns,
                "inode": st.st_ino, "device": st.st_dev, "nlink": st.st_nlink}
    return create_transaction(
        ctx, kind="restore", library_path=str(current), final_path=item["original_path"],
        staged_path=item["quarantine_path"], staged_size=item["size"],
        staged_sha256=item["sha256"], source_snapshot=snapshot,
        instance_id=item["instance_id"], media_kind=item["media_kind"],
        provider_id=item["provider_id"], restore_of_item_id=item_id,
        refresh=loads(origin["refresh_json"], {}), actor=actor,
        encoding_job_id=origin["encoding_job_id"])


def purge_target_fraction(ctx: AppContext) -> float:
    try:
        pct = int(ctx.config.value("quarantine.purge_target_percent"))
    except Exception:  # noqa: BLE001 - oude database zonder instelling
        pct = 85
    return max(50, min(100, pct)) / 100


def effective_cap_bytes(ctx: AppContext) -> int:
    """Het maximum dat nu geldt: quarantaine-quotum, en bij 'Direct verwijderen' met vangnet
    ook het vangnetmaximum (het kleinste van de twee)."""
    from mediaguard.recovery.release_check import quota_bytes

    cap = quota_bytes(ctx)
    if immediate_mode(ctx):
        hours, net = safety_net(ctx)
        if hours > 0 and net > 0:
            cap = min(cap, net)
    return cap


def pending_handoff_bytes(ctx: AppContext) -> int:
    """Grootte van het grootste origineel dat binnenkort de quarantaine in gaat (AV1-resultaat
    dat op activatie wacht): daar maken we vooraf ruimte voor (rapport 7, A1)."""
    rows = ctx.db.fetch_all(
        "SELECT source_snapshot_json FROM encoding_jobs WHERE status IN "
        "('awaiting_activation','awaiting_commit','handing_off','verifying')")
    biggest = 0
    for r in rows:
        snap = loads(r["source_snapshot_json"], {}) or {}
        try:
            biggest = max(biggest, int(snap.get("size") or 0))
        except (TypeError, ValueError, AttributeError):
            continue
    return biggest


def _cap_overflow(ctx: AppContext, chosen: list[dict[str, Any]], usage: int,
                  cap: int) -> int:
    """Hoeveel er (extra) weg moet: alleen als gebruik + verwacht origineel boven het maximum
    komt, en dan tot ``purge_target_percent`` van het maximum (hysterese)."""
    if cap <= 0:
        return 0
    left = usage - sum(int(q["size"] or 0) for q in chosen)
    reserve = pending_handoff_bytes(ctx)
    if left + reserve <= cap:
        return 0
    return int(left + reserve - cap * purge_target_fraction(ctx))


def purge_candidates(ctx: AppContext, *, now: datetime | None = None) -> list[dict[str, Any]]:
    """Automatisch op te ruimen originelen. Altijd vereist: afgeronde transactie, niet
    vastgezet, geslaagde vrijgavecontrole. Daarnaast óf de bewaartermijn verstreken, óf
    het quotum overschreden (dan oudste eerst, na de minimale leeftijd)."""
    now = now or datetime.now(UTC)
    immediate = immediate_mode(ctx)
    if immediate:
        return _safety_net_candidates(ctx, now)
    days = int(ctx.config.value("quarantine.retention_days"))
    min_hours = int(ctx.config.value("quarantine.min_age_hours"))
    # +1 s zodat 'direct' ook items van exact dit moment meeneemt
    cutoff = (now - timedelta(days=days) + timedelta(seconds=1)).isoformat(timespec="seconds")
    floor = (now - timedelta(hours=min_hours) + timedelta(seconds=1)).isoformat(
        timespec="seconds")
    eligible = ctx.db.fetch_dicts(
        "SELECT q.* FROM quarantine_items q JOIN replacement_transactions t "
        "ON t.id=q.transaction_id JOIN quarantine_release_checks c ON c.item_id=q.id "
        "WHERE q.status='restorable' AND q.pinned=0 AND t.state='committed' "
        "AND c.status='passed' AND q.created_at < ? ORDER BY q.created_at, q.id", (floor,))
    chosen = [q for q in eligible if q["created_at"] < cutoff]
    from mediaguard.recovery.release_check import quarantine_usage_bytes

    over = _cap_overflow(ctx, chosen, quarantine_usage_bytes(ctx), effective_cap_bytes(ctx))
    for q in eligible:
        if over <= 0:
            break
        if q in chosen:
            continue
        q["purge_reason"] = "quotum"
        chosen.append(q)
        over -= int(q["size"] or 0)
    for q in chosen:
        q.setdefault("purge_reason", "bewaartermijn")
    return chosen


def safety_net(ctx: AppContext) -> tuple[int, int]:
    """(uren, bytes) van het vangnet bij 'Direct verwijderen' (rapport 5, M5)."""
    try:
        hours = int(ctx.config.value("quarantine.safety_net_hours"))
        gb = int(ctx.config.value("quarantine.safety_net_gb"))
    except Exception:  # noqa: BLE001 - instelling ontbreekt in oude databases
        return 0, 0
    return max(0, hours), max(0, gb) * 1024**3


def _safety_net_candidates(ctx: AppContext, now: datetime) -> list[dict[str, Any]]:
    """Direct-modus: originelen blijven ``safety_net_hours`` verborgen bewaard (terugzetten
    kan dan nog), daarna weg; wordt het vangnet groter dan ``safety_net_gb``, dan gaan de
    oudste eerder. Altijd pas na een geslaagde vrijgavecontrole."""
    hours, _ = safety_net(ctx)
    eligible = ctx.db.fetch_dicts(
        "SELECT q.* FROM quarantine_items q JOIN replacement_transactions t "
        "ON t.id=q.transaction_id JOIN quarantine_release_checks c ON c.item_id=q.id "
        "WHERE q.status='restorable' AND q.pinned=0 AND t.state='committed' "
        "AND c.status='passed' ORDER BY q.created_at, q.id")
    cutoff = (now - timedelta(hours=hours) + timedelta(seconds=1)).isoformat(
        timespec="seconds")
    chosen = [q for q in eligible if hours == 0 or q["created_at"] < cutoff]
    for q in chosen:
        q["purge_reason"] = "direct" if hours == 0 else "vangnet verlopen"
    keep = [q for q in eligible if q not in chosen]
    from mediaguard.recovery.release_check import quarantine_usage_bytes

    # één getal: álle bewaarde originelen, ook die nog gecontroleerd worden (rapport 7, A1)
    over = _cap_overflow(ctx, chosen, quarantine_usage_bytes(ctx), effective_cap_bytes(ctx))
    for q in keep:  # oudste eerst
        if over <= 0:
            break
        q["purge_reason"] = "vangnet vol"
        chosen.append(q)
        over -= int(q["size"] or 0)
    return chosen


def immediate_mode(ctx: AppContext) -> bool:
    return ctx.config.value("quarantine.mode") == "immediate_after_check" or \
        int(ctx.config.value("quarantine.retention_days")) == 0


def safety_net_view(ctx: AppContext) -> dict[str, Any]:
    """Inhoud van het vangnet bij 'Direct verwijderen' (rapport 6, N8)."""
    hours, max_bytes = safety_net(ctx)
    rows = ctx.db.fetch_dicts(
        "SELECT created_at, size FROM quarantine_items WHERE status='restorable' AND pinned=0 "
        "ORDER BY created_at, id")
    first = rows[0]["created_at"] if rows else None
    expiry = None
    if first:
        try:
            dt = datetime.fromisoformat(str(first).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            expiry = (dt + timedelta(hours=hours)).isoformat(timespec="seconds")
        except ValueError:
            expiry = None
    from mediaguard.recovery.release_check import quarantine_usage_bytes

    # één getal (rapport 7, A1): ook originelen die nog gecontroleerd worden tellen mee
    used = quarantine_usage_bytes(ctx)
    return {"items": len(rows), "bytes": used,
            "bytes_checked": sum(int(r["size"] or 0) for r in rows),
            "first_expiry_at": expiry, "full": bool(max_bytes) and used >= max_bytes}


def next_purge_at(ctx: AppContext, candidates: list[dict[str, Any]],
                  net: dict[str, Any] | None) -> str | None:
    """Wanneer de volgende automatische opruiming werkelijk iets weghaalt."""
    if not ctx.config.value("quarantine.auto_purge"):
        return None
    if candidates:
        return datetime.now(UTC).isoformat(timespec="seconds")
    if net and net.get("first_expiry_at"):
        return str(net["first_expiry_at"])
    if not immediate_mode(ctx):
        row = ctx.db.fetch_one(
            "SELECT MIN(created_at) AS t FROM quarantine_items WHERE status='restorable' "
            "AND pinned=0")
        if row and row["t"]:
            try:
                dt = datetime.fromisoformat(str(row["t"]).replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=UTC)
                days = int(ctx.config.value("quarantine.retention_days"))
                return (dt + timedelta(days=days)).isoformat(timespec="seconds")
            except ValueError:
                return None
    return None


def next_purge_nl(at: str | None, count: int) -> str:
    if count:
        return f"Nu: {count} origineel/originelen klaar om op te ruimen"
    if not at:
        return "Niets gepland"
    try:
        from zoneinfo import ZoneInfo

        dt = datetime.fromisoformat(at.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        local = dt.astimezone(ZoneInfo("Europe/Amsterdam"))
    except ValueError:
        return at
    months = ("jan", "feb", "mrt", "apr", "mei", "jun", "jul", "aug", "sep", "okt", "nov",
              "dec")
    return f"Eerste item gaat weg op {local.day} {months[local.month - 1]} {local:%H:%M}"
