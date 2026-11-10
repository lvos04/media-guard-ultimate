"""Grootte telt alleen nog bij DV5; verder geen bovengrens (AV1 bepaalt de eindgrootte)."""

from mediaguard.provisioning import store
from mediaguard.provisioning import templates as T


def test_no_max_size_for_any_quality():
    assert all(hi is None for _lo, _pref, hi in T.QUALITY_SIZES.values())
    assert all(pref > 0 for _lo, pref, _hi in T.QUALITY_SIZES.values())


def test_dv5_formats_and_scores():
    dv5 = T.CUSTOM_FORMATS["DV5 (zonder HDR-terugval)"]
    assert any(s["negate"] and s["required"] for s in dv5)  # DV zonder HDR-tag
    big = T.CUSTOM_FORMATS["DV5 groot (film)"]
    assert any(s["implementation"] == "SizeSpecification" for s in big)
    for specs in T.CUSTOM_FORMATS.values():  # Sonarr/Radarr: max moet groter zijn dan min
        for spec in specs:
            if spec["implementation"] == "SizeSpecification":
                f = {x["name"]: x["value"] for x in spec["fields"]}
                assert f["max"] > f["min"] >= 0
    films = T.QUALITY_TEMPLATES["films"]["scores"]
    assert films["Dolby Vision"] + films["DV5 (zonder HDR-terugval)"] >= 0  # nog toegestaan
    assert films["DV5 groot (film)"] <= -10000
    assert T.QUALITY_TEMPLATES["series"]["scores"]["DV5 groot (aflevering)"] <= -10000


def test_background_keeps_sizes_and_formats_up_to_date():
    assert {"qualitydefinitions", "customformats", "qualityprofiles"} <= set(store.AUTO_SECTIONS)
    assert "qualitydefinitions" not in store.ESSENTIAL_SECTIONS  # hold blijft alleen essentials
