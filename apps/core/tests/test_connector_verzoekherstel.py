"""Contract van items en bewijs bij ontbrekende velden, rechten en brongegevens."""

from __future__ import annotations

import sqlite3

import pytest
from test_connector_lezen import error, key

from mediaguard.audit import audit


@pytest.mark.parametrize("auth", ["sleutel", "sessie"])
@pytest.mark.parametrize("route", ["items?type=status", "evidence/audit:1"])
def test_tijdelijke_aanmelduitval_weigert_met_wachttijd(
    client, anon_client, ctx, monkeypatch, auth, route
):
    if auth == "sleutel":
        headers, caller = key(client), anon_client
    else:
        headers, caller = {}, client
    fetch = ctx.db.fetch_one

    def blocked(sql, *args):
        if "FROM api_keys WHERE key_hash" in sql or "FROM sessions s JOIN users" in sql:
            raise sqlite3.OperationalError("database is locked")
        return fetch(sql, *args)

    monkeypatch.setattr(ctx.db, "fetch_one", blocked)
    response = caller.get("/connector/v1/" + route, headers=headers)
    error(response, 503, "database_unavailable")
    assert int(response.headers["Retry-After"]) >= 1 and "database is locked" not in response.text


@pytest.mark.parametrize(
    ("route", "query"),
    [
        ("items", {"type": "status", "onbekend": "proef"}),
        ("items", [("type", "status"), ("type", "wanted")]),
        ("evidence/audit:1", {"onbekend": "proef"}),
    ],
)
def test_onbekend_of_dubbel_queryveld_wordt_geweigerd(client, route, query):
    response = client.get("/connector/v1/" + route, params=query)
    assert error(response, 422, "validation_error")["details"]["fouten"]
    assert "proef" not in response.text


def test_bewijs_profielwijziging_projecteert_alleen_veilige_waarden(client, anon_client, ctx):
    headers = key(client)
    original = {"prefer_hdr": False, "blocked_groups": [headers["X-Api-Key"]]}
    for wanted in (False, True):
        response = client.put(
            "/api/v1/profiles/proef", json={"config": {**original, "prefer_hdr": wanted}}
        )
        assert response.is_success
    number = ctx.db.fetch_one(
        "SELECT MAX(id) FROM audit_events WHERE action='policy.profile_update'"
    )[0]
    response = anon_client.get(f"/connector/v1/evidence/audit:{number}", headers=headers)
    assert response.is_success and headers["X-Api-Key"] not in response.text
    data = response.json()
    assert data["request"]["prefer_hdr"] is False and data["response"]["prefer_hdr"] is True
    assert "blocked_groups" not in response.text and data["audit"]["undo_hint"]
    malformed = audit(
        ctx.db,
        actor="proef",
        action="policy.profile_update",
        old_value={"prefer_hdr": "nee"},
        new_value={"sessions": ["privé"]},
    )
    bad = anon_client.get(f"/connector/v1/evidence/audit:{malformed}", headers=headers)
    assert bad.is_success and bad.json()["request"] is None and bad.json()["response"] is None
