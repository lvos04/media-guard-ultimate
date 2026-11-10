"""Nederlandse uitleg bij de bekende health-meldingen van Sonarr/Radarr.

Sonarr/Radarr leveren Engelse teksten; voor de gebruiker vertalen we de bekende controles
(op ``source``) naar een korte Nederlandse zin. Onbekende meldingen blijven zoals ze zijn.
"""

from __future__ import annotations

import re
from typing import Any

HEALTH_NL: dict[str, str] = {
    "IndexerRssCheck": "Geen indexer met RSS aan: nieuwe releases worden niet automatisch "
                       "opgepikt",
    "IndexerSearchCheck": "Geen indexer met zoeken aan: automatisch zoeken werkt niet",
    "IndexerStatusCheck": "Eén of meer indexers zijn tijdelijk uitgeschakeld na fouten",
    "IndexerLongTermStatusCheck": "Eén of meer indexers werken al meer dan 6 uur niet",
    "IndexerJackettAllCheck": "Een indexer gebruikt Jackett 'all' (wordt afgeraden)",
    "DownloadClientCheck": "Downloadclient niet bereikbaar",
    "DownloadClientStatusCheck": "Eén of meer downloadclients geven fouten",
    "DownloadClientRootFolderCheck": "De downloadclient zet bestanden in een mediamap",
    "DownloadClientRemovesCompletedDownloadsCheck": "De downloadclient ruimt voltooide "
                                                   "downloads zelf op",
    "DownloadClientSortingCheck": "Sortering in de downloadclient staat aan voor deze "
                                  "categorie",
    "ImportMechanismCheck": "Automatisch importeren van voltooide downloads staat uit",
    "ImportListStatusCheck": "Eén of meer importlijsten geven fouten",
    "RootFolderCheck": "Een mediamap ontbreekt of is niet bereikbaar",
    "MissingMovieFolderCheck": "Een filmmap ontbreekt",
    "RemovedSeriesCheck": "Series zijn verwijderd bij TheTVDB",
    "RemovedMovieCheck": "Films zijn verwijderd bij TMDb",
    "MountCheck": "Een map staat alleen-lezen gekoppeld",
    "ProxyCheck": "De ingestelde proxy werkt niet",
    "UpdateCheck": "Er is een update; MediaGuard beheert updates zelf",
    "NotificationStatusCheck": "Eén of meer meldingskoppelingen geven fouten",
    "RecyclingBinCheck": "De prullenbakmap is niet schrijfbaar",
    "SystemTimeCheck": "De systeemtijd wijkt af",
    "AppDataLocationCheck": "De datamap staat op een ongeschikte plek",
    "ApiKeyValidationCheck": "De API-sleutel is te kort of ongeldig",
    "PackageGlobalMessageCheck": "Melding van Sonarr/Radarr zelf",
}


def _collections(message: str) -> str | None:
    """'Missing root folder for movie collection: /x (A, B, …)' → korte zin."""
    low = message.lower()
    if "collection" not in low or "root folder" not in low:
        return None
    m = re.search(r":\s*(/[^()]+?)\s*\((.*)\)\s*$", message)
    if not m:
        return "Collecties wijzen naar een map die niet meer bestaat"
    n = len([x for x in m.group(2).split(",") if x.strip()])
    return (f"{n} collectie{'s' if n != 1 else ''} wijzen naar {m.group(1).strip()}, een map "
            "die niet meer bestaat (MediaGuard zet ze vanzelf naar de juiste map)")


def translate(item: dict[str, Any]) -> str:
    source = str(item.get("source") or "")
    special = _collections(str(item.get("message") or ""))
    if special:
        return special
    nl = HEALTH_NL.get(source)
    if nl is None:
        return str(item.get("message") or source or "Onbekende melding")
    return nl


def summarize(items: list[dict[str, Any]], limit: int = 3) -> str:
    texts: list[str] = []
    for item in items:
        t = translate(item)
        if t not in texts:
            texts.append(t)
    more = len(texts) - limit
    out = "; ".join(texts[:limit])
    return out + (f" (+{more} meer)" if more > 0 else "")
