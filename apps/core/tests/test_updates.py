"""Updatemelding (verbeterlijst 23): hooguit dagelijks GitHub controleren, één melding per
nieuwe versie, punt in 'Wacht op jou' en het veld voor de Android-app."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import respx

from mediaguard import __version__, updates


def _next(version: str = __version__) -> str:
    parts = [int(p) for p in version.split(".")]
    parts[-1] += 1
    return ".".join(str(p) for p in parts)


def _release(version: str, *, apk: bool = True, **extra):
    assets = [{"name": f"mediaguard-android-{version}.apk",
               "browser_download_url": "https://github.com/lvos04/media-guard-ultimate/"
                                       f"releases/download/v{version}/mediaguard-android-"
                                       f"{version}.apk"}] if apk else []
    return {"tag_name": f"v{version}", "draft": False, "prerelease": False,
            "html_url": f"https://github.com/lvos04/media-guard-ultimate/releases/tag/v{version}",
            "published_at": "2026-11-09T10:00:00Z", "assets": assets, **extra}


def _check(ctx, status: int = 200, payload=None):
    with respx.mock(assert_all_called=True) as mock:
        route = mock.get(updates.LATEST_URL).respond(status, json=payload or {})
        result = asyncio.run(updates.check(ctx))
        assert route.called
    return result


def _notifications(ctx, version: str):
    return ctx.db.fetch_all("SELECT * FROM notifications WHERE dedupe_key=?",
                            (f"update:{version}",))


def test_version_compare_is_numeric_and_ignores_prereleases():
    assert updates.is_newer("v1.2.10", "1.2.9")
    assert updates.is_newer("1.3", "1.2.9")
    assert not updates.is_newer("1.2.1", "1.2.1")
    assert not updates.is_newer("1.2.0", "1.2.1")
    assert not updates.is_newer("1.3.0-rc1", "1.2.1")
    assert not updates.is_newer(None, "1.2.1") and not updates.is_newer("onzin", "1.2.1")


def test_release_info_takes_apk_and_rejects_drafts():
    info = updates.release_info(_release("1.2.2"))
    assert info["latest"] == "1.2.2"
    assert info["android"]["latest"] == "1.2.2"
    assert info["android"]["url"].endswith("/mediaguard-android-1.2.2.apk")
    assert updates.release_info(_release("1.2.2", apk=False))["android"] is None
    for bad in (_release("1.2.2", prerelease=True), _release("1.2.2", draft=True),
                {"tag_name": "nightly"}, ["geen", "object"]):
        try:
            updates.release_info(bad)
        except ValueError:
            continue
        raise AssertionError(bad)


def test_new_version_gives_one_notification_attention_item_and_app_field(client, ctx):
    newer = _next()
    assert client.get("/api/v1/system/update").json()["update_available"] is False
    _check(ctx, payload=_release(newer))
    v = client.get("/api/v1/system/update").json()
    assert v["enabled"] is True and v["current"] == __version__ and v["latest"] == newer
    assert v["update_available"] is True and v["image"].endswith(f":{newer}-aio")
    assert v["url"].startswith("https://github.com/") and v["error_nl"] is None
    assert v["android"]["latest"] == newer and v["next_check_at"]
    rows = _notifications(ctx, newer)
    assert len(rows) == 1 and newer in rows[0]["title"] and rows[0]["link"] == "/systeem#updates"
    items = [i for i in client.get("/api/v1/attention").json()["items"]
             if i["key"] == "system.update_available"]
    assert len(items) == 1 and items[0]["severity"] == "info"
    assert items[0]["actions"][0]["path"] == "/system/update/dismiss"
    # ook in het compacte app-antwoord
    status = client.get("/api/v1/app/status").json()
    assert any(i.get("key") == "system.update_available" for i in status["attention"]["items"])
    # dezelfde versie opnieuw gezien: geen tweede melding
    _check(ctx, payload=_release(newer))
    assert len(_notifications(ctx, newer)) == 1


def test_failed_check_keeps_last_result_and_waits_a_day(client, ctx):
    newer = _next()
    _check(ctx, payload=_release(newer))
    _check(ctx, status=503)
    v = client.get("/api/v1/system/update").json()
    assert v["latest"] == newer and v["update_available"] is True
    assert "Controleren lukte niet" in v["error_nl"] and "503" in v["error_nl"]
    assert not updates.due(ctx)  # ook na een mislukte poging pas morgen weer
    with respx.mock() as mock:
        mock.get(updates.LATEST_URL).mock(side_effect=httpx.ConnectError("geen netwerk"))
        asyncio.run(updates.check(ctx))
    assert "niet bereikbaar" in client.get("/api/v1/system/update").json()["error_nl"]
    # gelukt daarna: de fout verdwijnt
    _check(ctx, payload=_release(newer))
    assert client.get("/api/v1/system/update").json()["error_nl"] is None


def test_at_most_daily_and_never_when_disabled(ctx):
    assert updates.due(ctx)  # nog nooit gecontroleerd
    old = (datetime.now(UTC) - timedelta(hours=23)).isoformat(timespec="seconds")
    ctx.state_set(updates.STATE_KEY, {"attempted_at": old})
    assert not updates.due(ctx)
    assert updates.due(ctx, now=datetime.now(UTC) + timedelta(hours=2))
    ctx.config.set("global", "", updates.SETTING, False, "test")
    assert not updates.due(ctx, now=datetime.now(UTC) + timedelta(days=3))


def test_disabled_hides_everything_and_refuses_manual_check(client, ctx):
    _check(ctx, payload=_release(_next()))
    r = client.patch("/api/v1/settings/scoped", json={
        "changes": [{"scope_type": "global", "scope_id": "", "key": updates.SETTING,
                     "value": False}], "reason": "test"})
    assert r.status_code == 200, r.text
    v = client.get("/api/v1/system/update").json()
    assert v["enabled"] is False and v["update_available"] is False and v["latest"] is None
    assert v["android"] is None and v["image"] is None
    assert not [i for i in client.get("/api/v1/attention").json()["items"]
                if i["key"] == "system.update_available"]
    assert client.post("/api/v1/system/update/check").status_code == 409


def test_manual_check_and_throttle(client, ctx):
    newer = _next()
    with respx.mock() as mock:
        route = mock.get(updates.LATEST_URL).respond(200, json=_release(newer))
        assert client.post("/api/v1/system/update/check").json()["latest"] == newer
        # binnen een minuut nogmaals: geen nieuw verzoek aan GitHub
        assert client.post("/api/v1/system/update/check").json()["latest"] == newer
        assert route.call_count == 1


def test_dismiss_hides_item_until_next_version(client, ctx):
    newer = _next()
    _check(ctx, payload=_release(newer))
    assert client.post("/api/v1/system/update/dismiss",
                       json={"version": "0.0.1"}).status_code == 409
    v = client.post("/api/v1/system/update/dismiss", json={"version": newer}).json()
    assert v["dismissed"] is True and v["update_available"] is True
    assert not [i for i in client.get("/api/v1/attention").json()["items"]
                if i["key"] == "system.update_available"]
    # een nog nieuwere versie komt wel weer in beeld, met een eigen melding
    newest = _next(newer)
    _check(ctx, payload=_release(newest))
    assert [i for i in client.get("/api/v1/attention").json()["items"]
            if i["key"] == "system.update_available"]
    assert len(_notifications(ctx, newest)) == 1
    # de melding over de vorige versie is afgehandeld
    assert _notifications(ctx, newer)[0]["resolved_at"] is not None


def test_same_or_older_release_is_quiet(client, ctx):
    _check(ctx, payload=_release(__version__))
    v = client.get("/api/v1/system/update").json()
    assert v["update_available"] is False and v["latest"] == __version__
    assert not ctx.db.fetch_all("SELECT 1 FROM notifications WHERE dedupe_key LIKE 'update:%'")
    assert not [i for i in client.get("/api/v1/attention").json()["items"]
                if i["key"] == "system.update_available"]


def test_notification_resolved_after_updating(ctx):
    """Na het bijwerken (de draaiende versie is de gemelde) is de melding afgehandeld."""
    from mediaguard.api.system import notify

    notify(ctx, "info", f"MediaGuard {__version__} is uit", "", link="/systeem#updates",
           dedupe_key=f"update:{__version__}")
    ctx.state_set(updates.STATE_KEY, {"latest": __version__, "notified": __version__})
    asyncio.run(updates.startup(ctx))  # start_background staat uit in tests: geen lus
    assert _notifications(ctx, __version__)[0]["resolved_at"] is not None
    assert "notified" not in updates.state(ctx)
    assert "update_check_task" not in ctx.services


def test_update_notification_is_not_filed_as_storage():
    from mediaguard.notify.service import categorize

    assert categorize("info", "MediaGuard 1.2.2 is uit", "/systeem#updates",
                      "update:1.2.2") == "waiting"


def test_setting_is_registered_simple_and_safe_default(client):
    data = client.get("/api/v1/settings/schema").json()
    d = next(s for s in data["settings"] if s["key"] == updates.SETTING)
    assert d["default"] is True and d["type"] == "bool" and d["simple"] is True
    assert d["label_nl"] and d["help_nl"]
