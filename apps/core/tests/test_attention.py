"""'Wacht op jou': verborgen wachttoestanden komen op één plek, met uitleg en actie."""

from __future__ import annotations

import json

from mediaguard.db import utcnow
from mediaguard.instances.registry import seed_instances


def _job(ctx, status, path, *, size=10_000, staged=4_000, error=None):
    now = utcnow()
    return ctx.db.execute_insert(
        "INSERT INTO encoding_jobs(source_path,status,policy_json,source_snapshot_json,"
        "staged_output_json,error,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)",
        (path, status, "{}", json.dumps({"size": size}), json.dumps({"size": staged}), error,
         now, now))


def _keys(client):
    r = client.get("/api/v1/attention")
    assert r.status_code == 200, r.text
    return {i["key"]: i for i in r.json()["items"]}


def test_attention_collects_hidden_waiting_states(client, ctx, monkeypatch):
    monkeypatch.setenv("MG_ATTENTION_SKIP_ENCODER", "1")
    seed_instances(ctx)
    # zonder wachttoestanden alleen eventuele recovery-meldingen (geen heartbeat in tests) en
    # de herinnering om tweestapsverificatie aan te zetten (beheerder zonder 2FA)
    assert all(k.startswith(("recovery", "library", "backup", "security.twofa_off"))
               for k in _keys(client))
    # praktijk: drie omgezette bestanden zonder zichtbare 'accepteren'-knop
    for n in range(3):
        _job(ctx, "awaiting_activation", f"/data/media/tv/S/E{n}.mkv")
    _job(ctx, "conflict_review", "/data/media/tv/S/C.mkv", error="Bron gewijzigd")
    now = utcnow()
    ctx.db.execute("INSERT INTO anime_routing_actions(source_instance,kind,provider_id,title,"
                   "status,created_at,updated_at) VALUES ('sonarr-series','series',1,'Frieren',"
                   "'needs_review',?,?)", (now, now))
    ctx.db.execute("UPDATE instances SET suspended=1 WHERE id='radarr-films'")
    ctx.db.execute("UPDATE instances SET shadow_mode=1 WHERE id='sonarr-anime'")
    txn = ctx.db.execute_insert(
        "INSERT INTO replacement_transactions(kind,library_path,final_path,staged_path,"
        "source_snapshot_json,state,created_at,updated_at) VALUES ('replace','/a','/a','/s',"
        "'{}','committed',?,?)", (now, now))
    item = ctx.db.execute_insert(
        "INSERT INTO quarantine_items(transaction_id,original_path,quarantine_path,method,"
        "status,created_at) VALUES (?,?,?,?,?,?)",
        (txn, "/data/media/tv/S/old.mkv", "/work/quarantine/old.mkv", "move", "restorable",
         now))
    ctx.db.execute("INSERT INTO quarantine_release_checks(item_id,status,checks_json,"
                   "checked_at,attempts) VALUES (?,'failed',?,?,5)",
                   (item, json.dumps([{"name": "duur", "passed": False,
                                       "detail": "duur wijkt af"}]), now))
    items = _keys(client)
    act = items["encoding.awaiting_activation"]
    assert act["count"] == 3 and "GB" in act["detail_nl"]
    assert act["rows"][0]["actions"][0]["path"].endswith("/activate")
    assert items["encoding.conflict"]["severity"] == "error"
    # 'anime of niet?' vraagt de gebruiker niets meer: de leveringsmotor laat de AI beslissen
    assert "routing.review" not in items
    ctx.config.set("global", "", "delivery.enabled", False, "test")
    assert _keys(client)["routing.review"]["rows"][0]["actions"][0]["path"] == \
        "/routing/anime/actions/1/approve"
    ctx.config.set("global", "", "delivery.enabled", True, "test")
    assert items["instance.suspended.radarr-films"]["actions"][0]["path"] == \
        "/instances/radarr-films/resume"
    assert "instance.shadow.sonarr-anime" in items
    q = items["quarantine.check_failed"]
    assert q["count"] == 1 and "duur wijkt af" in q["rows"][0]["detail"]
    # bronnen staan op ernst: fouten eerst
    order = [i["severity"] for i in client.get("/api/v1/attention").json()["items"]]
    assert order == sorted(order, key={"error": 0, "warning": 1, "info": 2}.get)


def test_attention_actions_work_end_to_end(client, ctx, monkeypatch):
    """De knop uit de lijst doet echt wat hij belooft (hier: instantie hervatten)."""
    monkeypatch.setenv("MG_ATTENTION_SKIP_ENCODER", "1")
    seed_instances(ctx)
    ctx.db.execute("UPDATE instances SET suspended=1 WHERE id='radarr-films'")
    action = _keys(client)["instance.suspended.radarr-films"]["actions"][0]
    r = client.post("/api/v1" + action["path"], json=action["body"])
    assert r.status_code == 200, r.text
    assert "instance.suspended.radarr-films" not in _keys(client)


def test_failed_release_check_retried_weekly_after_five_attempts(ctx):
    from mediaguard.recovery.release_check import due_for_check

    now = utcnow()
    txn = ctx.db.execute_insert(
        "INSERT INTO replacement_transactions(kind,library_path,final_path,staged_path,"
        "source_snapshot_json,state,created_at,updated_at) VALUES ('replace','/a','/a','/s',"
        "'{}','committed',?,?)", (now, now))
    item = ctx.db.execute_insert(
        "INSERT INTO quarantine_items(transaction_id,original_path,quarantine_path,method,"
        "status,created_at) VALUES (?,'/o','/q','move','restorable',?)", (txn, now))
    ctx.db.execute("INSERT INTO quarantine_release_checks(item_id,status,checks_json,"
                   "checked_at,attempts) VALUES (?,'failed','[]',datetime('now','-3 days'),5)",
                   (item,))
    assert item not in due_for_check(ctx)
    ctx.db.execute("UPDATE quarantine_release_checks SET checked_at=datetime('now','-8 days')")
    assert item in due_for_check(ctx)
