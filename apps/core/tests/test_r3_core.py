"""Rapport 3: autopilot-stilstand (R3), job-koppeling (R4/R5), afgekeurde AV1 (R11), R15."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from mediaguard.db import utcnow


def _job(ctx, path, status, *, engine=None, error=None, created=None):
    now = created or utcnow()
    return ctx.db.execute_insert(
        "INSERT INTO encoding_jobs(source_path,status,policy_json,error,encoder_job_id,"
        "created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
        (path, status, "{}", error, None if engine is None else str(engine), now, now))


def test_awaiting_activation_does_not_block_autopilot_slots(ctx):
    from mediaguard.encoding import autopilot

    for i in range(9):  # 9 niet-geplaatste films (PermissionError) vulden de diepte (R3)
        _job(ctx, f"/data/media/movies/m{i}.mkv", "awaiting_activation", error="Errno 1")
    assert autopilot._in_flight(ctx) == 0
    _job(ctx, "/data/media/movies/run.mkv", "running")
    assert autopilot._in_flight(ctx) == 1
    assert autopilot.placement_backlog_cap(7) >= 20


def test_stalled_since_reports_after_an_hour(ctx):
    from mediaguard.encoding import autopilot

    old = (datetime.now(UTC) - timedelta(hours=3)).isoformat(timespec="seconds")
    ctx.state_set("encoding_autopilot_last_submit", old)
    ctx.state_set("encoding_autopilot", {"candidates": 12})
    ctx.config.set("global", "", "library.write_enabled", True, "t")
    assert autopilot.stalled_since(ctx) is not None
    _job(ctx, "/data/media/movies/x.mkv", "running")
    assert autopilot.stalled_since(ctx) is None


def test_engine_states_superseded_and_rolled_back(ctx):
    from mediaguard.encoding import jobstates

    a = _job(ctx, "/m/a.mkv", "failed", engine=37, error="Herstartlus")
    _job(ctx, "/m/a.mkv", "committed", engine=47)
    _job(ctx, "/m/b.mkv", "rejected", engine=12,
         error="Afgekeurd na activatie, origineel teruggezet: ondertitels")
    by = {s["engine_id"]: s for s in jobstates.states(ctx)}
    assert by[37]["superseded_by"] == 47 and by[37]["resolved"]
    assert by[12]["rolled_back"] and by[12]["status_nl"].startswith("Teruggezet")
    assert not by[47]["resolved"]
    assert jobstates.resolve(ctx, a) == {"core_id": a, "engine_id": 37, "matched": "core"}
    assert jobstates.resolve(ctx, 37)["matched"] == "engine"
    assert jobstates.engine_href(ctx, a) == "/av1/wachtrij?job=37"


def test_resolve_endpoint(client, ctx):
    a = _job(ctx, "/m/c.mkv", "failed", engine=58)
    r = client.get(f"/api/v1/encoding/jobs/resolve/{a}")
    assert r.status_code == 200 and r.json()["engine_id"] == 58
    assert client.get("/api/v1/encoding/jobs/resolve/99999").status_code == 404
    assert any(s["engine_id"] == 58 for s in client.get("/api/v1/encoding/engine-states")
               .json()["items"])


def test_plain_nl_cleans_python_dicts_and_dates():
    from mediaguard.textfmt import datetime_nl, plain_nl

    txt = plain_nl("ondertitels: ontbreekt: {'ara': 1, 'ger': 2, 'spa': 4}")
    assert "{" not in txt and "Arabisch" in txt and "Duits (2)" in txt
    assert datetime_nl("2026-11-04T12:01:25Z") == "4 nov 13:01"


def test_rejected_av1_purge_label_and_single_audit(ctx, tmp_path):
    from mediaguard.recovery import api as rapi

    f = tmp_path / "x.mkv"
    f.write_bytes(b"x")
    txn = ctx.db.execute_insert(
        "INSERT INTO replacement_transactions(kind,state,library_path,final_path,staged_path,"
        "source_snapshot_json,created_at,updated_at) VALUES ('restore','committed','/m/x.mkv',"
        "'/m/x.mkv','/m/x.part','{}',?,?)", (utcnow(), utcnow()))
    item = ctx.db.execute_insert(
        "INSERT INTO quarantine_items(transaction_id,original_path,quarantine_path,method,"
        "status,reason,created_at) VALUES (?,?,?,?,?,?,?)",
        (txn, "/m/x.mkv", str(f), "rename", "restorable", "Herstel van restore", utcnow()))
    row = dict(ctx.db.fetch_one("SELECT * FROM quarantine_items WHERE id=?", (item,)))
    assert rapi.is_rejected_av1(row)
    reason = rapi.rejected_av1_reason(ctx, row)
    assert reason.startswith("Afgekeurde AV1-versie verwijderd")
    rapi._delete_item_file(ctx, row, reason)
    rapi._delete_item_file(ctx, row, reason)  # tweede ronde: geen tweede auditregel
    n = ctx.db.fetch_one("SELECT COUNT(*) AS n FROM audit_events WHERE action='quarantine.purged' "
                         "AND target_id=?", (str(item),))["n"]
    assert n == 1


def test_invalid_size_spec_is_rejected_before_post():
    from mediaguard.provisioning.engine import spec_problem
    from mediaguard.provisioning.templates import CUSTOM_FORMATS

    bad = [{"implementation": "SizeSpecification",
            "fields": [{"name": "min", "value": 8}, {"name": "max", "value": 0}]}]
    assert spec_problem(bad) and "max" in spec_problem(bad)
    assert all(spec_problem(specs) is None for specs in CUSTOM_FORMATS.values())


def test_provisioning_gives_up_after_three_identical_failures(ctx):
    from mediaguard.provisioning import engine

    c = engine.Change(section="customformats", action="create", key="x", title_nl="t",
                      instance_id="sonarr-series")
    key, fp = engine.failure_key(c)
    for _ in range(3):
        assert not engine.gave_up(ctx, key, fp)
        engine.note_failure(ctx, key, fp, "'Max' must be greater than '8'", c)
    assert engine.gave_up(ctx, key, fp)
    assert not engine.gave_up(ctx, key, fp + "x")  # andere inhoud: gewoon opnieuw
    assert engine.failing_items(ctx)[0]["gave_up"]
    engine.clear_failure(ctx, key)
    assert engine.failing_items(ctx) == []


def test_mediainfo_summary_from_encoder_probe():
    from mediaguard.encoding.service import mediainfo_summary

    mi = mediainfo_summary({"codec": "av1", "width": 1920, "height": 1080, "size": 1,
                            "audio": [{"taal": "jpn", "codec": "opus", "kanalen": 2}],
                            "subs": [{"taal": "eng", "codec": "ass"}] * 3})
    assert mi["video"]["codec"] == "av1" and mi["audio"][0]["language"] == "jpn"
    assert len(mi["subtitles"]) == 3 and mi["source"] == "mediaguard"


def test_av1_history_marks_rollback_and_current_mediainfo():
    from mediaguard.library.api import current_av1_mediainfo

    hist = [{"status": "rejected", "rolled_back": True, "output_mediainfo": {"x": 1}},
            {"status": "committed", "rolled_back": False, "output_mediainfo": {"y": 2}}]
    assert current_av1_mediainfo(hist) == {"y": 2}


def test_twin_item_count_is_case_sensitive(ctx):
    from mediaguard.storage_typos import _items_under

    ctx.db.execute("PRAGMA foreign_keys=OFF")
    ctx.db.execute_insert(
        "INSERT INTO lib_items(instance_id,kind,provider_id,title,path,root_folder_path,synced_at) "
        "VALUES ('sonarr-series','series',1,'X','/data/Media/TV/X','/data/Media/TV','2026-11-05')")
    assert _items_under(ctx, "sonarr-series", "/data/Media/TV") == 1
    assert _items_under(ctx, "sonarr-series", "/data/media/tv") == 0


def test_free_space_snapshot_and_reasons(ctx, client):
    from mediaguard.library.api import _space_reasons, free_space_snapshot

    snap = free_space_snapshot(ctx)
    assert "free" in snap and isinstance(snap["disks"], list)
    reasons = _space_reasons(ctx, "2026-11-04", "2026-11-05", -900 * 1024 ** 3)
    assert reasons[0].startswith("Vrije ruimte −900,0 GB")
    assert client.get("/api/v1/library-count-changes?days=3").status_code == 200


def test_collections_bulk_then_fallback_without_movies():
    import asyncio

    from mediaguard.arr.client import ArrError
    from mediaguard.storage_typos import repoint_collections

    class C:
        app = "radarr"

        def __init__(self, bulk_ok):
            self.bulk_ok, self.puts = bulk_ok, []

        async def get(self, path):
            return [{"id": 1, "title": "AFC", "rootFolderPath": "/data/Media/Anime Fims/",
                     "movies": [{"id": 9}]}]

        async def put(self, path, body):
            if path == "collection" and not self.bulk_ok:
                raise ArrError("400", status=400)
            self.puts.append((path, body))

    ok = C(True)
    assert asyncio.run(repoint_collections(ok, "/data/Media/Anime Fims",
                                           "/data/Media/Anime Films")) == 1
    assert ok.puts[0][1] == {"collectionIds": [1], "rootFolderPath": "/data/Media/Anime Films"}
    fb = C(False)
    assert asyncio.run(repoint_collections(fb, "/data/Media/Anime Fims",
                                           "/data/Media/Anime Films")) == 1
    assert fb.puts[0][0] == "collection/1" and "movies" not in fb.puts[0][1]


def test_stale_scan_detection_without_runs(ctx):
    from mediaguard.migration.autopilot import stale_runs

    assert stale_runs(ctx) == []


def test_lookup_titles_uses_arr_lookup(ctx, monkeypatch):
    import asyncio
    import contextlib

    from mediaguard.instances import registry
    from mediaguard.migration.wizard import lookup_titles

    class C:
        async def get(self, path, **params):
            assert path == "movie/lookup/tmdb" and params["tmdbId"] == 900001
            return {"title": "Testfilm", "year": 2025}

    @contextlib.asynccontextmanager
    async def client(_ctx, _iid, **_kw):
        yield C()

    monkeypatch.setattr(registry, "arr_client", client)
    assert asyncio.run(lookup_titles(ctx, "radarr-films", "movie", ["900001"])) == {
        "900001": "Testfilm (2025)"}


def test_r4_rollback_is_not_a_failure_and_probe_unlocks_env_retry(ctx):
    from mediaguard.encoding import autopilot as ap
    from mediaguard.recovery import probe

    assert ap.classify_failure("rejected", "Afgekeurd na activatie, origineel teruggezet") is None
    ins = ("INSERT INTO encoding_jobs(source_path,status,error,trial_only,policy_json,"
           "created_at,updated_at) VALUES (?,?,?,0,'{}',?,?)")
    path = "/data/Media/Films/Dromenvanger (2010)/Dromenvanger.mkv"
    for _ in range(3):  # gewone pogingen op
        ctx.db.execute_insert(ins, (path, "rejected",
                                    "PermissionError: [Errno 1] Operation not permitted",
                                    "2026-11-04T10:00:00+00:00", "2026-11-04T10:00:00+00:00"))
    assert ap.retry_states(ctx)[path]["state"] == "final"
    ctx.state_set(probe.STATE_KEY, {"checked_at": "2026-11-05T09:00:00+00:00", "roots": [
        {"path": "/data/Media/Films", "ok": True, "checked_at": "2026-11-05T09:00:00+00:00"}]})
    st = ap.retry_states(ctx)[path]
    assert st["state"] == "retry" and st["after_probe"]


def test_r4_seerr_compare_normalised_and_repeats_ignored(ctx):
    from mediaguard.provisioning import seerr_sync as ss

    assert ss.same("activeDirectory", "/data/Media/Anime Films/", "/data/Media/Anime Films")
    assert ss.same("externalUrl", "http://NAS:7878/", "http://nas:7878")
    assert ss.same("port", "7878", 7878) and ss.same("baseUrl", "", None)
    assert not ss.same("activeProfileId", 3, 4)
    rem = [{"instance_id": "radarr-animefilms", "action": "update",
            "diff": [{"field": "minimumAvailability", "old": "announced", "new": "released"}]}]
    assert ss.note_repeats(ctx, rem) == []
    assert ss.note_repeats(ctx, rem) == ["radarr-animefilms: minimumAvailability"]
    assert "minimumAvailability" in ctx.state_get(ss.IGNORED_KEY)["radarr-animefilms"]


def test_r4_audit_rows_are_dutch_and_show_api_key_actor(client, ctx):
    from mediaguard.audit import audit

    audit(ctx.db, actor="apikey:mso", action="wanted.search_now", target_type="wanted")
    audit(ctx.db, actor="svc:download-guard", action="download.refused_removed")
    rows = client.get("/api/v1/audit?limit=5").json()
    by = {r["action"]: r for r in rows}
    assert by["download.refused_removed"]["action_nl"].startswith("Geweigerde download")
    mso = by["wanted.search_now"]
    assert mso["via_api_key"] and mso["actor_nl"].startswith("Media Source Optimizer")
    assert "search" not in mso["action_nl"]


def test_r4_wanted_status_and_notification_order(client, ctx):
    from datetime import UTC, datetime, timedelta

    from mediaguard.api.system import notify
    from mediaguard.wanted.engine import wanted_status_nl

    old = (datetime.now(UTC) - timedelta(hours=10)).isoformat(timespec="seconds")
    assert wanted_status_nl({"status": "searching", "last_searched_at": old,
                             "next_search_at": "2026-11-05T12:00:00+00:00"}).startswith(
        "Volgende zoekpoging")
    now = datetime.now(UTC).isoformat(timespec="seconds")
    assert wanted_status_nl({"status": "searching", "last_searched_at": now}) == "Wordt gezocht"
    notify(ctx, "warning", "A", "eerste", dedupe_key="a")
    notify(ctx, "warning", "B", "tweede", dedupe_key="b")
    ctx.db.execute("UPDATE notifications SET ts='2026-01-01T00:00:00+00:00' WHERE title='B'")
    notify(ctx, "warning", "A", "opnieuw", dedupe_key="a")  # samengevoegd: nieuwe ts
    rows = client.get("/api/v1/notifications?limit=5").json()
    assert [r["title"] for r in rows][:2] == ["A", "B"]
