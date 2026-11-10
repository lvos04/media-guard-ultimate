"""'Wacht op jou': alles waar MediaGuard op een mens wacht of door geblokkeerd is.

Eén plek die de verborgen wachttoestanden van alle modules verzamelt (zie
docs/WAITING_STATES.md), met een Nederlandse uitleg, een link naar de juiste pagina en
waar mogelijk een directe actie. Overzicht ('Nog te doen') en Activiteit → Problemen tonen
deze lijst; zo kan niets meer stil blijven hangen zonder dat de gebruiker het ziet.

Iedere bron staat in een eigen try-blok: één kapotte bron maakt de lijst nooit leeg.

Bewuste keuzes (een afwijkende veiligheidsinstelling, een instantie of AV1-baan die je zelf op
pauze zette, …) hebben een knop 'Bewust zo gekozen'. Dan verdwijnt het punt tot de
bijbehorende instelling verandert (verbeterlijst 8). Echte fouten kun je niet verbergen.
De bewaking op de achtergrond (``mediaguard.monitor``) pusht fouten en punten met
``push=True`` direct naar je telefoon.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from mediaguard.audit import audit
from mediaguard.auth import ROLE_RANK, Principal, require_operator, require_viewer
from mediaguard.context import AppContext, get_app_context
from mediaguard.db import dumps, loads, utcnow

LOGGER = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/attention", tags=["attention"])
MAX_ROWS = 20
SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}


def _act(label: str, path: str, *, body: dict[str, Any] | None = None, confirm: str | None = None,
         role: str = "operator", primary: bool = False) -> dict[str, Any]:
    return {"label_nl": label, "method": "post", "path": path, "body": body or {},
            "confirm_nl": confirm, "role": role, "primary": primary}


def _base(path: str | None) -> str:
    return (path or "").rsplit("/", 1)[-1] or "?"


def _item(key: str, severity: str, title: str, detail: str, href: str, *, count: int = 1,
          actions: list[dict[str, Any]] | None = None,
          rows: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {"key": key, "severity": severity, "title_nl": title, "detail_nl": detail,
            "href": href, "count": count, "actions": actions or [], "rows": rows or []}


# -- 'Bewust zo gekozen' ------------------------------------------------------------------------
DISMISSED_KEY = "attention_dismissed"
DISMISS_LABEL = "Bewust zo gekozen"
# Alleen punten die een bewuste keuze kunnen zijn; fouten en de bibliotheekvergrendeling niet.
DISMISSABLE_PREFIXES = ("settings.unsafe.", "instance.suspended.", "instance.shadow.",
                        "encoder.paused.", "storage.typo", "backup.bundle_missing",
                        "identity.ai_consent", "system.emergency_stop")


def dismissable(key: str) -> bool:
    return key.startswith(DISMISSABLE_PREFIXES)


def dismissed_map(ctx: AppContext) -> dict[str, dict[str, Any]]:
    data = ctx.state_get(DISMISSED_KEY, {})
    return data if isinstance(data, dict) else {}


def forget(ctx: AppContext, key: str) -> bool:
    data = dismissed_map(ctx)
    if key not in data:
        return False
    data.pop(key)
    ctx.state_set(DISMISSED_KEY, data)
    return True


def _hidden(ctx: AppContext, key: str, fingerprint: str | None) -> bool:
    """Verborgen met 'Bewust zo gekozen' en sindsdien niets veranderd? ``fingerprint=None``
    betekent: de situatie bestaat niet meer, dus de keuze vervalt (komt hij later terug, dan
    zie je hem weer)."""
    entry = dismissed_map(ctx).get(key)
    if entry is None:
        return False
    if fingerprint is not None and entry.get("fp") == fingerprint:
        return True
    forget(ctx, key)
    return False


def dismiss(ctx: AppContext, key: str, fingerprint: str, *, actor: str, title: str = "") -> None:
    if not dismissable(key):
        raise ValueError("Dit punt kun je niet verbergen")
    data = dismissed_map(ctx)
    data[key] = {"fp": str(fingerprint)[:500], "at": utcnow(), "by": actor,
                 "title": str(title)[:200]}
    ctx.state_set(DISMISSED_KEY, data)


# Een veiligheidswaarschuwing verbergen mag alleen wie de instelling zelf mag wijzigen. 'Geen
# herstelcode' hoort daarbij: zonder code is er na een schijfcrash geen herstelbundel.
ADMIN_DISMISS_PREFIXES = ("settings.unsafe.", "backup.bundle_missing")


def _dismiss_role(key: str) -> str:
    return "admin" if key.startswith(ADMIN_DISMISS_PREFIXES) else "operator"


def _dismiss_act(entries: list[tuple[str, str, str]], *, primary: bool = False) -> dict[str, Any]:
    """Knop 'Bewust zo gekozen' voor (sleutel, vingerafdruk, titel)-paren."""
    role = "admin" if any(_dismiss_role(k) == "admin" for k, _fp, _t in entries) \
        else "operator"
    return _act(DISMISS_LABEL, "/attention/dismiss", primary=primary, role=role,
                body={"entries": [{"key": k, "fingerprint": fp, "title": t}
                                  for k, fp, t in entries]},
                confirm="MediaGuard toont dit pas weer als je de instelling verandert. "
                        "Terughalen kan onder Activiteit → Problemen.")


# -- soort pauze van een AV1-baan (verbeterlijst 8) ---------------------------------------------
BENIGN_PAUSES = ("kijken", "handmatig", "stilte", "noodstop")


def pause_kind(reason: str | None) -> str:
    """'kijken' (iemand kijkt in Emby), 'handmatig', 'stilte' (stille uren), 'noodstop' of
    'probleem'. Alleen 'probleem' is een fout; de rest is een bewuste of tijdelijke keuze."""
    r = str(reason or "").strip().lower()
    if r.startswith(("kijken:", "jellyfin:")) or "iemand kijkt" in r \
            or "momenteel gekeken" in r:
        return "kijken"
    if "stiltevenster" in r or "stille uren" in r:
        return "stilte"
    if r.startswith("noodstop"):
        return "noodstop"
    if not r or r.startswith("handmatig"):
        return "handmatig"
    return "probleem"


# -- bronnen ----------------------------------------------------------------------------------
def _encoding(ctx: AppContext) -> list[dict[str, Any]]:
    from mediaguard.encoding import service as enc
    from mediaguard.encoding.jobstates import engine_href
    from mediaguard.textfmt import plain_nl

    out: list[dict[str, Any]] = []
    waiting = ctx.db.fetch_all("SELECT id FROM encoding_jobs WHERE status='awaiting_activation' "
                               "ORDER BY id LIMIT 200")
    if waiting:
        jobs = [j for r in waiting if (j := enc.get_job(ctx, int(r["id"])))]
        saving = sum(max(0, int((j.get("source_snapshot") or {}).get("size") or 0)
                         - int((j.get("staged_output") or {}).get("size") or 0)) for j in jobs)
        out.append(_item(
            "encoding.awaiting_activation", "warning",
            f"{len(jobs)} omgezet(te) bestand(en) wacht(en) op activatie",
            "Gecontroleerd en klaar; de besparing ("
            f"{saving / 1024**3:.1f} GB) telt mee zodra het origineel vervangen is.", "/av1",
            count=len(jobs),
            rows=[{"id": j["id"], "title": _base(j.get("source_path")),
                   "href": identity_href(ctx, j.get("instance_id") or "",
                                         j.get("media_kind") or "",
                                         j.get("provider_id") or ""),
                   "job_href": engine_href(ctx, j["id"]),
                   "detail": plain_nl(j.get("error") or ""), "actions": [
                       _act("Activeren", f"/encoding/jobs/{j['id']}/activate",
                            body={"confirm": True}, primary=True),
                       _act("Origineel houden", f"/encoding/jobs/{j['id']}/cancel")]}
                  for j in jobs[:MAX_ROWS]]))
    from mediaguard.encoding.autopilot import retry_states, stalled_since

    since = stalled_since(ctx)
    if since:
        from mediaguard.textfmt import time_nl

        out.append(_item(
            "encoding.stalled", "warning", f"AV1 staat stil sinds {time_nl(since)}",
            "Er is werk gepland, maar de autopilot heeft al meer dan een uur niets aan de "
            "encoder gegeven. MediaGuard probeert het elke ronde opnieuw; met 'Nu proberen' "
            "start je direct een ronde.", "/av1",
            actions=[_act("Nu proberen", "/encoding/autopilot/run", primary=True)]))
    from mediaguard.encoding import lanes

    idle = lanes.check_gpu_idle(ctx)  # verbeterpunt 6: GPU > 30 min leeg terwijl er werk is
    if idle:
        out.append(_item(
            lanes.GPU_IDLE_KEY, idle["severity"], f"AV1: GPU staat al {idle['duration_nl']} stil",
            idle["detail_nl"], "/av1",
            actions=[] if idle["cpu_only"] else [
                _act("Nu proberen", "/encoding/autopilot/run", primary=True)]))
    final = [(p, st) for p, st in retry_states(ctx).items() if st["state"] == "final"]
    if final:
        out.append(_item(
            "encoding.failed_final", "warning",
            f"{len(final)} bestand(en) lukken niet in AV1",
            "MediaGuard heeft het automatisch opnieuw geprobeerd (onderbrekingen tot 3 keer, "
            "inhoudelijke fouten één keer na een dag). Het origineel staat er nog; er is niets "
            "veranderd.", "/av1", count=len(final),
            rows=[{"id": st["job_id"], "title": _base(p),
                   "href": engine_href(ctx, st["job_id"]),
                   "detail": plain_nl(f"{st['attempts']}× · "
                                      f"{str(st['error'] or 'onbekende fout')[:300]}"),
                   "actions": []}
                  for p, st in final[:MAX_ROWS]]))
    conflicts = ctx.db.fetch_dicts(
        "SELECT id, source_path, error FROM encoding_jobs WHERE status='conflict_review' "
        "ORDER BY id DESC LIMIT 50")
    if conflicts:
        out.append(_item(
            "encoding.conflict", "error",
            f"{len(conflicts)} vervanging(en) met een conflict",
            "MediaGuard kon niet met zekerheid vaststellen of het origineel of het AV1-bestand "
            "actief is. Er is niets verwijderd; kies per bestand.", "/quarantaine",
            count=len(conflicts),
            rows=[{"id": c["id"], "title": _base(c["source_path"]),
                   "href": engine_href(ctx, c["id"]), "detail": plain_nl(c["error"] or ""),
                   "actions": [_act("Origineel houden", f"/encoding/jobs/{c['id']}/cancel",
                                    confirm="Het AV1-resultaat wordt weggegooid.")]}
                  for c in conflicts[:MAX_ROWS]]))
    return out


def _recovery(ctx: AppContext) -> list[dict[str, Any]]:
    from mediaguard.recovery import service as recovery_service
    from mediaguard.recovery.release_check import quota_exceeded

    out: list[dict[str, Any]] = []
    ready = recovery_service.readiness(ctx)
    other = [r for r in ready.get("reasons") or [] if "vergrendeld" not in r]
    if other:
        out.append(_item("recovery.not_ready", "error", "Vervangen van bestanden staat stil",
                         "; ".join(other) + ". Zolang dit zo is activeert MediaGuard geen "
                         "AV1-bestanden en zet het niets terug.", "/systeem"))
    if ready.get("library_locked"):
        active = ctx.db.fetch_one("SELECT COUNT(*) AS n FROM migration_runs WHERE "
                                  "status='active'")
        if not (active and int(active["n"])):
            out.append(_item(
                "library.locked", "warning", "Bibliotheek nog vergrendeld",
                "Er loopt geen migratie meer, maar MediaGuard mag nog geen bestanden vervangen. "
                "Zonder vrijgave worden omgezette AV1-bestanden nooit geactiveerd.", "/",
                actions=[_act("Bibliotheek vrijgeven", "/recovery/library-write",
                              body={"confirm": True}, role="admin", primary=True,
                              confirm="MediaGuard mag daarna bestanden vervangen (origineel "
                                      "gaat altijd eerst naar de quarantaine).")]))
    if quota_exceeded(ctx):
        from mediaguard.storage_forecast import pool_degraded

        # pool niet gezond: opruimen pauzeert (verbeterlijst 37), dus geen knop die toch faalt
        paused = bool(pool_degraded(ctx))
        out.append(_item("quarantine.full", "error", "Quarantaine vol: AV1 staat stil",
                         "De quarantaine heeft het ingestelde maximum bereikt. " + (
                             "Zolang de TrueNAS-pool niet gezond is, ruimt MediaGuard geen "
                             "originelen op; nieuwe encodes wachten tot de pool weer gezond is."
                             if paused else
                             "Nieuwe encodes starten pas als er originelen zijn opgeruimd of het "
                             "maximum omhoog gaat."), "/quarantaine",
                         actions=[] if paused else [
                             _act("Nu opruimen", "/quarantine/purge", body={"confirm": True},
                                  role="admin", primary=True,
                                  confirm="Alleen originelen waarvan de vervanging "
                                          "gecontroleerd is worden verwijderd.")]))
    stuck = ctx.db.fetch_dicts(
        "SELECT q.id, q.original_path, c.checks_json FROM quarantine_items q JOIN "
        "quarantine_release_checks c ON c.item_id=q.id WHERE q.status IN "
        "('restorable','held','pending') AND c.status='failed' AND c.attempts>=5 "
        "ORDER BY q.id LIMIT 100")
    if stuck:
        def why(raw: str) -> str:
            failed = [c for c in loads(raw, []) or [] if not c.get("passed")]
            return "; ".join(str(c.get("detail") or c.get("name")) for c in failed)[:200]
        out.append(_item(
            "quarantine.check_failed", "warning",
            f"{len(stuck)} origineel/originelen blijven bewaard: controle faalt steeds",
            "MediaGuard ruimt een origineel pas op als het nieuwe bestand de controle doorstaat. "
            "Bij deze bestanden lukt dat na 5 pogingen niet; ze nemen ruimte in.",
            "/quarantaine", count=len(stuck),
            rows=[{"id": s["id"], "title": _base(s["original_path"]),
                   "href": f"/quarantaine?item={int(s['id'])}",
                   "detail": why(s["checks_json"]), "actions": [
                       _act("Opnieuw controleren", f"/quarantine/{s['id']}/release-check",
                            primary=True),
                       _act("Origineel terugzetten", f"/quarantine/{s['id']}/restore",
                            body={"confirm": True},
                            confirm="Het origineel komt terug in de bibliotheek.")]}
                  for s in stuck[:MAX_ROWS]]))
    return out


def _storage(ctx: AppContext) -> list[dict[str, Any]]:
    """Schrijftest per hoofdmap: faalt die, dan lukt geen enkele AV1-vervanging daar."""
    from mediaguard.recovery import probe

    failing = probe.status(ctx)["failing"]
    if not failing:
        return []
    return [_item(
        "storage.write_probe", "error",
        f"Schrijven lukt niet in {len(failing)} bibliotheekmap(pen)",
        "MediaGuard test elk uur of het in elke hoofdmap kan doen wat een vervanging doet "
        "(aanmaken, schrijven, hernoemen, verwijderen). Uit deze mappen worden geen bestanden "
        "naar AV1 gestuurd tot de test slaagt; uitgestelde activaties gaan daarna vanzelf "
        "verder.", "/systeem#opslag", count=len(failing),
        actions=[_act("Opnieuw testen", "/storage/probe", primary=True)],
        rows=[{"id": r["path"], "title": r["path"],
               "detail": f"Mislukt bij '{r.get('step_nl') or r.get('step')}': {r.get('error')}",
               "actions": []} for r in failing[:MAX_ROWS]])]


def _typos(ctx: AppContext) -> list[dict[str, Any]]:
    """Mapnaam met een typfout: alleen op jouw bevestiging herstellen (media verhuist)."""
    from mediaguard import storage_typos

    found = storage_typos.detect_local(ctx)
    fp = "|".join(sorted(f"{t['instance_id']}:{t['path']}" for t in found)) if found else None
    if _hidden(ctx, "storage.typo", fp) or not found or fp is None:
        return []
    item = _item(
        "storage.typo", "info", f"{len(found)} mediamap(pen) met een typfout in de naam",
        "Bijvoorbeeld '" + found[0]["path"].rsplit("/", 1)[-1] + "' → '"
        + found[0]["suggested_path"].rsplit("/", 1)[-1] + "'. Herstellen laat Sonarr/Radarr de "
        "media naar de goed gespelde map verplaatsen; dat doet MediaGuard alleen als jij het "
        "bevestigt. Laten staan kan ook: alles werkt gewoon.", storage_typos.HREF,
        count=len(found),
        rows=[{"id": f"{t['instance_id']}:{t['path']}", "title": f"{t['instance_name']}: "
               f"{t['path']}", "detail": f"Voorstel: {t['suggested_path']} ({t['items']} items)",
               "actions": []} for t in found[:MAX_ROWS]],
        actions=[_dismiss_act([("storage.typo", fp, "Mapnamen met een typfout")])])
    item["needs_user"] = True
    return [item]


def _backup(ctx: AppContext) -> list[dict[str, Any]]:
    """Versleutelde herstelbundel nog niet ingesteld → 'Nog te doen' (rapport 4 nov, H10)."""
    from mediaguard.maintenance import bundle

    enabled = bool(bundle.status(ctx).get("enabled"))
    if _hidden(ctx, "backup.bundle_missing", None if enabled else "1") or enabled:
        return []
    item = _item(
        "backup.bundle_missing", "warning", "Wacht op jou: herstelcode instellen",
        "Eén ding kan MediaGuard niet zelf: een herstelcode die alleen jij kent. Tik op "
        "'Herstelcode instellen', bewaar de code die je dan één keer ziet (bijvoorbeeld in je "
        "wachtwoordmanager) — daarna maakt MediaGuard elke dag zelf een versleutelde "
        "herstelbundel waarmee alles na een schijfcrash terug kan.", "/systeem#herstelbundel",
        actions=[_dismiss_act([("backup.bundle_missing", "1", "Geen herstelcode")])])
    item["needs_user"] = True
    item["action_nl"] = "Herstelcode instellen"
    return [item]


def _safety(ctx: AppContext) -> list[dict[str, Any]]:
    from mediaguard.api.settings import SAFETY_KEYS, safety_deviations

    deviating = {r["key"]: r for r in safety_deviations(ctx)}
    rows = []
    for key in SAFETY_KEYS:
        r = deviating.get(key)
        # per instelling verbergen; een andere waarde (of terug naar standaard) wist de keuze
        if not _hidden(ctx, f"settings.unsafe.{key}", dumps(r["value"]) if r else None) and r:
            rows.append(r)
    if not rows:
        return []

    def entry(r: dict[str, Any]) -> tuple[str, str, str]:
        return (f"settings.unsafe.{r['key']}", dumps(r["value"]),
                f"{r['label_nl']}: {r['value_nl']}")

    return [_item(
        "settings.unsafe", "warning",
        f"{len(rows)} instelling(en) wijken af van de veilige standaard",
        "Deze keuzes kosten herstelmogelijkheden of kwaliteit. Bewust zo gekozen? Tik dan op "
        "'Bewust zo gekozen'; je ziet het pas weer als je de instelling verandert.",
        "/instellingen#veiligheid", count=len(rows),
        actions=[_act("Veilige standaard herstellen", "/settings/safety/restore", primary=True,
                      confirm="Alle hieronder genoemde instellingen gaan terug naar de "
                              "standaard."),
                 _dismiss_act([entry(r) for r in rows])],
        rows=[{"id": r["key"], "title": f"{r['label_nl']}: {r['value_nl']}",
               "detail": f"{r['why_nl']} Standaard: {r['default_nl']}.",
               "actions": [_act("Herstel standaard", "/settings/safety/restore",
                                body={"keys": [r["key"]]}),
                           _dismiss_act([entry(r)])]} for r in rows])]


def _instances(ctx: AppContext) -> list[dict[str, Any]]:
    from mediaguard.instances.registry import list_instances

    out: list[dict[str, Any]] = []
    active = ctx.db.fetch_one("SELECT COUNT(*) AS n FROM migration_runs WHERE status='active'")
    migrating = bool(active and int(active["n"]))
    for inst in list_instances(ctx):
        suspended = bool(inst.suspended)
        shadow = bool(inst.shadow_mode) and not migrating and not suspended
        skey, hkey = f"instance.suspended.{inst.id}", f"instance.shadow.{inst.id}"
        hide_s = _hidden(ctx, skey, "1" if suspended else None)
        hide_h = _hidden(ctx, hkey, "1" if shadow else None)
        if suspended and not hide_s:
            out.append(_item(
                skey, "warning", f"{inst.name} staat op pauze",
                "MediaGuard zoekt, downloadt en controleert niets voor deze instantie tot je "
                "hem hervat.", f"/instellingen/instanties#{inst.id}",
                actions=[_act("Hervatten", f"/instances/{inst.id}/resume", primary=True),
                         _dismiss_act([(skey, "1", f"{inst.name} op pauze")])]))
        elif shadow and not hide_h:
            out.append(_item(
                hkey, "warning", f"{inst.name} staat nog in proefmodus",
                "In proefmodus downloadt en importeert deze instantie niets. Er loopt geen "
                "migratie meer; rond de overstap af of zet de proefmodus uit.",
                f"/instellingen/instanties#{inst.id}",
                actions=[_dismiss_act([(hkey, "1", f"{inst.name} in proefmodus")])]))
    return out


def _routing(ctx: AppContext) -> list[dict[str, Any]]:
    # 'Anime of niet?' vraagt de gebruiker niets meer: de leveringsmotor laat de AI (of een
    # vaste regel) beslissen; terugdraaien kan op de Anime-pagina. Alleen als die motor uit
    # staat blijft de oude keuzelijst bestaan.
    if ctx.config.value("delivery.enabled"):
        return []
    rows = ctx.db.fetch_dicts(
        "SELECT id, title, source_instance, status, detail FROM anime_routing_actions WHERE "
        "status IN ('needs_review','suggested') ORDER BY id DESC LIMIT 100")
    if not rows:
        return []
    return [_item(
        "routing.review", "info", f"{len(rows)} titel(s): anime of niet?",
        "MediaGuard twijfelt of deze titels naar de anime-instantie horen. Kies per titel.",
        "/anime", count=len(rows),
        rows=[{"id": r["id"], "title": r["title"] or f"#{r['id']}", "detail": r["detail"] or "",
               "actions": [_act("Verplaatsen", f"/routing/anime/actions/{r['id']}/approve",
                                primary=True),
                           _act("Hier laten", f"/routing/anime/actions/{r['id']}/keep")]}
              for r in rows[:MAX_ROWS]])]


def _ai(ctx: AppContext) -> list[dict[str, Any]]:
    """Zero-interactie: AI-voorstellen wachten nooit meer op akkoord (ai.service handelt ze
    zelf af met een veilige standaard). De geschiedenis staat op de AI-pagina."""
    return []


def identity_href(ctx: AppContext, instance_id: str, kind: str,
                  provider_id: Any) -> str | None:
    """Link naar de afleverings- of filmpagina waar dit identiteitsresultaat over gaat."""
    from mediaguard.identity import service as identity

    if kind in ("episode", "episode_file"):
        ids = identity.episode_ids(ctx, instance_id, kind, provider_id)
        if not ids:
            return None
        row = ctx.db.fetch_one(
            "SELECT i.id FROM lib_episodes e JOIN lib_items i ON i.instance_id=e.instance_id "
            "AND i.kind='series' AND i.provider_id=e.series_provider_id WHERE "
            "e.instance_id=? AND e.provider_id=?", (instance_id, ids[0]))
        return f"/bibliotheek/{instance_id}/{row['id']}/aflevering/{ids[0]}" if row else None
    pid = str(provider_id)
    if not pid.isdigit():
        return None
    movie_id = int(pid)
    if kind == "movie_file":
        f = ctx.db.fetch_one("SELECT owner_provider_id FROM lib_files WHERE instance_id=? AND "
                             "kind='movie' AND provider_id=?", (instance_id, movie_id))
        if f is None:
            return None
        movie_id = int(f["owner_provider_id"])
    row = ctx.db.fetch_one("SELECT id FROM lib_items WHERE instance_id=? AND kind='movie' AND "
                           "provider_id=?", (instance_id, movie_id))
    return f"/bibliotheek/{instance_id}/{row['id']}" if row else None


def _identity(ctx: AppContext) -> list[dict[str, Any]]:
    from mediaguard import automation

    # alleen het laatste resultaat per bestand telt; een latere 'verified' lost het op
    rows = ctx.db.fetch_dicts(
        "SELECT r.* "
        "FROM identity_results r JOIN (SELECT instance_id, kind, provider_id, MAX(id) AS mid "
        "FROM identity_results GROUP BY instance_id, kind, provider_id) m ON m.mid=r.id "
        "WHERE r.status IN ('mismatch','uncertain') AND r.created_at >= datetime('now','-30 "
        "days') ORDER BY r.id DESC LIMIT 100")
    # alleen zwakke aanwijzingen (bestandsnaam, speelduur) is geen taak voor de gebruiker
    rows = [r for r in rows if not automation.weak_only(r)]
    if not rows:
        return []
    ai_on = automation.ai_ready(ctx) is not None
    mism = sum(1 for r in rows if r["status"] == "mismatch")
    out_rows: list[dict[str, Any]] = []
    for r in rows[:MAX_ROWS]:
        note = " · De AI beoordeelt dit automatisch" if ai_on and automation.conflicting(r) \
            else ""
        out_rows.append({
            "id": f"{r['instance_id']}:{r['kind']}:{r['provider_id']}",
            "title": _base(r["file_path"]),
            "href": identity_href(ctx, r["instance_id"], r["kind"], r["provider_id"]),
            "detail": ("Verkeerde aflevering" if r["status"] == "mismatch" else "Onzeker")
            + (f" · {r['reason_nl']}" if r["reason_nl"] else "") + note, "actions": []})
    first = next((r["href"] for r in out_rows if r["href"]), None)
    extra = []
    provider = automation.ai_missing_consent(ctx)
    ask = bool(provider and any(automation.conflicting(r) for r in rows))
    if ask and not _hidden(ctx, "identity.ai_consent", str(provider)):
        extra.append(_item(
            "identity.ai_consent", "info", "AI kan de twijfelgevallen zelf beoordelen",
            "Er is een API-sleutel ingesteld, maar nog geen toestemming om bestands- en "
            "releasenamen naar de provider te sturen. Na toestemming beslist de AI binnen je "
            "budget; alleen omkeerbare oordelen, nooit bestanden verwijderen.", "/ai",
            actions=[_act("Toestemming geven", "/ai/consent",
                          body={"provider": provider, "granted": True}, role="admin",
                          primary=True),
                     _dismiss_act([("identity.ai_consent", str(provider),
                                    "Geen AI-toestemming")])]))
    return extra + [_item(
        "identity.review", "warning" if mism else "info",
        f"{len(rows)} bestand(en) met twijfel over de juiste aflevering",
        ("Bewezen verkeerde afleveringen vervangt MediaGuard zelf"
         + (" en tegenstrijdig bewijs beoordeelt de AI" if ai_on else "")
         + ". Open een bestand om het bewijs te zien of het als juist te markeren."),
        first or "/bibliotheek", count=len(rows), rows=out_rows)]


def _dv(ctx: AppContext) -> list[dict[str, Any]]:
    rows = ctx.db.fetch_dicts("SELECT id, source_path FROM dv_tests WHERE state='verified' "
                              "ORDER BY id DESC LIMIT 50")
    if not rows:
        return []
    return [_item("dv.verified", "info", f"{len(rows)} Dolby Vision-proef(ven) klaar",
                  "De proefconversie is gecontroleerd. Bekijk het resultaat op je TV en "
                  "activeer of houd het origineel.", "/dolby-vision", count=len(rows))]


async def _encoder(ctx: AppContext) -> list[dict[str, Any]]:
    from mediaguard.encoding.client import (
        EncoderError,
        get_encoder_client,
        note_encoder_reachability,
    )

    try:
        health = await get_encoder_client(ctx).health()
        note_encoder_reachability(ctx, True)
    except EncoderError:
        if not note_encoder_reachability(ctx, False):
            return []  # één misser (druk of net herstart) is nog geen storing
        return [_item("encoder.offline", "error", "AV1-engine reageert niet",
                      "MediaGuard probeert het automatisch opnieuw en herstart de engine als "
                      "hij blijft hangen.", "/av1")]
    out = []
    paused_lanes = set()
    for p in health.get("lanes_paused") or []:
        reason = str(p.get("reason") or "")
        if "renderD" in reason or "GPU-baan uit" in reason:
            continue  # ontbrekende GPU: aparte 'Nog te doen'-regel met TrueNAS-uitleg
        lane = "GPU" if p.get("lane") == "gpu" else "CPU"
        key = f"encoder.paused.{p.get('lane')}"
        kind = pause_kind(reason)
        if kind in ("kijken", "noodstop", "stilte"):
            continue  # iemand kijkt, noodstop, stille uren: bewust en tijdelijk, geen taak
        if kind == "handmatig":
            paused_lanes.add(key)
            if _hidden(ctx, key, reason):
                continue
            out.append(_item(key, "info", f"AV1 {lane}-baan staat op pauze",
                             "Je hebt deze baan zelf gepauzeerd; nieuwe encodes wachten tot je "
                             "hem hervat op de AV1-pagina.", "/av1",
                             actions=[_dismiss_act([(key, reason,
                                                     f"AV1 {lane}-baan op pauze")])]))
            continue
        out.append(_item(key, "warning",
                         f"AV1 {lane}-baan staat stil", reason.removeprefix("automatisch: "),
                         "/av1"))
    for lane_id in ("cpu", "gpu"):
        if f"encoder.paused.{lane_id}" not in paused_lanes:
            _hidden(ctx, f"encoder.paused.{lane_id}", None)  # hervat: keuze vervalt
    for idle in health.get("baan_stilstand") or []:
        item = _lane_idle_item(ctx, idle)
        if item is not None:
            out.append(item)
    stalls = health.get("vastlopers") or {}
    for lane in stalls.get("teruggeschaald") or []:
        n = sum(int(p.get("aantal") or 0) for p in stalls.get("per_plek") or []
                if p.get("baan") == lane)
        label = "GPU" if lane == "gpu" else "CPU"
        out.append(_item(f"encoder.stalls.{lane}", "info",
                         f"AV1 {label}-baan tijdelijk op 1 plek",
                         f"{n}× vastgelopen in 12 uur; MediaGuard draait tijdelijk één encode "
                         "tegelijk op deze baan en schaalt vanzelf weer op.", "/av1"))
    return out


def _lane_idle_item(ctx: AppContext, idle: dict[str, Any]) -> dict[str, Any] | None:
    """Rapport 8 / A5: een AV1-baan die > 30 min stilstaat met wachtend werk (zelfde reden).
    MediaGuard blijft het zelf proberen; de bewaking op de achtergrond pusht dit één keer per
    stilstand (``push``). Kijken, een eigen pauze, stille uren en de noodstop zijn bewuste
    keuzes en tellen niet (verbeterlijst 8)."""
    lane = "GPU" if idle.get("baan") == "gpu" else "CPU"
    reason = str(idle.get("reden") or "onbekende reden")
    if pause_kind(reason) in BENIGN_PAUSES:
        return None
    minutes = int(idle.get("minuten") or 0)
    try:
        limit = int(ctx.config.value("monitor.lane_idle_minutes"))
    except Exception:  # noqa: BLE001 - instelling (nog) niet geregistreerd
        limit = 30
    if minutes < limit:
        return None
    waiting = int(idle.get("wachtend") or 0)
    duration = f"{minutes // 60} u {minutes % 60} min" if minutes >= 60 else f"{minutes} min"
    title = f"AV1 {lane}-baan staat al {duration} stil"
    detail = (f"{waiting} encode{'s' if waiting != 1 else ''} wacht{'en' if waiting != 1 else ''}"
              f"; reden: {reason}. MediaGuard probeert het vanzelf opnieuw.")
    key = f"encoder.idle.{idle.get('baan')}"
    item = _item(key, "warning", title, detail, "/av1")
    item["push"] = True
    item["push_ids"] = [str(idle.get("sinds") or "")]  # één push per stilstand
    return item


def _storage_forecast(ctx: AppContext) -> list[dict[str, Any]]:
    from mediaguard import storage_forecast

    out = []
    for d in storage_forecast.attention_items(ctx):
        out.append(_item("storage.full_soon", d["severity"], d["title_nl"], d["detail_nl"],
                         "/systeem#opslag"))
    return out


def _sab_categories(ctx: AppContext) -> list[dict[str, Any]]:
    """SABnzbd mist een categorie en MediaGuard kan hem niet zelf aanmaken (geen sleutel)."""
    from mediaguard.downloads import sab_categories

    return [_item("downloads.sab_category_missing", "error", d["title_nl"], d["detail_nl"],
                  "/inrichten?tab=downloads", count=d["count"])
            for d in sab_categories.attention_items(ctx)]


def _update(ctx: AppContext) -> list[dict[str, Any]]:
    """Nieuwe MediaGuard-versie op GitHub (dagelijkse controle, mediaguard/updates.py)."""
    from mediaguard import updates

    return [_item("system.update_available", "info", d["title_nl"], d["detail_nl"],
                  "/systeem#updates",
                  actions=[_act("Verbergen tot de volgende versie", "/system/update/dismiss",
                                body={"version": d["latest"]})])
            for d in updates.attention_items(ctx)]


def _maintenance(ctx: AppContext) -> list[dict[str, Any]]:
    """Mislukte back-up/bundel/nachtonderhoud, herstartlussen, klaargezette terugzetting en
    'Nieuw in deze versie' (mediaguard.maintenance.status)."""
    from mediaguard.maintenance import status

    return status.attention_items(ctx)


def _monitor(ctx: AppContext) -> list[dict[str, Any]]:
    """Stilstand (aanvraag, noodstop, Seerr-sync, achtergrondtaken) en de TrueNAS-pool."""
    from mediaguard import monitor

    return monitor.attention_items(ctx)


def _security(ctx: AppContext) -> list[dict[str, Any]]:
    from mediaguard.security.api import attention_items

    return attention_items(ctx)


def _replace_failures(ctx: AppContext) -> list[dict[str, Any]]:
    """Verkeerd bestand dat na 3 pogingen niet vervangen kon worden (blijft staan)."""
    from mediaguard.delivery import replace

    return [_item("identity.replace_failed", "warning", d["title_nl"], d["detail_nl"],
                  "/bibliotheek", count=d["count"], rows=d["rows"],
                  actions=[_act("Opnieuw proberen", "/attention/replace-failures/reset",
                                primary=True)])
            for d in replace.attention_items(ctx)]


def _seerr_sync(ctx: AppContext) -> list[dict[str, Any]]:
    """Automatisch Seerr-herstel afgebroken: Seerr's eigen test met de nieuwe waarden faalde."""
    from mediaguard.provisioning import seerr_sync

    return [_item("seerr.repair_test_failed", "warning", d["title_nl"], d["detail_nl"],
                  "/inrichten?tab=seerr", count=d["count"],
                  actions=[_act("Opnieuw testen", "/provisioning/seerr/apply",
                                body={"confirm": True}, role="admin", primary=True,
                                confirm="Seerr opnieuw testen en bijwerken als de test "
                                        "slaagt?")])
            for d in seerr_sync.attention_items(ctx)]


SOURCES = (_sab_categories, _encoding, _recovery, _storage, _storage_forecast, _typos,
           _safety, _backup, _instances, _routing, _ai, _identity, _dv, _update, _maintenance,
           _monitor, _security, _seerr_sync, _replace_failures)


def collect_local(ctx: AppContext) -> list[dict[str, Any]]:
    """Alle bronnen behalve de encoder (geen netwerk): goedkoop genoeg voor /status."""
    items: list[dict[str, Any]] = []
    for source in SOURCES:
        try:
            items.extend(source(ctx))
        except Exception:  # noqa: BLE001 - één bron mag de lijst nooit leegmaken
            LOGGER.exception("attention-bron %s mislukt", source.__name__)
    return items


# Rijen met namen die alleen een beheerder mag zien: welke geheimen onleesbaar zijn en welke
# beheerders geen tweestapsverificatie hebben (zoals /settings/secrets/health: namen alleen
# voor beheerders). Andere rollen zien het punt wel, met het aantal, maar zonder namen.
ADMIN_ROWS = frozenset({"security.secret_key_mismatch", "security.twofa_off"})


def for_role(items: list[dict[str, Any]], role: str) -> list[dict[str, Any]]:
    if role == "admin":
        return items
    return [{**i, "rows": [], "detail_nl": f"{i.get('detail_nl') or ''} Namen ziet alleen een "
             "beheerder.".strip()} if i.get("key") in ADMIN_ROWS else i for i in items]


def summary(ctx: AppContext) -> dict[str, int]:
    items = collect_local(ctx)
    return {"count": len(items), "errors": sum(1 for i in items if i["severity"] == "error"),
            "warnings": sum(1 for i in items if i["severity"] == "warning")}


async def collect(ctx: AppContext) -> list[dict[str, Any]]:
    items = collect_local(ctx)
    if os.environ.get("MG_ATTENTION_SKIP_ENCODER") != "1":
        try:
            items.extend(await _encoder(ctx))
        except Exception:  # noqa: BLE001
            LOGGER.exception("attention-bron encoder mislukt")
    items.sort(key=lambda i: (SEVERITY_ORDER.get(i["severity"], 3), i["key"]))
    return items


@router.get("")
async def attention(principal: Principal = Depends(require_viewer),
                    ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    items = for_role(await collect(ctx), principal.role)
    return {"items": items, "count": sum(1 for _ in items),
            "errors": sum(1 for i in items if i["severity"] == "error"),
            # aantal punten dat met 'Bewust zo gekozen' verborgen is (terughalen kan)
            "dismissed": len(dismissed_map(ctx))}


class DismissEntry(BaseModel):
    key: str = Field(min_length=1, max_length=200)
    fingerprint: str = Field(max_length=500)
    title: str = Field(default="", max_length=200)


class DismissBody(BaseModel):
    entries: list[DismissEntry] = Field(min_length=1, max_length=20)


def _invalidate(ctx: AppContext) -> None:
    for name in ("attention_count_cache", "health_summary_cache"):
        ctx.services.pop(name, None)


@router.post("/dismiss")
def dismiss_items(body: DismissBody, principal: Principal = Depends(require_operator),
                  ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    """'Bewust zo gekozen': verberg deze punten tot de bijbehorende instelling verandert."""
    bad = [e.key for e in body.entries if not dismissable(e.key)]
    if bad:
        raise HTTPException(422, "Dit punt kun je niet verbergen: het is geen bewuste keuze")
    if any(_dismiss_role(e.key) == "admin" for e in body.entries) and \
            ROLE_RANK.get(principal.role, 0) < ROLE_RANK["admin"]:
        raise HTTPException(403, "Alleen een beheerder kan een veiligheidswaarschuwing "
                                 "verbergen")
    for e in body.entries:
        dismiss(ctx, e.key, e.fingerprint, actor=principal.actor, title=e.title)
    audit(ctx.db, actor=principal.actor, action="attention.dismissed", target_type="attention",
          target_id=",".join(e.key for e in body.entries)[:200],
          new_value={"entries": [e.model_dump() for e in body.entries]},
          undo_hint="Activiteit → Problemen → Weer tonen")
    _invalidate(ctx)
    return {"dismissed": len(body.entries)}


@router.get("/dismissed")
def dismissed_list(_: Principal = Depends(require_viewer),
                   ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    rows = [{"key": k, "title": v.get("title") or k, "at": v.get("at"), "by": v.get("by")}
            for k, v in sorted(dismissed_map(ctx).items(),
                               key=lambda kv: str(kv[1].get("at") or ""), reverse=True)]
    return {"items": rows, "count": len(rows)}


class RestoreBody(BaseModel):
    keys: list[str] = Field(default_factory=list, max_length=50)


@router.post("/dismissed/restore")
def dismissed_restore(body: RestoreBody, principal: Principal = Depends(require_operator),
                      ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    """Verborgen punten weer tonen (leeg = alle)."""
    keys = body.keys or list(dismissed_map(ctx))
    n = sum(1 for k in keys if forget(ctx, k))
    if n:
        audit(ctx.db, actor=principal.actor, action="attention.restored",
              target_type="attention", target_id=",".join(keys)[:200],
              new_value={"keys": keys})
    _invalidate(ctx)
    return {"restored": n}


@router.post("/replace-failures/reset")
def replace_failures_reset(principal: Principal = Depends(require_operator),
                           ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    """'Opnieuw proberen': MediaGuard probeert verkeerde bestanden weer te vervangen."""
    from mediaguard.delivery import replace

    n = replace.clear_failure(ctx)
    audit(ctx.db, actor=principal.actor, action="identity.replace_retry",
          new_value={"files": n})
    _invalidate(ctx)
    return {"reset": n}
