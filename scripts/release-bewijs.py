#!/usr/bin/env python3
"""Schrijf de digests en SPDX-SBOM's van de te publiceren release-images."""

import argparse
import hashlib
import json
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLATFORM = "linux/amd64"
PIN = re.compile(r"sha256:[a-f0-9]{64}")
MAKER = re.compile(r"(?:Tool|Person|Organization):\s*\S.*")


def tag_bestaat(ref):
    result = subprocess.run(["bash", ROOT / "scripts/image-tag-exists.sh", ref], check=False)
    if result.returncode not in (0, 1):
        result.check_returncode()
    return result.returncode == 0


def digest(ref):
    descriptor = subprocess.check_output(
        ["docker", "buildx", "imagetools", "inspect", "--format", "{{json .Manifest}}", ref],
        text=True,
    )
    value = json.loads(descriptor).get("digest", "")
    if not PIN.fullmatch(value):
        raise ValueError(f"Geen volledige manifestdigest voor {ref}.")
    return value


def platform_digest(ref):
    """Syft beschrijft bij een OCI-index het geselecteerde linux/amd64-manifest."""
    manifest = json.loads(subprocess.check_output(
        ["docker", "buildx", "imagetools", "inspect", "--raw", ref], text=True))
    pins = [m["digest"] for m in manifest.get("manifests", [])
            if m.get("platform", {}).get("os") == "linux"
            and m["platform"].get("architecture") == "amd64"]
    return pins[0] if len(pins) == 1 and PIN.fullmatch(pins[0]) else None


def controleer_spdx(ref, output):
    fout = ValueError(f"Onvolledig SPDX-bewijs of ander image voor {ref}.")
    sbom = json.loads(output.read_text(encoding="utf-8"))
    try:
        info = sbom["creationInfo"]
        makers = info["creators"]
        if (sbom["spdxVersion"] != "SPDX-2.3" or sbom["SPDXID"] != "SPDXRef-DOCUMENT"
                or sbom["dataLicense"] != "CC0-1.0" or not sbom["name"]
                or not sbom["documentNamespace"] or not isinstance(makers, list) or not makers
                or not all(isinstance(m, str) and MAKER.fullmatch(m) for m in makers)
                or not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", info["created"])):
            raise fout
        packages = {p["SPDXID"]: p for p in sbom["packages"]}
        if (len(packages) != len(sbom["packages"]) or len(packages) < 2
                or any(not p["name"] or not p["downloadLocation"] for p in packages.values())):
            raise fout
        relations = {(r["spdxElementId"], r["relationshipType"], r["relatedSpdxElement"])
                     for r in sbom["relationships"]}
        described = set(sbom.get("documentDescribes", []))
        described.update(b for a, rel, b in relations if a == sbom["SPDXID"] and rel == "DESCRIBES")
        root, = [p for i, p in packages.items()
                 if i in described and p.get("primaryPackagePurpose") == "CONTAINER"]
        checksums = {"sha256:" + c["checksumValue"] for c in root["checksums"]
                     if c["algorithm"] == "SHA256"}
        pin = ref.rsplit("@", 1)[-1]
        if not PIN.fullmatch(pin) or (pin not in checksums and platform_digest(ref) not in checksums):
            raise fout
        image_id = root["SPDXID"]
        if not any(p.get("versionInfo") and (
                (i, "CONTAINED_BY", image_id) in relations or (image_id, "CONTAINS", i) in relations)
                for i, p in packages.items() if i != image_id):
            raise fout
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(str(fout)) from exc
    return sbom


def normaliseer_spdx(ref, output):
    sbom = controleer_spdx(ref, output)
    epoch = int(subprocess.check_output(["git", "show", "-s", "--format=%ct", "HEAD"],
                                        cwd=ROOT, text=True))
    created = datetime.fromtimestamp(epoch, UTC)
    sbom["creationInfo"]["created"] = created.strftime("%Y-%m-%dT%H:%M:%SZ")
    del sbom["documentNamespace"]
    inhoud = json.dumps(sbom, sort_keys=True, separators=(",", ":")).encode()
    sbom["documentNamespace"] = "https://spdx.org/spdxdocs/mediaguard-" + hashlib.sha256(inhoud).hexdigest()
    output.write_text(json.dumps(sbom, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def maak_sbom(syft, ref, output):
    subprocess.run(
        [syft, "scan", f"registry:{ref}", "--platform", PLATFORM, "-o", f"spdx-json={output}"],
        check=True,
    )
    normaliseer_spdx(ref, output)


def image_tag(image, registry_prefix):
    pakket = image["image"]
    if registry_prefix:
        pakket = f"{registry_prefix}/{pakket.rsplit('/', 1)[1]}"
    return f"{pakket}:{image['tag']}"


def bouwdigests(manifest, map_, aio):
    pins = {naam: aio if naam == "aio" else (map_ / naam).read_text().strip()
            for naam in manifest["images"]}
    if not all(pin and PIN.fullmatch(pin) for pin in pins.values()):
        raise ValueError("Ongeldige bouwdigest.")
    return pins


def controleer_bouw(manifest, map_, aio, output, registry_prefix):
    pins = bouwdigests(manifest, map_, aio)
    verwacht = {naam: {"image": image_tag(image, registry_prefix), "digest": pins[naam],
                      "sbom": f"{naam}.spdx.json"} for naam, image in manifest["images"].items()}
    bewijs = json.loads((output / "digests.json").read_text())
    if bewijs != {"version": manifest["version"], "platform": PLATFORM, "images": verwacht}:
        raise ValueError("Releasebewijs wijkt af van de bouwdigests.")
    for image in verwacht.values():
        controleer_spdx(f"{image['image']}@{image['digest']}", output / image["sbom"])


def verzamel(manifest, candidate, output, syft, registry_prefix=None, *, pins=None):
    output.mkdir(parents=True)
    images = {}
    for naam, image in manifest["images"].items():
        final = image_tag(image, registry_prefix)
        if pins is None:
            bron = f"{final}-{candidate}"
            if naam.endswith("-fork") and not tag_bestaat(bron):
                bron = final
            pin = digest(bron)
        else:
            pin = pins[naam]
        sbom = f"{naam}.spdx.json"
        maak_sbom(syft, f"{final}@{pin}", output / sbom)
        images[naam] = {"image": final, "digest": pin, "sbom": sbom}
        print(f"{naam}: manifestdigest en SBOM vastgelegd.")
    resultaat = {"version": manifest["version"], "platform": PLATFORM, "images": images}
    (output / "digests.json").write_text(json.dumps(resultaat, indent=2) + "\n", encoding="utf-8")


def bewijs_custom_app(manifest, pin, output):
    """Koppel het SBOM en de TrueNAS-velden aan de geteste registerdigest."""
    aio = manifest["images"]["aio"]
    pakket, tag = aio["image"], aio["tag"]
    if not PIN.fullmatch(pin) or digest(f"{pakket}@{pin}") != pin:
        raise ValueError("De geteste digest kon niet in het register worden bevestigd.")
    sbom = "aio.spdx.json"
    normaliseer_spdx(f"{pakket}@{pin}", output / sbom)
    bewijs = {"app_id": "mediaguard", "version": manifest["version"], "platform": PLATFORM,
              "image": pakket, "tag": tag, "digest": pin,
              "images": {"aio": {"image": f"{pakket}:{tag}", "digest": pin, "sbom": sbom}}}
    (output / "digests.json").write_text(json.dumps(bewijs, indent=2) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    bron = parser.add_mutually_exclusive_group(required=True)
    bron.add_argument("--candidate")
    bron.add_argument("--aio-digest", help="Geteste registerdigest van de Custom App")
    bron.add_argument("--bouwmap", type=Path, help="Bouwdigests van de losse onderdelen")
    bron.add_argument("--controleer-bouw", type=Path, help="Vergelijk alle digests vóór promotie")
    parser.add_argument("--geteste-aio", help="Digest uit de bouwjob voor de Custom App")
    parser.add_argument("--output", type=Path, default=Path("release"))
    parser.add_argument("--syft", default="syft")
    parser.add_argument("--registry-prefix", help="Publicatieregister met eigenaar voor deze run")
    args = parser.parse_args()
    manifest = json.loads((ROOT / "deploy/release-manifest.json").read_text(encoding="utf-8"))
    if args.aio_digest:
        bewijs_custom_app(manifest, args.aio_digest, args.output)
    elif args.controleer_bouw:
        controleer_bouw(manifest, args.controleer_bouw, args.geteste_aio, args.output,
                        args.registry_prefix)
    elif args.bouwmap:
        pins = bouwdigests(manifest, args.bouwmap, args.geteste_aio)
        verzamel(manifest, None, args.output, args.syft, args.registry_prefix, pins=pins)
    else:
        verzamel(manifest, args.candidate, args.output, args.syft, args.registry_prefix)


if __name__ == "__main__":
    main()
