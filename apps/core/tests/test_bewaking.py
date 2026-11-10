"""Bewaking zonder omkijken (verbeterlijst 1, 8, 9 en 37).

* 1: een achtergrondronde pusht problemen één keer en lost ze vanzelf op; eigen
  stilstandmeldingen voor aanvragen, de noodstop, de Seerr-sync en AV1-banen.
* 8: kijken, een eigen pauze, stille uren en de noodstop zijn geen fout; 'Bewust zo gekozen'
  verbergt een punt tot de instelling verandert.
* 9: een achtergrondtaak overleeft een onverwachte fout; per taak de laatste geslaagde ronde
  en een melding als een taak te lang niets deed.
* 37: een TrueNAS-pool die niet gezond is geeft een melding en pauzeert het opruimen van
  originelen.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from mediaguard import attention, monitor, taskwatch
from mediaguard.db import utcnow


def _ago(**kw: float) -> str:
    return (datetime.now(UTC) - timedelta(**kw)).isoformat(timespec="seconds")


def _channel(ctx) -> int:
    """Een actief pushkanaal, en een recovery-worker die leeft (anders is 'vervangen staat
    stil' in elke test de eerste push)."""
    from mediaguard.notify import service
    from mediaguard.recovery.service import mark_heartbeat

    mark_heartbeat(ctx)
    ctx.services["notify_channels_ready"] = True
    ch = service.create_channel(ctx, "ntfy", "Telefoon", {"server": "https://ntfy.example",
                                                          "topic": "mg-test"}, {})
    return int(ch["id"])


def _outbox(ctx) -> list[dict]:
    return ctx.db.fetch_dicts("SELECT title, category, priority FROM notify_outbox ORDER BY id")


def _conflict(ctx, path: str = "/m/a.mkv") -> int:
    now = utcnow()
    return ctx.db.execute_insert(
        "INSERT INTO encoding_jobs(source_path,status,policy_json,error,created_at,updated_at) "
        "VALUES (?,?,?,?,?,?)", (path, "conflict_review", "{}", "Bron gewijzigd", now, now))


# -- 9: taken die nooit stil stoppen -------------------------------------------------------------
def test_spawn_restarts_a_loop_that_crashes_outside_its_try(ctx, monkeypatch):
    monkeypatch.setattr(taskwatch, "BACKOFF_SECONDS", (0,))
    calls: list[int] = []
    done = asyncio.Event()

    async def loop(c):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("instelling onleesbaar")  # buiten het eigen try-blok
        taskwatch.ok(c, "proef")
        done.set()
        await asyncio.sleep(3600)

    async def main():
        task = taskwatch.spawn(ctx, "proef", loop, label_nl="Proeftaak", every=60)
        await asyncio.wait_for(done.wait(), 5)
        await taskwatch.stop(ctx, task)
        return task

    task = asyncio.run(main())
    assert task.cancelled() and len(calls) == 2
    row = ctx.state_get("taskwatch:proef")
    assert row["restarts"] == 1 and "instelling onleesbaar" in row["last_error"]
    assert row["last_ok_at"] and row["failing_since"] is None
    status = {t["name"]: t for t in taskwatch.status(ctx)}
    assert status["proef"]["stale"] is False and status["proef"]["quiet_minutes"] == 30


def test_quiet_task_becomes_a_pushed_attention_item(ctx):
    _channel(ctx)
    taskwatch.register(ctx, "delivery", "Aanvragen leveren", every=600)
    row = ctx.state_get("taskwatch:delivery")
    row.update(started_at=_ago(hours=3), last_ok_at=_ago(hours=2))
    ctx.state_set("taskwatch:delivery", row)
    taskwatch.fail(ctx, "delivery", "ArrUnavailable: Sonarr reageert niet")
    items = {i["key"]: i for i in attention.collect_local(ctx)}
    stale = items["tasks.stale"]
    assert stale["push"] and stale["rows"][0]["title"] == "Aanvragen leveren"
    assert "Sonarr reageert niet" in stale["rows"][0]["detail"]
    monitor.sync_alerts(ctx, monitor._alerts(list(items.values()), None))
    assert [o["title"] for o in _outbox(ctx)] == ["1 achtergrondtaak doet al lang niets"]
    taskwatch.ok(ctx, "delivery")  # weer een geslaagde ronde: punt weg
    assert "tasks.stale" not in {i["key"] for i in attention.collect_local(ctx)}


def test_recovery_rows_from_an_old_process_never_alarm(ctx):
    old = {"label_nl": "Quarantaine-onderhoud", "role": "recovery", "pid": 111,
           "quiet_s": 1800, "started_at": _ago(days=2), "last_ok_at": _ago(days=2)}
    ctx.state_set("taskwatch:quarantine_maintenance", old)
    ctx.state_set("recovery_heartbeat", {"ts": utcnow(), "pid": 222})
    assert not taskwatch.stale(ctx)  # rij van een vorige start
    ctx.state_set("recovery_heartbeat", {"ts": utcnow(), "pid": 111})
    assert [t["name"] for t in taskwatch.stale(ctx)] == ["quarantine_maintenance"]


def test_seerr_loop_records_failure_and_keeps_running(ctx, monkeypatch):
    from mediaguard.seerr import api as seerr_api
    from mediaguard.seerr.client import SECRET_NAME, SeerrError

    ctx.config.set("global", "", "seerr.url", "http://seerr.local:5055", "test")
    ctx.secrets.set(SECRET_NAME, "sleutel", actor="test")

    async def broken(_ctx):
        raise SeerrError("Seerr niet bereikbaar")

    sleeps: list[float] = []

    class Stop(Exception):
        pass

    async def fake_sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) > 2:
            raise Stop

    monkeypatch.setattr(seerr_api.service, "sync_requests", broken)
    monkeypatch.setattr(seerr_api.asyncio, "sleep", fake_sleep)
    with pytest.raises(Stop):
        asyncio.run(seerr_api._loop(ctx))
    assert len(sleeps) == 3  # twee mislukte rondes, de lus liep door
    row = ctx.state_get("taskwatch:seerr_sync")
    assert row["fails"] == 2 and "niet bereikbaar" in row["last_error"]
    assert not monitor.seerr_item(ctx)  # pas na 30 minuten een melding
    row["failing_since"] = _ago(minutes=45)
    ctx.state_set("taskwatch:seerr_sync", row)
    item = monitor.seerr_item(ctx)[0]
    assert item["key"] == "seerr.sync_failed" and item["push"]
    assert "niet bereikbaar" in item["detail_nl"]
    taskwatch.ok(ctx, "seerr_sync")
    assert not monitor.seerr_item(ctx)


# -- 1: pushen, één keer, en vanzelf oplossen ----------------------------------------------------
def test_monitor_pushes_errors_once_and_resolves_them(ctx, monkeypatch):
    monkeypatch.setenv("MG_ATTENTION_SKIP_ENCODER", "1")
    _channel(ctx)
    job = _conflict(ctx)

    def round_():
        items = attention.collect_local(ctx)
        return monitor.sync_alerts(ctx, monitor._alerts(items, None))

    assert "encoding.conflict" in round_()["pushed"]
    out = _outbox(ctx)
    assert len(out) == 1 and out[0]["category"] == "errors" and out[0]["priority"] == "high"
    round_()
    assert len(_outbox(ctx)) == 1  # zelfde probleem: geen tweede push
    _conflict(ctx, "/m/b.mkv")  # er komt iets nieuws bij: één nieuwe melding (samengevoegd)
    round_()
    n = ctx.db.fetch_one("SELECT count FROM notifications WHERE dedupe_key=?",
                         ("monitor.encoding.conflict",))["count"]
    assert n == 2
    ctx.db.execute("UPDATE encoding_jobs SET status='failed'")
    assert round_()["resolved"] == []  # één ronde weg is nog niet opgelost
    assert round_()["resolved"] == ["encoding.conflict"]
    row = ctx.db.fetch_one("SELECT read, resolved_at FROM notifications WHERE dedupe_key=?",
                           ("monitor.encoding.conflict",))
    assert row["read"] == 1 and row["resolved_at"]
    # binnen het uur terug (flapperen): zichtbaar in de app, maar geen nieuwe push
    ctx.db.execute("UPDATE encoding_jobs SET status='conflict_review' WHERE id=?", (job,))
    before = len(_outbox(ctx))
    round_()
    assert len(_outbox(ctx)) == before


def test_monitor_round_runs_without_the_app(ctx, monkeypatch):
    from mediaguard import health_summary

    monkeypatch.setenv("MG_ATTENTION_SKIP_ENCODER", "1")
    _channel(ctx)
    _conflict(ctx)

    async def fake_build(_ctx):
        return {"encoder": {"state": "ok", "reasons": []}}

    monkeypatch.setattr(health_summary, "build", fake_build)
    result = asyncio.run(monitor.run_once(ctx))
    assert result["errors"] >= 1 and "encoding.conflict" in result["pushed"]
    ctx.config.set("global", "", "monitor.enabled", False, "test")
    assert asyncio.run(monitor.run_once(ctx)) == {"skipped": "uit"}


def test_encoder_degraded_pushed_only_after_30_minutes(ctx):
    _channel(ctx)
    health = {"encoder": {"state": "degraded", "reasons": ["CPU-baan: automatisch: opslag"]}}
    now = datetime.now(UTC)
    monitor.sync_alerts(ctx, monitor._alerts([], health), now=now)
    assert not _outbox(ctx)
    monitor.sync_alerts(ctx, monitor._alerts([], health), now=now + timedelta(minutes=31))
    assert [o["title"] for o in _outbox(ctx)] == ["AV1-engine werkt beperkt"]


def test_forgotten_emergency_stop_alert(client, ctx):
    ctx.state_set("emergency_stop", {"active": True, "reason": "onderhoud", "since": _ago(hours=1)})
    assert not monitor.emergency_stop_item(ctx)
    ctx.state_set("emergency_stop", {"active": True, "reason": "onderhoud", "since": _ago(hours=3)})
    item = monitor.emergency_stop_item(ctx)[0]
    assert item["push"] and item["title_nl"].startswith("Noodstop staat al 3 u")
    lift = item["actions"][0]
    assert lift["path"] == "/system/emergency-stop" and lift["body"] == {"active": False}
    r = client.post("/api/v1" + lift["path"], json=lift["body"])
    assert r.status_code == 200, r.text
    assert not monitor.emergency_stop_item(ctx)


def _request(ctx, stage: str, *, hours: float, title: str = "Wadi", state: str = "active") -> int:
    now = utcnow()
    item = ctx.db.execute_insert(
        "INSERT INTO delivery_items(item_key,source,media_type,tmdb_id,title,stage,state,"
        "progress_json,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (f"movie:tmdb:{abs(hash((title, stage))) % 10**6}", "seerr", "movie", 1, title, stage,
         state, json.dumps({"have": 0, "missing": 1, "total": 1}), now, now))
    ctx.db.execute("INSERT INTO delivery_events(item_id,ts,stage,action,actor,detail_nl) "
                   "VALUES (?,?,?,?,?,?)", (item, _ago(hours=hours + 1), "requested",
                                            "aangevraagd", "t", "Aangevraagd"))
    ctx.db.execute("INSERT INTO delivery_events(item_id,ts,stage,action,actor,detail_nl) "
                   "VALUES (?,?,?,?,?,?)", (item, _ago(hours=hours), stage, "stand", "t", stage))
    return item


def test_stalled_requests(ctx):
    _request(ctx, "adding", hours=13, title="Toevoegen")
    _request(ctx, "adding", hours=2, title="Net aangevraagd")
    _request(ctx, "searching", hours=200, title="Zoekt nog")  # zoeken telt niet
    _request(ctx, "downloading", hours=13, title="Downloadt")  # downloaden: 24 u
    _request(ctx, "downloading", hours=30, title="Download hangt")
    titles = {s["title"] for s in monitor.stalled_requests(ctx)}
    assert titles == {"Toevoegen", "Download hangt"}
    item = monitor.requests_item(ctx)[0]
    assert item["push"] and item["count"] == 2 and item["actions"][0]["path"] == "/delivery/run"
    assert any("Wordt toegevoegd" in r["detail"] for r in item["rows"])
    # voortgang (een aflevering binnen) zet de klok terug
    monitor.update_request_progress(ctx)
    ctx.db.execute("UPDATE delivery_items SET progress_json=? WHERE title='Download hangt'",
                   (json.dumps({"have": 1, "missing": 0, "total": 1}),))
    monitor.update_request_progress(ctx)
    assert {s["title"] for s in monitor.stalled_requests(ctx)} == {"Toevoegen"}
    # noodstop of leveringsmotor uit: alles staat bewust stil, en na het opheffen begint de
    # klok opnieuw (geen melding over aanvragen die bewust stillagen)
    ctx.state_set("emergency_stop", {"active": True, "since": utcnow()})
    assert monitor.stalled_requests(ctx) == []
    monitor.update_request_progress(ctx)
    ctx.state_set("emergency_stop", {"active": False})
    assert monitor.stalled_requests(ctx) == []
    ctx.config.set("global", "", "delivery.enabled", False, "test")
    assert monitor.stalled_requests(ctx) == []


# -- 8: bewuste keuzes zijn geen fout -------------------------------------------------------------
class _Engine:
    def __init__(self, health):
        self._health = health

    async def health(self):
        return self._health


def _encoder_items(ctx, monkeypatch, health):
    from mediaguard.encoding import client as enc_client

    monkeypatch.setattr(enc_client, "get_encoder_client", lambda _ctx: _Engine(health))
    return {i["key"]: i for i in asyncio.run(attention._encoder(ctx))}


def test_watching_quiet_hours_and_own_pause_are_not_errors(ctx, monkeypatch):
    stall = {"baan": "gpu", "minuten": 120, "wachtend": 4, "sinds": _ago(hours=2)}
    items = _encoder_items(ctx, monkeypatch, {
        "lanes_paused": [{"lane": "gpu", "reason": "Kijken: iemand kijkt (Wadi)"}],
        "baan_stilstand": [{**stall, "reden": "Kijken: iemand kijkt (Wadi)"},
                           {**stall, "baan": "cpu", "reden": "stiltevenster actief"}]})
    assert items == {}
    items = _encoder_items(ctx, monkeypatch, {
        "lanes_paused": [{"lane": "cpu", "reason": "handmatig gepauzeerd"}],
        "baan_stilstand": [{**stall, "baan": "cpu", "reden": "handmatig gepauzeerd"},
                           {**stall, "reden": "noodstop actief: geen nieuwe encodes"}]})
    own = items.pop("encoder.paused.cpu")
    assert items == {} and own["severity"] == "info"
    assert own["actions"][0]["label_nl"] == "Bewust zo gekozen"
    # een echte storing blijft wel een (gepushte) melding
    items = _encoder_items(ctx, monkeypatch, {
        "lanes_paused": [], "baan_stilstand": [{**stall, "reden": "wacht op werkgeheugen"}]})
    assert items["encoder.idle.gpu"]["push"] is True
    ctx.config.set("global", "", "monitor.lane_idle_minutes", 180, "test")
    assert "encoder.idle.gpu" not in _encoder_items(ctx, monkeypatch, {
        "lanes_paused": [], "baan_stilstand": [{**stall, "reden": "wacht op werkgeheugen"}]})


def test_health_not_red_while_someone_watches():
    from mediaguard.health_summary import encoder_state_from_health

    watching = encoder_state_from_health({
        "status": "degraded", "lanes_paused": [{"lane": "cpu", "reason": "Kijken: iemand kijkt"},
                                               {"lane": "gpu", "reason": "handmatig gepauzeerd"}],
        "reasons": ["cpu-baan gepauzeerd: Kijken: iemand kijkt",
                    "gpu-baan gepauzeerd: handmatig gepauzeerd"]})
    assert watching["state"] == "ok" and watching["lanes_paused"] == 0
    assert len(watching["waiting"]) == 2
    broken = encoder_state_from_health({
        "status": "degraded",
        "lanes_paused": [{"lane": "cpu", "reason": "automatisch: opslag niet bereikbaar"}],
        "reasons": ["cpu-baan gepauzeerd: automatisch: opslag niet bereikbaar"]})
    assert broken["state"] == "degraded" and broken["lanes_paused"] == 1
    # CPU-only: geen GPU is geen storing
    cpu_only = encoder_state_from_health({
        "status": "ok", "notes": ["gpu-baan uit: geen GPU gevonden"],
        "lanes_paused": [{"lane": "gpu", "reason": "MediaGuard: Geen GPU gevonden"}],
        "reasons": []})
    assert cpu_only["state"] == "ok"


def test_overview_errors_ignore_watching_pause(client, ctx, monkeypatch):
    from mediaguard.encoding import client as enc_client

    monkeypatch.setattr(enc_client, "get_encoder_client", lambda _ctx: _Engine({
        "status": "degraded", "lanes_paused": [{"lane": "cpu", "reason": "Kijken: iemand"}],
        "reasons": ["cpu-baan gepauzeerd: Kijken: iemand"], "baan_stilstand": []}))
    h = client.get("/api/v1/health/summary").json()
    assert h["encoder"]["state"] == "ok" and h["errors"]["encoder_degraded"] == 0
    assert not any(i["key"].startswith("encoder.") for i in
                   client.get("/api/v1/attention").json()["items"])


def _keys(client) -> dict[str, dict]:
    r = client.get("/api/v1/attention")
    assert r.status_code == 200, r.text
    return {i["key"]: i for i in r.json()["items"]}


def test_dismiss_deliberate_choice_until_setting_changes(client, ctx, monkeypatch):
    monkeypatch.setenv("MG_ATTENTION_SKIP_ENCODER", "1")
    ctx.config.set("global", "", "quarantine.mode", "immediate_after_check", "test")
    ctx.config.set("global", "", "dv.allow_av1", True, "test")
    unsafe = _keys(client)["settings.unsafe"]
    assert unsafe["count"] == 2
    # één instelling bewust zo gekozen: alleen die verdwijnt
    row = next(r for r in unsafe["rows"] if r["id"] == "dv.allow_av1")
    act = next(a for a in row["actions"] if a["label_nl"] == "Bewust zo gekozen")
    assert act["confirm_nl"] and act["role"] == "admin"
    r = client.post("/api/v1" + act["path"], json=act["body"])
    assert r.status_code == 200, r.text
    after = _keys(client)
    assert after["settings.unsafe"]["count"] == 1
    assert client.get("/api/v1/attention").json()["dismissed"] == 1
    listed = client.get("/api/v1/attention/dismissed").json()["items"]
    assert listed[0]["key"] == "settings.unsafe.dv.allow_av1" and listed[0]["by"]
    # instelling terug naar standaard en weer aan: de keuze is vervallen, het punt is terug
    ctx.config.set("global", "", "dv.allow_av1", False, "test")
    _keys(client)
    ctx.config.set("global", "", "dv.allow_av1", True, "test")
    assert _keys(client)["settings.unsafe"]["count"] == 2
    # alles in één keer verbergen en weer tonen
    act = next(a for a in _keys(client)["settings.unsafe"]["actions"]
               if a["label_nl"] == "Bewust zo gekozen")
    assert client.post("/api/v1" + act["path"], json=act["body"]).status_code == 200
    assert "settings.unsafe" not in _keys(client)
    r = client.post("/api/v1/attention/dismissed/restore", json={})
    assert r.json()["restored"] == 2 and "settings.unsafe" in _keys(client)
    audit = {a["action"] for a in client.get("/api/v1/audit").json()}
    assert {"attention.dismissed", "attention.restored"} <= audit


def test_real_errors_cannot_be_dismissed(client):
    r = client.post("/api/v1/attention/dismiss",
                    json={"entries": [{"key": "quarantine.full", "fingerprint": "1"}]})
    assert r.status_code == 422


def test_only_admin_hides_a_safety_warning(client, app):
    """De app-sleutel (operator) mag een eigen pauze verbergen, geen veiligheidswaarschuwing."""
    from fastapi.testclient import TestClient

    key = client.post("/api/v1/auth/api-keys",
                      json={"name": "telefoon", "role": "operator"}).json()["key"]
    with TestClient(app) as machine:
        h = {"X-Api-Key": key}
        unsafe = {"entries": [{"key": "settings.unsafe.quarantine.mode", "fingerprint": "x"}]}
        assert machine.post("/api/v1/attention/dismiss", headers=h,
                            json=unsafe).status_code == 403
        own = {"entries": [{"key": "encoder.paused.cpu", "fingerprint": "handmatig"}]}
        assert machine.post("/api/v1/attention/dismiss", headers=h,
                            json=own).status_code == 200


def test_dismissed_instance_pause_returns_when_paused_again(client, ctx, monkeypatch):
    monkeypatch.setenv("MG_ATTENTION_SKIP_ENCODER", "1")
    ctx.db.execute("UPDATE instances SET suspended=1 WHERE id='radarr-films'")
    item = _keys(client)["instance.suspended.radarr-films"]
    act = next(a for a in item["actions"] if a["label_nl"] == "Bewust zo gekozen")
    assert client.post("/api/v1" + act["path"], json=act["body"]).status_code == 200
    assert "instance.suspended.radarr-films" not in _keys(client)
    ctx.db.execute("UPDATE instances SET suspended=0 WHERE id='radarr-films'")
    _keys(client)  # hervat: de keuze vervalt
    ctx.db.execute("UPDATE instances SET suspended=1 WHERE id='radarr-films'")
    assert "instance.suspended.radarr-films" in _keys(client)


# -- 37: TrueNAS-pool ----------------------------------------------------------------------------
def _truenas(ctx, pools):
    ctx.config.set("global", "", "recovery.truenas_url", "https://truenas.local", "test")
    ctx.secrets.set("truenas:apikey", "sleutel", actor="test")

    def handler(request: httpx.Request) -> httpx.Response:
        if pools is None:
            raise httpx.ConnectError("weg", request=request)
        return httpx.Response(200, json=pools)

    ctx.services["truenas_transport"] = httpx.MockTransport(handler)


def test_degraded_pool_alerts_and_pauses_purge(client, ctx, monkeypatch):
    from mediaguard import jobs, storage_forecast
    from mediaguard.recovery import api as recovery_api

    monkeypatch.setenv("MG_ATTENTION_SKIP_ENCODER", "1")
    _channel(ctx)
    _truenas(ctx, [{"name": "tank", "status": "DEGRADED", "healthy": False, "scan": {}},
                   {"name": "boot", "status": "ONLINE", "healthy": True, "scan": {}}])
    asyncio.run(storage_forecast.refresh_pool(ctx))
    item = _keys(client)["storage.pool_degraded"]
    assert item["severity"] == "error" and "tank (DEGRADED)" in item["title_nl"]
    monitor.sync_alerts(ctx, monitor._alerts(list(_keys(client).values()), None))
    assert any("TrueNAS-pool" in o["title"] for o in _outbox(ctx))
    # opruimen pauzeert: geen enkel origineel weg
    deleted: list[int] = []

    async def passed(_ctx, _item_id):
        return {"status": "passed", "checks": []}

    monkeypatch.setattr(recovery_api.service, "purge_candidates",
                        lambda _ctx: [{"id": 7, "purge_reason": "bewaartermijn", "size": 1}])
    monkeypatch.setattr(recovery_api.release_check, "run_check_async", passed)
    monkeypatch.setattr(recovery_api, "_delete_item_file",
                        lambda _ctx, item, _reason: deleted.append(item["id"]))
    job_id = jobs.enqueue(ctx.db, "quarantine.purge", {"manual": True})
    jc = jobs.JobContext(job_id=job_id, job_type="quarantine.purge", payload={"manual": True},
                         instance_id=None, correlation_id=None, app=ctx)
    result = asyncio.run(recovery_api._job_purge(jc))
    assert result["paused"] and result["purged"] == 0 and deleted == []
    r = client.post("/api/v1/quarantine/purge", json={"confirm": True})
    assert r.status_code == 409 and "TrueNAS-pool" in r.json()["detail"]
    # API even onbereikbaar: de pauze blijft (nooit opheffen op een onbekende stand)
    _truenas(ctx, None)
    asyncio.run(storage_forecast.refresh_pool(ctx))
    assert storage_forecast.pool_degraded(ctx)
    # pool weer gezond: melding weg en opruimen gaat verder
    _truenas(ctx, [{"name": "tank", "status": "ONLINE", "healthy": True, "scan": {}}])
    asyncio.run(storage_forecast.refresh_pool(ctx))
    assert "storage.pool_degraded" not in _keys(client)
    assert asyncio.run(recovery_api._job_purge(jc))["purged"] == 1 and deleted == [7]


def test_pool_not_configured_means_no_pause(ctx):
    from mediaguard import storage_forecast
    from mediaguard.recovery.api import purge_paused_reason

    asyncio.run(storage_forecast.refresh_pool(ctx))
    assert storage_forecast.pool_degraded(ctx) == [] and purge_paused_reason(ctx) is None


def test_restart_blips_are_not_pushed_right_away(ctx):
    """Een engine of recovery-worker die even herstart (update) geeft pas na 10 min een push."""
    _channel(ctx)
    item = {"key": "encoder.offline", "severity": "error", "title_nl": "AV1-engine reageert niet",
            "detail_nl": "", "href": "/av1", "rows": []}
    now = datetime.now(UTC)
    monitor.sync_alerts(ctx, monitor._alerts([item], None), now=now)
    monitor.sync_alerts(ctx, [], now=now + timedelta(minutes=5))
    monitor.sync_alerts(ctx, [], now=now + timedelta(minutes=10))  # weer weg: nooit gepusht
    assert not _outbox(ctx)
    monitor.sync_alerts(ctx, monitor._alerts([item], None), now=now + timedelta(minutes=20))
    monitor.sync_alerts(ctx, monitor._alerts([item], None), now=now + timedelta(minutes=31))
    assert [o["title"] for o in _outbox(ctx)] == ["AV1-engine reageert niet"]


# -- controle (checker): randgevallen ------------------------------------------------------------
def test_flapping_problem_that_stays_is_pushed_after_the_cooldown(ctx):
    """Komt een opgelost probleem binnen het uur terug, dan eerst geen push; blijft het staan,
    dan volgt er wel één zodra het uur om is (nooit stil vergeten)."""
    _channel(ctx)
    item = {"key": "quarantine.full", "severity": "error", "title_nl": "Quarantaine vol",
            "detail_nl": "", "href": "/quarantaine", "rows": []}
    now = datetime.now(UTC)
    alerts = monitor._alerts([item], None)
    monitor.sync_alerts(ctx, alerts, now=now)
    monitor.sync_alerts(ctx, [], now=now + timedelta(minutes=5))
    monitor.sync_alerts(ctx, [], now=now + timedelta(minutes=10))  # opgelost
    assert len(_outbox(ctx)) == 1
    monitor.sync_alerts(ctx, alerts, now=now + timedelta(minutes=20))  # flappert terug
    monitor.sync_alerts(ctx, alerts, now=now + timedelta(minutes=45))
    assert len(_outbox(ctx)) == 1
    monitor.sync_alerts(ctx, alerts, now=now + timedelta(minutes=75))  # blijft: na het uur
    assert len(_outbox(ctx)) == 2
    monitor.sync_alerts(ctx, alerts, now=now + timedelta(minutes=80))
    assert len(_outbox(ctx)) == 2


def test_single_seerr_failure_is_not_an_alert(ctx):
    taskwatch.register(ctx, "seerr_sync", "Seerr-aanvragen ophalen", every=1800)
    taskwatch.fail(ctx, "seerr_sync", "SeerrError: time-out")
    row = ctx.state_get("taskwatch:seerr_sync")
    row["failing_since"] = _ago(minutes=45)  # één misser, de volgende ronde moet nog komen
    ctx.state_set("taskwatch:seerr_sync", row)
    assert not monitor.seerr_item(ctx)
    taskwatch.fail(ctx, "seerr_sync", "SeerrError: time-out")
    assert monitor.seerr_item(ctx)[0]["key"] == "seerr.sync_failed"


def test_every_stuck_request_counts_as_new_even_beyond_the_shown_rows(ctx):
    for n in range(attention.MAX_ROWS + 2):
        _request(ctx, "adding", hours=13, title=f"Titel {n}")
    item = monitor.requests_item(ctx)[0]
    assert len(item["rows"]) == attention.MAX_ROWS
    assert len(monitor._alerts([item], None)[0]["ids"]) == attention.MAX_ROWS + 2


def test_quiet_hours_lane_pause_is_not_an_item(ctx, monkeypatch):
    items = _encoder_items(ctx, monkeypatch, {
        "lanes_paused": [{"lane": "cpu", "reason": "stiltevenster actief"}],
        "baan_stilstand": []})
    assert items == {}


def test_operator_cannot_hide_missing_recovery_code(client, app):
    from fastapi.testclient import TestClient

    key = client.post("/api/v1/auth/api-keys",
                      json={"name": "telefoon", "role": "operator"}).json()["key"]
    with TestClient(app) as machine:
        body = {"entries": [{"key": "backup.bundle_missing", "fingerprint": "1"}]}
        assert machine.post("/api/v1/attention/dismiss", headers={"X-Api-Key": key},
                            json=body).status_code == 403
    assert client.post("/api/v1/attention/dismiss", json=body).status_code == 200


def test_degraded_pool_full_quarantine_pauses_av1_instead_of_overfilling(ctx, monkeypatch):
    """Quarantaine boven het maximum met wel op te ruimen originelen: normaal plant MediaGuard
    een opruimronde en loopt AV1 door. Met een pool die niet gezond is kan opruimen geen
    ruimte maken, dus pauzeert de wachtrij ('vol') in plaats van verder te vullen."""
    from mediaguard import storage_forecast
    from mediaguard.recovery import release_check
    from mediaguard.recovery import service as recovery_service

    monkeypatch.setenv("MG_ATTENTION_SKIP_ENCODER", "1")
    monkeypatch.setattr(release_check, "quarantine_usage_bytes", lambda _c: 600)
    monkeypatch.setattr(recovery_service, "effective_cap_bytes", lambda _c: 500)
    monkeypatch.setattr(recovery_service, "purge_candidates",
                        lambda _c, **_k: [{"id": 1, "size": 200}])
    assert release_check.quota_exceeded(ctx) is False  # gezond: opruimen maakt ruimte
    storage_forecast.record_pool(ctx, {"ok": False, "pools": [
        {"name": "tank", "status": "DEGRADED", "healthy": False}]})
    before = ctx.db.fetch_one("SELECT COUNT(*) AS n FROM jobs WHERE job_type='quarantine.purge'")
    assert release_check.quota_exceeded(ctx) is True
    after = ctx.db.fetch_one("SELECT COUNT(*) AS n FROM jobs WHERE job_type='quarantine.purge'")
    assert after["n"] == before["n"]  # geen opruimronde ingepland die toch pauzeert
    full = {i["key"]: i for i in attention.collect_local(ctx)}["quarantine.full"]
    assert full["actions"] == [] and "TrueNAS-pool" in full["detail_nl"]


def test_pool_is_measured_even_with_monitoring_off(ctx):
    from mediaguard import storage_forecast

    ctx.config.set("global", "", "monitor.enabled", False, "test")
    _truenas(ctx, [{"name": "tank", "status": "DEGRADED", "healthy": False, "scan": {}}])
    assert asyncio.run(monitor.run_once(ctx)) == {"skipped": "uit"}
    assert [p["name"] for p in storage_forecast.pool_degraded(ctx)] == ["tank"]
