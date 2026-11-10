"""Alles-in-één-image: diensten, poorten, sleutels en dat opstarten nooit hard faalt."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

from mediaguard import aio, bootstrap


def _env(tmp_path: Path, **extra: str) -> dict[str, str]:
    return {"MG_AIO_CONFIG": str(tmp_path / "config"), "MG_AIO_WORK": str(tmp_path / "work"),
            "MG_AIO_DATA": str(tmp_path / "data"), "PATH": os.environ["PATH"], **extra}


def test_specs_cover_all_services_with_generated_keys(tmp_path, monkeypatch):
    monkeypatch.setenv("MG_UID", str(os.getuid()))
    monkeypatch.setenv("MG_GID", str(os.getgid()))
    env = _env(tmp_path)
    (tmp_path / "data").mkdir()
    (tmp_path / "work").mkdir()
    warnings = aio.prepare(env)
    # niet als mount gekoppeld in de test: alleen meldingen, geen exceptie
    assert any("/config" in w for w in warnings)
    specs = {s.name: s for s in aio.build_specs(env)}
    assert set(specs) == {"core", "recovery", "encode", "web", "sonarr-series", "sonarr-anime",
                          "radarr-films", "radarr-animefilms", "hookguard"}
    secrets = tmp_path / "config" / "secrets"
    sonarr = specs["sonarr-series"].env
    keys = secrets / "sonarr-series"
    assert sonarr["SONARR__AUTH__APIKEY"] == (keys / "apikey").read_text().strip()
    assert sonarr["MEDIAGUARD_HOOK_KEY"] == (keys / "hookkey").read_text().strip()
    assert sonarr["SONARR__SERVER__PORT"] == "38989"
    assert specs["radarr-animefilms"].env["RADARR__SERVER__PORT"] == "37879"
    assert specs["radarr-films"].argv[0] == "/app/radarr/bin/Radarr"
    # core kent alle instanties op het interne adres en dezelfde publieke poort (Seerr)
    core = specs["core"].env
    assert core["MG_INSTANCE_SONARR_ANIME_URL"] == "http://127.0.0.1:38990"
    assert core["MG_INSTANCE_SONARR_ANIME_PUBLIC_PORT"] == "38990"
    assert core["MG_BIND"] == "127.0.0.1" and specs["recovery"].env["MG_ROLE"] == "recovery"
    assert core["MG_LIBRARY_ROOTS"] == str(tmp_path / "data")
    # geheimen zijn door de core-loader te lezen
    loaded = dict(core)
    bootstrap.load_secret_files(loaded)
    assert loaded["MG_INSTANCE_RADARR_FILMS_APIKEY"] == \
        specs["radarr-films"].env["RADARR__AUTH__APIKEY"]
    assert specs["web"].env["PORT"] == "30080"
    assert specs["encode"].env["MG_INTERNAL_TOKEN_FILE"].endswith("secrets/internal/token")
    # werkmappen bestaan
    assert (tmp_path / "work" / "encode" / "staging").is_dir()
    assert (tmp_path / "work" / "quarantine").is_dir()


def test_ports_are_configurable(tmp_path):
    env = _env(tmp_path, MG_WEB_PORT="31000", MG_PORT_SONARR_SERIES="40000")
    specs = {s.name: s for s in aio.build_specs(env)}
    assert specs["web"].env["PORT"] == "31000"
    assert specs["sonarr-series"].env["SONARR__SERVER__PORT"] == "40000"
    assert specs["core"].env["MG_INSTANCE_SONARR_SERIES_PUBLIC_PORT"] == "40000"


def test_prepare_never_raises_when_appdata_unwritable(tmp_path, monkeypatch):
    def boom():
        raise PermissionError(13, "Permission denied", "/config/secrets")

    monkeypatch.setattr(aio.bootstrap, "main", boom)
    warnings = aio.prepare(_env(tmp_path))
    assert any("schrijfrechten" in w for w in warnings)


def test_supervisor_restarts_crashed_service_and_stops_cleanly(tmp_path):
    marker = tmp_path / "starts"
    script = (f"import pathlib,sys; p=pathlib.Path({str(marker)!r}); "
              "p.write_text(p.read_text()+'x' if p.exists() else 'x'); sys.exit(3)")
    spec = aio.Spec("crash", [sys.executable, "-c", script], {"PATH": os.environ["PATH"]})
    sleeper = aio.Spec("sleep", [sys.executable, "-c", "import time; time.sleep(60)"],
                       {"PATH": os.environ["PATH"]})
    sup = aio.Supervisor([spec, sleeper], None, None)
    import threading
    t = threading.Thread(target=sup.run, daemon=True)
    t.start()
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline and len(marker.read_text() if marker.exists() else "") < 2:
        time.sleep(0.2)
    assert len(marker.read_text()) >= 2  # opnieuw gestart na crash
    proc = sleeper.proc
    sup.stop()
    t.join(30)
    assert proc is not None and proc.poll() is not None


def test_missing_binary_does_not_crash_supervisor(tmp_path):
    spec = aio.Spec("missing", [str(tmp_path / "nope")], {})
    sup = aio.Supervisor([spec], None, None)
    sup.start(spec)
    assert spec.proc is None and spec.next_start > 0
    subprocess.run([sys.executable, "-c", "pass"], check=True)


def test_ready_and_problem_page(tmp_path, monkeypatch):
    monkeypatch.setenv("MG_UID", str(os.getuid()))
    monkeypatch.setenv("MG_GID", str(os.getgid()))
    env = _env(tmp_path)
    assert not aio.ready(env)
    aio.prepare(env)
    assert aio.ready(env)
    page = aio.problem_page(["Appdata <b>niet</b> schrijfbaar"]).decode()
    assert "&lt;b&gt;niet&lt;/b&gt;" in page and "apps" in page


def test_start_order_fork_settings_and_health_urls(tmp_path):
    specs = aio.build_specs(_env(tmp_path))
    names = [s.name for s in specs]
    # hookwacht, dan Sonarr/Radarr, dan encoder, core (wacht op de forks), recovery, web
    assert names[0] == "hookguard"
    assert set(names[1:5]) == {"sonarr-series", "sonarr-anime", "radarr-films",
                               "radarr-animefilms"}
    assert names[5:] == ["encode", "core", "recovery", "web"]
    by = {s.name: s for s in specs}
    assert sorted(by["core"].wait_for) == [37878, 37879, 38989, 38990]
    assert by["core"].ready_wait == 90 and by["sonarr-anime"].ready_wait == 0
    fork = by["radarr-films"].env
    assert fork["RADARR__LOG__LEVEL"] == "info" and fork["RADARR__LOG__ROTATE"] == "5"
    assert fork["DOTNET_gcServer"] == "0" and fork["DOTNET_GCgen0size"] == "0x400000"
    assert "DOTNET_SYSTEM_GLOBALIZATION_INVARIANT" not in fork  # Radarr crasht daarop
    assert by["sonarr-series"].health == "http://127.0.0.1:38989/ping"
    assert by["web"].health.endswith("/login") and by["encode"].health.endswith("/livez")
    assert by["encode"].busy_aware and by["encode"].probe_timeout >= 15
    # stopvolgorde: web → forks → encoder → recovery → core
    assert by["web"].stop_rank < by["sonarr-series"].stop_rank < by["encode"].stop_rank \
        < by["recovery"].stop_rank < by["core"].stop_rank


def test_hung_service_is_restarted_and_state_written(tmp_path):
    hung = aio.Spec("encode", [sys.executable, "-c", "import time; time.sleep(60)"],
                    {"PATH": os.environ["PATH"]}, health="http://127.0.0.1:9/health",
                    grace=0)
    ok = aio.Spec("web", [sys.executable, "-c", "import time; time.sleep(60)"],
                  {"PATH": os.environ["PATH"]})
    state = tmp_path / "supervisor.json"
    health = tmp_path / "health"
    sup = aio.Supervisor([hung, ok], None, None, state_path=state, health_path=health,
                         max_fails=2)
    sup.start(hung)
    sup.start(ok)
    proc = hung.proc
    assert sup.check_health() == [] and hung.fails == 1
    sup.write_state()
    import json as _json
    snap = _json.loads(state.read_text())
    assert snap["services"]["encode"]["state"] == "unresponsive"
    assert health.read_text().startswith("ok")  # core/web gezond
    assert sup.check_health() == ["encode"]
    assert proc.poll() is not None and hung.hang_restarts == 1
    sup.stop(budget=2)
    assert ok.proc.poll() is not None


def test_successful_health_probes_are_not_logged():
    assert aio.quiet_line('INFO:     127.0.0.1:5 - "GET /api/v1/health HTTP/1.1" 200 OK')
    assert aio.quiet_line('127.0.0.1 - "GET /ping HTTP/1.1" 200')
    assert not aio.quiet_line('INFO: 127.0.0.1:5 - "GET /api/v1/health HTTP/1.1" 503 Error')
    assert not aio.quiet_line('INFO: 127.0.0.1:5 - "POST /api/v1/jobs HTTP/1.1" 200 OK')


def test_dri_groepen_van_gpu_nodes(tmp_path):
    """/dev/dri-groepen (render/video) gaan mee naar de encoder; root nooit; geen GPU = leeg."""
    import os

    from mediaguard.aio import _dri_groups

    assert _dri_groups(tmp_path / "geen-dri") == []
    dri = tmp_path / "dri"
    dri.mkdir()
    (dri / "card0").write_bytes(b"")
    (dri / "renderD128").write_bytes(b"")
    if os.geteuid() == 0:
        os.chown(dri / "card0", 0, 44)
        os.chown(dri / "renderD128", 0, 105)
        assert _dri_groups(dri) == [44, 105]
        os.chown(dri / "card0", 0, 0)
        assert _dri_groups(dri) == [105]
    else:
        assert _dri_groups(dri) == sorted({os.getgid()} - {0})


def test_busy_encoder_is_not_restarted_while_children_compute(tmp_path):
    """N1: traag HTTP-antwoord onder volle belasting is geen hang zolang ffmpeg rekent."""
    busy = aio.Spec(
        "encode",
        [sys.executable, "-c",
         "import subprocess,sys,time; subprocess.Popen([sys.executable,'-c',"
         "'while True: pass']); time.sleep(60)"],
        {"PATH": os.environ["PATH"]}, health="http://127.0.0.1:9/livez", grace=0,
        busy_aware=True, max_fails=1, probe_timeout=0.2)
    sup = aio.Supervisor([busy], None, None, max_fails=1)
    sup.start(busy)
    import time as _t
    _t.sleep(0.5)
    sup._busy(busy)  # eerste meting = nulpunt
    _t.sleep(0.5)
    assert sup.check_health() == [] and busy.fails == 0 and busy.busy_skips == 1
    sup.stop(budget=2)


def test_descendant_cpu_ticks_reads_proc(tmp_path):
    root = tmp_path / "proc"
    for pid, ppid, ut, st in [(10, 1, 5, 5), (11, 10, 100, 20), (12, 11, 7, 3), (13, 1, 999, 0)]:
        (root / str(pid)).mkdir(parents=True)
        (root / str(pid) / "stat").write_text(
            f"{pid} (ffm peg) S {ppid} 0 0 0 0 0 0 0 0 0 {ut} {st} 0 0")
    assert aio.Supervisor.descendant_cpu_ticks(10, root) == 130


def test_forks_reach_core_through_the_hook_guard(tmp_path):
    """Item 32: de forks praten met de hookwacht, die de noodstop vasthoudt als core
    herstart; de wachter krijgt het noodstopbestand en de database van core."""
    by = {s.name: s for s in aio.build_specs(_env(tmp_path, MG_HOOK_TIMEOUT_MS="4000"))}
    guard = by["hookguard"]
    assert guard.argv[-2:] == ["-m", "mediaguard.hookguard"]
    assert guard.env["MG_HOOKGUARD_CORE"] == "127.0.0.1:38100"
    assert guard.env["MG_HOOKGUARD_FLAG"] == str(tmp_path / "config" / "core" / "noodstop.json")
    assert guard.env["MG_HOOKGUARD_DB"] == str(tmp_path / "config" / "core" / "mediaguard.db")
    assert guard.env["MG_HOOKGUARD_TIMEOUT_MS"] == "3500"  # korter dan de fork wacht
    assert guard.health == "http://127.0.0.1:38102/livez"
    for name in ("sonarr-series", "sonarr-anime", "radarr-films", "radarr-animefilms"):
        env = by[name].env
        assert env["MEDIAGUARD_URL"] == "http://127.0.0.1:38102"
        assert env["MEDIAGUARD_HOOK_TIMEOUT_MS"] == "4000"
        assert env["MEDIAGUARD_HOOK_FAIL_POLICY"] == "open"  # trage core blokkeert niets
        # forks stoppen vóór de wachter
        assert by[name].stop_rank < guard.stop_rank


def test_web_runs_as_own_user_without_secrets(tmp_path):
    """Item 33: de web-server (via de tunnel bereikbaar) draait als eigen gebruiker zonder
    de groepen van de app, en krijgt geen sleutels of het encoder-adres mee."""
    by = {s.name: s for s in aio.build_specs(_env(tmp_path))}
    web = by["web"]
    assert web.user == (65534, 65534)
    assert by["core"].user is None and by["sonarr-series"].user is None
    assert not any(k in web.env for k in ("MG_INTERNAL_TOKEN", "MG_INTERNAL_TOKEN_FILE",
                                          "MG_SECRETS_DIR", "MG_ENCODER_URL"))
    custom = {s.name: s for s in aio.build_specs(_env(tmp_path, MG_WEB_UID="1234"))}
    assert custom["web"].user == (1234, 1234)


def test_private_appdata_dirs_closed_for_other_users(tmp_path):
    """Item 33: een oudere installatie kan /config/core of een fork-map nog voor iedereen open
    hebben staan; de web-server (eigen gebruiker) mag daar niet in. Alleen de 'overige'
    rechten verdwijnen, alleen bij mappen van de app-gebruiker; media blijven ongemoeid."""
    import os
    import stat

    config = tmp_path / "config"
    for name in ("core", "secrets", "sonarr-series", "logs", "andere-app"):
        (config / name).mkdir(parents=True)
        (config / name).chmod(0o777)
    (config / "encode").mkdir()
    (config / "encode").chmod(0o750)
    me = os.getuid()
    # andere eigenaar (bv. root-map die de app via 'overige' rechten gebruikt): niets wijzigen
    assert aio.close_private_dirs(config, me + 1) == []
    assert stat.S_IMODE((config / "core").stat().st_mode) == 0o777
    closed = aio.close_private_dirs(config, me)
    assert sorted(closed) == ["core", "logs", "secrets", "sonarr-series"]
    for name in closed:
        assert stat.S_IMODE((config / name).stat().st_mode) == 0o770
    assert stat.S_IMODE((config / "encode").stat().st_mode) == 0o750
    assert stat.S_IMODE((config / "andere-app").stat().st_mode) == 0o777
    assert aio.close_private_dirs(config, me) == []  # idempotent


def test_stop_budget_follows_documented_grace_period(tmp_path):
    """Verbeterpunt 30: de README zegt 60 s stoptijd; de supervisor gebruikte vast 8 s."""
    assert aio.stop_budget({}) == 55.0
    assert aio.stop_budget({"MG_STOP_TIMEOUT": "120"}) == 115.0
    assert aio.stop_budget({"MG_STOP_TIMEOUT": "3"}) == 5.0  # nooit nul
    assert aio.Supervisor([], None, None).stop_budget == 55.0
    specs = {s.name: s for s in aio.build_specs(_env(tmp_path))}
    # zelfde map als CONFIG_ROOT van de encoder: daar zoekt hij het bestand
    assert specs["encode"].planned_stop_marker == str(
        Path(specs["encode"].env["CONFIG_ROOT"]) / "geplande-stop")
    assert specs["core"].planned_stop_marker is None


def test_planned_stop_is_announced_before_sigterm_and_gets_time(tmp_path):
    """De encoder ziet bij SIGTERM al dat het een geplande stop is (geen herstartlus) en
    krijgt de tijd om af te ronden in plaats van na ±2 s gekild te worden."""
    marker = tmp_path / "encode" / "geplande-stop"
    seen = tmp_path / "seen"
    script = (
        "import pathlib,signal,sys,time\n"
        f"m=pathlib.Path({str(marker)!r}); s=pathlib.Path({str(seen)!r})\n"
        "def h(*_):\n"
        "    s.write_text('ja' if m.exists() else 'nee')\n"
        "    time.sleep(2.5)\n"  # netjes afronden kost even tijd
        "    sys.exit(0)\n"
        "signal.signal(signal.SIGTERM, h)\n"
        "time.sleep(60)\n")
    spec = aio.Spec("encode", [sys.executable, "-c", script], {"PATH": os.environ["PATH"]},
                    planned_stop_marker=str(marker))
    sup = aio.Supervisor([spec], None, None, stop_budget=10)
    sup.start(spec)
    time.sleep(0.5)
    proc = spec.proc
    sup.stop()
    assert seen.read_text() == "ja"
    assert proc is not None and proc.returncode == 0  # zelf gestopt, niet gekild


def test_hang_restart_is_not_announced_as_planned(tmp_path):
    marker = tmp_path / "encode" / "geplande-stop"
    hung = aio.Spec("encode", [sys.executable, "-c", "import time; time.sleep(60)"],
                    {"PATH": os.environ["PATH"]}, health="http://127.0.0.1:9/livez", grace=0,
                    planned_stop_marker=str(marker))
    sup = aio.Supervisor([hung], None, None, max_fails=1)
    sup.start(hung)
    assert sup.check_health() == ["encode"]
    assert not marker.exists()  # vastgelopen ≠ gepland: telt wel voor de herstartlus
    sup.stop(budget=2)


def test_encoder_keeps_the_whole_budget_when_core_stops_fast(tmp_path):
    """Core (laatste in de stopvolgorde) is meteen weg; de encoder die nog jobs terugzet,
    mag tot het einde van het budget doorgaan en wordt niet direct daarna gekild."""
    done = tmp_path / "klaar"
    slow = (
        "import pathlib,signal,sys,time\n"
        f"d=pathlib.Path({str(done)!r})\n"
        "def h(*_):\n"
        "    time.sleep(4.5)\n"  # langer dan zijn 'beurt' (budget/2 = 3 s)
        "    d.write_text('ja')\n"
        "    sys.exit(0)\n"
        "signal.signal(signal.SIGTERM, h)\n"
        "time.sleep(60)\n")
    env = {"PATH": os.environ["PATH"]}
    encode = aio.Spec("encode", [sys.executable, "-c", slow], env, stop_rank=6)
    core = aio.Spec("core", [sys.executable, "-c", "import time; time.sleep(60)"], env,
                    stop_rank=9)
    web = aio.Spec("web", [sys.executable, "-c", "import time; time.sleep(60)"], env,
                   stop_rank=1)
    sup = aio.Supervisor([web, encode, core], None, None, stop_budget=6)
    for spec in (web, encode, core):
        sup.start(spec)
    time.sleep(0.5)
    proc = encode.proc
    sup.stop()
    assert done.read_text() == "ja"
    assert proc is not None and proc.returncode == 0


def test_core_and_recovery_get_sigterm_early_even_while_encoder_finishes(tmp_path,
                                                                        monkeypatch):
    """Zonder ingestelde Stop Grace Period killt Docker na 10 s. Core en recovery moeten dan
    al SIGTERM hebben gehad (netjes afsluiten), ook als de encoder nog afrondt; de encoder
    houdt daarna de rest van het budget (hier verkleind: 1 s i.p.v. 6 s)."""
    monkeypatch.setattr(aio, "LATE_SIGTERM_AT", 1.0)
    monkeypatch.setattr(aio, "FAST_STOP_WAIT", 0.5)
    stamp = tmp_path / "core-sigterm"
    done = tmp_path / "encoder-klaar"
    core_script = (
        "import pathlib,signal,sys,time\n"
        f"p=pathlib.Path({str(stamp)!r})\n"
        "def h(*_):\n"
        "    p.write_text(repr(time.monotonic()))\n"
        "    sys.exit(0)\n"
        "signal.signal(signal.SIGTERM, h)\n"
        "time.sleep(60)\n")
    enc_script = (
        "import pathlib,signal,sys,time\n"
        f"d=pathlib.Path({str(done)!r})\n"
        "def h(*_):\n"
        "    time.sleep(3)\n"
        "    d.write_text('ja')\n"
        "    sys.exit(0)\n"
        "signal.signal(signal.SIGTERM, h)\n"
        "time.sleep(60)\n")
    env = {"PATH": os.environ["PATH"]}
    web = aio.Spec("web", [sys.executable, "-c", "import time; time.sleep(60)"], env,
                   stop_rank=1)
    encode = aio.Spec("encode", [sys.executable, "-c", enc_script], env, stop_rank=6,
                      slow_stop=True)
    recovery = aio.Spec("recovery", [sys.executable, "-c", "import time; time.sleep(60)"],
                        env, stop_rank=8)
    core = aio.Spec("core", [sys.executable, "-c", core_script], env, stop_rank=9)
    sup = aio.Supervisor([web, encode, recovery, core], None, None, stop_budget=20)
    for spec in (web, encode, recovery, core):
        sup.start(spec)
    time.sleep(0.5)
    enc_proc = encode.proc
    began = time.monotonic()
    sup.stop()
    assert float(stamp.read_text()) - began < 1.0 + 0.5 + 0.5  # niet pas na de encoder
    assert done.read_text() == "ja"  # de encoder rondde daarna nog netjes af
    assert enc_proc is not None and enc_proc.returncode == 0
    specs = {s.name: s for s in aio.build_specs(_env(tmp_path))}
    assert [n for n, s in specs.items() if s.slow_stop] == ["encode"]

