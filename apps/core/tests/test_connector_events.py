"""Polling van de gevulde outbox, inclusief verlies van rijen door retentie."""

from __future__ import annotations

import logging
from contextlib import closing
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import NAMESPACE_URL, UUID, uuid5

import pytest
from test_connector_lezen import database_snapshot, error, key

from mediaguard import db as database
from mediaguard.maintenance.housekeeping import prune

PATH = "/connector/v1/events"


def publish(ctx, *, age=0, topic="library.synced", payload=None, instance_id=None):
    ctx.events.publish(topic, payload if payload is not None else {}, instance_id=instance_id)
    event_id = ctx.db.fetch_one("SELECT MAX(id) FROM outbox_events")[0]
    if age:
        stamp = (datetime.now(UTC) - timedelta(days=age)).isoformat(timespec="seconds")
        ctx.db.execute("UPDATE outbox_events SET ts=? WHERE id=?", (stamp, event_id))
    mark = ctx.db.fetch_one("SELECT birth FROM outbox_source WHERE event_id=?", (event_id,))
    return (event_id << 129) + int.from_bytes(mark[0], "big") if mark else event_id


def row_position(identifier):
    return identifier >> 129 if identifier > 2**63 - 1 else identifier


def page(transport, headers=None, **params):
    response = transport.get(PATH, headers=headers, params=params)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    result = response.json()
    assert set(result) == {"events", "next_cursor"}
    return result


def ids(result):
    return [int(event["source_event_id"]) for event in result["events"]]


def expired(response):
    assert error(response, 409, "cursor_expired")["details"] == {"restart": True}


def test_cursor_expires_after_retention_and_restarts_at_oldest(client, ctx):
    gone = publish(ctx, age=15)
    kept = publish(ctx, age=13)
    first = page(client, limit=1)
    assert ids(first) == [gone]
    assert ctx.config.value("maintenance.retention_days_events") == 14
    assert prune(ctx)["events"] == 1
    expired(client.get(PATH, params={"cursor": first["next_cursor"]}))
    assert ids(page(client)) == [kept]


def test_new_retention_gap_between_pages_even_with_older_retained_rows(client, ctx):
    oldest = publish(ctx)
    removed = publish(ctx, age=20)
    newest = publish(ctx)
    first = page(client, limit=1)
    assert ids(first) == [oldest]
    assert ids(page(client, cursor=first["next_cursor"], limit=1)) == [removed]

    assert prune(ctx)["events"] == 1
    expired(client.get(PATH, params={"cursor": first["next_cursor"], "limit": 1}))
    restarted = page(client, limit=1)
    assert ids(restarted) == [oldest]
    assert ids(page(client, cursor=restarted["next_cursor"], limit=1)) == [newest]


def test_new_gap_below_the_previous_deletion_boundary_expires_cursor(client, ctx):
    oldest = publish(ctx)
    later = publish(ctx)
    publish(ctx, age=20)
    assert prune(ctx)["events"] == 1
    restarted = page(client, limit=1)
    assert ids(restarted) == [oldest]

    ctx.db.execute("UPDATE outbox_events SET ts=? WHERE id=?",
                   ("2020-01-01T00:00:00Z", row_position(later)))
    assert prune(ctx)["events"] == 1
    expired(client.get(PATH, params={"cursor": restarted["next_cursor"]}))


def test_rolled_back_delete_does_not_expire_a_cursor(client, ctx):
    publish(ctx)
    latest = publish(ctx)
    cursor = page(client, limit=1)["next_cursor"]
    with pytest.raises(RuntimeError, match="terugrollen"), ctx.db.transaction() as conn:
        conn.execute("DELETE FROM outbox_events WHERE id=?", (row_position(latest),))
        raise RuntimeError("terugrollen")
    assert ids(page(client, cursor=cursor)) == [latest]


def test_ids_survive_cleanup_of_highest_row_and_empty_outbox(client, ctx):
    oldest = publish(ctx)
    highest = publish(ctx, age=20)
    first = page(client)
    assert ids(first) == [oldest, highest]
    assert prune(ctx)["events"] == 1
    next_id = publish(ctx, age=20)
    assert row_position(oldest) < row_position(highest) < row_position(next_id)
    assert oldest < highest < next_id
    before_empty = page(client)
    assert ids(before_empty) == [oldest, next_id]

    ctx.config.set("global", "", "maintenance.retention_days_events", 1, "test")
    ctx.db.execute("UPDATE outbox_events SET ts=? WHERE id=?",
                   ("2020-01-01T00:00:00Z", row_position(oldest)))
    assert prune(ctx)["events"] == 2
    expired(client.get(PATH, params={"cursor": before_empty["next_cursor"]}))
    empty = page(client)
    assert ids(empty) == []
    assert page(client, cursor=empty["next_cursor"]) == empty
    fresh = publish(ctx)
    assert row_position(fresh) > row_position(next_id)
    assert fresh > next_id
    last = page(client, cursor=empty["next_cursor"])
    assert ids(last) == [fresh]
    assert len({event["event_id"] for event in first["events"] + last["events"]}) == 3


def test_pages_are_bounded_stable_and_empty_page_keeps_position(client, ctx):
    with ctx.db.transaction() as conn:
        conn.executemany(
            "INSERT INTO outbox_events(ts,topic,payload) VALUES(?,?,?)",
            [(database.utcnow(), "library.synced", "{}")] * 201,
        )
    first = page(client, limit=200)
    assert first == page(client, limit=200)
    assert len(ids(first)) == 200
    second = page(client, cursor=first["next_cursor"], limit=200)
    assert len(ids(second)) == 1
    events = first["events"] + second["events"]
    source_ids = ids(first) + ids(second)
    assert source_ids == sorted(set(source_ids))
    positions = [row_position(identifier) for identifier in source_ids]
    assert positions == sorted(set(positions))
    assert [UUID(event["event_id"]) for event in events] == [
        uuid5(NAMESPACE_URL, f"mediaguard:{event_id}") for event_id in source_ids
    ]
    empty = page(client, cursor=second["next_cursor"])
    assert empty == {"events": [], "next_cursor": second["next_cursor"]}
    fresh = publish(ctx)
    assert fresh > source_ids[-1]
    assert ids(page(client, cursor=empty["next_cursor"])) == [fresh]


def test_only_safe_envelopes_leave_the_outbox(client, anon_client, ctx, caplog):
    headers = key(client)
    secret = headers["X-Api-Key"]
    path = f"/testmedia/{secret}/titel"
    viewing = "Vrije kijkpauzetekst uit een lokale fixture"
    first = publish(ctx, topic="recovery.rolled_back", instance_id=path,
                    payload={"api_key": secret, "error": path, "transaction_id": 4})
    second = publish(ctx, topic="legacy." + secret, instance_id=path,
                     payload={"reason": viewing, "path": path, "actor": secret})
    with caplog.at_level(logging.INFO, logger="httpx"):
        response = anon_client.get(PATH, headers=headers)
    assert response.status_code == 200
    assert "GET" in caplog.text and PATH in caplog.text
    if any(value in response.text or value in caplog.text for value in (secret, path, viewing)):
        pytest.fail("Gevoelige fixture-inhoud in antwoord of verzoeklog", pytrace=False)
    result = response.json()
    assert ids(result) == [first, second]
    for event, topic, subject, level in zip(
        result["events"], ["media.recovery.failed", "media.event.recorded"],
        ["recovery", "event"], ["hoog", "info"], strict=True,
    ):
        assert set(event) == {
            "schema_version", "event_id", "source_app", "source_event_id", "event_type",
            "entity", "timestamp_utc", "trace_id", "correlation_id", "actor", "classification",
            "payload", "payload_ref",
        }
        assert event["event_type"] == topic
        assert event["entity"] == {"type": "media_component", "id": subject}
        assert event["actor"] == {"type": "system", "id": "mediaguard"}
        assert event["payload"] == {"ernst": level}
        assert event["source_app"] == "mediaguard" and event["schema_version"] == "1.0"
        assert event["classification"] == "internal"
        assert event["correlation_id"] is None and event["payload_ref"] is None
        assert UUID(event["trace_id"]).version == 5 and event["trace_id"] != event["event_id"]
        assert event["timestamp_utc"].endswith("Z")


def test_events_refuse_missing_and_invalid_auth(client, anon_client, ctx):
    publish(ctx)
    error(anon_client.get(PATH), 401, "unauthorized")
    error(client.get(PATH, headers={"X-Api-Key": "ongeldig"}), 401, "unauthorized")
    assert page(anon_client, key(client, scopes=[])) == page(client)


@pytest.mark.parametrize("params", [
    {"cursor": ""}, {"cursor": "v1.-1.0"}, {"cursor": "v1.9223372036854775808.0"},
    {"cursor": "v1.1.9223372036854775808"}, {"cursor": "v1.1.0.extra"},
    {"limit": 0}, {"limit": 201}, {"limit": "onbekend"},
])
def test_invalid_poll_query_is_rejected(client, params):
    body = error(client.get(PATH, params=params), 422, "validation_error")
    assert body["details"]["fouten"]


def test_migration_and_snapshot_restore_preserve_every_existing_row(tmp_path, monkeypatch):
    migrations = database.MIGRATIONS_DIR
    previous = tmp_path / "vorige-migraties"
    previous.mkdir()
    for path in migrations.glob("*.sql"):
        if not path.name.startswith(("151_", "153_")):
            (previous / path.name).symlink_to(path)

    db_path = tmp_path / "config" / "core.sqlite"
    snapshot = tmp_path / "voor-migratie.sqlite"
    with closing(database.Database(db_path)) as db:
        monkeypatch.setattr(database, "MIGRATIONS_DIR", previous)
        db.initialize()
        db.execute("INSERT INTO outbox_events VALUES (17, ?, 'library.synced', 'fixture', '{}', 1)",
                   (database.utcnow(),))
        db.execute("INSERT INTO outbox_events VALUES (42, ?, 'recovery.committed', NULL, '{}', 0)",
                   (database.utcnow(),))
        before = database_snapshot(SimpleNamespace(db=db))
        db.backup_to(snapshot)

    def check_upgrade(path):
        monkeypatch.setattr(database, "MIGRATIONS_DIR", migrations)
        with closing(database.Database(path)) as db:
            db.initialize()
            assert "151_outbox_event_identity" in {
                row[0] for row in db.fetch_all("SELECT version FROM schema_migrations")
            }
            current = database_snapshot(SimpleNamespace(db=db))
            current_by_name = {name: rows for (name, _), rows in current.items()}
            for (name, _), rows in before.items():
                if name not in {"schema_migrations", "sqlite_sequence"}:
                    assert current_by_name[name] == rows, name
            assert db.fetch_one("PRAGMA integrity_check")[0] == "ok"
            assert db.fetch_all("PRAGMA foreign_key_check") == []
            seq = db.fetch_one("SELECT seq FROM sqlite_sequence WHERE name='outbox_events'")
            assert seq[0] == 42

    check_upgrade(db_path)
    monkeypatch.setattr(database, "MIGRATIONS_DIR", previous)
    restored = tmp_path / "teruggezet" / "core.sqlite"
    restored.parent.mkdir()
    restored.write_bytes(snapshot.read_bytes())
    with closing(database.Database(restored)) as db:
        db.initialize()
        assert database_snapshot(SimpleNamespace(db=db)) == before
    check_upgrade(restored)
