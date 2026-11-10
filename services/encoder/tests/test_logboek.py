"""Rapport 16: info-regels van de AV1-engine blijven bewaard en het niveau volgt core."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path

import pytest

from app import logboek


@pytest.fixture
def schone_root() -> Iterator[logging.Logger]:
    root = logging.getLogger()
    voor = (list(root.handlers), root.level)
    uv = {n: list(logging.getLogger(n).handlers) for n in ("uvicorn", "uvicorn.error",
                                                           "uvicorn.access")}
    yield root
    for handler in root.handlers:
        if handler not in voor[0]:
            handler.close()
    root.handlers[:] = voor[0]
    root.setLevel(voor[1])
    for naam, handlers in uv.items():
        logging.getLogger(naam).handlers[:] = handlers
    if hasattr(root, "_mg_logboek"):
        delattr(root, "_mg_logboek")


def test_zonder_logmap_verandert_er_niets(monkeypatch: pytest.MonkeyPatch,
                                          schone_root: logging.Logger) -> None:
    monkeypatch.delenv("MG_LOG_DIR", raising=False)
    handlers = list(schone_root.handlers)
    assert logboek.inrichten("INFO") is None
    assert schone_root.handlers == handlers


def test_info_regels_in_bestand_en_niveau_uit_core(tmp_path: Path,
                                                   schone_root: logging.Logger) -> None:
    pad = logboek.inrichten("INFO", tmp_path)
    assert pad == tmp_path / "encoder.log"
    logging.getLogger("app.planner").info("encode gestart")
    logging.getLogger("uvicorn.error").error("poort bezet")
    tekst = pad.read_text(encoding="utf-8")
    assert "encode gestart" in tekst and "poort bezet" in tekst
    # core schrijft het niveau uit de instelling 'Logniveau'
    (tmp_path / "niveau").write_text("warning\n", encoding="utf-8")
    assert logboek.volg_niveau(tmp_path) == "warning"
    assert schone_root.level == logging.WARNING
    logging.getLogger("app.planner").info("niet meer")
    assert "niet meer" not in pad.read_text(encoding="utf-8")
    # onzin in het bestand verandert niets
    (tmp_path / "niveau").write_text("luid", encoding="utf-8")
    assert logboek.volg_niveau(tmp_path) is None and schone_root.level == logging.WARNING
    # nog een keer inrichten voegt geen dubbele handlers toe
    aantal = len(schone_root.handlers)
    logboek.inrichten("INFO", tmp_path)
    assert len(schone_root.handlers) == aantal


def test_uvicorn_regels_een_keer_in_het_bestand(tmp_path: Path,
                                                schone_root: logging.Logger) -> None:
    """Zoals uvicorn zelf inricht: 'uvicorn' en 'uvicorn.access' geven niet door,
    'uvicorn.error' wel (aan 'uvicorn'). Elke regel hoort er één keer in te staan."""
    uv, access = logging.getLogger("uvicorn"), logging.getLogger("uvicorn.access")
    voor = (uv.propagate, access.propagate)
    uv.propagate = access.propagate = False
    try:
        pad = logboek.inrichten("INFO", tmp_path)
        assert pad is not None
        logging.getLogger("uvicorn.error").error("poort bezet")
        logging.getLogger("uvicorn").warning("afsluiten")
        tekst = pad.read_text(encoding="utf-8")
        assert tekst.count("poort bezet") == 1 and tekst.count("afsluiten") == 1
    finally:
        uv.propagate, access.propagate = voor
