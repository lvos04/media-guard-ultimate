"""Paparazzi-afdrukken (device-resolutie, 440 dpi) -> docs/screenshots/peil/voor/android/.
- naam: <scherm>-<licht|donker>-<360|393|393-volledig>.png
- schaal naar 2 px per dp (Lanczos), 256-kleurenpalet zonder dithering (contrast NOOIT uit deze PNG's meten)
- 'volledig' (viewport 2618 dp hoog): lege ruimte tussen de laatste inhoud en de ondernavigatie weggesneden
"""
import sys, pathlib, json
from PIL import Image

src, dst = map(pathlib.Path, sys.argv[1:3])
dst.mkdir(parents=True, exist_ok=True)
NAMES = {
    "overzicht": "overzicht", "overzichtRustig": "overzicht-alles-in-orde", "overzichtNoodstop": "overzicht-noodstop",
    "overzichtVerouderd": "overzicht-verouderd", "activiteit": "activiteit-nu", "activiteitProblemen": "activiteit-problemen",
    "activiteitGebeurd": "activiteit-wat-is-er-gebeurd", "bibliotheek": "bibliotheek", "bibliotheekDetail": "bibliotheek-detail",
    "bibliotheekOntbrekend": "bibliotheek-ontbrekend", "ruimte": "ruimte", "aanvragen": "aanvragen-ontdekken",
    "aanvragenMijn": "aanvragen-mijn", "aanvraagVenster": "aanvraagvenster", "instellingen": "instellingen",
    "instellingenWaarschuwingen": "instellingen-waarschuwingen",
}
VARIANT = {"ScreenShots": "393", "360ScreenShots": "360", "VolledigScreenShots": "393-volledig"}
DPI_SCALE = 2 / 2.75  # Pixel 5: 440 dpi = 2,75 px per dp

def crop_tall(im: Image.Image) -> Image.Image:
    """Snijdt de langste reeks gelijke, egale rijen (de lege ruimte boven de ondernavigatie) weg, op 48 px na."""
    rgb = im.convert("RGB"); w, h = rgb.size; px = rgb.load()
    best = (0, 0); run_start = None; run_color = None
    for y in range(h + 1):
        ok = False
        if y < h:
            c = px[0, y]
            ok = all(px[x, y] == c for x in range(0, w, 3))
        if ok and run_start is not None and c == run_color:
            continue
        if run_start is not None and y - run_start > best[1] - best[0]:
            best = (run_start, y)
        run_start, run_color = (y, c) if ok else (None, None)
    a, b = best
    if b - a < 150:
        return im
    keep = a + 48
    top = rgb.crop((0, 0, w, keep)); bottom = rgb.crop((0, b, w, h))
    out = Image.new("RGB", (w, top.height + bottom.height)); out.paste(top, (0, 0)); out.paste(bottom, (0, top.height))
    return out

index = {}
for f in sorted(src.glob("nl.mediaguard.app.shots_*.png")):
    cls, method = f.stem.split("_", 1)[1].split("_", 1)
    theme = "donker" if cls.startswith("Dark") else "licht"
    variant = VARIANT[cls.removeprefix("Dark").removeprefix("Light")]
    name = f"{NAMES[method]}-{theme}-{variant}.png"
    im = Image.open(f)
    if variant == "393-volledig":
        im = crop_tall(im)
    im = im.convert("RGB")
    im = im.resize((round(im.width * DPI_SCALE), round(im.height * DPI_SCALE)), Image.Resampling.LANCZOS)
    q = im.quantize(colors=256, method=Image.Quantize.FASTOCTREE, dither=Image.Dither.NONE)
    q.save(dst / name, optimize=True)
    index[name] = {"test": f"{cls}.{method}", "breedte_dp": 360 if variant == "360" else 393, "px": list(q.size)}
(dst / "index.json").write_text(json.dumps(index, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
print(len(index), "afdrukken", sum((dst / n).stat().st_size for n in index) / 1e6, "MB")
