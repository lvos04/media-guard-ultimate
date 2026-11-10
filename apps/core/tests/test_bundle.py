"""Versleutelde herstelbundel: database's + sleutels, alleen te openen met de herstelcode."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from mediaguard.maintenance import bundle


def _encoder_db(root: Path) -> None:
    (root / "encode").mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(root / "encode" / "app.db")
    conn.execute("CREATE TABLE job(id INTEGER PRIMARY KEY, pad TEXT)")
    conn.execute("INSERT INTO job(pad) VALUES ('/data/x.mkv')")
    conn.commit()
    conn.close()


def test_bundle_roundtrip_needs_recovery_code(client, ctx, tmp_path, monkeypatch):
    root = tmp_path / "config"
    monkeypatch.setenv("MG_AIO_CONFIG", str(root))
    _encoder_db(root)
    sec = root / "secrets" / "sonarr-series"
    sec.mkdir(parents=True)
    (sec / "apikey").write_text("abc123\n")
    monkeypatch.setenv("MG_SECRETS_DIR", str(root / "secrets"))
    ctx.secrets.set("test.secret", "geheim", "t")  # zorgt dat secret.key bestaat

    assert client.get("/api/v1/backups/bundle").json()["enabled"] is False
    assert client.post("/api/v1/backups/bundle").status_code == 409
    out = client.post("/api/v1/backups/bundle/setup", json={}).json()
    code = out["recovery_code"]
    assert len(code.replace("-", "")) == 24 and out["first_bundle"]["files"]
    assert {"core/mediaguard.db", "encode/app.db", "core/secret.key",
            "secrets/sonarr-series/apikey"} <= set(out["first_bundle"]["files"])
    # opnieuw instellen vraagt bevestiging
    assert client.post("/api/v1/backups/bundle/setup", json={}).status_code == 409

    folder = bundle.bundle_dir(ctx)
    file = folder / out["first_bundle"]["file"]
    keyfile = folder / "herstelsleutel.json"
    assert b"abc123" not in file.read_bytes() and code not in keyfile.read_text()
    with pytest.raises(bundle.BundleError):
        bundle.open_bundle(file, "AAAA-AAAA-AAAA-AAAA-AAAA-AAAA", keyfile)
    target = tmp_path / "hersteld"
    names = bundle.restore(file, code.lower().replace("-", " "), keyfile, target)
    assert "core/secret.key" in names
    assert (target / "secrets/sonarr-series/apikey").read_text() == "abc123\n"
    conn = sqlite3.connect(target / "encode/app.db")
    assert conn.execute("SELECT pad FROM job").fetchone()[0] == "/data/x.mkv"
    conn.close()
    assert sqlite3.connect(target / "core/mediaguard.db").execute(
        "PRAGMA integrity_check").fetchone()[0] == "ok"
    assert client.get(f"/api/v1/backups/bundle/{file.name}/download").status_code == 200
    assert client.get("/api/v1/backups/bundle/..%2Fsecret.key/download").status_code == 404


def test_regular_backup_includes_encoder_db(client, ctx, tmp_path, monkeypatch):
    root = tmp_path / "config"
    monkeypatch.setenv("MG_AIO_CONFIG", str(root))
    _encoder_db(root)
    meta = client.post("/api/v1/backups").json()
    assert meta["extra_files"] and meta["extra_files"][0]["file"].endswith(".encode-app.db")
    listed = client.get("/api/v1/backups").json()
    assert all(".encode-" not in b["file"] for b in listed)
    extra = meta["extra_files"][0]["file"]
    r = client.post(f"/api/v1/backups/{extra}/restore", json={"confirm": extra})
    assert r.status_code == 422 and "AV1-engine" in r.text
