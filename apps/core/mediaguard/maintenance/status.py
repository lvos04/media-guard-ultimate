"""Onderhoudsstatus voor 'Wacht op jou', de systeempagina en meldingen.

- mislukte back-up, herstelbundel of nachtonderhoud (met directe melding, zie api.py);
- diensten die steeds opnieuw starten (tellers uit ``/config/core/supervisor.json`` van de
  AIO-supervisor), met de uitleg die de supervisor vond (volle /config, migratie, …);
- een klaargezette terugzetting van een back-up van vóór een update;
- 'Nieuw in deze versie' (zie ``mediaguard.notices``).
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from mediaguard.context import AppContext

SUPERVISOR_FILE = "supervisor.json"
RESTART_WARN = 3            # herstarts per 24 uur per dienst
STALE_AFTER_S = 120         # de supervisor schrijft elke 5 s
RENOTIFY_S = 24 * 3600
LABELS_NL = {"core": "MediaGuard-core", "recovery": "Herstelproces (recovery)",
             "encode": "AV1-engine", "web": "Web-app", "sonarr-series": "Sonarr (series)",
             "sonarr-anime": "Sonarr (anime)", "radarr-films": "Radarr (films)",
             "radarr-animefilms": "Radarr (animefilms)"}


def supervisor_state(ctx: AppContext) -> dict[str, Any]:
    """Tellers van de AIO-supervisor; ``present`` False buiten de alles-in-één-container."""
    path = ctx.settings.data_dir / SUPERVISOR_FILE
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"present": False, "services": {}, "warnings": []}
    services = raw.get("services") if isinstance(raw.get("services"), dict) else {}
    age = None
    try:
        updated = datetime.strptime(str(raw.get("updated_at")), "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=UTC)
        age = (datetime.now(UTC) - updated).total_seconds()
    except ValueError:
        pass
    stale = age is None or age > STALE_AFTER_S
    warnings = []
    for name, svc in services.items():
        # een oud bestand (supervisor gestopt, of nu een losse installatie) waarschuwt niet
        if not isinstance(svc, dict) or stale:
            continue
        n = int(svc.get("restarts_24h") or 0)
        if n >= RESTART_WARN or svc.get("loop_nl"):
            warnings.append({"service": name, "label_nl": LABELS_NL.get(name, name),
                             "restarts_24h": n, "state": svc.get("state"),
                             "explanation_nl": svc.get("loop_nl") or
                             "De oorzaak is nog onbekend; kijk in de supportbundel (logs)."})
    migrating = {name: svc.get("migrating") for name, svc in services.items()
                 if isinstance(svc, dict) and svc.get("migrating") and not stale}
    return {"present": True, "updated_at": raw.get("updated_at"),
            "age_s": None if age is None else round(age), "stale": stale,
            "healthy": raw.get("healthy"), "services": services, "warnings": warnings,
            "migrating": migrating}


def under_supervisor(ctx: AppContext) -> bool:
    """Draait dit proces in de alles-in-één-container (met supervisor)? Alleen daar kan een
    back-up van vóór een update bij het stoppen worden teruggezet."""
    import os

    if os.environ.get("MG_AIO_SUPERVISOR") == "1":
        return True
    st = supervisor_state(ctx)
    return bool(st["present"] and not st["stale"])


def restart_component(ctx: AppContext) -> dict[str, Any] | None:
    """Regel voor 'Componenten' op de systeempagina (alleen in de AIO)."""
    st = supervisor_state(ctx)
    if not st["present"] or st["stale"]:
        return None
    total = {name: int(svc.get("restarts_24h") or 0) for name, svc in st["services"].items()
             if isinstance(svc, dict)}
    if st["warnings"]:
        detail = "; ".join(f"{w['label_nl']}: {w['restarts_24h']}× herstart in 24 uur. "
                           f"{w['explanation_nl']}" for w in st["warnings"])
    elif any(total.values()):
        detail = "Herstarts in 24 uur: " + ", ".join(
            f"{LABELS_NL.get(n, n)} {c}×" for n, c in total.items() if c)
    else:
        detail = "Geen herstarts in de laatste 24 uur"
    return {"ok": not st["warnings"], "detail": detail, "restarts_24h": total,
            "services": st["services"]}


# -- 'Wacht op jou' ---------------------------------------------------------------------------
def _item(key: str, severity: str, title: str, detail: str, href: str, *,
          actions: list[dict[str, Any]] | None = None,
          rows: list[dict[str, Any]] | None = None, count: int = 1) -> dict[str, Any]:
    return {"key": key, "severity": severity, "title_nl": title, "detail_nl": detail,
            "href": href, "count": count, "actions": actions or [], "rows": rows or []}


def _act(label: str, path: str, *, role: str = "operator", primary: bool = False,
         confirm: str | None = None, body: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"label_nl": label, "method": "post", "path": path, "body": body or {},
            "confirm_nl": confirm, "role": role, "primary": primary}


def attention_items(ctx: AppContext) -> list[dict[str, Any]]:
    from mediaguard.maintenance.api import STEPS

    out: list[dict[str, Any]] = []
    actions = {"backup": [_act("Nu een back-up maken", "/backups", primary=True)],
               "bundle": [_act("Herstelbundel nu maken", "/backups/bundle", role="admin",
                               primary=True)],
               "nightly": []}
    for step, cfg in STEPS.items():
        fail = ctx.state_get(cfg["fail_key"])
        if not isinstance(fail, dict) or not fail.get("at"):
            continue
        n = int(fail.get("count") or 1)
        out.append(_item(
            cfg["dedupe"], cfg["severity"], cfg["title"],
            cfg["body"].format(error=fail.get("error") or "onbekende fout")
            + (f" ({n} pogingen mislukt.)" if n > 1 else ""), "/systeem#back-ups",
            actions=actions[step]))
    st = supervisor_state(ctx)
    for w in st["warnings"]:
        out.append(_item(f"supervisor.restarts.{w['service']}", "warning",
                         f"{w['label_nl']} herstart steeds opnieuw",
                         f"{w['restarts_24h']}× herstart in 24 uur. {w['explanation_nl']}",
                         "/systeem"))
    from mediaguard.maintenance import preupdate

    pending = preupdate.pending(ctx.settings.data_dir)
    if pending:
        out.append(_item(
            "backup.rollback_pending", "warning", "Terugzetten staat klaar",
            f"De back-up van vóór de update ({pending.get('set')}) wordt teruggezet zodra de app "
            "stopt. Stop de app in TrueNAS, kies zo nodig de vorige versie en start hem "
            "weer. Wat na die back-up veranderde, is daarna niet meer te zien (de huidige "
            "databases blijven wel bewaard). Na een dag vervalt het klaarzetten vanzelf.",
            "/systeem#back-ups",
            actions=[_act("Annuleren", "/backups/terugzetten/annuleren", role="admin")]))
    from mediaguard import notices

    out.extend(notices.attention_items(ctx))
    return out


# -- meldingen (event loop, elke 10 minuten vanuit de onderhoudslus) ----------------------------
def check(ctx: AppContext) -> None:
    """Herstartlussen melden (eens per dag per dienst) en weer oplossen als het rustig is;
    de uitkomst van een teruggezette back-up één keer melden."""
    from mediaguard.api.system import notify, resolve_notifications

    st = supervisor_state(ctx)
    sent = dict(ctx.state_get("supervisor.notified", {}) or {})
    now = time.time()
    warned = set()
    for w in st["warnings"]:
        key = f"supervisor.restarts.{w['service']}"
        warned.add(key)
        if now - float(sent.get(key) or 0) < RENOTIFY_S:
            continue
        notify(ctx, "warning", f"{w['label_nl']} herstart steeds opnieuw",
               f"{w['restarts_24h']}× herstart in 24 uur. {w['explanation_nl']}",
               link="/systeem", dedupe_key=key)
        sent[key] = now
    for key in [k for k in sent if k not in warned]:
        resolve_notifications(ctx, key)
        sent.pop(key, None)
    ctx.state_set("supervisor.notified", sent)
    from mediaguard.maintenance import preupdate

    result = preupdate.take_result(ctx.settings.data_dir)
    if result:
        if result.get("ok"):
            notify(ctx, "info", "Back-up van vóór de update teruggezet",
                   f"MediaGuard draait nu met de databases uit {result.get('set')}. De "
                   f"databases van daarvoor staan nog in {result.get('previous')}.",
                   link="/systeem#back-ups", dedupe_key="backup.rollback_done")
        else:
            notify(ctx, "error", "Terugzetten niet gelukt",
                   f"De back-up {result.get('set')} is niet teruggezet: {result.get('error')}. "
                   "Er is niets veranderd.", link="/systeem#back-ups",
                   dedupe_key="backup.rollback_done")
