"""Gedeelde applicatiecontext (DB, secrets, settings, events, jobs, HTTP-clients)."""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass, field
from typing import Any

from fastapi import Request

from mediaguard.config import Settings
from mediaguard.db import Database, dumps, loads, utcnow
from mediaguard.events import EventBus
from mediaguard.secrets_store import SecretStore
from mediaguard.settings_registry import REGISTRY, SettingsRegistry, SettingsService


@dataclass
class AppContext:
    settings: Settings
    db: Database
    secrets: SecretStore
    events: EventBus
    registry: SettingsRegistry = field(default_factory=lambda: REGISTRY)
    config: SettingsService = field(init=False)
    # vrije plek voor services van modules (bv. "arr_clients", "jobs")
    services: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.config = SettingsService(self.db, self.registry)

    # -- systeemstatus --------------------------------------------------------------
    def state_get(self, key: str, default: Any = None) -> Any:
        row = self.db.fetch_one("SELECT value_json FROM system_state WHERE key=?", (key,))
        return default if row is None else loads(row["value_json"])

    def state_set(self, key: str, value: Any) -> None:
        self.db.execute(
            "INSERT INTO system_state(key,value_json,updated_at) VALUES (?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, "
            "updated_at=excluded.updated_at",
            (key, dumps(value), utcnow()),
        )

    def emergency_stopped(self) -> bool:
        return bool(self.state_get("emergency_stop", {}).get("active", False))

    # -- hook keys (per instantie, voor fork pre-grab/pre-import hooks) --------------
    def hook_key_owner(self, presented: str) -> str | None:
        digest = hashlib.sha256(presented.encode()).hexdigest()
        rows = self.db.fetch_all(
            "SELECT key, value_json FROM system_state WHERE key LIKE 'hook_key:%'"
        )
        for row in rows:
            if hmac.compare_digest(loads(row["value_json"]), digest):
                return str(row["key"]).split(":", 1)[1]
        return None

    def set_hook_key(self, instance_id: str, key: str) -> None:
        self.state_set(f"hook_key:{instance_id}", hashlib.sha256(key.encode()).hexdigest())


def get_app_context(request: Request) -> AppContext:
    ctx: AppContext = request.app.state.ctx
    return ctx
