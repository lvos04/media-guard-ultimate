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
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
WF = ROOT / ".github" / "workflows"


def _wf(name: str, workflows: Path = WF) -> dict:
    data = yaml.safe_load((workflows / name).read_text(encoding="utf-8"))
    data["on"] = data.pop(True, data.get("on"))  # YAML 1.1: 'on' wordt True
    return data


def _manifest() -> dict:
    return json.loads((ROOT / "deploy" / "release-manifest.json").read_text("utf-8"))


def _args(dockerfile: Path) -> dict[str, str]:
    return dict(re.findall(r"^ARG (\w+)=(\S*)$", dockerfile.read_text("utf-8"), re.M))


def _scripts(job: dict) -> str:
    return "\n".join(step.get("run", "") for step in job.get("steps", []))


@pytest.mark.parametrize("filename", ("deploy/aio/Dockerfile",
                                     "deploy/upgradebewijs/Dockerfile.kandidaat"))
def test_aio_healthcheck_uses_exec_form(filename):
    dockerfile = (ROOT / filename).read_text("utf-8")
    assert 'CMD ["/usr/local/bin/mg-aio-healthcheck"]' in dockerfile
    assert 'ENTRYPOINT ["/opt/venv/bin/python", "-m", "mediaguard.aio"]' in dockerfile
    assert ("COPY --from=core --chmod=0755 /mediaguard/aio_healthcheck.py "
            "/usr/local/bin/mg-aio-healthcheck") in dockerfile


def _smoke():
    spec = importlib.util.spec_from_file_location("aio_smoke", ROOT / "scripts" / "aio-smoke.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("uitkomst", ["aanmaak geweigerd", "start geweigerd", "gestart"])
def test_aio_rooktest_beheert_alleen_een_eigen_container(tmp_path, monkeypatch, uitkomst):
    smoke = _smoke()
    container_id = "abcd" * 16
    aanroepen = []
    args = SimpleNamespace(
        manifest=ROOT / "deploy" / "release-manifest.json", fixtures=FIXTURES,
        exceptions=UITZONDERINGEN, version=_manifest()["version"], image="lokaal",
        port=0, timeout=1, stop_timeout=1, keep=False,
    )

    def docker(argv, **_kw):
        assert argv[0] == "docker"
        aanroepen.append(argv)
        if argv[1] == "run":
            if uitkomst == "aanmaak geweigerd":
                return subprocess.CompletedProcess(argv, 1, "", "containernaam is bezet")
            if "--cidfile" in argv:
                Path(argv[argv.index("--cidfile") + 1]).write_text(container_id, encoding="ascii")
            if uitkomst == "start geweigerd":
                return subprocess.CompletedProcess(argv, 1, "", "containerstart geweigerd")
            return subprocess.CompletedProcess(argv, 0, container_id, "")
        return subprocess.CompletedProcess(argv, 0, "0" if argv[1] == "inspect" else "", "")

    def readiness(container, *_a):
        aanroepen.append(["wacht", container])
        raise RuntimeError("container werd niet klaar")

    monkeypatch.setattr(smoke.subprocess, "run", docker)
    monkeypatch.setattr(smoke, "wait_until_ready", readiness)
    monkeypatch.setattr(smoke, "set_fixture_owner", lambda *_: None)
    monkeypatch.setattr(smoke, "tempfile", SimpleNamespace(
        mkdtemp=lambda **_kw: str(tmp_path / "proef"),
    ))
    fouten, _ = smoke.run(args, root_start=True)

    assert aanroepen[0][1] == "run"
    if uitkomst == "aanmaak geweigerd":
        assert "containernaam is bezet" in fouten[0]
        assert len(aanroepen) == 1
        return
    if uitkomst == "start geweigerd":
        assert "containerstart geweigerd" in fouten[0]
        eerste_opruiming = 1
    else:
        assert "container werd niet klaar" in fouten[0]
        assert aanroepen[1] == ["wacht", container_id]
        eerste_opruiming = 2
    assert aanroepen[eerste_opruiming:] == [
        ["docker", "exec", "--user", "568:568", container_id, "cat",
         "/config/core/supervisor.json"],
        ["docker", "stop", "-t", "1", container_id],
        ["docker", "inspect", "-f", "{{.State.ExitCode}}", container_id],
        ["docker", "logs", "--tail", "200", container_id],
        ["docker", "rm", "-f", container_id],
    ]


def test_smoke_checks_healthcheck_for_both_uids(monkeypatch):
    smoke = _smoke()
    calls = []
    zero = "0000000000000000"
    status = {
        "Uid": "568 568 568 568", "Gid": "568 568 568 568",
        "CapEff": zero, "CapPrm": zero, "CapInh": zero, "CapAmb": zero,
        "CapBnd": zero, "NoNewPrivs": "1",
    }

    def docker(*args):
        calls.append(args)
        fields = dict(status)
        return "\n".join(f"{key}: {value}" for key, value in fields.items())

    monkeypatch.setattr(smoke, "docker", docker)
    assert not smoke.verify_healthcheck("proef", root_start=True)
    status["CapBnd"] = "00000000a80425fb"
    assert not smoke.verify_healthcheck("proef", root_start=False)
    assert calls == [("exec", "proef", "/usr/local/bin/mg-aio-healthcheck", "--controle")] * 2
    status["Uid"] = "0 0 0 0"
    status["NoNewPrivs"] = "0"
    del status["CapPrm"]
    problems = smoke.verify_healthcheck("proef", root_start=True)
    assert all(any(field in problem for problem in problems)
               for field in ("Uid", "NoNewPrivs", "CapPrm", "CapBnd"))


@pytest.mark.parametrize("root_start", (True, False))
def test_smoke_checks_every_descendant_and_only_allows_the_web_subtree(root_start):
    smoke = _smoke()
    zero = "0000000000000000"

    def fields(ppid, identity):
        return {
            "PPid": str(ppid), "Uid": f"{identity} " * 4, "Gid": f"{identity} " * 4,
            "CapEff": zero, "CapPrm": zero, "CapInh": zero, "CapAmb": zero,
            "CapBnd": zero if root_start else "00000000a80425fb", "NoNewPrivs": "1", "Groups": "",
        }

    processes = {"1": (fields(0, 568), False), "2": (fields(1, 568), False),
                 "3": (fields(2, 568), False), "4": (fields(0, 0), False)}
    web_id = 65534 if root_start else 568
    if root_start:
        processes["5"] = (fields(1, web_id), True)
    processes["6"] = (fields(5 if root_start else 1, web_id), False)
    services = {"core": {"pid": 2}, "web": {"pid": 6}}
    assert not smoke.verify_process_tree(processes, services, root_start)

    processes["3"][0]["Uid"] = "0 0 0 0"
    del processes["3"][0]["NoNewPrivs"]
    processes["6"][0]["PPid"] = "0"
    problems = smoke.verify_process_tree(processes, services, root_start)
    assert any("PID 3" in problem and "Uid" in problem for problem in problems)
    assert any("PID 3" in problem and "NoNewPrivs" in problem for problem in problems)
    assert any("dienst web" in problem for problem in problems)


# -- workflows --------------------------------------------------------------------------------
def test_all_workflows_are_valid_yaml():
    for f in WF.glob("*.yml"):
        data = _wf(f.name)
        assert data["on"] and data["jobs"], f.name


def test_forgejo_behoudt_de_linuxjobs_en_publicatiepoorten():
    workflows = ROOT / ".forgejo" / "workflows"
    for naam in ("android.yml", "ci.yml", "release.yml"):
        origineel = _wf(naam)["jobs"]
        jobs = _wf(naam, workflows)["jobs"]
        assert origineel.keys() <= jobs.keys()
        for jobnaam, job in origineel.items():
            assert jobs[jobnaam].get("needs", []) == job.get("needs", [])

    release = _wf("release.yml", workflows)
    aanroep = release["jobs"]["tests"]
    assert aanroep["uses"] == "./.forgejo/workflows/ci.yml"
    assert aanroep["secrets"] == {"BRON_LEESTOKEN": "${{ secrets.BRON_LEESTOKEN }}"}
    assert release["env"]["REGISTRY"] + "/" + release["env"]["OWNER"] == "git.medialeon.nl/leon"
    assert '--registry-prefix "$REGISTRY/$OWNER"' in _scripts(release["jobs"]["release-bewijs"])
    ci = _wf("ci.yml", workflows)
    # De server verwerkt secrets op de aanroepende job bij het inplannen van een push.
    assert "secrets" not in ci["on"]["workflow_call"]


def test_forgejo_kan_elke_job_afzonderlijk_inlezen():
    # De server slaat iedere job afzonderlijk op. Een anker uit een andere job
    # werkt daarom ook niet als de volledige workflow geldige YAML is.
    for pad in (ROOT / ".forgejo" / "workflows").glob("*.yml"):
        bron = pad.read_text(encoding="utf-8")
        jobs = next(node for key, node in yaml.compose(bron).value if key.value == "jobs")
        for naam, job in jobs.value:
            start = naam.start_mark.index - naam.start_mark.column
            assert naam.value in yaml.safe_load(bron[start:job.end_mark.index]), pad.name


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
    assert "release-bewijs" in needs("publish") and "aio-image" in needs("release-bewijs")
    assert "scripts/release-bewijs.py" in _scripts(jobs["release-bewijs"])
    assert "release/digests.json" in _scripts(jobs["publish"])
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
    series = re.escape(m["toolchain"]["core"]["python"])
    bases = re.findall(r"^FROM (\S*library/python:\S+)", df, re.M)
    assert len(bases) == 2
    assert bases == [m["images"]["core"]["base"]] * 2
    base = rf"mirror\.gcr\.io/library/python:{series}\.\d+-slim-bookworm@sha256:[a-f0-9]{{64}}"
    assert re.fullmatch(base, bases[0])


@pytest.mark.parametrize("registry_prefix", [None, "git.medialeon.nl/leon"])
def test_release_bewijs_dekt_alle_images_en_gebruikt_hun_digest(tmp_path, monkeypatch,
                                                               registry_prefix):
    spec = importlib.util.spec_from_file_location(
        "release_bewijs", ROOT / "scripts" / "release-bewijs.py",
    )
    assert spec and spec.loader
    bewijs = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bewijs)

    manifest = _manifest()
    candidate = "kandidaat-7-1"
    pins = {}
    scans = []

    def digest(ref):
        pins[ref] = f"sha256:{len(pins) + 1:064x}"
        return pins[ref]

    def scan(syft, ref, path):
        assert syft == "syft"
        scans.append(ref)
        path.write_text('{"spdxVersion": "SPDX-2.3"}', encoding="utf-8")

    monkeypatch.setattr(bewijs, "digest", digest)
    monkeypatch.setattr(bewijs, "maak_sbom", scan)
    monkeypatch.setattr(bewijs, "tag_bestaat", lambda ref: "-radarr-fork:" in ref)
    uitvoer = tmp_path / "release"
    bewijs.verzamel(manifest, candidate, uitvoer, "syft", registry_prefix)
    resultaat = json.loads((uitvoer / "digests.json").read_text(encoding="utf-8"))
    assert resultaat["version"] == manifest["version"]
    assert resultaat["platform"] == "linux/amd64"
    assert resultaat["images"].keys() == manifest["images"].keys()
    for naam, entry in resultaat["images"].items():
        image = manifest["images"][naam]
        prefix = registry_prefix or manifest["registry"]
        assert entry["image"] == f"{prefix}/{image['image'].split('/')[-1]}:{image['tag']}"
        bron = entry["image"] if naam == "sonarr-fork" else f"{entry['image']}-{candidate}"
        assert entry["digest"] == pins[bron]
        assert f"{entry['image']}@{entry['digest']}" in scans
        assert (uitvoer / entry["sbom"]).is_file()

    # Bij een scannerfout mag het bewijsbestand niet worden geschreven.
    def weigert(*_):
        raise RuntimeError("scanner geweigerd")

    monkeypatch.setattr(bewijs, "maak_sbom", weigert)
    mislukt = tmp_path / "mislukt"
    with pytest.raises(RuntimeError, match="scanner geweigerd"):
        bewijs.verzamel(manifest, candidate, mislukt, "syft")
    assert not (mislukt / "digests.json").exists()


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
@pytest.fixture
def programmamap(tmp_path):
    # Op een noexec-datavolume kan de shell de Docker-simulator niet via PATH starten.
    with tempfile.TemporaryDirectory(dir=os.environ.get("MG_CI_PROGRAMMAMAP", tmp_path)) as mapnaam:
        yield Path(mapnaam)


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
def test_image_tag_exists_fails_closed(programmamap, stderr, code, expected):
    if shutil.which("bash") is None:
        pytest.skip("bash ontbreekt")
    env = _fake_docker(programmamap, stderr, code)
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

def manifest(ref):
    if "@" not in ref:
        return tags.get(ref)
    tag, digest = ref.rsplit("@", 1)
    repo = tag.rsplit(":", 1)[0]
    if any(other.rsplit(":", 1)[0] == repo and value == digest for other, value in tags.items()):
        return digest

if os.environ.get("FAKE_REGISTRY_DOWN") and args[:2] == ["manifest", "inspect"]:
    print("Get https://ghcr.io/v2/: net/http: TLS handshake timeout", file=sys.stderr)
    sys.exit(1)
if args[:2] == ["manifest", "inspect"]:
    if args[-1] in tags:
        sys.exit(0)
    print("manifest unknown", file=sys.stderr)
    sys.exit(1)
if args[:3] == ["buildx", "imagetools", "inspect"]:
    found = manifest(args[-1])
    if not found:
        sys.exit(1)
    print(json.dumps({"mediaType": "application/vnd.oci.image.index.v1+json",
                      "digest": found}))
    sys.exit(0)
if args[:3] == ["buildx", "imagetools", "create"]:
    assert args[3] == "--prefer-index=false"
    rest, src = args[4:-1], args[-1]
    digest = manifest(src)
    assert "@" in src and digest
    for i in range(0, len(rest), 2):
        assert rest[i] == "-t"
        tags[rest[i + 1]] = digest  # een kopie: zelfde digest
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


def test_promote_publishes_candidate_and_never_overwrites(programmamap):
    if shutil.which("bash") is None or shutil.which("python3") is None:
        pytest.skip("bash/python3 ontbreekt")
    cand = "ghcr.io/lvos04/mediaguard-core:1.2.2-kandidaat-7-1"
    final = "ghcr.io/lvos04/mediaguard-core:1.2.2"
    # 1. nieuw: de kandidaat komt onder de echte tag, met dezelfde digest
    r, tags, created = _promote(programmamap, {cand: "sha256:aaa"}, cand, final)
    assert r.returncode == 0, r.stdout + r.stderr
    assert tags[final] == "sha256:aaa" and created == [final]
    # 2. 'Re-run failed jobs': de tag staat al op precies dit image → niets te doen
    r, tags, created = _promote(programmamap, tags, cand, final)
    assert r.returncode == 0 and created == [final]  # geen tweede create
    # 3. de tag bestaat met een ander image → weigeren, niets overschreven
    other = {cand: "sha256:aaa", final: "sha256:bbb"}
    (programmamap / "registry.json.log").unlink()
    r, tags, created = _promote(programmamap, other, cand, final)
    assert r.returncode == 1 and "niets overschreven" in r.stdout
    assert tags[final] == "sha256:bbb" and created == []
    # 4. van twee tags bestaat er één al met een ander image → geen van beide wordt gezet
    alias = "ghcr.io/lvos04/mediaguard-sonarr-fork:bc54afb-mg3"
    r, tags, created = _promote(programmamap, other, cand, alias, final)
    assert r.returncode == 1 and alias not in tags and created == []
    # 5. registry niet te bereiken → weigeren
    r, tags, created = _promote(programmamap, {cand: "sha256:aaa"}, cand, final, down=True)
    assert r.returncode == 1 and final not in tags and created == []
    # 6. kandidaat ontbreekt → weigeren
    r, tags, created = _promote(programmamap, {}, cand, final)
    assert r.returncode == 1 and "niet gevonden" in r.stdout and created == []


def test_promote_uses_the_release_evidence_digest(programmamap):
    final = "ghcr.io/lvos04/mediaguard-core:2.0.0"
    cand = f"{final}-kandidaat-7-1"
    proved = f"{final}@sha256:aaa"
    # De doeltag bestaat nog niet. Het aangetoonde manifest staat wel onder de kandidaat-tag.
    r, tags, created = _promote(programmamap, {cand: "sha256:aaa"}, proved, final)
    assert r.returncode == 0, r.stdout + r.stderr
    assert tags[final] == "sha256:aaa" and created == [final]


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
UITZONDERINGEN = FIXTURES / "upgradebewijs-uitzonderingen.json"


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
    # HARDE EIS: de meting per tabel van vóór de start (elke tabel van de fixture is gevuld)
    voor, niet_gevuld = smoke.measure(tmp_path / "config", fixture)
    assert niet_gevuld == []
    assert len(voor["databases"]["core/mediaguard.db"]["tabellen"]) >= 100
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
    # … en na de migratie en de start: per tabel niets verloren (zelfde regels als de poort)
    verloren, rapport = smoke.compare(tmp_path / "config", voor, UITZONDERINGEN, "Per tabel")
    assert verloren == [], verloren
    assert "✅ niets verloren" in rapport and "| core/mediaguard.db | encoding_jobs |" in rapport
    for domein in ("jobs", "quarantine_items", "replacement_transactions", "wanted_items",
                   "audit_events", "settings_values", "mediaserver_actions",
                   "mediaserver_pending", "mediaserver_watch"):
        assert f"| core/mediaguard.db | {domein} |" in rapport, domein


def test_smoke_per_tabel_ziet_elk_verlies(tmp_path):
    """De rooktest is rood bij één verloren rij, één gewijzigde waarde of een fixture die niet
    gevuld is zoals zijn metadata zegt; nieuwe rijen, kolommen en tabellen mogen."""
    smoke = _smoke()
    sql, fixture = smoke.pick_fixture(FIXTURES, "99.0.0")
    config = tmp_path / "config"
    db = config / "core" / "mediaguard.db"
    smoke.restore(sql, db)
    voor, niet_gevuld = smoke.measure(config, fixture)
    assert niet_gevuld == []
    assert smoke.compare(config, voor, UITZONDERINGEN, "x")[0] == []
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE nieuw_in_deze_versie(id INTEGER PRIMARY KEY, x TEXT)")
    conn.execute("ALTER TABLE mediaserver_actions ADD COLUMN nieuwe_kolom INTEGER")
    conn.execute("INSERT INTO mediaserver_watch(path,title,user_name,last_seen) "
                 "VALUES ('/data/nieuw.mkv','t','u','2026-11-11T00:00:00+00:00')")
    conn.commit()
    assert smoke.compare(config, voor, UITZONDERINGEN, "x")[0] == []      # alleen erbij: goed
    conn.execute("DELETE FROM quarantine_items WHERE id=(SELECT MIN(id) FROM quarantine_items)")
    conn.execute("UPDATE encoding_jobs SET status='queued' WHERE id=(SELECT MIN(id) FROM "
                 "encoding_jobs)")
    conn.execute("DELETE FROM settings_values WHERE key='ui.theme'")
    conn.commit()
    conn.close()
    problemen, rapport = smoke.compare(config, voor, UITZONDERINGEN, "x")
    tekst = "\n".join(problemen)
    assert "quarantine_items: 1 rij(en) verloren" in tekst
    assert "encoding_jobs: 1 rij(en) gewijzigd" in tekst and "status" in tekst
    assert "settings_values: 1 rij(en) verloren" in tekst
    assert "❌ verlies of fout" in rapport
    # een fixture die minder bevat dan zijn metadata zegt: niets bewezen, dus rood
    leeg = tmp_path / "leeg"
    smoke.restore(sql, leeg / "core" / "mediaguard.db")
    conn = sqlite3.connect(leeg / "core" / "mediaguard.db")
    conn.execute("DELETE FROM wanted_items")
    conn.commit()
    conn.close()
    assert any("wanted_items" in p for p in smoke.measure(leeg, fixture)[1])
    assert any("ontbreekt" in p for p in smoke.measure(tmp_path / "bestaat-niet", fixture)[1])


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
