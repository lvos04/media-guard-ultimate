from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import httpx
import pytest
from conftest import TESTDATA
from fastapi.testclient import TestClient
from mg_helpers import HEADERS

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


# ── Testknop van de encoder: POST /api/integraties/jellyfin/test ────────────────────────────
# Δ-MG-AUTH op de derde plek (B03_DELTAVERZOEKEN §3: core clients.py, encoder integraties.py
# en encoder api.py). De route leest eerst zonder sleutel de openbare info-route en vraagt
# daarna /System/Info met de aanmeldkop van de soort server die zich daar meldt.
MgClient = tuple[TestClient, Database, Settings]
SLEUTEL = "testsleutel-1"
# Emby 4.10.2.0 geeft op /System/Info/Public géén ProductName (gemeten tegen de echte container
# in tests/contract/mediaserver); Jellyfin 12.x meldt "Jellyfin Server".
EMBY_INFO = {"ServerName": "emby-test", "Version": "4.10.2.0", "Id": "emby-test-id"}
JELLYFIN_INFO = {"ProductName": "Jellyfin Server", "ServerName": "jf-test", "Id": "jf-test-id"}


def _nep_mediaserver(
    monkeypatch: pytest.MonkeyPatch, info: dict[str, str], status: int = 200
) -> list[httpx.Request]:
    """Zet een nep-server achter de httpx-client van de route; geeft elk verzoek terug."""
    seen: list[httpx.Request] = []
    echte_client = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/System/Info/Public":
            return httpx.Response(200, json=info)
        return httpx.Response(status, json={"Id": info["Id"]})

    def client(**kwargs: Any) -> httpx.AsyncClient:
        return echte_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr("app.api.httpx.AsyncClient", client)
    return seen


def _testknop(client: TestClient, sleutel: str) -> httpx.Response:
    return client.post(
        "/api/integraties/jellyfin/test",
        headers=HEADERS,
        json={"url": "http://server:8096/", "api_sleutel": sleutel},
    )


def _sleutel_nergens_in_de_url(seen: list[httpx.Request], sleutel: str) -> None:
    for request in seen:
        assert sleutel not in str(request.url)
        assert "api_key" not in str(request.url).lower()


@pytest.mark.parametrize(
    ("info", "kop", "weg"),
    [
        (EMBY_INFO, "x-emby-token", "authorization"),
        (JELLYFIN_INFO, "authorization", "x-emby-token"),
    ],
    ids=["emby-zonder-productname", "jellyfin"],
)
def test_testknop_meldt_zich_aan_met_de_kop_van_de_eigen_server(
    mg_client: MgClient,
    monkeypatch: pytest.MonkeyPatch,
    info: dict[str, str],
    kop: str,
    weg: str,
) -> None:
    """Emby zonder ProductName houdt X-Emby-Token (de kop van 1.2.2); Jellyfin 12.x krijgt alleen
    Authorization: MediaBrowser … Token="…". De openbare info-route krijgt geen sleutel, de
    sleutel staat in geen enkele URL en niet in het antwoord, en de route doet alleen twee
    leesverzoeken (RV1: geen schrijfroute naar Emby)."""
    client, _database, _settings = mg_client
    seen = _nep_mediaserver(monkeypatch, info)

    response = _testknop(client, SLEUTEL)

    assert response.status_code == 200, response.text
    assert response.json() == {"integratie": "jellyfin", "bereikbaar": True, "statuscode": 200}
    assert SLEUTEL not in response.text
    assert [(r.method, r.url.path) for r in seen] == [
        ("GET", "/System/Info/Public"),
        ("GET", "/System/Info"),
    ]
    openbaar, met_sleutel = seen
    assert "x-emby-token" not in openbaar.headers
    assert "authorization" not in openbaar.headers
    assert weg not in met_sleutel.headers
    if kop == "x-emby-token":
        assert met_sleutel.headers["x-emby-token"] == SLEUTEL
    else:
        assert met_sleutel.headers["authorization"].startswith("MediaBrowser ")
        assert met_sleutel.headers["authorization"].endswith(f'Token="{SLEUTEL}"')
    _sleutel_nergens_in_de_url(seen, SLEUTEL)


@pytest.mark.parametrize("sleutel", ['sleutel"1', "sleutel 1", "sleutel,1", "sleutel\\1"])
@pytest.mark.parametrize("info", [EMBY_INFO, JELLYFIN_INFO], ids=["emby", "jellyfin"])
def test_testknop_weigert_een_ongeldige_sleutel_met_422_en_verstuurt_hem_niet(
    mg_client: MgClient,
    monkeypatch: pytest.MonkeyPatch,
    info: dict[str, str],
    sleutel: str,
) -> None:
    """Een sleutel met tekens die de MediaBrowser-kop zouden breken geeft 422 met een vaste
    melding. Alleen de openbare info-route (zonder sleutel) is gevraagd; de sleutel gaat in geen
    enkel verzoek mee en staat niet in de foutmelding."""
    client, _database, _settings = mg_client
    seen = _nep_mediaserver(monkeypatch, info)

    response = _testknop(client, sleutel)

    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert "ongeldige API-sleutel" in detail and sleutel not in detail
    assert [(r.method, r.url.path) for r in seen] == [("GET", "/System/Info/Public")]
    assert all(sleutel not in value for r in seen for value in r.headers.values())
    _sleutel_nergens_in_de_url(seen, sleutel)


@pytest.mark.parametrize("info", [EMBY_INFO, JELLYFIN_INFO], ids=["emby", "jellyfin"])
def test_testknop_geweigerde_sleutel_geeft_502_zonder_de_sleutel_in_de_melding(
    mg_client: MgClient, monkeypatch: pytest.MonkeyPatch, info: dict[str, str]
) -> None:
    """Weigert de server de sleutel (401), dan meldt de route dat met de statuscode van de
    server; de sleutel staat niet in de foutmelding."""
    client, _database, _settings = mg_client
    seen = _nep_mediaserver(monkeypatch, info, status=401)

    response = _testknop(client, SLEUTEL)

    assert response.status_code == 502, response.text
    assert response.json()["detail"] == "jellyfin antwoordde met HTTP 401"
    assert SLEUTEL not in response.text
    assert [(r.method, r.url.path) for r in seen][-1] == ("GET", "/System/Info")
    _sleutel_nergens_in_de_url(seen, SLEUTEL)
