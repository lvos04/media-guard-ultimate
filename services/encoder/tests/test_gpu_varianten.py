"""GPU-proefladder, foutuitleg, kaartkeuze (Arc vóór iGPU) en het encodecommando per variant."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.config import Settings
from app.media import commandos, gpuvarianten
from app.media.beslissen import STANDAARD, Besluit

VAINFO_NEP = "vainfo: Driver version: nep (test)\nvaInitialize failed with error code -1"

INFO: dict[str, Any] = {"height": 1080, "subs": [], "audio": [], "video_indices": [0],
                        "attached_pics": [], "container": "matroska"}


def _settings(tmp_path: Path, **overrides: Any) -> Settings:
    from mg_helpers import container_settings

    settings = container_settings(tmp_path, **overrides)
    settings.work_root.mkdir(parents=True, exist_ok=True)
    return settings


def _fake_attempts(monkeypatch: pytest.MonkeyPatch, works: set[tuple[str, str]],
                   output: str = "Error creating a MFX session: -9.") -> list[tuple[str, str]]:
    import app.systeem as systeem

    calls: list[tuple[str, str]] = []

    async def fake(arguments: list[str], env: dict[str, str], target: Path,
                   timeout: float) -> tuple[bool, int | None, str]:
        if arguments[0] == "vainfo":
            # Diagnose na een volledig mislukte ladder (app/systeem.py): geen testencode,
            # dus geen poging. Zit vainfo in het image, dan komt deze aanroep ook langs.
            return False, 1, VAINFO_NEP
        codec = arguments[arguments.index("-c:v") + 1]
        node = next(a.split("child_device=")[-1].split(":")[-1] for a in arguments
                    if "renderD" in a)
        variant = next(v["id"] for v in gpuvarianten.VARIANTEN
                       if gpuvarianten.hw_args(v, node) == arguments[5:5 + len(
                           gpuvarianten.hw_args(v, node))]
                       and v["codec"] == codec
                       and gpuvarianten.upload_filter(v, 16) in arguments
                       and (("-low_power" in arguments) == bool(v["low_power"]))
                       and (("-global_quality" in arguments) == (v["rate"] == "icq")))
        calls.append((Path(node).name, variant))
        if (Path(node).name, variant) in works:
            return True, 0, ""
        return False, 1, output

    monkeypatch.setattr(systeem, "_run_attempt", fake)
    monkeypatch.setattr(Settings, "validate_storage", lambda self: None)
    return calls


async def test_ladder_kiest_eerste_werkende_variant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.systeem import qsv_test

    calls = _fake_attempts(monkeypatch, {("renderD128", "qsv_va_p010")})
    result = await qsv_test(_settings(tmp_path), nodes=[Path("/dev/dri/renderD128")])
    assert result["geslaagd"] is True
    assert result["variant"]["id"] == "qsv_va_p010"
    assert result["variant"]["libva_driver"] == "iHD"
    assert [c[1] for c in calls] == ["qsv_va_lp_p010", "qsv_va_lp_nv12", "qsv_va_p010"]
    assert len(result["pogingen"]) == 3


async def test_alleen_vaapi_werkt_dan_vaapi(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.systeem import qsv_test

    _fake_attempts(monkeypatch, {("renderD128", "vaapi_p010")})
    result = await qsv_test(_settings(tmp_path), nodes=[Path("/dev/dri/renderD128")])
    assert result["geslaagd"] is True
    assert gpuvarianten.is_vaapi(result["variant"])


async def test_alles_faalt_geeft_reden_hint_en_pogingen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.systeem as systeem
    from app.systeem import qsv_test

    _fake_attempts(monkeypatch, set())
    # Deterministisch, met of zonder vainfo op de testmachine (het testimage heeft hem wel).
    real_which = systeem.shutil.which
    monkeypatch.setattr(systeem.shutil, "which", lambda name, *args, **kwargs: (
        "/usr/bin/vainfo" if name == "vainfo" else real_which(name, *args, **kwargs)))
    result = await qsv_test(_settings(tmp_path), nodes=[Path("/dev/dri/renderD129"),
                                                        Path("/dev/dri/renderD128")])
    assert result["geslaagd"] is False
    assert result["vainfo"] == VAINFO_NEP
    assert result["reden"] == "MFX-sessie kon niet starten (-9)"
    assert result["hint"]
    # per node alle varianten + één keer zonder LIBVA_DRIVER_NAME
    assert len(result["pogingen"]) == 2 * (len(gpuvarianten.VARIANTEN) + 1)
    assert {p["env"] for p in result["pogingen"]} == {"iHD", "auto"}


async def test_tweede_kaart_wint_als_eerste_faalt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.systeem import qsv_test

    _fake_attempts(monkeypatch, {("renderD129", "qsv_va_lp_p010")})
    result = await qsv_test(_settings(tmp_path), nodes=[Path("/dev/dri/renderD128"),
                                                        Path("/dev/dri/renderD129")])
    assert result["geslaagd"] is True
    assert result["render_node"] == "/dev/dri/renderD129"


@pytest.mark.parametrize(("output", "expected"), [
    ("Error creating a MFX session: -9.", "MFX-sessie kon niet starten (-9)"),
    ("[AVHWDeviceContext] vaInitialize failed with error", "driver init failed"),
    ("Cannot open /dev/dri/renderD128: Permission denied", "Permission denied"),
    ("Low power mode is unsupported", "HuC-firmware"),
    ("QSV-testencode duurde langer dan 30 s en is gestopt", "bleef hangen"),
    ("Unknown encoder 'av1_qsv'", "mist de AV1-GPU-encoder"),
    ("iets heel anders\nlaatste regel", "laatste regel"),
])
def test_foutuitleg(output: str, expected: str) -> None:
    reason, hint = gpuvarianten.explain(output)
    assert expected in reason
    assert hint


@pytest.mark.parametrize("variant", gpuvarianten.VARIANTEN, ids=lambda v: v["id"])
def test_encodecommando_volgt_variant(variant: dict[str, Any]) -> None:
    decision = Besluit(actie="hercoderen", baan="gpu", encoder="av1_qsv")
    args = commandos.encode_gpu(INFO, decision, STANDAARD, "/m/in.mkv", "/m/uit.mkv",
                                render_node="/dev/dri/renderD129", variant=variant)
    assert args[args.index("-c:v", args.index("-vf")) + 1] == variant["codec"]
    assert ("-low_power" in args) == bool(variant["low_power"])
    joined = " ".join(args)
    assert "renderD129" in joined
    if variant["codec"] == "av1_vaapi":
        assert "-preset" not in args
        assert "-global_quality" not in args  # vaapi: altijd een doelbitrate
        assert "-b:v" in args
    else:
        assert "-preset" in args


def test_zonder_variant_blijft_oude_opstartregel() -> None:
    decision = Besluit(actie="hercoderen", baan="gpu", encoder="av1_qsv")
    args = commandos.encode_gpu(INFO, decision, STANDAARD, "/m/in.mkv", "/m/uit.mkv")
    assert "qsv=hw,child_device=/dev/dri/renderD128" in args


@pytest.mark.parametrize("variant", gpuvarianten.VARIANTEN, ids=lambda v: v["id"])
def test_dolby_vision_nooit_via_gpu(variant: dict[str, Any]) -> None:
    decision = Besluit(actie="hercoderen", baan="gpu", encoder="av1_qsv", dolby_vision=True)
    with pytest.raises(ValueError, match="Dolby Vision"):
        commandos.encode_gpu(INFO, decision, STANDAARD, "/m/in.mkv", "/m/uit.mkv",
                             variant=variant)


async def test_arc_wordt_gekozen_boven_igpu(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Raptor Lake-iGPU (renderD128, geen AV1-encode) + Arc A310 (renderD129)."""

    import app.mediaguard.capabilities as capabilities

    dri = tmp_path / "dri"
    dri.mkdir()
    igpu, arc = dri / "renderD128", dri / "renderD129"
    igpu.write_bytes(b"")
    arc.write_bytes(b"")
    ids = {"renderD128": "0xa780", "renderD129": "0x56a6"}
    monkeypatch.delenv("MG_RENDER_NODE", raising=False)
    monkeypatch.setattr(capabilities, "_render_nodes", lambda: [igpu, arc])
    monkeypatch.setattr(capabilities, "render_vendor", lambda _n: "0x8086")
    monkeypatch.setattr(capabilities, "render_device_id", lambda n: ids[n.name])
    monkeypatch.setattr(capabilities.shutil, "which",
                        lambda name: None if name == "vainfo" else "/usr/bin/" + name)
    original_report = capabilities.dependency_report

    async def report_with_qsv(render_node: Path | None = None) -> dict[str, Any]:
        return {**(await original_report(render_node)), "av1_qsv": True}

    probed: list[list[str]] = []

    async def fake_qsv_test(_settings: Settings, nodes: list[Path] | None = None
                            ) -> dict[str, Any]:
        probed.append([n.name for n in nodes or []])
        return {"geslaagd": True, "render_node": str(arc),
                "variant": {**gpuvarianten.VARIANTEN[0], "libva_driver": None}}

    monkeypatch.setattr(capabilities, "dependency_report", report_with_qsv)
    monkeypatch.setattr(capabilities, "qsv_test", fake_qsv_test)
    monkeypatch.setattr(capabilities, "qsv_runtime_report", lambda: {"complete": True})
    settings = _settings(tmp_path, render_node=capabilities.DEFAULT_RENDER_NODE)
    service = capabilities.CapabilityService(settings)
    caps = await service.detect(refresh=True, run_qsv_probe=True)
    assert probed == [["renderD129"]]  # iGPU zonder AV1-encode wordt niet geprobeerd
    assert settings.render_node == arc
    assert settings.gpu_variant is not None
    assert settings.gpu_variant["id"] == "qsv_va_lp_p010"
    status = caps["gpu_status"]
    assert status["state"] == "actief"
    assert status["kaart"] == "Intel Arc A310 (renderD129)"
    assert status["niet_gebruikt"] == [
        "Intel UHD/Iris (geïntegreerd) (renderD128) — geen AV1-encode"]
    assert "Intel Arc A310 actief" in status["message"]


def test_rangorde_arc_eerst(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import app.mediaguard.capabilities as capabilities

    nodes = [Path("/dev/dri/renderD128"), Path("/dev/dri/renderD129")]
    ids = {"renderD128": "0xa780", "renderD129": "0x56a6"}
    monkeypatch.delenv("MG_RENDER_NODE", raising=False)
    monkeypatch.setattr(capabilities, "_render_nodes", lambda: nodes)
    monkeypatch.setattr(capabilities, "render_vendor", lambda _n: "0x8086")
    monkeypatch.setattr(capabilities, "render_device_id", lambda n: ids[n.name])
    settings = _settings(tmp_path, render_node=capabilities.DEFAULT_RENDER_NODE)
    assert capabilities.ranked_render_nodes(settings) == [nodes[1], nodes[0]]
    assert capabilities.pick_render_node(settings) == nodes[1]
    monkeypatch.setenv("MG_RENDER_NODE", "/dev/dri/renderD128")
    assert capabilities.ranked_render_nodes(settings) == [settings.render_node]
