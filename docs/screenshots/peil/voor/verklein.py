"""Peil-nulmeting MediaGuard — web-afdrukken verkleinen (alleen-lezen hulpscript, hoort niet bij de app).

Zet elke PNG in schermen/, tabs/ en toestanden/ om naar een 256-kleurenpalet zonder dithering (± 3× kleiner;
vlakken en tekst blijven gelijk, alleen anti-aliasing kan minimaal verschuiven). Kleur- en contrastmetingen komen
daarom NOOIT uit deze PNG's, maar uit de browser (metingen.json, axe) en uit contrast.json van de ontwerptaal.
De Android-afdrukken (android/) zijn al door hulp/android_verwerk.py verkleind.

Gebruik: python3 docs/screenshots/peil/voor/verklein.py   (vereist Pillow)
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent


def main() -> None:
    before = after = 0
    for png in sorted(p for d in ("schermen", "tabs", "toestanden") for p in (HERE / d).glob("*.png")):
        before += png.stat().st_size
        with Image.open(png) as im:
            if im.mode == "P":
                after += png.stat().st_size
                continue
            q = im.convert("RGB").quantize(colors=256, method=Image.Quantize.FASTOCTREE, dither=Image.Dither.NONE)
        q.save(png, optimize=True)
        after += png.stat().st_size
    print(f"PNG: {before / 1e6:.1f} MB → {after / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
