"""Registry van uitvoerbare, niet-destructieve AI-vervolgacties.

Destructieve acties zijn *by construction* onmogelijk:

- alleen namen uit ``EXECUTABLE_ACTIONS`` kunnen een callback krijgen;
- de callback krijgt een vaste :class:`ActionRequest` met het lokale doel van de casus —
  nooit vrije tekst of parameters uit het modelantwoord;
- er bestaat geen code voor verwijderen, vervangen, identity-verdicts, taal-/HDR/DV-beleid,
  snapshots, cutover of profielactivatie.

Modules registreren hun implementatie met :func:`register_action`, bv. B1:
``register_action(ctx, "request_search", wanted_search_cb, precheck=wanted_allows)``.
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any

from mediaguard.ai.constants import EXECUTABLE_ACTIONS

if TYPE_CHECKING:
    from mediaguard.context import AppContext


@dataclass(frozen=True)
class ActionRequest:
    case_id: str
    task_type: str
    action: str
    instance_id: str | None
    target_type: str | None
    target_id: str | None
    actor: str
    via: str  # "approve" | "bounded_auto"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


ActionCallback = Callable[["AppContext", ActionRequest], Awaitable[Any] | Any]
# precheck: zou deze actie ZONDER AI ook toegestaan zijn? (deterministische policy)
Precheck = Callable[["AppContext", ActionRequest], bool]


@dataclass(frozen=True)
class RegisteredAction:
    name: str
    callback: ActionCallback
    precheck: Precheck | None
    module: str


def _registry(ctx: AppContext) -> dict[str, RegisteredAction]:
    reg: dict[str, RegisteredAction] = ctx.services.setdefault("ai_actions", {})
    return reg


def register_action(ctx: AppContext, action: str, callback: ActionCallback, *,
                    precheck: Precheck | None = None, module: str = "") -> None:
    if action not in EXECUTABLE_ACTIONS:
        raise ValueError(
            f"Actie '{action}' kan niet aan de AI-adviseur worden gekoppeld: alleen "
            f"niet-destructieve acties ({', '.join(EXECUTABLE_ACTIONS)}) zijn toegestaan.")
    if not callable(callback):
        raise ValueError("callback moet aanroepbaar zijn")
    _registry(ctx)[action] = RegisteredAction(action, callback, precheck,
                                              module or getattr(callback, "__module__", ""))


def registered(ctx: AppContext, action: str) -> RegisteredAction | None:
    return _registry(ctx).get(action)


def available_actions(ctx: AppContext) -> list[str]:
    return sorted(_registry(ctx))


async def execute(ctx: AppContext, request: ActionRequest, timeout: float) -> dict[str, Any]:
    """Voert een geregistreerde actie uit. Gooit nooit; time-out/fout = niets gedaan."""
    entry = registered(ctx, request.action)
    if entry is None:
        return {"status": "unavailable",
                "detail": "Geen uitvoerder geregistreerd voor deze actie; handmatig opvolgen."}
    if entry.precheck is not None:
        try:
            allowed = bool(entry.precheck(ctx, request))
        except Exception as exc:  # noqa: BLE001
            return {"status": "blocked", "detail": f"precheck faalde: {type(exc).__name__}"}
        if not allowed:
            return {"status": "blocked",
                    "detail": "Deterministische policy staat deze actie nu niet toe."}
    try:
        result = entry.callback(ctx, request)
        if inspect.isawaitable(result):
            result = await asyncio.wait_for(result, timeout=timeout)
    except TimeoutError:
        return {"status": "timeout", "detail": f"actie niet voltooid binnen {timeout}s"}
    except Exception as exc:  # noqa: BLE001
        return {"status": "failed", "detail": f"{type(exc).__name__}"}
    if not isinstance(result, dict):
        result = {"result": result}
    return {"status": "executed", "detail": "uitgevoerd", "result": result}
