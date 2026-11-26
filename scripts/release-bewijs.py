#!/usr/bin/env python3
"""Schrijf de digests en SPDX-SBOM's van de te publiceren release-images."""

import argparse
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLATFORM = "linux/amd64"


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
    if not re.fullmatch(r"sha256:[a-f0-9]{64}", value):
        raise ValueError(f"Geen volledige manifestdigest voor {ref}.")
    return value


def maak_sbom(syft, ref, output):
    subprocess.run(
        [syft, "scan", f"registry:{ref}", "--platform", PLATFORM, "-o", f"spdx-json={output}"],
        check=True,
    )
    sbom = json.loads(output.read_text(encoding="utf-8"))
    if not sbom.get("spdxVersion"):
        raise ValueError(f"Geen geldige SPDX-SBOM voor {ref}.")


def verzamel(manifest, candidate, output, syft, registry_prefix=None):
    output.mkdir(parents=True)
    images = {}
    for naam, image in manifest["images"].items():
        pakket = image["image"]
        if registry_prefix:
            pakket = f"{registry_prefix}/{pakket.rsplit('/', 1)[1]}"
        final = f"{pakket}:{image['tag']}"
        bron = f"{final}-{candidate}"
        if naam.endswith("-fork") and not tag_bestaat(bron):
            bron = final
        pin = digest(bron)
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
    if not re.fullmatch(r"sha256:[a-f0-9]{64}", pin) or digest(f"{pakket}@{pin}") != pin:
        raise ValueError("De geteste digest kon niet in het register worden bevestigd.")
    sbom = "aio.spdx.json"
    if not json.loads((output / sbom).read_text(encoding="utf-8")).get("spdxVersion"):
        raise ValueError("Het SPDX-bewijs van de Custom App ontbreekt.")
    bewijs = {"app_id": "mediaguard", "version": manifest["version"], "platform": PLATFORM,
              "image": pakket, "tag": tag, "digest": pin,
              "images": {"aio": {"image": f"{pakket}:{tag}", "digest": pin, "sbom": sbom}}}
    (output / "digests.json").write_text(json.dumps(bewijs, indent=2) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    bron = parser.add_mutually_exclusive_group(required=True)
    bron.add_argument("--candidate")
    bron.add_argument("--aio-digest", help="Geteste registerdigest van de Custom App")
    parser.add_argument("--output", type=Path, default=Path("release"))
    parser.add_argument("--syft", default="syft")
    parser.add_argument("--registry-prefix", help="Publicatieregister met eigenaar voor deze run")
    args = parser.parse_args()
    manifest = json.loads((ROOT / "deploy/release-manifest.json").read_text(encoding="utf-8"))
    if args.aio_digest:
        bewijs_custom_app(manifest, args.aio_digest, args.output)
    else:
        verzamel(manifest, args.candidate, args.output, args.syft, args.registry_prefix)


if __name__ == "__main__":
    main()
