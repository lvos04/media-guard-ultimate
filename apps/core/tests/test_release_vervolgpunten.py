"""Lokale tegenproeven voor de inhoud en herkomst van het releasebewijs."""

import importlib.util
import json

import pytest
from test_release_pipeline import ROOT, _manifest, _scripts, _wf, spdx


@pytest.fixture
def bewijs():
    spec = importlib.util.spec_from_file_location("bewijs", ROOT / "scripts/release-bewijs.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("gebrek", ["leeg", "pakketten", "image", "relaties", "bron",
                                    "lege_maker", "naamloze_maker"])
def test_gebrekkige_spdx_blokkeert_bewijs(tmp_path, monkeypatch, bewijs, gebrek):
    pin = "sha256:" + "1" * 64
    sbom = spdx(pin)
    if gebrek == "leeg":
        sbom = {"spdxVersion": "SPDX-2.3"}
    elif gebrek == "pakketten":
        sbom["packages"].pop()
    elif gebrek == "image":
        sbom["packages"][0]["checksums"][0]["checksumValue"] = "2" * 64
    elif gebrek == "relaties":
        sbom["relationships"].clear()
    elif gebrek in {"lege_maker", "naamloze_maker"}:
        sbom["creationInfo"]["creators"] = ["" if gebrek == "lege_maker" else "Tool:  "]
    else:
        del sbom["creationInfo"]
    (tmp_path / "aio.spdx.json").write_text(json.dumps(sbom))
    monkeypatch.setattr(bewijs, "digest", lambda ref: pin)
    monkeypatch.setattr(bewijs.subprocess, "check_output", lambda *a, **kw: "{}")
    with pytest.raises(ValueError, match="SPDX"):
        bewijs.bewijs_custom_app(_manifest(), pin, tmp_path)
    assert not (tmp_path / "digests.json").exists()


def test_spdx_herhaalbaar_en_gebonden_aan_inhoud(tmp_path, monkeypatch, bewijs):
    pin = "sha256:" + "1" * 64
    monkeypatch.setattr(bewijs, "digest", lambda ref: pin)
    documenten = []
    for i in range(3):
        map_ = tmp_path / str(i)
        map_.mkdir()
        sbom = spdx(pin)
        sbom["documentNamespace"] += str(i)
        sbom["creationInfo"]["created"] = f"2026-01-0{i + 1}T00:00:00Z"
        if i == 2:
            sbom["packages"][1]["versionInfo"] = "2.0"
        pad = map_ / "aio.spdx.json"
        pad.write_text(json.dumps(sbom))
        bewijs.bewijs_custom_app(_manifest(), pin, map_)
        documenten.append(pad.read_bytes())
    assert documenten[0] == documenten[1]
    namespaces = [json.loads(document)["documentNamespace"] for document in documenten]
    assert namespaces[1] != namespaces[2]


def test_publicatie_weigert_afwijkende_bouwdigest(tmp_path, bewijs, monkeypatch):
    manifest = _manifest()
    bouw = tmp_path / "bouw"
    bouw.mkdir()
    pins = {naam: f"sha256:{i:064x}" for i, naam in enumerate(manifest["images"], 1)}
    for naam, pin in pins.items():
        if naam != "aio":
            (bouw / naam).write_text(pin)
        (tmp_path / f"{naam}.spdx.json").write_text(json.dumps(spdx(pin)))
    images = {naam: {"image": f"{img['image']}:{img['tag']}", "digest": pins[naam],
                     "sbom": naam + ".spdx.json"} for naam, img in manifest["images"].items()}
    document = {"version": manifest["version"], "platform": bewijs.PLATFORM, "images": images}
    pad = tmp_path / "digests.json"
    pad.write_text(json.dumps(document))
    bewijs.controleer_bouw(manifest, bouw, pins["aio"], tmp_path, None)
    monkeypatch.setattr(bewijs, "platform_digest", lambda ref: None)
    for naam, pin in pins.items():
        sbom = tmp_path / images[naam]["sbom"]
        sbom.unlink()
        with pytest.raises(FileNotFoundError):
            bewijs.controleer_bouw(manifest, bouw, pins["aio"], tmp_path, None)
        sbom.write_text(json.dumps(spdx("sha256:" + "f" * 64)))
        with pytest.raises(ValueError, match="SPDX"):
            bewijs.controleer_bouw(manifest, bouw, pins["aio"], tmp_path, None)
        sbom.write_text(json.dumps(spdx(pin)))
    for naam in ("aio", "web"):
        images[naam]["digest"] = "sha256:" + "f" * 64
        pad.write_text(json.dumps(document))
        with pytest.raises(ValueError, match="bouwdigest"):
            bewijs.controleer_bouw(manifest, bouw, pins["aio"], tmp_path, None)
        images[naam]["digest"] = pins[naam]
    jobs = _wf("release.yml", ROOT / ".forgejo/workflows")["jobs"]
    for naam in ("aio-smoke", "upgradebewijs", "release-bewijs", "publish"):
        assert any(step.get("env", {}).get("DIGEST") == "${{ needs.aio-image.outputs.digest }}"
                   for step in jobs[naam]["steps"])
    publicatie = _scripts(jobs["publish"])
    assert publicatie.index("--controleer-bouw") < publicatie.index("bash scripts/promote-image.sh")
