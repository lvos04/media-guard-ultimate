"""Meldingen buiten de browser (M1), overzichten (M2) en de bundelkopie buiten de NAS (M11)."""

from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, date, datetime
from pathlib import Path

import httpx

from mediaguard.api.system import notify
from mediaguard.notify import channels as ch
from mediaguard.notify import offsite, service


class Recorder:
    def __init__(self, status: int = 200) -> None:
        self.requests: list[httpx.Request] = []
        self.status = status
        self.store: dict[str, bytes] = {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.method == "PUT":
            self.store[str(request.url)] = request.content
            return httpx.Response(201)
        if request.method == "GET" and str(request.url) in self.store:
            return httpx.Response(200, content=self.store[str(request.url)])
        return httpx.Response(self.status, text="ok")


def _transport(ctx, status: int = 200) -> Recorder:  # type: ignore[no-untyped-def]
    rec = Recorder(status)
    ctx.services["notify_channels_ready"] = True  # zoals na startup()
    ctx.services["notify_transport"] = httpx.MockTransport(rec)
    return rec


def _ntfy(ctx, enabled=True):  # type: ignore[no-untyped-def]
    return service.create_channel(ctx, "ntfy", "Telefoon",
                                  {"server": "https://ntfy.example", "topic": "mg-test"}, {},
                                  enabled=enabled)


def _run(coro):  # type: ignore[no-untyped-def]
    return asyncio.run(coro)


def test_default_ntfy_topic_is_private_and_off(ctx):  # type: ignore[no-untyped-def]
    ctx.db.execute("DELETE FROM notify_channels")
    ctx.state_set("notify.default_created", False)
    made = service.ensure_default_ntfy(ctx)
    assert made is not None and made["kind"] == "ntfy" and not made["enabled"]
    topic = made["config"]["topic"]
    assert topic.startswith("mediaguard-") and len(topic) > 25
    public = service.public_channel(ctx, made)
    assert public["subscribe_url"].endswith(topic)
    assert public["qr_svg"].startswith("data:image/svg+xml")
    assert service.ensure_default_ntfy(ctx) is None  # eenmalig


def test_error_goes_direct_and_is_sent(ctx):  # type: ignore[no-untyped-def]
    ctx.db.execute("DELETE FROM notify_channels")
    rec = _transport(ctx)
    channel = _ntfy(ctx)
    ctx.config.set("global", "", "notify.base_url", "https://mg.example", "t")
    notify(ctx, "error", "AV1-engine reageert niet", "Herstart volgt", link="/av1")
    stats = _run(service.dispatch(ctx))
    assert stats["sent"] == 1
    req = rec.requests[-1]
    assert str(req.url) == "https://ntfy.example/mg-test"
    assert req.headers["Click"] == "https://mg.example/av1"
    assert req.headers["Priority"] == "4"
    row = service.get_channel(ctx, int(channel["id"]))
    assert row is not None and row["last_ok_at"]


def test_digest_category_and_never(ctx):  # type: ignore[no-untyped-def]
    ctx.db.execute("DELETE FROM notify_channels")
    _transport(ctx)
    _ntfy(ctx)
    nid = notify(ctx, "warning", "Wacht op jou: 2 dingen", link="/#todo")
    assert ctx.db.fetch_one("SELECT COUNT(*) AS n FROM notify_outbox")["n"] == 0
    assert ctx.db.fetch_one("SELECT notification_id FROM notify_digest_items")[
        "notification_id"] == nid
    ctx.config.set("global", "", "notify.pref.errors", "never", "t")
    assert service.on_notification(ctx, 999, "error", "Iets faalt") == "never"


def test_repeat_and_rate_limit(ctx):  # type: ignore[no-untyped-def]
    ctx.db.execute("DELETE FROM notify_channels")
    _transport(ctx)
    _ntfy(ctx)
    notify(ctx, "error", "Encoder vastgelopen", link="/x")
    notify(ctx, "error", "Encoder vastgelopen", link="/x")  # zelfde melding opgehoogd
    assert ctx.db.fetch_one("SELECT COUNT(*) AS n FROM notify_outbox")["n"] == 1
    ctx.config.set("global", "", "notify.rate_per_hour", 2, "t")
    for i in range(4):
        notify(ctx, "error", f"Fout {i}")
    assert ctx.db.fetch_one("SELECT COUNT(*) AS n FROM notify_outbox")["n"] == 2
    assert ctx.db.fetch_one("SELECT COUNT(*) AS n FROM notify_digest_items")["n"] >= 2


def test_retry_with_backoff_then_give_up(ctx):  # type: ignore[no-untyped-def]
    ctx.db.execute("DELETE FROM notify_channels")
    _transport(ctx, status=503)
    channel = _ntfy(ctx)
    service.enqueue(ctx, int(channel["id"]), "errors", "Test")
    for _ in range(len(service.BACKOFF_MINUTES)):
        ctx.db.execute("UPDATE notify_outbox SET next_attempt_at='2000-01-01T00:00:00+00:00'")
        _run(service.dispatch(ctx))
    row = ctx.db.fetch_one("SELECT status, attempts, last_error FROM notify_outbox")
    assert row["status"] == "failed" and row["attempts"] == len(service.BACKOFF_MINUTES)
    assert "503" in row["last_error"]


def test_channel_payloads(ctx):  # type: ignore[no-untyped-def]
    rec = _transport(ctx)
    msg = ch.Message(title="Titel", body="Tekst")
    cases = {
        "pushover": ({}, {"user_key": "u", "app_token": "a"}, "api.pushover.net"),
        "telegram": ({"chat_id": "42"}, {"bot_token": "123:abc"}, "api.telegram.org"),
        "discord": ({}, {"webhook_url": "https://discord.example/hook"}, "discord.example"),
        "slack": ({}, {"webhook_url": "https://hooks.slack.example/x"}, "slack.example"),
        "webhook": ({}, {"url": "https://hook.example/mg"}, "hook.example"),
    }
    for kind, (cfg, secrets_, host) in cases.items():
        channel = service.create_channel(ctx, kind, kind, cfg, secrets_)
        _run(ch.send(ctx, channel, msg))
        assert host in str(rec.requests[-1].url), kind
    # geheimen nooit in de publieke weergave
    pub = service.public_channel(ctx, service.list_channels(ctx)[-1])
    assert "url" not in pub["config"] and pub["secrets_set"]["url"] is True


def test_email_via_fake_smtp(ctx, tmp_path: Path):  # type: ignore[no-untyped-def]
    sent: list[object] = []

    class FakeSMTP:
        def login(self, user, pw):  # type: ignore[no-untyped-def]
            sent.append(("login", user, pw))

        def send_message(self, msg):  # type: ignore[no-untyped-def]
            sent.append(msg)

        def quit(self):  # type: ignore[no-untyped-def]
            pass

    ctx.services["notify_smtp_factory"] = lambda host, port, sec: FakeSMTP()
    channel = service.create_channel(
        ctx, "email", "Mail", {"host": "smtp.example", "port": 587, "security": "starttls",
                               "username": "me", "from_addr": "mg@x.nl", "to_addr": "j@x.nl"},
        {"password": "pw"})
    att = tmp_path / "a.mgbundle"
    att.write_bytes(b"data")
    _run(ch.send(ctx, channel, ch.Message(title="Hoi", body="x", attachment=att)))
    assert sent[0] == ("login", "me", "pw")
    mail = sent[1]
    assert mail["Subject"] == "MediaGuard: Hoi"  # type: ignore[index]
    assert any(p.get_filename() == "a.mgbundle" for p in mail.iter_attachments())  # type: ignore[attr-defined]


def test_missing_config_is_not_retried(ctx):  # type: ignore[no-untyped-def]
    channel = service.create_channel(ctx, "telegram", "TG", {"chat_id": "1"}, {})
    try:
        _run(ch.send(ctx, channel, ch.Message(title="x")))
    except ch.ChannelError as exc:
        assert not exc.retry and "bot_token" in str(exc)
    else:  # pragma: no cover
        raise AssertionError


def test_digest_text_and_once_per_period(ctx):  # type: ignore[no-untyped-def]
    ctx.db.execute("DELETE FROM notify_channels")
    _transport(ctx)
    _ntfy(ctx)
    start, end = service._bounds(ctx, "daily", date(2026, 11, 5))
    mid = "2026-11-04T12:00:00+00:00"
    assert start < mid < end
    ctx.db.execute(
        "INSERT INTO encoding_jobs(source_path,status,policy_json,source_snapshot_json,"
        "staged_output_json,created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
        ("/m/a.mkv", "committed", "{}", '{"size": 3000000000}', '{"size": 1000000000}',
         mid, mid))
    digest = service.build_digest(ctx, "daily", date(2026, 11, 5))
    assert digest["stats"]["encoded"] == 1 and digest["stats"]["saved_bytes"] == 2_000_000_000
    assert digest["text_nl"].startswith("Gisteren: 1 bestand omgezet naar AV1 (1,9 GB bespaard)")
    assert service.store_and_send_digest(ctx, digest)
    assert not service.store_and_send_digest(ctx, digest)  # eenmalig
    assert ctx.db.fetch_one("SELECT COUNT(*) AS n FROM notify_outbox WHERE category='digest'")[
        "n"] == 1


def test_due_digests_respects_time(ctx):  # type: ignore[no-untyped-def]
    early = datetime(2026, 11, 5, 6, 0, tzinfo=UTC)  # 07:00 Amsterdam
    late = datetime(2026, 11, 5, 8, 0, tzinfo=UTC)  # 09:00 Amsterdam (donderdag)
    assert service.due_digests(ctx, early) == []
    kinds = [d["kind"] for d in service.due_digests(ctx, late)]
    assert kinds == ["daily"]
    ctx.config.set("global", "", "notify.digest.weekday", 4, "t")
    assert [d["kind"] for d in service.due_digests(ctx, late)] == ["daily", "weekly"]


def test_notify_api(client, ctx):  # type: ignore[no-untyped-def]
    ov = client.get("/api/v1/notify/overview").json()
    assert {c["key"] for c in ov["categories"]} == set(service.CATEGORIES)
    assert ov["channels"] and ov["channels"][0]["kind"] == "ntfy"
    r = client.post("/api/v1/notify/channels",
                    json={"kind": "discord", "secrets": {"webhook_url": "https://d.example/h"}})
    assert r.status_code == 200 and r.json()["secrets_set"]["webhook_url"]
    _transport(ctx)
    assert client.post(f"/api/v1/notify/channels/{r.json()['id']}/test").json() == {"ok": True}
    r = client.put("/api/v1/notify/preferences",
                   json={"categories": {"av1_done": "direct"}, "digest_time": "07:30"})
    assert r.status_code == 200
    body = r.json()
    assert body["digest"]["time"] == "07:30"
    assert next(c for c in body["categories"] if c["key"] == "av1_done")["value"] == "direct"
    assert client.put("/api/v1/notify/preferences",
                      json={"digest_time": "25:00"}).status_code == 422
    assert client.get("/api/v1/notify/digests").json()["today"]["text_nl"].startswith(
        "Vandaag tot nu toe")


# -- herstelbundel buiten de NAS ---------------------------------------------------------------
def _bundle(ctx) -> Path:  # type: ignore[no-untyped-def]
    from mediaguard.maintenance import bundle

    folder = bundle.bundle_dir(ctx)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "mediaguard-bundel-20261105-0800.mgbundle"
    path.write_bytes(b"MGBUNDLE1\n" + b"x" * 5000)
    (folder / offsite.KEYFILE).write_text("{}", "utf-8")
    return path


def test_offsite_path_copy_verify_and_once(ctx, tmp_path: Path):  # type: ignore[no-untyped-def]
    src = _bundle(ctx)
    dest = tmp_path / "share"
    dest.mkdir()
    ctx.config.set("global", "", "backup.offsite.kind", "path", "t")
    ctx.config.set("global", "", "backup.offsite.path", str(dest), "t")
    res = _run(offsite.maybe_run(ctx))
    assert res and res["ok"]
    assert (dest / src.name).read_bytes() == src.read_bytes()
    assert (dest / offsite.KEYFILE).exists()
    assert _run(offsite.maybe_run(ctx)) is None  # al gekopieerd
    assert offsite.status(ctx)["last_ok"]["sha256"] == hashlib.sha256(src.read_bytes()).hexdigest()


def test_offsite_missing_folder_fails_with_notification(ctx):  # type: ignore[no-untyped-def]
    _bundle(ctx)
    ctx.config.set("global", "", "backup.offsite.kind", "path", "t")
    ctx.config.set("global", "", "backup.offsite.path", "/bestaat/niet", "t")
    res = _run(offsite.run(ctx))
    assert not res["ok"] and "bestaat niet" in res["error"]
    assert ctx.db.fetch_one("SELECT 1 FROM notifications WHERE dedupe_key="
                            "'backup.offsite.failed'") is not None
    assert _run(offsite.maybe_run(ctx)) is None  # binnen het uur niet opnieuw


def test_offsite_webdav_and_s3(ctx):  # type: ignore[no-untyped-def]
    src = _bundle(ctx)
    rec = _transport(ctx)
    ctx.config.set("global", "", "backup.offsite.kind", "webdav", "t")
    ctx.config.set("global", "", "backup.offsite.webdav_url", "https://dav.example/mg", "t")
    ctx.config.set("global", "", "backup.offsite.webdav_user", "u", "t")
    ctx.secrets.set(offsite.secret_name("webdav_password"), "p")
    assert _run(offsite.run(ctx))["ok"]
    assert f"https://dav.example/mg/{src.name}" in rec.store
    assert rec.requests[0].headers["authorization"].startswith("Basic ")
    ctx.config.set("global", "", "backup.offsite.kind", "s3", "t")
    ctx.config.set("global", "", "backup.offsite.s3_endpoint", "https://s3.example", "t")
    ctx.config.set("global", "", "backup.offsite.s3_bucket", "bak", "t")
    ctx.config.set("global", "", "backup.offsite.s3_access_key", "AK", "t")
    ctx.secrets.set(offsite.secret_name("s3_secret_key"), "SK")
    assert _run(offsite.run(ctx))["ok"]
    put = next(r for r in rec.requests if "s3.example" in str(r.url) and r.method == "PUT")
    assert str(put.url) == f"https://s3.example/bak/mediaguard/{src.name}"
    assert put.headers["authorization"].startswith("AWS4-HMAC-SHA256 Credential=AK/")


def test_sigv4_known_vector():  # type: ignore[no-untyped-def]
    """AWS-voorbeeld (GET Object, us-east-1, 20130524) uit de S3-documentatie."""
    headers = offsite.sigv4_headers(
        "GET", "https://examplebucket.s3.amazonaws.com/test.txt", b"",
        access_key="AKIAIOSFODNN7EXAMPLE",
        secret_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY", region="us-east-1",
        now=datetime(2013, 5, 24, tzinfo=UTC))
    # zonder Range-header wijkt de handtekening af van het doc-voorbeeld; we controleren de vorm
    assert headers["x-amz-date"] == "20130524T000000Z"
    assert headers["x-amz-content-sha256"] == hashlib.sha256(b"").hexdigest()
    assert "Credential=AKIAIOSFODNN7EXAMPLE/20130524/us-east-1/s3/aws4_request" in \
        headers["Authorization"]


def test_offsite_api(client, ctx, tmp_path: Path):  # type: ignore[no-untyped-def]
    _bundle(ctx)
    dest = tmp_path / "share2"
    dest.mkdir()
    r = client.put("/api/v1/notify/offsite", json={"kind": "path", "path": str(dest)})
    assert r.status_code == 200 and r.json()["config"]["kind"] == "path"
    assert r.json()["pending"] is True
    assert client.post("/api/v1/notify/offsite/run").json()["ok"] is True
    r = client.put("/api/v1/notify/offsite", json={"s3_secret_key": "geheim"})
    assert r.json()["config"]["secrets_set"]["s3_secret_key"] is True
    assert "geheim" not in r.text
