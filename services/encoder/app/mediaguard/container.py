"""Containermodus (TrueNAS SCALE): eenmalige, minimale provisioning bij start.

Vervangt voor de container wat op Ubuntu ``install/installeer-ubuntu-26.04.sh``
doet, maar uitsluitend binnen de eigen datasets: geen systemd, geen UFW, geen
SMB-sockethelper, geen LAN-gateway en nooit iets in de mediabibliotheek.
"""

from __future__ import annotations

import logging
import os
import secrets
from contextlib import suppress
from pathlib import Path

from app.config import Settings

LOGGER = logging.getLogger(__name__)


def prepare_container_storage(settings: Settings) -> None:
    for path in (
        settings.config_root,
        settings.backup_root,
        settings.cache_root,
        settings.work_root,
        settings.effective_staging_root,
    ):
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            # validate_storage meldt dit daarna als duidelijke, fail-closed fout.
            LOGGER.error("Containermap %s kon niet worden aangemaakt: %s", path, exc)
    ensure_secret_key(settings.secret_key_file)


def ensure_secret_key(path: Path) -> None:
    """Maak eenmalig een eigen versleutelsleutel (0600) als die ontbreekt."""

    if path.exists():
        return
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return
    except OSError as exc:
        LOGGER.error("Sleutelbestand %s kon niet worden gemaakt: %s", path, exc)
        return
    with os.fdopen(descriptor, "w", encoding="ascii") as handle:
        handle.write(secrets.token_hex(32) + "\n")
        handle.flush()
        with suppress(OSError):
            os.fsync(handle.fileno())
