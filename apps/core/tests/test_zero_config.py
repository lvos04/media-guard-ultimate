"""Nul-configuratie-installatie: sleutels automatisch, bibliotheekvergrendeling in software,
publieke URL's uit het browseradres."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from mediaguard import bootstrap


def test_bootstrap_generates_secrets_and_dirs_idempotently(tmp_path, monkeypatch):
    monkeypatch.setenv("MG_UID", str(os.getuid()))
    monkeypatch.setenv("MG_GID", str(os.getgid()))
    data = tmp_path / "data"
    (data / "media" / "tv").mkdir(parents=True)
    keep = data / "media" / "tv" / "Bestaande serie"
    keep.mkdir()
    app_dirs = [tmp_path / "appdata" / "core"]
    data_dirs = [data / "media" / "tv", data / "media" / "anime"]
    log = bootstrap.run(tmp_path / "appdata" / "secrets", app_dirs, data_dirs)
    sec = tmp_path / "appdata" / "secrets"
    token = (sec / "internal" / "token").read_text().strip()
    assert len(token) == 64
    for inst in bootstrap.INSTANCES:
        assert len((sec / inst / "apikey").read_text().strip()) == 32
        assert len((sec / inst / "hookkey").read_text().strip()) == 64
        assert oct((sec / inst / "apikey").stat().st_mode)[-3:] == "640"
    assert (data / "media" / "anime").is_dir() and keep.is_dir()   # bestaande media intact
    assert any("API-sleutel" in line for line in log)
    # tweede run: dezelfde sleutels, niets overschreven
    assert bootstrap.run(sec, app_dirs, data_dirs) == []
    assert (sec / "internal" / "token").read_text().strip() == token


def test_secret_files_loaded_into_env(tmp_path):
    sec = tmp_path / "s"
    (sec / "internal").mkdir(parents=True)
    (sec / "internal" / "token").write_text("t" * 64)
    (sec / "sonarr-anime").mkdir()
    (sec / "sonarr-anime" / "apikey").write_text("a" * 32)
    (sec / "sonarr-anime" / "hookkey").write_text("h" * 64)
    env = {"MG_SECRETS_DIR": str(sec), "MG_INSTANCE_SONARR_ANIME_APIKEY": "expliciet"}
    bootstrap.load_secret_files(env)
    assert env["MG_INTERNAL_TOKEN"] == "t" * 64
    assert env["MG_INSTANCE_SONARR_ANIME_HOOKKEY"] == "h" * 64
    assert env["MG_INSTANCE_SONARR_ANIME_APIKEY"] == "expliciet"  # env blijft leidend


def test_library_locked_by_default(app):
    ctx = app.state.ctx  # zonder de ctx-fixture (die ontgrendelt)
    assert ctx.config.value("library.write_enabled") is False
    from mediaguard.recovery import service

    ready = service.readiness(ctx)
    assert ready["library_locked"] and any("vergrendeld" in r for r in ready["reasons"])


async def test_locked_library_aborts_transaction(ctx, tmp_path):
    from mediaguard.recovery import executor, service

    ctx.config.set("global", "", "library.write_enabled", False, "t")
    lib = tmp_path / "lib.mkv"
    lib.write_bytes(b"x")
    st = lib.stat()
    txn_id, _ = service.create_transaction(
        ctx, library_path=str(lib), staged_path=str(tmp_path / "out.mkv"),
        source_snapshot={"size": st.st_size, "mtime_ns": st.st_mtime_ns, "inode": st.st_ino,
                         "device": st.st_dev})
    txn = await executor.run_transaction(ctx, txn_id)
    assert txn["state"] == "aborted" and "vergrendeld" in txn["error"]
    assert lib.read_bytes() == b"x"


def test_unlock_endpoint_requires_confirmation(client):
    assert client.post("/api/v1/recovery/library-write", json={}).status_code == 422
    r = client.post("/api/v1/recovery/library-write", json={"confirm": True})
    assert r.json()["library_write_enabled"] is True


def test_public_urls_filled_from_browser_host(ctx):
    from mediaguard.instances.registry import fill_public_urls, list_instances, seed_instances

    seed_instances(ctx)
    for inst in list_instances(ctx):
        ctx.db.execute("UPDATE instances SET public_url=NULL WHERE id=?", (inst.id,))
    ctx.db.execute("UPDATE instances SET public_url='http://eigen:1' WHERE id='radarr-films'")
    changed = fill_public_urls(ctx, "192.168.1.20")
    urls = {i.id: i.public_url for i in list_instances(ctx)}
    assert urls["sonarr-series"] == "http://192.168.1.20:38989"
    assert urls["radarr-animefilms"] == "http://192.168.1.20:37879"
    assert urls["radarr-films"] == "http://eigen:1" and "radarr-films" not in changed
    assert fill_public_urls(ctx, "evil/path") == []


@pytest.mark.parametrize("compose", ["deploy/truenas/compose.yaml"])
def test_compose_needs_no_handmade_keys(compose):
    text = (Path(__file__).resolve().parents[3] / compose).read_text()
    for var in ("MG_INTERNAL_TOKEN}", "_APIKEY:?", "_HOOKKEY:?"):
        assert var not in text, f"{var} moet niet meer door de gebruiker worden ingevuld"
    assert "mediaguard-init" in text


def test_bootstrap_main_creates_layout_only_on_empty_dataset(tmp_path, monkeypatch):
    monkeypatch.setenv("MG_UID", str(os.getuid()))
    monkeypatch.setenv("MG_GID", str(os.getgid()))
    for name in ("appdata", "work", "data"):
        (tmp_path / name).mkdir()
    monkeypatch.setenv("MG_BOOTSTRAP_APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("MG_BOOTSTRAP_SECRETS", str(tmp_path / "appdata" / "secrets"))
    monkeypatch.setenv("MG_BOOTSTRAP_WORK", str(tmp_path / "work"))
    monkeypatch.setenv("MG_BOOTSTRAP_DATA", str(tmp_path / "data"))
    (tmp_path / "data" / "Series").mkdir()            # bestaande bibliotheek van de gebruiker
    bootstrap.main()
    assert sorted(p.name for p in (tmp_path / "data").iterdir()) == ["Series"]
    (tmp_path / "data" / "Series").rmdir()             # lege dataset → standaardindeling
    bootstrap.main()
    assert (tmp_path / "data" / "media" / "anime").is_dir()
    assert (tmp_path / "data" / "downloads" / "movies-anime").is_dir()


def test_bootstrap_continues_when_acl_forbids_chmod_chown(tmp_path, monkeypatch):
    """TrueNAS-datasets met NFSv4-ACL (aclmode=restricted) weigeren chmod/chown met EPERM."""
    monkeypatch.setenv("MG_UID", "568")
    monkeypatch.setenv("MG_GID", "568")

    def deny(*_a, **_k):
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(bootstrap.os, "chown", deny)
    monkeypatch.setattr(bootstrap.os, "chmod", deny)
    log = bootstrap.run(tmp_path / "secrets", [tmp_path / "core"], [])
    assert (tmp_path / "secrets" / "internal" / "token").read_text().strip()
    assert (tmp_path / "core").is_dir() and any("API-sleutel" in x for x in log)


def test_bootstrap_main_continues_when_data_dataset_unreadable(tmp_path, monkeypatch):
    """Geen leesrecht op de data-dataset mag de sleutelgeneratie niet blokkeren."""
    monkeypatch.setenv("MG_UID", str(os.getuid()))
    monkeypatch.setenv("MG_GID", str(os.getgid()))
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("MG_BOOTSTRAP_APPDATA", str(tmp_path / "app"))
    monkeypatch.setenv("MG_BOOTSTRAP_SECRETS", str(tmp_path / "app" / "secrets"))
    monkeypatch.setenv("MG_BOOTSTRAP_WORK", str(tmp_path / "work"))
    monkeypatch.setenv("MG_BOOTSTRAP_DATA", str(data))
    real_iterdir = Path.iterdir

    def iterdir(self):
        if self == data:
            raise PermissionError(13, "Permission denied")
        return real_iterdir(self)

    monkeypatch.setattr(Path, "iterdir", iterdir)
    assert bootstrap.main() == 0
    assert (tmp_path / "app" / "secrets" / "internal" / "token").read_text().strip()
    assert not (data / "media").exists()


def test_seerr_uses_lan_ip_not_tunnel_domain(ctx, monkeypatch):
    """Praktijk: UI geopend via media.voorbeeld.nl (tunnel) → Seerr kreeg dat domein als host,
    terwijl de Sonarr/Radarr-poorten alleen op het LAN openstaan."""
    from mediaguard.instances.registry import fill_public_urls, get_instance, seed_instances

    monkeypatch.delenv("MG_PUBLIC_HOST", raising=False)
    seed_instances(ctx)
    ctx.db.execute("UPDATE instances SET public_url=NULL")
    fill_public_urls(ctx, "media.voorbeeld.nl")
    assert "media.voorbeeld.nl" in (get_instance(ctx, "sonarr-series").public_url or "")
    monkeypatch.setenv("MG_PUBLIC_HOST", "192.0.2.10")
    fill_public_urls(ctx, "media.voorbeeld.nl")
    assert get_instance(ctx, "sonarr-series").public_url == "http://192.0.2.10:38989"


def test_seerr_root_falls_back_to_existing_folder():
    from mediaguard.provisioning.seerr_sync import pick_root

    roots = {"/data/media/Films", "/data/media/Anime Films"}
    assert pick_root(roots, "anime_movie") == "/data/media/Anime Films"
    assert pick_root(roots, "movie") == "/data/media/Films"
    assert pick_root(set(), "movie") is None
