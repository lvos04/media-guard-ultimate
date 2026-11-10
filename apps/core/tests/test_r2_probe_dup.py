"""Rapport 2 / N4, N5, M7: schrijftest in een titelmap, geen dubbele encodes, rustig wachten."""

import os
import stat

import pytest

from mediaguard.recovery import probe


def test_title_folder_is_probed(ctx, tmp_path):
    film = tmp_path / "Film (2020)"
    film.mkdir()
    (film / "Film (2020).mkv").write_bytes(b"x")
    res = probe._probe_title_folder(ctx, tmp_path, {"ok": True, "path": str(tmp_path)})
    assert res["ok"] and res["title_folder"] == str(film)


def test_sticky_foreign_owner_predicts_eperm(ctx, tmp_path, monkeypatch):
    film = tmp_path / "Film (2021)"
    film.mkdir()
    (film / "Film (2021).mkv").write_bytes(b"x")
    os.chmod(film, os.stat(film).st_mode | stat.S_ISVTX)
    monkeypatch.setattr(os, "geteuid", lambda: 12345)
    res = probe._probe_title_folder(ctx, tmp_path, {"ok": True, "path": str(tmp_path)})
    assert not res["ok"] and res["step"] == "replace" and "sticky" in res["error"]


@pytest.mark.asyncio
async def test_existing_result_blocks_second_encode(ctx):
    from mediaguard.encoding import service

    ctx.db.execute(
        "INSERT INTO encoding_jobs(source_path,trial_only,status,policy_json,created_at,"
        "created_by,updated_at) VALUES ('/m/hp.mkv',0,'awaiting_activation','{}',"
        "datetime('now'),'t',datetime('now'))")
    with pytest.raises(service.GateError) as exc:
        await service.submit(ctx, path="/m/hp.mkv")
    assert exc.value.code == "DUPLICATE"


def test_plan_waiting_is_quiet_dutch():
    from mediaguard.optimization.runner import waiting_nl

    assert "vanzelf verder" in waiting_nl("max actieve downloads bereikt")


def test_health_errors_drilldown_lists_exact_items(client, ctx):
    ctx.db.execute(
        "INSERT INTO encoding_jobs(source_path,trial_only,status,policy_json,error,created_at,"
        "created_by,updated_at) VALUES ('/m/HP7.mkv',0,'awaiting_activation','{}',"
        "'PermissionError (stap vervangen)',datetime('now'),'t',datetime('now'))")
    r = client.get("/api/v1/health/errors").json()
    g = next(g for g in r["groups"] if g["key"] == "av1_not_activated")
    assert g["count"] == 1 and g["items"][0]["title"] == "HP7.mkv"
    assert sum(x["count"] for x in r["groups"]) == r["total"]
    s = client.get("/api/v1/health/summary").json()
    assert s["av1"]["failed_total"] >= 1


def test_wanted_blocked_parts_sum(client):
    k = client.get("/api/v1/wanted/kpis").json()
    b = k["blocked"]
    assert b["total"] == b["import_failed"] + b["client_error"] + b["unavailable"] + b["ignored"]
    assert k["missing_total"] == k["missing"]
