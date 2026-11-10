from __future__ import annotations

import io
import zipfile


def test_backup_create_list_restore(client, ctx):
    ctx.config.set("global", "", "ui.theme", "light", "t")
    meta = client.post("/api/v1/backups").json()
    assert meta["integrity_ok"] and len(meta["sha256"]) == 64
    ctx.config.set("global", "", "ui.theme", "dark", "t")
    assert client.get("/api/v1/backups").json()[0]["file"]
    bad = client.post(f"/api/v1/backups/{meta['file']}/restore", json={"confirm": "nee"})
    assert bad.status_code == 422
    r = client.post(f"/api/v1/backups/{meta['file']}/restore", json={"confirm": meta["file"]})
    assert r.status_code == 200, r.text
    assert ctx.config.value("ui.theme") == "light"


def test_backup_path_traversal_rejected(client):
    assert client.get("/api/v1/backups/..%2Fsecret.key/download").status_code in (404, 422)


def test_support_bundle_has_no_secrets(client, ctx):
    ctx.secrets.set("ai:openai:apikey", "sk-proj-ditmagnooitlekken123")
    r = client.get("/api/v1/admin/support-bundle")
    assert r.status_code == 200
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    blob = b"".join(zf.read(n) for n in zf.namelist())
    assert b"ditmagnooitlekken" not in blob
    assert b"ai:openai:apikey" in blob  # alleen de naam


def test_coverage_lists_all_settings(client, ctx):
    data = client.get("/api/v1/admin/coverage").json()
    assert data["total"] == len(ctx.registry.all())


def test_admin_health_reports_offline_encoder(client):
    data = client.get("/api/v1/admin/health").json()
    assert data["components"]["database"]["ok"] is True
    assert data["components"]["encoder"]["ok"] is False


def test_admin_health_shows_encoder_lane_pause_reason(client, ctx, tmp_path):
    from test_encoding_support import install

    fake = install(ctx, ctx.settings.staging_dir)
    enc = client.get("/api/v1/admin/health").json()["components"]["encoder"]
    assert enc["ok"] is True and enc["lanes_paused"] == []
    assert enc["cache_min_free_gb"] == 150
    fake.cache_free_gb = 12.0
    enc = client.get("/api/v1/admin/health").json()["components"]["encoder"]
    assert enc["ok"] is True and enc["status"] == "degraded"
    assert {p["lane"] for p in enc["lanes_paused"]} == {"cpu", "gpu"}
    assert all(p["storage_pause"] for p in enc["lanes_paused"])
    assert "pauzebodem van 150 GB" in enc["pause_reason_nl"]
    assert enc["cache_below_floor"] is True and enc["cache_free_gb"] == 12.0


def test_version_reads_release_manifest_from_repo(client, ctx, monkeypatch):
    monkeypatch.delenv("MG_RELEASE_MANIFEST", raising=False)
    data = client.get("/api/v1/admin/version").json()
    assert data["manifest_found"] is True
    assert data["manifest_path"].endswith("deploy/release-manifest.json")
    manifest = data["manifest"]
    assert manifest["version"] == data["version"] == __import__("mediaguard").__version__
    assert manifest["upstream"]["sonarr"]["sha"] == "bc54afbdd0e38bdb0fca4785fcf7d7a6af77bf9a"
    assert manifest["upstream"]["radarr"]["sha"] == "a96bf7c3ecb23cb76993770727285dc17a613fbc"
    assert manifest["upstream"]["av1"]["sha"] == "4b31986b8e91bc3d03f5372436e02644efe0c41d"
    # Het manifest moet de echt toegepaste core-migraties noemen (geen verouderde lijst).
    assert data["schema_versions"] == manifest["schema_versions"]["core"]
    assert "manifest_warning" not in data


def test_version_manifest_via_env_path_as_in_image(client, tmp_path, monkeypatch):
    import json as _json

    target = tmp_path / "usr-share" / "release-manifest.json"
    target.parent.mkdir()
    target.write_text(_json.dumps({"version": "9.9.9", "images": {}}), encoding="utf-8")
    monkeypatch.setenv("MG_RELEASE_MANIFEST", str(target))
    data = client.get("/api/v1/admin/version").json()
    assert data["manifest_path"] == str(target) and data["manifest"]["version"] == "9.9.9"
    assert "9.9.9" in data["manifest_warning"]
    # Ontbrekend env-pad → terugval op de repo-checkout, nooit een crash.
    monkeypatch.setenv("MG_RELEASE_MANIFEST", str(tmp_path / "bestaat-niet.json"))
    assert client.get("/api/v1/admin/version").json()["manifest_found"] is True


def test_encoder_migrations_in_manifest_match_repo():
    import json as _json
    from pathlib import Path

    root = Path(__file__).resolve().parents[3]
    manifest = _json.loads((root / "deploy" / "release-manifest.json").read_text("utf-8"))
    files = sorted(int(p.name.split("_")[0])
                   for p in (root / "services/encoder/app/migrations").glob("*.sql"))
    assert manifest["schema_versions"]["encoder"] == files
