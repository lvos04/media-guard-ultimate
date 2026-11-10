"""Seerr moet de instanties via het LAN-adres bereiken, nooit via 127.0.0.1 van de AIO-container."""

from mediaguard.seerr.service import _expected_url


def test_loopback_public_url_uses_lan_host(monkeypatch):
    monkeypatch.setenv("MG_PUBLIC_HOST", "192.0.2.10")
    inst = {"id": "radarr-films", "url": "http://127.0.0.1:37878",
            "public_url": "http://127.0.0.1:37878"}
    url = _expected_url(inst)
    assert url.startswith("http://192.0.2.10:")


def test_explicit_lan_public_url_is_kept(monkeypatch):
    monkeypatch.setenv("MG_PUBLIC_HOST", "192.0.2.10")
    inst = {"id": "sonarr-series", "url": "http://127.0.0.1:38989",
            "public_url": "http://10.0.0.5:30989/"}
    assert _expected_url(inst) == "http://10.0.0.5:30989"


def test_non_loopback_internal_url_kept_without_lan(monkeypatch):
    monkeypatch.delenv("MG_PUBLIC_HOST", raising=False)
    inst = {"id": "sonarr-series", "url": "http://sonarr:8989", "public_url": None}
    assert _expected_url(inst) == "http://sonarr:8989"
