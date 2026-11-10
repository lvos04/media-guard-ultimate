"""Eenvoudige instellingen: weinig, begrijpelijk, en elke verwijzing bestaat echt."""

from __future__ import annotations


def test_simple_view_is_small_and_every_key_exists(client):
    from mediaguard.settings_curation import HIDDEN, SECTIONS, SIMPLE

    data = client.get("/api/v1/settings/schema").json()
    keys = {d["key"] for d in data["settings"]}
    assert set(SIMPLE) <= keys and set(HIDDEN) <= keys
    simple = [d for d in data["settings"] if d["simple"]]
    assert 15 <= len(simple) <= 30
    assert {s["id"] for s in data["sections"]} == {s[0] for s in SECTIONS}
    assert all(d["section"] in {s[0] for s in SECTIONS} for d in data["settings"])
    jargon = ("canary", "fail policy", "scope", "cutoff", "backfill", "dry-run")
    for d in simple:
        text = f"{d['label_nl']} {d['help_nl']}".lower()
        assert not any(j in text for j in jargon), d["key"]
    cpu = next(d for d in simple if d["key"] == "resources.av1_cpu_percent")
    assert cpu["section"] == "prestaties" and cpu["default"] == 75
    assert next(d for d in data["settings"]
                if d["key"] == "optimization.max_concurrent_encodes")["hidden"]
