"""Paparazzi-afdrukken voor de documentatie (PEIL_AFDRUKKEN=1, klassen Docs*) -> docs/screenshots/peil/android/.

Gebruik (vanuit de repo-root, na `:app:recordPaparazziDebug` met PEIL_AFDRUKKEN=1):
    python3 -I docs/screenshots/peil/android/verwerk.py apps/android/app/src/test/snapshots/images docs/screenshots/peil/android

- naam: <scherm>-<licht|donker>-<360|393|393-volledig|393-grote-letter>.png (zelfde namen als ../voor/android/)
- de afdrukken zijn al 2 px per dp (xhdpi); niets wordt geschaald
- 'volledig' (viewport 2618 dp hoog): lege ruimte tussen de laatste inhoud en de ondernavigatie weggesneden
- 256-kleurenpalet zonder dithering (± 3x kleiner). Meet NOOIT contrast in deze PNG's: contrast komt uit
  PeilContrastTest (apps/android) en uit contrast.json van Peil.
- de Docs*-bronbestanden worden daarna uit de snapshotmap verwijderd (ze horen niet bij de referentieset)
"""
import json
import pathlib
import sys

from PIL import Image

src, dst = map(pathlib.Path, sys.argv[1:3])
dst.mkdir(parents=True, exist_ok=True)
NAMES = {
    "overzicht": "overzicht", "overzichtRustig": "overzicht-alles-in-orde", "overzichtNoodstop": "overzicht-noodstop",
    "overzichtVerouderd": "overzicht-verouderd", "activiteit": "activiteit-nu", "activiteitProblemen": "activiteit-problemen",
    "activiteitGebeurd": "activiteit-wat-is-er-gebeurd", "bibliotheek": "bibliotheek", "bibliotheekDetail": "bibliotheek-detail",
    "bibliotheekOntbrekend": "bibliotheek-ontbrekend", "ruimte": "ruimte", "aanvragen": "aanvragen-ontdekken",
    "aanvragenMijn": "aanvragen-mijn", "aanvraagVenster": "aanvraagvenster", "instellingen": "instellingen",
    "instellingenWaarschuwingen": "instellingen-waarschuwingen", "koppelen": "koppelen",
    "koppelenGeweigerd": "koppelen-geweigerd",
}
VARIANT = {"393": "393", "360": "360", "Volledig": "393-volledig", "GroteLetter": "393-grote-letter"}


def crop_tall(im: Image.Image) -> Image.Image:
    """Snijdt de langste reeks gelijke, egale rijen (de lege ruimte boven de ondernavigatie) weg, op 48 px na."""
    rgb = im.convert("RGB")
    w, h = rgb.size
    px = rgb.load()
    best = (0, 0)
    run_start = None
    run_color = None
    for y in range(h + 1):
        ok = False
        c = None
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
    top = rgb.crop((0, 0, w, keep))
    bottom = rgb.crop((0, b, w, h))
    out = Image.new("RGB", (w, top.height + bottom.height))
    out.paste(top, (0, 0))
    out.paste(bottom, (0, top.height))
    return out


index = {}
bronnen = sorted(src.glob("nl.mediaguard.app.shots_Docs*.png"))
for f in bronnen:
    cls, method = f.stem.split("_", 1)[1].split("_", 1)
    rest = cls.removeprefix("Docs")
    theme = "donker" if rest.startswith("Dark") else "licht"
    variant = VARIANT[rest.removeprefix("Dark").removeprefix("Light")]
    name = f"{NAMES[method]}-{theme}-{variant}.png"
    im = Image.open(f)
    if variant == "393-volledig":
        im = crop_tall(im)
    q = im.convert("RGB").quantize(colors=256, method=Image.Quantize.FASTOCTREE, dither=Image.Dither.NONE)
    q.save(dst / name, optimize=True)
    index[name] = {"test": f"{cls}.{method}", "breedte_dp": 360 if variant == "360" else 393,
                   "letterschaal": 1.3 if variant == "393-grote-letter" else 1.0, "px": list(q.size)}
(dst / "index.json").write_text(json.dumps(dict(sorted(index.items())), indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
for f in bronnen:
    f.unlink()
print(len(index), "afdrukken", round(sum((dst / n).stat().st_size for n in index) / 1e6, 1), "MB")
