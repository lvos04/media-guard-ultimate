"""Contracttest mediaservers: MediaGuard tegen een echte Emby en een echte Jellyfin 12.x
(Δ-MG-AUTH, Δ-MG-DUAL; ADR-0022; contract 1.3 §2.3 regel 2, C1.3-MGU-04).

Starten met ``tests/contract/mediaserver/run.sh`` (die zet de servers en de omgeving klaar). De
tests lopen in bestandsvolgorde en bouwen op elkaar voort (``STATE``); ``run.sh`` stopt bij de
eerste fout.

Deel A  de clients van core en de encoder, rechtstreeks tegen beide servers. Een opnemende
        transportlaag legt vast welke koppen er werkelijk verstuurd worden.
Deel B  de echte images: de vorige release krijgt via zijn eigen API een Emby-koppeling, daarna
        start de kandidaat op dezelfde datamap. MediaGuard bereikt de servers via een
        doorgeefluik dat elk verzoek vastlegt (``logproxy.py``).

Wat hier **niet** bewezen wordt, staat in ``RESULTS.md``: sessies zijn nagebootst met de
sessie-API van de servers (geen echte speler), en de afspeeltest en de controle op Nederlandse
ondertitels lopen in deel A via de client, niet via een echte overdracht in de achtergrondlus.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
for extra in (ROOT / "apps" / "core", ROOT / "scripts", ROOT / "services" / "encoder"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

pytestmark = pytest.mark.skipif(
    "MGMS_VORIGE" not in os.environ,
    reason="start deze test met tests/contract/mediaserver/run.sh (echte containers nodig)")

RUN = Path(os.environ.get("MGMS_RUN") or HERE / ".run")
MGDIR = RUN / "mg"
EMBY = f"http://127.0.0.1:{os.environ.get('MGMS_EMBY_PORT', '38096')}"
JELLYFIN = f"http://127.0.0.1:{os.environ.get('MGMS_JELLYFIN_PORT', '38097')}"
MG_PORT = os.environ.get("MGMS_MG_PORT", "38098")
MG = f"http://127.0.0.1:{MG_PORT}"
MG_NAAM = os.environ.get("MGMS_MG_NAAM", "mgms-mediaguard")
NETWERK = "mgms_mgnet"
VORIGE = os.environ.get("MGMS_VORIGE", "")
KANDIDAAT = os.environ.get("MGMS_KANDIDAAT", "")
VORIGE_VERSIE = os.environ.get("MGMS_VORIGE_VERSIE", "")
VERSIE = os.environ.get("MGMS_VERSIE", "")
EMBY_VERSIE = os.environ.get("MGMS_EMBY_VERSIE", "4.10.2")
JELLYFIN_VERSIE = os.environ.get("MGMS_JELLYFIN_VERSIE", "12.4")
# zo kent MediaGuard de servers: via het doorgeefluik dat elk verzoek vastlegt
EMBY_INTERN, JELLYFIN_INTERN = "http://emby:8096", "http://jellyfin:8096"
APP_UID = 568
JF_PASS = "MgJellyfin-Test-1"
FILM = "/media/Films/Testfilm (2024)/Testfilm (2024).mkv"
FILM_ZONDER = "/media/Films/Zonder Ondertitel (2023)/Zonder Ondertitel (2023).mkv"
# de achtergrondlus van MediaGuard: eerste ronde 60 s na de start, daarna elke 30 s
EERSTE_RONDE, RONDE = 60, 30

STATE: dict[str, Any] = {}
METINGEN: dict[str, Any] = {"controles": [], "feiten": {}}
SPELERS: list[Speler] = []


# -- hulpjes --------------------------------------------------------------------------------------
def log(*args: Any) -> None:
    print("   ", *args, flush=True)


def check(naam: str, voorwaarde: Any, detail: Any = "") -> None:
    """Elke contractcontrole vastleggen (voor RESULTS.md) en daarna afdwingen."""
    test = os.environ.get("PYTEST_CURRENT_TEST", "?").split(" ")[0].split("::")[-1]
    METINGEN["controles"].append({"test": test, "controle": naam, "ok": bool(voorwaarde),
                                  "detail": str(detail)[:400]})
    log("GOED" if voorwaarde else "FOUT", naam, "|", str(detail)[:300])
    assert voorwaarde, f"{naam}: {detail}"


def feit(naam: str, waarde: Any) -> None:
    METINGEN["feiten"][naam] = waarde
    log("gemeten:", naam, "=", waarde)


def wacht_op(fn: Callable[[], Any], timeout: float = 120, interval: float = 2,
             wat: str = "voorwaarde") -> Any:
    deadline = time.monotonic() + timeout
    laatste: Any = None
    while time.monotonic() < deadline:
        try:
            laatste = fn()
            if laatste:
                return laatste
        except (httpx.HTTPError, KeyError, ValueError, AssertionError, RuntimeError) as exc:
            laatste = exc
        time.sleep(interval)
    raise AssertionError(f"niet binnen {int(timeout)} s: {wat} (laatste: {laatste!r})")


def vingerafdruk(waarde: str) -> str:
    """Dezelfde vingerafdruk als het doorgeefluik: de sleutel zelf staat nooit in een log."""
    return hashlib.sha256(waarde.encode()).hexdigest()[:16]


def docker(*args: str, check_: bool = True, timeout: float | None = 600) -> str:
    uit = subprocess.run(["docker", *args], capture_output=True, text=True, check=False,
                         timeout=timeout)
    if check_ and uit.returncode != 0:
        raise RuntimeError(f"docker {' '.join(args[:3])}…: {uit.stderr.strip()[-800:]}")
    return uit.stdout.strip()


def compose(*args: str) -> str:
    uit = subprocess.run(["docker", "compose", "-f", str(HERE / "docker-compose.yml"), *args],
                         capture_output=True, text=True, check=False, cwd=HERE, timeout=300)
    if uit.returncode != 0:
        raise RuntimeError(f"docker compose {' '.join(args)}: {uit.stderr.strip()[-800:]}")
    return uit.stdout.strip()


def luik(naam: str) -> list[dict[str, Any]]:
    """Wat MediaGuard naar deze server stuurde (regels van het doorgeefluik)."""
    pad = RUN / "proxylog" / f"{naam}.jsonl"
    if not pad.exists():
        return []
    return [json.loads(r) for r in pad.read_text("utf-8").splitlines() if r.strip()]


def sessiepolls(regels: list[dict[str, Any]], pad: str) -> list[dict[str, Any]]:
    return [r for r in regels if r["method"] == "GET" and r["path"] == pad]


def grootste_gat(regels: list[dict[str, Any]]) -> float:
    tijden = [datetime.fromisoformat(r["t"]) for r in regels if r["status"] == 200]
    return max(((b - a).total_seconds() for a, b in zip(tijden, tijden[1:], strict=False)),
               default=0.0)


# -- de echte servers inrichten (rechtstreeks via de hostpoort, niet via het doorgeefluik) ------
def _films(http: httpx.Client, kop: dict[str, str], user_id: str) -> dict[str, str] | None:
    r = http.get("/Items", headers=kop, params={
        "Recursive": "true", "IncludeItemTypes": "Movie", "Fields": "Path,MediaStreams",
        "UserId": user_id})
    if r.status_code != 200:
        return None
    items = r.json().get("Items") or []
    klaar = {it["Path"]: str(it["Id"]) for it in items
             if it.get("Path") and any(s.get("Type") == "Video"
                                       for s in it.get("MediaStreams") or [])}
    return klaar if {FILM, FILM_ZONDER} <= set(klaar) else None


def _bibliotheek(http: httpx.Client, kop: dict[str, str], user_id: str, wat: str) -> dict[str, str]:
    r = http.post("/Library/VirtualFolders", headers=kop,
                  params={"name": "Films", "collectionType": "movies", "refreshLibrary": "true"},
                  json={"LibraryOptions": {"EnableRealtimeMonitor": False,
                                           "PathInfos": [{"Path": "/media/Films"}]}})
    assert r.status_code < 300, f"bibliotheek maken: {r.status_code} {r.text[:200]}"
    # De wizard start zelf al een scan; loopt die nog als de bibliotheek erbij komt, dan slaat de
    # server het verzoek om te verversen over (gemeten bij Jellyfin 12.4: de bibliotheek bleef
    # leeg). Daarom vragen we de scan opnieuw aan tot beide films er staan.
    einde = time.monotonic() + 300
    while time.monotonic() < einde:
        for _ in range(5):
            items = _films(http, kop, user_id)
            if items:
                return items
            time.sleep(3)
        http.post("/Library/Refresh", headers=kop)
    raise AssertionError(f"{wat}: de films staan na 300 s niet in de bibliotheek")


def emby_inrichten() -> dict[str, Any]:
    http = httpx.Client(base_url=EMBY + "/emby", timeout=30)
    wacht_op(lambda: http.get("/System/Info/Public").status_code == 200, 240, wat="Emby start")
    openbaar = http.get("/System/Info/Public").json()
    http.post("/Startup/Configuration", json={"UICulture": "en-US", "MetadataCountryCode": "NL",
                                              "PreferredMetadataLanguage": "nl"})
    # 4.10.2.0 antwoordt hier 400 maar hernoemt de eerste gebruiker wel (gemeten); de naam lezen
    # we daarom terug in plaats van hem aan te nemen
    http.post("/Startup/User", json={"Name": "mgadmin"})
    http.post("/Startup/Complete")
    naam = wacht_op(lambda: http.get("/Users/Public").json()[0]["Name"], 60,
                    wat="eerste Emby-gebruiker")
    kop0 = {"X-Emby-Authorization": 'Emby Client="Contracttest", Device="pytest", '
                                    'DeviceId="mg-contract-1", Version="1"'}
    r = http.post("/Users/AuthenticateByName", json={"Username": naam, "Pw": ""}, headers=kop0)
    assert r.status_code == 200, f"Emby aanmelden: {r.status_code} {r.text[:200]}"
    token, user_id = r.json()["AccessToken"], str(r.json()["User"]["Id"])
    gebruiker = {"X-Emby-Token": token}
    r = http.post("/Auth/Keys", params={"App": "MediaGuard"}, headers=gebruiker)
    assert r.status_code < 300, f"Emby API-sleutel maken: {r.status_code} {r.text[:200]}"
    sleutels = http.get("/Auth/Keys", headers=gebruiker).json()["Items"]
    sleutel = next(k["AccessToken"] for k in sleutels if k.get("AppName") == "MediaGuard")
    items = _bibliotheek(http, gebruiker, user_id, "Emby")
    return {"soort": "emby", "url": EMBY, "voorvoegsel": "/emby", "sleutel": sleutel,
            "token": token, "gebruiker": gebruiker, "user_id": user_id, "items": items,
            "openbaar": openbaar}


def jellyfin_inrichten() -> dict[str, Any]:
    http = httpx.Client(base_url=JELLYFIN, timeout=30)

    def gestart() -> bool:
        r = http.get("/System/Info/Public")
        # tijdens het opstarten antwoordt 12.x in camelCase; PascalCase = helemaal gestart
        return r.status_code == 200 and "ProductName" in r.json()

    wacht_op(gestart, 420, wat="Jellyfin start")
    openbaar = http.get("/System/Info/Public").json()
    kop0 = {"Authorization": 'MediaBrowser Client="Contracttest", Device="pytest", '
                             'DeviceId="mg-contract-2", Version="1"'}
    stappen: list[tuple[str, str, Any]] = [
        ("POST", "/Startup/Configuration", {"UICulture": "en-US", "MetadataCountryCode": "NL",
                                            "PreferredMetadataLanguage": "nl"}),
        ("GET", "/Startup/User", None),
        ("POST", "/Startup/User", {"Name": "mgadmin", "Password": JF_PASS}),
        ("POST", "/Startup/RemoteAccess", {"EnableRemoteAccess": True,
                                           "EnableAutomaticPortMapping": False}),
        ("POST", "/Startup/Complete", None),
    ]
    for methode, pad, body in stappen:
        r = http.request(methode, pad, json=body, headers=kop0)
        assert r.status_code < 300, f"Jellyfin {pad}: {r.status_code} {r.text[:200]}"
    r = http.post("/Users/AuthenticateByName", json={"Username": "mgadmin", "Pw": JF_PASS},
                  headers=kop0)
    assert r.status_code == 200, f"Jellyfin aanmelden: {r.status_code} {r.text[:200]}"
    token, user_id = r.json()["AccessToken"], str(r.json()["User"]["Id"])
    gebruiker = {"Authorization": kop0["Authorization"] + f', Token="{token}"'}
    r = http.post("/Auth/Keys", params={"app": "MediaGuard"}, headers=gebruiker)
    assert r.status_code < 300, f"Jellyfin API-sleutel maken: {r.status_code} {r.text[:200]}"
    sleutels = http.get("/Auth/Keys", headers=gebruiker).json()["Items"]
    sleutel = next(k["AccessToken"] for k in sleutels if k.get("AppName") == "MediaGuard")
    items = _bibliotheek(http, gebruiker, user_id, "Jellyfin")
    return {"soort": "jellyfin", "url": JELLYFIN, "voorvoegsel": "", "sleutel": sleutel,
            "token": token, "gebruiker": gebruiker, "user_id": user_id, "items": items,
            "openbaar": openbaar}


class Speler:
    """Een nagebootste kijker: meldt via de sessie-API van de server dat hij een film afspeelt en
    blijft dat melden tot ``stop``. Geen echte speler en geen echte stream."""

    def __init__(self, server: dict[str, Any], pad: str = FILM) -> None:
        self.http = httpx.Client(base_url=server["url"] + server["voorvoegsel"], timeout=15,
                                 headers=server["gebruiker"])
        self.soort = server["soort"]
        self.item = server["items"][pad]
        self.gepauzeerd = False
        self.ticks = 0
        self._stop = threading.Event()
        self._draad: threading.Thread | None = None

    def _body(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "ItemId": self.item, "CanSeek": True, "IsPaused": self.gepauzeerd,
            "PositionTicks": self.ticks, "PlayMethod": "DirectPlay",
            "PlaySessionId": f"mgc-{self.soort}", "QueueableMediaTypes": ["Video"]}
        if self.soort == "emby":
            body["MediaSourceId"] = ""
        return body

    def _meld(self, pad: str) -> None:
        r = self.http.post(pad, json=self._body())
        assert r.status_code < 300, f"{self.soort} {pad}: {r.status_code} {r.text[:200]}"

    def _levend(self) -> None:
        while not self._stop.wait(8):
            if not self.gepauzeerd:
                self.ticks += 80_000_000
            try:
                self._meld("/Sessions/Playing/Progress")
            except (httpx.HTTPError, AssertionError) as exc:
                log(f"speler {self.soort}: voortgang melden mislukt ({exc})")

    def speel(self) -> None:
        self.gepauzeerd = False
        self._meld("/Sessions/Playing")
        self._stop.clear()
        self._draad = threading.Thread(target=self._levend, daemon=True)
        self._draad.start()
        SPELERS.append(self)

    def pauzeer(self, aan: bool = True) -> None:
        self.gepauzeerd = aan
        self._meld("/Sessions/Playing/Progress")

    def stop(self) -> None:
        self._stop.set()
        if self._draad is not None:
            self._draad.join(timeout=20)
            self._draad = None
        self._meld("/Sessions/Playing/Stopped")
        if self in SPELERS:
            SPELERS.remove(self)


@pytest.fixture(scope="session")
def servers() -> Iterator[dict[str, dict[str, Any]]]:
    beide = {"emby": emby_inrichten(), "jellyfin": jellyfin_inrichten()}
    feit("emby_openbaar", {k: beide["emby"]["openbaar"].get(k)
                           for k in ("ServerName", "Version", "ProductName")})
    feit("jellyfin_openbaar", {k: beide["jellyfin"]["openbaar"].get(k)
                               for k in ("ServerName", "Version", "ProductName")})
    yield beide
    for speler in list(SPELERS):
        with contextlib.suppress(httpx.HTTPError, AssertionError):
            speler.stop()


@pytest.fixture(scope="session", autouse=True)
def _metingen_bewaren() -> Iterator[None]:
    yield
    if RUN.is_dir():
        (RUN / "metingen.json").write_text(
            json.dumps(METINGEN, ensure_ascii=False, indent=1), "utf-8")


# -- deel A: de clients tegen de echte servers ---------------------------------------------------
class Opnemer(httpx.AsyncBaseTransport):
    """Stuurt elk verzoek echt door en onthoudt wat er verstuurd is (kop en adres)."""

    def __init__(self) -> None:
        self.verzoeken: list[tuple[httpx.Request, int]] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        async with httpx.AsyncHTTPTransport() as echt:
            antwoord = await echt.handle_async_request(request)
            inhoud = await antwoord.aread()
        self.verzoeken.append((request, antwoord.status_code))
        koppen = [(k, v) for k, v in antwoord.headers.items()
                  if k.lower() not in ("content-encoding", "content-length", "transfer-encoding")]
        return httpx.Response(antwoord.status_code, headers=koppen, content=inhoud,
                              request=request)

    async def aclose(self) -> None:  # de client sluit zijn transport na elk verzoek
        return


def maak_client(server: dict[str, Any], sleutel: str | None = None, soort: str | None = None,
                url: str | None = None) -> tuple[Any, Opnemer]:
    from mediaguard.mediaserver.clients import EmbyClient

    opnemer = Opnemer()
    client = EmbyClient({"mediaserver_transport": opnemer}, url or server["url"],
                        server["sleutel"] if sleutel is None else sleutel,
                        soort or server["soort"])
    return client, opnemer


def controleer_koppen(opnemer: Opnemer, soort: str, sleutel: str) -> str:
    """Elke verstuurde kop hoort bij de soort server; de sleutel staat nooit in een adres."""
    gezien: set[str] = set()
    for verzoek, _status in opnemer.verzoeken:
        assert sleutel not in str(verzoek.url), f"sleutel in het adres: {verzoek.url.path}"
        x_token = verzoek.headers.get("x-emby-token")
        auth = verzoek.headers.get("authorization", "")
        assert "x-emby-authorization" not in verzoek.headers
        if verzoek.url.path.endswith("/System/Info/Public"):
            assert not x_token and not auth, "openbare info gaat zonder sleutel"
            continue
        if soort == "emby":
            assert x_token == sleutel and not auth, f"Emby: {dict(verzoek.headers)}"
            assert verzoek.url.path.startswith("/emby/"), verzoek.url.path
            gezien.add("X-Emby-Token")
        else:
            assert auth.startswith("MediaBrowser ") and f'Token="{sleutel}"' in auth, auth[:40]
            assert not x_token, "Jellyfin krijgt geen X-Emby-Token"
            assert not verzoek.url.path.startswith("/emby"), verzoek.url.path
            gezien.add("Authorization: MediaBrowser")
    return ", ".join(sorted(gezien)) or "geen"


SOORTEN = ("emby", "jellyfin")


@pytest.mark.parametrize("soort", SOORTEN)
def test_a1_openbare_info_en_soort(servers: dict[str, Any], soort: str) -> None:
    srv = servers[soort]
    client, opnemer = maak_client(srv, sleutel="")
    info = asyncio.run(client.info())
    verwacht = EMBY_VERSIE if soort == "emby" else JELLYFIN_VERSIE
    check(f"{soort}: soort herkend als {soort}", info["kind"] == soort, info)
    check(f"{soort}: versie begint met {verwacht}", str(info["version"]).startswith(verwacht),
          info["version"])
    check(f"{soort}: openbare info zonder aanmeldkop",
          all("authorization" not in v.headers and "x-emby-token" not in v.headers
              for v, _ in opnemer.verzoeken), [str(v.url) for v, _ in opnemer.verzoeken])
    product = srv["openbaar"].get("ProductName")
    if soort == "emby":
        check("Emby 4.10.2.0 geeft geen ProductName (daarom: alleen 'jellyfin' = Jellyfin)",
              not product, product)
    else:
        check("Jellyfin meldt zich met ProductName 'Jellyfin Server'",
              "jellyfin" in str(product).lower(), product)


@pytest.mark.parametrize("soort", SOORTEN)
def test_a2_sleutel_alleen_in_de_kop_van_die_server(servers: dict[str, Any], soort: str) -> None:
    srv = servers[soort]
    client, opnemer = maak_client(srv)
    asyncio.run(client.check_key())
    mappen = asyncio.run(client.locations())
    check(f"{soort}: bibliotheekmappen gelezen", mappen == ["/media/Films"], mappen)
    kop = controleer_koppen(opnemer, soort, srv["sleutel"])
    verwacht = "X-Emby-Token" if soort == "emby" else "Authorization: MediaBrowser"
    check(f"{soort}: alleen {verwacht}, sleutel nooit in het adres", kop == verwacht, kop)
    check(f"{soort}: alle antwoorden 2xx", all(s < 300 for _, s in opnemer.verzoeken),
          [(v.url.path, s) for v, s in opnemer.verzoeken])


@pytest.mark.parametrize("soort", SOORTEN)
def test_a3_verkeerde_sleutel_geeft_een_duidelijke_fout(servers: dict[str, Any],
                                                        soort: str) -> None:
    from mediaguard.mediaserver.clients import MediaServerError

    client, _ = maak_client(servers[soort], sleutel="0123456789abcdef0123456789abcdef")
    with pytest.raises(MediaServerError) as fout:
        asyncio.run(client.check_key())
    check(f"{soort}: verkeerde sleutel → 401 'sleutel geweigerd'",
          fout.value.status == 401 and "sleutel geweigerd" in str(fout.value),
          f"{fout.value.status} {fout.value}")


def test_a4_jellyfin_weigert_de_oude_vormen_en_emby_houdt_de_zijne(servers: dict[str, Any]
                                                                   ) -> None:
    from mediaguard.mediaserver.clients import MediaServerError

    jf, emby = servers["jellyfin"], servers["emby"]
    http = httpx.Client(timeout=15)
    oud = {
        "X-Emby-Token": http.get(JELLYFIN + "/System/Info",
                                 headers={"X-Emby-Token": jf["sleutel"]}).status_code,
        "api_key= in het adres": http.get(JELLYFIN + "/System/Info",
                                          params={"api_key": jf["sleutel"]}).status_code,
        "X-Emby-Authorization": http.get(JELLYFIN + "/System/Info", headers={
            "X-Emby-Authorization": 'MediaBrowser Client="MediaGuard", Device="MediaGuard", '
                                    f'DeviceId="mg", Version="1", Token="{jf["sleutel"]}"'
        }).status_code,
        "/emby-voorvoegsel": http.get(JELLYFIN + "/emby/System/Info/Public").status_code,
    }
    feit("jellyfin_oude_vormen", oud)
    check("Jellyfin 12.x weigert X-Emby-Token, api_key= en X-Emby-Authorization (401)",
          [oud[k] for k in ("X-Emby-Token", "api_key= in het adres", "X-Emby-Authorization")]
          == [401, 401, 401], oud)
    check("Jellyfin 12.x kent het voorvoegsel /emby niet (404)",
          oud["/emby-voorvoegsel"] == 404, oud)
    nieuw = http.get(JELLYFIN + "/System/Info", headers={
        "Authorization": f'MediaBrowser Client="MediaGuard", Token="{jf["sleutel"]}"'})
    check("Jellyfin 12.x neemt Authorization: MediaBrowser aan", nieuw.status_code == 200,
          nieuw.status_code)
    emby_vormen = {
        "X-Emby-Token": http.get(EMBY + "/emby/System/Info",
                                 headers={"X-Emby-Token": emby["sleutel"]}).status_code,
        "zonder sleutel": http.get(EMBY + "/emby/System/Info").status_code,
    }
    feit("emby_vormen", emby_vormen)
    check("Emby neemt X-Emby-Token aan en weigert zonder sleutel",
          emby_vormen == {"X-Emby-Token": 200, "zonder sleutel": 401}, emby_vormen)
    # soort "Emby" gekozen op een Jellyfin-adres: de info-route zegt wat er echt draait
    client, _ = maak_client(jf, soort="emby")
    info = asyncio.run(client.info())
    check("soort Emby op een Jellyfin-adres: herkend als Jellyfin (melding 'kies de juiste "
          "soort')", info["kind"] == "jellyfin", info)
    with pytest.raises(MediaServerError) as fout:
        asyncio.run(client.check_key())
    check("…en zonder die herkenning zou elke route onder /emby 404 geven",
          fout.value.status == 404, fout.value.status)


@pytest.mark.parametrize("soort", SOORTEN)
def test_a5_sessies_lezen(servers: dict[str, Any], soort: str) -> None:
    srv = servers[soort]
    client, opnemer = maak_client(srv)
    check(f"{soort}: niemand kijkt → geen sessies", asyncio.run(client.sessions()) == [])
    speler = Speler(srv)
    speler.speel()
    try:
        kijkt = wacht_op(lambda: asyncio.run(client.sessions()), 30, wat="sessie zichtbaar")
        check(f"{soort}: één kijker, niet gepauzeerd, met titel en pad",
              len(kijkt) == 1 and not kijkt[0].paused and "Testfilm" in kijkt[0].title
              and kijkt[0].path == FILM, kijkt)
        speler.pauzeer()
        pauze = wacht_op(lambda: [s for s in asyncio.run(client.sessions()) if s.paused], 30,
                         wat="pauze zichtbaar")
        check(f"{soort}: pauze wordt herkend (telt niet als kijken)", len(pauze) == 1, pauze)
    finally:
        speler.stop()
    wacht_op(lambda: asyncio.run(client.sessions()) == [], 30, wat="sessie voorbij")
    check(f"{soort}: na stoppen geen sessies meer", True)
    controleer_koppen(opnemer, soort, srv["sleutel"])


@pytest.mark.parametrize("soort", SOORTEN)
def test_a6_bibliotheek_verversen(servers: dict[str, Any], soort: str) -> None:
    srv = servers[soort]
    client, opnemer = maak_client(srv)
    asyncio.run(client.refresh_paths([FILM]))
    asyncio.run(client.refresh_all())
    paden = [(v.method, v.url.path.removeprefix("/emby"), s) for v, s in opnemer.verzoeken]
    check(f"{soort}: gericht verversen en volledige scan aangenomen",
          [(m, p) for m, p, _ in paden] == [("POST", "/Library/Media/Updated"),
                                            ("POST", "/Library/Refresh")]
          and all(s < 300 for _, _, s in paden), paden)
    controleer_koppen(opnemer, soort, srv["sleutel"])


@pytest.mark.parametrize("soort", SOORTEN)
def test_a7_afspeeltest_en_nederlandse_ondertitels(servers: dict[str, Any], soort: str) -> None:
    srv = servers[soort]
    client, opnemer = maak_client(srv)

    def met_ondertitel() -> Any:
        res = asyncio.run(client.playback_test(FILM, "Testfilm"))
        return res if res.result != "not_found" and res.extra.get("nl_subtitles") else None

    res = wacht_op(met_ondertitel, 120, 5, wat="afspeeltest met Nederlandse ondertitel")
    check(f"{soort}: afspeeltest 'ok' (speelt direct af)", res.result == "ok",
          f"{res.result}: {res.detail_nl} {res.extra}")
    check(f"{soort}: Nederlandse ondertitel (.nl.srt) zichtbaar",
          res.extra.get("nl_subtitles") is True, res.extra)
    zonder = wacht_op(lambda: (r := asyncio.run(client.playback_test(
        FILM_ZONDER, "Zonder Ondertitel"))).result != "not_found" and r, 120, 5,
        wat="afspeeltest film zonder ondertitel")
    check(f"{soort}: film zonder Nederlandse ondertitel → niet zichtbaar",
          zonder.result == "ok" and zonder.extra.get("nl_subtitles") is False,
          f"{zonder.result} {zonder.extra}")
    weg = asyncio.run(client.playback_test("/media/Films/Bestaat Niet (1999)/x.mkv",
                                           "Bestaat Niet"))
    check(f"{soort}: onbekend bestand → 'not_found', geen verzonnen uitkomst",
          weg.result == "not_found", weg)
    controleer_koppen(opnemer, soort, srv["sleutel"])


@pytest.mark.parametrize("soort", SOORTEN)
def test_a8_encoder_kiest_dezelfde_kop(servers: dict[str, Any], soort: str) -> None:
    """De losse encoder-instelling "pauzeer bij kijken" (één adres, één sleutel)."""
    from app.integraties import jellyfin_is_playing, mediaserver_headers, mediaserver_product

    srv = servers[soort]

    async def meet() -> tuple[str, bool]:
        async with httpx.AsyncClient(timeout=15) as client:
            return (await mediaserver_product(client, srv["url"]),
                    await jellyfin_is_playing(client, srv["url"], srv["sleutel"]))

    product, kijkt = asyncio.run(meet())
    kop = mediaserver_headers(product, srv["sleutel"])
    verwacht = "X-Emby-Token" if soort == "emby" else "Authorization"
    check(f"{soort}: encoder kiest {verwacht} (ProductName {product!r})", list(kop) == [verwacht],
          list(kop))
    check(f"{soort}: encoder leest 'niemand kijkt' met die kop", kijkt is False, kijkt)
    speler = Speler(srv)
    speler.speel()
    try:
        wacht_op(lambda: asyncio.run(meet())[1], 30, wat="encoder ziet de kijker")
        check(f"{soort}: encoder ziet de kijker", True)
        speler.pauzeer()
        wacht_op(lambda: not asyncio.run(meet())[1], 30, wat="encoder ziet de pauze")
        check(f"{soort}: encoder telt een gepauzeerde sessie niet", True)
    finally:
        speler.stop()


# -- deel B: de echte images ----------------------------------------------------------------------
_DB_LEZEN = r"""
import hashlib, json, sqlite3, sys
c = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
def h(sql):
    rijen = [[v.hex() if isinstance(v, bytes) else v for v in r] for r in c.execute(sql)]
    return hashlib.sha256(json.dumps(rijen).encode()).hexdigest()[:16] if rijen else None
tabellen = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
print(json.dumps({
    "geheim_server_1": h("SELECT name,ciphertext,updated_at,updated_by FROM secrets "
                         "WHERE name='mediaserver.api_key'"),
    "geheimen": [r[0] for r in c.execute("SELECT name FROM secrets ORDER BY name")],
    "instellingen": {r[0]: json.loads(r[1]) for r in c.execute(
        "SELECT key,value_json FROM settings_values WHERE key IN "
        "('mediaserver.kind','mediaserver.url') ORDER BY key")},
    "meldingen": [list(r) for r in c.execute(
        "SELECT severity,title FROM notifications ORDER BY id")],
    "extra_servers": [list(r) for r in c.execute(
        "SELECT id,kind,label,enabled FROM mediaserver_servers ORDER BY id")]
        if "mediaserver_servers" in tabellen else None,
    "migraties": c.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0],
}))
"""
_BANEN = ("import json,sqlite3;c=sqlite3.connect('file:/config/encode/app.db?mode=ro',uri=True);"
          "print(json.dumps([list(r) for r in c.execute("
          "'SELECT baan,gepauzeerd,pauze_reden FROM worker_config ORDER BY baan')]))")


class MediaGuard:
    """De MediaGuard-container (vorige release of kandidaat) en zijn API."""

    def __init__(self) -> None:
        self.kop: dict[str, str] = {}
        self.http = httpx.Client(base_url=MG, timeout=60)
        self.gestart = 0.0
        self.logs: dict[str, str] = {}

    def voorbereiden(self) -> None:
        import upgradebewijs

        sql, fixture = upgradebewijs.kies_fixture(ROOT / "tests" / "fixtures" / "aio",
                                                  VORIGE_VERSIE)
        for d in ("config/core", "work", "data/media", "data/downloads"):
            (MGDIR / d).mkdir(parents=True, exist_ok=True)
        upgradebewijs.herstel(sql, MGDIR / "config" / "core" / "mediaguard.db")
        docker("run", "--rm", "--user", "0", "--network", "none", "--entrypoint", "sh",
               "-v", f"{MGDIR}:/mnt", VORIGE, "-c",
               f"chown -R {APP_UID}:{APP_UID} /mnt/config /mnt/work /mnt/data")
        STATE["fixture"] = fixture

    def start(self, image: str) -> None:
        docker("run", "-d", "--name", MG_NAAM, "--network", NETWERK,
               "-p", f"127.0.0.1:{MG_PORT}:30080", "-e", "TZ=UTC", "-e", "MG_HOST=127.0.0.1",
               "-v", f"{MGDIR / 'config'}:/config", "-v", f"{MGDIR / 'work'}:/work",
               "-v", f"{MGDIR / 'data'}:/data", image)
        self.gestart = time.time()

        def gezond() -> bool:
            staat = docker("inspect", "-f", "{{.State.Status}} {{if .State.Health}}"
                           "{{.State.Health.Status}}{{end}}", MG_NAAM)
            if staat.startswith(("exited", "dead")):
                staart = docker("logs", "--tail", "80", MG_NAAM, check_=False)
                raise RuntimeError(f"container gestopt: {staat}\n{staart}")
            return staat.endswith(" healthy") and \
                self.http.get("/api/v1/health").status_code == 200

        wacht_op(gezond, 600, 3, wat=f"{image} gezond")
        fixture = STATE["fixture"]
        r = self.http.post("/api/v1/auth/login", json={"username": fixture["user"],
                                                       "password": fixture["password"]})
        assert r.status_code == 200, f"inloggen: {r.status_code} {r.text[:200]}"
        koekjes = "; ".join(f"{k}={v}" for k, v in r.cookies.items())
        self.http.cookies.clear()
        csrf = r.json().get("csrf_token") or ""
        self.kop = {"Cookie": koekjes}
        if not csrf:
            csrf = self.http.get("/api/v1/auth/me", headers=self.kop).json().get("csrf_token", "")
        self.kop["X-CSRF-Token"] = csrf

    def get(self, pad: str) -> Any:
        r = self.http.get(pad, headers=self.kop)
        assert r.status_code == 200, f"GET {pad}: {r.status_code} {r.text[:300]}"
        return r.json()

    def post(self, pad: str, body: dict[str, Any] | None = None, verwacht: int = 200) -> Any:
        r = self.http.post(pad, json=body, headers=self.kop)
        assert r.status_code == verwacht, f"POST {pad}: {r.status_code} {r.text[:300]}"
        return r.json()

    def status(self) -> dict[str, Any]:
        return self.get("/api/v1/mediaserver/status")

    def config(self, body: dict[str, Any], verwacht: int = 200) -> dict[str, Any]:
        return self.post("/api/v1/mediaserver/config", body, verwacht)

    def banen(self) -> list[list[Any]]:
        """De banen van de encoder (cpu/gpu): wachten ze met de reden 'Kijken: …'?"""
        return json.loads(docker("exec", "--user", f"{APP_UID}:{APP_UID}", MG_NAAM,
                                 "/opt/venv/bin/python", "-c", _BANEN))

    def db(self) -> dict[str, Any]:
        """Geheim, instellingen en meldingen uit de core-database (alleen lezen)."""
        draait = docker("inspect", "-f", "{{.State.Running}}", MG_NAAM, check_=False) == "true"
        if draait:
            uit = docker("exec", "--user", f"{APP_UID}:{APP_UID}", MG_NAAM,
                         "/opt/venv/bin/python", "-c", _DB_LEZEN, "/config/core/mediaguard.db")
        else:
            uit = docker("run", "--rm", "--user", f"{APP_UID}:{APP_UID}", "--network", "none",
                         "--entrypoint", "/opt/venv/bin/python", "-v", f"{MGDIR}:/mnt", VORIGE,
                         "-c", _DB_LEZEN, "/mnt/config/core/mediaguard.db")
        return json.loads(uit.splitlines()[-1])

    def stop(self, fase: str) -> str:
        subprocess.run(["docker", "stop", "-t", "90", MG_NAAM], capture_output=True, check=False)
        code = docker("inspect", "-f", "{{.State.ExitCode}}", MG_NAAM, check_=False)
        uit = subprocess.run(["docker", "logs", MG_NAAM], capture_output=True, text=True,
                             check=False)
        self.logs[fase] = uit.stdout + uit.stderr
        (RUN / f"logs-mediaguard-{fase}.txt").write_text(self.logs[fase], "utf-8")
        docker("rm", "-f", MG_NAAM, check_=False)
        return code


mg = MediaGuard()
SERVER_SLEUTELS = {"server_id", "kind", "label", "url", "url_source", "key_set", "enabled",
                   "followed", "legacy", "auth", "connected", "contact", "hold", "path_map",
                   "results"}


def kijkpauze(aan: bool, timeout: float) -> dict[str, Any]:
    return wacht_op(lambda: (st := mg.status())["hold"]["active"] is aan and st, timeout, 3,
                    wat=f"kijkpauze {'aan' if aan else 'uit'}")


def wachtende_banen() -> list[str]:
    return [str(b[2]) for b in mg.banen() if b[1] and str(b[2] or "").startswith("Kijken:")]


def controleer_luik_emby(regels: list[dict[str, Any]], sleutel: str, wat: str) -> None:
    fout = [r for r in regels if not r["path"].startswith("/emby/")
            or r["auth"] not in ([], ["x-emby-token"])
            or (r["auth"] and r["token"] != vingerafdruk(sleutel))
            or vingerafdruk(sleutel) in r["query"].values()]
    check(f"{wat}: Emby kreeg alleen X-Emby-Token, onder /emby, sleutel nooit in het adres "
          f"({len(regels)} verzoeken)", regels and not fout, fout[:3])


def controleer_luik_jellyfin(regels: list[dict[str, Any]], sleutel: str, wat: str) -> None:
    met = [r for r in regels if r["auth"]]
    fout = [r for r in met if r["auth"] != ["mediabrowser"] or r["path"].startswith("/emby")
            or r["token"] != vingerafdruk(sleutel)]
    fout += [r for r in regels if vingerafdruk(sleutel) in r["query"].values()
             or {k.lower() for k in r["query"]} & {"api_key", "apikey"}]
    check(f"{wat}: Jellyfin kreeg alleen Authorization: MediaBrowser, zonder /emby, sleutel "
          f"nooit in het adres ({len(met)} verzoeken met sleutel)", met and not fout, fout[:3])


def test_b1_vorige_release_volgt_emby(servers: dict[str, Any]) -> None:
    emby = servers["emby"]
    mg.voorbereiden()
    mg.start(VORIGE)
    check(f"vorige release {VORIGE_VERSIE} draait op de gevulde fixture",
          mg.get("/api/v1/health").get("version") == VORIGE_VERSIE, mg.get("/api/v1/health"))
    uit = mg.config({"kind": "emby", "url": EMBY_INTERN, "api_key": emby["sleutel"]})
    check("vorige release: Emby gekoppeld via zijn eigen API",
          uit["test"] and uit["test"]["ok"], uit["test"])
    st = mg.status()
    check("vorige release kent nog geen servers[] (één mediaserver)", "servers" not in st,
          sorted(st))
    speler = Speler(emby)
    speler.speel()
    STATE["emby_speler"] = speler
    st = kijkpauze(True, EERSTE_RONDE + 3 * RONDE)
    feit("vorige_release_kijkpauze", st["hold"]["reason"])
    check("vorige release: kijkpauze zodra iemand op Emby kijkt",
          "Testfilm" in str(st["hold"]["reason"]), st["hold"])
    check("vorige release: banen van de encoder wachten ('Kijken: …')", wachtende_banen(),
          mg.banen())
    regels = luik("emby")
    controleer_luik_emby(regels, emby["sleutel"], "vorige release")
    STATE["emby_paden_voor"] = sorted({(r["method"], r["path"]) for r in regels})
    feit("vorige_release_emby_verzoeken", STATE["emby_paden_voor"])


def test_b2_upgrade_emby_blijft_gevolgd_zonder_handeling(servers: dict[str, Any]) -> None:
    emby = servers["emby"]
    code = mg.stop("vorige")
    check("vorige release stopt netjes (exitcode 0)", code == "0", code)
    voor = mg.db()
    STATE["db_voor"] = voor
    check("vóór de upgrade: één geheim voor de mediaserver, soort en adres ingesteld",
          voor["geheim_server_1"] and voor["instellingen"] == {
              "mediaserver.kind": "emby", "mediaserver.url": EMBY_INTERN}, voor)
    n_voor = len(luik("emby"))
    laatste_voor = luik("emby")[-1]["t"]
    mg.start(KANDIDAAT)
    check(f"kandidaat {VERSIE} draait op dezelfde datamap",
          mg.get("/api/v1/health").get("version") == VERSIE, mg.get("/api/v1/health"))
    st = mg.status()
    eerste = (st.get("servers") or [{}])[0]
    check("na de upgrade is de bestaande koppeling server 1 (Emby), zonder één handeling",
          len(st["servers"]) == 1 and eerste["server_id"] == 1 and eerste["kind"] == "emby"
          and eerste["legacy"] and eerste["key_set"] and eerste["enabled"] and eerste["followed"]
          and eerste["url"] == EMBY_INTERN and eerste["auth"] == "X-Emby-Token", eerste)
    check("de velden van 1.2.2 beschrijven nog steeds Emby",
          (st["kind"], st["url"], st["key_set"], st["setting_kind"], st["setting_url"])
          == ("emby", EMBY_INTERN, True, "emby", EMBY_INTERN),
          {k: st[k] for k in ("kind", "url", "key_set", "setting_kind", "setting_url")})
    check("servers[] bevat alleen de afgesproken velden (geen sessies, titels of gebruikers)",
          set(eerste) == SERVER_SLEUTELS, sorted(set(eerste) ^ SERVER_SLEUTELS))
    check("de kijkpauze van vóór de upgrade staat er nog (er wordt nog gekeken)",
          st["hold"]["active"] and "Testfilm" in str(st["hold"]["reason"]), st["hold"])
    na = mg.db()
    check("geheim en instellingen van de koppeling zijn byte-gelijk gebleven",
          na["geheim_server_1"] == voor["geheim_server_1"]
          and na["instellingen"] == voor["instellingen"] and na["geheimen"] == voor["geheimen"],
          {"voor": voor, "na": na})
    check("migratie 150 is toegepast en er is nog geen extra server",
          na["migraties"] == voor["migraties"] + 1 and na["extra_servers"] == [],
          {"migraties": (voor["migraties"], na["migraties"]), "extra": na["extra_servers"]})
    # de eerste ronde van de achtergrondlus: Emby wordt gevolgd zonder dat iemand iets instelde
    nieuw = wacht_op(lambda: [r for r in sessiepolls(luik("emby")[n_voor:], "/emby/Sessions")
                              if r["status"] == 200], EERSTE_RONDE + 3 * RONDE, 3,
                     wat="eerste sessiepoll van de kandidaat bij Emby")
    onderbreking = (datetime.fromisoformat(nieuw[0]["t"])
                    - datetime.fromisoformat(laatste_voor)).total_seconds()
    feit("seconden_tussen_laatste_verzoek_vorige_en_eerste_poll_kandidaat", round(onderbreking))
    st = wacht_op(lambda: (s := mg.status())["servers"][0]["connected"] and s, 30,
                  wat="server 1 verbonden")
    check("Emby is verbonden na de eerste ronde, zonder POST /config of /test",
          st["servers"][0]["connected"] and st["servers"][0]["contact"]["ok"],
          st["servers"][0])
    check("met één server is de tekst van de kijkpauze die van 1.2.2 (geen 'op …')",
          st["hold"]["active"] and " op " not in str(st["hold"]["reason"]), st["hold"])
    regels = luik("emby")[n_voor:]
    controleer_luik_emby(regels, emby["sleutel"], "kandidaat")
    check("de kandidaat vraagt Emby niets wat 1.2.2 niet vroeg",
          {(r["method"], r["path"]) for r in regels} <= set(map(tuple, STATE["emby_paden_voor"])),
          sorted({(r["method"], r["path"]) for r in regels}))
    check("Jellyfin heeft tot nu toe geen enkel verzoek met een sleutel gekregen",
          not [r for r in luik("jellyfin") if r["auth"]], luik("jellyfin")[:3])
    STATE.pop("emby_speler").stop()
    kijkpauze(False, 4 * RONDE)
    check("kijker stopt → kijkpauze weg na twee stille metingen, banen weer vrij",
          not wachtende_banen(), mg.banen())
    STATE["n_emby_kandidaat"] = n_voor


def test_b3_zoeken_vindt_beide_en_jellyfin_komt_erbij(servers: dict[str, Any]) -> None:
    emby, jf = servers["emby"], servers["jellyfin"]
    gevonden = mg.post("/api/v1/mediaserver/discover")
    paren = {(s["kind"], s["url"]) for s in gevonden["servers"]}
    check("'Zoek automatisch' vindt Emby én Jellyfin 12.x en noemt de juiste soort",
          {("emby", EMBY_INTERN), ("jellyfin", JELLYFIN_INTERN)} <= paren, sorted(paren))
    uit = mg.config({"add": True, "kind": "jellyfin", "url": JELLYFIN_INTERN,
                     "api_key": jf["sleutel"], "label": "Jelly Leon"})
    check("Jellyfin toegevoegd als server 2 en meteen getest",
          uit["server_id"] == 2 and uit["test"]["ok"], uit["test"])
    feit("jellyfin_testmelding", uit["test"]["message_nl"])
    st = uit["status"]
    lijst = [(s["server_id"], s["kind"], s["label"], s["auth"], s["enabled"], s["followed"],
              s["connected"]) for s in st["servers"]]
    check("servers[]: Emby (X-Emby-Token) en Jelly Leon (Authorization: MediaBrowser), beide "
          "gevolgd en verbonden",
          lijst == [(1, "emby", "Emby", "X-Emby-Token", True, True, True),
                    (2, "jellyfin", "Jelly Leon", "Authorization: MediaBrowser", True, True,
                     True)], lijst)
    check("de velden van 1.2.2 blijven Emby beschrijven",
          (st["kind"], st["label"], st["url"]) == ("emby", "Emby", EMBY_INTERN),
          (st["kind"], st["label"], st["url"]))
    na = mg.db()
    check("toevoegen raakt de Emby-koppeling niet: geheim en instellingen ongewijzigd, eigen "
          "geheim voor server 2",
          na["geheim_server_1"] == STATE["db_voor"]["geheim_server_1"]
          and na["instellingen"] == STATE["db_voor"]["instellingen"]
          and "mediaserver.server.2.api_key" in na["geheimen"], na)
    # vergissing: soort Emby gekozen voor het Jellyfin-adres
    fout = mg.config({"add": True, "kind": "emby", "url": JELLYFIN_INTERN,
                      "api_key": jf["sleutel"], "label": "Vergissing"})
    feit("verkeerde_soort_melding", fout["test"]["message_nl"])
    check("soort Emby op het Jellyfin-adres: duidelijke melding in plaats van 404 of 401",
          fout["test"]["ok"] is False and "Jellyfin" in fout["test"]["message_nl"]
          and "kies de juiste soort" in fout["test"]["message_nl"], fout["test"])
    weg = mg.config({"server_id": fout["server_id"], "remove": True})
    check("de vergissing verwijderen laat server 1 en 2 staan",
          [s["server_id"] for s in weg["status"]["servers"]] == [1, 2],
          [s["server_id"] for s in weg["status"]["servers"]])
    check("server 1 verwijderen bestaat niet (422): losmaken, adres en sleutel blijven",
          mg.http.post("/api/v1/mediaserver/config", json={"server_id": 1, "remove": True},
                       headers=mg.kop).status_code == 422)
    wacht_op(lambda: [r for r in sessiepolls(luik("jellyfin"), "/Sessions")
                      if r["status"] == 200], 3 * RONDE, 3, wat="eerste sessiepoll bij Jellyfin")
    controleer_luik_jellyfin(luik("jellyfin"), jf["sleutel"], "na toevoegen")
    controleer_luik_emby(luik("emby")[STATE["n_emby_kandidaat"]:], emby["sleutel"],
                         "na toevoegen")


def test_b4_kijken_op_emby_pauzeert(servers: dict[str, Any]) -> None:
    speler = Speler(servers["emby"])
    speler.speel()
    try:
        st = kijkpauze(True, 3 * RONDE)
        feit("kijkpauze_emby", st["hold"]["reason"])
        check("iemand kijkt op Emby → nieuwe encodes wachten, met de naam van de server",
              "op Emby" in st["hold"]["reason"] and "Testfilm" in st["hold"]["reason"],
              st["hold"])
        per = [(s["label"], s["hold"]["active"]) for s in st["servers"]]
        check("per server: Emby ja, Jelly Leon nee", per == [("Emby", True), ("Jelly Leon", False)],
              per)
        check("banen van de encoder wachten ('Kijken: … op Emby …')",
              any("op Emby" in b for b in wachtende_banen()), mg.banen())
    finally:
        speler.stop()
    kijkpauze(False, 4 * RONDE)
    check("kijker op Emby stopt → kijkpauze weg, banen vrij", not wachtende_banen(), mg.banen())


def test_b5_kijken_op_jellyfin_pauzeert(servers: dict[str, Any]) -> None:
    speler = Speler(servers["jellyfin"])
    speler.speel()
    try:
        st = kijkpauze(True, 3 * RONDE)
        feit("kijkpauze_jellyfin", st["hold"]["reason"])
        check("iemand kijkt op Jelly Leon → nieuwe encodes wachten, met de naam van de server",
              "op Jelly Leon" in st["hold"]["reason"] and "Testfilm" in st["hold"]["reason"],
              st["hold"])
        per = [(s["label"], s["hold"]["active"]) for s in st["servers"]]
        check("per server: Emby nee, Jelly Leon ja", per == [("Emby", False), ("Jelly Leon", True)],
              per)
        check("banen van de encoder wachten ('Kijken: … op Jelly Leon …')",
              any("op Jelly Leon" in b for b in wachtende_banen()), mg.banen())
    finally:
        speler.stop()
    kijkpauze(False, 4 * RONDE)
    check("kijker op Jelly Leon stopt → kijkpauze weg, banen vrij", not wachtende_banen(),
          mg.banen())


def test_b6_jellyfin_valt_weg_emby_blijft_gevolgd(servers: dict[str, Any]) -> None:
    emby = servers["emby"]
    speler = Speler(emby)
    speler.speel()
    try:
        kijkpauze(True, 3 * RONDE)
        n_emby = len(luik("emby"))
        compose("stop", "jellyfin", "srv-jellyfin")
        st = wacht_op(lambda: (s := mg.status())["servers"][1]["connected"] is False
                      and (s["servers"][1]["contact"] or {}).get("ok") is False and s,
                      3 * RONDE, 3, wat="Jellyfin gemeld als niet verbonden")
        feit("jellyfin_weg_foutmelding", st["servers"][1]["contact"]["error"])
        check("Jellyfin weg → server 2 niet verbonden, met een foutmelding",
              st["servers"][1]["contact"]["error"], st["servers"][1]["contact"])
        # nog een volle ronde wachten: de pauze van Emby mag niet wegvallen door Jellyfin
        time.sleep(RONDE + 8)
        st = mg.status()
        check("de kijkpauze door Emby blijft staan terwijl Jellyfin weg is",
              st["hold"]["active"] and "op Emby" in st["hold"]["reason"], st["hold"])
        check("Emby blijft verbonden terwijl Jellyfin weg is",
              st["servers"][0]["connected"] and st["servers"][0]["contact"]["ok"],
              st["servers"][0])
        polls = [r for r in sessiepolls(luik("emby")[n_emby:], "/emby/Sessions")
                 if r["status"] == 200]
        check("Emby wordt gewoon verder gevolgd (sessiepolls lopen door)", len(polls) >= 2,
              [r["t"] for r in polls])
    finally:
        speler.stop()
    st = kijkpauze(False, 4 * RONDE)
    check("kijker op Emby stopt → kijkpauze weg, ook al is Jellyfin nog steeds weg",
          st["servers"][1]["connected"] is False and not wachtende_banen(),
          (st["servers"][1]["connected"], mg.banen()))
    compose("start", "srv-jellyfin", "jellyfin")
    http = httpx.Client(timeout=10)
    wacht_op(lambda: "ProductName" in http.get(JELLYFIN + "/System/Info/Public").json(), 300,
             wat="Jellyfin weer gestart")
    wacht_op(lambda: mg.status()["servers"][1]["connected"], 4 * RONDE, 3,
             wat="Jellyfin weer verbonden")
    check("Jellyfin terug → server 2 vanzelf weer verbonden", True)


def test_b7_emby_losmaken_volgt_alleen_jellyfin(servers: dict[str, Any]) -> None:
    jf = servers["jellyfin"]
    meldingen_voor = mg.db()["meldingen"]
    n_emby = len(luik("emby"))
    feit("emby_verzoeken_tot_losmaken", n_emby)
    STATE["n_emby_losgemaakt"] = n_emby
    uit = mg.config({"server_id": 1, "enabled": False})
    st = uit["status"]
    lijst = [(s["server_id"], s["kind"], s["enabled"], s["followed"], s["connected"],
              s["key_set"], s["url"]) for s in st["servers"]]
    check("Emby losgemaakt: blijft in de lijst met adres en sleutel bewaard, niet gevolgd",
          lijst == [(1, "emby", False, False, False, True, EMBY_INTERN),
                    (2, "jellyfin", True, True, True, True, JELLYFIN_INTERN)], lijst)
    check("de velden van 1.2.2 beschrijven nu Jelly Leon",
          (st["kind"], st["label"], st["url"], st["setting_kind"])
          == ("jellyfin", "Jelly Leon", JELLYFIN_INTERN, "off"),
          (st["kind"], st["label"], st["url"], st["setting_kind"]))
    losgemaakt_op = time.monotonic()
    speler = Speler(jf)
    speler.speel()
    try:
        st = kijkpauze(True, 3 * RONDE)
        feit("kijkpauze_alleen_jellyfin", st["hold"]["reason"])
        check("alleen Jelly Leon gevolgd: kijken daar pauzeert nog steeds",
              "Testfilm" in st["hold"]["reason"] and wachtende_banen(), st["hold"])
    finally:
        speler.stop()
    kijkpauze(False, 4 * RONDE)
    time.sleep(max(0.0, 2 * RONDE + 10 - (time.monotonic() - losgemaakt_op)))
    check("na het losmaken gaat er geen enkel verzoek meer naar Emby",
          len(luik("emby")) == n_emby, luik("emby")[n_emby:][:3])
    db = mg.db()
    check("losmaken geeft geen melding of incident", db["meldingen"] == meldingen_voor,
          db["meldingen"][len(meldingen_voor):])
    check("het geheim van Emby is bewaard en ongewijzigd",
          db["geheim_server_1"] == STATE["db_voor"]["geheim_server_1"], db["geheimen"])
    acties = [a for a in mg.status()["actions"] if not a["ok"]]
    check("geen mislukte actie in de geschiedenis door het losmaken",
          not [a for a in acties if a.get("server_id") in (None, 1)], acties[:3])


def test_b8_emby_weer_koppelen_zonder_sleutel_opnieuw(servers: dict[str, Any]) -> None:
    emby = servers["emby"]
    uit = mg.config({"server_id": 1, "enabled": True})
    check("Emby weer gekoppeld in één handeling: test slaagt met de bewaarde sleutel",
          uit["test"] and uit["test"]["ok"], uit["test"])
    st = uit["status"]
    lijst = [(s["server_id"], s["kind"], s["enabled"], s["followed"], s["connected"])
             for s in st["servers"]]
    check("beide servers weer gevolgd en verbonden",
          lijst == [(1, "emby", True, True, True), (2, "jellyfin", True, True, True)], lijst)
    check("de velden van 1.2.2 beschrijven weer Emby",
          (st["kind"], st["setting_kind"], st["url"]) == ("emby", "emby", EMBY_INTERN),
          (st["kind"], st["setting_kind"], st["url"]))
    n = STATE["n_emby_losgemaakt"]
    wacht_op(lambda: [r for r in sessiepolls(luik("emby")[n:], "/emby/Sessions")
                      if r["status"] == 200], 3 * RONDE, 3, wat="Emby wordt weer gevolgd")
    controleer_luik_emby(luik("emby")[n:], emby["sleutel"], "na weer koppelen")
    check("het geheim van Emby is nog steeds hetzelfde",
          mg.db()["geheim_server_1"] == STATE["db_voor"]["geheim_server_1"])


def test_b9_niets_gelekt_en_nette_stop(servers: dict[str, Any]) -> None:
    emby, jf = servers["emby"], servers["jellyfin"]
    code = mg.stop("kandidaat")
    check("kandidaat stopt netjes (exitcode 0)", code == "0", code)
    na = mg.db()
    check("aan het eind: geheim van Emby byte-gelijk aan vóór de upgrade, soort en adres terug",
          na["geheim_server_1"] == STATE["db_voor"]["geheim_server_1"]
          and na["instellingen"] == STATE["db_voor"]["instellingen"], na)
    geheimen = {"Emby-sleutel": emby["sleutel"], "Jellyfin-sleutel": jf["sleutel"]}
    teksten = {f"log {fase}": tekst for fase, tekst in mg.logs.items()}
    for naam in ("emby", "jellyfin"):
        teksten[f"doorgeefluik {naam}"] = (RUN / "proxylog" / f"{naam}.jsonl").read_text("utf-8")
    gelekt = [(wat, waar) for wat, sleutel in geheimen.items() for waar, tekst in teksten.items()
              if sleutel in tekst]
    check("geen API-sleutel in de logs van MediaGuard of in het doorgeefluik", not gelekt, gelekt)
    feit("logregels_mediaguard", {fase: len(t.splitlines()) for fase, t in mg.logs.items()})
    # over de hele run: wat ging er werkelijk over de lijn?
    alle_emby, alle_jf = luik("emby"), luik("jellyfin")
    controleer_luik_emby(alle_emby, emby["sleutel"], "hele run")
    controleer_luik_jellyfin(alle_jf, jf["sleutel"], "hele run")
    feit("emby_aanmeldvormen", sorted({",".join(r["auth"]) or "geen" for r in alle_emby}))
    feit("jellyfin_aanmeldvormen", sorted({",".join(r["auth"]) or "geen" for r in alle_jf}))
    feit("emby_statussen", sorted({r["status"] for r in alle_emby}))
    feit("jellyfin_statussen", sorted({r["status"] for r in alle_jf}))
    check("Emby gaf op geen enkel verzoek van MediaGuard 401 of 403",
          not [r for r in alle_emby if r["status"] in (401, 403)])
    check("Jellyfin gaf op geen enkel verzoek met sleutel 401 of 403",
          not [r for r in alle_jf if r["auth"] and r["status"] in (401, 403)],
          [r for r in alle_jf if r["auth"] and r["status"] in (401, 403)][:3])
    # de Emby-koppeling in de kandidaat, van de eerste ronde tot het losmaken
    gevolgd = sessiepolls(alle_emby[STATE["n_emby_kandidaat"]:STATE["n_emby_losgemaakt"]],
                          "/emby/Sessions")
    gat = grootste_gat(gevolgd)
    feit("emby_sessiepolls_kandidaat_tot_losmaken", len(gevolgd))
    feit("emby_grootste_gat_tussen_polls_s", round(gat, 1))
    check("Emby is in de kandidaat zonder onderbreking gevolgd (geen gat van meer dan 75 s "
          "tussen twee geslaagde polls, ook niet toen Jellyfin erbij kwam of wegviel)",
          len(gevolgd) >= 10 and gat <= 75 and all(r["status"] == 200 for r in gevolgd),
          f"{len(gevolgd)} polls, grootste gat {gat:.1f} s")
