"""Privésleutel voor outbox-leesposities in de geheimenmap van de installatie."""

from __future__ import annotations

import os
import tempfile
from contextlib import suppress
from pathlib import Path

KEY_REF = "core/outbox.key"


def key_path(core_dir: Path) -> Path:
    appdata = core_dir.parent if core_dir.name == "core" else core_dir
    return Path(os.environ.get("MG_SECRETS_DIR") or appdata / "secrets") / KEY_REF


def ensure_key(path: Path, owner: tuple[int, int] | None = None) -> None:
    if path.exists():
        return
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    # Publiceer pas na de volledige schrijf. Ook gelijktijdige starters vervangen nooit een sleutel.
    with tempfile.NamedTemporaryFile(dir=path.parent) as part:
        part.write(os.urandom(32))
        part.flush()
        os.fsync(part.fileno())
        if owner is not None and os.geteuid() == 0:
            os.fchown(part.fileno(), *owner)
        with suppress(FileExistsError):
            os.link(part.name, path)


def read_key(core_dir: Path, reference: object) -> bytes | None:
    if reference != KEY_REF:
        return None
    try:
        with key_path(core_dir).open("rb") as handle:
            key = handle.read(33)
    except OSError:
        return None
    return key if len(key) == 32 else None
