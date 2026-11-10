"""Persistente state machine van een migratierun (stappen, checkpoints, rapporten)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from mediaguard.db import dumps, loads, utcnow

if TYPE_CHECKING:
    from mediaguard.context import AppContext

# Volgorde volgens R3 (leidend) + C1: 1 preflight, 2 backup, 3 mapping, 4 shadow_import,
# 5 verify, 6 scan (read-only + reconciliatie), 7 optimization, 8 readiness, 9 cutover,
# 10 post_cutover.
STEPS = ("preflight", "backup", "mapping", "shadow_import", "verify", "scan", "optimization",
         "readiness", "cutover", "post_cutover")
STEP_NL = {"preflight": "Preflight", "backup": "Back-up + testrestore",
           "mapping": "Mappingpreview bron → doel", "shadow_import": "Schaduwimport",
           "verify": "Vergelijkingsrapport", "scan": "Read-only scan & reconciliatie",
           "optimization": "Optimalisatievoorstel (dry-run)",
           "readiness": "Cutover-gereedheid", "cutover": "Gecontroleerde cutover",
           "post_cutover": "Post-cutover audit"}
DONE = ("succeeded", "warning", "skipped")


def run_row(ctx: AppContext, run_id: int) -> dict[str, Any] | None:
    row = ctx.db.fetch_one("SELECT * FROM migration_runs WHERE id=?", (run_id,))
    return dict(row) if row else None


def run_options(ctx: AppContext, run_id: int) -> dict[str, Any]:
    row = run_row(ctx, run_id)
    return loads(row["options_json"], {}) if row else {}


def accepted_ids(ctx: AppContext, run_id: int, target: str) -> set[str]:
    row = run_row(ctx, run_id)
    data = loads(row["accepted_json"], {}) if row else {}
    return set((data.get(target) or {}).keys())


def add_accepted(ctx: AppContext, run_id: int, target: str, items: dict[str, str],
                 by: str = "svc:migration",
                 reason: str = "Bestaat niet in de nieuwe versie") -> None:
    """Verschillen automatisch accepteren (bijv. instellingen die het doel niet kent)."""
    row = run_row(ctx, run_id)
    if row is None or not items:
        return
    data = loads(row["accepted_json"], {})
    bucket = data.setdefault(target, {})
    for mid, message in items.items():
        bucket.setdefault(mid, {"by": by, "at": utcnow(), "reason": reason,
                                "message_nl": message, "auto": True})
    ctx.db.execute("UPDATE migration_runs SET accepted_json=?, updated_at=? WHERE id=?",
                   (dumps(data), utcnow(), run_id))


def create_run(ctx: AppContext, name: str, source_ids: list[str], options: dict[str, Any],
               actor: str) -> int:
    now = utcnow()
    with ctx.db.transaction() as conn:
        cur = conn.execute("INSERT INTO migration_runs(name,status,options_json,created_at,"
                           "created_by,updated_at) VALUES (?,?,?,?,?,?)",
                           (name, "active", dumps(options), now, actor, now))
        run_id = int(cur.lastrowid or 0)
        for sid in source_ids:
            conn.execute("INSERT INTO migration_mappings(run_id,source_id,updated_at) "
                         "VALUES (?,?,?)", (run_id, sid, now))
        for step in STEPS:
            conn.execute("INSERT INTO migration_steps(run_id,step,scope,status,updated_at) "
                         "VALUES (?,?,?,?,?)", (run_id, step, "", "pending", now))
    return run_id


def step_get(ctx: AppContext, run_id: int, step: str, scope: str = "") -> dict[str, Any] | None:
    row = ctx.db.fetch_one("SELECT * FROM migration_steps WHERE run_id=? AND step=? AND scope=?",
                           (run_id, step, scope))
    if row is None:
        return None
    out = dict(row)
    out["result"] = loads(out.pop("result_json"), {})
    return out


def step_set(ctx: AppContext, run_id: int, step: str, scope: str, status: str, *,
             result: dict[str, Any] | None = None, error: str | None = None,
             job_id: int | None = None) -> None:
    now = utcnow()
    started = now if status == "running" else None
    finished = now if status in (*DONE, "failed", "blocked") else None
    ctx.db.execute(
        "INSERT INTO migration_steps(run_id,step,scope,status,job_id,attempts,result_json,error,"
        "started_at,finished_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(run_id,step,scope) DO UPDATE SET status=excluded.status, "
        "job_id=COALESCE(excluded.job_id, migration_steps.job_id), "
        "attempts=migration_steps.attempts + excluded.attempts, "
        "result_json=CASE WHEN excluded.result_json='{}' THEN migration_steps.result_json "
        "ELSE excluded.result_json END, error=excluded.error, "
        "started_at=COALESCE(excluded.started_at, migration_steps.started_at), "
        "finished_at=excluded.finished_at, updated_at=excluded.updated_at",
        (run_id, step, scope, status, job_id, 1 if status == "running" else 0,
         dumps(result or {}), error, started, finished, now))
    # run-niveau stap volgt de per-scope stappen (samenvatting voor de stepper)
    if scope:
        rows = ctx.db.fetch_all("SELECT status FROM migration_steps WHERE run_id=? AND step=? "
                                "AND scope<>''", (run_id, step))
        statuses = {r["status"] for r in rows}
        if "running" in statuses or "queued" in statuses:
            agg = "running"
        elif "failed" in statuses:
            agg = "failed"
        elif "blocked" in statuses:
            agg = "blocked"
        elif statuses <= {"succeeded", "skipped"}:
            agg = "succeeded"
        elif statuses <= set(DONE):
            agg = "warning"
        else:
            agg = "pending"
        ctx.db.execute("UPDATE migration_steps SET status=?, updated_at=? WHERE run_id=? AND "
                       "step=? AND scope=''", (agg, now, run_id, step))
    ctx.db.execute("UPDATE migration_runs SET updated_at=? WHERE id=?", (now, run_id))
    ctx.events.publish("migration.step", {"run_id": run_id, "step": step, "scope": scope,
                                          "status": status},
                       instance_id=scope if scope and not scope.startswith("source:") else None,
                       persist=False)


def checkpoint_get(ctx: AppContext, run_id: int, instance_id: str, phase: str, kind: str,
                   source_key: str) -> dict[str, Any] | None:
    row = ctx.db.fetch_one(
        "SELECT * FROM instance_migration_checkpoints WHERE run_id=? AND instance_id=? AND "
        "phase=? AND kind=? AND source_key=?", (run_id, instance_id, phase, kind, source_key))
    if row is None:
        return None
    out = dict(row)
    out["data"] = loads(out.pop("data_json"), {})
    return out


def checkpoint_map(ctx: AppContext, run_id: int, instance_id: str, phase: str,
                   kind: str) -> dict[str, str]:
    rows = ctx.db.fetch_all(
        "SELECT source_key, target_ref FROM instance_migration_checkpoints WHERE run_id=? AND "
        "instance_id=? AND phase=? AND kind=?", (run_id, instance_id, phase, kind))
    return {r["source_key"]: r["target_ref"] for r in rows}


def checkpoint_set(ctx: AppContext, run_id: int, instance_id: str, phase: str, kind: str,
                   source_key: str, target_ref: Any = None,
                   data: dict[str, Any] | None = None) -> None:
    ctx.db.execute(
        "INSERT INTO instance_migration_checkpoints(run_id,instance_id,phase,kind,source_key,"
        "target_ref,data_json,updated_at) VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(run_id,"
        "instance_id,phase,kind,source_key) DO UPDATE SET target_ref=excluded.target_ref, "
        "data_json=excluded.data_json, updated_at=excluded.updated_at",
        (run_id, instance_id, phase, kind, str(source_key),
         None if target_ref is None else str(target_ref), dumps(data or {}), utcnow()))


def add_report(ctx: AppContext, run_id: int, kind: str, verdict: str, *,
               source_id: str | None = None, target: str | None = None,
               summary: dict[str, Any] | None = None,
               mismatches: list[dict[str, Any]] | None = None, actor: str | None = None) -> int:
    return ctx.db.execute_insert(
        "INSERT INTO migration_reports(run_id,kind,source_id,target_instance_id,verdict,"
        "summary_json,mismatches_json,created_at,created_by) VALUES (?,?,?,?,?,?,?,?,?)",
        (run_id, kind, source_id, target, verdict, dumps(summary or {}), dumps(mismatches or []),
         utcnow(), actor))


def report_view(row: Any, ctx: AppContext | None = None) -> dict[str, Any]:
    out = dict(row)
    out["summary"] = loads(out.pop("summary_json"), {})
    out["mismatches"] = loads(out.pop("mismatches_json"), [])
    accepted: set[str] = set(out["summary"].get("accepted") or [])
    if ctx is not None and out.get("target_instance_id"):
        accepted |= accepted_ids(ctx, int(out["run_id"]), str(out["target_instance_id"]))
    # 'Geaccepteerde afwijkingen' apart: een al geaccepteerde NO-GO staat niet meer tussen
    # de open punten (rapport 4 nov, H13)
    out["open_mismatches"] = [m for m in out["mismatches"]
                              if not (m.get("acceptable") and m.get("id") in accepted)]
    out["accepted_mismatches"] = [m for m in out["mismatches"]
                                  if m.get("acceptable") and m.get("id") in accepted]
    return out


def latest_report(ctx: AppContext, run_id: int, kind: str,
                  target: str | None = None) -> dict[str, Any] | None:
    if target is None:
        row = ctx.db.fetch_one("SELECT * FROM migration_reports WHERE run_id=? AND kind=? "
                               "ORDER BY id DESC LIMIT 1", (run_id, kind))
    else:
        row = ctx.db.fetch_one("SELECT * FROM migration_reports WHERE run_id=? AND kind=? AND "
                               "target_instance_id=? ORDER BY id DESC LIMIT 1",
                               (run_id, kind, target))
    return report_view(row) if row else None


def mappings(ctx: AppContext, run_id: int, *, confirmed_only: bool = False) -> list[dict]:
    sql = "SELECT * FROM migration_mappings WHERE run_id=?"
    if confirmed_only:
        sql += " AND status='confirmed'"
    out = []
    for row in ctx.db.fetch_all(sql + " ORDER BY source_id", (run_id,)):
        m = dict(row)
        for key in ("reasons", "warnings", "path_map"):
            m[key] = loads(m.pop(f"{key}_json"), [])
        out.append(m)
    return out


def mapping_for_target(ctx: AppContext, run_id: int, target: str) -> dict[str, Any]:
    for m in mappings(ctx, run_id, confirmed_only=True):
        if m["target_instance_id"] == target:
            return m
    raise ValueError(f"Geen bevestigde mapping naar '{target}' in run {run_id}")


def shadow_state(ctx: AppContext, run_id: int, target: str) -> dict[str, Any] | None:
    row = ctx.db.fetch_one("SELECT * FROM migration_shadow_states WHERE run_id=? AND "
                           "target_instance_id=?", (run_id, target))
    if row is None:
        return None
    out = dict(row)
    out["state"] = loads(out.pop("state_json"), {})
    return out


def save_shadow_state(ctx: AppContext, run_id: int, target: str, source_id: str, mode: str,
                      state: dict[str, Any]) -> None:
    """Bewaart de ORIGINELE standen; een herhaalde import overschrijft ze niet met de
    (al uitgeschakelde) doelstanden."""
    existing = shadow_state(ctx, run_id, target)
    if existing is not None and existing["source_id"] == source_id:
        merged = existing["state"]
        for section, values in state.items():
            if isinstance(values, dict):
                merged.setdefault(section, {})
                for k, v in values.items():
                    merged[section].setdefault(k, v)
            else:
                merged[section] = values
        state = merged
    ctx.db.execute(
        "INSERT INTO migration_shadow_states(run_id,target_instance_id,source_id,mode,state_json,"
        "applied_at) VALUES (?,?,?,?,?,?) ON CONFLICT(run_id,target_instance_id) DO UPDATE SET "
        "source_id=excluded.source_id, mode=excluded.mode, state_json=excluded.state_json, "
        "lifted_at=NULL", (run_id, target, source_id, mode, dumps(state), utcnow()))


def run_status(ctx: AppContext, run_id: int) -> dict[str, Any] | None:
    row = run_row(ctx, run_id)
    if row is None:
        return None
    steps = [dict(r) for r in ctx.db.fetch_all(
        "SELECT * FROM migration_steps WHERE run_id=? ORDER BY scope, step", (run_id,))]
    for s in steps:
        s["result"] = loads(s.pop("result_json"), {})
    order = {s: i for i, s in enumerate(STEPS)}
    steps.sort(key=lambda s: (order.get(s["step"], 99), s["scope"]))
    reports = [report_view(r) for r in ctx.db.fetch_all(
        "SELECT * FROM migration_reports WHERE run_id=? AND id IN (SELECT MAX(id) FROM "
        "migration_reports WHERE run_id=? GROUP BY kind, COALESCE(target_instance_id,''), "
        "COALESCE(source_id,''))", (run_id, run_id))]
    for r in reports:
        r["mismatch_count"] = len(r["mismatches"])
        r["mismatches"] = r["mismatches"][:200]
    cutovers = [dict(r) for r in ctx.db.fetch_all(
        "SELECT id,source_id,target_instance_id,kind,phase,status,confirmed_by,error,started_at,"
        "finished_at,rolled_back_at FROM migration_cutovers WHERE run_id=? ORDER BY id",
        (run_id,))]
    # rapport 2 / N12: 'Overschakelen' (en de nazorg) alleen afgevinkt als de overstap voor
    # élk doel echt is gedaan; anders volgt de stap de werkelijke cutovers
    targets = [m["target_instance_id"] for m in mappings(ctx, run_id)
               if m.get("target_instance_id") and m.get("status") != "excluded"]
    latest = {}
    for c in cutovers:
        latest[c["target_instance_id"]] = c
    all_done = bool(targets) and all(
        (latest.get(t) or {}).get("status") == "completed" for t in targets)
    any_running = any((latest.get(t) or {}).get("status") == "running" for t in targets)
    for st in steps:
        if st["step"] == "cutover" and st["scope"] == "":
            st["status"] = "succeeded" if all_done else ("running" if any_running
                                                         else "pending")
        elif st["step"] == "post_cutover" and st["scope"] == "" and not all_done:
            st["status"] = "pending"
    run_steps = [s for s in steps if s["scope"] == ""]
    current = next((s["step"] for s in run_steps if s["status"] not in DONE), None)
    return {"id": row["id"], "name": row["name"], "status": row["status"],
            "options": loads(row["options_json"], {}),
            "accepted_mismatches": loads(row["accepted_json"], {}),
            "created_at": row["created_at"], "created_by": row["created_by"],
            "updated_at": row["updated_at"], "current_step": current,
            "steps": [{**s, "label_nl": STEP_NL.get(s["step"], s["step"])} for s in steps],
            "mappings": mappings(ctx, run_id),
            "mapping_items": ctx.db.fetch_dicts(
                "SELECT * FROM migration_mapping_items WHERE run_id=? ORDER BY source_id, kind, "
                "external_id LIMIT 500", (run_id,)),
            "reports": reports, "cutovers": cutovers}
