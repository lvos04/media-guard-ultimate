"""Rapport 3 / R6, R8: reden uit de downloadclient en geweigerde/mislukte downloads opruimen."""

import asyncio
import contextlib

import httpx

from mediaguard.downloads import failure_lookup as clients
from mediaguard.downloads import failures
from mediaguard.instances import registry


class FakeArr:
    app = "radarr"

    def __init__(self, history=None):
        self.removed, self.posts, self.history = [], [], history or []

    async def queue_remove(self, queue_id, *, remove_from_client=True, blocklist=False):
        self.removed.append((queue_id, remove_from_client, blocklist))

    async def history_for_movie(self, _pid):
        return self.history

    async def history_for_episode(self, _pid, _limit=30):
        return self.history

    async def post(self, path, json=None, **_):
        self.posts.append(path)


def _patch(monkeypatch, fake):
    @contextlib.asynccontextmanager
    async def client(_ctx, _iid, **_kw):
        yield fake

    monkeypatch.setattr(registry, "arr_client", client)


def test_translations():
    assert failures.refusal_reason(["Not a Custom Format upgrade for existing movie file(s)"]) \
        == "Geen verbetering in custom formats"
    assert failures.refusal_reason(["Found matching movie via grab history, but release was "
                                    "matched to movie by ID. Manual Import required."]) is None
    assert clients.translate_failure("Unpacking failed, archive requires a password",
                                     "Failed")["code"] == "password"
    assert clients.translate_failure("Repair failed, not enough repair blocks")["code"] == \
        "repair-failed"
    assert clients.translate_failure("Download might fail, only 59.1% of required "
                                     "100.3% available")["code"] == "missing-articles"


def test_refused_download_is_removed_and_blocklisted(ctx, monkeypatch):
    fake = FakeArr()
    _patch(monkeypatch, fake)
    rows = [{"id": 7, "instance_id": "radarr-films", "awaiting_import": True,
             "title": "Jaar in Delft (2025)", "messages": ["Not a Custom Format upgrade"],
             "download_id": "SABnzbd_nzo_1", "movie_id": 3, "state": "importblocked"},
            {"id": 8, "instance_id": "radarr-films", "awaiting_import": False,
             "title": "Loopt nog", "messages": [], "state": "downloading"}]
    done = asyncio.run(failures.handle_queue(ctx, rows))
    assert fake.removed == [(7, True, True)] and done[0]["reason_nl"].startswith("Geen")
    assert ctx.db.fetch_one("SELECT COUNT(*) AS n FROM audit_events WHERE "
                            "action='download.refused_removed'")["n"] == 1


def test_group_avoidance_after_two_failures(ctx):
    key = "sonarr-anime:42"
    failures.record_group_failure(ctx, key, "BadGroup")
    assert "BadGroup" not in failures.avoided_groups(ctx, key)
    failures.record_group_failure(ctx, key, "BadGroup")
    assert "BadGroup" in failures.avoided_groups(ctx, key)


def test_sab_failure_marks_grab_failed(ctx, monkeypatch):
    from mediaguard.provisioning import store

    store.save(ctx, {"download_clients": [{"name": "SAB", "implementation": "Sabnzbd",
                                           "host": "sab", "port": 8080,
                                           "api_key": "k"}]}, "t")

    def handler(request):
        if request.url.params["mode"] == "queue":
            return httpx.Response(200, json={"queue": {"slots": []}})
        assert request.url.params["mode"] == "history"
        return httpx.Response(200, json={"history": {"slots": [
            {"nzo_id": "SABnzbd_nzo_9", "status": "Failed",
             "fail_message": "Unpacking failed, archive requires a password"}]}})

    ctx.services["failure_lookup_transport"] = httpx.MockTransport(handler)
    fake = FakeArr(history=[{"id": 55, "eventType": "grabbed", "date": "2026-11-04T12:01:25Z",
                             "downloadId": "SABnzbd_nzo_9", "sourceTitle": "X.2025.1080p",
                             "data": {"releaseGroup": "GRP"}}])
    _patch(monkeypatch, fake)
    ctx.db.execute_insert(
        "INSERT INTO wanted_items(instance_id,kind,provider_id,item_provider_id,reason,status,"
        "title,last_reason_code,first_seen_at,updated_at) VALUES ('radarr-films','movie',3,3,"
        "'missing','wanted','X','import-failed',?,?)", ("2026-11-04", "2026-11-04"))
    done = asyncio.run(failures.handle_grabbed_without_import(ctx))
    assert fake.posts == ["history/failed/55"]
    assert done[0]["reason_nl"] == "Archief is met een wachtwoord beveiligd"
    # tweede ronde: dezelfde grab niet nog eens
    assert asyncio.run(failures.handle_grabbed_without_import(ctx)) == []


def test_failures_endpoint(client):
    r = client.get("/api/v1/downloads/failures")
    assert r.status_code == 200 and "groups" in r.json()


def _wanted(ctx):
    ctx.db.execute_insert(
        "INSERT INTO wanted_items(instance_id,kind,provider_id,item_provider_id,reason,status,"
        "title,last_reason_code,first_seen_at,updated_at) VALUES ('radarr-films','movie',3,3,"
        "'missing','wanted','X','import-failed',?,?)", ("2026-11-04", "2026-11-04"))


def _sab(ctx, handler):
    from mediaguard.provisioning import store

    store.save(ctx, {"download_clients": [{"name": "SAB", "implementation": "Sabnzbd",
                                           "host": "sab", "port": 8080,
                                           "api_key": "k"}]}, "t")
    ctx.services["failure_lookup_transport"] = httpx.MockTransport(handler)


def _grab(date="2026-11-04T12:01:25Z", extra=()):
    return FakeArr(history=[{"id": 55, "eventType": "grabbed", "date": date,
                             "downloadId": "SABnzbd_nzo_9", "sourceTitle": "X.2025.1080p",
                             "data": {"releaseGroup": "GRP", "indexer": "NZBgeek"}},
                            *extra])


def test_r4_still_in_sab_queue_is_not_gone(ctx, monkeypatch):
    def handler(request):
        if request.url.params["mode"] == "queue":
            return httpx.Response(200, json={"queue": {"slots": [
                {"nzo_id": "SABnzbd_nzo_9", "status": "Downloading", "filename": "X"}]}})
        return httpx.Response(200, json={"history": {"slots": []}})

    _sab(ctx, handler)
    fake = _grab()
    _patch(monkeypatch, fake)
    _wanted(ctx)
    assert asyncio.run(failures.handle_grabbed_without_import(ctx)) == []
    assert fake.posts == []


def test_r4_unreachable_client_never_marks(ctx, monkeypatch):
    def handler(request):
        return httpx.Response(500)

    _sab(ctx, handler)
    fake = _grab()
    _patch(monkeypatch, fake)
    _wanted(ctx)
    assert asyncio.run(failures.handle_grabbed_without_import(ctx)) == []
    assert fake.posts == []


def test_r4_young_grab_and_imported_are_left_alone(ctx, monkeypatch):
    from datetime import UTC, datetime

    def handler(request):
        return httpx.Response(200, json={"queue": {"slots": []}, "history": {"slots": []}})

    _sab(ctx, handler)
    fake = _grab(date=datetime.now(UTC).isoformat())
    _patch(monkeypatch, fake)
    _wanted(ctx)
    assert asyncio.run(failures.handle_grabbed_without_import(ctx)) == []
    imported = _grab(extra=({"id": 56, "eventType": "downloadFolderImported",
                             "date": "2026-11-04T13:00:00Z",
                             "downloadId": "SABnzbd_nzo_9"},))
    _patch(monkeypatch, imported)
    assert asyncio.run(failures.handle_grabbed_without_import(ctx)) == []
    assert fake.posts == [] and imported.posts == []


def test_r4_gone_everywhere_is_marked_with_stats(ctx, monkeypatch):
    def handler(request):
        return httpx.Response(200, json={"queue": {"slots": []}, "history": {"slots": []}})

    _sab(ctx, handler)
    fake = _grab()
    _patch(monkeypatch, fake)
    _wanted(ctx)
    done = asyncio.run(failures.handle_grabbed_without_import(ctx))
    assert fake.posts == ["history/failed/55"] and "niet meer in de client" in done[0]["reason_nl"]
    stats = failures.failure_stats(ctx)
    assert stats["indexer"][0]["name"] == "NZBgeek" and stats["group"][0]["count"] == 1


def test_r4_propers_default_migrated_once(ctx):
    """S3: 'preferAndUpgrade' liet Radarr REPACKs grabben die de import weigerde (lus)."""
    from mediaguard.db import dumps
    from mediaguard.provisioning import store

    ctx.db.execute("INSERT OR REPLACE INTO provisioning_config(key,value_json,updated_at,"
                   "updated_by) VALUES (?,?,?,?)",
                   (store.CONFIG_KEY, dumps({"media": {"propers": "preferAndUpgrade"}}),
                    "2026-11-01", "t"))
    assert store.load(ctx).media.propers == "doNotPrefer"
    store.save(ctx, {"media": {"propers": "preferAndUpgrade"}}, "t")  # bewuste keuze later
    assert store.load(ctx).media.propers == "preferAndUpgrade"
    assert "mediamanagement" in store.AUTO_SECTIONS
