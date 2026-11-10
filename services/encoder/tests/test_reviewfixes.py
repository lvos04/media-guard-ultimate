"""Regressietests voor de reviewbevindingen (D/E) in native modus."""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from conftest import TESTDATA
from fastapi.testclient import TestClient

from app import hostlimits
from app.config import Settings, settings
from app.db import Database
from app.main import create_app
from app.media import commandos
from app.media.beslissen import STANDAARD, Besluit, beslis
from app.media.bestandsfilter import is_hoofdbestand, veilig_om_op_te_ruimen
from app.media.probe import probe
from app.onderhoud import storage_guard, weekly_database_maintenance
from app.planner import Planner, recover_orphaned_jobs
from app.scanner import ScanBusy
from app.scanscheduler import LibraryScanScheduler
from app.uitvoerder import _run_process, create_job_from_decision, run_job, terminate_process_group
from app.verificatie import verify_result


@pytest.fixture
def native(tmp_path: Path) -> tuple[Database, Settings, Path]:
    roots = {name: tmp_path / name for name in ("cache", "work", "config", "backups")}
    for path in roots.values():
        path.mkdir()
    library = tmp_path / "library"
    library.mkdir()
    configured = replace(
        settings,
        library_root=library,
        pipeline_root=tmp_path,
        cache_root=roots["cache"],
        work_root=roots["work"],
        config_root=roots["config"],
        backup_root=roots["backups"],
        database_path=roots["config"] / "app.db",
    )
    database = Database(configured)
    database.initialize()
    return database, configured, library


def _job(database: Database, library: Path, source: Path) -> int:
    profile = {**STANDAARD, "drempel_720": 1, "preset_720": 12, "crf_720": 50}
    profile_id = database.execute_insert(
        "INSERT INTO profiel(naam,inst) VALUES (?,?)", ("T", json.dumps(profile))
    )
    library_id = database.execute_insert(
        "INSERT INTO library(naam,pad,profiel_id,verwacht_device) VALUES (?,?,?,?)",
        ("T", str(library), profile_id, library.stat().st_dev),
    )
    stat = source.stat()
    info = probe(str(source))
    assert info is not None
    file_id = database.execute_insert(
        "INSERT INTO bestand(library_id,pad,grootte,mtime,nlink,categorie,probe,gescand_op) "
        "VALUES (?,?,?,?,?,'ok',?,?)",
        (
            library_id,
            str(source),
            stat.st_size,
            stat.st_mtime,
            stat.st_nlink,
            json.dumps(info),
            datetime.now(UTC).isoformat(),
        ),
    )
    decision = beslis(info, profile)
    decision_id = database.execute_insert(
        "INSERT INTO besluit(bestand_id,actie,baan,encoder,reden,payload,status,gemaakt_op) "
        "VALUES (?,?,?,?,?,?,'goedgekeurd',?)",
        (
            file_id,
            "hercoderen",
            "cpu",
            "libsvtav1",
            "test",
            decision.model_copy(update={"actie": "hercoderen"}).model_dump_json(),
            datetime.now(UTC).isoformat(),
        ),
    )
    return create_job_from_decision(database, decision_id)


# ── E1/E4/E3/E6/E15: uitvoerder ─────────────────────────────────────────────


async def test_ffmpeg_exit_255_na_sigterm_gaat_terug_in_de_wachtrij(
    native: tuple[Database, Settings, Path],
) -> None:
    database, configured, library = native
    source = library / "film.mkv"
    shutil.copy2(TESTDATA / "sdr-meertalig.mkv", source)
    job_id = _job(database, library, source)
    script = "print('Exiting normally, received signal 15.', flush=True);raise SystemExit(255)"
    result = await run_job(
        database,
        configured,
        job_id,
        storage_validator=lambda: None,
        command_override=["/usr/bin/python3", "-c", script],
    )
    assert result is False
    row = database.fetch_one("SELECT status,foutsoort FROM job WHERE id=?", (job_id,))
    assert row is not None and row["status"] == "wacht" and row["foutsoort"] is None


async def test_sigkill_tijdens_update_gaat_terug_in_de_wachtrij(
    native: tuple[Database, Settings, Path],
) -> None:
    """docker stop/app-update: na de wachttijd volgt SIGKILL; dat is geen encodefout."""
    from app import uitvoerder

    database, configured, library = native
    source = library / "film.mkv"
    shutil.copy2(TESTDATA / "sdr-meertalig.mkv", source)
    job_id = _job(database, library, source)
    script = "import os,signal,time;time.sleep(1);os.kill(os.getpid(), signal.SIGKILL)"

    async def stop_soon() -> None:
        await asyncio.sleep(0.3)
        uitvoerder.request_shutdown()

    stopper = asyncio.create_task(stop_soon())
    try:
        result = await run_job(
            database,
            configured,
            job_id,
            storage_validator=lambda: None,
            command_override=["/usr/bin/python3", "-c", script],
        )
    finally:
        await stopper
        uitvoerder.reset_shutdown()
    assert result is False
    row = database.fetch_one("SELECT status,foutsoort FROM job WHERE id=?", (job_id,))
    assert row is not None and row["status"] == "wacht" and row["foutsoort"] is None


async def test_ontbrekend_programma_is_een_fout_geen_verdwenen_bron(
    native: tuple[Database, Settings, Path],
) -> None:
    database, configured, library = native
    source = library / "film.mkv"
    shutil.copy2(TESTDATA / "sdr-meertalig.mkv", source)
    job_id = _job(database, library, source)
    assert not await run_job(
        database,
        configured,
        job_id,
        storage_validator=lambda: None,
        command_override=["/bestaat/niet/mkvmerge", "-o", "x"],
    )
    row = database.fetch_one("SELECT status,fout FROM job WHERE id=?", (job_id,))
    assert row is not None and row["status"] == "mislukt"
    assert "ontbreekt" in str(row["fout"])
    assert not (configured.cache_root / str(job_id)).exists()


async def test_mislukte_encode_ruimt_cache_op(native: tuple[Database, Settings, Path]) -> None:
    database, configured, library = native
    source = library / "film.mkv"
    shutil.copy2(TESTDATA / "sdr-meertalig.mkv", source)
    job_id = _job(database, library, source)
    partial = configured.cache_root / str(job_id) / "resultaat.mkv.deel"
    script = f"open({str(partial)!r},'wb').write(b'x'*1024);raise SystemExit(7)"
    assert not await run_job(
        database,
        configured,
        job_id,
        storage_validator=lambda: None,
        command_override=["/usr/bin/python3", "-c", script],
    )
    assert not (configured.cache_root / str(job_id)).exists()


async def test_mkvmerge_waarschuwing_exit_1_is_succes(
    native: tuple[Database, Settings, Path], tmp_path: Path
) -> None:
    database, _configured, library = native
    source = library / "film.mkv"
    shutil.copy2(TESTDATA / "sdr-meertalig.mkv", source)
    job_id = _job(database, library, source)
    fake = tmp_path / "bin" / "mkvmerge"
    fake.parent.mkdir()
    fake.write_text("#!/bin/sh\necho 'Warning: iets'\nexit 1\n", encoding="utf-8")
    fake.chmod(0o755)
    log = tmp_path / "log.txt"
    assert await _run_process(database, job_id, [str(fake)], log, 1.0) is True
    other = tmp_path / "bin" / "tool"
    shutil.copy2(fake, other)
    assert await _run_process(database, job_id, [str(other)], log, 1.0) is False


def test_killpg_op_verdwenen_proces_is_geen_fout() -> None:
    process = subprocess.Popen(["/bin/true"], start_new_session=True)
    process.wait()
    assert terminate_process_group(process.pid) is False


async def test_planner_stopt_lopende_encodes_bij_afsluiten(
    native: tuple[Database, Settings, Path],
) -> None:
    import asyncio

    database, configured, library = native
    source = library / "film.mkv"
    shutil.copy2(TESTDATA / "sdr-meertalig.mkv", source)
    job_id = _job(database, library, source)
    process = subprocess.Popen(["/bin/sleep", "60"], start_new_session=True)
    database.execute("UPDATE job SET status='bezig',proces_pid=? WHERE id=?", (process.pid, job_id))
    planner = Planner(database, configured, storage_validator=lambda: None)

    async def worker() -> bool:
        await asyncio.to_thread(process.wait)
        return False

    planner.tasks[job_id] = asyncio.create_task(worker())
    assert planner.terminate_active() == [job_id]
    await asyncio.wait_for(planner.tasks[job_id], timeout=5)
    assert process.returncode is not None


def test_weesherstel_sluit_poging_af(native: tuple[Database, Settings, Path]) -> None:
    database, configured, library = native
    source = library / "film.mkv"
    shutil.copy2(TESTDATA / "sdr-meertalig.mkv", source)
    job_id = _job(database, library, source)
    database.execute(
        "UPDATE job SET status='bezig',pogingen=1,proces_pid=999999 WHERE id=?", (job_id,)
    )
    database.execute(
        "INSERT INTO job_poging(job_id,poging,status,gestart_op) VALUES (?,1,'bezig',?)",
        (job_id, datetime.now(UTC).isoformat()),
    )
    assert recover_orphaned_jobs(database, configured) == 1
    attempt = database.fetch_one("SELECT status,klaar_op FROM job_poging WHERE job_id=?", (job_id,))
    assert attempt is not None and attempt["status"] == "afgebroken" and attempt["klaar_op"]


# ── E8/E10/E13/E14: commando's en verificatie ───────────────────────────────


def test_coverafbeelding_wordt_geteld_en_verlies_expliciet_gemeld(tmp_path: Path) -> None:
    from app.mediaguard.service import metadata_loss_of

    source = tmp_path / "met-cover.mkv"
    cover = tmp_path / "cover.jpg"
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
         "color=red:size=64x64:duration=0.1", "-frames:v", "1", str(cover)],
        check=True,
    )
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-y", "-i", str(TESTDATA / "sdr-meertalig.mkv"),
         "-map", "0", "-c", "copy", "-attach", str(cover),
         "-metadata:s:t", "mimetype=image/jpeg", "-metadata:s:t", "filename=cover.jpg",
         str(source)],
        check=True,
    )
    info = probe(str(source))
    assert info is not None and info["attached_pics"]
    assert info["video_indices"] == [0]
    decision = beslis(info, {**STANDAARD, "drempel_1080": 1})
    assert decision.actie == "hercoderen"
    assert "coverafbeelding" in " ".join(decision.waarschuwingen)
    assert "cover_art" in metadata_loss_of(decision)


def test_spoorindexen_worden_na_voorbewerking_herleid() -> None:
    bron = {
        "audio": [{"index": 2}, {"index": 3}],
        "subs": [{"index": 4}],
    }
    voorbewerkt = {"audio": [{"index": 1}, {"index": 2}], "subs": [{"index": 3}]}
    decision = Besluit(actie="hercoderen", audio_houden=[3], subs_houden=[4])
    remapped = commandos.remap_sporen(decision, bron, voorbewerkt)
    assert remapped.audio_houden == [2] and remapped.subs_houden == [3]


def test_mkvmerge_gebruikt_het_gemeten_videospoor() -> None:
    decision = Besluit(actie="opschonen", audio_houden=[0])
    args = commandos._mkvmerge_track_selection(decision, {"video_indices": [1]})
    assert args[:2] == ["--video-tracks", "1"]


def test_bron_zonder_audio_hoeft_geen_audio_op_te_leveren(tmp_path: Path) -> None:
    source = tmp_path / "stil.mkv"
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
         "testsrc2=size=320x180:rate=24:duration=1", "-c:v", "libx264", str(source)],
        check=True,
    )
    info = probe(str(source))
    assert info is not None and not info["audio"]
    decision = Besluit(actie="hercoderen", baan="cpu", encoder="libsvtav1")
    result = tmp_path / "uit.mkv"
    arguments = commandos.encode_cpu(
        info, decision, {**STANDAARD, "preset_720": 12, "crf_720": 55}, str(source), str(result)
    )
    subprocess.run(arguments, check=True, capture_output=True)
    verification = verify_result(info, result, 10**12, decision)
    assert verification.reason != "resultaat bevat geen audiospoor"


def test_oude_route_slaat_dv7_zonder_bewezen_mel_over() -> None:
    info = {
        "codec": "hevc", "width": 3840, "height": 2160, "kbps": 50000, "duration": 100,
        "is_hdr10": True, "hdr10plus": False, "dv_profile": 7, "dv_compat": 6,
        "audio": [], "subs": [], "attachments": 0, "container": "matroska",
    }
    decision = beslis(info, {**STANDAARD, "dv_modus": "av1"})
    assert decision.actie == "overslaan" and "MEL" in decision.reden
    mel = beslis({**info, "dv_el_type": "MEL"}, {**STANDAARD, "dv_modus": "av1"})
    assert mel.actie == "hercoderen" and mel.dv7_omzetten


# ── D: scanner, bestandsfilter, scheduler ───────────────────────────────────


def test_mediamount_met_extra_mapnaam_maakt_niet_alles_extra(tmp_path: Path) -> None:
    root = tmp_path / "data" / "media" / "other"
    film = root / "Film (2020)" / "Film.mkv"
    film.parent.mkdir(parents=True)
    film.write_bytes(b"x" * 10)
    trailer = root / "Film (2020)" / "Trailers" / "t.mkv"
    _ok, _reason, category = is_hoofdbestand(str(film), 0)
    assert category == "extra"  # het oude, foute gedrag zonder bibliotheekroot
    _ok, _reason, category = is_hoofdbestand(str(film), 0, basis=str(root))
    assert category == "ok"
    _ok, _reason, category = is_hoofdbestand(str(trailer), 0, basis=str(root))
    assert category == "extra"
    allowed, _reason = veilig_om_op_te_ruimen(str(film), 500, basis=str(root))
    assert allowed is False


def test_scanner_slaat_verborgen_snapshot_en_symlinkmappen_over(
    native: tuple[Database, Settings, Path],
) -> None:
    from app.scanner import _directories

    _database, _configured, library = native
    for name in (".zfs/snapshot", "@eaDir", ".Trash-1000", "Film"):
        (library / name).mkdir(parents=True)
    (library / "link").symlink_to(library / "Film")
    found = {path.name for path in _directories(library)}
    assert "Film" in found
    assert not found & {".zfs", "snapshot", "@eaDir", ".Trash-1000", "link"}


async def test_scheduler_ziet_lockconflict_niet_als_mislukking(
    native: tuple[Database, Settings, Path],
) -> None:
    database, _configured, library = native
    profile_id = database.execute_insert(
        "INSERT INTO profiel(naam,inst) VALUES (?,?)", ("S", json.dumps(STANDAARD))
    )
    library_id = database.execute_insert(
        "INSERT INTO library(naam,pad,profiel_id,scan_interval,volgende_scan) "
        "VALUES (?,?,?,86400,?)",
        ("S", str(library), profile_id, "2000-01-01T00:00:00+00:00"),
    )

    def busy(_database: Database, _library_id: int) -> Any:
        raise ScanBusy("Er draait al een bibliotheekscan")

    scheduler = LibraryScanScheduler(database, busy)
    assert await scheduler.tick() == []
    row = database.fetch_one(
        "SELECT laatste_scan_status,volgende_scan FROM library WHERE id=?", (library_id,)
    )
    assert row is not None and row["laatste_scan_status"] != "mislukt"
    retry = datetime.fromisoformat(str(row["volgende_scan"]))
    assert retry - datetime.now(UTC) < timedelta(minutes=10)
    assert database.fetch_one("SELECT 1 FROM scan_run WHERE status='mislukt'") is None


def test_herstart_schuift_een_gepland_scanmoment_niet_op(
    native: tuple[Database, Settings, Path],
) -> None:
    database, _configured, library = native
    profile_id = database.execute_insert(
        "INSERT INTO profiel(naam,inst) VALUES (?,?)", ("S", json.dumps(STANDAARD))
    )
    past = "2000-01-01T03:00:00+00:00"
    library_id = database.execute_insert(
        "INSERT INTO library(naam,pad,profiel_id,scan_interval,volgende_scan) "
        "VALUES (?,?,?,86400,?)",
        ("S", str(library), profile_id, past),
    )
    scheduler = LibraryScanScheduler(database)
    scheduler.initialize_schedule()
    row = database.fetch_one("SELECT volgende_scan FROM library WHERE id=?", (library_id,))
    assert row is not None and row["volgende_scan"] == past  # wordt nu ingehaald
    scheduler.initialize_schedule(keep_existing=False)
    row = database.fetch_one("SELECT volgende_scan FROM library WHERE id=?", (library_id,))
    assert row is not None and row["volgende_scan"] != past


# ── D: onderhoud, hostgrenzen, config ───────────────────────────────────────


def test_onbereikbare_opslag_overschrijft_geen_handmatige_pauze(
    native: tuple[Database, Settings, Path],
) -> None:
    database, configured, _library = native
    database.execute(
        "UPDATE worker_config SET gepauzeerd=1,pauze_reden='handmatig gepauzeerd' "
        "WHERE baan='gpu'"
    )
    shutil.rmtree(configured.cache_root)
    storage_guard(database, configured)
    gpu = database.fetch_one("SELECT pauze_reden FROM worker_config WHERE baan='gpu'")
    cpu = database.fetch_one("SELECT pauze_reden FROM worker_config WHERE baan='cpu'")
    assert gpu is not None and gpu["pauze_reden"] == "handmatig gepauzeerd"
    assert cpu is not None and str(cpu["pauze_reden"]).startswith("automatisch: opslag")


def test_vacuum_wacht_tot_er_geen_encode_loopt(
    native: tuple[Database, Settings, Path],
) -> None:
    database, _configured, library = native
    source = library / "film.mkv"
    shutil.copy2(TESTDATA / "sdr-meertalig.mkv", source)
    job_id = _job(database, library, source)
    database.execute("UPDATE job SET status='bezig' WHERE id=?", (job_id,))
    assert weekly_database_maintenance(database) is False
    database.execute("UPDATE job SET status='klaar' WHERE id=?", (job_id,))
    assert weekly_database_maintenance(database) is True


def test_cgroup_grenzen_gaan_voor_hostwaarden(tmp_path: Path) -> None:
    (tmp_path / "cpu.max").write_text("200000 100000\n")
    (tmp_path / "memory.max").write_text(str(2 * 1024**3) + "\n")
    (tmp_path / "memory.current").write_text(str(512 * 1024**2) + "\n")
    assert hostlimits.cgroup_cpu_limit(tmp_path) == 2.0
    assert hostlimits.effective_cpu_count(tmp_path) <= 2
    memory = hostlimits.effective_memory(tmp_path)
    assert memory.total <= 2 * 1024**3
    assert memory.available <= int(1.5 * 1024**3)
    (tmp_path / "cpu.max").write_text("max 100000\n")
    assert hostlimits.cgroup_cpu_limit(tmp_path) is None


def test_cpu_temperatuur_negeert_schijven(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    sensors = {
        "nvme": [SimpleNamespace(label="Composite", current=75.0)],
        "coretemp": [
            SimpleNamespace(label="Core 0", current=48.0),
            SimpleNamespace(label="Package id 0", current=52.0),
        ],
    }
    monkeypatch.setattr(hostlimits.psutil, "sensors_temperatures", lambda: sensors)
    assert hostlimits.cpu_temperature() == 52.0
    monkeypatch.setattr(hostlimits.psutil, "sensors_temperatures", lambda: {"nvme": []})
    assert hostlimits.cpu_temperature() is None


def test_containercache_gebruikt_percentage_van_kleine_dataset(tmp_path: Path) -> None:
    container = replace(
        settings, container_mode=True, cache_root=tmp_path, storage_mode="aparte_schijf"
    )
    total_gb = shutil.disk_usage(tmp_path).total / 1024**3
    warning, pause = container.effective_cache_limits(250, 150)
    assert pause <= 150 and warning <= 250 and pause < warning
    if total_gb < 1000:
        assert pause < 150
    # Een bewust gekozen grens blijft staan.
    assert container.effective_cache_limits(400, 300) == (400, 300)


# ── D: legacy-API ───────────────────────────────────────────────────────────


@pytest.fixture
def client(native: tuple[Database, Settings, Path]) -> Iterator[TestClient]:
    database, configured, _library = native
    configured = replace(
        configured,
        secret_key_file=configured.config_root / "secret.key",
        storage_config_path=configured.config_root / "opslag.json",
    )
    configured.secret_key_file.write_bytes(b"api-test-sleutelmateriaal" * 4)
    application = create_app(configured, database, start_planner=False, validate_storage=False)
    with TestClient(application) as test_client:
        yield test_client


def test_workerinstellingen_worden_gevalideerd(client: TestClient) -> None:
    assert client.patch("/api/workers/cpu", json={"stilte_start": "25:99"}).status_code == 422
    assert client.patch("/api/workers/cpu", json={"aantal": 99}).status_code == 422
    assert client.patch("/api/workers/cpu", json={"nice": -5}).status_code == 422
    ok = client.patch("/api/workers/cpu", json={"stilte_start": "23:00", "nice": 10})
    assert ok.status_code == 200 and ok.json()["stilte_start"] == "23:00"


def test_pauzeren_en_hervatten_zijn_gescheiden(client: TestClient) -> None:
    assert client.post("/api/workers/cpu/pauzeren").json()["gepauzeerd"] is True
    assert client.post("/api/workers/cpu/pauzeren").json()["gepauzeerd"] is True
    assert client.post("/api/workers/cpu/hervatten").json()["gepauzeerd"] is False
    assert client.get("/api/modus").json()["beslisser"] == "encoder"


def test_job_baan_wijzigen_is_niet_meer_mogelijk(client: TestClient) -> None:
    response = client.patch("/api/jobs/1", json={"baan": "gpu"})
    assert response.status_code == 422


def test_profielnaam_dubbel_en_canaryconfig_geven_nette_fouten(client: TestClient) -> None:
    first = client.post("/api/profielen", json={"naam": "Een"}).json()
    client.post("/api/profielen", json={"naam": "Twee"})
    duplicate = client.patch(f"/api/profielen/{first['id']}", json={"naam": "Twee"})
    assert duplicate.status_code == 409
    bad = client.patch(
        "/api/functies/canary_limiet",
        json={"ingeschakeld": True, "configuratie": {"max_bestanden": "veel"}},
    )
    assert bad.status_code == 422


def test_websocket_negeert_geen_json(client: TestClient) -> None:
    with client.websocket_connect("/ws") as websocket:
        assert websocket.receive_json()["type"] == "tick"
        websocket.send_text("geen json")
        assert websocket.receive_json()["type"] == "tick"


def test_scantijd_tick_is_een_tijdstempel(client: TestClient) -> None:
    with client.websocket_connect("/ws") as websocket:
        first = websocket.receive_json()
    parsed = datetime.fromisoformat(first["tijd"])
    assert parsed.tzinfo is not None
