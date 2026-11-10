"""Release-pipeline (verbeterlijst 14, 23 en 40): workflows, versiemanifest en hulpscripts.

GitHub Actions kan hier niet draaien; deze tests bewaken wat wél lokaal te controleren is:
geen publicatie zonder tests en rooktest, nooit een bestaande tag overschrijven, het manifest
gelijk aan de Dockerfiles, en de AIO-rooktest die een gevulde oude database migreert.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
WF = ROOT / ".github" / "workflows"


def _wf(name: str) -> dict:
    data = yaml.safe_load((WF / name).read_text(encoding="utf-8"))
    data["on"] = data.pop(True, data.get("on"))  # YAML 1.1: 'on' wordt True
    return data


def _manifest() -> dict:
    return json.loads((ROOT / "deploy" / "release-manifest.json").read_text("utf-8"))


def _args(dockerfile: Path) -> dict[str, str]:
    return dict(re.findall(r"^ARG (\w+)=(\S*)$", dockerfile.read_text("utf-8"), re.M))


def _scripts(job: dict) -> str:
    return "\n".join(step.get("run", "") for step in job.get("steps", []))


def _smoke():
    spec = importlib.util.spec_from_file_location("aio_smoke", ROOT / "scripts" / "aio-smoke.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# -- workflows --------------------------------------------------------------------------------
def test_all_workflows_are_valid_yaml():
    for f in WF.glob("*.yml"):
        data = _wf(f.name)
        assert data["on"] and data["jobs"], f.name


def test_release_publishes_nothing_without_tests_and_smoke():
    jobs = _wf("release.yml")["jobs"]
    assert jobs["tests"]["uses"] == "./.github/workflows/ci.yml"

    def needs(name: str) -> set[str]:
        n = jobs[name].get("needs", [])
        return {n} if isinstance(n, str) else set(n)

    # niets bouwen of pushen voordat core, encoder-unit en web groen zijn
    assert {"meta", "tests"} <= needs("app-images")
    assert {"meta", "tests"} <= needs("fork-images")
    assert {"app-images", "fork-images"} <= needs("aio-image")
    assert "aio-image" in needs("aio-smoke")
    assert {"tests", "aio-smoke", "app-images", "fork-images", "aio-image"} <= needs("publish")
    assert "publish" in needs("summary") and "publish" in needs("tag")
    # alleen 'publish' zet echte tags; de bouwjobs pushen uitsluitend kandidaat-tags
    for name, job in jobs.items():
        script = _scripts(job)
        if name != "publish":
            assert "imagetools create" not in script, name
            assert "promote-image.sh" not in script, name
        for tag in re.findall(r'-t "([^"]+)"', script) + re.findall(r'docker push "([^"]+)"',
                                                                       script):
            if name != "publish":
                assert "CANDIDATE" in tag, f"{name} pusht {tag} zonder kandidaat-tag"
    # de AIO-rooktest draait echt het gebouwde image
    assert "scripts/aio-smoke.py" in _scripts(jobs["aio-smoke"])
    assert "${VERSION}-aio-${CANDIDATE}" in _scripts(jobs["aio-smoke"])
    # de AIO-tag als laatste, na core/web/encode
    pub = _scripts(jobs["publish"])
    assert pub.rindex("${VERSION}-aio") > pub.index("for n in core web encode")


def test_release_never_overwrites_an_existing_tag():
    jobs = _wf("release.yml")["jobs"]
    meta = _scripts(jobs["meta"])
    for ref in ("mediaguard-core:$VERSION", "mediaguard-web:$VERSION",
                "mediaguard-encode:$VERSION", "mediaguard-core:$VERSION-aio"):
        assert ref in meta
    assert "scripts/image-tag-exists.sh" in meta and "exit 1" in meta
    pub = _scripts(jobs["publish"])
    # elke echte tag via scripts/promote-image.sh (weigert een bestaande tag met een ander image)
    assert "imagetools create" not in pub
    assert pub.count("scripts/promote-image.sh") >= 3
    promote = (ROOT / "scripts" / "promote-image.sh").read_text("utf-8")
    assert '"$here/image-tag-exists.sh" "$t"' in promote and "niets overschreven" in promote
    # een bestaande fork-tag wordt alleen hergebruikt bij gelijke patches/Dockerfile
    fork = _scripts(jobs["fork-images"])
    assert "io.mediaguard.patches.sha256" in fork and "io.mediaguard.dockerfile.sha256" in fork
    assert "Verhoog MG_PATCH_REV" in fork
    for app in ("sonarr", "radarr"):
        build = (ROOT / "forks" / app / "build.sh").read_text("utf-8")
        # zelfde berekening als in release.yml, anders zou hergebruik nooit (of altijd) kloppen
        assert 'cat "$HERE"/patches/*.patch | sha256sum | cut -c1-16' in build
        assert 'sha256sum "$HERE/Dockerfile" | cut -c1-16' in build
    assert 'cat "$D"/patches/*.patch | sha256sum | cut -c1-16' in fork
    assert 'sha256sum "$D/Dockerfile" | cut -c1-16' in fork


def test_ci_is_reusable_and_tests_core_on_the_aio_python():
    ci = _wf("ci.yml")
    assert "workflow_call" in ci["on"]
    m = _manifest()["toolchain"]
    aio = ".".join(m["encode"]["python"].split(".")[:2])
    assert m["core"]["python_aio"] == aio
    core = ci["jobs"]["core"]
    assert set(core["strategy"]["matrix"]["python"]) == {m["core"]["python"], aio}
    assert "matrix.python" in json.dumps(core["steps"])
    enc = ci["jobs"]["encoder-unit"]
    assert any(s.get("with", {}).get("python-version") == aio for s in enc["steps"])
    # in release.yml (event push/workflow_dispatch) draaien core, encoder-unit en web wél,
    # de zware nachtelijke jobs niet (heavy staat voor workflow_call standaard uit)
    for name in ("core", "encoder-unit", "web"):
        assert ci["jobs"][name]["if"] == "github.event_name != 'schedule'"
    assert ci["on"]["workflow_call"]["inputs"]["heavy"]["default"] is False
    for name in ("encoder-image", "forks", "upstream-sync"):
        assert "inputs.heavy" in ci["jobs"][name]["if"]


def test_core_dockerfile_python_matches_manifest():
    m = _manifest()
    df = (ROOT / "apps" / "core" / "Dockerfile").read_text("utf-8")
    assert f"python:{m['toolchain']['core']['python']}-slim" in df
    assert df.count(m["images"]["core"]["base"]) >= 1


# -- manifest -------------------------------------------------------------------------------
@pytest.mark.parametrize("app", ["sonarr", "radarr"])
def test_manifest_matches_fork_dockerfile(app):
    m = _manifest()
    up = m["upstream"][app]
    args = _args(ROOT / "forks" / app / "Dockerfile")
    assert up["sha"] == args["UPSTREAM_SHA"]
    assert up["version"] == args["UPSTREAM_VERSION"]
    assert up["patch_rev"] == args["MG_PATCH_REV"]
    assert up["sdk_image"] == args["SDK_IMAGE"]
    assert up["runtime_image"] == args["RUNTIME_IMAGE"]
    assert up["node_image"] == args["NODE_IMAGE"]
    img = m["images"][f"{app}-fork"]
    assert img["tag"] == f"{args['UPSTREAM_VERSION']}-{args['MG_PATCH_REV']}"
    assert img["alias_tag"] == f"{args['UPSTREAM_SHA'][:7]}-{args['MG_PATCH_REV']}"


def test_manifest_matches_encoder_toolchain():
    enc = _manifest()["toolchain"]["encode"]
    args = _args(ROOT / "services" / "encoder" / "Dockerfile")
    df = (ROOT / "services" / "encoder" / "Dockerfile").read_text("utf-8")
    assert enc["ffmpeg"].startswith(args["FFMPEG_VERSION"] + " ")
    assert "geen libvmaf" not in enc["ffmpeg"] and "--enable-libvmaf" in df
    for block in (enc["ffmpeg_build"], enc["ffmpeg_hdr10plus"]):
        assert block["version"] == args["FFMPEG_VERSION"]
        assert block["source_sha256"] == args["FFMPEG_SHA256"]
    assert args["VMAF_COMMIT"] in enc["ffmpeg_build"]["libvmaf"]
    assert enc["dovi_tool"] == {"version": args["DOVI_TOOL_VERSION"],
                                "sha256": args["DOVI_TOOL_SHA256"]}
    assert enc["hdr10plus_tool_test_only"] == {"version": args["HDR10PLUS_TOOL_VERSION"],
                                               "sha256": args["HDR10PLUS_TOOL_SHA256"]}
    for wf in ("release.yml", "ci.yml"):
        env = _wf(wf)["env"]
        assert env["FFMPEG_HDR10PLUS_VERSION"] == args["FFMPEG_VERSION"], wf
        assert env["FFMPEG_HDR10PLUS_SHA256"] == args["FFMPEG_SHA256"], wf


def test_gpu_driver_and_runtime_are_pinned():
    """Een rebuild mag nooit stil een andere Intel-driver/QSV-runtime meenemen."""
    pins = _manifest()["toolchain"]["encode"]["gpu_runtime_apt"]
    args = _args(ROOT / "services" / "encoder" / "Dockerfile")
    df = (ROOT / "services" / "encoder" / "Dockerfile").read_text("utf-8")
    stages = {part.split()[2]: part for part in df.split("\nFROM ")[1:] if " AS " in part}
    runtime, build = stages["runtime"], stages["ffmpeg-build"]
    arg_of = {"intel-media-va-driver-non-free": "INTEL_MEDIA_DRIVER_VERSION",
              "libmfx-gen1.2": "LIBMFX_GEN_VERSION", "libvpl2": "LIBVPL_VERSION",
              "libigdgmm12": "LIBIGDGMM_VERSION", "libva2": "LIBVA_VERSION",
              "libva-drm2": "LIBVA_VERSION", "libva-x11-2": "LIBVA_VERSION",
              "libva-wayland2": "LIBVA_VERSION", "libdrm2": "LIBDRM_VERSION",
              "libdrm-common": "LIBDRM_VERSION"}
    assert set(pins) == set(arg_of)
    for pkg, arg in arg_of.items():
        assert pins[pkg] == args[arg], pkg
        assert f'"{pkg}=${{{arg}}}"' in runtime, f"{pkg} niet gepind in de runtime-stage"
    # bouw-headers gelijk aan de runtime, en ook alle broertjes die een -dev-pakket met
    # '(= versie)' vraagt: anders breekt de bouw zodra Ubuntu er één van bijwerkt
    build_pins = {"libvpl-dev": "LIBVPL_VERSION", "libvpl2": "LIBVPL_VERSION",
                  **{p: "LIBVA_VERSION" for p in ("libva-dev", "libva2", "libva-drm2",
                                                  "libva-x11-2", "libva-wayland2",
                                                  "libva-glx2")},
                  **{p: "LIBDRM_VERSION" for p in ("libdrm-dev", "libdrm2", "libdrm-common",
                                                   "libdrm-intel1", "libdrm-amdgpu1",
                                                   "libdrm-radeon1", "libdrm-nouveau2")}}
    for pkg, arg in build_pins.items():
        assert f'"{pkg}=${{{arg}}}"' in build, f"{pkg} niet gepind in de bouwstage"
    # geen ongepinde libva/libdrm/libvpl-pakketten naast de gepinde
    for stage in (runtime, build):
        installs = re.findall(r"apt-get \$APTOPT install (.*?);", stage, re.S)
        assert installs
        for words in (block.replace("\\\n", " ").split() for block in installs):
            for word in words:
                pkg = word.strip('"').split("=", 1)[0]
                if re.match(r"lib(va|drm|vpl)|intel-media|libmfx|libigdgmm", pkg):
                    assert "=" in word, f"{pkg} staat er zonder versie"
    # elke stage die de pins gebruikt, declareert de ARG opnieuw (anders is hij leeg)
    for stage in df.split("\nFROM ")[1:]:
        for arg in set(re.findall(r"\$\{(LIB\w+_VERSION|INTEL_\w+_VERSION)\}", stage)):
            assert f"\nARG {arg}\n" in stage, f"ARG {arg} ontbreekt in stage {stage[:30]!r}"
    assert _manifest()["toolchain"]["encode"]["intel_media_driver"].startswith(
        pins["intel-media-va-driver-non-free"].split("+")[0])


# -- hulpscripts ------------------------------------------------------------------------------
def _fake_docker(tmp_path: Path, stderr: str, code: int) -> dict[str, str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "docker"
    fake.write_text(f"#!/bin/sh\necho '{stderr}' >&2\nexit {code}\n")
    fake.chmod(0o755)
    return {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"}


@pytest.mark.parametrize(("stderr", "code", "expected"), [
    ("", 0, 0),
    ("manifest unknown", 1, 1),
    ("no such manifest: ghcr.io/lvos04/mediaguard-core:9.9.9", 1, 1),
    ("ERROR: ghcr.io/lvos04/mediaguard-core:9.9.9: not found", 1, 1),
    ("denied: permission_denied", 1, 2),
    ("Get https://ghcr.io/v2/: net/http: TLS handshake timeout", 1, 2),
])
def test_image_tag_exists_fails_closed(tmp_path, stderr, code, expected):
    if shutil.which("bash") is None:
        pytest.skip("bash ontbreekt")
    env = _fake_docker(tmp_path, stderr, code)
    r = subprocess.run(["bash", str(ROOT / "scripts" / "image-tag-exists.sh"),
                        "ghcr.io/lvos04/mediaguard-core:9.9.9"], env=env, capture_output=True,
                       text=True, check=False)
    assert r.returncode == expected, r.stderr
    assert r.stdout == ""


_FAKE_REGISTRY = r'''
import json, os, sys
state = os.environ["FAKE_REGISTRY"]
tags = json.load(open(state)) if os.path.exists(state) else {}
args = sys.argv[1:]
if os.environ.get("FAKE_REGISTRY_DOWN") and args[:2] == ["manifest", "inspect"]:
    print("Get https://ghcr.io/v2/: net/http: TLS handshake timeout", file=sys.stderr)
    sys.exit(1)
if args[:2] == ["manifest", "inspect"]:
    if args[-1] in tags:
        sys.exit(0)
    print("manifest unknown", file=sys.stderr)
    sys.exit(1)
if args[:3] == ["buildx", "imagetools", "inspect"]:
    if args[-1] not in tags:
        sys.exit(1)
    print(json.dumps({"mediaType": "application/vnd.oci.image.index.v1+json",
                      "digest": tags[args[-1]]}))
    sys.exit(0)
if args[:3] == ["buildx", "imagetools", "create"]:
    rest, src = args[3:-1], args[-1]
    for i in range(0, len(rest), 2):
        assert rest[i] == "-t"
        tags[rest[i + 1]] = tags[src]  # een kopie: zelfde digest
    json.dump(tags, open(state, "w"))
    open(state + ".log", "a").write(" ".join(rest[1::2]) + "\n")
    sys.exit(0)
sys.exit(2)
'''


def _promote(tmp_path: Path, tags: dict[str, str], *refs: str, down: bool = False):
    import sys

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    fake = bin_dir / "docker"
    fake.write_text(f"#!{sys.executable}\n{_FAKE_REGISTRY}")
    fake.chmod(0o755)
    state = tmp_path / "registry.json"
    state.write_text(json.dumps(tags))
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "FAKE_REGISTRY": str(state)}
    if down:
        env["FAKE_REGISTRY_DOWN"] = "1"
    r = subprocess.run(["bash", str(ROOT / "scripts" / "promote-image.sh"), *refs], env=env,
                       capture_output=True, text=True, check=False)
    log = tmp_path / "registry.json.log"
    created = log.read_text().splitlines() if log.exists() else []
    return r, json.loads(state.read_text()), created


def test_promote_publishes_candidate_and_never_overwrites(tmp_path):
    if shutil.which("bash") is None or shutil.which("python3") is None:
        pytest.skip("bash/python3 ontbreekt")
    cand = "ghcr.io/lvos04/mediaguard-core:1.2.2-kandidaat-7-1"
    final = "ghcr.io/lvos04/mediaguard-core:1.2.2"
    # 1. nieuw: de kandidaat komt onder de echte tag, met dezelfde digest
    r, tags, created = _promote(tmp_path, {cand: "sha256:aaa"}, cand, final)
    assert r.returncode == 0, r.stdout + r.stderr
    assert tags[final] == "sha256:aaa" and created == [final]
    # 2. 'Re-run failed jobs': de tag staat al op precies dit image → niets te doen
    r, tags, created = _promote(tmp_path, tags, cand, final)
    assert r.returncode == 0 and created == [final]  # geen tweede create
    # 3. de tag bestaat met een ander image → weigeren, niets overschreven
    other = {cand: "sha256:aaa", final: "sha256:bbb"}
    (tmp_path / "registry.json.log").unlink()
    r, tags, created = _promote(tmp_path, other, cand, final)
    assert r.returncode == 1 and "niets overschreven" in r.stdout
    assert tags[final] == "sha256:bbb" and created == []
    # 4. van twee tags bestaat er één al met een ander image → geen van beide wordt gezet
    alias = "ghcr.io/lvos04/mediaguard-sonarr-fork:bc54afb-mg3"
    r, tags, created = _promote(tmp_path, other, cand, alias, final)
    assert r.returncode == 1 and alias not in tags and created == []
    # 5. registry niet te bereiken → weigeren
    r, tags, created = _promote(tmp_path, {cand: "sha256:aaa"}, cand, final, down=True)
    assert r.returncode == 1 and final not in tags and created == []
    # 6. kandidaat ontbreekt → weigeren
    r, tags, created = _promote(tmp_path, {}, cand, final)
    assert r.returncode == 1 and "niet gevonden" in r.stdout and created == []


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c",
                           "user.email=t@t", "-c", "commit.gpgsign=false", *args],
                          capture_output=True, text=True, check=True).stdout.strip()


def _write_manifest(repo: Path, version: str, ffmpeg: str = "8.1.3") -> None:
    (repo / "deploy").mkdir(exist_ok=True)
    (repo / "deploy" / "release-manifest.json").write_text(json.dumps(
        {"schema": 1, "version": version, "toolchain": {"encode": {"version": ffmpeg}}},
        indent=2) + "\n")


def test_android_build_number_counts_commits_since_version_bump(tmp_path):
    if shutil.which("git") is None:
        pytest.skip("git ontbreekt")
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "scripts").mkdir()
    shutil.copy(ROOT / "scripts" / "android-build-number.sh", repo / "scripts")
    script = str(repo / "scripts" / "android-build-number.sh")

    def number() -> int:
        return int(subprocess.run(["bash", script], cwd=repo, capture_output=True, text=True,
                                  check=True).stdout)

    _write_manifest(repo, "1.2.1")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "Release 1.2.1")
    assert number() == 0  # de release-commit zelf
    _git(repo, "commit", "-q", "--allow-empty", "-m", "fix")
    # een geneste "version" (4 spaties) is geen versiewijziging
    _write_manifest(repo, "1.2.1", ffmpeg="8.1.4")
    _git(repo, "commit", "-qam", "ffmpeg")
    assert number() == 2
    _write_manifest(repo, "1.2.2")
    _git(repo, "commit", "-qam", "Release 1.2.2")
    assert number() == 0
    for i in range(101):
        _git(repo, "commit", "-q", "--allow-empty", "-m", f"c{i}")
    assert number() == 99  # nooit in het bereik van de volgende versie


def test_android_version_code_uses_build_number():
    gradle = (ROOT / "apps" / "android" / "app" / "build.gradle.kts").read_text("utf-8")
    assert 'System.getenv("MG_ANDROID_BUILD")' in gradle and ".coerceIn(0, 99)" in gradle
    assert "it * 100 + mgBuild" in gradle and "versionName = mgVersionName" in gradle
    wf = _wf("android.yml")
    steps = wf["jobs"]["build"]["steps"]
    checkout = next(s for s in steps if s.get("uses", "").startswith("actions/checkout"))
    assert checkout["with"]["fetch-depth"] == 0
    script = _scripts(wf["jobs"]["build"])
    assert "scripts/android-build-number.sh" in script and "MG_ANDROID_BUILD=" in script
    assert "MG_ANDROID_RELEASE=1" in script


# -- AIO-rooktest -----------------------------------------------------------------------------
FIXTURES = ROOT / "tests" / "fixtures" / "aio"


def test_smoke_picks_newest_fixture_not_newer_than_release(tmp_path):
    smoke = _smoke()
    for v in ("1.2.1", "1.3.0", "1.10.0"):
        (tmp_path / f"core-{v}.json").write_text(json.dumps({"version": v}))
        (tmp_path / f"core-{v}.sql").write_text("")
    assert smoke.pick_fixture(tmp_path, "1.2.5")[1]["version"] == "1.2.1"
    assert smoke.pick_fixture(tmp_path, "1.9.0")[1]["version"] == "1.3.0"
    assert smoke.pick_fixture(tmp_path, "1.10.0")[0].name == "core-1.10.0.sql"
    with pytest.raises(SystemExit):
        smoke.pick_fixture(tmp_path, "1.0.0")


def test_fixture_database_migrates_and_stays_usable(tmp_path, monkeypatch):
    """Wat de rooktest in het AIO-image doet, hier met de core-code van nu: de gevulde
    database van de vorige versie migreren, inloggen met de oude beheerder en alles terugvinden."""
    from fastapi.testclient import TestClient

    from mediaguard.app import create_app
    from mediaguard.config import load_settings
    from mediaguard.db import MIGRATIONS_DIR

    smoke = _smoke()
    current = __import__("mediaguard").__version__
    sql, fixture = smoke.pick_fixture(FIXTURES, current)
    data = tmp_path / "config" / "core"
    smoke.restore(sql, data / "mediaguard.db")
    settings = load_settings(data_dir=data, staging_dir=tmp_path / "staging",
                             quarantine_dir=tmp_path / "quarantine", start_background=False,
                             internal_token="t")
    monkeypatch.delenv("MG_RELEASE_MANIFEST", raising=False)
    app = create_app(settings)
    try:
        with TestClient(app) as c:
            assert c.get("/api/v1/health").json()["version"] == current
            assert c.get("/api/v1/auth/setup-status").json() == {"needs_setup": False}
            r = c.post("/api/v1/auth/login",
                       json={"username": fixture["user"], "password": fixture["password"]})
            assert r.status_code == 200, r.text
            info = c.get("/api/v1/admin/version").json()
            assert info["schema_versions"] == sorted(p.stem for p in MIGRATIONS_DIR.glob("*.sql"))
            eff = {e["key"]: e["value"] for e in c.get(
                "/api/v1/settings/effective",
                params={"keys": ",".join(fixture["settings"])}).json()}
            assert eff == fixture["settings"]
            expected = sorted(p.stem for p in MIGRATIONS_DIR.glob("*.sql"))

            # dezelfde HTTP-controles als in de container (check_api), via deze core;
            # alleen de cookie die de rooktest zelf doorgeeft telt
            def fake_http(url, body=None, headers=None):
                c.cookies.clear()
                path = url.removeprefix("http://aio")
                r = (c.post(path, json=body, headers=headers) if body is not None
                     else c.get(path, headers=headers))
                try:
                    payload = r.json()
                except ValueError:
                    payload = r.text
                return r.status_code, payload, r.headers.get_list("set-cookie")

            monkeypatch.setattr(smoke, "http", fake_http)
            assert smoke.check_api("http://aio", fixture, current, expected) == []
            # een andere versie, ander schema of verkeerd wachtwoord valt op
            assert any("/admin/version meldt" in p
                       for p in smoke.check_api("http://aio", fixture, "9.9.9", expected))
            assert any("release-manifest" in p
                       for p in smoke.check_api("http://aio", fixture, "9.9.9", expected))
            assert any("schemaversies" in p for p in smoke.check_api(
                "http://aio", fixture, current, [*expected, "999_nieuw"]))
            wrong = {**fixture, "password": "fout-wachtwoord-1"}
            assert any("inloggen" in p
                       for p in smoke.check_api("http://aio", wrong, current, expected))
    finally:
        app.state.ctx.db.close()
    assert smoke.verify_db(data / "mediaguard.db", fixture, expected) == []


def test_smoke_db_check_catches_problems(tmp_path):
    smoke = _smoke()
    sql, fixture = smoke.pick_fixture(FIXTURES, "99.0.0")
    db = tmp_path / "core" / "mediaguard.db"
    smoke.restore(sql, db)
    versions = list(fixture["schema_versions"])
    assert smoke.verify_db(db, fixture, versions) == []
    # een migratie die niet is toegepast, en dus ook geen back-up van vóór de migratie
    problems = smoke.verify_db(db, fixture, [*versions, "999_nieuw"])
    assert any("999_nieuw" in p for p in problems)
    assert any("voor-update" in p for p in problems)
    (db.parent / "backups" / "voor-update" / "voor-update-x").mkdir(parents=True)
    (db.parent / "backups" / "voor-update" / "voor-update-x" / "mediaguard.db").write_bytes(b"")
    assert not any("voor-update" in p for p in smoke.verify_db(db, fixture, [*versions,
                                                                             "999_nieuw"]))
    # een verloren instelling of beheerder valt op
    conn = sqlite3.connect(db)
    conn.execute("UPDATE settings_values SET value_json='14' WHERE key='backup.retention_count'")
    conn.execute("DELETE FROM users WHERE role='admin'")
    conn.commit()
    conn.close()
    problems = smoke.verify_db(db, fixture, versions)
    assert any("backup.retention_count" in p for p in problems)
    assert any("beheerder" in p for p in problems)


def test_smoke_requires_every_service_running_without_restarts():
    smoke = _smoke()
    ok = {"services": {n: {"state": "running", "restarts": 0, "hang_restarts": 0}
                       for n in smoke.SERVICES}}
    assert smoke.verify_services(ok) == []
    bad = json.loads(json.dumps(ok))
    bad["services"]["core"]["restarts"] = 1
    bad["services"]["encode"]["state"] = "restarting"
    del bad["services"]["web"]
    problems = smoke.verify_services(bad)
    assert len(problems) == 3
    # dezelfde diensten als mediaguard.aio start
    from mediaguard import aio

    names = {s.name for s in aio.build_specs({"MG_AIO_CONFIG": "/tmp/x"})}
    assert names == set(smoke.SERVICES)
