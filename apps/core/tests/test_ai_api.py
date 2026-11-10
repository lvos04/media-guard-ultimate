"""AI Adviseur — UI/API-flow (S5.10 op API-niveau): providerconfig, modelkeuze, toestemming,
preview, advies, feedback, goedkeuren/afwijzen, budgetlimiet en uitschakelen."""

from __future__ import annotations

import json

import httpx
import respx
from test_ai_support import ANTHROPIC_URL, FAKE_KEY, anime_case, anthropic_handler

from mediaguard.ai import register_action, register_evidence, request_advice

MODELS_URL = "https://api.anthropic.com/v1/models"


def test_full_ui_flow(client, ctx):  # type: ignore[no-untyped-def]
    ctx.services["ai_retry_delay"] = 0.0
    # 1. standaard uit, begrijpelijke status
    s = client.get("/api/v1/ai/settings").json()
    assert s["values"]["ai.mode"] == "disabled" and s["values"]["ai.provider"] == "off"
    assert s["values"]["ai.budget.monthly_limit"] == 5.0
    assert s["status"]["active"] is False and "zonder AI" in s["status"]["message_nl"]
    assert all(d["label_nl"] for d in s["schema"])

    # 2. inschakelen zonder voorwaarden → 409 met lijst
    r = client.patch("/api/v1/ai/settings", json={"values": {"ai.mode": "advisory",
                                                             "ai.provider": "anthropic"}})
    assert r.status_code == 409
    blockers = r.json()["detail"]["blockers"]
    assert any("API-sleutel" in b for b in blockers) and any("Toestemming" in b for b in blockers)

    # 3. provider + sleutel (write-only)
    r = client.patch("/api/v1/ai/settings", json={"values": {"ai.provider": "anthropic"},
                                                  "api_keys": {"anthropic": FAKE_KEY}})
    assert r.status_code == 200 and FAKE_KEY not in r.text
    assert r.json()["api_keys"]["anthropic"] is True
    prov = client.get("/api/v1/ai/providers")
    assert FAKE_KEY not in prov.text
    assert prov.json()["providers"][0]["api_key_set"] is True

    # 4. testcall zonder toestemming → geweigerd zonder externe call
    with respx.mock(assert_all_called=False) as mock:
        route = mock.post(ANTHROPIC_URL)
        t = client.post("/api/v1/ai/providers/anthropic/test", json={}).json()
        assert t["ok"] is False and not route.called
    # 5. toestemming
    c = client.post("/api/v1/ai/consent", json={"provider": "anthropic", "granted": True})
    assert c.json()["granted"] is True
    assert "nooit videobestanden" in client.get("/api/v1/ai/consent").json()["text_nl"]

    # 6. live modellijst + modelkeuze
    with respx.mock() as mock:
        mock.get(MODELS_URL).mock(return_value=httpx.Response(200, json={
            "data": [{"id": "claude-haiku-5-5"}, {"id": "claude-sonnet-5-5"}],
            "has_more": False}))
        prov = client.get("/api/v1/ai/providers", params={"refresh": "true"}).json()
    anth = prov["providers"][0]
    assert [m["id"] for m in anth["models"]] == ["claude-haiku-5-5", "claude-sonnet-5-5"]
    assert anth["model"] == "claude-haiku-5-5" and anth["price_known"] is True

    # 7. echte (gemockte) gestructureerde testcall
    with respx.mock() as mock:
        route = mock.post(ANTHROPIC_URL).mock(side_effect=anthropic_handler())
        t = client.post("/api/v1/ai/providers/anthropic/test", json={}).json()
        assert route.called
        sent = json.loads(route.calls[0].request.content)
        assert sent["output_config"]["format"]["type"] == "json_schema"
        assert route.calls[0].request.headers["anthropic-version"] == "2023-06-01"
    assert t["ok"] is True, t
    # 8. nu kan advies aan; bounded_auto blijft vergrendeld
    assert client.patch("/api/v1/ai/settings",
                        json={"values": {"ai.mode": "advisory"}}).status_code == 200
    r = client.patch("/api/v1/ai/settings", json={"values": {"ai.mode": "bounded_auto"}})
    assert r.status_code == 409 and "benchmark" in r.text.lower()
    assert client.get("/api/v1/ai/settings").json()["status"]["active"] is True

    # 9. casus + preview
    register_evidence(ctx, "ev-1", "thexem_mapping", "scene S02E01 = tvdb S01E13")
    register_evidence(ctx, "ev-2", "release_name", "[SubsPlease] Voorbeeld - 13")
    ticket = request_advice(ctx, "anime_identity", anime_case(), ["ev-1", "ev-2"],
                            "CONFLICTING_EPISODE_MAP", target_type="episode",
                            target_id="sonarr-anime:42", instance_id="sonarr-anime")
    assert ticket["status"] == "queued"
    case_id = ticket["case_id"]
    pv = client.get(f"/api/v1/ai/preview/{case_id}")
    assert pv.status_code == 200
    assert "/mnt/tank" not in pv.text and FAKE_KEY not in pv.text
    assert pv.json()["headers"]["x-api-key"] == "***"
    assert pv.json()["url"] == ANTHROPIC_URL
    assert pv.json()["minimization"]["paths_shortened"] >= 1

    # 10. analyseren en tonen
    with respx.mock() as mock:
        mock.post(ANTHROPIC_URL).mock(side_effect=anthropic_handler())
        a = client.post(f"/api/v1/ai/cases/{case_id}/analyze", json={}).json()
    assert a["result"]["status"] == "auto_resolved"
    detail = client.get(f"/api/v1/ai/cases/{case_id}").json()
    assert "needs_advice-code CONFLICTING_EPISODE_MAP" in detail["why_ai_nl"]
    assert detail["proposals"][0]["proposal"]["evidence_ids"] == ["ev-1", "ev-2"]
    assert detail["proposals"][0]["policy_results"][0]["decision"] == "await_approval"
    assert detail["model"] == "claude-haiku-5-5" and detail["cost"] > 0
    assert detail["can_approve"] is True
    listing = client.get("/api/v1/ai/cases").json()["items"]
    assert listing[0]["id"] == case_id

    # 11. feedback + goedkeuren voert de geregistreerde, niet-destructieve actie uit
    fb = client.post(f"/api/v1/ai/cases/{case_id}/feedback",
                     json={"verdict": "incorrect", "comment": "zie /mnt/tank/x.mkv"})
    assert fb.status_code == 200
    stored = ctx.db.fetch_one("SELECT comment FROM ai_feedback")["comment"]
    assert "/mnt/tank" not in stored
    done: list = []
    register_action(ctx, "inspect_mapping", lambda c, r: done.append(r) or {"refreshed": True})
    ap = client.post(f"/api/v1/ai/cases/{case_id}/approve")
    assert ap.status_code == 200 and ap.json()["result"]["status"] == "executed"
    assert done[0].target_id == "sonarr-anime:42" and done[0].via == "approve"
    assert client.post(f"/api/v1/ai/cases/{case_id}/approve").status_code == 409

    # 12. afwijzen
    t2 = request_advice(ctx, "anime_identity", {"other": 1}, [], "ALIAS_CONFLICT")
    with respx.mock() as mock:
        mock.post(ANTHROPIC_URL).mock(side_effect=anthropic_handler())
        client.post(f"/api/v1/ai/cases/{t2['case_id']}/analyze", json={})
    rj = client.post(f"/api/v1/ai/cases/{t2['case_id']}/reject", json={"reason": "fout"})
    assert rj.json()["status"] == "rejected"

    # 13. gebruik en kosten
    usage = client.get("/api/v1/ai/usage").json()
    assert usage["spent"] > 0 and usage["currency"] == "EUR" and usage["monthly_limit"] == 5.0
    assert "meegeleverd" in usage["price_note_nl"]
    assert usage["calls_today"] >= 3

    # 14. budgetlimiet via UI
    assert client.patch("/api/v1/ai/settings", json={
        "values": {"ai.budget.monthly_limit": usage["spent"]}}).status_code == 200
    t3 = request_advice(ctx, "anime_identity", {"budget": 1}, [], "ALIAS_CONFLICT")
    with respx.mock(assert_all_called=False) as mock:
        route = mock.post(ANTHROPIC_URL)
        res = client.post(f"/api/v1/ai/cases/{t3['case_id']}/analyze", json={}).json()
        assert not route.called
    assert res["result"]["status"] == "skipped" and "Maandbudget" in res["result"]["detail"]

    # 15. validatie van prijstabel / onbekende keys
    assert client.patch("/api/v1/ai/settings", json={
        "values": {"ai.price_table": {"zonder-provider": {}}}}).status_code == 422
    assert client.patch("/api/v1/ai/settings", json={
        "values": {"ui.theme": "light"}}).status_code == 422
    assert client.patch("/api/v1/ai/settings", json={"values": {
        "ai.price_table": {"anthropic:claude-haiku-5-5": {"input_per_mtok": 0.2,
                                                          "output_per_mtok": 1.0,
                                                          "currency": "USD"}}}}
    ).status_code == 200

    # 16. uitschakelen en toestemming intrekken
    assert client.patch("/api/v1/ai/settings",
                        json={"values": {"ai.mode": "disabled"}}).status_code == 200
    assert request_advice(ctx, "anime_identity", {"off": 1}, [],
                          "ALIAS_CONFLICT")["status"] == "skipped"
    client.patch("/api/v1/ai/settings", json={"values": {"ai.mode": "advisory"}})
    client.post("/api/v1/ai/consent", json={"provider": "anthropic", "granted": False})
    assert client.get("/api/v1/ai/settings").json()["values"]["ai.mode"] == "disabled"


def test_benchmark_endpoint_and_evaluations(client, ctx):  # type: ignore[no-untyped-def]
    from test_ai_support import configure

    configure(ctx, "shadow")
    with respx.mock() as mock:
        mock.post(ANTHROPIC_URL).mock(side_effect=anthropic_handler(
            suggested_action="manual_review", reason_codes=["INSUFFICIENT_EVIDENCE"]))
        r = client.post("/api/v1/ai/benchmark", json={"limit": 3}).json()
    assert r["ok"] and r["total"] == 3 and r["passed"] is False
    assert "vergrendeld" in r["message_nl"]
    evals = client.get("/api/v1/ai/evaluations").json()
    assert evals[0]["total"] == 3


def test_unknown_cases_and_auth(client, anon_client):  # type: ignore[no-untyped-def]
    assert anon_client.get("/api/v1/ai/settings").status_code == 401
    assert client.get("/api/v1/ai/cases").status_code == 200
    assert client.get("/api/v1/ai/preview/bestaat-niet").status_code == 404
    assert client.post("/api/v1/ai/cases/bestaat-niet/approve").status_code == 404
