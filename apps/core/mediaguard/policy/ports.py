"""Dunne, defensieve toegang tot interfaces van andere werkstromen (B1).

- ``mediaguard.instances.registry`` (contract ``packages/contracts/instances.md``) wordt
  pas bij gebruik geïmporteerd: zo blijft deze module bruikbaar als B1's code (nog)
  ontbreekt, en kunnen tests een nep-registry injecteren via
  ``ctx.services["instance_registry"]``.
- Ontbreekt de registry, dan geldt: instantie onbekend → niet geschorst, geen shadow.
"""

from __future__ import annotations

import importlib
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from mediaguard.context import AppContext

LOGGER = logging.getLogger(__name__)


def _registry(ctx: AppContext) -> Any | None:
    override = ctx.services.get("instance_registry")
    if override is not None:
        return override
    try:
        return importlib.import_module("mediaguard.instances.registry")
    except ModuleNotFoundError:
        return None


def get_instance(ctx: AppContext, instance_id: str) -> Any | None:
    reg = _registry(ctx)
    if reg is None:
        return None
    try:
        return reg.get_instance(ctx, instance_id)
    except Exception:  # noqa: BLE001 - registry-fout mag hooks nooit laten crashen
        LOGGER.exception("instance-registry faalde voor %s", instance_id)
        return None


def instance_flags(ctx: AppContext, instance_id: str) -> dict[str, Any]:
    inst = get_instance(ctx, instance_id)
    if inst is None:
        return {"known": False, "suspended": False, "shadow_mode": False, "archived": False,
                "media_type": None, "app": None}
    return {
        "known": True,
        "suspended": bool(getattr(inst, "suspended", False)),
        "shadow_mode": bool(getattr(inst, "shadow_mode", False)),
        "archived": bool(getattr(inst, "archived", False)),
        "media_type": getattr(inst, "media_type", None),
        "app": getattr(inst, "app", None),
    }


def arr_client(ctx: AppContext, instance_id: str) -> Any | None:
    """Async ArrClient of None als de registry niet beschikbaar is."""
    reg = _registry(ctx)
    if reg is None:
        return None
    return reg.arr_client(ctx, instance_id)
