"""Logboek van core en recovery.

Zonder inrichting gooide Python alle info-regels weg (alleen waarschuwingen kwamen in het
containerlog). Nu gaan ze naar stdout (TrueNAS-containerlog) én naar een roterend bestand
onder ``/config/logs`` (``core.log``, ``recovery.log``; hooguit 4 × 5 MB per proces), zodat
de supportbundel ze kan meenemen.

Het niveau volgt de instelling 'Logniveau' (``logging.level``), ook zonder herstart. Core
schrijft het gekozen niveau ook naar ``/config/logs/niveau``; de AV1-engine volgt dat bestand.
"""

from __future__ import annotations

import contextlib
import logging
import logging.handlers
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from mediaguard.config import Settings
    from mediaguard.context import AppContext

LEVELS = {"debug": logging.DEBUG, "info": logging.INFO, "warning": logging.WARNING,
          "error": logging.ERROR}
FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
MAX_BYTES = 5 * 1024 * 1024
BACKUP_COUNT = 3
LEVEL_FILE = "niveau"
# bibliotheken die bij 'info' per HTTP-verzoek een regel schrijven
NOISY = ("httpx", "httpcore", "multipart", "watchfiles")
_MARK = "_mg_logs"


def log_dir(settings: Settings | None = None) -> Path:
    env = os.environ.get("MG_LOG_DIR")
    if env:
        return Path(env)
    if settings is not None:
        return settings.data_dir / "logs"
    return Path(os.environ.get("MG_DATA_DIR", "/config")) / "logs"


def configure(role: str, folder: Path, level: str = "info") -> Path | None:
    """Root-logger inrichten (idempotent). Geeft het logbestand terug, of None als de map
    niet schrijfbaar is (dan alleen stdout: nooit een reden om niet te starten)."""
    root = logging.getLogger()
    formatter = logging.Formatter(FORMAT)
    path: Path | None = None
    if not getattr(root, _MARK, False):
        console = logging.StreamHandler(sys.stdout)
        console.setFormatter(formatter)
        root.addHandler(console)
        try:
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / f"{role or 'core'}.log"
            handler = logging.handlers.RotatingFileHandler(
                path, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8")
            handler.setFormatter(formatter)
            root.addHandler(handler)
        except OSError as exc:
            path = None
            root.warning("logbestand niet beschikbaar (%s); alleen het containerlog", exc)
        setattr(root, _MARK, True)
    # uvicorn-loggers via de root (één opmaak, ook in het bestand)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers.clear()
        lg.propagate = True
    for name in NOISY:
        logging.getLogger(name).setLevel(logging.WARNING)
    apply_level(level)
    return path


def apply_level(level: str) -> bool:
    """Zet het niveau van de root-logger; True als het veranderde."""
    wanted = LEVELS.get(str(level).lower(), logging.INFO)
    root = logging.getLogger()
    if root.level == wanted:
        return False
    root.setLevel(wanted)
    logging.getLogger(__name__).info("logniveau is nu %s", logging.getLevelName(wanted).lower())
    return True


def sync(ctx: AppContext) -> str:
    """Instelling → dit proces (en voor core: het niveaubestand voor de AV1-engine)."""
    level = str(ctx.config.value("logging.level") or "info").lower()
    apply_level(level)
    if ctx.settings.role in ("core", "all"):
        target = log_dir(ctx.settings) / LEVEL_FILE
        with contextlib.suppress(OSError):
            if not target.is_file() or target.read_text(encoding="utf-8").strip() != level:
                target.parent.mkdir(parents=True, exist_ok=True)
                tmp = target.with_name(target.name + ".tmp")
                tmp.write_text(level + "\n", encoding="utf-8")
                os.replace(tmp, target)
    return level


async def watch(ctx: AppContext, interval: float = 30.0) -> None:
    """Achtergrondtaak (beide rollen): wijzigingen van 'Logniveau' binnen ±30 s toepassen."""
    import asyncio

    while True:
        try:
            sync(ctx)
        except Exception:  # noqa: BLE001 - het logboek mag niets laten stoppen
            logging.getLogger(__name__).debug("logniveau bijwerken mislukt", exc_info=True)
        await asyncio.sleep(interval)


def files(folder: Path) -> list[Path]:
    """Alle logbestanden (ook de geroteerde .1–.3), nieuwste eerst per dienst."""
    if not folder.is_dir():
        return []
    return sorted((p for p in folder.iterdir()
                   if p.is_file() and (p.suffix == ".log" or ".log." in p.name)),
                  key=lambda p: p.name)


def tail(path: Path, max_bytes: int = 2 * 1024 * 1024) -> str:
    """De laatste ``max_bytes`` van een logbestand (heel bestand als het kleiner is)."""
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            if size > max_bytes:
                handle.seek(size - max_bytes)
                handle.readline()  # half begonnen regel overslaan
            data = handle.read()
    except OSError as exc:
        return f"(niet leesbaar: {exc})"
    return data.decode("utf-8", "replace")


def bundle_entries(settings: Settings, *, budget: int = 12 * 1024 * 1024) -> list[tuple[str, str]]:
    """(naam, inhoud) voor de supportbundel: de nieuwste logs, samen hooguit ``budget``."""
    out: list[tuple[str, str]] = []
    used = 0
    for path in files(log_dir(settings)):
        rotated = ".log." in path.name
        take = min(2 * 1024 * 1024 if not rotated else 512 * 1024, max(0, budget - used))
        if take <= 0:
            break
        text = tail(path, take)
        used += len(text.encode("utf-8", "replace"))
        out.append((f"logs/{path.name}", text))
    return out


def summary(settings: Settings) -> dict[str, Any]:
    folder = log_dir(settings)
    return {"dir": str(folder), "files": [
        {"name": p.name, "size": p.stat().st_size} for p in files(folder)]}
