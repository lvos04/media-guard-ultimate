"""Runtime-configuratie van mediaguard-core.

Alleen hostinrichting (paden, poorten, interne tokens) komt uit omgevingsvariabelen.
Alle operationele instellingen staan in de database en zijn via de UI te beheren
(zie ``mediaguard.settings_registry``).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env_path(name: str, default: str) -> Path:
    return Path(os.environ.get(name, default))


def _env_list(name: str) -> list[str]:
    raw = os.environ.get(name, "")
    return [part.strip() for part in raw.split(",") if part.strip()]


@dataclass(frozen=True)
class Settings:
    # rol bepaalt welke achtergrondtaken dit proces draait: core | recovery
    role: str = field(default_factory=lambda: os.environ.get("MG_ROLE", "core"))
    data_dir: Path = field(default_factory=lambda: _env_path("MG_DATA_DIR", "/config"))
    staging_dir: Path = field(
        default_factory=lambda: _env_path("MG_STAGING_DIR", "/work/staging")
    )
    quarantine_dir: Path = field(
        default_factory=lambda: _env_path("MG_QUARANTINE_DIR", "/work/quarantine")
    )
    library_roots: list[str] = field(default_factory=lambda: _env_list("MG_LIBRARY_ROOTS"))
    encoder_url: str = field(
        default_factory=lambda: os.environ.get("MG_ENCODER_URL", "http://mediaguard-encode:8000")
    )
    recovery_url: str = field(
        default_factory=lambda: os.environ.get(
            "MG_RECOVERY_URL", "http://mediaguard-recovery:8100"
        )
    )
    # gedeeld geheim tussen interne containers (core <-> encoder <-> recovery)
    internal_token: str = field(default_factory=lambda: os.environ.get("MG_INTERNAL_TOKEN", ""))
    # publieke basis-URL waaronder forks de hooks van core bereiken
    public_core_url: str = field(
        default_factory=lambda: os.environ.get("MG_CORE_URL", "http://mediaguard-core:8100")
    )
    # alleen voor tests/dev: sla login over (nooit in productie-compose)
    dev_auth_bypass: bool = field(
        default_factory=lambda: os.environ.get("MG_DEV_AUTH_BYPASS", "0") == "1"
    )
    start_background: bool = field(
        default_factory=lambda: os.environ.get("MG_START_BACKGROUND", "1") == "1"
    )

    @property
    def db_path(self) -> Path:
        return self.data_dir / "mediaguard.db"

    @property
    def secret_key_path(self) -> Path:
        return self.data_dir / "secret.key"

    @property
    def backup_dir(self) -> Path:
        return self.data_dir / "backups"


def load_settings(**overrides: object) -> Settings:
    base = Settings()
    if not overrides:
        return base
    values = {name: getattr(base, name) for name in base.__dataclass_fields__}
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]
