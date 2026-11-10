"""Configuratie uit het systemd-omgevingsbestand en de procesomgeving."""

from __future__ import annotations

import json
import math
import os
import shutil
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal

StorageMode = Literal["systeemschijf", "aparte_schijf"]
ReplacementMode = Literal["direct", "handoff"]
BackupLocation = Literal["systeemschijf", "werkruimte"]
STORAGE_CONFIG_VERSION = 1
# Standaard cachegrenzen (GB) zoals migratie 007 ze in `instelling` zet.
DEFAULT_CACHE_PAUSE_GB = 150
DEFAULT_CACHE_WARNING_GB = 250


class ConfigError(RuntimeError):
    """De configuratie is onveilig of onvolledig."""


def _absolute_path(name: str, default: str) -> Path:
    value = Path(os.environ.get(name, default))
    if not value.is_absolute():
        raise ConfigError(f"{name} moet een absoluut pad zijn: {value}")
    return value


def _integer(name: str, default: int, minimum: int = 0) -> int:
    raw = os.environ.get(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} moet een geheel getal zijn") from exc
    if value < minimum:
        raise ConfigError(f"{name} moet minimaal {minimum} zijn")
    return value


def _float(name: str, default: float, minimum: float = 0.0) -> float:
    raw = os.environ.get(name, str(default))
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} moet een getal zijn") from exc
    if value < minimum:
        raise ConfigError(f"{name} moet minimaal {minimum} zijn")
    return value


def _choice(name: str, value: str, allowed: set[str]) -> str:
    if value not in allowed:
        raise ConfigError(f"{name} moet een van deze waarden zijn: {', '.join(sorted(allowed))}")
    return value


def _flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "ja", "on"}:
        return True
    if value in {"0", "false", "no", "nee", "off"}:
        return False
    raise ConfigError(f"{name} moet 0/1 of true/false zijn")


def _media_roots(default: Path) -> tuple[Path, ...]:
    raw = os.environ.get("MG_MEDIA_ROOTS", "")
    roots = tuple(Path(item) for item in raw.split(":") if item.strip()) or (default,)
    for root in roots:
        if not root.is_absolute():
            raise ConfigError(f"MG_MEDIA_ROOTS bevat een relatief pad: {root}")
    return roots


def _storage_document(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"Opslagconfiguratie is niet leesbaar: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ConfigError(f"Opslagconfiguratie moet een JSON-object zijn: {path}")
    allowed = {
        "versie",
        "modus",
        "werkruimte_pad",
        "backups_op",
        "systeemschijf_reserve_gb",
    }
    unknown = set(value) - allowed
    if unknown:
        raise ConfigError(f"Onbekende opslagvelden: {', '.join(sorted(unknown))}")
    if value.get("versie") != STORAGE_CONFIG_VERSION:
        raise ConfigError(f"Niet-ondersteunde opslagconfiguratieversie in {path}")
    return value


@dataclass(slots=True)
class Settings:
    """Niet-geheime procesconfiguratie; bedieningswaarden staan in SQLite."""

    library_root: Path
    pipeline_root: Path
    workspace_root: Path
    cache_root: Path
    work_root: Path
    config_root: Path
    backup_root: Path
    database_path: Path
    secret_key_file: Path
    server_name: str
    timezone: str
    log_level: str
    cache_reserve_factor: float
    max_active_jobs: int
    storage_mode: StorageMode
    backup_location: BackupLocation
    system_disk_reserve_gb: int
    system_device: int
    storage_config_path: Path
    # ── MediaGuard/TrueNAS (defaults = ongewijzigde native Ubuntu-installatie) ──
    # container_mode schakelt Ubuntu-only onderdelen (systemd-, SMB-sockethelper-,
    # LAN-gateway- en systeemschijfaannames) uit zonder ze te verwijderen.
    container_mode: bool = False
    # direct = oude atomische vervanging; handoff = stop bij awaiting_commit in
    # staging, de recovery-worker van MediaGuard core is de enige writer.
    replacement_mode: ReplacementMode = "direct"
    staging_root: Path | None = None
    media_roots: tuple[Path, ...] = ()
    render_node: Path = Path("/dev/dri/renderD128")
    # Door de GPU-proef gekozen opstartwijze (app/media/gpuvarianten.py); None = standaard.
    gpu_variant: dict[str, Any] | None = None

    @classmethod
    def from_env(cls) -> Settings:
        pipeline_root = _absolute_path("PIPELINE_ROOT", "/srv/av1-pipeline")
        config_root = _absolute_path("CONFIG_ROOT", str(pipeline_root / "config"))
        database_url = os.environ.get("DATABASE_URL", f"sqlite:///{config_root / 'app.db'}")
        prefix = "sqlite:///"
        if not database_url.startswith(prefix):
            raise ConfigError("Alleen een lokale SQLite DATABASE_URL is toegestaan")
        database_path = Path(database_url.removeprefix(prefix))
        if not database_path.is_absolute():
            raise ConfigError("DATABASE_URL moet naar een absoluut pad verwijzen")
        storage_config_path = config_root / "opslag.json"
        document = _storage_document(storage_config_path)
        storage_mode = _choice(
            "STORAGE_MODE",
            str(document.get("modus", os.environ.get("STORAGE_MODE", "systeemschijf"))),
            {"systeemschijf", "aparte_schijf"},
        )
        workspace_default = os.environ.get("WORKSPACE_ROOT", str(pipeline_root))
        workspace_root = Path(str(document.get("werkruimte_pad", workspace_default)))
        if not workspace_root.is_absolute():
            raise ConfigError(f"WORKSPACE_ROOT moet een absoluut pad zijn: {workspace_root}")
        backup_location = _choice(
            "BACKUPS_ON",
            str(document.get("backups_op", os.environ.get("BACKUPS_ON", "systeemschijf"))),
            {"systeemschijf", "werkruimte"},
        )
        reserve_raw = document.get(
            "systeemschijf_reserve_gb", os.environ.get("SYSTEM_DISK_RESERVE_GB", "150")
        )
        try:
            system_disk_reserve_gb = int(reserve_raw)
        except (TypeError, ValueError) as exc:
            raise ConfigError("SYSTEM_DISK_RESERVE_GB moet een geheel getal zijn") from exc
        if system_disk_reserve_gb < 150:
            raise ConfigError("SYSTEM_DISK_RESERVE_GB moet minimaal 150 zijn")
        if storage_mode == "systeemschijf":
            workspace_root = pipeline_root
        cache_root = workspace_root / "cache"
        work_root = workspace_root / "work"
        backup_root = (
            pipeline_root / "backups"
            if backup_location == "systeemschijf"
            else workspace_root / "backups"
        )
        container_mode = _flag("MG_CONTAINER_MODE")
        replacement_mode = _choice(
            "AV1_REPLACEMENT_MODE",
            os.environ.get("AV1_REPLACEMENT_MODE", "").strip()
            or ("handoff" if container_mode else "direct"),
            {"direct", "handoff"},
        )
        library_root = _absolute_path("LIBRARY_ROOT", "/library")
        if container_mode:
            # Op TrueNAS zijn /config en /work afzonderlijke datasets. Er is geen
            # 1-TB-systeemschijf om te beschermen; de UI-cachebodem blijft gelden.
            storage_mode = "aparte_schijf"
            workspace_root = pipeline_root
            cache_root = workspace_root / "cache"
            work_root = workspace_root / "work"
            backup_root = config_root / "backups"
        return cls(
            library_root=library_root,
            pipeline_root=pipeline_root,
            workspace_root=workspace_root,
            cache_root=cache_root,
            work_root=work_root,
            config_root=config_root,
            backup_root=backup_root,
            database_path=database_path,
            secret_key_file=_absolute_path("SECRET_KEY_FILE", "/etc/av1-pipeline-secrets.key"),
            server_name=os.environ.get("SERVER_NAME", "AV1 Pipeline"),
            timezone=os.environ.get("TIMEZONE", "Europe/Amsterdam"),
            log_level=os.environ.get("LOG_LEVEL", "INFO").upper(),
            cache_reserve_factor=_float("CACHE_RESERVE_FACTOR", 3.0, 1.0),
            max_active_jobs=_integer("MAX_ACTIVE_JOBS", 2, 1),
            storage_mode=storage_mode,  # type: ignore[arg-type]
            backup_location=backup_location,  # type: ignore[arg-type]
            system_disk_reserve_gb=system_disk_reserve_gb,
            system_device=Path("/").stat().st_dev,
            storage_config_path=storage_config_path,
            container_mode=container_mode,
            replacement_mode=replacement_mode,  # type: ignore[arg-type]
            staging_root=_absolute_path("MG_STAGING_ROOT", str(pipeline_root / "staging")),
            media_roots=_media_roots(library_root),
            render_node=_absolute_path("MG_RENDER_NODE", "/dev/dri/renderD128"),
        )

    @property
    def managed_roots(self) -> tuple[Path, Path, Path, Path]:
        return (self.cache_root, self.work_root, self.config_root, self.backup_root)

    @property
    def hard_free_floor_bytes(self) -> int:
        """Reserveer op de systeemschijf altijd ruimte voor Ubuntu en SQLite."""

        if self.storage_mode != "systeemschijf":
            return 0
        return self.system_disk_reserve_gb * 1024**3

    def effective_cache_limits(self, warning_gb: int, pause_gb: int) -> tuple[int, int]:
        """Een UI-instelling mag de vaste systeemschijfbodem nooit verlagen.

        Containermodus met de standaardgrenzen (150/250 GB): /work is vaak een
        dataset met een kleiner quotum, waarop 150 GB vrij nooit haalbaar is en
        de banen voorgoed pauzeren. Dan geldt 5 % (pauze) en 10 % (waarschuwing)
        van de datasetgrootte, nooit hoger dan de oude standaardwaarden.
        """

        if (
            self.container_mode
            and (warning_gb, pause_gb) == (DEFAULT_CACHE_WARNING_GB, DEFAULT_CACHE_PAUSE_GB)
        ):
            try:
                total_gb = shutil.disk_usage(self.cache_root).total / 1024**3
            except OSError:
                total_gb = 0.0
            if total_gb > 0:
                pause_gb = min(DEFAULT_CACHE_PAUSE_GB, max(1, math.ceil(total_gb * 0.05)))
                warning_gb = min(
                    DEFAULT_CACHE_WARNING_GB, max(pause_gb + 1, math.ceil(total_gb * 0.10))
                )
        effective_pause = (
            max(pause_gb, self.system_disk_reserve_gb)
            if self.storage_mode == "systeemschijf"
            else pause_gb
        )
        return max(warning_gb, effective_pause + 1), effective_pause

    def with_storage(
        self,
        *,
        storage_mode: StorageMode,
        workspace_root: Path,
        backup_location: BackupLocation,
        system_disk_reserve_gb: int,
    ) -> Settings:
        """Maak een volledig afleidbare kandidaat zonder de actieve paden te wijzigen."""

        if storage_mode == "systeemschijf":
            workspace_root = self.pipeline_root
        backup_root = (
            self.pipeline_root / "backups"
            if backup_location == "systeemschijf"
            else workspace_root / "backups"
        )
        return replace(
            self,
            workspace_root=workspace_root,
            cache_root=workspace_root / "cache",
            work_root=workspace_root / "work",
            backup_root=backup_root,
            storage_mode=storage_mode,
            backup_location=backup_location,
            system_disk_reserve_gb=system_disk_reserve_gb,
        )

    def activate_storage(self, candidate: Settings) -> None:
        """Wissel de gevalideerde paden zonder een half toegepaste configuratie."""

        values = (
            candidate.workspace_root,
            candidate.cache_root,
            candidate.work_root,
            candidate.backup_root,
            candidate.storage_mode,
            candidate.backup_location,
            candidate.system_disk_reserve_gb,
        )
        (
            self.workspace_root,
            self.cache_root,
            self.work_root,
            self.backup_root,
            self.storage_mode,
            self.backup_location,
            self.system_disk_reserve_gb,
        ) = values

    @property
    def effective_staging_root(self) -> Path:
        return self.staging_root or self.pipeline_root / "staging"

    def validate_container_storage(self) -> None:
        """TrueNAS-containermodus: datasets i.p.v. systeemschijf, fail-closed.

        /config en /work moeten bestaan, schrijfbaar zijn en geen symlinks
        bevatten; minstens één read-only mediaroot moet leesbaar zijn; werk- en
        mediapaden mogen niet in elkaar liggen. Eigenaarschap volgt de
        TrueNAS-dataset-ACL, dus geen uid-gelijkheidseis zoals op Ubuntu.
        """

        required = (
            self.config_root,
            self.cache_root,
            self.work_root,
            self.backup_root,
            self.effective_staging_root,
        )
        for path in required:
            if not path.is_dir():
                raise ConfigError(f"Vereiste containermap ontbreekt: {path}")
            if path.resolve(strict=True) != path:
                raise ConfigError(f"Containermap mag geen symlink zijn: {path}")
            if not os.access(path, os.R_OK | os.W_OK | os.X_OK):
                raise ConfigError(f"Containermap is niet lees- en schrijfbaar: {path}")
        readable = []
        for root in self.media_roots:
            if root.is_dir() and os.access(root, os.R_OK | os.X_OK):
                readable.append(root.resolve())
        if not readable:
            raise ConfigError(
                "Geen leesbare mediamount gevonden ("
                + ", ".join(str(root) for root in self.media_roots)
                + "); encodes blijven gestopt"
            )
        work = self.pipeline_root.resolve()
        for root in readable:
            if work.is_relative_to(root) or root.is_relative_to(work):
                raise ConfigError("Werkruimte en mediabibliotheek mogen niet in elkaar liggen")

    def validate_storage(self) -> None:
        """Valideer systeem- of werkschijf zonder een ontbrekende mount te maskeren."""

        if self.container_mode:
            self.validate_container_storage()
            return
        if self.pipeline_root in {Path("/"), self.library_root}:
            raise ConfigError("PIPELINE_ROOT mag nooit / of de library-root zijn")
        try:
            pipeline = self.pipeline_root.resolve(strict=True)
            workspace = self.workspace_root.resolve(strict=True)
            library = self.library_root.resolve(strict=True)
        except OSError as exc:
            raise ConfigError(f"Opslagpad is niet toegankelijk: {exc}") from exc
        if pipeline != self.pipeline_root or workspace != self.workspace_root:
            raise ConfigError("Pipeline- en werkruimtepad mogen geen symlink bevatten")
        if pipeline.stat().st_dev != self.system_device:
            raise ConfigError("Configuratie en database moeten op de systeemschijf blijven")
        if self.storage_mode == "systeemschijf":
            if workspace != pipeline or workspace.stat().st_dev != self.system_device:
                raise ConfigError("Systeemschijfmodus vereist de vaste lokale pipeline-map")
        elif not workspace.is_mount() or workspace.stat().st_dev == self.system_device:
            raise ConfigError(
                "Aparte-schijfmodus vereist een echt gemount, afzonderlijk filesystem"
            )
        if workspace.is_relative_to(library) or library.is_relative_to(workspace):
            raise ConfigError("Werkruimte en mediabibliotheek mogen niet in elkaar liggen")
        expected = {
            self.workspace_root / "cache",
            self.workspace_root / "work",
            self.pipeline_root / "config",
            (
                self.pipeline_root / "backups"
                if self.backup_location == "systeemschijf"
                else self.workspace_root / "backups"
            ),
        }
        if set(self.managed_roots) != expected:
            raise ConfigError("Opslagpaden wijken af van de gekozen veilige indeling")
        for path in self.managed_roots:
            if not path.is_dir():
                raise ConfigError(f"Vereiste opslagmap ontbreekt: {path}")
            if path.resolve(strict=True) != path:
                raise ConfigError(f"Opslagmap mag geen symlink zijn: {path}")
            if path.stat().st_uid != os.geteuid():
                raise ConfigError(f"Opslagmap heeft niet de backendgebruiker als eigenaar: {path}")
            if not os.access(path, os.R_OK | os.W_OK | os.X_OK):
                raise ConfigError(f"Opslagmap is niet lees- en schrijfbaar: {path}")


settings = Settings.from_env()
