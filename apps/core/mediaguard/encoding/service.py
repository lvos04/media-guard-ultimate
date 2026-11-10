"""Encoding-orkestratie (core-kant van encoder-bridge.md).

- ``plan_title``: per-titelplan met poorten; ``approved_for_execution`` blijft ``false``
  tot alle poorten slagen (P4).
- ``submit``: effectieve DV/audio/profielpolicy van dít bestand naar de encoder.
- ``sync_active``: volgt encoderjobs; bij ``awaiting_commit`` eigen verificatie (DV/HDR/
  streams, niet uitschakelbaar) en daarna handoff naar recovery of wachten op activatie.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mediaguard import locks
from mediaguard.audio import inventory
from mediaguard.audio.evaluate import AudioPolicy, evaluate_inventory
from mediaguard.audit import audit
from mediaguard.db import dumps, loads, utcnow
from mediaguard.dv import policy as dv_policy
from mediaguard.dv import rules as dv_rules
from mediaguard.dv.verify import verify_output
from mediaguard.encoding.client import EncoderError, get_encoder_client
from mediaguard.identity import service as identity_service
from mediaguard.policy import profiles
from mediaguard.policy.ports import instance_flags
from mediaguard.recovery import service as recovery_service
from mediaguard.settings_registry import SettingDef

if TYPE_CHECKING:
    from mediaguard.context import AppContext

LOGGER = logging.getLogger(__name__)
CATEGORY = "AV1 Studio"
ENC_SCOPES = ("global", "media_type", "instance", "library", "profile", "series", "movie",
              "file")
ACTIVE = ("submitted", "queued", "running", "verifying", "awaiting_commit", "handed_off")
TERMINAL_ENCODER = ("committed", "rejected", "failed", "skipped", "cancelled")


class GateError(Exception):
    def __init__(self, code: str, message: str, *, lane: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.lane = lane  # bij LANE_FULL: de volle baan


def setting_defs() -> list[SettingDef]:
    return [
        SettingDef("encoding.encoder", "enum", "auto", CATEGORY, "Encoder", "Encoder",
                   choices=("auto", "cpu", "gpu"),
                   choice_labels_nl={"auto": "Automatisch", "cpu": "CPU (SVT-AV1)",
                                     "gpu": "GPU (Intel QSV)"},
                   help_nl="HDR/DV gaan altijd via de CPU-route zolang QSV geen metadata "
                           "meedraagt.", scopes=ENC_SCOPES),
        SettingDef("encoding.crf", "int", 0, CATEGORY, "CRF (0 = automatisch)",
                   "CRF (0 = automatic)", minimum=0, maximum=63, scopes=ENC_SCOPES,
                   advanced=True),
        SettingDef("encoding.preset", "int", 0, CATEGORY, "Preset (0 = automatisch)",
                   "Preset (0 = automatic)", minimum=0, maximum=13, scopes=ENC_SCOPES,
                   advanced=True),
        SettingDef("encoding.min_saving_percent", "int", 15, CATEGORY,
                   "Minimale besparing (%)", "Minimum saving (%)",
                   help_nl="Kleinere winst: origineel behouden.", minimum=0, maximum=90,
                   scopes=ENC_SCOPES),
        SettingDef("encoding.keep_all_audio", "bool", True, CATEGORY,
                   "Alle audiosporen behouden", "Keep all audio tracks",
                   help_nl="Niets verwijderen vóór de nieuwe taalpolicy (P6).",
                   scopes=ENC_SCOPES),
        SettingDef("encoding.keep_all_subtitles", "bool", True, CATEGORY,
                   "Alle ondertitels behouden", "Keep all subtitles", scopes=ENC_SCOPES),
        SettingDef("encoding.auto_commit_after_verify", "bool", True, CATEGORY,
                   "Automatisch activeren na verificatie (Full Auto)",
                   "Auto-activate after verification",
                   help_nl="Set-and-forget: een geverifieerde encode vervangt de bron vanzelf; "
                           "het origineel gaat eerst naar de quarantaine en is te herstellen. "
                           "Alleen met werkend herstelpad.",
                   scopes=ENC_SCOPES),
        SettingDef("encoding.canary_required", "int", 3, CATEGORY,
                   "Voorzichtige start: eerste conversies één voor één",
                   "Careful start: first conversions one at a time",
                   help_nl="Zolang er minder dan dit aantal conversies geslaagd zijn, biedt de "
                           "autopilot maar één bestand tegelijk aan.", minimum=0, maximum=1000),
        SettingDef("encoding.identity_gate", "enum", "block_mismatch_uncertain", CATEGORY,
                   "Identiteitspoort", "Identity gate",
                   choices=("block_mismatch_uncertain", "require_verified"),
                   choice_labels_nl={"block_mismatch_uncertain":
                                     "Blokkeer bij verkeerd/onzeker",
                                     "require_verified": "Alleen geverifieerd/waarschijnlijk"},
                   scopes=ENC_SCOPES),
        SettingDef("encoding.cache_min_free_gb", "int", 150, CATEGORY,
                   "Pauzebodem encodercache (GB vrij)", "Encoder cache pause floor (GB free)",
                   help_nl="Zakt de vrije ruimte op de encodercache (/work) onder deze grens, "
                           "dan pauzeert de encoder zijn banen en blijven jobs in de wachtrij. "
                           "De reden staat in Onderhoud → Gezondheid. Core zet deze waarde "
                           "automatisch door naar de encoder.",
                   minimum=1, maximum=100000, unit="GB",
                   effect_nl="Direct: de encoder herbeoordeelt de vrije ruimte meteen."),
        SettingDef("encoding.poll_interval_seconds", "int", 30, CATEGORY,
                   "Statusinterval encoder (s)", "Encoder poll interval (s)", minimum=5,
                   maximum=3600, advanced=True),
    ]


# ── context ───────────────────────────────────────────────────────────────────
def chains(ctx: AppContext, *, instance_id: str | None, media_type: str | None = None,
           series_id: Any = None, movie_id: Any = None, file_id: Any = None,
           job_id: Any = None) -> tuple[dict[str, str], dict[str, str]]:
    """(policy/encoding-keten, DV-keten) voor één bestand."""
    if instance_id and not media_type:
        media_type = instance_flags(ctx, instance_id).get("media_type")
    pchain = profiles.build_chain(instance_id=instance_id, media_type=media_type,
                                  series_id=series_id, movie_id=movie_id, file_id=file_id)
    if instance_id:
        pchain["library"] = instance_id
    eff = profiles.effective_policy(ctx, pchain)
    pchain["profile"] = eff["profile_id"]
    dchain = dv_policy.scope_chain(
        library=instance_id, profile=eff["profile_id"],
        series=f"{instance_id}:{series_id}" if series_id is not None else None,
        movie=f"{instance_id}:{movie_id}" if movie_id is not None else None,
        file=f"{instance_id}:{file_id}" if file_id is not None else None,
        job=str(job_id) if job_id is not None else None)
    return pchain, dchain


PUSHED_FLOOR_STATE = "encoding.cache_min_free_gb.pushed"


async def push_encoder_settings(ctx: AppContext, *, force: bool = False) -> dict[str, Any]:
    """Zet core-instellingen die de encoder zelf toepast door (nu: de pauzebodem).

    Idempotent: alleen pushen als de gewenste waarde afwijkt van de laatst
    geslaagde push (of ``force``). Encoder onbereikbaar → volgende poll opnieuw."""
    effective = ctx.config.effective("encoding.cache_min_free_gb")
    desired = int(effective["value"])
    last = ctx.state_get(PUSHED_FLOOR_STATE)
    if not force and isinstance(last, dict) and last.get("value") == desired:
        return {"pushed": False, "value": desired}
    if not force and last is None and effective["source"] == "default":
        # Nooit ingesteld in core: de encoder houdt zijn eigen waarde (default ook 150;
        # een bewust lagere testwaarde via de oude encoder-API blijft dan staan).
        return {"pushed": False, "value": desired, "reason": "standaardwaarde"}
    try:
        result = await get_encoder_client(ctx).set_storage_settings(desired)
    except EncoderError as exc:
        return {"pushed": False, "value": desired, "error": str(exc)}
    ctx.state_set(PUSHED_FLOOR_STATE, {"value": desired, "at": utcnow()})
    return {"pushed": True, "value": desired, "encoder": result}


async def reconcile_engine_commits(ctx: AppContext) -> dict[str, int]:
    """Rapport 6, N5: een in core al afgeronde overdracht die in de engine nog op
    'wacht op commit' staat (gemiste melding, engine even onbereikbaar) alsnog melden, zodat
    de engine-tellingen ('wacht op activatie') kloppen."""
    client = get_encoder_client(ctx)
    try:
        waiting = await client.awaiting_commit()
    except EncoderError:
        return {"committed": 0, "rejected": 0}
    if not waiting:
        return {"committed": 0, "rejected": 0, "adopted": 0}
    placeholders = ",".join("?" for _ in waiting)
    rows = ctx.db.fetch_dicts(
        f"SELECT encoder_job_id, status FROM encoding_jobs WHERE encoder_job_id IN "  # noqa: S608
        f"({placeholders}) AND status IN ('committed','rejected','cancelled')",
        tuple(str(x) for x in waiting))
    done = {"committed": 0, "rejected": 0, "adopted": await _adopt_engine_results(ctx, waiting)}
    for row in rows:
        try:
            if row["status"] == "committed":
                await client.committed(row["encoder_job_id"])
                done["committed"] += 1
            else:
                await client.rejected(row["encoder_job_id"],
                                      "In MediaGuard al afgehandeld (niet geplaatst)")
                done["rejected"] += 1
        except EncoderError as exc:
            LOGGER.info("engine-stand van job %s bijwerken mislukt: %s",
                        row["encoder_job_id"], exc)
    return done


def _source_unchanged(path: str, snapshot: Any) -> bool:
    """Klopt het bronbestand nog met wat de engine encodeerde (grootte, en mtime als bekend)?"""
    import os

    snap = snapshot if isinstance(snapshot, dict) else {}
    try:
        st = os.stat(path)
    except OSError:
        return False
    size = snap.get("size")
    if size is not None and int(size) != st.st_size:
        return False
    mtime = snap.get("mtime")
    return mtime is None or abs(float(mtime) - st.st_mtime) < 2


async def _adopt_engine_results(ctx: AppContext, waiting: list[int]) -> int:
    """Rapport 8 / A6: een herkansing in de engine die WEL lukte (wacht_op_commit) terwijl
    core de job nog als 'mislukt' kent, wordt alsnog opgepakt: fout wissen, (nieuw)
    engine-ID koppelen en de gewone controle + overdracht laten lopen. Alleen als het
    bronbestand ongewijzigd is; door de gebruiker afgebroken jobs nooit."""
    client = get_encoder_client(ctx)
    known = {str(r["encoder_job_id"]): r for r in ctx.db.fetch_dicts(
        "SELECT id, encoder_job_id, status FROM encoding_jobs WHERE encoder_job_id IN ("
        + ",".join("?" for _ in waiting) + ")", tuple(str(x) for x in waiting))}
    adopted = 0
    for eid in waiting:
        row = known.get(str(eid))
        if row is not None and row["status"] != "failed":
            continue  # core volgt hem al (of hij is bewust afgebroken/afgehandeld)
        try:
            enc = await client.get_job(eid)
        except EncoderError:
            continue
        if row is None:
            corr = str(enc.get("correlation_id") or "")
            path = str(enc.get("source_path") or enc.get("path") or "")
            if corr.startswith("mg-enc-"):
                row = ctx.db.fetch_one(
                    "SELECT id, encoder_job_id, status FROM encoding_jobs WHERE "
                    "correlation_id=? OR id=?", (corr, corr.removeprefix("mg-enc-")))
            elif path:  # geen correlatie: alleen de nieuwste mislukte job voor dit pad
                row = ctx.db.fetch_one(
                    "SELECT id, encoder_job_id, status FROM encoding_jobs WHERE source_path=? "
                    "ORDER BY id DESC LIMIT 1", (path,))
            if row is None or row["status"] != "failed":
                continue
        job = get_job(ctx, int(row["id"])) or {}
        if not _source_unchanged(str(job.get("source_path") or ""),
                                 enc.get("source_snapshot")):
            with contextlib.suppress(EncoderError):
                await client.rejected(eid, "Bron is intussen gewijzigd; niet geplaatst")
            continue
        _update(ctx, int(row["id"]), status="verifying", error=None,
                encoder_job_id=str(eid))
        audit(ctx.db, actor="svc:encoding", action="encoding.retry_adopted",
              target_type="encoding_job", target_id=str(row["id"]),
              reason=f"Herkansing in de engine (job {eid}) gelukt; wordt alsnog gecontroleerd "
                     "en overgedragen")
        try:
            await sync_job(ctx, int(row["id"]))
        except Exception:  # noqa: BLE001
            LOGGER.exception("geadopteerde engine-job %s", eid)
        adopted += 1
    return adopted


EMERGENCY_PUSH_STATE = "encoder_emergency_pushed"


async def push_emergency_stop(ctx: AppContext, *, force: bool = False) -> dict[str, Any]:
    """Zet de noodstop door naar de AV1-engine (rapport 6, N1).

    De engine bewaart de stand zelf; core zet hem direct bij een wijziging door en bij elke
    poll opnieuw zolang hij afwijkt van de laatst geslaagde push (of na een engineherstart:
    de engine meldt dan een andere stand, zie ``force``)."""
    state = ctx.state_get("emergency_stop", {}) or {}
    desired = {"active": bool(state.get("active")), "reason": str(state.get("reason") or "")}
    last = ctx.state_get(EMERGENCY_PUSH_STATE)
    if not force and isinstance(last, dict) and last.get("active") == desired["active"] \
            and last.get("reason") == desired["reason"]:
        return {"pushed": False, **desired}
    try:
        result = await get_encoder_client(ctx).set_emergency_stop(desired["active"],
                                                                  desired["reason"])
    except EncoderError as exc:
        return {"pushed": False, **desired, "error": str(exc)}
    ctx.state_set(EMERGENCY_PUSH_STATE, {**desired, "at": utcnow()})
    return {"pushed": True, **desired, "encoder": result}


async def capabilities(ctx: AppContext) -> dict[str, Any] | None:
    cached = ctx.services.get("encoder_caps_cache")
    try:
        caps = await get_encoder_client(ctx).capabilities()
    except EncoderError:
        return cached  # type: ignore[no-any-return]
    ctx.services["encoder_caps_cache"] = caps
    return caps


def confirmations(ctx: AppContext) -> list[dict[str, Any]]:
    return ctx.db.fetch_dicts("SELECT * FROM dv_playback_confirmations")


def dv_decision(ctx: AppContext, probe: dict[str, Any], dchain: dict[str, str],
                caps: dict[str, Any] | None) -> dv_rules.DvDecision:
    pol = dv_policy.effective(ctx, dchain)
    return dv_rules.decide(probe, pol, caps, confirmations(ctx),
                           show_playback_warnings=bool(
                               ctx.config.value("dv.show_playback_warnings")))


def encoder_policy(ctx: AppContext, pchain: dict[str, str], decision: dv_rules.DvDecision,
                   *, force_cpu: bool = False) -> dict[str, Any]:
    eff = profiles.effective_policy(ctx, pchain)
    audio_cfg = eff["config"]["audio"]
    keep_langs = ["nld", "eng", "jpn"]
    crf = int(ctx.config.value("encoding.crf", pchain))
    preset = int(ctx.config.value("encoding.preset", pchain))
    # DV blijft op de CPU (RPU behouden), behalve DV 8.1 → HDR10 als bewuste keuze (rapport 7)
    dv_on_cpu = decision.is_dv and decision.route != "dv81_naar_hdr10"
    enc = "cpu" if force_cpu or dv_on_cpu else ctx.config.value("encoding.encoder", pchain)
    if enc == "auto" and int(ctx.config.value("resources.encodes_gpu")) <= 0:
        # 'GPU-encodes tegelijk = 0' betekent: de GPU niet gebruiken. Anders zet de encoder
        # het bestand op een GPU-baan zonder plekken en blijft het daar staan.
        enc = "cpu"
    pol: dict[str, Any] = {
        "encoder": enc, "target": "staging",
        **decision.encoder_policy(),
        "hdr10plus": (decision.policy or {}).get("hdr10plus_mode", "skip")
        if decision.hdr10plus and decision.executable else "skip",
        "audio_policy": {"keep_languages": keep_langs,
                         "keep_all": bool(ctx.config.value("encoding.keep_all_audio", pchain)),
                         "mode": audio_cfg["mode"]},
        "subtitle_policy": {"keep_all": bool(ctx.config.value("encoding.keep_all_subtitles",
                                                              pchain))},
        "min_saving_percent": int(ctx.config.value("encoding.min_saving_percent", pchain)),
        "profile_id": eff["profile_id"],
        # MediaGuard heeft het bestand al gekozen (drempels per resolutie, profielen, DV): de
        # encoder past geen eigen bitrate-/codecdrempels meer toe (zie encoder-bridge.md)
        "decided_by": "mediaguard",
        "skip_thresholds": True,
    }
    if crf:
        pol["crf"] = crf
    if preset:
        pol["preset"] = preset
    return pol


def identity_gate(ctx: AppContext, instance_id: str | None, media_kind: str | None,
                  provider_id: Any, pchain: dict[str, str]) -> dict[str, Any]:
    if not instance_id or provider_id is None:
        return {"name": "identity", "passed": True, "detail_nl": "Geen arr-item: n.v.t."}
    latest = identity_service.latest(ctx, instance_id, media_kind or "episode_file",
                                     provider_id)
    status = latest["status"] if latest else "unresolved"
    mode = ctx.config.value("encoding.identity_gate", pchain)
    blocked = {"mismatch", "uncertain"} if mode == "block_mismatch_uncertain" else \
        {"mismatch", "uncertain", "unresolved"}
    return {"name": "identity", "passed": status not in blocked,
            "detail_nl": f"Identiteit: {status}"}


def _resource(instance_id: str | None, media_kind: str | None, provider_id: Any) -> str | None:
    if instance_id and provider_id is not None:
        return locks.media_resource(instance_id, media_kind or "file", provider_id)
    return None


def compute_gates(ctx: AppContext, *, instance_id: str | None, media_kind: str | None,
                  provider_id: Any, path: str, probe: dict[str, Any] | None,
                  decision: dv_rules.DvDecision | None, encoder_plan: dict[str, Any] | None,
                  pchain: dict[str, str]) -> list[dict[str, Any]]:
    gates = [{"name": "emergency_stop", "passed": not ctx.emergency_stopped(),
              "detail_nl": "Noodstop " + ("actief" if ctx.emergency_stopped() else "uit")}]
    gates.append(identity_gate(ctx, instance_id, media_kind, provider_id, pchain))
    res = _resource(instance_id, media_kind, provider_id)
    held = locks.holder(ctx.db, res) if res else None
    gates.append({"name": "not_locked", "passed": held is None,
                  "detail_nl": f"Bezet door {held['owner']}" if held else "Geen lock"})
    gates.append({"name": "probe", "passed": probe is not None,
                  "detail_nl": "Probe beschikbaar" if probe else "Probe mislukt"})
    if probe is not None:
        is_av1 = str(probe.get("codec", "")).lower() == "av1"
        gates.append({"name": "not_already_av1", "passed": not is_av1,
                      "detail_nl": "Al AV1: geen hercodering" if is_av1 else
                      f"Bron {probe.get('codec')}"})
    if decision is not None:
        gates.append({"name": "dv_hdr_policy", "passed": decision.executable,
                      "detail_nl": decision.reason_nl})
    if encoder_plan is not None:
        actie = str(encoder_plan.get("actie") or encoder_plan.get("action") or "")
        ok = actie in ("hercoderen", "encode", "transcode")
        gates.append({"name": "encoder_decision", "passed": ok,
                      "detail_nl": str(encoder_plan.get("reden") or actie or "onbekend")})
    try:
        st = os.stat(path)
        gates.append({"name": "hardlinks", "passed": st.st_nlink <= 1 or ctx.config.value(
            "recovery.hardlink_policy", {"instance": instance_id} if instance_id else None
        ) != "skip", "detail_nl": f"{st.st_nlink} link(s)"})
    except OSError:
        gates.append({"name": "hardlinks", "passed": False,
                      "detail_nl": "Bestand niet bereikbaar vanuit core"})
    ready = recovery_service.readiness(ctx)
    gates.append({"name": "recovery_ready", "passed": ready["ready"],
                  "detail_nl": "; ".join(ready["reasons"]) or "Herstelpad gereed"})
    return gates


def risk_class(probe: dict[str, Any] | None) -> str:
    if not probe:
        return "onbekend"
    if probe.get("dv_profile") or probe.get("hdr10plus"):
        return "hoog"
    if probe.get("is_hdr10"):
        return "middel"
    return "laag"


async def plan_title(ctx: AppContext, *, path: str, instance_id: str | None = None,
                     media_kind: str | None = None, provider_id: Any = None,
                     series_id: Any = None, movie_id: Any = None, media_type: str | None = None,
                     actor: str = "system", store: bool = True) -> dict[str, Any]:
    pchain, dchain = chains(ctx, instance_id=instance_id, media_type=media_type,
                            series_id=series_id, movie_id=movie_id, file_id=provider_id)
    client = get_encoder_client(ctx)
    probe: dict[str, Any] | None = None
    enc_plan: dict[str, Any] | None = None
    errors: list[str] = []
    try:
        probe = await client.probe(path)
    except EncoderError as exc:
        errors.append(str(exc))
    caps = await capabilities(ctx)
    decision = dv_decision(ctx, probe, dchain, caps) if probe else None
    pol = encoder_policy(ctx, pchain, decision) if decision else {}
    if probe is not None:
        try:
            enc_plan = await client.plan(path, pol)
        except EncoderError as exc:
            errors.append(str(exc))
    audio = None
    if probe is not None:
        eff = profiles.effective_policy(ctx, pchain)
        inv = inventory.from_encoder_probe(probe)
        audio = {"inventory": inv.to_dict(), "verdict": evaluate_inventory(
            inv, AudioPolicy.from_dict(eff["config"]["audio"])).to_dict()}
    gates = compute_gates(ctx, instance_id=instance_id, media_kind=media_kind,
                          provider_id=provider_id, path=path, probe=probe, decision=decision,
                          encoder_plan=enc_plan, pchain=pchain)
    approved = all(g["passed"] for g in gates) and not errors
    plan = {"source_path": path, "instance_id": instance_id, "media_kind": media_kind,
            "provider_id": provider_id, "probe_summary": _probe_summary(probe),
            "dv_decision": decision.to_dict() if decision else None, "encoder_plan": enc_plan,
            "policy": pol, "audio": audio, "risk_class": risk_class(probe),
            "sample_policy": (enc_plan or {}).get("sample_policy"),
            "expected_size": (enc_plan or {}).get("expected_size"),
            "errors": errors, "gates": gates, "approved_for_execution": approved}
    if store:
        plan["id"] = ctx.db.execute_insert(
            "INSERT INTO encoding_plans(instance_id,media_kind,provider_id,source_path,plan_json,"
            "gates_json,approved_for_execution,risk_class,created_at,created_by) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (instance_id, media_kind, None if provider_id is None else str(provider_id), path,
             dumps(plan), dumps(gates), int(approved), plan["risk_class"], utcnow(), actor))
    return plan


HDR_TRANSFERS = {"smpte2084": "PQ", "arib-std-b67": "HLG"}


def mediainfo_summary(probe: dict[str, Any] | None) -> dict[str, Any] | None:
    """Mediainfo van het nieuwe bestand uit MediaGuard's eigen controle (rapport 3, R10;
    rapport 4, S18). Zonder videogegevens is er níets gemeten: dan None, zodat de UI
    'niet gemeten' toont in plaats van 'SDR, geen geluid'."""
    if not probe or not probe.get("codec"):
        return None
    audio = probe.get("audio") or []
    subs = probe.get("subs") or probe.get("subtitles") or []
    kleur = probe.get("kleur") or {}
    transfer = str(kleur.get("transfer") or probe.get("color_transfer") or "")
    pix_fmt = str(kleur.get("pix_fmt") or probe.get("pix_fmt") or "")
    dv = probe.get("dv_profile") or None
    hdr10 = bool(probe.get("is_hdr10"))
    if dv:
        dynamic_range = f"Dolby Vision {dv}"
    elif probe.get("hdr10plus"):
        dynamic_range = "HDR10+"
    elif hdr10:
        dynamic_range = "HDR10"
    elif transfer in HDR_TRANSFERS:
        dynamic_range = HDR_TRANSFERS[transfer]
    else:
        dynamic_range = "SDR" if transfer or pix_fmt else None
    bit_depth = 10 if ("10" in pix_fmt or "p010" in pix_fmt) else (8 if pix_fmt else None)
    return {
        "video": {"codec": probe.get("codec"), "width": probe.get("width"),
                  "height": probe.get("height"), "hdr10": hdr10 or transfer in HDR_TRANSFERS,
                  "dv_profile": dv, "kbps": probe.get("kbps"), "pix_fmt": pix_fmt or None,
                  "bit_depth": bit_depth, "color_transfer": transfer or None,
                  "color_primaries": kleur.get("primaries") or None,
                  "dynamic_range": dynamic_range},
        "audio": [{"language": a.get("taal") or a.get("language"), "codec": a.get("codec"),
                   "channels": a.get("kanalen") or a.get("channels"),
                   "title": a.get("titel") or a.get("title") or None} for a in audio],
        "subtitles": [{"language": x.get("taal") or x.get("language"),
                       "codec": x.get("codec")} for x in subs],
        "size": probe.get("size"), "duration": probe.get("duration"),
        "source": "mediaguard", "measured_at": utcnow(),
    }


def _probe_summary(probe: dict[str, Any] | None) -> dict[str, Any] | None:
    if not probe:
        return None
    keys = ("codec", "width", "height", "duration", "size", "kbps", "is_hdr10", "hdr10plus",
            "dv_profile", "dv_compat", "dv_el_type", "container")
    return {k: probe.get(k) for k in keys if k in probe}


# ── jobs ──────────────────────────────────────────────────────────────────────
def get_job(ctx: AppContext, job_id: int) -> dict[str, Any] | None:
    row = ctx.db.fetch_one("SELECT * FROM encoding_jobs WHERE id=?", (job_id,))
    if row is None:
        return None
    d = dict(row)
    for col in ("policy_json", "dv_decision_json", "source_snapshot_json", "staged_output_json",
                "verification_json", "core_verification_json", "decision_json"):
        d[col[:-5]] = loads(d.pop(col), None)
    d["trial_only"] = bool(d["trial_only"])
    return d


def _update(ctx: AppContext, job_id: int, **fields: Any) -> None:
    fields["updated_at"] = utcnow()
    cols = ", ".join(f"{k}=?" for k in fields)
    ctx.db.execute(f"UPDATE encoding_jobs SET {cols} WHERE id=?", (*fields.values(), job_id))


# Een resultaat dat er al is (of komt) voor hetzelfde bestand: nooit een tweede encode.
RESULT_STATES = ("submitted", "queued", "running", "verifying", "awaiting_commit",
                 "awaiting_activation", "handing_off", "handed_off", "conflict_review")


def existing_result(ctx: AppContext, path: str) -> dict[str, Any] | None:
    marks = ",".join("?" for _ in RESULT_STATES)
    return ctx.db.fetch_one(
        f"SELECT id, status FROM encoding_jobs WHERE source_path=? AND trial_only=0 AND "  # noqa: S608
        f"status IN ({marks}) ORDER BY id DESC LIMIT 1", (path, *RESULT_STATES))


async def submit(ctx: AppContext, *, path: str, instance_id: str | None = None,
                 media_kind: str | None = None, provider_id: Any = None,
                 series_id: Any = None, movie_id: Any = None, media_type: str | None = None,
                 trial_only: bool = False, force_cpu: bool = False, actor: str = "system",
                 manual_activation: bool = False,
                 dv_test_id: int | None = None, plan_id: int | None = None,
                 job_scope_id: str | None = None,
                 refresh: dict[str, Any] | None = None,
                 avoid_lanes: list[str] | None = None) -> dict[str, Any]:
    """Bestand naar de encoder. ``avoid_lanes``: banen die vol zitten; komt het bestand op
    zo'n baan uit, dan geen job maar ``GateError('LANE_FULL')`` (verbeterpunt 6)."""
    from mediaguard.encoding import lanes

    if ctx.emergency_stopped():
        raise GateError("EMERGENCY_STOP", "Noodstop actief: geen nieuwe encodes")
    from mediaguard.recovery.release_check import quota_exceeded

    if not trial_only and quota_exceeded(ctx):
        raise GateError("QUARANTINE_FULL", "Quarantaine heeft het ingestelde maximum bereikt: "
                        "AV1-wachtrij gepauzeerd tot er originelen zijn opgeruimd")
    pchain, dchain = chains(ctx, instance_id=instance_id, media_type=media_type,
                            series_id=series_id, movie_id=movie_id, file_id=provider_id,
                            job_id=job_scope_id)
    gate = identity_gate(ctx, instance_id, media_kind, provider_id, pchain)
    if not gate["passed"]:
        raise GateError("IDENTITY", gate["detail_nl"] + ": eerst identiteit controleren")
    res = _resource(instance_id, media_kind, provider_id)
    held = locks.holder(ctx.db, res) if res else None
    if held is not None:
        raise GateError("LOCKED", f"Item bezet door {held['owner']} ({held['purpose']})")
    if not trial_only:
        dup = existing_result(ctx, path)
        if dup is not None:
            raise GateError("DUPLICATE", f"Er is al een AV1-resultaat of lopende encode voor dit "
                            f"bestand (job #{dup['id']}, {dup['status']}): niet opnieuw encoderen")
    client = get_encoder_client(ctx)
    try:
        probe = await client.probe(path)
    except EncoderError as exc:
        raise GateError("ENCODER_UNAVAILABLE", str(exc)) from exc
    if not trial_only and str(probe.get("codec", "")).lower() == "av1":
        raise GateError("ALREADY_AV1", "Bestand is al AV1: niets te doen")
    caps = await capabilities(ctx)
    decision = dv_decision(ctx, probe, dchain, caps)
    pol = encoder_policy(ctx, pchain, decision, force_cpu=force_cpu)
    avoid = sorted({lane for lane in (avoid_lanes or []) if lane in lanes.LANES})
    planned_lane = lanes.predicted_lane(pol)
    if decision.executable and planned_lane in avoid:
        # al bekend vóór de encoder: geen job, later opnieuw (de andere baan gaat voor)
        raise GateError("LANE_FULL", lanes.lane_full_message(planned_lane), lane=planned_lane)
    # alleen echt niet-vervangbare resultaten als proefkopie naar de encoder
    trial = trial_only or (decision.separate_output and not decision.replace_source_allowed)
    now = utcnow()
    job_id = ctx.db.execute_insert(
        "INSERT INTO encoding_jobs(instance_id,media_kind,provider_id,source_path,trial_only,"
        "status,policy_json,dv_decision_json,dv_test_id,plan_id,decision_json,created_at,"
        "created_by,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (instance_id, media_kind, None if provider_id is None else str(provider_id), path,
         int(trial), "submitted", dumps(pol), dumps(decision.to_dict()), dv_test_id, plan_id,
         dumps({"refresh": refresh or {}, "series_id": series_id, "movie_id": movie_id,
                "manual_activation": manual_activation}),
         now, actor, now))
    if not decision.executable:
        _update(ctx, job_id, status="skipped", skip_reason=decision.reason_nl)
        return get_job(ctx, job_id) or {}
    body: dict[str, Any] = {"source_path": path, "correlation_id": f"mg-enc-{job_id}",
                            "media_ref": {"instance_id": instance_id, "kind": media_kind,
                                          "provider_id": provider_id},
                            "policy": pol, "trial_only": trial}
    if avoid:
        body["avoid_lanes"] = avoid
    try:
        try:
            resp = await client.create_job(body)
        except EncoderError as exc:
            if not (avoid and exc.status == 422 and "avoid_lanes" in str(exc)):
                raise
            # oudere encoder kent avoid_lanes niet: zonder baanwens indienen
            resp = await client.create_job({k: v for k, v in body.items()
                                            if k != "avoid_lanes"})
    except EncoderError as exc:
        full = lanes.lane_full_error(exc) if exc.status == 409 else None
        if full:
            # geen fout en geen resultaat: de rij weg, het bestand komt later terug
            ctx.db.execute("DELETE FROM encoding_jobs WHERE id=? AND encoder_job_id IS NULL",
                           (job_id,))
            raise GateError("LANE_FULL", lanes.lane_full_message(full), lane=full) from exc
        _update(ctx, job_id, status="failed", error=str(exc))
        raise GateError("ENCODER_ERROR", str(exc)) from exc
    lane = str(resp.get("lane") or "") or planned_lane
    _update(ctx, job_id, encoder_job_id=str(resp.get("job_id")), status="queued",
            correlation_id=f"mg-enc-{job_id}",
            lane=lane if lane in lanes.LANES else None)
    audit(ctx.db, actor=actor, action="encoding.submit", target_type=media_kind or "file",
          target_id=provider_id, instance_id=instance_id,
          new_value={"job_id": job_id, "trial_only": trial, "dv_mode": pol.get("dv_mode")})
    return get_job(ctx, job_id) or {}


async def cancel(ctx: AppContext, job_id: int, actor: str) -> dict[str, Any]:
    job = get_job(ctx, job_id)
    if job is None:
        raise LookupError("Encodeerjob niet gevonden")
    if job["status"] in ("handed_off",):
        raise GateError("HANDED_OFF", "Vervanging loopt al; annuleren via herstel")
    if job["encoder_job_id"] and job["status"] not in TERMINAL_ENCODER:
        try:
            enc = await get_encoder_client(ctx).cancel_job(job["encoder_job_id"])
        except EncoderError as exc:
            if exc.status != 404:
                # niet lokaal als geannuleerd markeren: anders loopt de encode ongezien door
                raise GateError("ENCODER_UNAVAILABLE",
                                f"Annuleren bij de encoder mislukt: {exc}") from exc
            enc = {"status": "cancelled"}
        if str(enc.get("status") or "") not in ("cancelled", "failed", "rejected",
                                                "skipped"):
            _update(ctx, job_id, error="Annuleren gevraagd; de encoder stopt de lopende stap")
            audit(ctx.db, actor=actor, action="encoding.cancel_requested",
                  target_type="encoding_job", target_id=job_id,
                  instance_id=job["instance_id"])
            return get_job(ctx, job_id) or {}
    from mediaguard.encoding.autopilot import USER_CANCEL

    reason = USER_CANCEL if not actor.startswith("svc:") else "geannuleerd door MediaGuard"
    _update(ctx, job_id, status="cancelled", error=reason)
    audit(ctx.db, actor=actor, action="encoding.cancel", target_type="encoding_job",
          target_id=job_id, instance_id=job["instance_id"])
    return get_job(ctx, job_id) or {}


def canary_passed(ctx: AppContext) -> bool:
    need = int(ctx.config.value("encoding.canary_required"))
    row = ctx.db.fetch_one("SELECT COUNT(*) AS n FROM encoding_jobs WHERE status='committed'")
    return int(row["n"] if row else 0) >= need


async def _verify(ctx: AppContext, job: dict[str, Any], enc: dict[str, Any]
                  ) -> dict[str, Any]:
    snap = enc.get("source_snapshot") or {}
    source_probe = snap.get("probe") or {}
    staged = enc.get("staged_output") or {}
    output_probe: dict[str, Any] = {}
    if staged.get("path"):
        try:
            output_probe = await get_encoder_client(ctx).probe(staged["path"])
        except EncoderError as exc:
            return {"passed": False, "checks": [{"name": "output_probe", "passed": False,
                                                 "detail_nl": str(exc)}]}
    policy = job["policy"] or {}
    action = str((enc.get("decision") or {}).get("actie") or "")
    if action and action != "hercoderen":
        # MediaGuard vroeg een AV1-encode; een remux/opruimactie van de encoder is niet wat
        # core besliste en wordt nooit geactiveerd
        return {"passed": False, "checks": [{
            "name": "encoder_action", "passed": False,
            "detail_nl": f"Encoder voerde '{action}' uit in plaats van een AV1-encode"}]}
    result = verify_output(
        source_probe, output_probe,
        keep_all_audio=bool((policy.get("audio_policy") or {}).get("keep_all", True)),
        keep_all_subtitles=bool((policy.get("subtitle_policy") or {}).get("keep_all", True)),
        encoder_verification=enc.get("verification"),
        hdr10plus_mode=str(policy.get("hdr10plus") or "convert_verified"),
        dv_to_hdr10=bool(policy.get("dv81_to_hdr10")))
    data = result.to_dict()
    data["output_mediainfo"] = mediainfo_summary(output_probe)
    decision = job["dv_decision"] or {}
    if decision.get("is_dv"):
        ctx.db.execute(
            "INSERT INTO dv_conversion_evidence(dv_test_id,encoding_job_id,source_profile,"
            "el_type,route,method,rpu_preserved,all_picture_info_preserved,passed,"
            "metadata_before_json,metadata_after_json,checks_json,created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (job["dv_test_id"], job["id"], decision.get("input_profile"),
             source_probe.get("dv_el_type"), decision.get("route"),
             (enc.get("verification") or {}).get("method") or decision.get("route"),
             None if result.rpu_preserved is None else int(result.rpu_preserved),
             None if result.all_picture_info_preserved is None
             else int(result.all_picture_info_preserved), int(result.passed),
             dumps(_probe_summary(source_probe) or {}), dumps(_probe_summary(output_probe) or {}),
             dumps(data["checks"]), utcnow()))
    return data


async def _precheck(ctx: AppContext, job: dict[str, Any], enc: dict[str, Any],
                    verification: dict[str, Any]) -> dict[str, Any]:
    """Vrijgavecontrole VÓÓR vervangen: het AV1-bestand moet alles bevatten wat het beleid
    vraagt (standaard alle audio- en ondertitelsporen) voordat het origineel wordt geraakt."""
    from mediaguard.recovery import release_check

    staged = Path(str((enc.get("staged_output") or {}).get("path") or ""))
    source = Path(str(job.get("source_path") or ""))
    if not staged.is_file() or not source.is_file():
        return verification  # niet zichtbaar voor core (los encoder-image): encoder verifieert
    try:
        checks = await asyncio.to_thread(release_check.precheck, source, staged,
                                         job.get("policy") or {})
    except Exception as exc:  # noqa: BLE001 - kan niet controleren = niet vervangen
        checks = [{"name": "probe", "passed": False,
                   "detail": f"Controle vooraf niet gelukt: {str(exc)[:200]}"}]
    out = dict(verification)
    out["checks"] = list(verification.get("checks") or []) + [
        {"name": f"vooraf:{c['name']}", "passed": c["passed"], "detail_nl": c["detail"]}
        for c in checks]
    out["passed"] = bool(verification.get("passed")) and all(c["passed"] for c in checks)
    return out


def failed_checks_text(verification: dict[str, Any]) -> str:
    """'audio_tracks (8 → 7); mastering_display_values (…)' — wat precies niet klopte."""
    failed = [c for c in verification.get("checks") or [] if not c.get("passed")]
    if not failed:
        return "onbekende controle"
    parts = [f"{c.get('name')}: {c.get('detail_nl') or ''}".rstrip(": ") for c in failed]
    return "; ".join(parts)


async def sync_job(ctx: AppContext, job_id: int) -> dict[str, Any]:
    job = get_job(ctx, job_id)
    if job is None:
        raise LookupError("Encodeerjob niet gevonden")
    client = get_encoder_client(ctx)
    if job["status"] == "handed_off":
        return await _sync_handoff(ctx, job)
    if job["status"] not in ACTIVE or not job["encoder_job_id"]:
        return job
    try:
        enc = await client.get_job(job["encoder_job_id"])
    except EncoderError as exc:
        if exc.status == 404:
            _update(ctx, job_id, status="failed",
                    error="De encoder kent deze job niet meer (database opnieuw opgebouwd?)")
            return get_job(ctx, job_id) or {}
        LOGGER.info("encoderstatus niet op te halen: %s", exc)
        return job
    est = str(enc.get("status") or "")
    fields: dict[str, Any] = {
        "encoder_status": est, "progress": float(enc.get("progress") or 0),
        "source_snapshot_json": dumps(enc.get("source_snapshot")),
        "staged_output_json": dumps(enc.get("staged_output")),
        "verification_json": dumps(enc.get("verification")),
        "error": enc.get("error"), "skip_reason": enc.get("skip_reason")}
    if enc.get("lane") in ("cpu", "gpu"):
        # baan uit de encoder; verandert bij een GPU→CPU-herkansing (verbeterpunt 6)
        fields["lane"] = enc["lane"]
    if est in ("queued", "running", "verifying"):
        _update(ctx, job_id, status=est, **fields)
        return get_job(ctx, job_id) or {}
    if est in ("failed", "skipped", "cancelled", "rejected"):
        _update(ctx, job_id, status=est, **fields)
        return get_job(ctx, job_id) or {}
    if est != "awaiting_commit":
        _update(ctx, job_id, **fields)
        return get_job(ctx, job_id) or {}
    _update(ctx, job_id, status="awaiting_commit", **fields)
    job = get_job(ctx, job_id) or {}
    verification = await _verify(ctx, job, enc)
    if verification["passed"]:
        verification = await _precheck(ctx, job, enc, verification)
    _update(ctx, job_id, core_verification_json=dumps(verification))
    if not verification["passed"]:
        why = failed_checks_text(verification)
        _update(ctx, job_id, status="rejected",
                error=f"Verificatie mislukt, bron behouden. {why}"[:2000])
        try:
            await client.rejected(job["encoder_job_id"], f"Controle in MediaGuard mislukt: {why}")
        except EncoderError as exc:
            LOGGER.warning("encoder rejected-melding mislukt: %s", exc)
        audit(ctx.db, actor="svc:core", action="encoding.verification_failed",
              target_type="encoding_job", target_id=job_id, instance_id=job["instance_id"],
              new_value=[c for c in verification["checks"] if not c["passed"]])
        return get_job(ctx, job_id) or {}
    decision = job["dv_decision"] or {}
    meta = job["decision"] or {}
    pchain, _ = chains(ctx, instance_id=job["instance_id"], file_id=job["provider_id"],
                       series_id=meta.get("series_id"), movie_id=meta.get("movie_id"))
    auto = (not job["trial_only"] and not decision.get("requires_manual_activation")
            and not meta.get("manual_activation")
            and bool(ctx.config.value("encoding.auto_commit_after_verify", pchain))
            and recovery_service.readiness(ctx)["ready"]
            and not ctx.emergency_stopped())
    if auto:
        return await handoff(ctx, job_id, actor="svc:full-auto")
    _update(ctx, job_id, status="awaiting_activation")
    return get_job(ctx, job_id) or {}


async def handoff(ctx: AppContext, job_id: int, *, actor: str,
                  hardlink_break_confirmed: bool = False) -> dict[str, Any]:
    """Na verificatie: recovery-transactie aanmaken (enige schrijver)."""
    job = get_job(ctx, job_id)
    if job is None:
        raise LookupError("Encodeerjob niet gevonden")
    if job["status"] not in ("awaiting_activation", "awaiting_commit"):
        raise GateError("STATE", f"Job staat op '{job['status']}', niet klaar voor activatie")
    verification = job["core_verification"] or {}
    if not verification.get("passed"):
        raise GateError("NOT_VERIFIED", "Verificatie niet geslaagd: activatie geweigerd")
    decision = job["dv_decision"] or {}
    if decision.get("action") == "experimental_separate":
        raise GateError("EXPERIMENTAL", "Experimenteel resultaat: bron wordt nooit vervangen; "
                        "het resultaat blijft apart staan")
    if not decision.get("replace_source_allowed", True):
        raise GateError("NOT_ALLOWED", "DV-beleid staat vervanging niet toe")
    if job.get("trial_only"):
        raise GateError("TRIAL_ONLY", "Dit is een proefkopie: de encoder laat die nooit de bron "
                        "vervangen. Start een gewone encode om te activeren.")
    if ctx.emergency_stopped():
        raise GateError("EMERGENCY_STOP", "Noodstop actief")
    from mediaguard.recovery.release_check import quota_exceeded

    if quota_exceeded(ctx):
        raise GateError("QUARANTINE_FULL", "Quarantaine vol: activatie wacht tot er ruimte is")
    ready = recovery_service.readiness(ctx)
    if not ready["ready"]:
        raise GateError("RECOVERY_NOT_READY", "; ".join(ready["reasons"]))
    # atomisch claimen: poll-loop, /sync en een dubbelklik mogen nooit twee transacties maken
    prev = job["status"]
    claimed = ctx.db.execute(
        "UPDATE encoding_jobs SET status='handing_off', updated_at=? WHERE id=? AND status=?",
        (utcnow(), job_id, prev))
    if not claimed:
        raise GateError("STATE", "Activatie loopt al voor deze job")
    snap = job["source_snapshot"] or {}
    staged = job["staged_output"] or {}
    meta = job["decision"] or {}
    try:
        txn_id, _ = recovery_service.create_transaction(
            ctx, library_path=job["source_path"], staged_path=staged["path"],
            staged_size=staged.get("size"), staged_sha256=staged.get("sha256"),
            source_snapshot=snap, instance_id=job["instance_id"],
            media_kind=job["media_kind"], provider_id=job["provider_id"],
            encoding_job_id=job_id, hardlink_break_confirmed=hardlink_break_confirmed,
            refresh=meta.get("refresh") or {}, actor=actor,
            correlation_id=job["correlation_id"])
    except Exception:
        _update(ctx, job_id, status=prev)
        raise
    _update(ctx, job_id, status="handed_off", transaction_id=txn_id)
    audit(ctx.db, actor=actor, action="encoding.handoff", target_type="encoding_job",
          target_id=job_id, instance_id=job["instance_id"],
          new_value={"transaction_id": txn_id})
    return get_job(ctx, job_id) or {}


# Tijdelijke weigeringen: de geverifieerde encode blijft in staging staan en kan later
# opnieuw geactiveerd worden (nooit uren werk weggooien om een slot of noodstop).
ENV_RETRY_HOURS = 6
TEMPORARY_ABORTS = {"LOCKED", "IMPORT_IN_PROGRESS", "EMERGENCY_STOP", "LIBRARY_LOCKED",
                    "INSUFFICIENT_SPACE", "HARDLINK_CONFIRMATION_REQUIRED", "SNAPSHOT_FAILED",
                    "ENVIRONMENT"}


def _abort_code(ctx: AppContext, txn_id: int) -> str | None:
    row = ctx.db.fetch_one(
        "SELECT detail_json FROM replacement_journal WHERE transaction_id=? AND step='abort' "
        "ORDER BY id DESC LIMIT 1", (txn_id,))
    return (loads(row["detail_json"], {}) or {}).get("code") if row else None


async def _encoder_already(client: Any, encoder_job_id: str, wanted: set[str]) -> bool:
    """Na een verloren antwoord of herstart: staat de encoderjob al in de eindtoestand?"""
    try:
        enc = await client.get_job(encoder_job_id)
    except EncoderError:
        return False
    return str(enc.get("status") or "") in wanted


async def _sync_handoff(ctx: AppContext, job: dict[str, Any]) -> dict[str, Any]:
    txn = ctx.db.fetch_one("SELECT state, error FROM replacement_transactions WHERE id=?",
                           (job["transaction_id"],))
    if txn is None or txn["state"] not in ("committed", "rolled_back", "aborted", "conflict"):
        return job
    client = get_encoder_client(ctx)
    if txn["state"] == "committed":
        try:
            await client.committed(job["encoder_job_id"])
        except EncoderError as exc:
            if not await _encoder_already(client, job["encoder_job_id"], {"committed"}):
                LOGGER.warning("encoder committed-melding mislukt: %s", exc)
                return job  # volgende sync probeert opnieuw
        # rapport 6 / N4: een eerdere 'Activatie uitgesteld'-melding hoort niet te blijven staan
        _update(ctx, job["id"], status="committed", error=None)
    elif txn["state"] == "aborted" and \
            (code := _abort_code(ctx, job["transaction_id"])) in TEMPORARY_ABORTS:
        fields: dict[str, Any] = {}
        if code == "ENVIRONMENT":
            # rechten/ruimte: niet elke 10 min opnieuw (quarantainekopie kost veel I/O)
            meta = dict(job.get("decision") or {})
            meta["retry_after"] = (datetime.now(UTC) + timedelta(hours=ENV_RETRY_HOURS)
                                   ).isoformat(timespec="seconds")
            fields["decision_json"] = dumps(meta)
        retry = (datetime.now(UTC) + (timedelta(hours=ENV_RETRY_HOURS) if code == "ENVIRONMENT"
                                      else timedelta(minutes=AUTO_RETRY_MINUTES)))
        _update(ctx, job["id"], status="awaiting_activation", transaction_id=None,
                error=f"Activatie uitgesteld: {txn['error']} — {_retry_nl(retry)}", **fields)
    elif txn["state"] in ("rolled_back", "aborted"):
        reason = str(txn["error"] or txn["state"])[:1000]
        try:
            await client.rejected(job["encoder_job_id"], reason)
        except EncoderError as exc:
            if not await _encoder_already(client, job["encoder_job_id"],
                                          {"rejected", "cancelled", "failed"}):
                LOGGER.warning("encoder rejected-melding mislukt: %s", exc)
                return job
        _update(ctx, job["id"], status="rejected", error=txn["error"])
    else:
        _update(ctx, job["id"], status="conflict_review", error=txn["error"])
    return get_job(ctx, job["id"]) or {}


AUTO_RETRY_MINUTES = 10


def _retry_nl(when: datetime) -> str:
    """'wordt automatisch opnieuw geprobeerd rond 14:35' (Nederlandse tijd)."""
    from zoneinfo import ZoneInfo

    local = when.astimezone(ZoneInfo("Europe/Amsterdam"))
    return f"wordt automatisch opnieuw geprobeerd rond {local:%H:%M}"


async def activate_waiting(ctx: AppContext) -> int:
    """Set-and-forget: geverifieerde encodes die op 'klaar voor activatie' bleven staan
    (tijdelijk slot, noodstop, bibliotheek nog vergrendeld, herstelpad even niet gereed)
    worden opnieuw geactiveerd zodra dat weer mag. Handmatige proeven (DV-test, expliciete
    proef) blijven op de gebruiker wachten."""
    if ctx.emergency_stopped():
        return 0
    cutoff = (datetime.now(UTC) - timedelta(minutes=AUTO_RETRY_MINUTES)).isoformat(
        timespec="seconds")
    rows = ctx.db.fetch_all("SELECT id FROM encoding_jobs WHERE status='awaiting_activation' "
                            "AND updated_at<=? ORDER BY id LIMIT 20", (cutoff,))
    if not rows or not recovery_service.readiness(ctx)["ready"]:
        return 0
    done = 0
    for r in rows:
        job = get_job(ctx, int(r["id"]))
        if job is None or job["trial_only"]:
            continue
        decision = job["dv_decision"] or {}
        meta = job["decision"] or {}
        if decision.get("requires_manual_activation") or meta.get("manual_activation"):
            continue
        if str(meta.get("retry_after") or "") > utcnow():
            continue
        if not (job.get("core_verification") or {}).get("passed"):
            continue
        pchain, _ = chains(ctx, instance_id=job["instance_id"], file_id=job["provider_id"],
                           series_id=meta.get("series_id"), movie_id=meta.get("movie_id"))
        if not ctx.config.value("encoding.auto_commit_after_verify", pchain):
            continue
        try:
            await handoff(ctx, int(r["id"]), actor="svc:full-auto")
            done += 1
        except GateError as exc:
            # opnieuw proberen na het interval; reden zichtbaar in de UI
            retry = datetime.now(UTC) + timedelta(minutes=AUTO_RETRY_MINUTES)
            _update(ctx, int(r["id"]),
                    error=f"Activatie uitgesteld: {exc.message} — {_retry_nl(retry)}")
    return done


async def sync_active(ctx: AppContext) -> int:
    rows = ctx.db.fetch_all(
        "SELECT id FROM encoding_jobs WHERE status IN ('submitted','queued','running',"
        "'verifying','awaiting_commit','handed_off') ORDER BY id")
    for r in rows:
        try:
            await sync_job(ctx, int(r["id"]))
        except Exception:  # noqa: BLE001 - één job mag de rest niet blokkeren
            LOGGER.exception("sync encodeerjob %s", r["id"])
    return len(rows)


COMMIT_CONFIRMED_KEY = "encoder_commit_confirmed"


async def reconcile_committed(ctx: AppContext, limit: int = 20) -> int:
    """Rapport 6 / N5: een overdracht die in MediaGuard 'committed' is maar waarvan de
    encoder de melding miste (bv. tijdens een herstart), bleef daar 'awaiting_commit'. Dan
    telde de encoder hem nog als 'wacht op activatie' en liepen de besparingscijfers uiteen.
    Elke ronde een paar nalopen; bevestigde jobs worden onthouden."""
    done = {int(x) for x in ctx.state_get(COMMIT_CONFIRMED_KEY, []) or []}
    rows = ctx.db.fetch_all(
        "SELECT id, encoder_job_id FROM encoding_jobs WHERE status='committed' AND "
        "encoder_job_id IS NOT NULL AND updated_at >= datetime('now','-14 days') "
        "ORDER BY id DESC LIMIT 300")
    client = get_encoder_client(ctx)
    fixed = checked = 0
    for r in rows:
        if int(r["id"]) in done:
            continue
        if checked >= limit:
            break
        checked += 1
        try:
            enc = await client.get_job(r["encoder_job_id"])
        except EncoderError:
            continue
        if str(enc.get("status") or "") in ("awaiting_commit", "handed_off"):
            try:
                await client.committed(r["encoder_job_id"])
            except EncoderError:
                continue
            fixed += 1
        done.add(int(r["id"]))
    if checked:
        ctx.state_set(COMMIT_CONFIRMED_KEY, sorted(done)[-3000:])
    return fixed
