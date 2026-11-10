"""Kleinere core-fixes uit het testrapport van 4 nov (H13, M3, nazorg)."""

from __future__ import annotations


def test_compare_null_equals_absent_category():
    from mediaguard.migration.snapshot import _no_nulls

    assert _no_nulls({"movieCategory": "radarr", "movieImportedCategory": None}) == \
        _no_nulls({"movieCategory": "radarr"})


def test_report_view_splits_accepted(ctx):
    from mediaguard.migration import store

    run_id = store.create_run(ctx, "t", [], {}, "test")
    ctx.db.execute("UPDATE migration_runs SET accepted_json=? WHERE id=?",
                   ('{"sonarr-series": {"abc": {"by": "user:jan"}}}', run_id))
    rid = store.add_report(ctx, run_id, "verify", "go", target="sonarr-series",
                           mismatches=[{"id": "abc", "acceptable": True, "severity": "no_go"},
                                       {"id": "def", "acceptable": True,
                                        "severity": "warning"}])
    row = ctx.db.fetch_one("SELECT * FROM migration_reports WHERE id=?", (rid,))
    view = store.report_view(row, ctx)
    assert [m["id"] for m in view["accepted_mismatches"]] == ["abc"]
    assert [m["id"] for m in view["open_mismatches"]] == ["def"]


def test_notifications_merge_and_resolve(client, ctx):
    from mediaguard.api.system import notify, resolve_notifications

    for n in range(4):
        notify(ctx, "warning", f"Film {n}: vrijgavecontrole mislukt", "x",
               link=f"/quarantaine?item={n}", dedupe_key="qc",
               group_title="{n} originelen blijven bewaard")
    rows = ctx.db.fetch_dicts("SELECT * FROM notifications WHERE dedupe_key='qc'")
    assert len(rows) == 1 and rows[0]["count"] == 4
    assert rows[0]["title"] == "4 originelen blijven bewaard"
    assert rows[0]["link"] == "/quarantaine"
    notify(ctx, "warning", "Inrichting wijkt af", "2 afwijkingen", dedupe_key="drift")
    assert resolve_notifications(ctx, "drift") == 1
    assert client.get("/api/v1/status").json()["unread_notifications"] == 1


async def test_aftercare_prunes_only_dead_prowlarr_indexers(ctx, monkeypatch):
    from mediaguard.migration import aftercare

    deleted: list[int] = []

    class Arr:
        async def provider_list(self, kind):
            return [{"id": 1, "name": "OudIdx (Prowlarr)"}, {"id": 2, "name": "NZBgeek (Prowlarr)"},
                    {"id": 3, "name": "Eigen indexer"}]

        async def provider_delete(self, kind, item_id):
            deleted.append(item_id)

    class Prow:
        async def indexers(self):
            return [{"name": "NZBgeek"}]

    class Cfg:
        indexer_mode = "prowlarr"

        class prowlarr:  # noqa: N801
            url = "http://prowlarr"

    from mediaguard.instances import registry
    from mediaguard.provisioning import prowlarr, prowlarr_sync
    from mediaguard.provisioning import store as pstore

    monkeypatch.setattr(pstore, "load", lambda _ctx: Cfg)
    monkeypatch.setattr(ctx.secrets, "has", lambda _k: True)
    monkeypatch.setattr(prowlarr, "prowlarr_client", lambda _ctx: Prow())
    monkeypatch.setattr(registry, "arr_client", lambda _ctx, _iid: Arr())

    async def no_sync(_ctx):
        return None

    monkeypatch.setattr(prowlarr_sync, "trigger_sync", no_sync)
    removed = await aftercare.prune_dead_prowlarr_indexers(ctx, "sonarr-series")
    assert removed == ["OudIdx (Prowlarr)"] and deleted == [1]


def test_media_summary_from_arr_mediainfo():
    from mediaguard.library.api import media_summary

    m = media_summary({"resolution": "3840x2160", "videoCodec": "HEVC",
                       "videoDynamicRangeType": "DV HDR10", "audioLanguages": "English/Dutch",
                       "subtitles": "English / Dutch / Japanese"})
    assert m["dolby_vision"] and m["hdr"] and m["audio_languages"] == ["English", "Dutch"]
    assert m["subtitle_languages"] == ["English", "Dutch", "Japanese"]
    assert media_summary({}) is None


def test_shipped_model_prices_without_manual_entry(ctx):
    from mediaguard.ai import budget

    ctx.config.set("global", "", "ai.price_table", {}, "test")  # oude/lege tabel
    p = budget.price_for(ctx, "anthropic", "claude-opus-5-5")
    assert p is not None and (p.input_per_mtok, p.output_per_mtok) == (4.0, 20.0)
    assert budget.price_for(ctx, "anthropic", "onbekend-model") is None
