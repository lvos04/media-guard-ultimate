"""Een schemaproef controleert appdata en het gedrag van de bewaker."""

import argparse
import subprocess
from types import SimpleNamespace

import pytest
from test_upgradebewijs import ub


@pytest.mark.parametrize("herstartregel", ["", "[mediaguard] core hangt: wordt herstart"])
def test_schemaproef_meet_bewakingsrondes_en_herstartregels(tmp_path, monkeypatch, herstartregel):
    klok = [0.0]
    stops = []

    def docker(*args, **_):
        if args[0] == "run":
            return ""
        if args[0] == "inspect":
            return "running unhealthy 0"
        if args[0] == "exec":
            return '[503, {"status":"down","reason_code":"schema_nieuwer"}]'
        pytest.fail(args)

    monkeypatch.setattr(ub, "_docker", docker)
    monkeypatch.setattr(ub, "time", SimpleNamespace(
        monotonic=lambda: klok[0], sleep=lambda n: klok.__setitem__(0, klok[0] + n),
    ))
    poort = ub.Poort(argparse.Namespace(werkmap=tmp_path, poort=1, timeout=240))
    monkeypatch.setattr(poort, "stop", lambda: stops.append(klok[0]))
    monkeypatch.setattr(poort, "logs", lambda *, volledig: herstartregel if klok[0] >= 90 else "")
    if herstartregel:
        with pytest.raises(RuntimeError, match="herstartregels"):
            poort.schema_probe("synthetisch", "core")
        assert not stops
    else:
        state, _, restarts = poort.schema_probe("synthetisch", "core")
        assert state == "unhealthy" and restarts == 0
        assert stops and stops[0] >= 90 + 3 * 30


@pytest.mark.parametrize("herstelt", [True, False])
def test_schemaproef_meet_opnieuw_na_een_dockertimeout(tmp_path, monkeypatch, herstelt):
    klok = [0.0]
    onderbrekingen = []

    def docker(*args, **opties):
        if args[0] == "run":
            return ""
        if args[0] == "inspect":
            return "running unhealthy 0"
        assert args[0] == "exec"
        if klok[0] >= 60 and (not herstelt or not onderbrekingen):
            klok[0] += opties["timeout"]
            onderbrekingen.append(klok[0])
            raise subprocess.TimeoutExpired("docker exec", opties["timeout"])
        return '[503, {"status":"down","reason_code":"schema_nieuwer"}]'

    monkeypatch.setattr(ub, "_docker", docker)
    monkeypatch.setattr(ub, "time", SimpleNamespace(
        monotonic=lambda: klok[0], sleep=lambda n: klok.__setitem__(0, klok[0] + n),
    ))
    poort = ub.Poort(argparse.Namespace(werkmap=tmp_path, poort=1, timeout=300))
    monkeypatch.setattr(poort, "logs", lambda **_: "")
    monkeypatch.setattr(poort, "stop", lambda: None)

    if herstelt:
        health, body, restarts = poort.schema_probe("synthetisch", "core")
        assert (health, body["reason_code"], restarts) == ("unhealthy", "schema_nieuwer", 0)
        assert len(onderbrekingen) == 1 and klok[0] - onderbrekingen[0] >= 185
    else:
        with pytest.raises(RuntimeError, match="verwachte toestand unhealthy ontbreekt"):
            poort.schema_probe("synthetisch", "core")
        assert len(onderbrekingen) > 1 and klok[0] >= 300
