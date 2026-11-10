"""Wachtende MediaGuard-jobs naar de juiste baan als de GPU-baan aan- of uitgaat.

Een job krijgt zijn baan bij het aanmaken (``build_plan`` met de baanstatus van dat moment).
Stond de GPU toen uit, dan bleef de job op de CPU staan, ook nadat de Arc werkte. Hier
wordt voor nog niet gestarte bridge-jobs dezelfde beslissing opnieuw genomen met de
opgeslagen MediaGuard-policy en probe; alleen als die beslissing nu een andere baan kiest,
gaan job en besluit samen (één transactie) om. DV, HDR10+ en ``encoder: cpu`` blijven
vanzelf op de CPU, want ``beslis`` stuurt die nooit naar de GPU.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from app.config import Settings
from app.db import Database
from app.media.beslissen import Besluit
from app.mediaguard.policy import MediaGuardPolicy

LOGGER = logging.getLogger(__name__)
BATCH = 200
INTERVAL_SECONDS = 180.0
_last: dict[str, Any] = {"at": None, "gpu_ok": None}


def maybe_rebalance(database: Database, settings: Settings | None,
                    capabilities: dict[str, Any]) -> dict[str, int] | None:
    """Herzien bij een baanwissel meteen, verder hooguit elke 3 minuten."""

    if settings is None or not settings.container_mode:
        return None
    gpu_ok = bool(((capabilities.get("lanes") or {}).get("gpu") or {}).get("enabled"))
    now = time.monotonic()
    due = (_last["gpu_ok"] != gpu_ok or _last["at"] is None
           or now - float(_last["at"]) >= INTERVAL_SECONDS)
    if not due:
        return None
    _last.update(at=now, gpu_ok=gpu_ok)
    try:
        return rebalance_waiting_jobs(database, settings, capabilities)
    except Exception as exc:  # noqa: BLE001 - herzien is een optimalisatie, nooit fataal
        LOGGER.warning("wachtende encodes herzien mislukt: %s", exc)
        return None


def rebalance_waiting_jobs(
    database: Database, settings: Settings, capabilities: dict[str, Any],
    *, limit: int = BATCH,
) -> dict[str, int]:
    """Verplaats wachtende bridge-jobs tussen CPU en GPU. Geeft {'naar_gpu', 'naar_cpu'}."""

    from app.mediaguard.service import build_plan  # geen importcyclus bij het laden

    lanes = capabilities.get("lanes") or {}
    gpu_ok = bool((lanes.get("gpu") or {}).get("enabled"))
    cpu_ok = bool((lanes.get("cpu") or {}).get("enabled"))
    source, target = ("cpu", "gpu") if gpu_ok else ("gpu", "cpu")
    moved = {"naar_gpu": 0, "naar_cpu": 0}
    if not settings.container_mode or (target == "cpu" and not cpu_ok):
        return moved
    rows = database.fetch_all(
        """
        SELECT j.id,j.besluit_id,j.mg_policy,f.pad,f.probe
        FROM job j JOIN bestand f ON f.id=j.bestand_id
        WHERE j.status='wacht' AND j.baan=? AND j.mg_bron IS NOT NULL
          AND j.gestart_op IS NULL AND j.proces_pid IS NULL
          AND COALESCE(j.ai_encode_override,'{}')='{}'
          AND COALESCE(j.gpu_hdr_teruggevallen,0)=0
        ORDER BY j.prioriteit DESC,j.id ASC LIMIT ?
        """,
        (source, limit),
    )
    for row in rows:
        try:
            policy = MediaGuardPolicy.model_validate_json(str(row["mg_policy"] or "{}"))
            if target == "gpu" and policy.encoder == "cpu":
                continue
            info: Any = json.loads(str(row["probe"] or "null"))
            if not isinstance(info, dict) or not info:
                continue  # geen opgeslagen probe: laten staan (geen herprobe midden in de rij)
            plan = build_plan(settings, capabilities, Path(str(row["pad"])), policy, info=info)
        except Exception as exc:  # noqa: BLE001 - één afwijkende job mag de rest niet blokkeren
            LOGGER.debug("baan herzien overgeslagen voor job %s: %s", row["id"], exc)
            continue
        if not plan["approved_for_execution"] or plan["lane"] != target:
            continue
        decision = Besluit.model_validate(plan["decision"])
        with database._write_lock, database.connection() as connection:  # noqa: SLF001
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                "UPDATE job SET baan=?,mg_inst=? WHERE id=? AND status='wacht' AND baan=? "
                "AND gestart_op IS NULL AND proces_pid IS NULL",
                (target, json.dumps(plan["profile_overrides"], ensure_ascii=False),
                 int(row["id"]), source),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                continue
            connection.execute(
                "UPDATE besluit SET baan=?,encoder=?,reden=?,payload=? WHERE id=?",
                (decision.baan or target, decision.encoder or None, decision.reden,
                 decision.model_dump_json(), int(row["besluit_id"])),
            )
            connection.commit()
        moved["naar_gpu" if target == "gpu" else "naar_cpu"] += 1
    if moved["naar_gpu"]:
        LOGGER.info("%s wachtende encodes naar de GPU verplaatst", moved["naar_gpu"])
    if moved["naar_cpu"]:
        LOGGER.info("%s wachtende encodes terug naar de CPU (GPU-baan uit)", moved["naar_cpu"])
    if any(moved.values()):
        from app.planner import wake_planner

        wake_planner()
    return moved
