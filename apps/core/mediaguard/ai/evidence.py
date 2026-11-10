"""Lokale evidence-registry.

Andere modules (B1 wanted/backfill, B2 identity/encoding) slaan bewijs in hun eigen tabellen
op en melden het ID + korte samenvatting aan met :func:`register_evidence`. Daarnaast kan
een module een *provider* registreren die bestaan van ID's controleert in eigen tabellen.
Het model mag alleen evidence-ID's noemen die hier lokaal bestaan én bij de casus horen.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING, Any

from mediaguard.ai.privacy import MinimizationReport, clean_text
from mediaguard.db import utcnow

if TYPE_CHECKING:
    from mediaguard.context import AppContext

# provider(ctx, ids) -> {id: {"kind": ..., "summary": ...}} voor de ID's die bestaan
EvidenceProvider = Callable[["AppContext", list[str]], dict[str, dict[str, Any]]]


def _providers(ctx: AppContext) -> list[EvidenceProvider]:
    providers: list[EvidenceProvider] = ctx.services.setdefault("ai_evidence_providers", [])
    return providers


def register_evidence(ctx: AppContext, evidence_id: str, kind: str, summary: str, *,
                      instance_id: str | None = None, source_module: str | None = None) -> None:
    """Meldt een lokaal evidence-object aan (idempotent; samenvatting wordt
    geminimaliseerd opgeslagen: geen paden/sleutels)."""
    if not evidence_id or len(evidence_id) > 200:
        raise ValueError("evidence_id ongeldig")
    report = MinimizationReport()
    ctx.db.execute(
        "INSERT INTO ai_evidence_index(evidence_id,kind,summary,instance_id,source_module,"
        "created_at) VALUES (?,?,?,?,?,?) ON CONFLICT(evidence_id) DO UPDATE SET "
        "kind=excluded.kind, summary=excluded.summary, instance_id=excluded.instance_id, "
        "source_module=excluded.source_module",
        (evidence_id, clean_text(kind, report, limit=60), clean_text(summary, report, limit=400),
         instance_id, source_module, utcnow()),
    )


def register_evidence_provider(ctx: AppContext, provider: EvidenceProvider) -> None:
    if provider not in _providers(ctx):
        _providers(ctx).append(provider)


def lookup(ctx: AppContext, ids: Iterable[str]) -> dict[str, dict[str, Any]]:
    wanted = [i for i in dict.fromkeys(ids) if isinstance(i, str) and i]
    found: dict[str, dict[str, Any]] = {}
    if not wanted:
        return found
    for chunk_start in range(0, len(wanted), 200):
        chunk = wanted[chunk_start:chunk_start + 200]
        marks = ",".join("?" * len(chunk))
        for row in ctx.db.fetch_all(
                f"SELECT evidence_id, kind, summary FROM ai_evidence_index "
                f"WHERE evidence_id IN ({marks})", chunk):
            found[row["evidence_id"]] = {"kind": row["kind"], "summary": row["summary"]}
    missing = [i for i in wanted if i not in found]
    for provider in list(_providers(ctx)):
        if not missing:
            break
        try:
            extra = provider(ctx, list(missing)) or {}
        except Exception:  # noqa: BLE001 - een kapotte provider mag niets blokkeren
            continue
        report = MinimizationReport()
        for eid, info in extra.items():
            if eid in missing and isinstance(info, dict):
                found[eid] = {"kind": clean_text(str(info.get("kind", "extern")), report, limit=60),
                              "summary": clean_text(str(info.get("summary", "")), report,
                                                    limit=400)}
        missing = [i for i in missing if i not in found]
    return found


def exists(ctx: AppContext, evidence_id: str) -> bool:
    return evidence_id in lookup(ctx, [evidence_id])
