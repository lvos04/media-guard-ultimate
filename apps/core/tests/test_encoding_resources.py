"""Systeem & prestaties: core zet CPU-budget, encodes tegelijk en tijdvenster door."""

from __future__ import annotations

import httpx
from test_encoding_support import TOKEN

from mediaguard.encoding import autopilot, resources


def _fake(seen: list[dict]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("X-MediaGuard-Token") == TOKEN
        if request.url.path.endswith("/settings/resources"):
            if request.method == "PUT":
                import json
                seen.append(json.loads(request.content))
            body = seen[-1] if seen else {}
            return httpx.Response(200, json={"percent": body.get("cpu_percent", 100),
                                             "cores": 16, "threads_per_encode": 12})
        return httpx.Response(200, json={})
    return httpx.MockTransport(handler)


async def test_resources_pushed_and_window_inverted(client, ctx):
    seen: list[dict] = []
    ctx.services["encoder_transport"] = _fake(seen)
    first = await resources.push(ctx)
    assert first["pushed"] and seen[-1] == {
        "cpu_percent": 75, "cpu_workers": 1, "gpu_workers": 1, "max_cpu_temp": 85,
        "nice": 10, "quiet_start": None, "quiet_end": None}
    assert not (await resources.push(ctx))["pushed"]  # ongewijzigd: geen tweede push
    ctx.config.set("global", "", "resources.encode_window_enabled", True, "t")
    ctx.config.set("global", "", "resources.encode_window", {"start": "23:00", "end": "07:00"},
                   "t")
    ctx.config.set("global", "", "resources.encodes_cpu", 2, "t")
    await resources.push(ctx)
    # encoderen 23–07 = stil van 07 tot 23
    assert (seen[-1]["quiet_start"], seen[-1]["quiet_end"]) == ("07:00", "23:00")
    assert seen[-1]["cpu_workers"] == 2
    status = client.get("/api/v1/encoding/resources").json()
    assert status["effective"]["cores"] == 16 and status["parallel_encodes"] == 3
    # autopilot volgt 'encodes tegelijk' automatisch, per baan één op voorraad
    # (2 CPU + 1 op voorraad, 1 GPU + 1 op voorraad; verbeterpunt 6)
    assert autopilot.queue_depth(ctx) == 5
    ctx.config.set("global", "", "encoding.autopilot_queue_depth", 2, "t")
    assert autopilot.queue_depth(ctx) == 2


def test_resource_settings_validated(client):
    r = client.patch("/api/v1/settings/scoped", json={"changes": [
        {"scope_type": "global", "scope_id": "", "key": "resources.av1_cpu_percent",
         "value": 5}], "reason": "t"})
    assert r.status_code == 422


async def test_engine_out_of_sync_is_repushed_and_cores_computed(client, ctx):
    """Rapport 4 nov (H11): engine toonde 90 % naast 75 % in core. Status trekt dat direct
    bij en rekent '≈ N van M kernen' uit de huidige waarde."""
    state = {"percent": 90}

    def handler(request: httpx.Request) -> httpx.Response:
        import json
        if request.url.path.endswith("/settings/resources"):
            if request.method == "PUT":
                state["percent"] = json.loads(request.content)["cpu_percent"]
            return httpx.Response(200, json={"percent": state["percent"], "cpu_workers": 1,
                                             "cores": 28})
        return httpx.Response(200, json={})

    ctx.services["encoder_transport"] = httpx.MockTransport(handler)
    ctx.state_set(resources.PUSHED_STATE, {"value": resources.desired(ctx),
                                           "at": "2999-01-01T00:00:00+00:00"})
    ctx.config.set("global", "", "resources.av1_cpu_percent", 35, "t")
    ctx.state_set(resources.PUSHED_STATE, {"value": resources.desired(ctx),
                                           "at": "2999-01-01T00:00:00+00:00"})
    st = client.get("/api/v1/encoding/resources").json()
    assert st["repushed"] and st["in_sync"] and st["engine_cpu_percent"] == 35
    assert st["cores_nl"] == "≈ 10 van 28 kernen"
