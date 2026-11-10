"""Gedeelde helpers voor instance-routes: foutvertaling, instance-validatie, maskering."""

from __future__ import annotations

import re
from collections.abc import Awaitable
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException

from mediaguard.arr.client import ArrError
from mediaguard.instances.registry import instance_row

if TYPE_CHECKING:
    from mediaguard.context import AppContext

MASK = "********"
_SECRET_NAME = re.compile(r"(?i)(api.?key|password|passkey|secret|token|cookie|pass$|rsskey)")
_SECRET_PRIVACY = {"apikey", "password"}


async def arr_call[T](awaitable: Awaitable[T]) -> T:
    try:
        return await awaitable
    except ArrError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.to_dict()) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def require_instance(ctx: AppContext, instance_id: str, *, app: str | None = None,
                     mutable: bool = False) -> dict[str, Any]:
    row = instance_row(ctx, instance_id)
    if row is None:
        raise HTTPException(404, f"Instantie '{instance_id}' bestaat niet")
    if app and row["app"] != app:
        raise HTTPException(400, f"Deze actie kan alleen op een {app}-instantie "
                                 f"('{instance_id}' is {row['app']})")
    if mutable and row["archived"]:
        raise HTTPException(409, f"Instantie '{instance_id}' is gearchiveerd (alleen-lezen)")
    return row


def _is_secret_field(field: dict[str, Any]) -> bool:
    privacy = str(field.get("privacy", "")).lower()
    if privacy in _SECRET_PRIVACY:
        return True
    if str(field.get("type", "")).lower() == "password":
        return True
    return bool(_SECRET_NAME.search(str(field.get("name", ""))))


def mask_provider(resource: dict[str, Any]) -> dict[str, Any]:
    """Verberg API-sleutels/wachtwoorden van indexers/downloadclients/notificaties."""
    out = dict(resource)
    fields = []
    for field in resource.get("fields") or []:
        f = dict(field)
        if _is_secret_field(f) and f.get("value") not in (None, ""):
            f["value"] = MASK
            f["is_set"] = True
        fields.append(f)
    if "fields" in resource:
        out["fields"] = fields
    return out


def unmask_provider(incoming: dict[str, Any], current: dict[str, Any] | None) -> dict[str, Any]:
    """Vervang gemaskeerde waarden door de huidige waarde uit Sonarr/Radarr."""
    out = dict(incoming)
    current_fields = {str(f.get("name")): f.get("value") for f in (current or {}).get(
        "fields") or []}
    fields = []
    for field in incoming.get("fields") or []:
        f = {k: v for k, v in field.items() if k != "is_set"}
        if f.get("value") == MASK:
            f["value"] = current_fields.get(str(f.get("name")))
        fields.append(f)
    if "fields" in incoming:
        out["fields"] = fields
    return out
