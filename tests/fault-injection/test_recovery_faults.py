"""Recovery fault injection inside the REAL recovery container (tests/fault-injection/run.sh).

Invariant after every crash + container restart (journal replay in `recovery` startup):
  - the transaction is terminal (committed / rolled_back / aborted), never stuck;
  - the library path holds a valid file: the original (sha256 identical) or the verified new file;
  - when committed, the original is restorable from quarantine (sha256 identical);
  - no `.mg-replace-*` leftovers, lock released.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import time
from pathlib import Path

import pytest
import requests

HERE = Path(__file__).resolve().parent
RUN = HERE / ".run"
FIX = RUN / "fixtures"
REC = "mgfault-recovery-1"
CORE = "http://127.0.0.1:28200/api/v1"
POINTS = ["lock", "validate", "quarantine:after_action", "quarantine", "stage_temp",
          "activate:after_action", "activate", "record", "refresh"]
EXPECT = {"lock": "aborted", "validate": "aborted", "quarantine:after_action": "rolled_back",
          "quarantine": "rolled_back", "stage_temp": "rolled_back",
          "activate:after_action": "committed", "activate": "committed", "record": "committed",
          "refresh": "committed"}
TERMINAL = {"committed", "rolled_back", "aborted"}


def log(*a):
    print("   ", *a, flush=True)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


ORIG, NEW = FIX / "orig.mkv", FIX / "new.mkv"
SHA_ORIG, SHA_NEW = sha(ORIG), sha(NEW)


def driver(*args, check=True):
    with open(HERE / "driver.py", "rb") as f:
        p = subprocess.run(["docker", "exec", "-i", REC, "python", "-", *args], stdin=f,
                           capture_output=True, text=True)
    lines = [json.loads(x) for x in p.stdout.splitlines() if x.startswith("{")]
    if check and p.returncode != 0:
        raise AssertionError(f"driver {args} rc={p.returncode}\n{p.stdout}\n{p.stderr[-2000:]}")
    return p.returncode, lines


def dexec(*args, user="568"):
    return subprocess.run(["docker", "exec", "-u", user, REC, *args], check=True,
                          capture_output=True, text=True).stdout


def health():
    return subprocess.run(["docker", "inspect", "-f", "{{.State.Health.Status}}", REC],
                          capture_output=True, text=True).stdout.strip()


def restart_recovery(kill=False):
    t0 = time.time()
    if kill:
        subprocess.run(["docker", "kill", "-s", "KILL", REC], check=True, capture_output=True)
        subprocess.run(["docker", "start", REC], check=True, capture_output=True)
    else:
        subprocess.run(["docker", "restart", REC], check=True, capture_output=True)
    deadline = time.time() + 90
    while time.time() < deadline:
        try:
            if requests.get("http://127.0.0.1:28200/api/v1/health", timeout=3).ok and health() in ("healthy", "starting"):
                rc = subprocess.run(["docker", "exec", REC, "python", "-c",
                                     "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8100/api/v1/health',timeout=3)"],
                                    capture_output=True)
                if rc.returncode == 0:
                    log("recovery container back in %.1fs" % (time.time() - t0))
                    return
        except requests.RequestException:
            pass
        time.sleep(1)
    raise AssertionError("recovery did not come back")


def prepare(name, orig=ORIG, new=NEW):
    """Library file in the scratch tree + staged output, owned by the app user (568)."""
    d = RUN / "media/tv/Show/Season 01"
    d.mkdir(parents=True, exist_ok=True)
    lib = d / f"Show - {name}.mkv"
    shutil.copy(orig, lib)
    sd = RUN / "work/staging" / name
    sd.mkdir(parents=True, exist_ok=True)
    staged = sd / "out.mkv"
    shutil.copy(new, staged)
    for p in (RUN / "media/tv", RUN / "work/staging"):
        subprocess.run(["chown", "-R", "568:568", str(p)], check=True)
    return f"/media/tv/Show/Season 01/{lib.name}", f"/work/staging/{name}/out.mkv"


def create(lib, staged, job=False):
    _, out = driver("create", lib, staged, *(["--job"] if job else []))
    return out[-1]["txn"]


def inspect(txn):
    return driver("inspect", str(txn))[1][-1]


def assert_invariants(txn, expect_state=None, orig_sha=SHA_ORIG, new_sha=SHA_NEW):
    st = inspect(txn)
    t, files = st["txn"], st["files"]
    assert t["state"] in TERMINAL, st
    if expect_state:
        assert t["state"] == expect_state, (expect_state, t, st["journal"][-3:])
    final = files["final_path"]
    assert final["exists"], st
    assert final["sha256"] in (orig_sha, new_sha)
    if t["state"] == "committed":
        assert final["sha256"] == new_sha
        q = files["quarantine_path"]
        assert q["exists"] and q["sha256"] == orig_sha, q
        assert st["quarantine_items"][0]["status"] == "restorable"
    else:
        assert final["sha256"] == orig_sha
        assert not (files.get("quarantine_path") or {}).get("exists"), "quarantine copy not cleaned"
    assert not [x for x in st["leftovers"] if f"-{txn}-" in x], st["leftovers"]
    assert not [lk for lk in st["locks"] or [] if lk["owner"] == f"recovery:txn:{txn}"], st["locks"]
    return st


class Core:
    s = requests.Session()


@pytest.fixture(scope="session", autouse=True)
def setup_once():
    r = Core.s.post(CORE + "/auth/setup", json={"username": "admin", "password": "fault-test-123"}, timeout=10)
    assert r.status_code == 200, r.text
    Core.s.headers["X-CSRF-Token"] = r.json()["csrf_token"]
    driver("setting", "recovery.min_free_gb", "0")  # scratch disks are small
    # fresh install: the software lock is on by default (ADR-011)
    assert Core.s.get(CORE + "/recovery/readiness", timeout=10).json()["library_locked"] is True
    # ADR-011 'Bibliotheek vrijgeven' (fresh install, no migration) through the real API
    u = Core.s.post(CORE + "/recovery/library-write", json={"confirm": True}, timeout=10)
    assert u.status_code == 200 and u.json() == {"library_write_enabled": True}, u.text
    yield


def test_locked_library_aborts_before_touching_anything():
    """Software lock (library.write_enabled=False, the default) — the executor aborts at the
    lock step: original byte-identical, nothing in quarantine, no leftovers, lock released."""
    Core.s.post(CORE + "/recovery/library-write", json={"enabled": False}, timeout=10).raise_for_status()
    try:
        assert Core.s.get(CORE + "/recovery/readiness", timeout=10).json()["library_locked"] is True
        lib, staged = prepare("locked-S09E01")
        txn = create(lib, staged)
        driver("run", str(txn), check=False)
        st = assert_invariants(txn, "aborted")
        assert "vergrendeld" in (st["txn"]["error"] or ""), st["txn"]
        assert "LIBRARY_LOCKED" in json.dumps(st["journal"]), st["journal"][-3:]
    finally:
        Core.s.post(CORE + "/recovery/library-write", json={"confirm": True}, timeout=10).raise_for_status()


# --------------------------------------------------------------------------------------------- tests
@pytest.mark.parametrize("method", ["link", "copy"])
def test_sigkill_at_every_step_then_container_restart_replays_journal(method):
    """kill -9 of the recovery process at each fault point; one real container restart replays
    all interrupted transactions. method=link: quarantine inside the library dataset (hardlink);
    method=copy: central quarantine on another filesystem (tmpfs) → copy + fsync + sha256."""
    driver("setting", "recovery.quarantine_location",
           json.dumps("library_root" if method == "link" else "central"))
    txns = {}
    for i, point in enumerate(POINTS):
        lib, staged = prepare(f"{method}-S01E{i + 1:02d}")
        txn = create(lib, staged)
        rc, out = driver("run", str(txn), "--kill-at", point, check=False)
        reached = [x["point"] for x in out if "point" in x]
        assert rc in (-9, 137), (point, rc, out)       # really SIGKILLed, no clean exit
        assert reached[-1] == point
        pre = inspect(txn)["txn"]["state"]
        log(f"{method} kill at {point:24s} -> state before replay: {pre}")
        txns[point] = txn
    restart_recovery()
    for point, txn in txns.items():
        st = assert_invariants(txn, EXPECT[point])
        log(f"{method} {point:24s} -> {st['txn']['state']:12s} method={st['txn']['quarantine_method']}"
            f" journal_tail={[j['step'] + ':' + j['phase'] for j in st['journal'][-2:]]}")
        if st["txn"]["state"] == "committed":
            assert st["txn"]["quarantine_method"] == method


def test_committed_original_is_restorable_via_api_after_crash():
    driver("setting", "recovery.quarantine_location", json.dumps("library_root"))
    lib, staged = prepare("restore-S02E01")
    txn = create(lib, staged)
    driver("run", str(txn), "--kill-at", "activate", check=False)
    restart_recovery()
    st = assert_invariants(txn, "committed")
    item = st["quarantine_items"][0]["id"]
    r = Core.s.post(f"{CORE}/quarantine/{item}/restore", json={"confirm": True, "reason": "fault test"}, timeout=10)
    assert r.status_code == 200, r.text
    rtxn = r.json()["transaction_id"]
    deadline = time.time() + 60
    while time.time() < deadline:
        t = Core.s.get(f"{CORE}/recovery/transactions/{rtxn}", timeout=10).json()
        if t.get("state") in TERMINAL | {"conflict"}:
            break
        time.sleep(1)
    log("restore transaction:", t.get("state"), t.get("error"))
    assert t["state"] == "committed"
    assert sha(RUN / "media/tv/Show/Season 01/Show - restore-S02E01.mkv") == SHA_ORIG
    out = dexec("ffprobe", "-v", "error", "-show_entries", "stream=codec_name", "-of", "csv=p=0",
                "/media/tv/Show/Season 01/Show - restore-S02E01.mkv")
    assert "h264" in out


def _journal_seen(txn, step):
    db = sqlite3.connect(f"file:{RUN}/appdata/mediaguard.db?mode=ro", uri=True, timeout=1)
    try:
        return db.execute("SELECT 1 FROM replacement_journal WHERE transaction_id=? AND step=? AND phase='begin'",
                          (txn, step)).fetchone() is not None
    except sqlite3.OperationalError:
        return False
    finally:
        db.close()


@pytest.mark.parametrize("step", ["quarantine", "stage_temp"])
def test_kill9_running_worker_mid_copy_by_timing(step):
    """The container's OWN worker executes the job; we SIGKILL the whole container (power-loss
    like) as soon as the journal shows the copy step began (400 MB, cross-device copy)."""
    driver("setting", "recovery.quarantine_location", json.dumps("central"))
    big = FIX / "big.bin"
    big_new = RUN / "big-new.bin"
    if not big_new.exists():
        with open(big, "rb") as src, open(big_new, "wb") as dst:
            dst.write(b"X" * 1024)
            shutil.copyfileobj(src, dst)
    lib, staged = prepare(f"big-{step}", orig=big, new=big_new)
    txn = create(lib, staged, job=True)
    t0 = time.time()
    while not _journal_seen(txn, step):
        if time.time() - t0 > 60:
            raise AssertionError("step never started")
        time.sleep(0.005)
    subprocess.run(["docker", "kill", "-s", "KILL", REC], check=True, capture_output=True)
    log(f"killed container {time.time() - t0:.2f}s after job start, during '{step}'")
    pre = inspect_offline(txn)
    log("state at kill:", pre)
    subprocess.run(["docker", "start", REC], check=True, capture_output=True)
    restart_wait()
    st = assert_invariants(txn, None, orig_sha=sha(big), new_sha=sha(big_new))
    log(f"after replay: {st['txn']['state']} error={st['txn']['error']}")
    # the requeued job must not re-run the transaction a second time
    time.sleep(3)
    assert inspect(txn)["txn"]["state"] == st["txn"]["state"]


def inspect_offline(txn):
    db = sqlite3.connect(f"file:{RUN}/appdata/mediaguard.db?mode=ro", uri=True, timeout=5)
    try:
        return db.execute("SELECT state FROM replacement_transactions WHERE id=?", (txn,)).fetchone()[0]
    finally:
        db.close()


def restart_wait():
    deadline = time.time() + 90
    while time.time() < deadline:
        rc = subprocess.run(["docker", "exec", REC, "python", "-c",
                             "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8100/api/v1/health',timeout=3)"],
                            capture_output=True)
        if rc.returncode == 0:
            return
        time.sleep(1)
    raise AssertionError("recovery not back")


def _small(name, orig_mb, new_mb):
    """Files on the 64 MiB tmpfs library (/media/small)."""
    dexec("mkdir", "-p", "/media/small/Show/Season 01", f"/work/staging/{name}")
    dexec("sh", "-c", f"head -c {orig_mb}M /dev/urandom > '/media/small/Show/Season 01/Show - {name}.mkv'")
    dexec("sh", "-c", f"head -c {new_mb}M /dev/urandom > '/work/staging/{name}/out.mkv'")
    lib, staged = f"/media/small/Show/Season 01/Show - {name}.mkv", f"/work/staging/{name}/out.mkv"
    sums = dexec("sha256sum", lib, staged).split()
    return lib, staged, sums[0], sums[2]


def test_disk_full_during_copy_rolls_back():
    driver("setting", "recovery.quarantine_location", json.dumps("library_root"))
    lib, staged, so, sn = _small("full-S01E01", 20, 30)
    txn = create(lib, staged)
    # the free-space pre-check passes (44 MiB free >= 30 MiB); then the disk fills up
    rc, out = driver("run", str(txn), "--fill", "/media/small:5000000")
    log("disk full result:", out[-1])
    dexec("rm", "-f", "/media/small/.filler")
    st = assert_invariants(txn, "rolled_back", orig_sha=so, new_sha=sn)
    assert "Schijf vol" in (st["txn"]["error"] or ""), st["txn"]
    # and when the space is missing up front, the pre-check aborts before touching anything
    lib2, staged2, so2, sn2 = _small("full-S01E02", 10, 40)
    txn2 = create(lib2, staged2)
    rc, out = driver("run", str(txn2))
    st = assert_invariants(txn2, "aborted", orig_sha=so2, new_sha=sn2)
    log("pre-check:", st["txn"]["error"])
    assert "INSUFFICIENT_SPACE" in json.dumps(st["journal"]) or "ruimte" in (st["txn"]["error"] or "")
    dexec("rm", "-rf", "/media/small/Show")


def test_permission_denied_rolls_back():
    driver("setting", "recovery.quarantine_location", json.dumps("library_root"))
    lib, staged = prepare("perm-S03E01")
    season = RUN / "media/tv/Show/Season 01"
    os.chmod(season, 0o555)               # app user may read but not create the temp file
    try:
        txn = create(lib, staged)
        rc, out = driver("run", str(txn))
        log("permission (library dir):", out[-1])
    finally:
        os.chmod(season, 0o755)
    st = assert_invariants(txn, "rolled_back")
    assert "PermissionError" in (st["txn"]["error"] or "") or "Permission" in json.dumps(st["journal"])
    # quarantine dir not writable either → link and copy both refused → rolled back, original kept
    lib, staged = prepare("perm-S03E02")
    qroot = RUN / "media/tv/.mediaguard-quarantine"
    qroot.mkdir(exist_ok=True)
    subprocess.run(["chown", "568:568", str(qroot)], check=True)
    os.chmod(qroot, 0o555)
    try:
        txn = create(lib, staged)
        rc, out = driver("run", str(txn))
        log("permission (quarantine dir):", out[-1])
    finally:
        os.chmod(qroot, 0o755)
    st = assert_invariants(txn, "rolled_back")
