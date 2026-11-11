from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
from conftest import TESTDATA

from app.config import Settings, settings
from app.db import Database
from app.geheimen import SecretStore
from app.integraties import IntegrationMonitor, refresh_radarr, refresh_sonarr
from app.kwaliteit import extract_frame, run_profile_trial, vmaf_available
from app.media.probe import probe


def _case(tmp_path: Path) -> tuple[Database, Settings]:
    roots = {name: tmp_path / name for name in ("cache", "work", "config", "backups")}
    for path in roots.values():
        path.mkdir()
    library = tmp_path / "library"
    library.mkdir()
    key = tmp_path / "secrets.key"
    key.write_bytes(b"testmateriaal-dat-nooit-in-productie-wordt-gebruikt" * 2)
    configured = replace(
        settings,
        library_root=library,
        pipeline_root=tmp_path,
        cache_root=roots["cache"],
        work_root=roots["work"],
        config_root=roots["config"],
        backup_root=roots["backups"],
        database_path=roots["config"] / "app.db",
        secret_key_file=key,
    )
    database = Database(configured)
    database.initialize()
    return database, configured


def test_geheim_wordt_versleuteld_en_nooit_platte_tekst(tmp_path: Path) -> None:
    database, configured = _case(tmp_path)
    store = SecretStore(database, configured)
    store.set("jellyfin_api_sleutel", "heel-geheim")

    row = database.fetch_one("SELECT ciphertext FROM geheim")
    assert row is not None and "heel-geheim" not in str(row["ciphertext"])
    assert "heel-geheim" not in configured.database_path.read_bytes().decode(
        "utf-8", errors="ignore"
    )
    assert store.get("jellyfin_api_sleutel") == "heel-geheim"


@pytest.mark.asyncio
async def test_jellyfin_pauzeert_alleen_cpu_en_heft_eigen_pauze_op(tmp_path: Path) -> None:
    database, configured = _case(tmp_path)
    store = SecretStore(database, configured)
    store.set("jellyfin_api_sleutel", "sleutel")
    database.execute(
        "UPDATE instelling SET jellyfin_url='http://jellyfin',pauzeer_cpu_bij_kijken=1 WHERE id=1"
    )
    database.execute("UPDATE functie SET ingeschakeld=1 WHERE sleutel='jellyfin_pauze'")
    playing = True

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/System/Info/Public":
            return httpx.Response(200, json={"ProductName": "Jellyfin Server", "Id": "x"})
        # Δ-MG-AUTH: Jellyfin 12.x krijgt alleen Authorization: MediaBrowser … Token="…"
        assert "x-emby-token" not in request.headers
        assert request.headers["Authorization"].startswith("MediaBrowser ")
        assert request.headers["Authorization"].endswith('Token="sleutel"')
        assert "api_key" not in str(request.url)
        sessions = [{"NowPlayingItem": {"Id": "1"}, "PlayState": {"IsPaused": False}}]
        return httpx.Response(200, json=sessions if playing else [])

    monitor = IntegrationMonitor(database, configured)
    assert await monitor.tick(httpx.MockTransport(handler)) is True
    cpu = database.fetch_one("SELECT gepauzeerd,pauze_reden FROM worker_config WHERE baan='cpu'")
    gpu = database.fetch_one("SELECT gepauzeerd FROM worker_config WHERE baan='gpu'")
    assert cpu is not None and cpu["gepauzeerd"] == 1 and "Jellyfin" in cpu["pauze_reden"]
    assert gpu is not None and gpu["gepauzeerd"] == 0

    playing = False
    assert await monitor.tick(httpx.MockTransport(handler)) is False
    cpu = database.fetch_one("SELECT gepauzeerd FROM worker_config WHERE baan='cpu'")
    assert cpu is not None and cpu["gepauzeerd"] == 0


@pytest.mark.asyncio
async def test_arr_refresh_is_altijd_op_het_gevonden_item_gericht(tmp_path: Path) -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path.endswith("/movie"):
            return httpx.Response(200, json=[{"id": 42, "path": str(tmp_path / "Film")}])
        if request.method == "GET" and request.url.path.endswith("/series"):
            return httpx.Response(200, json=[{"id": 7, "path": str(tmp_path / "Serie")}])
        body = json.loads(request.content)
        calls.append((request.url.path, body))
        return httpx.Response(201, json={})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert (
            await refresh_radarr(client, "http://radarr", "key", tmp_path / "Film" / "film.mkv")
            == 42
        )
        assert (
            await refresh_sonarr(
                client, "http://sonarr", "key", tmp_path / "Serie" / "S01" / "ep.mkv"
            )
            == 7
        )

    assert calls == [
        ("/api/v3/command", {"name": "RefreshMovie", "movieIds": [42]}),
        ("/api/v3/command", {"name": "RescanSeries", "seriesId": 7}),
    ]


@pytest.mark.asyncio
async def test_kwaliteitsframe_is_een_echte_ffmpeg_extractie(tmp_path: Path) -> None:
    target = tmp_path / "frame.jpg"
    assert await extract_frame(TESTDATA / "sdr-meertalig.mkv", target, 2.0)
    assert target.stat().st_size > 100
    # Deze host mag libvmaf missen; de detectie moet eerlijk een bool geven.
    assert isinstance(await vmaf_available(), bool)


@pytest.mark.asyncio
async def test_proefdraai_encodeert_drie_echte_tijdelijke_resultaten(tmp_path: Path) -> None:
    database, configured = _case(tmp_path)
    source = TESTDATA / "sdr-meertalig.mkv"
    source_probe = probe(str(source))
    assert source_probe is not None
    profile_id = database.execute_insert("INSERT INTO profiel(naam,inst) VALUES ('Test','{}')")
    library_id = database.execute_insert(
        "INSERT INTO library(naam,pad,profiel_id) VALUES ('Test',?,?)",
        (str(source.parent), profile_id),
    )
    file_id = database.execute_insert(
        """
        INSERT INTO bestand(
            library_id,pad,grootte,mtime,categorie,probe,gescand_op
        ) VALUES (?,?,?,?,?,?,CURRENT_TIMESTAMP)
        """,
        (
            library_id,
            str(source),
            source.stat().st_size,
            source.stat().st_mtime,
            "sdr",
            json.dumps(source_probe),
        ),
    )

    await run_profile_trial(database, configured, "proef-test", profile_id, file_id, [27, 29, 31])

    rows = database.fetch_all("SELECT crf,grootte,status FROM kwaliteitsmeting ORDER BY crf")
    assert [row["crf"] for row in rows] == [27, 29, 31]
    assert all(row["status"] == "klaar" and row["grootte"] > 0 for row in rows)
    assert rows[0]["grootte"] > rows[1]["grootte"] > rows[2]["grootte"]
    assert not (configured.work_root / "proefdraaien" / "proef-test").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("product", "kop", "weg"),
    [
        # Emby 4.10.2.0 geeft op /System/Info/Public géén ProductName (gemeten tegen de echte
        # container): zonder dat veld blijft het de kop van 1.2.2
        ("", "X-Emby-Token", "authorization"),
        ("Emby Server", "X-Emby-Token", "authorization"),
        ("Jellyfin Server", "Authorization", "x-emby-token"),
    ],
)
async def test_sessies_met_de_aanmeldkop_van_de_eigen_server(
    tmp_path: Path, product: str, kop: str, weg: str
) -> None:
    """Δ-MG-AUTH per server: Emby houdt X-Emby-Token (de Emby-koppeling onderbreekt nooit);
    Jellyfin 12.x (legacy-auth uit) alleen Authorization: MediaBrowser Token=…. Alleen een
    server die zich als Jellyfin meldt krijgt de nieuwe kop."""
    from app.integraties import jellyfin_is_playing, mediaserver_headers

    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/System/Info/Public":
            return httpx.Response(200, json={"ProductName": product} if product else {})
        return httpx.Response(200, json=[])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await jellyfin_is_playing(client, "http://server", "k-1") is False
    last = seen[-1]
    assert kop.lower() in last.headers and weg not in last.headers
    assert "k-1" in last.headers[kop] and "api_key" not in str(last.url)
    with pytest.raises(ValueError):
        mediaserver_headers("Jellyfin Server", 'a"b')


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("antwoord", "verwacht"),
    [
        (httpx.Response(200, json={"ProductName": "Jellyfin Server"}), "Jellyfin Server"),
        # Jellyfin 12.x antwoordt tijdens het opstarten in camelCase (gemeten)
        (httpx.Response(200, json={"productName": "Jellyfin Server"}), "Jellyfin Server"),
        (httpx.Response(200, json={"ServerName": "emby", "Version": "4.10.2.0"}), ""),
        (httpx.Response(200, json=["geen", "object"]), ""),
        (httpx.Response(503, text="<html>Server is starting</html>"), ""),
        (httpx.Response(200, text="geen json"), ""),
    ],
)
async def test_productnaam_van_de_openbare_info_route(
    antwoord: httpx.Response, verwacht: str
) -> None:
    """Onleesbaar of zonder ProductName is leeg: dan blijft het de Emby-kop van 1.2.2."""
    from app.integraties import mediaserver_product

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: antwoord)
    ) as client:
        assert await mediaserver_product(client, "http://server/") == verwacht
