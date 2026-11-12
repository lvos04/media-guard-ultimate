"""Δ-MG-AUTH en Δ-MG-DUAL (contract 1.3 §2.3 regel 2, C1.3-MGU-04): MediaGuard volgt Emby én
Jelly Leon (Jellyfin 12.x) tegelijk.

- authenticatie per server: Emby ``X-Emby-Token``; Jellyfin 12.x alleen
  ``Authorization: MediaBrowser … Token="…"`` (legacy-auth uit, BG-01); nooit een sleutel in een
  URL;
- de bestaande Emby-koppeling is zonder handeling server 1 en onderbreekt nooit: niet bij het
  toevoegen van Jelly Leon, niet bij een fout of storing van Jelly Leon;
- kijkpauze als iemand op één van beide kijkt; verversen, afspeeltest en NL-ondertitels per
  server;
- ``GET /api/v1/mediaserver/status``: bestaande velden blijven, ``servers[]`` erbij zonder
  sessies;
- Emby losmaken: geen polls, verversingen of tests meer voor Emby, geschiedenis blijft, geen
  incident.

De nagebootste servers volgen de meting op de echte containers (emby/embyserver:4.10.2.0 en
jellyfin/jellyfin:12.4); dezelfde stappen tegen die containers: ``tests/contract/mediaserver``.
"""

from __future__ import annotations

import asyncio
import html
import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import quote, quote_plus

import httpx
import pytest

from mediaguard.db import utcnow
from mediaguard.mediaserver import servers as M
from mediaguard.mediaserver import service as S
from mediaguard.mediaserver.clients import EmbyClient, MediaServerError, auth_headers, valid_key

MG_ROOT = "/data/Media/Films"
FILM = f"{MG_ROOT}/Stormvogel (2017)/Stormvogel (2017).mkv"
EMBY_ROOT, JF_ROOT = "/mnt/tank/Media/Films", "/media/Films"
EMBY_FILM = FILM.replace(MG_ROOT, EMBY_ROOT)
JF_FILM = FILM.replace(MG_ROOT, JF_ROOT)
JF_AUTH = re.compile(r'^MediaBrowser .*Token="([^"]+)"')


class Server:
    """Nagebootste Emby of Jellyfin 12.x, naar de meting op de echte containers
    (``tests/contract/mediaserver``):

    - Emby 4.10.2.0: routes met én zonder ``/emby``; de openbare info heeft geen ``ProductName``;
      ``X-Emby-Token`` werkt (en ook de MediaBrowser-kop);
    - Jellyfin 12.4: ``/emby/…`` bestaat niet (404); alleen ``Authorization: MediaBrowser
      Token=…``; ``X-Emby-Token``, ``X-Emby-Authorization`` en ``api_key=`` worden geweigerd;
      tijdens het opstarten antwoordt de openbare info in camelCase en de rest met 503.
    """

    def __init__(self, kind: str, host: str, key: str, root: str) -> None:
        self.kind, self.host, self.key, self.root = kind, host, key, root
        self.down = False
        self.starting = False
        self.requests: list[httpx.Request] = []
        self.updated: list[str] = []
        self.sessions: list[dict[str, Any]] = []
        self.subs = True
        self.film = FILM.replace(MG_ROOT, root)
        #: foutpagina die de ontvangen aanmeldkoppen terugkaatst (letterlijk, kaal en als JSON)
        self.echo: int | None = None
        #: eigen foutpagina bij ``echo``: krijgt de ontvangen sleutel en bepaalt vorm en plaats
        self.echo_body: Callable[[str], str] | None = None

    def authorized(self, request: httpx.Request) -> bool:
        if "api_key" in str(request.url).lower():
            return False
        m = JF_AUTH.match(request.headers.get("Authorization", ""))
        if self.kind == "emby":
            return request.headers.get("X-Emby-Token") == self.key or bool(
                m and m.group(1) == self.key)
        if request.headers.get("X-Emby-Token") or request.headers.get("X-Emby-Authorization"):
            return False  # 12.x: legacy-auth staat uit
        return bool(m and m.group(1) == self.key)

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.down:
            raise httpx.ConnectError("verbinding geweigerd", request=request)
        path = request.url.path
        if path.startswith("/emby/"):
            if self.kind != "emby":
                return httpx.Response(404)
            path = path[len("/emby"):]
        if path == "/System/Info/Public":
            if self.kind == "emby":
                return httpx.Response(200, json={
                    "LocalAddresses": [], "RemoteAddresses": [], "ServerName": self.host,
                    "Version": "4.10.2.0", "Id": self.host})
            if self.starting:
                return httpx.Response(200, json={
                    "serverName": self.host, "version": "12.4.0", "id": self.host,
                    "productName": "Jellyfin Server", "startupWizardCompleted": True})
            return httpx.Response(200, json={
                "LocalAddress": f"http://{self.host}:8096", "ServerName": self.host,
                "Version": "12.4.0", "ProductName": "Jellyfin Server", "Id": self.host,
                "StartupWizardCompleted": True})
        if self.starting:
            return httpx.Response(503, text="<html>Server is starting</html>")
        if self.echo:
            auth = request.headers.get("Authorization", "")
            token = request.headers.get("X-Emby-Token", "")
            bare = (JF_AUTH.match(auth).group(1) if JF_AUTH.match(auth) else "") or token
            if self.echo_body is not None:
                return httpx.Response(self.echo, text=self.echo_body(bare))
            return httpx.Response(self.echo, text=(
                f"fout; verzoek was: {auth} | X-Emby-Token: {token} | sleutel {bare} | "
                + json.dumps({"headers": dict(request.headers)})))
        if not self.authorized(request):
            return httpx.Response(401)
        if path == "/System/Info":
            return httpx.Response(200, json={"Id": self.host})
        if path == "/Library/VirtualFolders":
            return httpx.Response(200, json=[{"Name": "Films", "Locations": [self.root]}])
        if path == "/Library/Media/Updated":
            self.updated += [u["Path"] for u in json.loads(request.content)["Updates"]]
            return httpx.Response(204)
        if path == "/Library/Refresh":
            return httpx.Response(204)
        if path == "/Sessions":
            return httpx.Response(200, json=self.sessions)
        if path == "/Users":
            return httpx.Response(200, json=[{"Id": "u1", "Policy": {"IsAdministrator": True}}])
        if path == "/Items":
            return httpx.Response(200, json={"Items": [{"Id": "9", "Type": "Movie",
                                                        "Path": self.film}]})
        if path.endswith("/PlaybackInfo"):
            streams: list[dict[str, Any]] = [{"Type": "Video", "Codec": "av1"}]
            if self.subs:
                streams.append({"Type": "Subtitle", "Language": "dut", "IsExternal": True})
            return httpx.Response(200, json={"MediaSources": [{"SupportsDirectPlay": True,
                                                               "MediaStreams": streams}]})
        return httpx.Response(404)

    def watching(self, title: str = "Stormvogel") -> None:
        self.sessions = [{"UserName": "Jan", "NowPlayingItem": {"Name": title, "Path": self.film},
                          "PlayState": {"IsPaused": False}}]


class Net:
    def __init__(self, *servers: Server) -> None:
        self.by_host = {s.host: s for s in servers}

    def handler(self, request: httpx.Request) -> httpx.Response:
        srv = self.by_host.get(request.url.host)
        if srv is None or request.url.port != 8096:
            raise httpx.ConnectError("geen server", request=request)
        return srv.handle(request)


class FakeEncoder:
    def __init__(self) -> None:
        self.calls: list[tuple[bool, list[str], str]] = []

    async def set_playback_hold(self, active: bool, lanes: list[str], reason: str
                                ) -> dict[str, Any]:
        self.calls.append((active, lanes, reason))
        return {}


@pytest.fixture
def net(ctx, monkeypatch):  # type: ignore[no-untyped-def]
    emby = Server("emby", "emby", "emby-sleutel", EMBY_ROOT)
    jf = Server("jellyfin", "jellyleon", "jl-sleutel", JF_ROOT)
    n = Net(emby, jf)
    ctx.services["mediaserver_transport"] = httpx.MockTransport(n.handler)
    ctx.services["encoder_client"] = FakeEncoder()
    monkeypatch.setattr(S, "mg_roots", lambda _ctx: [MG_ROOT])
    # de bestaande Emby-koppeling van 1.2.2 (instelling + geheim, geen nieuwe tabel)
    ctx.config.set("global", "", "mediaserver.kind", "emby", "test")
    ctx.config.set("global", "", "mediaserver.url", "http://emby:8096", "test")
    ctx.secrets.set(S.SECRET_KEY, "emby-sleutel", "test")
    return emby, jf


def _add_jelly_leon(ctx, key: str = "jl-sleutel") -> int:  # type: ignore[no-untyped-def]
    sid = M.add(ctx, "jellyfin", "http://jellyleon:8096", "Jelly Leon", "test")
    ctx.secrets.set(M.secret_name(sid), key, "test")
    return sid


def _txn(ctx, path: str = FILM, job: int | None = 7) -> int:  # type: ignore[no-untyped-def]
    return ctx.db.execute_insert(
        "INSERT INTO replacement_transactions(kind,library_path,final_path,staged_path,"
        "source_snapshot_json,state,encoding_job_id,created_at,updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        ("replace", path, path, "/work/x", "{}", "committed", job, utcnow(), utcnow()))


def _due(ctx, txn: int) -> None:  # type: ignore[no-untyped-def]
    for table in ("mediaserver_pending", "mediaserver_server_results"):
        ctx.db.execute(f"UPDATE {table} SET test_after=? WHERE transaction_id=? AND "
                       "test_after IS NOT NULL", ("2000-01-01T00:00:00+00:00", txn))


def tick(ctx, **kw: Any) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    return asyncio.run(S.tick(ctx, **kw))


# -- Δ-MG-AUTH ---------------------------------------------------------------------------------
def test_auth_headers_per_soort_en_nooit_in_de_url() -> None:
    assert auth_headers("emby", "k1") == {"X-Emby-Token": "k1"}
    jf = auth_headers("jellyfin", "k2")
    assert set(jf) == {"Authorization"} and JF_AUTH.match(jf["Authorization"]).group(1) == "k2"
    assert "MediaBrowser" in jf["Authorization"] and "X-Emby-Token" not in jf
    assert auth_headers("jellyfin", "") == {}
    for bad in ('a"b', "a,b", "a b", "a\\b", "é"):
        assert not valid_key(bad)
        with pytest.raises(MediaServerError):
            auth_headers("jellyfin", bad)
    assert valid_key("0123456789abcdef0123456789abcdef")


def test_jellyfin_12_weigert_x_emby_token_en_mediaguard_stuurt_het_niet(ctx, net) -> None:  # type: ignore[no-untyped-def]
    emby, jf = net
    legacy = EmbyClient(ctx.services, "http://jellyleon:8096", "jl-sleutel", "emby")
    legacy.url = "http://jellyleon:8096"  # zoals 1.2.2 een Jellyfin aansprak: X-Emby-Token
    with pytest.raises(MediaServerError, match="sleutel geweigerd"):
        asyncio.run(legacy.check_key())
    asyncio.run(EmbyClient(ctx.services, "http://jellyleon:8096", "jl-sleutel",
                           "jellyfin").check_key())
    asyncio.run(EmbyClient(ctx.services, "http://emby:8096", "emby-sleutel", "emby").check_key())
    sent = [r for r in jf.requests if r.url.path != "/System/Info/Public"]
    assert sent[-1].headers.get("Authorization", "").startswith("MediaBrowser ")
    assert "x-emby-token" not in sent[-1].headers
    assert emby.requests[-1].headers["X-Emby-Token"] == "emby-sleutel"
    assert "authorization" not in emby.requests[-1].headers
    assert not any("api_key" in str(r.url).lower() for r in emby.requests + jf.requests)


def test_sleutel_nooit_in_log_of_foutmelding(client, ctx, net, caplog) -> None:  # type: ignore[no-untyped-def]
    from mediaguard.secrets_store import install_log_redaction

    install_log_redaction()
    import logging

    header = auth_headers("jellyfin", "zeer-geheim-123")["Authorization"]
    logging.getLogger("mediaguard.test").warning("kop %s / X-Emby-Token: %s", header, "ook-geheim")
    assert "zeer-geheim-123" not in caplog.text and "ook-geheim" not in caplog.text

    # Foutmelding (S-P2-600, R-VEILIG-3/R-TECH-2): een server die bij een fout de ontvangen
    # aanmeldkop terugkaatst (letterlijk, als kale sleutel en JSON-geëscapet). De sleutel komt
    # nergens terecht: niet in MediaServerError, niet in servers[].contact.error van /status,
    # niet in het antwoord van /test of /config en niet in mediaserver_actions.
    emby, jf = net
    keys = ("emby-sleutel", "jl-sleutel")
    sid = _add_jelly_leon(ctx)
    tick(ctx, sessions=False)                 # cursor
    _txn(ctx)
    emby.echo = jf.echo = 500
    for url, key, kind in (("http://emby:8096", "emby-sleutel", "emby"),
                           ("http://jellyleon:8096", "jl-sleutel", "jellyfin")):
        with pytest.raises(MediaServerError) as exc:
            asyncio.run(EmbyClient(ctx.services, url, key, kind).check_key())
        assert exc.value.status == 500 and str(exc.value).startswith("fout 500: fout;")
        assert key not in str(exc.value), str(exc.value)
    tick(ctx)                                 # verversen en sessies: beide 500 met de echo
    st = client.get("/api/v1/mediaserver/status").json()
    errors = [x["contact"]["error"] for x in st["servers"]]
    assert all(e and e.startswith("fout 500") for e in errors), errors
    texts = [json.dumps(st)]
    texts.append(client.post("/api/v1/mediaserver/test").text)
    texts.append(client.post(f"/api/v1/mediaserver/test?server_id={sid}").text)
    r = client.post("/api/v1/mediaserver/config", json={
        "add": True, "kind": "jellyfin", "url": "jellyleon:8096", "api_key": "jl-sleutel"})
    assert r.status_code == 200 and not r.json()["test"]["ok"], r.text
    texts.append(r.text)
    acts = ctx.db.fetch_dicts("SELECT detail_nl FROM mediaserver_actions")
    assert any("verversen mislukt" in str(a["detail_nl"]) for a in acts), acts
    texts += [str(a["detail_nl"]) for a in acts]
    texts.append(caplog.text)
    for text in texts:
        for key in keys:
            assert key not in text, (key, text)


#: een sleutel zoals 1.2.2 hem zonder tekencontrole kon opslaan: met ``"`` en spaties, zodat
#: JSON, URL-code en HTML hem anders schrijven dan letterlijk
OUDE_SLEUTEL = 'oud "emby" sleutel 0123456789abcdef'


def _stukken(key: str, n: int = 8) -> set[str]:
    return {key[i:i + n] for i in range(len(key) - n + 1)}


@pytest.mark.parametrize("vorm", [
    pytest.param(lambda k: json.dumps({"ontvangen": k}), id="json"),
    pytest.param(lambda k: "/pad?q=" + quote(k, safe=""), id="url"),
    pytest.param(lambda k: "/pad?q=" + quote_plus(k), id="url-plus"),
    pytest.param(lambda k: f"<p>ontvangen: {html.escape(k)}</p>", id="html"),
    pytest.param(lambda k: "x" * 175 + k + "y" * 40, id="rond-positie-200"),
    pytest.param(lambda k: "x" * 190 + k + "y" * 40, id="over-positie-200"),
])
def test_teruggekaatste_oude_emby_sleutel_in_elke_vorm_en_rond_positie_200(  # type: ignore[no-untyped-def]
        client, ctx, net, vorm: Callable[[str], str]) -> None:
    """S-P2-600, R-TECH-7 en R-VEILIG-4: een server kaatst bij een fout de 1.2.2-Emby-sleutel
    terug als JSON, als URL-code of als HTML, of zo dat de sleutel over de grens van 200 tekens
    valt. Geen stuk van 8 of meer tekens van de sleutel staat in de melding of in
    ``servers[].contact.error``: eerst de sleutels eruit (in elke vorm), dan pas inkorten."""
    emby, _jf = net
    emby.key = OUDE_SLEUTEL
    ctx.secrets.set(S.SECRET_KEY, OUDE_SLEUTEL, "1.2.2")  # zoals 1.2.2 hem opsloeg
    tick(ctx, sessions=False)                 # cursor
    _txn(ctx)
    emby.echo, emby.echo_body = 500, vorm
    # JSON, URL en HTML schrijven deze sleutel echt anders dan letterlijk
    assert OUDE_SLEUTEL not in vorm(OUDE_SLEUTEL) or vorm(OUDE_SLEUTEL).startswith("x")
    stukken = _stukken(OUDE_SLEUTEL)
    with pytest.raises(MediaServerError) as exc:
        asyncio.run(EmbyClient(ctx.services, "http://emby:8096", OUDE_SLEUTEL,
                               "emby").check_key())
    melding = str(exc.value)
    assert not [s for s in stukken if s in melding], melding
    assert exc.value.status == 500 and melding.startswith("fout 500: ") and "***" in melding
    tick(ctx)                                 # verversen en sessies: 500 met de echo
    st = client.get("/api/v1/mediaserver/status").json()
    error = next(x for x in st["servers"] if x["server_id"] == 1)["contact"]["error"]
    assert error and not [s for s in stukken if s in error], error
    assert error.startswith("fout 500: ") and "***" in error, error


# -- soort herkennen: alleen wie zich als Jellyfin meldt, krijgt de nieuwe kop ---------------
def test_emby_zonder_productname_blijft_emby_en_jellyfin_camelcase_is_jellyfin(ctx, net) -> None:  # type: ignore[no-untyped-def]
    emby, jf = net
    info = asyncio.run(EmbyClient(ctx.services, "http://emby:8096", None, "emby").info())
    assert info["kind"] == "emby" and info["version"] == "4.10.2.0"
    info = asyncio.run(EmbyClient(ctx.services, "http://jellyleon:8096", None, "jellyfin").info())
    assert info["kind"] == "jellyfin" and info["name"] == "jellyleon"
    jf.starting = True                        # opstartfase: camelCase, de rest 503
    client = EmbyClient(ctx.services, "http://jellyleon:8096", "jl-sleutel", "jellyfin")
    assert asyncio.run(client.info()) == {"name": "jellyleon", "version": "12.4.0",
                                          "id": "jellyleon", "kind": "jellyfin"}
    with pytest.raises(MediaServerError, match="start nog op") as exc:
        asyncio.run(client.check_key())
    assert exc.value.status == 503 and "<html" not in str(exc.value)


def test_zoeken_vindt_emby_en_jellyfin_12_met_de_juiste_soort(ctx, net) -> None:  # type: ignore[no-untyped-def]
    found = asyncio.run(S.discover(ctx, hosts=["emby", "jellyleon"], udp=False))
    assert [(s["kind"], s["url"], s["label"]) for s in found["servers"]] == [
        ("emby", "http://emby:8096", "Emby"), ("jellyfin", "http://jellyleon:8096", "Jellyfin")]


def test_automatisch_op_een_jellyfin_adres_kiest_jellyfin(ctx, net) -> None:  # type: ignore[no-untyped-def]
    emby, jf = net
    ctx.config.set("global", "", "mediaserver.kind", "auto", "test")
    ctx.config.set("global", "", "mediaserver.url", "http://jellyleon:8096", "test")
    ctx.secrets.set(S.SECRET_KEY, "jl-sleutel", "test")
    res = asyncio.run(S.test_connection(ctx))
    assert res["ok"], res
    assert ctx.state_get("mediaserver_detected_kind") == "jellyfin"
    assert M.legacy(ctx).kind == "jellyfin"
    tick(ctx)
    assert M.connected(ctx, M.legacy(ctx))
    sent = [r for r in jf.requests if not r.url.path.endswith("/System/Info/Public")]
    assert sent and all(r.headers.get("Authorization", "").startswith("MediaBrowser ")
                        and "x-emby-token" not in r.headers
                        and not r.url.path.startswith("/emby/") for r in sent)


def test_automatisch_op_het_emby_adres_blijft_emby_met_x_emby_token(ctx, net) -> None:  # type: ignore[no-untyped-def]
    emby, _jf = net
    ctx.config.set("global", "", "mediaserver.kind", "auto", "test")
    res = asyncio.run(S.test_connection(ctx))
    assert res["ok"], res
    assert M.legacy(ctx).kind == "emby" and ctx.state_get("mediaserver_detected_kind") is None
    tick(ctx)
    sent = [r for r in emby.requests if not r.url.path.endswith("/System/Info/Public")]
    assert sent and all(r.headers.get("X-Emby-Token") == "emby-sleutel"
                        and "authorization" not in r.headers
                        and r.url.path.startswith("/emby/") for r in sent)


def test_verkeerde_soort_gekozen_geeft_een_duidelijke_melding(client, ctx, net) -> None:  # type: ignore[no-untyped-def]
    emby, jf = net
    # extra server: Emby gekozen, maar op het adres draait Jellyfin 12.x (en omgekeerd)
    r = client.post("/api/v1/mediaserver/config", json={
        "add": True, "kind": "emby", "url": "jellyleon:8096", "api_key": "jl-sleutel"})
    assert r.status_code == 200, r.text
    test = r.json()["test"]
    assert not test["ok"] and test["message_nl"] == (
        "Op dit adres draait Jellyfin, niet Emby: kies de juiste soort.")
    sid = r.json()["server_id"]
    r = client.post("/api/v1/mediaserver/config", json={"server_id": sid, "kind": "jellyfin"})
    assert r.json()["test"]["ok"], r.json()["test"]
    r = client.post("/api/v1/mediaserver/config", json={
        "add": True, "kind": "jellyfin", "url": "emby:8096", "api_key": "emby-sleutel"})
    assert r.json()["test"]["message_nl"] == (
        "Op dit adres draait Emby, niet Jellyfin: kies de juiste soort.")
    # de bestaande koppeling: "Emby" gekozen op een Jellyfin-adres → dezelfde melding
    ctx.config.set("global", "", "mediaserver.url", "http://jellyleon:8096", "test")
    res = asyncio.run(S.test_connection(ctx))
    assert not res["ok"] and "draait Jellyfin, niet Emby" in res["message_nl"]


def test_bestaande_koppeling_met_soort_jellyfin_op_emby_blijft_werken(ctx, net) -> None:  # type: ignore[no-untyped-def]
    """1.2.2 sprak een Emby-server achter de keuze "Jellyfin" zonder ``/emby`` aan en dat werkte;
    na de upgrade werkt dat nog (Emby 4.10.2.0 neemt ook de MediaBrowser-kop aan, gemeten)."""
    ctx.config.set("global", "", "mediaserver.kind", "jellyfin", "test")
    res = asyncio.run(S.test_connection(ctx))
    assert res["ok"], res
    tick(ctx)
    assert M.connected(ctx, M.legacy(ctx))


# -- server 1 = de bestaande koppeling --------------------------------------------------------
def test_bestaande_emby_koppeling_is_zonder_handeling_server_1(ctx, net) -> None:  # type: ignore[no-untyped-def]
    servers = M.all_servers(ctx)
    assert [(s.id, s.kind, s.url, s.legacy, s.followed) for s in servers] == [
        (1, "emby", "http://emby:8096", True, True)]
    assert servers[0].secret_name == "mediaserver.api_key"
    # niets gekopieerd: de nieuwe tabel is leeg, de instellingen zijn ongewijzigd
    assert ctx.db.fetch_one("SELECT COUNT(*) AS n FROM mediaserver_servers")["n"] == 0
    assert ctx.config.value("mediaserver.url") == "http://emby:8096"


def test_jelly_leon_toevoegen_raakt_emby_niet_en_beide_worden_gevolgd(client, ctx, net) -> None:  # type: ignore[no-untyped-def]
    emby, jf = net
    before = (ctx.config.value("mediaserver.kind"), ctx.config.value("mediaserver.url"),
              ctx.secrets.get(S.SECRET_KEY))
    r = client.post("/api/v1/mediaserver/config", json={
        "add": True, "kind": "jellyfin", "url": "jellyleon:8096", "api_key": "jl-sleutel",
        "label": "Jelly Leon"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["test"]["ok"], body["test"]
    assert after_equal(ctx, before)
    st = body["status"]
    assert [(x["server_id"], x["kind"], x["label"], x["legacy"]) for x in st["servers"]] == [
        (1, "emby", "Emby", True), (body["server_id"], "jellyfin", "Jelly Leon", False)]
    # bestaande velden blijven en beschrijven de eerste ingestelde server (Emby)
    assert st["kind"] == "emby" and st["url"] == "http://emby:8096" and st["key_set"]
    assert st["servers"][1]["auth"] == "Authorization: MediaBrowser"
    assert "jl-sleutel" not in json.dumps(st) and "emby-sleutel" not in json.dumps(st)


def after_equal(ctx, before: tuple[Any, ...]) -> bool:  # type: ignore[no-untyped-def]
    return (ctx.config.value("mediaserver.kind"), ctx.config.value("mediaserver.url"),
            ctx.secrets.get(S.SECRET_KEY)) == before


def test_fout_bij_jelly_leon_toevoegen_laat_emby_ongemoeid(client, ctx, net) -> None:  # type: ignore[no-untyped-def]
    emby, jf = net
    before = (ctx.config.value("mediaserver.kind"), ctx.config.value("mediaserver.url"),
              ctx.secrets.get(S.SECRET_KEY))
    r = client.post("/api/v1/mediaserver/config", json={
        "add": True, "kind": "jellyfin", "url": "jellyleon:8096", "api_key": "fout"})
    assert r.status_code == 200 and not r.json()["test"]["ok"]
    assert "sleutel geweigerd" in r.json()["test"]["message_nl"]
    assert after_equal(ctx, before)
    for bad in ({"add": True, "kind": "auto", "url": "x"}, {"add": True, "kind": "jellyfin"},
                {"add": True, "kind": "jellyfin", "url": "x", "api_key": 'a"b'},
                {"server_id": 1, "remove": True}, {"server_id": 99, "label": "x"}):
        r = client.post("/api/v1/mediaserver/config", json=bad)
        assert r.status_code in (404, 422), (bad, r.text)
    assert after_equal(ctx, before)
    tick(ctx)
    assert M.connected(ctx, M.get(ctx, 1))  # Emby verbonden ondanks de foute Jelly Leon


# -- kijkpauze bij een sessie op één van beide ------------------------------------------------
@pytest.mark.parametrize("op", ["emby", "jellyfin"])
def test_kijkpauze_bij_een_sessie_op_een_van_beide(ctx, net, op: str) -> None:  # type: ignore[no-untyped-def]
    emby, jf = net
    _add_jelly_leon(ctx)
    enc = ctx.services["encoder_client"]
    (emby if op == "emby" else jf).watching()
    tick(ctx)
    assert enc.calls[-1][0] is True and enc.calls[-1][1] == ["cpu", "gpu"]
    assert ("Emby" if op == "emby" else "Jelly Leon") in enc.calls[-1][2]
    assert FILM in S.watched_paths(ctx)
    st = {s.id: M.sget(ctx, s, "watching") for s in M.followed(ctx)}
    assert [bool(v["active"]) for v in st.values()] == ([True, False] if op == "emby"
                                                         else [False, True])
    emby.sessions = jf.sessions = []
    tick(ctx)
    assert enc.calls[-1][0] is True     # eerste stille meting: nog niet hervatten
    tick(ctx)
    assert enc.calls[-1][0] is False


def test_onbereikbare_jelly_leon_heft_de_emby_pauze_niet_op(ctx, net) -> None:  # type: ignore[no-untyped-def]
    emby, jf = net
    _add_jelly_leon(ctx)
    enc = ctx.services["encoder_client"]
    emby.watching()
    jf.down = True
    for _ in range(3):
        tick(ctx)
    assert enc.calls and all(c[0] for c in enc.calls)
    first, second = M.followed(ctx)
    assert M.connected(ctx, first) and not M.connected(ctx, second)


def test_storing_bij_emby_heft_de_jelly_leon_pauze_niet_op_en_omgekeerd(ctx, net) -> None:  # type: ignore[no-untyped-def]
    emby, jf = net
    _add_jelly_leon(ctx)
    enc = ctx.services["encoder_client"]
    jf.watching()
    tick(ctx)
    assert enc.calls[-1][0] is True
    # Jelly Leon valt weg terwijl er gekeken werd: de laatst bekende stand telt (geen hervatten)
    jf.down = True
    for _ in range(3):
        tick(ctx)
    assert enc.calls[-1][0] is True


def test_lang_onbereikbare_server_houdt_de_encodes_niet_eindeloos_tegen(ctx, net) -> None:  # type: ignore[no-untyped-def]
    emby, jf = net
    sid = _add_jelly_leon(ctx)
    enc = ctx.services["encoder_client"]
    jf.watching()
    tick(ctx)
    jf.down = True
    tick(ctx)
    assert enc.calls[-1][0] is True           # korte storing: de pauze blijft
    # de laatst bekende stand is ouder dan de grens: Jelly Leon telt niet meer mee
    st = M.sget(ctx, sid, "watching")
    oud = (S.now_utc() - S.timedelta(minutes=S.HOLD_STALE_MIN + 1)).isoformat(timespec="seconds")
    M.sset(ctx, sid, "watching", {**st, "at": oud})
    tick(ctx)
    tick(ctx)
    assert enc.calls[-1][0] is False          # na twee stille metingen gaan de encodes verder
    assert M.connected(ctx, M.get(ctx, 1)) and not M.connected(ctx, M.get(ctx, sid))
    # komt Jelly Leon terug terwijl er nog gekeken wordt, dan wachten de encodes weer
    jf.down = False
    tick(ctx)
    assert enc.calls[-1][0] is True and "Jelly Leon" in enc.calls[-1][2]


def test_een_uitzondering_bij_de_ene_server_stopt_de_andere_niet(ctx, net, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    emby, jf = net
    _add_jelly_leon(ctx)
    real = S.refresh_pending

    async def boom(c, client, server=None):  # type: ignore[no-untyped-def]
        if server is not None and not server.legacy:
            raise RuntimeError("kapot")
        return await real(c, client, server)

    monkeypatch.setattr(S, "refresh_pending", boom)
    tick(ctx)                                 # cursor
    _txn(ctx)
    out = tick(ctx)
    assert emby.updated == [EMBY_FILM]
    assert out["servers"]["1"]["refreshed"] == 1 and "error" in out["servers"]["2"]


# -- verversen, afspeeltest en NL-ondertitels per server --------------------------------------
def test_verversen_afspeeltest_en_nl_ondertitels_per_server(  # type: ignore[no-untyped-def]
        ctx, net, tmp_path, monkeypatch) -> None:
    emby, jf = net
    sid = _add_jelly_leon(ctx)
    # MediaGuard kent een Nederlandse ondertitel bij het bestand (.nl.srt ernaast)
    root = tmp_path / "Films"
    film = root / "Stormvogel (2017)" / "Stormvogel (2017).mkv"
    film.parent.mkdir(parents=True)
    film.write_bytes(b"x")
    film.with_suffix(".nl.srt").write_text("1\n00:00:01,000 --> 00:00:02,000\nHoi\n")
    monkeypatch.setattr(S, "mg_roots", lambda _ctx: [str(root)])
    for s in (emby, jf):
        s.film = str(film).replace(str(root), s.root)
    jf.subs = False                           # Jelly Leon ziet de NL-ondertitel niet
    tick(ctx, sessions=False)
    txn = _txn(ctx, str(film))
    tick(ctx, sessions=False)
    assert emby.updated == [emby.film] and jf.updated == [jf.film]
    _due(ctx, txn)
    tick(ctx, sessions=False)
    per = {r["server_id"]: r for r in ctx.db.fetch_dicts(
        "SELECT * FROM mediaserver_server_results WHERE transaction_id=?", (txn,))}
    assert per[1]["result"] == "ok" and per[1]["nl_subtitles"] == "visible"
    assert per[sid]["result"] == "ok" and per[sid]["nl_subtitles"] == "missing"
    summary = S.playback_for_job(ctx, 7)
    assert summary and summary["result"] == "ok" and "Jelly Leon" in summary["detail_nl"]
    note = ctx.db.fetch_one("SELECT title FROM notifications WHERE title LIKE "
                            "'NL-ondertitel niet zichtbaar in Jelly Leon%'")
    assert note is not None
    acts = ctx.db.fetch_dicts("SELECT server_id, kind FROM mediaserver_actions")
    assert {(a["server_id"], a["kind"]) for a in acts} >= {
        (1, "refresh"), (sid, "refresh"), (1, "playback_test"), (sid, "playback_test"),
        (sid, "subtitles_check")}


def test_mislukte_afspeeltest_op_een_server_meldt_met_slechtste_uitkomst(ctx, net) -> None:  # type: ignore[no-untyped-def]
    emby, jf = net
    _add_jelly_leon(ctx)
    tick(ctx, sessions=False)
    txn = _txn(ctx)
    tick(ctx, sessions=False)
    jf.film = "/elders/onbekend.mkv"          # Jelly Leon vindt het bestand niet
    _due(ctx, txn)
    for _ in range(3):
        tick(ctx, sessions=False)
        _due(ctx, txn)
    res = S.playback_for_job(ctx, 7)
    assert res and res["result"] == "not_found" and res["done_at"]


def test_upgrade_van_1_2_2_open_overdracht_wordt_door_emby_afgemaakt(ctx, net) -> None:  # type: ignore[no-untyped-def]
    """Een overdracht die vóór de upgrade al ververst was (alleen ``mediaserver_pending``, zoals
    1.2.2 schreef) wordt door server 1 afgemaakt: niet opnieuw ververst, wel getest."""
    emby, _jf = net
    S.collect_handoffs(ctx, [])              # cursor
    txn = _txn(ctx)
    S.collect_handoffs(ctx, [])              # zoals 1.2.2: geen taak per server
    ctx.db.execute("UPDATE mediaserver_pending SET refreshed_at=?, test_after=? WHERE "
                   "transaction_id=?", (utcnow(), "2000-01-01T00:00:00+00:00", txn))
    assert ctx.db.fetch_one("SELECT COUNT(*) AS n FROM mediaserver_server_results")["n"] == 0
    tick(ctx, sessions=False)
    assert emby.updated == []                 # niet opnieuw ververst
    res = S.playback_for_job(ctx, 7)
    assert res and res["result"] == "ok"


# -- status: bestaande velden + servers[] zonder kijkgedrag -----------------------------------
def test_status_servers_lijst_zonder_sessies_of_titels(client, ctx, net) -> None:  # type: ignore[no-untyped-def]
    emby, jf = net
    _add_jelly_leon(ctx)
    jf.watching("Geheime titel")
    tick(ctx)
    st = client.get("/api/v1/mediaserver/status").json()
    for key in ("kind", "label", "url", "url_source", "key_set", "connected", "hold",
                "sessions", "pending", "tests", "actions", "subtitles", "features"):
        assert key in st, key
    assert len(st["servers"]) == 2
    text = json.dumps(st["servers"])
    assert "Geheime titel" not in text and "Jan" not in text and "sessions" not in text
    assert [x["hold"]["active"] for x in st["servers"]] == [False, True]
    assert all(x["connected"] for x in st["servers"])
    assert st["hold"]["active"] is True


# -- Emby losmaken (controlelijst "Emby verwijderen", stap 5) ---------------------------------
def test_emby_losmaken_stopt_pollen_en_geeft_geen_incident(client, ctx, net) -> None:  # type: ignore[no-untyped-def]
    emby, jf = net
    _add_jelly_leon(ctx)
    tick(ctx, sessions=False)
    txn = _txn(ctx)
    tick(ctx)
    history = ctx.db.fetch_one("SELECT COUNT(*) AS n FROM mediaserver_actions")["n"]
    assert history >= 2
    r = client.post("/api/v1/mediaserver/config", json={"server_id": 1, "enabled": False})
    assert r.status_code == 200, r.text
    st = r.json()["status"]
    # losgemaakt, niet weg: Emby staat er nog, uit, met adres en sleutel bewaard
    assert [(x["kind"], x["enabled"], x["followed"], x["connected"]) for x in st["servers"]] == [
        ("emby", False, False, False), ("jellyfin", True, True, True)]
    assert st["servers"][0]["key_set"] and st["servers"][0]["url"] == "http://emby:8096"
    assert st["servers"][0]["hold"] == {"active": False}
    assert st["kind"] == "jellyfin" and st["label"] == "Jelly Leon"   # eerste server die aan staat
    n_emby, n_notes = len(emby.requests), ctx.db.fetch_one(
        "SELECT COUNT(*) AS n FROM notifications")["n"]
    emby.watching()
    _due(ctx, txn)
    for _ in range(3):
        tick(ctx)
    assert len(emby.requests) == n_emby       # geen enkel verzoek meer naar Emby
    assert ctx.db.fetch_one("SELECT COUNT(*) AS n FROM notifications")["n"] == n_notes
    assert ctx.db.fetch_one("SELECT COUNT(*) AS n FROM mediaserver_actions WHERE "
                            "server_id=1")["n"] >= 1          # geschiedenis blijft
    assert all(not c[0] for c in ctx.services["encoder_client"].calls)
    # adres en sleutel blijven bewaard: in één handeling terug naar Emby
    assert ctx.config.value("mediaserver.url") == "http://emby:8096"
    r = client.post("/api/v1/mediaserver/config", json={"server_id": 1, "enabled": True})
    assert [(x["kind"], x["enabled"]) for x in r.json()["status"]["servers"]] == [
        ("emby", True), ("jellyfin", True)]
    assert r.json()["test"]["ok"]


def test_losgemaakte_server_1_zonder_adres_en_sleutel_staat_niet_in_de_lijst(client, ctx) -> None:  # type: ignore[no-untyped-def]
    """Wie nooit een mediaserver had en "Uit" kiest, krijgt geen lege regel; en zonder bekende
    soort wordt er niets geraden."""
    assert client.post("/api/v1/mediaserver/config", json={"kind": "off"}).status_code == 200
    assert client.get("/api/v1/mediaserver/status").json()["servers"] == []
    ctx.config.set("global", "", "mediaserver.url", "http://ergens:8096", "test")
    ctx.state_set("mediaserver_kind_before_off", "auto")
    only = client.get("/api/v1/mediaserver/status").json()["servers"]
    assert [(x["server_id"], x["kind"], x["label"], x["enabled"], x["key_set"], x["auth"])
            for x in only] == [(1, "auto", "Mediaserver", False, False, None)]
    assert M.followed(ctx) == []


def test_extra_server_los_vast_en_verwijderen(client, ctx, net) -> None:  # type: ignore[no-untyped-def]
    emby, jf = net
    sid = _add_jelly_leon(ctx)
    r = client.post("/api/v1/mediaserver/config", json={"server_id": sid, "enabled": False})
    assert r.status_code == 200
    assert [x["followed"] for x in r.json()["status"]["servers"]] == [True, False]
    n = len(jf.requests)
    tick(ctx)
    assert len(jf.requests) == n
    r = client.post("/api/v1/mediaserver/config", json={"server_id": sid, "remove": True})
    assert r.status_code == 200 and len(r.json()["status"]["servers"]) == 1
    assert not ctx.secrets.has(M.secret_name(sid))
    assert client.post("/api/v1/mediaserver/config",
                       json={"server_id": sid, "label": "x"}).status_code == 404


def test_alleen_een_beheerder_beheert_servers(app, ctx, net) -> None:  # type: ignore[no-untyped-def]
    from fastapi.testclient import TestClient

    with TestClient(app) as c:
        r = c.post("/api/v1/auth/setup", json={"username": "admin", "password": "geheim-ww-1"})
        c.headers["X-CSRF-Token"] = r.json()["csrf_token"]
        key = c.post("/api/v1/auth/api-keys", json={"name": "app", "role": "operator"}).json()
    with TestClient(app) as op:
        op.headers["X-Api-Key"] = key["key"] if "key" in key else key.get("api_key", "")
        r = op.post("/api/v1/mediaserver/config", json={"add": True, "kind": "jellyfin",
                                                         "url": "jellyleon:8096"})
        assert r.status_code == 403, r.text


MIGRATIE_150 = Path(S.__file__).parents[1] / "migrations" / "150_mediaserver_servers.sql"
#: witte lijst (S-P2-600, R-TECH-5/R-HARDE-EIS-3): een migratie die alleen toevoegt bevat alleen
#: CREATE TABLE (met kolommen, geen AS SELECT), CREATE INDEX en ALTER TABLE … ADD COLUMN
_ALLEEN_TOEVOEGEN = (re.compile(r"(?is)^CREATE\s+TABLE\s+\w+\s*\((?!.*\bAS\s+SELECT\b).*\)$"),
                     re.compile(r"(?is)^CREATE\s+(?:UNIQUE\s+)?INDEX\s+\w+\s+ON\s+\w+\s*\([^;]*\)$"),
                     re.compile(r"(?is)^ALTER\s+TABLE\s+\w+\s+ADD\s+COLUMN\s+[^;]+$"))


def _alleen_toevoegen(sql: str) -> list[str]:
    """Alle opdrachten van een migratie; faalt op elke opdracht buiten de witte lijst."""
    body = "\n".join(line.split("--", 1)[0] for line in sql.splitlines())
    statements = [s.strip() for s in body.split(";") if s.strip()]
    assert statements, "lege migratie"
    for st in statements:
        assert any(p.match(st) for p in _ALLEEN_TOEVOEGEN), f"niet alleen toevoegen: {st}"
    return statements


def test_migratie_150_voegt_alleen_toe(ctx) -> None:  # type: ignore[no-untyped-def]
    statements = _alleen_toevoegen(MIGRATIE_150.read_text())
    assert [" ".join(s.split()[:2]).upper() for s in statements] == [
        "CREATE TABLE", "CREATE TABLE", "CREATE INDEX", "ALTER TABLE"]
    cols = [r["name"] for r in ctx.db.fetch_all("PRAGMA table_info(mediaserver_actions)")]
    assert cols[-1] == "server_id"


@pytest.mark.parametrize("extra", [
    "INSERT OR REPLACE INTO settings_values(scope_type, scope_id, key) VALUES ('g', '', 'x');",
    "REPLACE INTO system_state(key, value_json) VALUES ('x', '1');",
    "INSERT INTO mediaserver_servers(id, kind) SELECT 1, 'emby';",
    "CREATE TABLE kopie AS SELECT * FROM mediaserver_actions;",
    "CREATE TRIGGER t AFTER INSERT ON lib_items BEGIN DELETE FROM lib_files; END;",
    "UPDATE settings_values SET value_json = '1';",
    "DELETE FROM mediaserver_pending;",
    "DROP TABLE mediaserver_watch;",
    "ALTER TABLE mediaserver_actions RENAME TO oud;",
])
def test_migratiecontrole_wordt_rood_bij_alles_behalve_toevoegen(extra: str) -> None:
    """Mutatieproef (V-003): dezelfde migratie met één opdracht die niet alleen toevoegt."""
    with pytest.raises(AssertionError, match="niet alleen toevoegen"):
        _alleen_toevoegen(MIGRATIE_150.read_text() + "\n" + extra + "\n")


# -- herstel na de technische review (S-P2-600) -----------------------------------------------
def _redirect_net(ctx, location: str) -> list[httpx.Request]:  # type: ignore[no-untyped-def]
    """Elke route buiten /doel antwoordt met een omleiding naar ``location``; /doel geeft 200."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.startswith("/doel"):
            return httpx.Response(200, json={"Id": "x", "MediaContainer": {}})
        return httpx.Response(307, headers={"Location": location})

    ctx.services["mediaserver_transport"] = httpx.MockTransport(handler)
    return seen


def _met_sleutel(request: httpx.Request, key: str) -> bool:
    return any(key in v for v in request.headers.values()) or key in str(request.url)


@pytest.mark.parametrize("kind", ["emby", "jellyfin", "plex"])
@pytest.mark.parametrize(("start", "location", "toegestaan", "adres"), [
    ("http://ms:8096", "http://ander-adres:9999/doel?token=geheim-pad", False,
     "http://ander-adres:9999"),
    ("http://ms:8096", "http://ms:9999/doel", False, "http://ms:9999"),
    ("http://ms:8096", "https://ms:8096/doel", False, "https://ms:8096"),
    ("https://ms", "http://ms/doel", False, "http://ms"),
    ("http://ms:8096", "/doel/info", True, ""),
    ("http://ms", "https://ms/doel", True, ""),
])
def test_omleiding_alleen_binnen_dezelfde_server(ctx, kind: str, start: str, location: str,  # type: ignore[no-untyped-def]
                                                 toegestaan: bool, adres: str) -> None:
    """Besluit van de gebruiker 2026-11-12 (R-VEILIG-2): een omleiding wordt alleen gevolgd op
    dezelfde server (zelfde host en poort; http->https op dezelfde host met de standaardpoorten).
    Naar een ander adres: een duidelijke melding en geen enkel verzoek met de sleutel daarheen."""
    from mediaguard.mediaserver.clients import make_server

    key = "ms-sleutel"
    seen = _redirect_net(ctx, location)
    srv = make_server(kind, ctx.services, start, key)
    if toegestaan:
        asyncio.run(srv.check_key())
        assert seen[-1].url.path.startswith("/doel") and _met_sleutel(seen[-1], key)
        assert seen[-1].url.host == "ms"
        return
    with pytest.raises(MediaServerError) as exc:
        asyncio.run(srv.check_key())
    assert str(exc.value) == f"server stuurt door naar een ander adres: {adres}"
    assert key not in str(exc.value) and "geheim-pad" not in str(exc.value)
    assert len(seen) == 1 and not seen[0].url.path.startswith("/doel")
    if kind != "plex":                        # ook een POST (verversen) gaat niet mee
        with pytest.raises(MediaServerError, match="ander adres"):
            asyncio.run(srv.refresh_paths(["/media/x.mkv"]))
    assert not any(r.url.path.startswith("/doel") for r in seen)
    assert not any(_met_sleutel(r, key) for r in seen if r.url.host != "ms"
                   or r.url.port not in (None, 8096))


def test_omleiding_zonder_sleutel_alleen_voor_de_ondertiteldownload(ctx) -> None:  # type: ignore[no-untyped-def]
    """Alleen de downloadlink van OpenSubtitles (zonder sleutel, zoals 1.2.2) mag naar een ander
    adres doorverwijzen; draagt het verzoek een sleutel, dan nooit."""
    from mediaguard.mediaserver.clients import _call

    seen = _redirect_net(ctx, "https://cdn.elders/doel/ondertitel.srt")
    out = asyncio.run(_call(ctx.services, "GET", "https://dl.opensubtitles/x", headers={},
                            raw=True, any_host=True))
    assert out and seen[-1].url.host == "cdn.elders"
    seen.clear()
    for headers in ({"Api-Key": "os-sleutel"}, {"X-Emby-Token": "os-sleutel"}):
        with pytest.raises(MediaServerError, match="ander adres: https://cdn.elders$"):
            asyncio.run(_call(ctx.services, "GET", "https://dl.opensubtitles/x",
                              headers=headers, any_host=True))
    assert not any(r.url.host == "cdn.elders" for r in seen)
    with pytest.raises(MediaServerError, match="ander adres"):
        asyncio.run(_call(ctx.services, "GET", "https://dl.opensubtitles/x", headers={}))


def test_ondertiteldownload_volgt_een_302_naar_een_andere_host_zonder_sleutel(ctx) -> None:  # type: ignore[no-untyped-def]
    """S-P2-600, R-TECH-8 (keuze 1 van de regisseur, zoals 1.2.2): ``OpenSubtitlesClient.download``
    volgt een downloadlink die met een 302 naar een andere host gaat. De inhoud komt binnen en
    de verzoeken naar die host dragen geen Api-Key, geen Authorization en geen token."""
    from mediaguard.mediaserver.clients import OpenSubtitlesClient

    seen: list[httpx.Request] = []
    inhoud = b"1\n00:00:01,000 --> 00:00:02,000\nHallo\n"

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        host, path = request.url.host, request.url.path
        if host == "api.opensubtitles.com" and path.endswith("/login"):
            return httpx.Response(200, json={"token": "os-token-geheim"})
        if host == "api.opensubtitles.com" and path.endswith("/download"):
            return httpx.Response(200, json={"link": "https://dl.opensubtitles.com/f/x.srt"})
        if host == "dl.opensubtitles.com":
            return httpx.Response(302, headers={"Location": "https://cdn.elders.example/x.srt"})
        if host == "cdn.elders.example":
            return httpx.Response(200, content=inhoud)
        return httpx.Response(404)

    ctx.services["mediaserver_transport"] = httpx.MockTransport(handler)
    os_client = OpenSubtitlesClient(ctx.services, "os-sleutel", "gebruiker", "wachtwoord")
    assert asyncio.run(os_client.download("22")) == inhoud
    elders = [r for r in seen if r.url.host == "cdn.elders.example"]
    assert len(elders) == 1, [str(r.url) for r in seen]
    api = [r for r in seen if r.url.host == "api.opensubtitles.com"]
    assert api and all(r.headers.get("Api-Key") == "os-sleutel" for r in api)
    for r in seen:
        if r.url.host == "api.opensubtitles.com":
            continue
        assert not {"api-key", "authorization", "x-emby-token"} & set(r.headers), r.headers
        for geheim in ("os-sleutel", "os-token-geheim", "wachtwoord"):
            assert geheim not in str(r.url) and geheim not in r.content.decode()
            assert not any(geheim in v for v in r.headers.values()), r.headers


def _emby_stand(ctx) -> tuple[Any, ...]:  # type: ignore[no-untyped-def]
    open_work = ctx.db.fetch_one("SELECT COUNT(*) AS n FROM mediaserver_server_results WHERE "
                                 "server_id=1 AND done_at IS NULL")["n"]
    return (ctx.config.value("mediaserver.kind"), ctx.config.value("mediaserver.url"),
            ctx.secrets.get(S.SECRET_KEY), ctx.state_get("mediaserver_kind_before_off"),
            json.dumps(ctx.state_get("mediaserver_path_map"), sort_keys=True), open_work,
            {k: ctx.config.value(f"mediaserver.{k}") for k in
             ("pause_while_watching", "recent_watch_hours")})


@pytest.mark.parametrize("body", [
    {"kind": "jellyfin", "api_key": 'a"b'},
    {"kind": "off", "url": "http://a b"},
    {"url": "http://elders:8096", "api_key": "a,b"},
    {"enabled": False, "api_key": "a b"},
    {"kind": "off", "features": {"onbekend": 1}},
    {"url": "http://elders:8096", "features": {"recent_watch_hours": 0}},
    {"kind": "emby", "remove": True},
])
def test_geweigerd_verzoek_voor_server_1_wijzigt_niets(client, ctx, net, body) -> None:  # type: ignore[no-untyped-def]
    """S-P2-600, R-TECH-1: een verzoek dat 422 krijgt, laat de bestaande Emby-koppeling
    byte-gelijk (soort, adres, sleutel, padvertaling, open werk, functies) en Emby verbonden."""
    tick(ctx, sessions=False)                 # cursor
    _txn(ctx)
    tick(ctx)                                 # open werk voor server 1 en een padvertaling
    assert ctx.state_get("mediaserver_path_map")
    before = _emby_stand(ctx)
    assert before[5] == 1, before
    r = client.post("/api/v1/mediaserver/config", json=body)
    assert r.status_code == 422, r.text
    assert _emby_stand(ctx) == before
    tick(ctx)
    assert M.connected(ctx, M.legacy(ctx))


def test_server_1_heeft_een_eigen_wacht_in_verwijderen_en_wijzigen(ctx, net) -> None:  # type: ignore[no-untyped-def]
    """S-P2-600, R-HARDE-EIS-2: server 1 is de bestaande koppeling; ``servers.remove`` en
    ``servers.update`` weigeren hem zelf, ook buiten de API om (geheim en padvertaling blijven)."""
    tick(ctx)
    path_map = json.dumps(ctx.state_get("mediaserver_path_map"), sort_keys=True)
    for call in (lambda: M.remove(ctx, M.LEGACY_ID),
                 lambda: M.update(ctx, M.LEGACY_ID, url="http://elders:8096"),
                 lambda: M.update(ctx, M.LEGACY_ID, enabled=False)):
        with pytest.raises(ValueError, match="server 1"):
            call()
    assert ctx.secrets.get(S.SECRET_KEY) == "emby-sleutel"
    assert json.dumps(ctx.state_get("mediaserver_path_map"), sort_keys=True) == path_map
    tick(ctx)
    assert M.connected(ctx, M.legacy(ctx))


def test_een_nieuwe_server_erft_niets_van_een_verwijderde(client, ctx, net) -> None:  # type: ignore[no-untyped-def]
    """S-P2-600, R-TECH-3: een id wordt nooit opnieuw uitgedeeld; een nieuwe server begint
    zonder uitkomsten en zonder actiegeschiedenis van een verwijderde server."""
    sid = _add_jelly_leon(ctx)
    tick(ctx, sessions=False)
    _txn(ctx)
    tick(ctx, sessions=False)
    assert ctx.db.fetch_one("SELECT COUNT(*) AS n FROM mediaserver_actions WHERE server_id=?",
                            (sid,))["n"] >= 1
    r = client.post("/api/v1/mediaserver/config", json={"server_id": sid, "remove": True})
    assert r.status_code == 200, r.text
    r = client.post("/api/v1/mediaserver/config", json={
        "add": True, "kind": "plex", "url": "plex:32400", "label": "Plex"})
    assert r.status_code == 200, r.text
    new = r.json()["server_id"]
    assert new != sid and new > sid
    entry = next(x for x in r.json()["status"]["servers"] if x["server_id"] == new)
    assert set(entry["results"].values()) == {0}
    assert ctx.db.fetch_one("SELECT COUNT(*) AS n FROM mediaserver_actions WHERE server_id=?",
                            (new,))["n"] == 0


@pytest.mark.parametrize("key", ["emby sleutel", 'emby"sleutel', "emby,sleutel", "emby\\sleutel",
                                 "emby\tsleutel"])
def test_emby_sleutel_van_1_2_2_met_leestekens_blijft_werken(ctx, net, key: str) -> None:  # type: ignore[no-untyped-def]
    """S-P2-600 (HARDE EIS, vervolgpunt R-VEILIG): 1.2.2 bewaarde de sleutel zonder tekencontrole
    en stuurde hem als ``X-Emby-Token``. Zo'n sleutel blijft na de upgrade werken: spaties, ``"``,
    komma en backslash kunnen in die kop geen kop toevoegen. Alleen de MediaBrowser-kop van
    Jellyfin eist de strengere regel."""
    emby, _jf = net
    emby.key = key
    ctx.secrets.set(S.SECRET_KEY, key, "1.2.2")   # zoals 1.2.2 hem opsloeg: zonder controle
    tick(ctx)
    assert M.connected(ctx, M.legacy(ctx))
    sent = [r for r in emby.requests if not r.url.path.endswith("/System/Info/Public")]
    assert sent and all(r.headers["X-Emby-Token"] == key and "authorization" not in r.headers
                        for r in sent)
    with pytest.raises(MediaServerError, match="ongeldige API-sleutel"):
        auth_headers("jellyfin", key)


@pytest.mark.parametrize("key", ["emby\r\nX-Kwaad: 1", "emby\nsleutel", "emby\x00", "emby\x01",
                                 "emby\x7f", "embyé", " emby", "emby "])
def test_emby_sleutel_met_stuurteken_wordt_nooit_verstuurd(ctx, net, key: str) -> None:  # type: ignore[no-untyped-def]
    """CR, LF, NUL en andere stuurtekens (kopinjectie) en niet-ASCII gaan nooit de deur uit; dat
    kon 1.2.2 ook niet versturen. De koppeling meldt het in woorden, zonder de sleutel."""
    emby, _jf = net
    ctx.secrets.set(S.SECRET_KEY, key, "1.2.2")
    with pytest.raises(MediaServerError, match="ongeldige API-sleutel") as exc:
        auth_headers("emby", key)
    assert key not in str(exc.value)
    n = len(emby.requests)
    tick(ctx)
    assert not M.connected(ctx, M.legacy(ctx))
    assert all(r.url.path.endswith("/System/Info/Public") for r in emby.requests[n:])
