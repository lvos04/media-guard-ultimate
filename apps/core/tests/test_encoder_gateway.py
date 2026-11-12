"""AV1 Studio praat via core met de engine: login verplicht, intern token alleen server-side."""

from __future__ import annotations

import httpx


def _engine(seen: list[httpx.Request]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/api/systeem/hardware":
            return httpx.Response(200, json={"gpu": "Intel Arc A310", "cpu_threads": 16})
        if request.url.path == "/api/instellingen" and request.method == "PUT":
            return httpx.Response(200, json={"ok": True, "body": request.content.decode()})
        return httpx.Response(404, json={"detail": "niet gevonden"})
    return httpx.MockTransport(handler)


def test_gateway_adds_token_and_requires_login(client, anon_client, ctx):
    seen: list[httpx.Request] = []
    ctx.services["encoder_transport"] = _engine(seen)
    ctx.services.pop("encoder_gateway_client", None)
    assert anon_client.get("/api/v1/encoder/systeem/hardware").status_code == 401
    r = client.get("/api/v1/encoder/systeem/hardware?uren=1")
    assert r.status_code == 200 and r.json()["gpu"] == "Intel Arc A310"
    assert seen[-1].headers["X-MediaGuard-Token"] == ctx.settings.internal_token
    assert seen[-1].url.params["uren"] == "1"
    # browsercookies en CSRF gaan nooit door naar de engine
    assert "cookie" not in {k.lower() for k in seen[-1].headers}
    r = client.put("/api/v1/encoder/instellingen", json={"crf": 30})
    assert r.status_code == 200 and '"crf": 30' in r.json()["body"].replace(":30", ": 30")
    assert client.get("/api/v1/encoder/bestaat-niet").status_code == 404


def test_gateway_reports_engine_down(client, ctx):
    def down(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("weg")
    ctx.services["encoder_transport"] = httpx.MockTransport(down)
    ctx.services.pop("encoder_gateway_client", None)
    r = client.get("/api/v1/encoder/statistieken")
    assert r.status_code == 503 and "niet bereikbaar" in r.json()["detail"]


def test_gateway_get_cache_and_pagination(client, ctx):
    """Rapport 4 nov (M16): gelijke GET's binnen 2 s één keer naar de engine; limit/offset
    gaan mee; na een wijziging geen oud antwoord."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"jobs": [], "offset": request.url.params.get("offset")},
                              headers={"X-Total-Count": "14"})

    ctx.services["encoder_transport"] = httpx.MockTransport(handler)
    ctx.services.pop("encoder_gateway_client", None)
    ctx.services.pop("encoder_gateway_cache", None)
    a = client.get("/api/v1/encoder/jobs?limit=50&offset=50")
    b = client.get("/api/v1/encoder/jobs?offset=50&limit=50")
    assert a.json() == b.json() and a.json()["offset"] == "50"
    assert a.headers["x-total-count"] == "14" and len(seen) == 1
    client.post("/api/v1/encoder/jobs/1/pauzeer")
    client.get("/api/v1/encoder/jobs?limit=50&offset=50")
    assert len(seen) == 3


def test_gateway_keeps_last_known_statistics(client, ctx):
    """Rapport 2 (N14): bij een korte onderbreking de laatste besparing tonen, geen 503."""
    up = {"ok": True}

    def handler(_: httpx.Request) -> httpx.Response:
        if not up["ok"]:
            raise httpx.ConnectError("even weg")
        return httpx.Response(200, json={"totaal": {"bespaard_bytes": 7_100_000_000}})

    ctx.services["encoder_transport"] = httpx.MockTransport(handler)
    ctx.services.pop("encoder_gateway_client", None)
    ctx.services.pop("encoder_gateway_cache", None)
    ctx.services.pop("encoder_gateway_last_good", None)
    assert client.get("/api/v1/encoder/statistieken").status_code == 200
    up["ok"] = False
    ctx.services.pop("encoder_gateway_cache", None)
    r = client.get("/api/v1/encoder/statistieken")
    assert r.status_code == 200 and r.json()["totaal"]["bespaard_bytes"] == 7_100_000_000
    assert "x-mediaguard-stale" in r.headers


def test_koppelingen_van_de_encoder_wijzigen_en_testen_alleen_als_beheerder(app, client, ctx):
    """S-P2-600, R-VEILIG-1: adres en sleutel van de encoder-koppelingen (Emby/Jellyfin, Radarr,
    Sonarr) wijzigen en testen is beheer, net als POST /api/v1/mediaserver/config. Een operator
    krijgt 403 en er gaat niets naar de engine; lezen blijft voor iedereen met een login."""
    from fastapi.testclient import TestClient

    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": True})

    ctx.services["encoder_transport"] = httpx.MockTransport(handler)
    ctx.services.pop("encoder_gateway_client", None)
    ctx.services.pop("encoder_gateway_cache", None)
    keys = {role: client.post("/api/v1/auth/api-keys", json={"name": role, "role": role}).json()
            for role in ("operator", "viewer")}
    writes = [("POST", "integraties/jellyfin/test", {"url": "http://aanvaller:8096"}),
              ("PATCH", "integraties/jellyfin", {"url": "http://aanvaller:8096"}),
              ("POST", "integraties/radarr/test", {}),
              ("PATCH", "Integraties/sonarr", {"api_sleutel": "x"})]
    for role, key in keys.items():
        with TestClient(app) as other:
            other.headers["X-Api-Key"] = key.get("key") or key.get("api_key", "")
            for method, path, body in writes:
                r = other.request(method, f"/api/v1/encoder/{path}", json=body)
                assert r.status_code == 403, (role, method, path, r.text)
            assert other.get("/api/v1/encoder/integraties").status_code == 200
            if role == "operator":                # ander werk van een operator blijft mogen
                assert other.put("/api/v1/encoder/instellingen", json={}).status_code == 200
    assert not [r for r in seen if r.method in ("POST", "PATCH")]
    assert not any("integraties/" in r.url.path.lower() for r in seen)
    for method, path, body in writes[:3]:
        r = client.request(method, f"/api/v1/encoder/{path}", json=body)
        assert r.status_code == 200, (method, path, r.text)
    assert {r.url.path for r in seen if r.method != "GET"} >= {
        "/api/integraties/jellyfin/test", "/api/integraties/jellyfin",
        "/api/integraties/radarr/test"}


def test_encoder_offline_only_after_consecutive_failures(ctx):
    from mediaguard.encoding.client import note_encoder_reachability

    ctx.services.pop("encoder_fail_streak", None)
    assert not note_encoder_reachability(ctx, False)
    assert not note_encoder_reachability(ctx, False)
    assert note_encoder_reachability(ctx, False)
    assert not note_encoder_reachability(ctx, True)
