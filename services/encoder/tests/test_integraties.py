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


@pytest.mark.parametrize(
    "sleutel",
    # niet-ASCII en stuurtekens (S-P2-600, R-TECH-4): 422, geen kale 500
    ['sleutel"1', "sleutel 1", "sleutel,1", "sleutel\\1", "sleutelé", "sleutel\x01"],
)
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


# ── Herstel na de technische review (S-P2-600) ──────────────────────────────────────────────
OPGESLAGEN = "opgeslagen-sleutel-42"


@pytest.mark.parametrize(
    ("kind", "info", "kop"),
    [
        ("jellyfin", EMBY_INFO, "x-emby-token"),
        ("jellyfin", JELLYFIN_INFO, "authorization"),
        ("radarr", EMBY_INFO, "x-api-key"),
    ],
    ids=["emby", "jellyfin", "radarr"],
)
def test_testknop_gebruikt_de_opgeslagen_sleutel_alleen_met_het_opgeslagen_adres(
    mg_client: MgClient,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    info: dict[str, str],
    kop: str,
) -> None:
    """R-VEILIG-1: de opgeslagen sleutel gaat alleen naar het opgeslagen adres. Een ander adres
    zonder eigen sleutel geeft 422 zonder één verzoek; met een eigen sleutel gaat alleen die
    sleutel mee."""
    client, _database, _settings = mg_client
    r = client.patch(
        f"/api/integraties/{kind}",
        headers=HEADERS,
        json={"url": "http://emby:8096", "api_sleutel": OPGESLAGEN},
    )
    assert r.status_code == 200, r.text
    seen = _nep_mediaserver(monkeypatch, info)

    for anders in ("http://aanvaller:8096", "http://emby:9999", "https://emby:8096"):
        r = client.post(f"/api/integraties/{kind}/test", headers=HEADERS, json={"url": anders})
        assert r.status_code == 422, r.text
        assert "opgeslagen" in r.json()["detail"] and OPGESLAGEN not in r.text
    assert seen == []

    for body in ({}, {"url": "http://emby:8096"}, {"url": "http://emby:8096/"}):
        r = client.post(f"/api/integraties/{kind}/test", headers=HEADERS, json=body)
        assert r.status_code == 200, (body, r.text)
        assert seen[-1].url.host == "emby" and OPGESLAGEN in seen[-1].headers[kop]
    seen.clear()

    r = client.post(
        f"/api/integraties/{kind}/test",
        headers=HEADERS,
        json={"url": "http://ander:8096", "api_sleutel": "eigen-sleutel-1"},
    )
    assert r.status_code == 200, r.text
    assert {req.url.host for req in seen} == {"ander"}
    assert all(OPGESLAGEN not in v for req in seen for v in req.headers.values())
    assert "eigen-sleutel-1" in seen[-1].headers[kop]


@pytest.mark.asyncio
async def test_monitor_vraagt_de_productnaam_eenmaal_per_adres(tmp_path: Path) -> None:
    """R-HARDE-EIS-1: de encoder-instelling "pauzeer bij kijken" vraagt Emby per poll alleen
    /Sessions, zoals 1.2.2. De openbare info-route (zonder sleutel) gaat één keer per adres, en
    opnieuw na een adreswijziging of een fout."""
    database, configured = _case(tmp_path)
    SecretStore(database, configured).set("jellyfin_api_sleutel", "emby-sleutel")
    database.execute(
        "UPDATE instelling SET jellyfin_url='http://emby:8096',pauzeer_cpu_bij_kijken=1 WHERE id=1"
    )
    database.execute("UPDATE functie SET ingeschakeld=1 WHERE sleutel='jellyfin_pauze'")
    seen: list[httpx.Request] = []
    fout = False

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/System/Info/Public":
            assert "x-emby-token" not in request.headers
            return httpx.Response(200, json=EMBY_INFO)
        assert request.headers["X-Emby-Token"] == "emby-sleutel"
        return httpx.Response(500) if fout else httpx.Response(200, json=[])

    def paden() -> list[tuple[str, str]]:
        return [(r.url.host, r.url.path) for r in seen]

    monitor = IntegrationMonitor(database, configured)
    for _ in range(3):
        assert await monitor.tick(httpx.MockTransport(handler)) is False
    assert paden() == [("emby", "/System/Info/Public")] + [("emby", "/Sessions")] * 3

    seen.clear()
    database.execute("UPDATE instelling SET jellyfin_url='http://emby2:8096' WHERE id=1")
    await monitor.tick(httpx.MockTransport(handler))
    await monitor.tick(httpx.MockTransport(handler))
    assert paden() == [("emby2", "/System/Info/Public")] + [("emby2", "/Sessions")] * 2

    seen.clear()
    fout = True
    assert await monitor.tick(httpx.MockTransport(handler)) is False
    fout = False
    await monitor.tick(httpx.MockTransport(handler))
    assert paden() == [
        ("emby2", "/Sessions"),
        ("emby2", "/System/Info/Public"),
        ("emby2", "/Sessions"),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "sleutel", ["emby sleutel", 'emby"sleutel', "emby,sleutel", "emby\\sleutel", "emby\tsleutel"]
)
async def test_emby_sleutel_van_1_2_2_blijft_werken_in_de_encoder(
    tmp_path: Path, sleutel: str
) -> None:
    """HARDE EIS (vervolgpunt R-VEILIG): 1.2.2 stuurde de opgeslagen sleutel zonder controle als
    X-Emby-Token. Leestekens en spaties kunnen in die kop geen kop toevoegen; zo'n sleutel blijft
    werken. Alleen de MediaBrowser-kop van Jellyfin eist de strengere regel."""
    from app.integraties import mediaserver_headers

    database, configured = _case(tmp_path)
    SecretStore(database, configured).set("jellyfin_api_sleutel", sleutel)
    database.execute(
        "UPDATE instelling SET jellyfin_url='http://emby:8096',pauzeer_cpu_bij_kijken=1 WHERE id=1"
    )
    database.execute("UPDATE functie SET ingeschakeld=1 WHERE sleutel='jellyfin_pauze'")
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/System/Info/Public":
            return httpx.Response(200, json=EMBY_INFO)
        sessions = [{"NowPlayingItem": {"Id": "1"}, "PlayState": {"IsPaused": False}}]
        return httpx.Response(200, json=sessions)

    assert await IntegrationMonitor(database, configured).tick(httpx.MockTransport(handler))
    assert seen[-1].headers["X-Emby-Token"] == sleutel and "authorization" not in seen[-1].headers
    assert mediaserver_headers("", sleutel) == {"X-Emby-Token": sleutel}
    with pytest.raises(ValueError):
        mediaserver_headers("Jellyfin Server", sleutel)


@pytest.mark.parametrize(
    "sleutel", ["emby\r\nX-Kwaad: 1", "emby\nx", "emby\x00", "emby\x01", "emby\x7f", "embyé"]
)
def test_emby_sleutel_met_stuurteken_gaat_nooit_de_deur_uit(sleutel: str) -> None:
    """CR, LF, NUL, andere stuurtekens en niet-ASCII: nooit in een kop (kopinjectie; 1.2.2 kon ze
    ook niet versturen). Uitzondering (ADR-0022 besluit 3): een tab binnenin de sleutel gaat wel
    mee, want een tab mag in een kopwaarde en voegt geen kop toe. De melding noemt de sleutel
    niet."""
    from app.integraties import mediaserver_headers

    for product in ("", "Emby Server", "Jellyfin Server"):
        with pytest.raises(ValueError, match="ongeldige API-sleutel") as exc:
            mediaserver_headers(product, sleutel)
        assert sleutel not in str(exc.value)


# ── Tekencontrole op sleutel en adres (KLEIN-51, S-P2-600 vervolgpunt, R-VEILIG-2) ─────────────
ONGELDIGE_SLEUTELS = ["sleutelé", "sleutel\r\nX-Kwaad: 1", "sleutel\n1", "sleutel\r1"]
ONGELDIGE_ADRESSEN = [
    "http://emby:8096\t",
    "http://emby:8096/\n",
    "http://emby:8096\r\n",
    "http://emby:8096 ",
    "http://em\x01by:8096",
    "http://emby:8096/a\x7fb",
]


def _opgeslagen(database: Database, configured: Settings, kind: str) -> tuple[str | None, str]:
    row = database.fetch_one(f"SELECT {kind}_url AS url FROM instelling WHERE id=1")
    assert row is not None
    return row["url"], SecretStore(database, configured).get(f"{kind}_api_sleutel")


@pytest.mark.parametrize("sleutel", ONGELDIGE_SLEUTELS, ids=["e-accent", "crlf", "lf", "cr"])
@pytest.mark.parametrize("kind", ["jellyfin", "radarr", "sonarr"])
def test_patch_weigert_een_nieuwe_sleutel_zonder_tekencontrole_met_422(
    mg_client: MgClient, caplog: pytest.LogCaptureFixture, kind: str, sleutel: str
) -> None:
    """PATCH slaat een nieuwe sleutel alleen op als hij de strengste regel (``valid_key``)
    doorstaat; anders 422 met de tekst van de testknop, zonder de sleutel in antwoord of log. Het
    adres en de al opgeslagen sleutel blijven onaangeroerd."""
    client, database, configured = mg_client
    r = client.patch(
        f"/api/integraties/{kind}",
        headers=HEADERS,
        json={"url": "http://emby:8096", "api_sleutel": OPGESLAGEN},
    )
    assert r.status_code == 200, r.text
    caplog.set_level("DEBUG")

    r = client.patch(
        f"/api/integraties/{kind}",
        headers=HEADERS,
        json={"url": "http://ander:8096", "api_sleutel": sleutel},
    )

    assert r.status_code == 422, r.text
    assert r.json()["detail"] == "ongeldige API-sleutel (onverwachte tekens)"
    assert sleutel not in r.text and sleutel not in caplog.text
    assert _opgeslagen(database, configured, kind) == ("http://emby:8096", OPGESLAGEN)


@pytest.mark.parametrize("adres", ONGELDIGE_ADRESSEN)
@pytest.mark.parametrize("kind", ["jellyfin", "radarr"])
def test_patch_weigert_een_adres_met_stuurtekens_of_witruimte_aan_de_rand(
    mg_client: MgClient, kind: str, adres: str
) -> None:
    """Een adres met een stuurteken of met witruimte aan het begin of eind geeft 422 met een vaste
    melding; het opgeslagen adres en de opgeslagen sleutel blijven staan."""
    client, database, configured = mg_client
    r = client.patch(
        f"/api/integraties/{kind}",
        headers=HEADERS,
        json={"url": "http://emby:8096", "api_sleutel": OPGESLAGEN},
    )
    assert r.status_code == 200, r.text

    for body in ({"url": adres}, {"url": adres, "api_sleutel": "nieuwe-sleutel-7"}):
        r = client.patch(f"/api/integraties/{kind}", headers=HEADERS, json=body)
        assert r.status_code == 422, (body, r.text)
        assert "stuurtekens" in r.json()["detail"]
        assert "nieuwe-sleutel-7" not in r.text and OPGESLAGEN not in r.text
        assert _opgeslagen(database, configured, kind) == ("http://emby:8096", OPGESLAGEN)


def test_patch_laat_een_opgeslagen_sleutel_van_1_2_2_onaangeroerd(mg_client: MgClient) -> None:
    """HARDE EIS: een sleutel die 1.2.2 zonder controle opsloeg, blijft staan als PATCH alleen
    het adres wijzigt; de tekencontrole geldt alleen voor een nieuw ingevoerde sleutel."""
    client, database, configured = mg_client
    oud = "emby sleutel,1"
    SecretStore(database, configured).set("jellyfin_api_sleutel", oud)

    r = client.patch(
        "/api/integraties/jellyfin", headers=HEADERS, json={"url": "http://emby:8096"}
    )

    assert r.status_code == 200, r.text
    assert r.json()["sleutel_ingesteld"] is True and oud not in r.text
    assert _opgeslagen(database, configured, "jellyfin") == ("http://emby:8096", oud)


@pytest.mark.parametrize("adres", ONGELDIGE_ADRESSEN)
@pytest.mark.parametrize("kind", ["jellyfin", "radarr"])
def test_testknop_weigert_een_adres_met_stuurtekens_met_422_zonder_verzoek(
    mg_client: MgClient, monkeypatch: pytest.MonkeyPatch, kind: str, adres: str
) -> None:
    """R-VEILIG-2: een adres dat op een tab of regeleinde eindigt gaf httpx.InvalidURL, een kale
    500. Nu 422 met een vaste melding, zonder één verzoek en zonder de sleutel in het antwoord."""
    client, _database, _settings = mg_client
    seen = _nep_mediaserver(monkeypatch, EMBY_INFO)

    r = client.post(
        f"/api/integraties/{kind}/test",
        headers=HEADERS,
        json={"url": adres, "api_sleutel": SLEUTEL},
    )

    assert r.status_code == 422, r.text
    assert "stuurtekens" in r.json()["detail"] and SLEUTEL not in r.text
    assert seen == []


@pytest.mark.parametrize("adres", ["http://emby:8096\t", "http://emby:8096/\n", "http://e\x01:1"])
def test_testknop_met_een_opgeslagen_adres_met_stuurteken_geeft_422(
    mg_client: MgClient, monkeypatch: pytest.MonkeyPatch, adres: str
) -> None:
    """Een adres dat vóór deze controle is opgeslagen (PATCH controleerde het niet) geeft bij de
    testknop ook 422 in plaats van 500; er gaat geen verzoek en de sleutel staat nergens."""
    client, database, configured = mg_client
    SecretStore(database, configured).set("jellyfin_api_sleutel", OPGESLAGEN)
    database.execute("UPDATE instelling SET jellyfin_url=? WHERE id=1", (adres,))
    seen = _nep_mediaserver(monkeypatch, EMBY_INFO)

    r = client.post("/api/integraties/jellyfin/test", headers=HEADERS, json={})

    assert r.status_code == 422, r.text
    assert "stuurtekens" in r.json()["detail"] and OPGESLAGEN not in r.text
    assert seen == []
