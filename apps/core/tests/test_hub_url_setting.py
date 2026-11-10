"""Peil O-11 (ADR-0016 punt 1): optionele instelling system.hub_url voor 'Terug naar de Hub'.

Bestaand instellingenregister, geen nieuwe route: lezen via GET /settings/effective (elke rol),
schrijven via PATCH /settings/scoped met de HUB_URL-regels van het contract (§3.2).
"""

from __future__ import annotations

import pytest

from mediaguard.hub_url import HUB_URL_ERROR, validate_hub_url

# Fictieve, gedocumenteerde testadressen (RFC 5737 / RFC 3849 / .example): geen NAS-gegevens.
GOOD = [
    ("http://192.0.2.10:8080", "http://192.0.2.10:8080"),
    ("https://hub.example:30443/", "https://hub.example:30443"),
    ("HTTP://Hub.Example", "http://hub.example"),
    ("http://[2001:db8::5]:9000", "http://[2001:db8::5]:9000"),
    ("  http://198.51.100.7  ", "http://198.51.100.7"),
]
BAD = [
    "javascript:alert(1)", "ftp://192.0.2.10", "http://", "192.0.2.10:8080",
    "http://192.0.2.10:8080/hub", "http://192.0.2.10/?x=1", "http://192.0.2.10/#a", "http://192.0.2.10?",
    "http://user:pw@192.0.2.10", "http://localhost:8080", "http://app.localhost", "http://127.0.0.1",
    "http://127.1.2.3:80", "http://[::1]:80", "http://0.0.0.0:80", "http://[::]:80",
    "http://169.254.1.1", "http://[fe80::1]", "http://[::ffff:127.0.0.1]", "http://192.0.2.10:99999",
    "http://192.0.2.10 :80", "http:\\\\192.0.2.10",
]


# Contract 1.3 §3.2 (adresregels, A11; C1.3-MGU-08): alleen canonieke IP-vormen, IPv6 tussen [],
# één punt aan het eind van een naam telt niet mee, geen %-codering of IDN (de browser zou er een
# ander adres van maken: http://127%2e0%2e0%2e1 en http://0x7f.1 worden 127.0.0.1).
GOOD_13 = [
    ("http://hub.example.:30443", "http://hub.example:30443"),
    ("http://192.0.2.10.", "http://192.0.2.10"),
    ("https://xn--hb-via.example", "https://xn--hb-via.example"),
    ("http://[2001:DB8::5]", "http://[2001:db8::5]"),
    ("http://nas-1.example:08080", "http://nas-1.example:8080"),
    ("http://192.0.2.10:80", "http://192.0.2.10:80"),
    # grens van fe80::/10: 0fe8:: en fec0:: zijn geen link-local (QA-review: web was strenger)
    ("http://[fe8::1]", "http://[fe8::1]"),
    ("http://[FEC0::1]:80", "http://[fec0::1]:80"),
]
BAD_13 = [
    "http://127.1", "http://2130706433", "http://0x7f000001", "http://0x7f.1", "http://010.0.0.1",
    "http://0", "http://192.0.2.010", "http://256.1.2.3", "http://1.2.3.4.5", "http://foo.123",
    "http://localhost.", "http://localhost..", "http://127.0.0.1.", "http://0.1.2.3",
    "http://127%2e0%2e0%2e1", "http://hub%2eexample", "http://hub..example", "http://.hub.example",
    "http://-hub.example", "http://hub_x.example", "http://\u24dbocalhost",
    "http://\uff11\uff12\uff17.\uff10.\uff10.\uff11", "http://h\u00fcb.example", "http://[v1.x]",
    "http://[fe80::1%25eth0]:80", "http://[::ffff:7f00:1]", "http://[::ffff:127.0.0.1]",
    "http://[2001:db8::5]x", "http://192.0.2.10:65536",
    "http://hub.example:", "http://@hub.example", "http://hub.example:80@192.0.2.10",
    "http://[febf::1]", "http://[FE80:0:0:0:0:0:0:1]", "http://[::ffff:169.254.0.1]",
    "http://[::ffff:0.0.0.0]",
]


@pytest.mark.parametrize(("raw", "expected"), GOOD_13)
def test_contract_13_canonical_forms_are_accepted(raw: str, expected: str) -> None:
    assert validate_hub_url(raw) == expected


@pytest.mark.parametrize("raw", BAD_13)
def test_contract_13_non_canonical_or_rewritable_hosts_are_refused(raw: str) -> None:
    with pytest.raises(ValueError, match="Hub-adres"):
        validate_hub_url(raw)


def test_contract_13_bad_value_via_api_gives_422_and_keeps_old_value(client) -> None:
    ok = client.patch("/api/v1/settings/scoped", json={"changes": [
        {"scope_type": "global", "key": "system.hub_url", "value": "http://192.0.2.10:30443"}]})
    assert ok.status_code == 200
    for raw in ("http://127.1:8080", "http://0x7f000001", "http://127%2e0%2e0%2e1"):
        bad = client.patch("/api/v1/settings/scoped", json={"changes": [
            {"scope_type": "global", "key": "system.hub_url", "value": raw}]})
        assert bad.status_code == 422, raw
    eff = client.get("/api/v1/settings/effective?keys=system.hub_url").json()[0]
    assert eff["value"] == "http://192.0.2.10:30443"


@pytest.mark.parametrize(("raw", "expected"), GOOD)
def test_valid_addresses_are_normalised(raw: str, expected: str) -> None:
    assert validate_hub_url(raw) == expected


def test_empty_means_no_link() -> None:
    assert validate_hub_url("") == ""
    assert validate_hub_url("   ") == ""


@pytest.mark.parametrize("raw", BAD)
def test_invalid_addresses_are_refused(raw: str) -> None:
    with pytest.raises(ValueError, match="Hub-adres"):
        validate_hub_url(raw)


def test_setting_is_registered_empty_without_example(client) -> None:
    schema = client.get("/api/v1/settings/schema").json()
    hub = next(s for s in schema["settings"] if s["key"] == "system.hub_url")
    assert hub["type"] == "string" and hub["default"] == ""
    assert hub["restart_required"] is False and hub["category"] == "Systeem"
    assert hub["label_nl"] == "Hub-adres" and hub["label_en"] == "Hub address"
    eff = client.get("/api/v1/settings/effective?keys=system.hub_url").json()[0]
    assert eff["value"] == ""


def test_patch_stores_normalised_and_refuses_invalid_with_422(client) -> None:
    ok = client.patch("/api/v1/settings/scoped", json={"changes": [
        {"scope_type": "global", "key": "system.hub_url", "value": "HTTP://Hub.Example:30443/"}]})
    assert ok.status_code == 200
    eff = client.get("/api/v1/settings/effective?keys=system.hub_url").json()[0]
    assert eff["value"] == "http://hub.example:30443"
    for raw in ("http://127.0.0.1:8080", "javascript:alert(1)", "http://hub.example/pad"):
        bad = client.patch("/api/v1/settings/scoped", json={"changes": [
            {"scope_type": "global", "key": "system.hub_url", "value": raw}]})
        assert bad.status_code == 422
        assert HUB_URL_ERROR in bad.text
    # een geweigerde wijziging laat de bestaande waarde staan
    assert client.get("/api/v1/settings/effective?keys=system.hub_url").json()[0]["value"] == (
        "http://hub.example:30443")
    reset = client.patch("/api/v1/settings/scoped", json={"changes": [
        {"scope_type": "global", "key": "system.hub_url", "reset": True}]})
    assert reset.status_code == 200
    assert client.get("/api/v1/settings/effective?keys=system.hub_url").json()[0]["value"] == ""


def test_viewer_can_read_hub_address(client) -> None:
    client.post("/api/v1/auth/users", json={"username": "kijker", "password": "kijkwachtwoord1",
                                             "role": "viewer"})
    from fastapi.testclient import TestClient
    with TestClient(client.app) as other:
        other.post("/api/v1/auth/login", json={"username": "kijker", "password": "kijkwachtwoord1"})
        r = other.get("/api/v1/settings/effective?keys=system.hub_url")
        assert r.status_code == 200 and r.json()[0]["key"] == "system.hub_url"


def test_ui_theme_default_is_system(client) -> None:
    """Peil O-02 / ADR-0016 punt 5: standaardthema Systeem (de keuze per gebruiker blijft)."""
    eff = client.get("/api/v1/settings/effective?keys=ui.theme").json()[0]
    assert eff["value"] == "system"
