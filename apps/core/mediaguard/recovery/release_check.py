"""Vrijgavecontrole: een origineel in quarantaine mag pas worden opgeruimd nadat het
ACTIEVE nieuwe bestand opnieuw uitgebreid is gecontroleerd tegen dat origineel.

Waarom apart van de verificatie vóór activatie: die controleerde het staged bestand.
Deze controle kijkt naar wat er nu echt in de bibliotheek staat (ongewijzigd sinds
activatie? volledig decodeerbaar? zelfde streams/talen/HDR/DV als het origineel?) vlak
voordat de laatste herstelbare versie verdwijnt. Faalt iets, dan blijft het origineel
staan en krijgt de gebruiker een melding; opruimen gebeurt dan nooit automatisch.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import subprocess
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mediaguard.db import dumps, loads, utcnow
from mediaguard.recovery.fsops import sha256_file

if TYPE_CHECKING:
    from mediaguard.context import AppContext

HDR_TRANSFERS = {"smpte2084", "arib-std-b67"}

LANG_NL = {
    "nld": "Nederlands", "dut": "Nederlands", "eng": "Engels", "jpn": "Japans",
    "ger": "Duits", "deu": "Duits", "fre": "Frans", "fra": "Frans", "spa": "Spaans",
    "ita": "Italiaans", "por": "Portugees", "pol": "Pools", "rus": "Russisch",
    "chi": "Chinees", "zho": "Chinees", "kor": "Koreaans", "ara": "Arabisch",
    "fil": "Filipijns", "tur": "Turks", "swe": "Zweeds", "nor": "Noors", "nob": "Noors",
    "dan": "Deens", "fin": "Fins", "hun": "Hongaars", "cze": "Tsjechisch", "ces": "Tsjechisch",
    "gre": "Grieks", "ell": "Grieks", "heb": "Hebreeuws", "hin": "Hindi", "tha": "Thai",
    "vie": "Vietnamees", "ind": "Indonesisch", "may": "Maleis", "msa": "Maleis",
    "ukr": "Oekraïens", "rum": "Roemeens", "ron": "Roemeens", "und": "onbekende taal",
}
CONTENT_CHECKS = {"duur", "videostream", "resolutie", "hdr-metadata", "dolby-vision",
                  "audiosporen", "ondertitels", "hoofdstukken", "volledige-decode"}


def languages_nl(counter: Counter[str] | dict[str, int]) -> str:
    """{'ger': 2, 'ara': 1} → 'Arabisch, Duits (2)'."""
    parts = []
    for code, n in sorted(dict(counter).items(), key=lambda kv: LANG_NL.get(kv[0], kv[0])):
        name = LANG_NL.get(str(code).lower(), str(code))
        parts.append(f"{name} ({n})" if n > 1 else name)
    return ", ".join(parts)


def _ffprobe(path: Path) -> dict[str, Any]:
    out = subprocess.run(  # noqa: S603 - argumentlijst, geen shell
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams",
         "-show_chapters", "--", str(path)],
        capture_output=True, text=True, timeout=300, check=False,
    )
    if out.returncode != 0:
        raise RuntimeError(f"ffprobe faalde: {out.stderr.strip()[:300]}")
    data: dict[str, Any] = json.loads(out.stdout or "{}")
    return data


def _full_decode(path: Path, timeout_s: int) -> tuple[bool, str]:
    """Decodeert alle video/audio volledig; elke decodeerfout = afkeur."""
    try:
        out = subprocess.run(  # noqa: S603
            ["nice", "-n", "15", "ffmpeg", "-nostdin", "-v", "error", "-xerror", "-i",
             str(path), "-map", "0:v?", "-map", "0:a?", "-f", "null", "-"],
            capture_output=True, text=True, timeout=timeout_s, check=False,
        )
    except subprocess.TimeoutExpired:
        return False, f"volledige decode duurde langer dan {timeout_s // 60} min"
    if out.returncode != 0 or out.stderr.strip():
        return False, (out.stderr.strip() or f"ffmpeg exit {out.returncode}")[:300]
    return True, "volledig gedecodeerd zonder fouten"


def _streams(probe: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    return [s for s in probe.get("streams", []) if s.get("codec_type") == kind
            and not (kind == "video" and s.get("disposition", {}).get("attached_pic"))]


def _langs(streams: list[dict[str, Any]]) -> Counter[str]:
    return Counter((s.get("tags", {}) or {}).get("language", "und") for s in streams)


def _duration(probe: dict[str, Any]) -> float:
    try:
        return float(probe.get("format", {}).get("duration") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _has_dovi(stream: dict[str, Any]) -> bool:
    return any("DOVI" in str(sd.get("side_data_type", "")).upper()
               or "dolby vision" in str(sd.get("side_data_type", "")).lower()
               for sd in stream.get("side_data_list", []) or [])


def _required(lo: Counter[str], kind_policy: dict[str, Any] | None) -> Counter[str]:
    """Wat er volgens het beleid mee moest: alles (standaard) of alleen de gekozen talen."""
    pol = kind_policy or {}
    if pol.get("keep_all", True):
        return lo
    keep = {str(x).lower() for x in pol.get("keep_languages") or []}
    return Counter({k: v for k, v in lo.items() if k.lower() in keep}) if keep else lo


def compare(original: dict[str, Any], active: dict[str, Any],
            policy: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Vergelijkt probe van origineel met het (te activeren of actieve) AV1-bestand.

    ``policy`` is het per-job beleid van MediaGuard: alleen sporen die je zelf hebt laten
    weghalen (keep_all uit + talenlijst) mogen ontbreken; standaard moet alles mee."""
    checks: list[dict[str, Any]] = []
    policy = policy or {}

    def add(name: str, passed: bool, detail: str) -> None:
        checks.append({"name": name, "passed": passed, "detail": detail})

    d_o, d_a = _duration(original), _duration(active)
    tol = max(1.0, d_o * 0.005)
    add("duur", d_o > 0 and abs(d_o - d_a) <= tol,
        f"origineel {d_o:.1f}s, AV1 {d_a:.1f}s (marge {tol:.1f}s)")
    vo, va = _streams(original, "video"), _streams(active, "video")
    add("videostream", bool(va), f"{len(va)} videostream(s) in AV1-bestand")
    if vo and va:
        same_res = (vo[0].get("width"), vo[0].get("height")) == \
            (va[0].get("width"), va[0].get("height"))
        add("resolutie", same_res, f"{vo[0].get('width')}x{vo[0].get('height')} → "
                                   f"{va[0].get('width')}x{va[0].get('height')}")
        if vo[0].get("color_transfer") in HDR_TRANSFERS:
            same_hdr = (vo[0].get("color_transfer"), vo[0].get("color_primaries")) == \
                (va[0].get("color_transfer"), va[0].get("color_primaries"))
            add("hdr-metadata", same_hdr, "HDR gelijk" if same_hdr else
                f"HDR verschilt: {vo[0].get('color_transfer')}/{vo[0].get('color_primaries')}"
                f" → {va[0].get('color_transfer')}/{va[0].get('color_primaries')}")
        if _has_dovi(vo[0]):
            add("dolby-vision", _has_dovi(va[0]),
                "Dolby Vision-configuratie aanwezig" if _has_dovi(va[0])
                else "origineel had Dolby Vision, AV1-bestand niet")
    for kind, label, key, word in (("audio", "audiosporen", "audio_policy", "Audiosporen"),
                                   ("subtitle", "ondertitels", "subtitle_policy",
                                    "Ondertitels")):
        lo, la = _langs(_streams(original, kind)), _langs(_streams(active, kind))
        missing = _required(lo, policy.get(key)) - la
        add(label, not missing, "alle sporen/talen aanwezig" if not missing
            else f"{word} ontbreken: {languages_nl(missing)}")
    co, ca = len(original.get("chapters", [])), len(active.get("chapters", []))
    add("hoofdstukken", ca >= co, f"{co} → {ca}")
    return checks


def summary_nl(checks: list[dict[str, Any]]) -> str:
    """Eén leesbare zin met wat er mis is."""
    from mediaguard.textfmt import plain_nl

    failed = [c for c in checks if not c.get("passed")]
    return plain_nl("; ".join(str(c.get("detail") or c.get("name")) for c in failed))[:500]


def job_policy(ctx: AppContext, encoding_job_id: Any) -> dict[str, Any]:
    if not encoding_job_id:
        return {}
    row = ctx.db.fetch_one("SELECT policy_json FROM encoding_jobs WHERE id=?",
                           (int(encoding_job_id),))
    return loads(row["policy_json"], {}) if row else {}


def precheck(source: Path, staged: Path, policy: dict[str, Any]) -> list[dict[str, Any]]:
    """Dezelfde controle als de vrijgavecontrole, maar VÓÓR het vervangen (staged tegen
    het bronbestand in de bibliotheek). Synchroon; via asyncio.to_thread aanroepen."""
    try:
        src = _ffprobe(source)
    except (RuntimeError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return []  # bron zelf niet te lezen: de encoder-verificatie is dan leidend
    return compare(src, _ffprobe(staged), policy)


def wrong_file(txn: dict[str, Any], item: dict[str, Any]) -> bool:
    """Verkeerd bestand dat MediaGuard in quarantaine zette (``delivery/replace.py``): geen
    staged nieuw bestand en geen AV1-baan."""
    return txn.get("kind") == "replace" and not txn.get("staged_path") and \
        txn.get("encoding_job_id") is None and \
        str(item.get("reason") or "").startswith("Verkeerd bestand")


def run_check(ctx: AppContext, item_id: int) -> dict[str, Any]:
    """Synchroon (draait via asyncio.to_thread in de recovery-rol)."""
    item = ctx.db.fetch_one("SELECT * FROM quarantine_items WHERE id=?", (item_id,))
    if item is None:
        raise LookupError("Quarantaine-item niet gevonden")
    if item["status"] not in ("restorable", "held", "pending"):
        # rapport 6 / N10: een al opgeruimd/teruggezet item niet meer 'controleren' (anders
        # stond er 'Opgeruimd' naast 'vrijgavecontrole mislukt: ffprobe … No such file')
        prev = check_status(ctx, item_id)
        return {"item_id": item_id, "status": prev["status"] if prev else "skipped",
                "checks": prev["checks"] if prev else [], "skipped": True}
    txn = ctx.db.fetch_one("SELECT * FROM replacement_transactions WHERE id=?",
                           (item["transaction_id"],))
    checks: list[dict[str, Any]] = []
    if txn is None or txn["state"] != "committed":
        checks.append({"name": "transactie", "passed": False,
                       "detail": "transactie niet afgerond"})
    elif wrong_file(dict(txn), dict(item)):
        # verbeterlijst 12: een aantoonbaar verkeerd bestand heeft geen 'nieuwe versie' om
        # tegen te vergelijken (de juiste release komt later via een gewone import). Alleen
        # nagaan dat het bewaarde bestand er nog heel staat; anders blijft het eeuwig staan.
        original = Path(item["quarantine_path"])
        if not original.is_file():
            checks.append({"name": "origineel", "passed": False,
                           "detail": "origineel in quarantaine ontbreekt"})
            _mark_vanished(ctx, dict(item))
        else:
            same = int(original.stat().st_size) == int(item["size"] or 0)
            checks.append({"name": "verkeerd-bestand", "passed": same,
                           "detail": "verkeerd bestand staat heel in quarantaine" if same
                           else "bestand in quarantaine heeft een andere grootte"})
    else:
        active = Path(txn["final_path"])
        original = Path(item["quarantine_path"])
        if not active.is_file():
            checks.append({"name": "actief-bestand", "passed": False,
                           "detail": "nieuw bestand staat niet (meer) op zijn plek"})
        elif not original.is_file():
            checks.append({"name": "origineel", "passed": False,
                           "detail": "origineel in quarantaine ontbreekt"})
            _mark_vanished(ctx, dict(item))
        else:
            if txn["staged_sha256"]:
                same = sha256_file(active) == txn["staged_sha256"]
                checks.append({"name": "ongewijzigd-sinds-activatie", "passed": same,
                               "detail": "checksum gelijk aan geactiveerde versie" if same
                               else "bestand is na activatie gewijzigd"})
            try:
                checks.extend(compare(_ffprobe(original), _ffprobe(active),
                                      job_policy(ctx, txn["encoding_job_id"])))
            except (RuntimeError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
                gone = not original.is_file()
                checks.append({"name": "probe", "passed": False,
                               "detail": "origineel verdween tijdens de controle" if gone
                               else str(exc)[:300]})
                if gone:
                    _mark_vanished(ctx, dict(item))
            if ctx.config.value("quarantine.release_check_full_decode") and \
                    all(c["passed"] for c in checks):
                timeout = int(ctx.config.value("quarantine.release_check_timeout_minutes")) * 60
                ok, detail = _full_decode(active, timeout)
                checks.append({"name": "volledige-decode", "passed": ok, "detail": detail})
    passed = bool(checks) and all(c["passed"] for c in checks)
    status = "passed" if passed else "failed"
    now_row = ctx.db.fetch_one("SELECT status FROM quarantine_items WHERE id=?", (item_id,))
    if now_row is not None and now_row["status"] == "purged" and \
            not any(c["name"] in ("origineel", "probe") for c in checks if not c["passed"]):
        # tijdens de controle door een opruimronde afgehandeld: uitkomst niet overschrijven
        return {"item_id": item_id, "status": status, "checks": checks, "skipped": True}
    ctx.db.execute(
        "INSERT INTO quarantine_release_checks(item_id,status,checks_json,checked_at) "
        "VALUES (?,?,?,?) ON CONFLICT(item_id) DO UPDATE SET status=excluded.status, "
        "checks_json=excluded.checks_json, checked_at=excluded.checked_at, "
        "attempts=quarantine_release_checks.attempts+1",
        (item_id, status, dumps(checks), utcnow()))
    return {"item_id": item_id, "status": status, "checks": checks}


def _mark_vanished(ctx: AppContext, item: dict[str, Any]) -> None:
    """Het quarantainebestand is weg zonder dat MediaGuard het opruimde (bv. extern
    verwijderd). Eerlijk melden als fout — niet als 'Opgeruimd' (rapport 6, N10)."""
    from mediaguard.audit import audit

    purged_by_us = ctx.db.fetch_one(
        "SELECT 1 FROM audit_events WHERE action='quarantine.purged' AND target_type='quarantine' "
        "AND target_id=? LIMIT 1", (str(item["id"]),))
    if purged_by_us is not None:
        return
    reason = "Origineel verdwenen buiten MediaGuard om"
    changed = ctx.db.execute(
        "UPDATE quarantine_items SET status='purged', purged_at=?, reason=? WHERE id=? AND "
        "status IN ('restorable','held','pending')", (utcnow(), reason, item["id"]))
    if not changed:
        return
    audit(ctx.db, actor="svc:recovery", action="quarantine.vanished", target_type="quarantine",
          target_id=item["id"], instance_id=item.get("instance_id"), reason=reason,
          old_value={"path": item.get("quarantine_path")})
    try:
        from mediaguard.api.system import notify

        notify(ctx, "error", f"{Path(str(item['original_path'])).name}: origineel verdwenen "
               "buiten MediaGuard om", "Het bewaarde origineel staat niet meer in de "
               "quarantaine. MediaGuard heeft het niet verwijderd; terugzetten kan niet meer.",
               link=f"/quarantaine?item={item['id']}", dedupe_key="quarantine.vanished")
    except Exception:  # noqa: BLE001 - melding is extra
        pass


def check_status(ctx: AppContext, item_id: int) -> dict[str, Any] | None:
    row = ctx.db.fetch_one("SELECT * FROM quarantine_release_checks WHERE item_id=?",
                           (item_id,))
    if row is None:
        return None
    data = dict(row)
    data["checks"] = loads(data.pop("checks_json"), [])
    return data


def quarantine_usage_bytes(ctx: AppContext) -> int:
    """Ruimte die herstelbare originelen innemen (vastgezet telt mee: neemt ook ruimte)."""
    row = ctx.db.fetch_one(
        "SELECT COALESCE(SUM(size),0) AS n FROM quarantine_items "
        "WHERE status IN ('restorable','held','pending')")
    return int(row["n"]) if row else 0


def quota_bytes(ctx: AppContext) -> int:
    return int(ctx.config.value("quarantine.max_size_gb")) * 1024**3


def quota_exceeded(ctx: AppContext) -> bool:
    """Alleen 'vol' (AV1-wachtrij pauzeert) als opruimen geen ruimte kan maken.

    Rapport 7 / A1: de wachtrij pauzeerde bij elk groot origineel even op 'quarantaine vol',
    ook als er genoeg gecontroleerde originelen klaarstonden om op te ruimen. Is er wél iets
    op te ruimen, dan plannen we die opruiming en laten de wachtrij doorlopen."""
    from mediaguard.recovery.service import effective_cap_bytes, purge_candidates
    from mediaguard.storage_forecast import pool_degraded

    used, cap = quarantine_usage_bytes(ctx), effective_cap_bytes(ctx)
    if used < cap:
        return False
    # verbeterlijst 37: zolang een TrueNAS-pool niet gezond is, wordt niets opgeruimd. Dan kan
    # opruimen ook geen ruimte maken: de AV1-wachtrij pauzeert in plaats van boven het
    # maximum door te gaan.
    candidates = [] if pool_degraded(ctx) else purge_candidates(ctx)
    freeable = sum(int(c["size"] or 0) for c in candidates)
    if used - freeable < cap:
        request_purge(ctx)
        return False
    return True


def request_purge(ctx: AppContext) -> None:
    """Opruimronde inplannen (hooguit één per 10 minuten)."""
    from mediaguard import jobs

    if not ctx.config.value("quarantine.auto_purge"):
        return
    slot = utcnow()[:15]  # per 10 minuten
    # opruimen inplannen mag nooit iets blokkeren
    with contextlib.suppress(Exception):
        jobs.enqueue(ctx.db, "quarantine.purge", {}, priority=205,
                     idempotency_key=f"quarantine.purge:room:{slot}", max_attempts=1)


async def run_check_async(ctx: AppContext, item_id: int) -> dict[str, Any]:
    return await asyncio.to_thread(run_check, ctx, item_id)


def due_for_check(ctx: AppContext) -> list[int]:
    """Herstelbare items zonder geslaagde controle; een gefaalde controle wordt na 24 uur
    opnieuw geprobeerd (bv. na een tijdelijke I/O-fout), de eerste 5 keer dagelijks en daarna
    wekelijks — nooit voorgoed stil (staat ook in 'Wacht op jou')."""
    rows = ctx.db.fetch_all(
        "SELECT q.id FROM quarantine_items q LEFT JOIN quarantine_release_checks c "
        "ON c.item_id=q.id WHERE q.status='restorable' AND (c.item_id IS NULL OR "
        "(c.status='failed' AND ((c.attempts<5 AND c.checked_at < datetime('now','-1 day')) "
        "OR c.checked_at < datetime('now','-7 days'))))"
        " ORDER BY q.id")
    return [int(r["id"]) for r in rows]


_ = os  # os blijft beschikbaar voor toekomstige stat-controles
