"""Eén gezondheidsmodel: topbalk, Overzicht en Activiteit tellen hetzelfde (rapport B2/H8)."""

from __future__ import annotations


def test_summary_counts_av1_failures_and_active(client, ctx):
    old = "2020-01-01T00:00:00+00:00"
    for n, (status, err) in enumerate([
            ("running", None), ("queued", None),
            ("awaiting_activation", "Activatie uitgesteld: EPERM"),
            ("rejected", "PermissionError: [Errno 1] Operation not permitted: '/x'"),
            ("failed", "Verificatie mislukt")]):
        ctx.db.execute(
            "INSERT INTO encoding_jobs(source_path,status,error,trial_only,policy_json,"
            "created_at,updated_at) VALUES (?,?,?,0,'{}',?,?)", (f"/m/{n}.mkv", status, err,
                                                                 old, old))
    h = client.get("/api/v1/health/summary").json()
    assert h["active"]["encodes"] == 1  # alleen echt bezig (rapport 3, R13)
    assert h["active"]["total"] == sum(h["active"][k] for k in
                                       ("downloads", "searches", "encodes", "tasks"))
    assert h["errors"]["av1_not_activated"] == 1
    assert h["errors"]["av1_failed"] == 2
    assert h["errors"]["total"] >= 3
    st = client.get("/api/v1/status").json()
    assert st["health"]["active"]["encodes"] == 1
    assert st["health"]["errors"]["av1_failed"] == 2
    att = client.get("/api/v1/attention").json()
    assert h["attention"]["errors"] <= att["errors"] + 1  # zelfde bronnen
    act = client.get("/api/v1/activity").json()
    assert act["health"]["errors"]["av1_failed"] == 2
