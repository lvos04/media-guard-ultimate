"""Correlatie voor HTTP-antwoorden en de audit van sleutelmutaties en noodstop."""

import asyncio
import json
import logging
from contextlib import closing
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from test_schema_nieuwer import nieuw_schema  # noqa: F401 - gedeelde pytest-fixture

from mediaguard.app import create_app
from mediaguard.config import Settings
from mediaguard.event_cursor import read_batch

HEADER = "X-Correlation-Id"
KEY_PATH = "/api/v1/auth/api-keys"
STOP_PATH = "/api/v1/system/emergency-stop"


def audit_rows(ctx, action):
    return ctx.db.fetch_dicts("SELECT * FROM audit_events WHERE action=? ORDER BY id", (action,))


@pytest.mark.parametrize("raw", [
    b"fixture" + b"x" * 65,
    b"geen-uuid-uit-de-proef",
    str(uuid4()).encode() + b"\n",
])
def test_ongeldige_kop_wordt_vervangen_zonder_log_of_audit(client, ctx, caplog, raw):
    caplog.set_level(logging.DEBUG)
    # TestClient levert de headerbytes rechtstreeks aan ASGI, ook met het regeleinde.
    response = client.post(KEY_PATH, headers=[(HEADER.encode(), raw)],
                           json={"name": "correlatieproef", "role": "viewer"})
    assert response.status_code == 200
    effective = response.headers[HEADER]
    assert str(UUID(effective, version=4)) == effective
    row, = audit_rows(ctx, "apikey.created")
    assert row["correlation_id"] == effective
    assert raw.rstrip().decode() not in effective
    assert raw.rstrip().decode() not in caplog.text
    assert raw.rstrip().decode() not in str(row)


def test_zonder_kop_krijgt_elk_verzoek_een_nieuwe_uuid(anon_client):
    first = anon_client.get("/healthz")
    second = anon_client.get("/healthz")
    assert first.status_code == second.status_code == 200
    a, b = (r.headers[HEADER] for r in (first, second))
    assert str(UUID(a, version=4)) == a
    assert str(UUID(b, version=4)) == b
    assert a != b


def test_dubbele_kop_krijgt_een_nieuwe_uuid(anon_client):
    first, second = str(uuid4()), str(uuid4())
    response = anon_client.get("/healthz", headers=[(HEADER, first), (HEADER, second)])
    assert response.status_code == 200
    effective, = response.headers.get_list(HEADER)
    assert str(UUID(effective, version=4)) == effective
    assert effective not in {first, second}


def test_sleutelmutaties_delen_antwoordkop_met_audit(client, ctx):
    created_id = str(uuid4()).upper()
    created = client.post(KEY_PATH, headers={HEADER: created_id},
                          json={"name": "correlatieproef", "role": "viewer"})
    assert created.status_code == 200
    key_id = str(created.json()["id"])
    revoked_id = str(uuid4())
    revoked = client.post(f"{KEY_PATH}/{key_id}/revoke", headers={HEADER: revoked_id})
    assert revoked.status_code == 200
    for response, value, action in ((created, created_id, "apikey.created"),
                                    (revoked, revoked_id, "apikey.revoked")):
        assert response.headers.get_list(HEADER) == [value]
        row, = audit_rows(ctx, action)
        assert row["target_id"] == key_id
        assert row["correlation_id"] == value


def test_herkoppelen_geeft_beide_sleutelmutaties_dezelfde_correlatie(client, ctx):
    path = "/api/v1/app/pairing/key"
    old = client.post(path)
    assert old.status_code == 200
    correlation = str(uuid4())
    new = client.post(path, headers={HEADER: correlation})
    assert new.status_code == 200
    assert new.headers[HEADER] == correlation
    revoked, = audit_rows(ctx, "apikey.revoked")
    created = audit_rows(ctx, "apikey.created")[-1]
    assert revoked["target_id"] == str(old.json()["key_id"])
    assert created["target_id"] == str(new.json()["key_id"])
    assert revoked["correlation_id"] == created["correlation_id"] == correlation


@pytest.mark.parametrize(("path", "body"), [
    (STOP_PATH, {"active": True, "reason": "proef"}),
    ("/api/v1/app/control", {"action": "emergency_stop_on", "confirm": True}),
])
def test_noodstop_deelt_antwoordkop_met_audit(client, ctx, path, body):
    correlation = str(uuid4())
    response = client.post(path, json=body, headers={HEADER: correlation})
    assert response.status_code == 200
    row, = audit_rows(ctx, "system.emergency_stop")
    assert response.headers[HEADER] == row["correlation_id"] == correlation


def test_herhaalde_noodstop_deelt_correlatie_en_weigert_opheffen(client, anon_client, ctx):
    key = client.post(KEY_PATH, json={
        "name": "noodstopproef", "role": "viewer", "scopes": ["emergency_stop:on"],
    })
    assert key.status_code == 200
    headers = {"X-Api-Key": key.json()["key"]}
    first = anon_client.post(STOP_PATH, json={"active": True}, headers=headers)
    assert first.status_code == 200
    correlation = str(uuid4())
    headers[HEADER] = correlation
    repeated = anon_client.post(STOP_PATH, json={"active": True}, headers=headers)
    assert repeated.status_code == 200
    assert repeated.json() == first.json()
    row, = audit_rows(ctx, "system.emergency_stop_repeated")
    assert repeated.headers[HEADER] == row["correlation_id"] == correlation
    denied = anon_client.post(STOP_PATH, json={"active": False}, headers=headers)
    assert denied.status_code == 403
    assert denied.headers[HEADER] == correlation
    assert ctx.state_get("emergency_stop") == first.json()
    change, = audit_rows(ctx, "system.emergency_stop")
    assert change["correlation_id"] == first.headers[HEADER]


def test_connectorfout_behoudt_contract_en_antwoordkop(anon_client):
    correlation = str(uuid4())
    response = anon_client.get("/connector/v1/discover", headers={HEADER: correlation})
    assert response.status_code == 401
    assert response.headers[HEADER] == correlation
    body = response.json()
    assert set(body) == {"code", "message", "details", "trace_id"}
    assert body["code"] == "unauthorized"
    assert str(UUID(body["trace_id"])) == body["trace_id"]


def test_ook_onverwachte_kernfout_krijgt_antwoordkop(app):
    @app.get("/correlatieproef")
    async def fout():
        raise RuntimeError("synthetische fout")

    correlation = str(uuid4())
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/correlatieproef", headers={HEADER: correlation})
    assert response.status_code == 500
    assert response.headers[HEADER] == correlation


def test_herstelantwoord_krijgt_antwoordkop(nieuw_schema):  # noqa: F811
    app = create_app(Settings(data_dir=nieuw_schema.parent, start_background=False))
    correlation = str(uuid4())
    with closing(app.state.ctx.db), TestClient(app) as client:
        response = client.get("/readyz", headers={HEADER: correlation})
    assert response.status_code == 503
    assert response.json()["reason_code"] == "schema_nieuwer"
    assert response.headers[HEADER] == correlation


async def test_sse_antwoordkop_replay_en_verbreken(app, client, ctx):
    key = client.post(KEY_PATH, json={"name": "SSE-proef", "role": "viewer"})
    assert key.status_code == 200
    ctx.events.publish("proef.vooraf", {})
    first = ctx.db.fetch_one("SELECT MAX(id) FROM outbox_events")[0]
    batch = read_batch(ctx.db, None, channel="sse", through=first, limit=1, newest=True)
    cursor = batch.next_cursor()
    ctx.events.publish("proef.replay", {"waarde": 1})
    correlation = str(uuid4())
    scope = {
        "type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1", "method": "GET", "scheme": "http",
        "path": "/api/v1/events/stream", "query_string": b"",
        "headers": [(HEADER.lower().encode(), correlation.encode()),
                    (b"last-event-id", cursor.encode()),
                    (b"x-api-key", key.json()["key"].encode())],
    }
    disconnect = asyncio.Event()
    messages = []

    async def receive():
        await disconnect.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        messages.append(message)
        if message.get("body") == b"retry: 3000\n\n":
            disconnect.set()

    await asyncio.wait_for(app(scope, receive, send), timeout=3)
    start = messages[0]
    assert start["status"] == 200
    frame, retry = [message for message in messages[1:] if message.get("body")]
    assert (b"x-correlation-id", correlation.encode()) in start["headers"]
    assert (b"x-accel-buffering", b"no") in start["headers"]
    event_id, data = frame["body"].decode().splitlines()[:2]
    assert event_id.startswith("id: ")
    assert event_id.removeprefix("id: ") != cursor
    assert json.loads(data.removeprefix("data: "))["topic"] == "proef.replay"
    assert frame["more_body"] and retry["more_body"]
    assert not ctx.events._subscribers
