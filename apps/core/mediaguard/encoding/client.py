"""Async client voor de encoder-bridge (packages/contracts/encoder-bridge.md).

Alle calls dragen ``X-MediaGuard-Token``; netwerkfouten worden ``EncoderUnavailable``
(→ UI toont 'encoder onbereikbaar', jobs plannen een retry), HTTP-fouten
``EncoderError`` met status. Tests injecteren een transport via
``ctx.services["encoder_transport"]`` of een complete client via
``ctx.services["encoder_client"]``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx

from mediaguard.secrets_store import redact

if TYPE_CHECKING:
    from mediaguard.context import AppContext

PREFIX = "/api/mediaguard/v1"


class EncoderError(Exception):
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(redact(message))
        self.status = status


class EncoderUnavailable(EncoderError):
    pass


# Velden die de encoder accepteert (services/encoder app/mediaguard/policy.py, extra=forbid).
POLICY_KEYS = {"encoder", "crf", "cq", "preset", "film_grain", "target", "dv_mode",
               "dv_allowed_profiles", "hdr10plus", "audio_policy", "subtitle_policy",
               "min_saving_percent", "quality_profile", "decided_by", "skip_thresholds",
               "dv81_to_hdr10"}
AUDIO_POLICY_KEYS = {"keep_languages", "keep_all", "keep_commentary"}
SUBTITLE_POLICY_KEYS = {"keep_all", "keep_languages"}


def wire_policy(policy: dict[str, Any]) -> dict[str, Any]:
    """Core bewaart extra beslisinformatie in de policy (profile_id, dv_route, audio-mode);
    de echte encoder weigert onbekende velden met 422 (gemeten in de stacktest). Stuur
    alleen het contract. ``hdr10plus``: skip | convert_verified | hdr10_fallback_explicit."""
    out = {k: v for k, v in policy.items() if k in POLICY_KEYS and v is not None}
    if isinstance(out.get("audio_policy"), dict):
        out["audio_policy"] = {k: v for k, v in out["audio_policy"].items()
                               if k in AUDIO_POLICY_KEYS}
    if isinstance(out.get("subtitle_policy"), dict):
        out["subtitle_policy"] = {k: v for k, v in out["subtitle_policy"].items()
                                  if k in SUBTITLE_POLICY_KEYS}
    if out.get("hdr10plus") not in (None, "skip", "convert_verified",
                                    "hdr10_fallback_explicit"):
        out["hdr10plus"] = "skip"
    return out


class EncoderClient:
    def __init__(self, base_url: str, token: str, *,
                 transport: httpx.AsyncBaseTransport | None = None,
                 timeout: float = 15.0) -> None:
        self.base_url = base_url.rstrip("/")
        self._token = token
        self._transport = transport
        self._timeout = timeout

    async def _request(self, method: str, path: str, json: Any = None,
                       timeout: float | None = None) -> Any:
        try:
            async with httpx.AsyncClient(base_url=self.base_url, transport=self._transport,
                                         timeout=timeout or self._timeout,
                                         headers={"X-MediaGuard-Token": self._token}) as client:
                resp = await client.request(method, PREFIX + path, json=json)
        except httpx.HTTPError as exc:
            raise EncoderUnavailable(f"encoder onbereikbaar: {type(exc).__name__}") from exc
        if resp.status_code >= 400:
            detail = resp.text[:500]
            raise EncoderError(f"encoder {method} {path} → {resp.status_code}: {detail}",
                               resp.status_code)
        if not resp.content:
            return {}
        return resp.json()

    async def health(self) -> dict[str, Any]:
        return await self._request("GET", "/health", timeout=5.0)

    async def engine(self, method: str, path: str, json: Any = None) -> Any:
        """Rechtstreeks naar de engine-API (``/api/...``), met het interne token; voor
        bediening vanuit de app (baan pauzeren/hervatten, job opnieuw)."""
        try:
            async with httpx.AsyncClient(base_url=self.base_url, transport=self._transport,
                                         timeout=self._timeout,
                                         headers={"X-MediaGuard-Token": self._token}) as client:
                resp = await client.request(method, "/api" + path, json=json)
        except httpx.HTTPError as exc:
            raise EncoderUnavailable(f"encoder onbereikbaar: {type(exc).__name__}") from exc
        if resp.status_code >= 400:
            raise EncoderError(f"encoder {method} {path} → {resp.status_code}: "
                               f"{resp.text[:300]}", resp.status_code)
        return resp.json() if resp.content else {}

    async def capabilities(self) -> dict[str, Any]:
        return await self._request("GET", "/capabilities")

    async def probe(self, path: str) -> dict[str, Any]:
        """Probe in het av1-formaat (codec, audio[], subs[], dv_profile, …).

        De echte encoder (services/encoder, gemeten in de stacktest) antwoordt met een
        omhulsel ``{path, probe: {...}, dv_route, dv_el_type, inventory, stat}``; zonder
        uitpakken zag core géén codec en géén audiosporen. Platte antwoorden (oudere
        encoder/tests) blijven werken; de extra velden komen ernaast."""
        data = await self._request("POST", "/probe", {"path": path}, timeout=300.0)
        inner = data.get("probe") if isinstance(data, dict) else None
        if isinstance(inner, dict):
            extra = {k: v for k, v in data.items() if k != "probe" and k not in inner}
            data = {**inner, **extra}
        # Een probe zonder videogegevens is geen meting (rapport 4, S18): nooit stil als
        # 'leeg bestand' doorgeven, anders lijkt een AV1-versie SDR zonder geluid.
        if not isinstance(data, dict) or not data.get("codec"):
            raise EncoderError(f"probe van {path} gaf geen videogegevens terug", 502)
        return data

    async def plan(self, path: str, policy: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", "/plan", {"path": path,
                                                     "policy": wire_policy(policy)},
                                   timeout=300.0)

    async def create_job(self, body: dict[str, Any]) -> dict[str, Any]:
        if isinstance(body.get("policy"), dict):
            body = {**body, "policy": wire_policy(body["policy"])}
        # de encoder probet de bron volledig (DV7: dovi_tool) vóór hij antwoordt
        return await self._request("POST", "/jobs", body, timeout=300.0)

    async def get_job(self, job_id: int | str) -> dict[str, Any]:
        return await self._request("GET", f"/jobs/{job_id}")

    async def cancel_job(self, job_id: int | str) -> dict[str, Any]:
        return await self._request("POST", f"/jobs/{job_id}/cancel")

    async def awaiting_commit(self) -> list[int]:
        """Engine-jobs die nog op commit/reject wachten."""
        data = await self._request("GET", "/jobs-awaiting-commit", timeout=5.0)
        return [int(x) for x in (data or {}).get("job_ids", [])]

    async def committed(self, job_id: int | str) -> dict[str, Any]:
        return await self._request("POST", f"/jobs/{job_id}/committed")

    async def rejected(self, job_id: int | str, reason: str = "") -> dict[str, Any]:
        return await self._request("POST", f"/jobs/{job_id}/rejected",
                                   {"reason": (reason or "")[:1000]})

    async def storage_settings(self) -> dict[str, Any]:
        return await self._request("GET", "/settings/storage", timeout=5.0)

    async def set_storage_settings(self, cache_min_free_gb: int) -> dict[str, Any]:
        return await self._request("PUT", "/settings/storage",
                                   {"cache_min_free_gb": int(cache_min_free_gb)})

    async def get_resources(self) -> dict[str, Any]:
        return await self._request("GET", "/settings/resources", timeout=5.0)

    async def set_resources(self, body: dict[str, Any]) -> dict[str, Any]:
        return await self._request("PUT", "/settings/resources", body)

    async def set_emergency_stop(self, active: bool, reason: str = "") -> dict[str, Any]:
        """Noodstop doorzetten: engine pakt niets nieuws op en bevriest lopende encodes."""
        return await self._request("PUT", "/settings/emergency-stop",
                                   {"active": bool(active), "reason": reason[:500]},
                                   timeout=5.0)

    async def set_playback_hold(self, active: bool, lanes: list[str],
                                reason: str) -> dict[str, Any]:
        """Nieuwe encodes laten wachten zolang er in de mediaserver gekeken wordt."""
        return await self._request("PUT", "/settings/playback-hold",
                                   {"active": active, "lanes": lanes, "reason": reason},
                                   timeout=5.0)

    async def trials(self, body: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", "/trials", body, timeout=3600.0)


def get_encoder_client(ctx: AppContext) -> EncoderClient:
    override = ctx.services.get("encoder_client")
    if override is not None:
        return override  # type: ignore[no-any-return]
    return EncoderClient(ctx.settings.encoder_url, ctx.settings.internal_token,
                         transport=ctx.services.get("encoder_transport"))


# -- bereikbaarheid: pas 'offline' na meerdere mislukte metingen achter elkaar ---------------
OFFLINE_AFTER_FAILURES = 3


def note_encoder_reachability(ctx: Any, ok: bool) -> bool:
    """Registreer één meting; geeft True als de engine als offline moet gelden.

    Eén trage of mislukte meting (engine druk, net herstart) is geen storing: pas na
    ``OFFLINE_AFTER_FAILURES`` missers op rij meldt MediaGuard 'AV1-engine reageert niet'."""
    streak = 0 if ok else int(ctx.services.get("encoder_fail_streak", 0)) + 1
    ctx.services["encoder_fail_streak"] = streak
    return streak >= OFFLINE_AFTER_FAILURES
