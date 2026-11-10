"""Fault-injection driver. Runs INSIDE the real recovery container (piped in via
`docker exec -i <recovery> python - <cmd> ...`), as a separate recovery process that shares the
container's DB, library and quarantine mounts.

  create LIB STAGED [--job]          -> {"txn": id}   (without --job: no recovery job is queued,
                                                         so the container's own worker does not race us)
  run TXN [--kill-at POINT] [--fill DIR:LEAVE_BYTES]
        executes the replacement transaction with B2's fault hook. --kill-at sends SIGKILL to
        THIS process at that point (a real kill -9: no cleanup, no finally blocks, no flush beyond
        what was already fsynced). --fill writes a filler file into DIR at the 'validate' point so
        that only LEAVE_BYTES remain free (disk fills up after the free-space pre-check).
  inspect TXN                        -> transaction, journal, on-disk state as JSON
  setting KEY JSON                   -> set a global setting (e.g. recovery.min_free_gb 0)
"""
import asyncio
import hashlib
import json
import os
import shutil
import signal
import sys
from pathlib import Path

from mediaguard import jobs
from mediaguard.app import create_app
from mediaguard.config import load_settings
from mediaguard.recovery import executor, service


def ctx():
    return create_app(load_settings(start_background=False)).state.ctx


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def cmd_create(c, lib, staged, *flags):
    st = os.stat(lib)
    snap = {"path": lib, "size": st.st_size, "mtime_ns": st.st_mtime_ns, "inode": st.st_ino,
            "device": st.st_dev, "nlink": st.st_nlink}
    if "--job" not in flags:
        jobs.enqueue = lambda *a, **k: 0  # type: ignore[assignment]
        service.jobs.enqueue = jobs.enqueue  # type: ignore[attr-defined]
    txn, job = service.create_transaction(
        c, library_path=lib, staged_path=staged, source_snapshot=snap,
        staged_size=os.path.getsize(staged), staged_sha256=sha(staged),
        instance_id=None, media_kind="episode_file", provider_id=hashlib.md5(lib.encode()).hexdigest()[:8],
        actor="fault-injection")
    print(json.dumps({"txn": txn, "job": job}))


def cmd_run(c, txn, *flags):
    kill_at = flags[flags.index("--kill-at") + 1] if "--kill-at" in flags else None
    fill = flags[flags.index("--fill") + 1] if "--fill" in flags else None

    def hook(point):
        print(json.dumps({"point": point}), flush=True)
        if fill and point == "validate":
            d, leave = fill.rsplit(":", 1)
            free = shutil.disk_usage(d).free - int(leave)
            with open(os.path.join(d, ".filler"), "wb") as f:
                while free > 0:
                    n = min(free, 1 << 20)
                    f.write(b"\0" * n)
                    free -= n
        if point == kill_at:
            os.kill(os.getpid(), signal.SIGKILL)

    result = asyncio.run(executor.run_transaction(c, int(txn), fault=hook))
    print(json.dumps({"state": result["state"], "error": result["error"]}))


def cmd_inspect(c, txn):
    t = executor.load(c, int(txn))
    journal = [dict(r) for r in c.db.fetch_all(
        "SELECT step, phase, detail_json FROM replacement_journal WHERE transaction_id=? ORDER BY id",
        (int(txn),))]
    items = [dict(r) for r in c.db.fetch_all("SELECT * FROM quarantine_items WHERE transaction_id=?",
                                             (int(txn),))]
    files = {}
    for key in ("library_path", "final_path", "quarantine_path", "temp_path", "staged_path"):
        p = t.get(key)
        if p:
            files[key] = {"path": p, "exists": os.path.exists(p),
                          "sha256": sha(p) if os.path.isfile(p) else None,
                          "inode": os.stat(p).st_ino if os.path.exists(p) else None}
    leftovers = []
    for root in ("/media/tv", "/media/small"):
        for p in Path(root).rglob(".mg-*"):
            leftovers.append(str(p))
    locks = [dict(r) for r in c.db.fetch_all("SELECT * FROM locks")] \
        if c.db.fetch_one("SELECT name FROM sqlite_master WHERE name='locks'") else None
    print(json.dumps({"txn": {k: t[k] for k in ("id", "state", "error", "quarantine_method",
                                                "original_sha256", "quarantine_item_id")},
                      "journal": journal, "quarantine_items": items, "files": files,
                      "leftovers": leftovers, "locks": locks}, default=str))


def cmd_setting(c, key, value):
    c.config.set("global", "", key, json.loads(value), "fault-injection")
    print(json.dumps({key: c.config.value(key)}))


if __name__ == "__main__":
    name, *args = sys.argv[1:]
    {"create": cmd_create, "run": cmd_run, "inspect": cmd_inspect,
     "setting": cmd_setting}[name](ctx(), *args)
