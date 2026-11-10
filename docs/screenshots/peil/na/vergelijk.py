"""Peil-meting MediaGuard — "voor" en "na" naast elkaar (alleen-lezen hulpscript, hoort niet bij de app).

Leest docs/screenshots/peil/voor/metingen.json en na/metingen.json (zelfde meting, zelfde schermen) en schrijft een
Markdown-tabel naar stdout. Gebruik: python3 docs/screenshots/peil/na/vergelijk.py
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent


def load(name: str) -> dict:
    return json.loads((HERE.parent / name / "metingen.json").read_text(encoding="utf-8"))


def summary(m: dict) -> dict:
    out: dict = {}
    ok = {k: v for k, v in m.items() if "fout" not in v}
    out["afdrukken"] = len(ok)
    out["mislukt"] = len(m) - len(ok)
    out["overloop"] = sum(1 for v in ok.values() if (v.get("overloop_apparaat_px") or 0) > 1)
    for w in (360, 1440):
        s = [v for k, v in ok.items() if k.startswith("schermen/") and v.get("breedte") == w]
        out[f"doelen_{w}"] = (sum(v.get("doelen_kleiner_dan_44", 0) for v in s), sum(v.get("doelen", 0) for v in s))
        out[f"tekst_{w}"] = sum(v.get("tekst_kleiner_dan_14", 0) for v in s)
        sizes = [v.get("kleinste_tekst_px") for v in s if v.get("kleinste_tekst_px")]
        out[f"kleinste_{w}"] = min(sizes) if sizes else None
    axe: Counter = Counter()
    for v in ok.values():
        for a in v.get("axe", []):
            if a.get("impact") in ("serious", "critical"):
                axe[a["id"]] += 1
    out["axe"] = dict(axe)
    out["js_fouten"] = sum(len(v.get("js_fouten", [])) for v in ok.values())
    fonts: Counter = Counter()
    for v in ok.values():
        fonts.update(v.get("lettertypes", []))
    out["lettertypes"] = dict(fonts)
    for key in ("pauze_icoon", "dot_success", "cirkel_check", "kpi_success", "kpi_warning", "kpi_danger"):
        out[key] = sum(v.get("tonen", {}).get(key, 0) for v in ok.values() if v.get("breedte") == 1440)
    return out


def px(count: int, smallest: float | None) -> str:
    return f"{count} (kleinste {smallest} px)" if smallest else f"{count}"


def main() -> None:
    a, b = summary(load("voor")), summary(load("na"))
    rows = [
        ("Afdrukken (gelukt / mislukt)", f"{a['afdrukken']} / {a['mislukt']}", f"{b['afdrukken']} / {b['mislukt']}"),
        ("Afdrukken met horizontale overloop (t.o.v. apparaatbreedte)", a["overloop"], b["overloop"]),
        ("Schermen 1440: doelen < 44 px / alle doelen", "{} / {}".format(*a["doelen_1440"]), "{} / {}".format(*b["doelen_1440"])),
        ("Schermen 360: doelen < 44 px / alle doelen", "{} / {}".format(*a["doelen_360"]), "{} / {}".format(*b["doelen_360"])),
        ("Schermen 1440: tekstelementen < 14 px (kleinste)", px(a['tekst_1440'], a['kleinste_1440']), px(b['tekst_1440'], b['kleinste_1440'])),
        ("Schermen 360: tekstelementen < 14 px (kleinste)", px(a['tekst_360'], a['kleinste_360']), px(b['tekst_360'], b['kleinste_360'])),
        ("axe serious/critical (afdrukken × regel)", json.dumps(a["axe"], ensure_ascii=False), json.dumps(b["axe"], ensure_ascii=False)),
        ("JS-fouten", a["js_fouten"], b["js_fouten"]),
        ("Lettertypes", json.dumps(a["lettertypes"], ensure_ascii=False), json.dumps(b["lettertypes"], ensure_ascii=False)),
        ("1440: pauze-iconen", a["pauze_icoon"], b["pauze_icoon"]),
        ("1440: groene stippen (dot--success)", a["dot_success"], b["dot_success"]),
        ("1440: circle-check-iconen (alleen nog Gezond)", a["cirkel_check"], b["cirkel_check"]),
        ("1440: KPI-waarden in statuskleur-klasse (success/warning/danger)", a["kpi_success"] + a["kpi_warning"] + a["kpi_danger"], b["kpi_success"] + b["kpi_warning"] + b["kpi_danger"]),
    ]
    print("| Meting | Voor | Na |\n|---|---|---|")
    for r in rows:
        print(f"| {r[0]} | {r[1]} | {r[2]} |")


if __name__ == "__main__":
    main()
