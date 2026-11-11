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
import json
import re
from pathlib import Path
from typing import Any

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


def test_sleutel_nooit_in_log_of_foutmelding(ctx, net, caplog) -> None:  # type: ignore[no-untyped-def]
    from mediaguard.secrets_store import install_log_redaction

    install_log_redaction()
    import logging

    header = auth_headers("jellyfin", "zeer-geheim-123")["Authorization"]
    logging.getLogger("mediaguard.test").warning("kop %s / X-Emby-Token: %s", header, "ook-geheim")
    assert "zeer-geheim-123" not in caplog.text and "ook-geheim" not in caplog.text


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


def test_migratie_150_voegt_alleen_toe(ctx) -> None:  # type: ignore[no-untyped-def]
    sql = (Path(S.__file__).parents[1] / "migrations" / "150_mediaserver_servers.sql").read_text()
    statements = [s.strip().split()[0:2] for s in re.split(r";\s*\n", sql)
                  if s.strip() and not s.strip().startswith("--")]
    for st in sql.splitlines():
        upper = st.upper()
        assert not any(w in upper for w in ("DROP ", "DELETE ", "UPDATE ", "RENAME ")), st
    assert statements
    cols = [r["name"] for r in ctx.db.fetch_all("PRAGMA table_info(mediaserver_actions)")]
    assert cols[-1] == "server_id"
