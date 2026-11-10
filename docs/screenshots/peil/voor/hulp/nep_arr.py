"""Peil-nulmeting MediaGuard: de nep-Sonarr/Radarr-API's uit apps/core/tests/test_arr_fakes.py als echte HTTP-servers.

Alleen-lezen hulpscript buiten de repo. Fictieve titels; geen echte media, geen echte NAS-gegevens.
Gebruik: python -I nep_arr.py <repo>  (luistert op 127.0.0.1:18911-18914; gestart door start.sh)
"""
from __future__ import annotations

import sys
import threading
import time

REPO = sys.argv[1]
sys.path.insert(0, f"{REPO}/apps/core/tests")

import uvicorn  # noqa: E402
from test_arr_fakes import (FakeArr, download_client, indexer, make_episode, make_episode_file,  # noqa: E402
                            make_movie, make_series)

KEY = "nep-sleutel-peil-0000000000000000"


def series_fake() -> FakeArr:
    f = FakeArr("sonarr", api_key=KEY)
    f.add_indexer(indexer(1, "Nep-indexer"))
    f.providers["downloadclient"][1] = download_client(1)
    eid = 100
    fid = 5000

    def add(sid: int, title: str, tvdb: int, layout: dict[int, list[str]], **kw):  # 'f'=bestand, 'm'=ontbreekt, 'u'=nog niet uitgezonden, 'c'=onder cutoff
        nonlocal eid, fid
        f_series = make_series(sid, title, tvdb, seasons={s: True for s in layout}, **kw)
        eps, files = [], []
        for season, codes in layout.items():
            for n, code in enumerate(codes, start=1):
                eid += 1
                days = -400 + season * 60 + n * 7 if code != "u" else n
                has = code in ("f", "c")
                file_id = 0
                if has:
                    fid += 1
                    file_id = fid
                    files.append(make_episode_file(fid, sid, season, cutoff_not_met=code == "c"))
                eps.append(make_episode(eid, sid, season, n, aired_days=days, has_file=has, file_id=file_id))
        f.add_series(f_series, eps, files)

    add(1, "Bergpas", 81001, {1: ["f"] * 8, 2: ["f"] * 8, 3: ["f", "f", "f", "m", "m", "u"]})
    add(2, "De Kustlijn", 81002, {1: ["f", "f", "c", "f", "f", "f"]})
    add(3, "Nachttrein", 81003, {1: ["f"] * 6, 2: ["f", "u", "u"]})
    add(4, "Stadshaven", 81004, {1: ["f", "f", "m", "f"]}, status="ended")
    return f


def anime_fake() -> FakeArr:
    f = FakeArr("sonarr", api_key=KEY)
    f.add_indexer(indexer(1, "Nep-indexer"))
    f.providers["downloadclient"][1] = download_client(1)
    s = make_series(11, "Sterrenveld", 82001, series_type="anime", seasons={1: True}, root="/anime/")
    eps, files = [], []
    for n in range(1, 13):
        has = n not in (7, 8)
        file_id = 6000 + n if has else 0
        if has:
            files.append(make_episode_file(file_id, 11, 1))
        eps.append(make_episode(700 + n, 11, 1, n, aired_days=-200 + n * 7, has_file=has, file_id=file_id, absolute=n))
    f.add_series(s, eps, files)
    return f


def films_fake(anime: bool = False) -> FakeArr:
    f = FakeArr("radarr", api_key=KEY)
    f.add_indexer(indexer(1, "Nep-indexer"))
    f.providers["downloadclient"][1] = download_client(1)
    root = "/animefilms/" if anime else "/movies/"
    items = ([make_movie(31, "Wolkenkasteel", 93001, has_file=True, root=root)] if anime else [
        make_movie(21, "Zomerlicht", 92001, has_file=True, root=root),
        make_movie(22, "De Vuurtoren", 92002, has_file=False, root=root),
        make_movie(23, "Noorderzon", 92003, available=False, digital_days=5, root=root),
        make_movie(24, "Polderwind", 92004, has_file=True, file_cutoff=True, root=root),
    ])
    for m in items:
        f.movies[m["id"]] = m
    return f


def serve(app, port: int) -> None:
    cfg = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", access_log=False)
    uvicorn.Server(cfg).run()


FAKES = {18911: series_fake(), 18912: anime_fake(), 18913: films_fake(), 18914: films_fake(anime=True)}
for port, fake in FAKES.items():
    threading.Thread(target=serve, args=(fake.asgi, port), daemon=True).start()
print("nep-arr klaar op 18911-18914", flush=True)
while True:
    time.sleep(3600)
