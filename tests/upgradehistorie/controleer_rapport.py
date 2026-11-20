"""Controleer beide richtingen van één upgradebewijs of vergelijk het met het CI-artefact."""

import sys
from pathlib import Path


def vulling(pad: Path) -> dict:
    tekst = pad.read_text()
    for uitkomst in ("Upgrade: niets verloren", "terugzetten: niets verloren"):
        assert tekst.splitlines().count(uitkomst) == 1
    delen = tekst.split("## Terugzetten (vorige image + snapshot)")
    assert len(delen) == 2
    standen = []
    for deel in delen:
        gevuld = {}
        for regel in deel.splitlines():
            velden = [s.strip() for s in regel.strip("|").split("|")]
            if len(velden) == 7 and velden[2].isdigit():
                voor, na, verloren, gewijzigd, nieuw = map(int, velden[2:])
                assert 0 < voor <= na and verloren == gewijzigd == 0 and nieuw >= 0
                naam = tuple(velden[:2])
                assert naam not in gevuld
                gevuld[naam] = (voor, na)
        assert len(gevuld) == 162
        for instantie in ("radarr-films", "radarr-animefilms"):
            assert gevuld[f"{instantie}/radarr.db", "NamingConfig"] == (1, 1)
        assert (
            "Open overdrachten (mediaserver_pending zonder done_at): vóór 1, na 1."
            in deel
        )
        for tabel, aantal in (
            ("users", 4),
            ("jobs", 4),
            ("outbox_events", 2),
            ("mediaserver_pending", 3),
        ):
            assert gevuld["core/mediaguard.db", tabel] == (aantal, aantal)
        assert gevuld["encode/app.db", "job"] == (2, 2)
        standen.append({naam: aantal[0] for naam, aantal in gevuld.items()})
    assert standen[0] == standen[1]
    return standen[0]


if __name__ == "__main__":
    lokaal, *andere = map(vulling, map(Path, sys.argv[1:]))
    assert all(stand == lokaal for stand in andere)
    print(
        f"{len(lokaal)} gevulde tabellen per richting, 0 verloren of gewijzigd buiten de "
        "bestaande uitzonderingen, 1/1 open overdracht."
    )
