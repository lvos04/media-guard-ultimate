"""Logboek van de AV1-engine.

Uvicorn richt alleen zijn eigen loggers in; zonder dit verdwenen alle info-regels van de
engine (alleen waarschuwingen kwamen in het containerlog). Met ``MG_LOG_DIR`` (de AIO zet
``/config/logs``) gaan ze naar stdout én naar ``encoder.log`` (hooguit 4 × 5 MB). Het niveau
volgt het bestand ``niveau`` in die map, dat MediaGuard-core schrijft uit de instelling
'Logniveau'; zonder dat bestand geldt ``LOG_LEVEL`` (standaard INFO).
"""

from __future__ import annotations

import contextlib
import logging
import logging.handlers
import os
import sys
from pathlib import Path

LEVELS = {"debug": logging.DEBUG, "info": logging.INFO, "warning": logging.WARNING,
          "error": logging.ERROR}
FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
MAX_BYTES = 5 * 1024 * 1024
BACKUP_COUNT = 3
LEVEL_FILE = "niveau"
_MARK = "_mg_logboek"


def log_dir() -> Path | None:
    raw = os.environ.get("MG_LOG_DIR", "").strip()
    return Path(raw) if raw else None


def inrichten(default_level: str = "INFO", folder: Path | None = None) -> Path | None:
    """Root-logger inrichten (idempotent). Geeft het logbestand terug, of None."""
    folder = folder or log_dir()
    if folder is None:
        return None
    root = logging.getLogger()
    eerder = getattr(root, _MARK, None)
    if isinstance(eerder, Path):
        return eerder
    formatter = logging.Formatter(FORMAT)
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    root.addHandler(console)
    path: Path | None = None
    try:
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / "encoder.log"
        handler = logging.handlers.RotatingFileHandler(
            path, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8")
        handler.setFormatter(formatter)
        root.addHandler(handler)
        # uvicorn houdt zijn eigen consolehandler (propagate uit); fouten en wijzigende
        # verzoeken ook in het bestand. Alleen op loggers die niet doorgeven, anders staat
        # elke regel er twee keer in ('uvicorn.error' geeft door aan 'uvicorn').
        for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
            lg = logging.getLogger(name)
            if not lg.propagate:
                lg.addHandler(handler)
    except OSError as exc:
        path = None
        root.warning("logbestand niet beschikbaar (%s); alleen het containerlog", exc)
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)
    setattr(root, _MARK, path or folder)
    zet_niveau(default_level)
    volg_niveau(folder)
    return path


def zet_niveau(level: str) -> bool:
    wanted = LEVELS.get(str(level).strip().lower(), logging.INFO)
    root = logging.getLogger()
    if root.level == wanted:
        return False
    root.setLevel(wanted)
    return True


def volg_niveau(folder: Path | None = None) -> str | None:
    """Lees het door core geschreven niveau (``niveau``) en pas het toe."""
    folder = folder or log_dir()
    if folder is None:
        return None
    with contextlib.suppress(OSError):
        level = (folder / LEVEL_FILE).read_text(encoding="utf-8").strip().lower()
        if level in LEVELS:
            if zet_niveau(level):
                logging.getLogger(__name__).info("logniveau is nu %s", level)
            return level
    return None


async def volg_loop(interval: float = 30.0) -> None:
    import asyncio

    while True:
        await asyncio.sleep(interval)
        volg_niveau()
