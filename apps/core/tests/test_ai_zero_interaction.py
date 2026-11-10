"""Zero-interactie: niets in de AI-flow wacht nog op de gebruiker."""

from __future__ import annotations

import respx
from test_ai_advisor import _unlock
from test_ai_support import ANTHROPIC_URL, anthropic_handler, configure

from mediaguard import attention
from mediaguard.ai import ActionRequest, analyze_case, register_action, request_advice, service


def test_mode_is_automatic_once_configured(ctx):  # type: ignore[no-untyped-def]
    configure(ctx, "advisory")
    assert service.current_mode(ctx) == "bounded_auto"  # advies wacht nooit meer op een klik
    ctx.config.reset("global", "", "ai.mode")  # nooit bewust gekozen → automatisch (veilig)
    assert service.current_mode(ctx) == "bounded_auto"
    ctx.config.set("global", "", "ai.mode", "disabled", "user:test")  # bewust uit = uit
    assert service.current_mode(ctx) == "disabled"
    ctx.config.set("global", "", "ai.mode", "shadow", "user:test")
    assert service.current_mode(ctx) == "shadow"


def test_without_consent_never_automatic(ctx):  # type: ignore[no-untyped-def]
    configure(ctx, "advisory")
    ctx.config.reset("global", "", "ai.mode")
    service.record_consent(ctx, "anthropic", False, "user:test")
    assert service.current_mode(ctx) == "disabled"


async def test_locked_proposal_resolves_to_safe_default(ctx):  # type: ignore[no-untyped-def]
    configure(ctx, "advisory")
    calls: list[ActionRequest] = []
    register_action(ctx, "request_search", lambda c, r: calls.append(r) or {"queued": 1})
    with respx.mock() as mock:
        mock.post(ANTHROPIC_URL).mock(side_effect=anthropic_handler())
        t = request_advice(ctx, "missing_diagnosis", {"z": 1}, [], "MULTIPLE_ROOT_CAUSES")
        result = await analyze_case(ctx, t["case_id"])
    assert result["status"] == "auto_resolved" and calls == []
    assert result["detail"].startswith("Veilige standaard aangehouden")
    assert "automatisch zoeken" in result["detail"]
    audit = ctx.db.fetch_dicts("SELECT action FROM audit_events WHERE target_id=?", (t["case_id"],))
    assert any(a["action"] == "ai.case.auto_resolved" for a in audit)


async def test_non_executable_action_never_runs(ctx):  # type: ignore[no-untyped-def]
    configure(ctx, "bounded_auto")
    _unlock(ctx)
    calls: list[ActionRequest] = []
    for name in ("request_search", "retry_later", "inspect_mapping"):
        register_action(ctx, name, lambda c, r: calls.append(r) or {"ok": 1})
    for action in ("adjust_filter", "manual_review"):
        with respx.mock() as mock:
            mock.post(ANTHROPIC_URL).mock(side_effect=anthropic_handler(suggested_action=action))
            t = request_advice(ctx, "missing_diagnosis", {"a": action}, [],
                               "MULTIPLE_ROOT_CAUSES")
            r = await analyze_case(ctx, t["case_id"])
        assert r["status"] == "auto_resolved"
    assert calls == []


async def test_daily_max_defers_to_tomorrow(ctx):  # type: ignore[no-untyped-def]
    configure(ctx, "bounded_auto")
    _unlock(ctx)
    ctx.config.set("global", "", "ai.bounded_auto.max_actions_per_day", 1, "test")
    calls: list[ActionRequest] = []
    register_action(ctx, "request_search", lambda c, r: calls.append(r) or {"queued": 1})
    with respx.mock() as mock:  # eerste actie van vandaag: gewoon uitgevoerd
        mock.post(ANTHROPIC_URL).mock(side_effect=anthropic_handler())
        t0 = request_advice(ctx, "missing_diagnosis", {"d": 0}, [], "MULTIPLE_ROOT_CAUSES")
        assert (await analyze_case(ctx, t0["case_id"]))["status"] == "auto_executed"
    calls.clear()
    with respx.mock() as mock:
        mock.post(ANTHROPIC_URL).mock(side_effect=anthropic_handler())
        t = request_advice(ctx, "missing_diagnosis", {"d": 1}, [], "MULTIPLE_ROOT_CAUSES")
        r = await analyze_case(ctx, t["case_id"])
    assert r["status"] == "deferred" and calls == []
    job = ctx.db.fetch_one("SELECT run_after FROM jobs WHERE job_type='ai.analyze' AND "
                           "target_id=? AND run_after IS NOT NULL", (t["case_id"],))
    assert job is not None
    # morgen: dagmaximum weer vrij → wordt (uit de cache, zonder kosten) uitgevoerd
    ctx.config.set("global", "", "ai.bounded_auto.max_actions_per_day", 10, "test")
    with respx.mock(assert_all_called=False) as mock:
        route = mock.post(ANTHROPIC_URL).mock(side_effect=anthropic_handler())
        r2 = await analyze_case(ctx, t["case_id"])
        assert not route.called
    assert r2["status"] == "auto_executed" and len(calls) == 1


def test_old_waiting_cases_are_resolved_on_startup(ctx):  # type: ignore[no-untyped-def]
    configure(ctx, "advisory")
    ctx.db.execute(
        "INSERT INTO ai_cases(id,task_type,reason_code,status,payload_json,payload_hash,"
        "created_at,updated_at) VALUES ('old1','anime_identity','CONFLICTING_EPISODE_MAP',"
        "'awaiting_approval','{}','hx','2026-11-01','2026-11-01')")
    assert service.on_startup(ctx)["auto_resolved"] == 1
    row = ctx.db.fetch_one("SELECT status, status_detail FROM ai_cases WHERE id='old1'")
    assert row["status"] == "auto_resolved"
    assert "huidige koppeling blijft" in row["status_detail"]


def test_no_attention_item_for_ai(ctx):  # type: ignore[no-untyped-def]
    configure(ctx, "advisory")
    ctx.db.execute(
        "INSERT INTO ai_cases(id,task_type,reason_code,status,payload_json,payload_hash,"
        "created_at,updated_at) VALUES ('w1','anime_identity','CONFLICTING_EPISODE_MAP',"
        "'awaiting_approval','{}','hw','2026-11-01','2026-11-01')")
    assert attention._ai(ctx) == []


async def test_benchmark_scheduled_automatically_when_shadow_full(ctx):  # type: ignore[no-untyped-def]
    configure(ctx, "bounded_auto")
    _unlock(ctx)
    ctx.db.execute("DELETE FROM ai_model_evaluations")  # schaduwfase vol, nog geen benchmark
    with respx.mock() as mock:
        mock.post(ANTHROPIC_URL).mock(side_effect=anthropic_handler())
        t = request_advice(ctx, "missing_diagnosis", {"b": 1}, [], "MULTIPLE_ROOT_CAUSES")
        r = await analyze_case(ctx, t["case_id"])
    assert r["status"] == "auto_resolved"
    jobs = ctx.db.fetch_dicts("SELECT job_type FROM jobs WHERE job_type='ai.benchmark'")
    assert len(jobs) == 1
