"""Nederlandse namen voor audit-acties en -actoren (rapport 4, S12/S17).

De audit bewaart technische sleutels (``download.marked_failed``); de UI toont deze labels.
Onbekende acties krijgen een leesbare terugvaltekst in plaats van de ruwe sleutel."""

from __future__ import annotations

from typing import Any

ACTIONS_NL: dict[str, str] = {
    "download.marked_failed": "Download als mislukt gemarkeerd (opnieuw zoeken)",
    "download.refused_removed": "Geweigerde download verwijderd en geblokkeerd",
    "recovery.replace_committed": "Bestand vervangen door de AV1-versie",
    "recovery.restore_committed": "Origineel teruggezet",
    "quarantine.purged": "Quarantaine opgeruimd",
    "delivery.delivered": "Aanvraag geleverd",
    "delivery.ai_decision": "AI-besluit bij een aanvraag",
    "delivery.stopped": "Aanvraag gestopt",
    "delivery.retry": "Aanvraag nu opnieuw geprobeerd",
    "identity.auto_replaced": "Verkeerd bestand in quarantaine gezet en opnieuw gezocht",
    "identity.replace_retry": "Verkeerd bestand: opnieuw proberen te vervangen",
    "wanted.search_now": "Ontbrekend: nu zoeken",
    "wanted.search": "Ontbrekend: zoeken",
    "wanted.backfill_run": "Backfill: zoekronde",
    "backfill.run": "Backfill: zoekronde",
    "encoding.handoff": "AV1: overgedragen aan de bibliotheek",
    "encoding.autopilot": "AV1: autopilot-ronde",
    "encoding.retry": "AV1: opnieuw ingediend na een eerdere fout",
    "apikey.created": "API-sleutel aangemaakt",
    "apikey.revoked": "API-sleutel ingetrokken",
    "apikey.rejected": "API-sleutel geweigerd",
    "storage.typo_fix": "Map hernoemd",
    "arr.removed_deleted_title": "Verwijderde titel opgeruimd",
    "attention.dismissed": "Wacht op jou: bewust zo gekozen (verborgen)",
    "attention.restored": "Wacht op jou: verborgen punten weer getoond",
}

PREFIX_NL: tuple[tuple[str, str], ...] = (
    ("provisioning.seerr.", "Inrichting Seerr: "),
    ("provisioning.", "Inrichting: "),
    ("download.", "Download: "),
    ("downloads.", "Downloads opruimen: "),
    ("recovery.", "Herstel: "),
    ("quarantine.", "Quarantaine: "),
    ("delivery.", "Aanvragen: "),
    ("encoding.", "AV1: "),
    ("wanted.", "Ontbrekend: "),
    ("ai.", "AI: "),
    ("settings.", "Instelling: "),
    ("migration.", "Migratie: "),
    ("auth.", "Inloggen: "),
    ("backfill.", "Backfill: "),
    ("identity.", "Identiteit: "),
    ("instance.", "Instantie: "),
    ("instances.", "Instanties: "),
    ("job.", "Taak: "),
    ("optimization.", "Optimalisatieplan: "),
    ("dv.", "Dolby Vision: "),
    ("backup.", "Back-up: "),
    ("diagnostics.", "Diagnose: "),
)

WORDS_NL = {"update": "bijgewerkt", "create": "aangemaakt", "delete": "verwijderd",
            "failed": "mislukt", "committed": "afgerond", "run": "uitgevoerd",
            "search": "zoeken", "now": "nu", "qualityprofiles": "kwaliteitsprofielen",
            "customformats": "custom formats", "qualitydefinitions": "kwaliteitsdefinities",
            "mediamanagement": "mediabeheer", "rootfolders": "mediamappen",
            "downloadclients": "downloadclients", "indexers": "indexers", "tags": "tags",
            "webhook": "webhook", "naming": "naamgeving", "replace": "vervangen",
            "restore": "terugzetten", "purged": "opgeruimd", "decision": "besluit",
            "enqueue": "ingepland", "trashed": "naar prullenbak", "restored": "teruggezet",
            "cancel": "gestopt", "retry": "opnieuw", "ignore": "genegeerd", "pause": "gepauzeerd",
            "resume": "hervat", "manual": "handmatige", "import": "import",
            "removed": "verwijderd", "submit": "ingediend", "verify": "controle"}

ACTORS_NL: dict[str, str] = {
    "apikey:mso": "Media Source Optimizer (API-sleutel)",
    "svc:download-guard": "MediaGuard (downloadbewaking)",
    "svc:downloads": "MediaGuard (opruimer)",
    "svc:recovery": "MediaGuard (herstel)",
    "svc:av1-autopilot": "MediaGuard (AV1-autopilot)",
    "svc:delivery": "MediaGuard (aanvragen)",
    "system:provisioning": "MediaGuard (inrichting)",
}


def action_nl(action: str | None) -> str:
    a = str(action or "")
    if a in ACTIONS_NL:
        return ACTIONS_NL[a]
    for prefix, label in PREFIX_NL:
        if a.startswith(prefix):
            rest = a[len(prefix):].replace("_", ".").split(".")
            return label + " ".join(WORDS_NL.get(w, w) for w in rest if w)
    return a.replace("_", " ").replace(".", ": ", 1) if a else ""


def actor_nl(actor: str | None) -> str:
    a = str(actor or "")
    if a in ACTORS_NL:
        return ACTORS_NL[a]
    if a.startswith("apikey:"):
        name = a.split(":", 1)[1]
        return ("Media Source Optimizer" if name.lower() in ("mso", "media source optimizer")
                else f"Koppeling '{name}'") + " (API-sleutel)"
    if a.startswith(("svc:", "system:")):
        return "MediaGuard"
    if a.startswith("ai:"):
        return "MediaGuard (AI)"
    return a


def label_row(row: dict[str, Any]) -> dict[str, Any]:
    row["action_nl"] = action_nl(row.get("action"))
    row["actor_nl"] = actor_nl(row.get("actor"))
    row["via_api_key"] = str(row.get("actor") or "").startswith("apikey:")
    return row
