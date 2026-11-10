"""Peil-review MediaGuard (stap 5) — uitvoer van review.mjs overnemen in deze map (alleen-lezen hulpscript).

- kopieert afdrukken/ en toestanden/ uit de uitvoermap (PEIL_UIT, buiten de repo) en zet elke PNG om naar een
  256-kleurenpalet zonder dithering (± 3× kleiner). Meet NOOIT contrast in deze PNG's: dat komt uit review.json.
- schrijft review.json, maar zonder iets dat op een geheim lijkt: een ntfy-onderwerp (/mediaguard-…) wordt
  vervangen door "<ntfy-onderwerp>" (het is een kanaalnaam van de testomgeving, maar wie hem kent kan meelezen).

Gebruik: python3 -I docs/screenshots/peil/review/overnemen.py /pad/naar/PEIL_UIT   (vereist Pillow)
"""

from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent
NTFY = re.compile(r"(ntfy\.sh/)[A-Za-z0-9_-]+")


def main(src: Path) -> None:
    n = before = after = 0
    for sub in ("afdrukken", "toestanden"):
        (HERE / sub).mkdir(exist_ok=True)
        for png in sorted((src / sub).glob("*.png")):
            dst = HERE / sub / png.name
            shutil.copyfile(png, dst)
            before += dst.stat().st_size
            with Image.open(dst) as im:
                q = im.convert("RGB").quantize(colors=256, method=Image.Quantize.FASTOCTREE, dither=Image.Dither.NONE)
            q.save(dst, optimize=True)
            after += dst.stat().st_size
            n += 1
    text = NTFY.sub(r"\1<ntfy-onderwerp>", (src / "review.json").read_text(encoding="utf-8"))
    json.loads(text)  # moet geldige JSON blijven
    (HERE / "review.json").write_text(text, encoding="utf-8")
    print(f"{n} afdrukken, {before / 1e6:.1f} MB → {after / 1e6:.1f} MB; review.json overgenomen")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
