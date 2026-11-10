"""Instellingen die alleen een beheerder mag wijzigen, en instellingen met een eigen knop.

De app-sleutel (rol operator) en gebruikers met de rol operator kunnen via de gewone
instellingen veel aanpassen, maar niet:

- **wat je media of herstelmogelijkheden raakt**: alles onder ``quarantine.*``, dus wanneer en
  hoeveel originelen worden opgeruimd. Niet alleen 'direct verwijderen' en de bewaartermijn:
  ook een quotum van 1 GB met een minimale bewaartijd van 0 uur ruimt originelen direct na de
  controle op;
- **beveiliging**: 2FA verplichten, inlogmeldingen, inlogremmen en vertrouwde proxy's;
- **adressen waar MediaGuard opgeslagen sleutels of back-ups naartoe stuurt**: Seerr, Emby,
  Bazarr, TrueNAS en de off-site back-up. Wie zo'n adres verandert, laat MediaGuard zijn
  sleutels naar een andere server sturen;
- **adressen waar de UI elke gebruiker naartoe stuurt**: het Hub-adres van de link 'Terug naar
  de Hub' (``system.hub_url``). Wie dat verandert, kan iedereen naar een nagemaakte inlogpagina
  laten klikken.

``library.write_enabled`` (bibliotheek vrijgeven) gaat nooit via de gewone instellingen, ook
niet voor een beheerder: alleen via de knop met bevestiging (``POST
/api/v1/recovery/library-write``), zodat productiemedia alleen na expliciete toestemming
veranderen.
"""

from __future__ import annotations

from fastapi import HTTPException

ADMIN_ONLY_KEYS: frozenset[str] = frozenset({
    # adressen waar opgeslagen sleutels naartoe gaan
    "seerr.url",
    "mediaserver.url",
    "subtitles.bazarr_url",
    "recovery.truenas_url",
    "recovery.truenas_verify_tls",
    # waar MediaGuard met die TrueNAS-sleutel snapshots maakt
    "recovery.zfs_dataset",
    "app.public_url",
    "notify.base_url",
    # waar de link 'Terug naar de Hub' iedereen naartoe stuurt (Peil O-11): een operator of de
    # app-sleutel mag gebruikers niet naar een andere server laten klikken (ADR-0020)
    "system.hub_url",
    # hoe lang sporen bewaard blijven: een operator mag zijn eigen acties niet laten wissen
    "maintenance.retention_days_audit",
    "maintenance.retention_days_history",
})
# quarantine.*: modus, bewaartermijn, vangnet, quotum, minimale bewaartijd, opruimen en de
# vrijgavecontrole bepalen samen wanneer een origineel definitief verdwijnt
ADMIN_ONLY_PREFIXES: tuple[str, ...] = ("quarantine.", "security.", "backup.offsite.")

BUTTON_ONLY: dict[str, str] = {
    "library.write_enabled": "Dit gaat alleen via de knop ‘Bibliotheek vrijgeven’, met "
                             "bevestiging. Vergrendelen kan daar ook.",
}

ADMIN_ONLY_NOTE_NL = "Alleen een beheerder kan dit wijzigen."


def admin_only(key: str) -> bool:
    return key in ADMIN_ONLY_KEYS or key.startswith(ADMIN_ONLY_PREFIXES)


def button_only(key: str) -> str | None:
    return BUTTON_ONLY.get(key)


def check_change(role: str, key: str, label_nl: str) -> None:
    """Weigert een wijziging via de gewone instellingen (ook 'Herstel standaard')."""
    managed = button_only(key)
    if managed is not None:
        raise HTTPException(409, f"'{label_nl}' ({key}): {managed}")
    if admin_only(key) and role != "admin":
        raise HTTPException(403, f"'{label_nl}' ({key}): {ADMIN_ONLY_NOTE_NL}")


def annotate(public: dict[str, object]) -> dict[str, object]:
    key = str(public["key"])
    managed = button_only(key)
    return {**public, "admin_only": admin_only(key) or managed is not None,
            "managed_nl": managed or ""}
