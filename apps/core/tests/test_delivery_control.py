"""Leveringsmotor, tweede ronde (verbeterlijst 5, 7, 9, 11, 12, 15, 36).

- 7: het MediaGuard-profiel komt alleen op titels die ná de overname via Seerr zijn
  aangevraagd, maar één keer, en nooit op gemigreerde titels, proefinstanties of een profiel
  dat de eigenaar zelf koos (bv. 4K).
- 11: een titel die uit Sonarr/Radarr verdween wordt niet opnieuw aangevraagd; Seerr alleen
  opnieuw bij een echte mislukking (max. 3×); verwijderde aanvragen stoppen; Stop/Nu opnieuw.
- 12: verkeerd bestand via MediaGuard's eigen quarantaine (ook films); tellers opnieuw.
- 36: zoekacties tellen mee in het quotum; 'geleverd' naar Seerr wordt opnieuw geprobeerd.
- 9/15: één kapotte vraag stopt de ronde niet; noodstop = niets doen.
"""

# ruff: noqa: F811 - de fixture 'env' komt uit test_delivery
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
from test_arr_fakes import (  # noqa: F401 - b1ctx is een fixture
    FakeArr,
    b1ctx,
    iso,
    make_episode,
    make_movie,
    make_series,
)
from test_delivery import (  # noqa: F401 - env is een fixture
    MG_SERIES,
    commands,
    env,
    item,
    managed_since,
    seerr_request,
)

from mediaguard.db import utcnow
from mediaguard.delivery import engine, store


def series_with_request(env: Any, sid: int, tvdb: int, rid: int, *, added: str | None = None,
                        **req: Any) -> FakeArr:
    ctx, arr, seerr = env
    sonarr = arr["sonarr-series"]
    if not any(p["name"] == MG_SERIES for p in sonarr.quality_profiles):
        sonarr.quality_profiles.append({"id": 9, "name": MG_SERIES, "items": []})
    s = make_series(sid, f"Serie {sid}", tvdb)
    s["added"] = added or iso()
    sonarr.add_series(s, [make_episode(sid * 10 + 1, sid, 1, 1)])
    seerr.requests.append({**seerr_request(rid, tv=True, ext=tvdb, seasons=[1]), **req})
    return sonarr


def seerr_calls(seerr: Any, part: str) -> list[Any]:
    return [c for c in seerr.calls if c[0] == "POST" and part in c[1]]


# ── 7: profiel alleen bij nieuwe Seerr-aanvragen, één keer ──────────────────────────────
async def test_profile_set_once_then_owner_choice_stays(env):
    ctx, _arr, _seerr = env
    managed_since(ctx)
    sonarr = series_with_request(env, 20, 403245, 20)
    await engine.run_once(ctx, force=True)
    assert sonarr.series[20]["qualityProfileId"] == 9
    # de eigenaar kiest daarna zelf 4K en zet bewaken uit: MediaGuard blijft eraf
    sonarr.series[20]["qualityProfileId"] = 2
    sonarr.series[20]["monitored"] = False
    await engine.run_once(ctx, force=True)
    await engine.run_once(ctx, force=True)
    assert sonarr.series[20]["qualityProfileId"] == 2
    assert sonarr.series[20]["monitored"] is False


@pytest.mark.parametrize("case", ["migrated", "before_takeover", "already_there", "is4k",
                                  "chosen_profile", "shadow"])
async def test_profile_left_alone(env, case):
    ctx, _arr, seerr = env
    managed_since(ctx, days=0 if case == "before_takeover" else 30)
    req: dict[str, Any] = {}
    if case == "is4k":
        req["is4k"] = True
    if case == "chosen_profile":
        req["profileId"] = 1
    sonarr = series_with_request(env, 21, 81189, 21,
                                 added="2023-01-01T00:00:00Z" if case == "already_there"
                                 else None, **req)
    if case == "migrated":
        run = ctx.db.execute_insert("INSERT INTO migration_runs(name,created_at,updated_at) "
                                    "VALUES ('m',?,?)", (utcnow(), utcnow()))
        ctx.db.execute("INSERT INTO instance_migration_checkpoints(run_id,instance_id,phase,"
                       "kind,source_key,target_ref,updated_at) VALUES (?,?,?,?,?,?,?)",
                       (run, "sonarr-series", "import", "series", "5", "21", utcnow()))
    if case == "shadow":
        ctx.db.execute("UPDATE instances SET shadow_mode=1 WHERE id='sonarr-series'")
    await engine.run_once(ctx, force=True)
    assert sonarr.series[21]["qualityProfileId"] == 1
    assert not [c for c in sonarr.calls if c[0] == "PUT" and "/series/" in c[1]]
    row = item(ctx)
    events = [e["detail_nl"] for e in store.events(ctx, row["id"])]
    if case == "shadow":
        # proefmodus: alleen kijken, ook niet zoeken
        assert not commands(sonarr, "EpisodeSearch")
        assert "proefmodus" in (row["detail_nl"] or "")
    else:
        assert any("blijft staan" in e for e in events), events
    assert seerr.calls  # Seerr is wel gelezen


async def test_migrated_titles_are_not_new_requests(env):
    ctx, _arr, _seerr = env
    run = ctx.db.execute_insert("INSERT INTO migration_runs(name,created_at,updated_at) "
                                "VALUES ('m',?,?)", (utcnow(), utcnow()))
    for pid, migrated in ((31, True), (32, False)):
        ctx.db.execute(
            "INSERT INTO lib_items(instance_id,kind,provider_id,title,tvdb_id,monitored,"
            "added_at,synced_at) VALUES ('sonarr-series','series',?,?,?,1,?,?)",
            (pid, f"Titel {pid}", 7000 + pid, iso(), utcnow()))
        if migrated:
            ctx.db.execute("INSERT INTO instance_migration_checkpoints(run_id,instance_id,"
                           "phase,kind,source_key,target_ref,updated_at) VALUES "
                           "(?,'sonarr-series','import','series','1',?,?)",
                           (run, str(pid), utcnow()))
    await engine.discover(ctx)
    keys = {r["item_key"] for r in store.list_items(ctx)}
    assert keys == {"series:tvdb:7032"}


# ── 11: verdwenen titels, Seerr-pogingen, Stop en Nu opnieuw ─────────────────────────────
async def test_title_removed_from_arr_stops_without_seerr_retry(env):
    ctx, _arr, seerr = env
    sonarr = series_with_request(env, 22, 355567, 22)
    await engine.run_once(ctx, force=True)
    assert item(ctx)["state"] == "active"
    del sonarr.series[22]
    for _ in range(3):
        await engine.run_once(ctx, force=True)
    row = item(ctx)
    assert row["state"] == "stopped" and row["stage_nl"] == "Gestopt"
    assert "niet opnieuw aan" in row["detail_nl"]
    assert not seerr_calls(seerr, "/retry") and not seerr_calls(seerr, "/approve")


async def test_seerr_retry_only_on_failure_and_at_most_three_times(env):
    ctx, _arr, seerr = env
    seerr.requests.append(seerr_request(23, tv=False, ext=5555, status=4))
    for _ in range(6):
        await engine.run_once(ctx, force=True)
    assert len(seerr_calls(seerr, "/request/23/retry")) == 3
    row = item(ctx)
    assert "3× geprobeerd" in row["detail_nl"] and row["state"] == "active"
    notes = ctx.db.fetch_dicts("SELECT title FROM notifications WHERE title LIKE "
                               "'%komt niet in Sonarr/Radarr%'")
    assert len(notes) == 1


async def test_approved_request_is_never_resent(env):
    ctx, _arr, seerr = env
    seerr.requests.append(seerr_request(24, tv=False, ext=6666, status=2))
    for _ in range(3):
        await engine.run_once(ctx, force=True)
    assert not seerr_calls(seerr, "/retry")
    assert "Seerr" in (item(ctx)["detail_nl"] or "")


async def test_request_deleted_in_seerr_stops(env):
    ctx, arr, seerr = env
    arr["radarr-films"].movies[7] = make_movie(7, "Blijft", 7001)
    arr["radarr-films"].movies[8] = make_movie(8, "Weg", 7002)
    seerr.requests += [seerr_request(25, tv=False, ext=7001),
                       seerr_request(26, tv=False, ext=7002)]
    await engine.run_once(ctx, force=True)
    assert store.counts(ctx)["active"] == 2
    seerr.requests = [r for r in seerr.requests if r["id"] != 26]
    await engine.run_once(ctx, force=True)
    by_title = {r["title"]: r for r in store.list_items(ctx)}
    assert by_title["Weg"]["state"] == "stopped" and by_title["Blijft"]["state"] == "active"
    assert store.counts(ctx)["stopped"] == 1


async def test_stop_and_retry_now_buttons(env, client):  # type: ignore[no-untyped-def]
    ctx, _arr, seerr = env
    sonarr = series_with_request(env, 27, 371572, 27)
    await engine.run_once(ctx, force=True)
    rid = item(ctx)["id"]
    searches = len(commands(sonarr, "EpisodeSearch"))
    r = client.post(f"/api/v1/delivery/requests/{rid}/stop")
    assert r.status_code == 200 and r.json()["state"] == "stopped"
    await engine.run_once(ctx, force=True)
    assert len(commands(sonarr, "EpisodeSearch")) == searches  # niets meer gedaan
    assert client.get("/api/v1/delivery/requests?state=stopped").json()["items"]
    # 'Nu opnieuw' zoekt meteen, ook al loopt de terugvaltijd nog
    r = client.post(f"/api/v1/delivery/requests/{rid}/retry")
    assert r.status_code == 200 and r.json()["state"] == "active"
    assert len(commands(sonarr, "EpisodeSearch")) == searches + 1
    acts = {a["action"] for a in ctx.db.fetch_dicts("SELECT action FROM audit_events")}
    assert {"delivery.stopped", "delivery.retry"} <= acts
    # noodstop: niet opnieuw
    ctx.state_set("emergency_stop", {"active": True, "since": utcnow()})
    assert client.post(f"/api/v1/delivery/requests/{rid}/retry").status_code == 409
    assert client.post("/api/v1/delivery/requests/999/stop").status_code == 404
    # een nieuwe aanvraag voor dezelfde titel opent een gestopte titel weer
    ctx.state_set("emergency_stop", {"active": False, "since": utcnow()})
    client.post(f"/api/v1/delivery/requests/{rid}/stop")
    store.update(ctx, rid, attempts=5, first_search_at=iso(-40), notified_at=utcnow())
    seerr.requests.append(seerr_request(28, tv=True, ext=371572, seasons=[2]))
    await engine.discover(ctx)
    row = store.get(ctx, rid)
    assert row and row["state"] == "active" and row["attempts"] == 0
    assert row["first_search_at"] is None and row["notified_at"] is None


# ── 12: verkeerd bestand via de quarantaine, ook films ──────────────────────────────────
@pytest.fixture
def lib(ctx, tmp_path):  # type: ignore[no-untyped-def]
    root = tmp_path / "media"
    root.mkdir()
    ctx.settings.library_roots[:] = [str(root)]
    return root


def wrong_file(ctx: Any, kind: str, file_id: int, path: Path, *,
               actionable: bool = True) -> None:
    ctx.db.execute(
        "INSERT INTO identity_results(instance_id,kind,provider_id,file_path,status,"
        "confidence,actionable,reason_nl,source,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("sonarr-series" if kind == "episode_file" else "radarr-films", kind, str(file_id),
         str(path), "mismatch", 0.97, int(actionable), "Bestand is S01E02, verwacht S01E01",
         "post_import", utcnow()))


def arr_file(fake: FakeArr, resource: str, file_id: int, path: Path, owner: dict[str, Any],
             on_delete: Any, *, fail: bool = False) -> None:
    base = f"/api/v3/{resource}/{file_id}"
    fake.intercept[("GET", base)] = lambda _b: (200, {"id": file_id, "path": str(path),
                                                      "size": path.stat().st_size, **owner})

    def delete(_b: Any) -> tuple[int, Any]:
        if fail:
            return 500, {"message": "schijf vol"}
        os.unlink(path)  # zoals Sonarr/Radarr: bestand weg uit de bibliotheek
        on_delete()
        return 200, {}

    fake.intercept[("DELETE", base)] = delete


def episode_setup(env: Any, lib: Path, *, fail: bool = False,
                  actionable: bool = True) -> tuple[FakeArr, Path]:
    ctx, arr, seerr = env
    sonarr = arr["sonarr-series"]
    folder = lib / "tv" / "Show" / "Season 01"
    folder.mkdir(parents=True)
    path = folder / "Show.S01E01.mkv"
    path.write_bytes(b"verkeerde aflevering" * 100)
    sonarr.add_series(make_series(30, "Show", 30030),
                      [make_episode(301, 30, 1, 1, has_file=True, file_id=3001)])
    seerr.requests.append(seerr_request(30, tv=True, ext=30030, seasons=[1]))

    def gone() -> None:
        sonarr.episodes[301].update(hasFile=False, episodeFileId=0)

    arr_file(sonarr, "episodefile", 3001, path, {"seriesId": 30}, gone, fail=fail)
    wrong_file(ctx, "episode_file", 3001, path, actionable=actionable)
    return sonarr, path


async def test_wrong_episode_goes_to_quarantine_then_correct_one_is_searched(env, lib):
    ctx, _arr, _seerr = env
    sonarr, path = episode_setup(env, lib)
    await engine.run_once(ctx, force=True)
    assert not path.exists()
    q = ctx.db.fetch_one("SELECT * FROM quarantine_items")
    assert q["status"] == "restorable" and q["reason"].startswith("Verkeerd bestand")
    assert Path(q["quarantine_path"]).read_bytes() == b"verkeerde aflevering" * 100
    txn = ctx.db.fetch_one("SELECT * FROM replacement_transactions WHERE id=?",
                           (q["transaction_id"],))
    assert txn["state"] == "committed" and txn["encoding_job_id"] is None
    check = ctx.db.fetch_one("SELECT status FROM quarantine_release_checks WHERE item_id=?",
                             (q["id"],))
    assert check["status"] == "passed"
    assert commands(sonarr, "EpisodeSearch")[-1]["episodeIds"] == [301]
    row = item(ctx)
    assert "quarantaine" in row["detail_nl"] and row["stage"] == "checking"
    assert ctx.db.fetch_one("SELECT 1 FROM audit_events WHERE action='identity.auto_replaced'")
    assert ctx.db.fetch_one("SELECT SUM(cost) AS n FROM delivery_searches")["n"] >= 1


async def test_wrongly_replaced_file_can_be_restored_and_stays(env, lib):
    """Was de identiteitscontrole fout, dan zet Terugzetten het bestand op zijn (lege) plek
    terug en laat Sonarr herscannen. Daarna haalt MediaGuard het niet opnieuw weg."""
    ctx, _arr, _seerr = env
    from mediaguard.delivery import replace
    from mediaguard.recovery import executor, service

    sonarr, path = episode_setup(env, lib)
    await engine.run_once(ctx, force=True)
    assert not path.exists()
    q = ctx.db.fetch_one("SELECT * FROM quarantine_items")
    txn_id, _job = service.create_restore(ctx, int(q["id"]), "user:jan")
    done = await executor.run_transaction(ctx, txn_id)
    assert done["state"] == "committed", done["error"]
    assert path.read_bytes() == b"verkeerde aflevering" * 100
    assert Path(q["quarantine_path"]).exists()  # het bewaarde bestand blijft ook staan
    assert ctx.db.fetch_one("SELECT status FROM quarantine_items WHERE id=?",
                            (q["id"],))["status"] == "restored"
    assert commands(sonarr, "RescanSeries")
    # Sonarr kent het weer (nieuw bestands-ID); de controle vindt het opnieuw 'verkeerd'
    sonarr.episodes[301].update(hasFile=True, episodeFileId=3002)
    arr_file(sonarr, "episodefile", 3002, path, {"seriesId": 30}, lambda: None)
    wrong_file(ctx, "episode_file", 3002, path)
    res = await replace.replace_wrong_file(ctx, instance_id="sonarr-series",
                                           kind="episode_file", file_id=3002, reason_nl="x")
    assert res["status"] == "kept" and path.exists()
    latest = ctx.db.fetch_one("SELECT status, source, created_by FROM identity_results WHERE "
                              "provider_id='3002' ORDER BY id DESC LIMIT 1")
    assert (latest["status"], latest["source"], latest["created_by"]) == \
        ("verified", "user", "user:jan")
    assert await replace.replace_wrong_file(
        ctx, instance_id="sonarr-series", kind="episode_file", file_id=3002,
        reason_nl="x") == {"status": "review", "reason_nl": "niet (meer) als verkeerd beoordeeld"}


async def test_restore_into_empty_spot_never_overwrites(env, lib):
    """Staat er intussen weer een bestand op de plek (bv. de juiste release), dan zet
    terugzetten niets terug en blijft dat bestand ongemoeid."""
    ctx, _arr, _seerr = env
    from mediaguard.recovery import executor, service

    _sonarr, path = episode_setup(env, lib)
    await engine.run_once(ctx, force=True)
    q = ctx.db.fetch_one("SELECT * FROM quarantine_items")
    txn_id, _job = service.create_restore(ctx, int(q["id"]), "test")
    path.write_bytes(b"juiste release")
    done = await executor.run_transaction(ctx, txn_id)
    assert done["state"] == "aborted" and "niets overschreven" in done["error"]
    assert path.read_bytes() == b"juiste release"
    assert Path(q["quarantine_path"]).exists()
    assert ctx.db.fetch_one("SELECT status FROM quarantine_items WHERE id=?",
                            (q["id"],))["status"] == "restorable"


async def test_read_only_core_hands_the_file_step_to_recovery(env, lib, monkeypatch):
    """In de compose-installatie heeft core de bibliotheek en quarantaine alleen-lezen: dan
    doet de recovery-werker de bestandsstap (geen transactie in core, geen EROFS-lus)."""
    ctx, _arr, _seerr = env
    from mediaguard import jobs
    from mediaguard.delivery import replace
    from mediaguard.recovery import api as recovery_api

    sonarr, path = episode_setup(env, lib)
    monkeypatch.setattr(replace, "_writable", lambda _p: False)
    for _ in range(2):
        res = await replace.replace_wrong_file(ctx, instance_id="sonarr-series",
                                               kind="episode_file", file_id=3001,
                                               reason_nl="x")
        assert res["status"] == "queued"
    assert path.exists() and not ctx.db.fetch_one("SELECT 1 FROM replacement_transactions")
    queued = ctx.db.fetch_dicts("SELECT * FROM jobs WHERE job_type='recovery.replace_wrong'")
    assert len(queued) == 1 and jobs.allowed_for_role("recovery.replace_wrong", "recovery")
    assert not jobs.allowed_for_role("recovery.replace_wrong", "core")
    # de worker zelf mag ook niet schrijven: overslaan met uitleg, niets aangeraakt
    jc = jobs.JobContext(int(queued[0]["id"]), "recovery.replace_wrong",
                         jobs.loads(queued[0]["payload"]), "sonarr-series", None, ctx)
    out = await recovery_api._job_replace_wrong(jc)
    assert out["status"] == "skipped" and "alleen-lezen" in out["reason_nl"]
    assert path.exists() and not ctx.db.fetch_one("SELECT 1 FROM replacement_transactions")
    # de recovery-werker mag wel schrijven
    monkeypatch.setattr(replace, "_writable", lambda _p: True)
    out = await recovery_api._job_replace_wrong(jc)
    assert out["status"] == "replaced" and not path.exists()
    assert commands(sonarr, "EpisodeSearch")


async def test_failed_replacement_backs_off_and_stops_after_three(env, lib, client):  # type: ignore[no-untyped-def]
    """Kan Sonarr het bestand blijvend niet weghalen, dan niet elk half uur een nieuwe kopie:
    na 1 uur en 6 uur opnieuw, na 3 keer stoppen en 'Wacht op jou'."""
    ctx, _arr, _seerr = env
    from mediaguard import attention
    from mediaguard.delivery import replace

    _sonarr, path = episode_setup(env, lib, fail=True)

    async def attempt() -> dict[str, Any]:
        return await replace.replace_wrong_file(ctx, instance_id="sonarr-series",
                                                kind="episode_file", file_id=3001,
                                                reason_nl="x")

    def copies() -> int:
        return ctx.db.fetch_one("SELECT COUNT(*) AS n FROM quarantine_items")["n"]

    def age(hours: float) -> None:
        data = ctx.state_get(replace.FAILURES_KEY)
        data["sonarr-series:3001"]["at"] = iso(-hours / 24)
        ctx.state_set(replace.FAILURES_KEY, data)

    assert (await attempt())["status"] == "failed" and copies() == 1
    res = await attempt()
    assert res["status"] == "skipped" and "vorige poging mislukt" in res["reason_nl"]
    assert copies() == 1
    age(2)
    assert (await attempt())["status"] == "failed" and copies() == 2
    age(2)
    assert (await attempt())["status"] == "skipped" and copies() == 2  # nu 6 uur wachten
    age(7)
    assert (await attempt())["status"] == "failed" and copies() == 3
    age(48)
    res = await attempt()
    assert res["status"] == "skipped" and "3 keer mislukt" in res["reason_nl"]
    assert copies() == 3 and path.exists()
    items = [i for i in attention.collect_local(ctx) if i["key"] == "identity.replace_failed"]
    assert items and items[0]["rows"][0]["title"] == path.name
    # 'Opnieuw proberen' wist de teller
    assert client.post("/api/v1/attention/replace-failures/reset").json()["reset"] == 1
    assert not [i for i in attention.collect_local(ctx)
                if i["key"] == "identity.replace_failed"]
    assert (await attempt())["status"] == "failed" and copies() == 4
    # een nieuwe identiteitsuitslag begint ook opnieuw
    wrong_file(ctx, "episode_file", 3001, path)
    assert (await attempt())["status"] == "failed" and copies() == 5


async def test_wrong_file_never_removed_without_safety_net(env, lib):
    ctx, _arr, _seerr = env
    # bibliotheek vergrendeld: niets aanraken, eerlijke tekst
    ctx.config.set("global", "", "library.write_enabled", False, "test")
    sonarr, path = episode_setup(env, lib)
    await engine.run_once(ctx, force=True)
    assert path.exists() and not sonarr.calls_to("DELETE", "/api/v3/episodefile")
    assert "vervangen kan nu niet" in item(ctx)["detail_nl"]
    assert "vergrendeld" in item(ctx)["detail_nl"]


async def test_failed_removal_keeps_original_in_place(env, lib):
    ctx, _arr, _seerr = env
    sonarr, path = episode_setup(env, lib, fail=True)
    await engine.run_once(ctx, force=True)
    assert path.read_bytes() == b"verkeerde aflevering" * 100
    q = ctx.db.fetch_one("SELECT status, quarantine_path FROM quarantine_items")
    assert q["status"] == "discarded" and not Path(q["quarantine_path"]).exists()
    txn = ctx.db.fetch_one("SELECT state FROM replacement_transactions")
    assert txn["state"] in ("rolled_back", "aborted")
    assert "vervangen kan nu niet" in item(ctx)["detail_nl"]
    assert not commands(sonarr, "EpisodeSearch")


async def test_interrupted_removal_puts_original_back(env, lib):
    """Sonarr haalde het bestand weg maar meldde een fout (of MediaGuard viel weg): de
    journal-afhandeling zet het origineel uit de quarantaine terug. Niets kwijt."""
    ctx, _arr, _seerr = env
    sonarr, path = episode_setup(env, lib)

    def delete(_b: Any) -> tuple[int, Any]:
        os.unlink(path)
        return 500, {"message": "time-out"}

    sonarr.intercept[("DELETE", "/api/v3/episodefile/3001")] = delete
    await engine.run_once(ctx, force=True)
    assert path.read_bytes() == b"verkeerde aflevering" * 100
    assert ctx.db.fetch_one("SELECT state FROM replacement_transactions")["state"] == \
        "rolled_back"
    assert ctx.db.fetch_one("SELECT status FROM quarantine_items")["status"] == "restored"


async def test_wrong_movie_is_replaced_too(env, lib):
    """Films hadden geen vervangroute; nu via dezelfde quarantaine (automation-lus)."""
    ctx, arr, _seerr = env
    radarr = arr["radarr-films"]
    folder = lib / "movies" / "Film (2021)"
    folder.mkdir(parents=True)
    path = folder / "Film.mkv"
    path.write_bytes(b"remake" * 50)
    radarr.movies[40] = make_movie(40, "Film", 4040, has_file=True)

    def gone() -> None:
        radarr.movies[40].update(hasFile=False, movieFile=None)

    arr_file(radarr, "moviefile", 400, path, {"movieId": 40}, gone)
    wrong_file(ctx, "movie_file", 400, path)
    from mediaguard import automation

    assert await automation.replace_mismatches(ctx) == 1
    assert not path.exists()
    q = ctx.db.fetch_one("SELECT * FROM quarantine_items")
    assert q["media_kind"] == "movie_file" and Path(q["quarantine_path"]).exists()
    assert commands(radarr, "MoviesSearch")[-1]["movieIds"] == [40]
    # tweede ronde: niet nog eens
    assert await automation.replace_mismatches(ctx) == 0
    # de quarantainelijst noemt het een verkeerd bestand, geen origineel van een AV1-versie
    from mediaguard.recovery.api import quarantine_list

    rows = quarantine_list(status=None, instance_id=None, limit=200, _=None,  # type: ignore[arg-type]
                           ctx=ctx)
    assert rows[0]["kind_nl"] == "Verkeerd bestand (vervangen)"


async def test_not_found_counter_restarts_after_delivery(env):
    ctx, arr, seerr = env
    sonarr = arr["sonarr-series"]
    sonarr.add_series(make_series(33, "Lang", 33033),
                      [make_episode(331, 33, 1, 1)])
    seerr.requests.append(seerr_request(33, tv=True, ext=33033, seasons=[1]))
    await engine.run_once(ctx, force=True)
    store.update(ctx, item(ctx)["id"], first_search_at=iso(-40))
    await engine.run_once(ctx, force=True)
    assert item(ctx)["state"] == "not_found"
    sonarr.episodes[331].update(hasFile=True, episodeFileId=3310)
    await engine.run_once(ctx, force=True)
    row = item(ctx)
    assert row["state"] == "delivered" and row["first_search_at"] is None
    assert row["attempts"] == 0 and row["notified_at"] is None
    # nieuwe aflevering verschijnt: gewoon zoeken, niet meteen 'na 30 dagen'
    sonarr.episodes[332] = make_episode(332, 33, 1, 2, aired_days=-1)
    await engine.run_once(ctx, force=True)
    assert item(ctx)["state"] == "active"


# ── 36: zoekquotum en Seerr bijwerken ────────────────────────────────────────────────────
async def test_delivery_searches_count_towards_quota(env):
    ctx, _arr, _seerr = env
    from mediaguard.wanted.backfill import quota_status

    sonarr = series_with_request(env, 34, 34034, 34)
    sonarr.episodes[342] = make_episode(342, 34, 1, 2)
    await engine.run_once(ctx, force=True)
    assert quota_status(ctx, "sonarr-series")["hour_used"] == 2
    # quotum op: niet zoeken, wel uitleggen
    ctx.config.set("global", "", "backfill.max_searches_per_hour", 2, "test")
    store.update(ctx, item(ctx)["id"], next_action_at=None, attempts=0)
    before = len(commands(sonarr, "EpisodeSearch"))
    await engine.run_once(ctx, force=True)
    assert len(commands(sonarr, "EpisodeSearch")) == before
    assert "Zoekquotum" in (item(ctx)["next_step_nl"] or "")


async def test_seerr_delivered_update_is_retried(env):
    ctx, arr, seerr = env
    arr["radarr-films"].movies[9] = make_movie(9, "Arrival", 329865, has_file=True)
    seerr.requests.append(seerr_request(35, tv=False, ext=329865))
    await engine.discover(ctx)
    good = seerr.api_key
    seerr.api_key = "kapot"  # Seerr weigert alles
    await engine.run_once(ctx, force=True)
    row = item(ctx)
    assert row["state"] == "delivered" and row["seerr_synced_at"] is None
    assert row["seerr_sync_attempts"] == 1 and "Seerr" in row["next_step_nl"]
    seerr.api_key = good
    await engine.run_once(ctx, force=True)
    row = item(ctx)
    assert row["seerr_synced_at"] and "radarr-scan" in seerr.jobs


# ── 9 en 15: één kapotte vraag of een noodstop ───────────────────────────────────────────
async def test_routing_question_about_deleted_title_does_not_stop_round(env):
    ctx, _arr, _seerr = env
    from mediaguard.routing import service as routing

    routing._record(ctx, source="sonarr-series", target="sonarr-anime", kind="series",
                    provider_id=999, external_id=999999, title="Weg",
                    status="suggested", evidence=["Seriestype 'anime'"])
    series_with_request(env, 36, 36036, 36)
    out = await engine.run_once(ctx, force=True)
    assert out["processed"] == 1 and out["errors"] == 0
    status = ctx.db.fetch_one("SELECT status, detail FROM anime_routing_actions")
    assert status["status"] == "dismissed" and "bestaat niet meer" in status["detail"]


async def test_emergency_stop_means_hands_off(env):
    ctx, arr, seerr = env
    radarr = arr["radarr-films"]
    radarr.movies[5] = make_movie(5, "Wadi", 999)
    radarr.queue.append({"id": 77, "movieId": 5, "title": "Wadi.2160p", "status": "failed",
                         "trackedDownloadState": "failedPending",
                         "trackedDownloadStatus": "error", "downloadId": "SAB_9"})
    seerr.requests.append(seerr_request(37, tv=False, ext=999, status=1))
    ctx.state_set("emergency_stop", {"active": True, "since": utcnow()})
    out = await engine.run_once(ctx, force=True)
    assert out["skipped"] == "noodstop" and out["processed"] == 0
    assert not radarr.calls_to("DELETE", "/api/v3/queue")
    assert not commands(radarr, "MoviesSearch") and not seerr_calls(seerr, "/approve")
    assert store.list_items(ctx)  # wel bijgehouden wat er is aangevraagd


async def test_stuck_checker_waits_for_mediaguard_identity_check(env):
    """Verbeterlijst 5: ook de vastloper-controle gooit een download niet weg terwijl
    MediaGuard zelf de import uitstelt (identiteitscontrole/AI)."""
    ctx, arr, _seerr = env
    from mediaguard.wanted import stuck

    sonarr = arr["sonarr-series"]
    sonarr.queue = [{"id": 8, "downloadId": "SAB_2", "title": "x", "status": "completed",
                     "trackedDownloadState": "importBlocked", "statusMessages": [
                         {"messages": ["MediaGuard: twijfel over de aflevering "
                                       "[IDENTITY_UNCERTAIN]"]}]}]
    for _ in range(2):
        await stuck.check_instance(ctx, "sonarr-series")
        ctx.db.execute("UPDATE stuck_import_actions SET first_seen_at=?", (iso(-1),))
    assert not sonarr.calls_to("DELETE", "/api/v3/queue/8")


def test_partial_seerr_listing_never_marks_requests_deleted():
    """Alleen een volledige lijst telt; een lege of afgebroken lijst nooit (storing)."""
    import asyncio

    import httpx

    from mediaguard.seerr.client import SeerrClient

    pages = {0: [{"id": 1}, {"id": 2}], 2: []}

    def handler(request: httpx.Request) -> httpx.Response:
        skip = int(request.url.params.get("skip", 0))
        return httpx.Response(200, json={"pageInfo": {"results": 5},
                                         "results": pages.get(skip, [])})

    client = SeerrClient("http://seerr", "k", transport=httpx.MockTransport(handler))
    out = asyncio.run(client.requests(take=2))
    assert len(out) == 2 and client.requests_complete is False
    pages[2] = [{"id": 3}, {"id": 4}]
    pages[4] = [{"id": 5}]
    out = asyncio.run(client.requests(take=2))
    assert len(out) == 5 and client.requests_complete is True


async def test_deletion_between_pages_never_stops_a_live_request(env):
    """Verwijdert iemand in Seerr een aanvraag terwijl MediaGuard de lijst ophaalt, dan
    schuift alles één plek op en valt er op de tweede pagina een levende aanvraag weg. Die
    mag niet als 'verwijderd' gelden: MediaGuard vraagt elke ontbrekende aanvraag nog na."""
    from mediaguard.seerr.service import sync_requests

    ctx, _arr, seerr = env
    seerr.requests.extend(seerr_request(i, tv=False, ext=70000 + i) for i in range(1, 151))
    await sync_requests(ctx)
    real = seerr.asgi

    async def racing(scope, receive, send):  # type: ignore[no-untyped-def]
        # na de eerste pagina verdwijnt aanvraag 1 in Seerr
        await real(scope, receive, send)
        if scope.get("path") == "/api/v1/request" and b"skip=0" in scope.get("query_string",
                                                                              b""):
            seerr.requests[:] = [r for r in seerr.requests if r["id"] != 1]

    import httpx

    ctx.services["seerr_transport"] = httpx.ASGITransport(app=racing)
    def gone() -> set[int]:
        return {r["seerr_request_id"] for r in ctx.db.fetch_dicts(
            "SELECT seerr_request_id FROM seerr_requests WHERE deleted_at IS NOT NULL")}

    counts = await sync_requests(ctx)
    assert counts["total"] == 149  # 101 werd overgeslagen, maar Seerr zegt: bestaat nog
    assert any(c[1] == "/api/v1/request/101" for c in seerr.calls)
    assert gone() == set()
    ctx.services["seerr_transport"] = seerr.transport()
    counts = await sync_requests(ctx)
    assert gone() == {1} and counts["deleted"] == 1


async def test_wrongly_stopped_request_is_followed_again(env):
    """Een titel die stopte omdat zijn aanvraag 'verwijderd' leek, volgt MediaGuard weer
    zodra die aanvraag weer in Seerr staat (en niet afgewezen is)."""
    ctx, _arr, seerr = env
    seerr.requests.append(seerr_request(80, tv=False, ext=80080))
    await engine.discover(ctx)
    rid = item(ctx)["id"]
    ctx.db.execute("UPDATE seerr_requests SET deleted_at=? WHERE seerr_request_id=80",
                   (utcnow(),))
    engine._stop_withdrawn(ctx)
    assert item(ctx)["state"] == "stopped"
    await engine.discover(ctx)  # de volgende ronde ziet hem gewoon weer
    row = item(ctx)
    assert row["state"] != "stopped" and not row["stop_code"]
    assert any("weer in Seerr" in e["detail_nl"] for e in store.events(ctx, rid))
    # afgewezen blijft gestopt
    seerr.requests[0]["status"] = 3
    await engine.discover(ctx)
    assert item(ctx)["state"] == "stopped"
    await engine.discover(ctx)
    assert item(ctx)["state"] == "stopped"


async def test_replacement_cut_off_halfway_is_recovered(env, lib):
    """Valt MediaGuard weg nadat Sonarr het bestand weghaalde maar vóór het afronden, dan
    zet recover_interrupted het origineel terug (de journal replay van het recovery-proces
    blijft van een lopende vervanging af: die staat op 'pending')."""
    ctx, _arr, _seerr = env
    from mediaguard.delivery import replace
    from mediaguard.recovery import executor

    sonarr, path = episode_setup(env, lib)

    class Crash(BaseException):
        pass

    def delete(_b: Any) -> tuple[int, Any]:
        os.unlink(path)
        raise Crash

    sonarr.intercept[("DELETE", "/api/v3/episodefile/3001")] = delete
    with pytest.raises(BaseException):  # noqa: B017 - nagebootste stroomstoring
        await replace.replace_wrong_file(ctx, instance_id="sonarr-series",
                                         kind="episode_file", file_id=3001, reason_nl="x")
    assert not path.exists()
    txn = ctx.db.fetch_one("SELECT id, state FROM replacement_transactions")
    assert txn["state"] == "pending"
    assert executor.recover_pending(ctx) == []  # recovery-proces laat hem met rust
    assert replace.recover_interrupted(ctx) == 0  # te vers en slot nog bezet
    ctx.db.execute("DELETE FROM locks")
    ctx.db.execute("UPDATE replacement_transactions SET updated_at=?", (iso(-1),))
    assert replace.recover_interrupted(ctx) == 1
    assert path.read_bytes() == b"verkeerde aflevering" * 100
    assert ctx.db.fetch_one("SELECT state FROM replacement_transactions")["state"] == \
        "rolled_back"


# ── controle-ronde: gaten die de review vond ─────────────────────────────────────────────
async def test_doubtful_mismatch_is_never_removed(env, lib):
    """Alleen eenduidig bewijs ('actionable') mag naar de quarantaine; tegenstrijdig bewijs
    gaat naar de AI of de gebruiker en het bestand blijft staan."""
    ctx, _arr, _seerr = env
    sonarr, path = episode_setup(env, lib, actionable=False)
    await engine.run_once(ctx, force=True)
    assert path.exists() and not sonarr.calls_to("DELETE", "/api/v3/episodefile")
    assert not ctx.db.fetch_one("SELECT 1 FROM quarantine_items")
    row = item(ctx)
    assert "niet eenduidig" in row["detail_nl"] and "beoordeling" in row["next_step_nl"]
    from mediaguard.delivery import replace

    res = await replace.replace_wrong_file(ctx, instance_id="sonarr-series",
                                           kind="episode_file", file_id=3001, reason_nl="x")
    assert res["status"] == "review" and path.exists()


async def test_full_quarantine_on_degraded_pool_leaves_wrong_file_in_place(env, lib,
                                                                          monkeypatch):
    """Samenspel 12 + 37: is de quarantaine vol en kan opruimen geen ruimte maken (TrueNAS-pool
    niet gezond), dan zet MediaGuard ook geen verkeerd bestand meer in de quarantaine. Het
    bestand blijft staan; de levering probeert het later opnieuw."""
    ctx, _arr, _seerr = env
    from mediaguard import storage_forecast
    from mediaguard.recovery import release_check
    from mediaguard.recovery import service as recovery_service

    sonarr, path = episode_setup(env, lib)
    monkeypatch.setattr(release_check, "quarantine_usage_bytes", lambda _c: 600)
    monkeypatch.setattr(recovery_service, "effective_cap_bytes", lambda _c: 500)
    monkeypatch.setattr(recovery_service, "purge_candidates",
                        lambda _c, **_k: [{"id": 1, "size": 200}])
    storage_forecast.record_pool(ctx, {"ok": False, "pools": [
        {"name": "voorbeeldpool", "status": "DEGRADED", "healthy": False}]})
    await engine.run_once(ctx, force=True)
    assert path.exists() and not sonarr.calls_to("DELETE", "/api/v3/episodefile")
    assert not ctx.db.fetch_one("SELECT 1 FROM quarantine_items")
    assert "quarantaine is vol" in item(ctx)["detail_nl"]


async def test_wrong_file_in_quarantine_passes_release_check_and_can_be_purged(env, lib):
    """Zonder dit faalde de vrijgavecontrole altijd ('nieuw bestand staat niet op zijn
    plek'): het verkeerde bestand bleef dan eeuwig in de quarantaine staan."""
    ctx, _arr, _seerr = env
    from mediaguard.recovery import release_check, service

    _sonarr, _path = episode_setup(env, lib)
    await engine.run_once(ctx, force=True)
    q = ctx.db.fetch_one("SELECT * FROM quarantine_items")
    res = release_check.run_check(ctx, int(q["id"]))
    assert res["status"] == "passed", res["checks"]
    assert [c["name"] for c in res["checks"]] == ["verkeerd-bestand"]
    # bewaartermijn verstreken → gewone opruimkandidaat (zelfde regels als elk origineel)
    ctx.db.execute("UPDATE quarantine_items SET created_at=?", (iso(-400),))
    assert [c["id"] for c in service.purge_candidates(ctx)] == [q["id"]]
    # bewaarde kopie verdwenen buiten MediaGuard om: eerlijk 'mislukt', nooit 'geslaagd'
    Path(q["quarantine_path"]).unlink()
    assert release_check.run_check(ctx, int(q["id"]))["status"] == "failed"


async def test_new_request_after_removal_is_followed_again(env):
    ctx, _arr, seerr = env
    sonarr = series_with_request(env, 50, 50050, 50)
    await engine.run_once(ctx, force=True)
    del sonarr.series[50]
    await engine.run_once(ctx, force=True)
    assert item(ctx)["state"] == "stopped"
    # iemand vraagt hem opnieuw aan: niet meteen weer 'verwijderd', maar gewoon volgen
    seerr.requests.append(seerr_request(51, tv=True, ext=50050, seasons=[1], status=1))
    await engine.run_once(ctx, force=True)
    row = item(ctx)
    assert row["state"] == "active" and row["stage"] == "adding", row["detail_nl"]
    assert seerr_calls(seerr, "/request/51/approve")


async def test_retry_now_on_removed_title_asks_seerr_once(env, client):  # type: ignore[no-untyped-def]
    ctx, _arr, seerr = env
    sonarr = series_with_request(env, 52, 52052, 52)
    await engine.run_once(ctx, force=True)
    del sonarr.series[52]
    await engine.run_once(ctx, force=True)
    rid = item(ctx)["id"]
    assert item(ctx)["state"] == "stopped"
    r = client.post(f"/api/v1/delivery/requests/{rid}/retry")
    assert r.status_code == 200 and r.json()["state"] == "active"
    assert len(seerr_calls(seerr, "/request/52/retry")) == 1
    events = [e["detail_nl"] for e in store.events(ctx, rid)]
    assert any("Op jouw verzoek" in e for e in events)
    # daarna niet uit zichzelf steeds opnieuw (goedgekeurd = Seerr verstuurt hem zelf)
    for _ in range(3):
        await engine.run_once(ctx, force=True)
    assert len(seerr_calls(seerr, "/request/52/retry")) == 1


async def test_unreachable_instance_never_counts_as_removed(env):
    """Lag de andere instantie even plat, dan is 'nergens gevonden' niet zeker: niet stoppen."""
    ctx, arr, _seerr = env
    sonarr = series_with_request(env, 53, 53053, 53)
    await engine.run_once(ctx, force=True)
    del sonarr.series[53]
    arr["sonarr-anime"].fail_paths["/api/v3/series"] = 500
    await engine.run_once(ctx, force=True)
    row = item(ctx)
    assert row["state"] == "active" and "niet bereikbaar" in row["detail_nl"]
    arr["sonarr-anime"].fail_paths.clear()
    await engine.run_once(ctx, force=True)
    assert item(ctx)["state"] == "stopped"


async def test_delivered_title_stays_delivered_when_request_is_cleaned_up(env):
    ctx, arr, seerr = env
    arr["radarr-films"].movies[11] = make_movie(11, "Klaar", 11011, has_file=True)
    seerr.requests.append(seerr_request(54, tv=False, ext=11011))
    await engine.run_once(ctx, force=True)
    assert item(ctx)["state"] == "delivered"
    seerr.requests.clear()
    seerr.requests.append(seerr_request(55, tv=False, ext=99999))  # lijst niet leeg
    await engine.run_once(ctx, force=True)
    assert ctx.db.fetch_one("SELECT deleted_at FROM seerr_requests WHERE "
                            "seerr_request_id=54")["deleted_at"]
    by_title = {r["title"]: r for r in store.list_items(ctx)}
    assert by_title["Klaar"]["state"] == "delivered"


async def test_migrated_titles_already_in_tracker_stop(env):
    """Vóór deze versie kwamen gemigreerde titels als 'aanvraag' in de tracker; die volgen
    we niet meer. Een echte Seerr-aanvraag voor zo'n titel blijft gewoon gevolgd."""
    ctx, _arr, _seerr = env
    run = ctx.db.execute_insert("INSERT INTO migration_runs(name,created_at,updated_at) "
                                "VALUES ('m',?,?)", (utcnow(), utcnow()))
    for pid in (61, 62):
        ctx.db.execute("INSERT INTO instance_migration_checkpoints(run_id,instance_id,phase,"
                       "kind,source_key,target_ref,updated_at) VALUES (?,'sonarr-series',"
                       "'import','series',?,?,?)", (run, str(pid), str(pid), utcnow()))
    old = store.upsert(ctx, media_type="series", tmdb_id=None, tvdb_id=6061, title="Oud",
                       source="arr", instance_id="sonarr-series", provider_id=61)
    asked = store.upsert(ctx, media_type="series", tmdb_id=None, tvdb_id=6062, title="Gevraagd",
                         source="arr", instance_id="sonarr-series", provider_id=62)
    store.upsert(ctx, media_type="series", tmdb_id=None, tvdb_id=6062, title="Gevraagd",
                 source="seerr", seerr_request_id=62)
    await engine.discover(ctx)
    assert store.get(ctx, int(old))["state"] == "stopped"  # type: ignore[arg-type]
    assert "Gemigreerde titel" in store.get(ctx, int(old))["detail_nl"]  # type: ignore[index]
    assert store.get(ctx, int(asked))["state"] == "active"  # type: ignore[arg-type,index]


def test_delivery_search_log_stays_small(ctx):  # type: ignore[no-untyped-def]
    ctx.db.execute("INSERT INTO delivery_searches(item_id,instance_id,ts,command,cost) "
                   "VALUES (NULL,'sonarr-series',?,'EpisodeSearch',3)", (iso(-30),))
    store.record_search(ctx, "sonarr-series", "EpisodeSearch", 2)
    rows = ctx.db.fetch_dicts("SELECT cost FROM delivery_searches")
    assert [r["cost"] for r in rows] == [2]


def test_old_import_failed_state_is_left_to_the_stuck_checker():
    assert engine._queue_state({"trackedDownloadState": "importFailed",
                                "trackedDownloadStatus": "error"}) == "importing"
    assert engine._queue_state({"trackedDownloadState": "failedPending"}) == "failed"
