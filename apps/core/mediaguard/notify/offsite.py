"""Herstelbundel buiten de NAS bewaren (rapport 5, M11).

Na elke nieuwe herstelbundel kopieert MediaGuard die (plus het versleutelde sleutelbestand,
dat zonder herstelcode onbruikbaar is) automatisch naar het gekozen doel: een andere
gekoppelde map (bijv. een back-upshare), WebDAV (Nextcloud, Stack, …), S3-compatibele opslag
(Backblaze B2, Wasabi, MinIO, …) of als e-mailbijlage. Elke kopie wordt teruggelezen en
op SHA-256 vergeleken. Alles is in de app in te stellen; geheimen staan versleuteld.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import io
import os
import re
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import quote, urlparse

import httpx

from mediaguard.db import utcnow
from mediaguard.maintenance import bundle
from mediaguard.secrets_store import redact
from mediaguard.settings_registry import REGISTRY, SettingDef

if TYPE_CHECKING:
    from mediaguard.context import AppContext

CATEGORY = "Back-ups"
KINDS = ("off", "path", "webdav", "s3", "email")
KIND_LABELS_NL = {"off": "Uit", "path": "Andere map (share)", "webdav": "WebDAV",
                  "s3": "S3-compatibel", "email": "E-mail"}
SECRET_FIELDS = {"webdav": ("webdav_password",), "s3": ("s3_secret_key",)}
EMAIL_MAX = 20 * 1024 * 1024
KEYFILE = bundle.KEYFILE
BUNDLE_NAME = re.compile(r"mediaguard-bundel-(\d{8}T\d{6}Z)\.mgbundle")
_SHA256 = re.compile(r"[0-9a-f]{64}")
READBACK_KINDS = ("path", "webdav", "s3")


def register_settings() -> None:
    defs = [
        SettingDef("backup.offsite.kind", "enum", "off", CATEGORY,
                   "Herstelbundel ook buiten de NAS bewaren", "Off-NAS bundle copy",
                   choices=KINDS, choice_labels_nl=dict(KIND_LABELS_NL),
                   help_nl="Na elke nieuwe herstelbundel maakt MediaGuard een gecontroleerde "
                           "kopie op dit doel."),
        SettingDef("backup.offsite.path", "string", "", CATEGORY, "Map voor de kopie",
                   "Copy folder", help_nl="Een gekoppelde map in de container, bijv. "
                                          "/backup/mediaguard."),
        SettingDef("backup.offsite.webdav_url", "string", "", CATEGORY, "WebDAV-adres",
                   "WebDAV URL"),
        SettingDef("backup.offsite.webdav_user", "string", "", CATEGORY, "WebDAV-gebruiker",
                   "WebDAV user"),
        SettingDef("backup.offsite.s3_endpoint", "string", "", CATEGORY, "S3-adres",
                   "S3 endpoint"),
        SettingDef("backup.offsite.s3_region", "string", "us-east-1", CATEGORY, "S3-regio",
                   "S3 region"),
        SettingDef("backup.offsite.s3_bucket", "string", "", CATEGORY, "S3-bucket",
                   "S3 bucket"),
        SettingDef("backup.offsite.s3_prefix", "string", "mediaguard/", CATEGORY,
                   "S3-map (prefix)", "S3 prefix"),
        SettingDef("backup.offsite.s3_access_key", "string", "", CATEGORY,
                   "S3-toegangssleutel (access key)", "S3 access key"),
        SettingDef("backup.offsite.email_channel", "int", 0, CATEGORY,
                   "E-mailkanaal voor de bijlage", "E-mail channel", minimum=0,
                   help_nl="Een e-mailkanaal uit Instellingen → Meldingen."),
        SettingDef("backup.offsite.keep", "int", 7, CATEGORY, "Aantal kopieën bewaren",
                   "Copies to keep", minimum=1, maximum=100,
                   help_nl="Alleen bij een map: oudere kopieën worden opgeruimd."),
    ]
    known = {x.key for x in REGISTRY.all()}
    for d in defs:
        if d.key not in known:
            REGISTRY.register(d)


class OffsiteError(Exception):
    pass


def _v(ctx: AppContext, key: str) -> Any:
    return ctx.config.value(f"backup.offsite.{key}")


def secret_name(field: str) -> str:
    return f"backup.offsite.{field}"


def generation(ctx: AppContext) -> str | None:
    try:
        row = ctx.db.fetch_one("SELECT generation FROM offsite_target_generation WHERE id=1")
    except sqlite3.OperationalError as exc:
        if str(exc) != "no such table: offsite_target_generation":
            raise
        return None  # Direct herstelde kernkopie van vóór de verificatiemigratie.
    return row[0] if row else None


def config(ctx: AppContext) -> dict[str, Any]:
    kind = str(_v(ctx, "kind"))
    out: dict[str, Any] = {"kind": kind, "kind_nl": KIND_LABELS_NL.get(kind, kind)}
    for key in ("path", "webdav_url", "webdav_user", "s3_endpoint", "s3_region", "s3_bucket",
                "s3_prefix", "s3_access_key", "email_channel", "keep"):
        out[key] = _v(ctx, key)
    out["secrets_set"] = {f: ctx.secrets.has(secret_name(f))
                          for fields in SECRET_FIELDS.values() for f in fields}
    return out


def target_label(ctx: AppContext) -> str:
    kind = str(_v(ctx, "kind"))
    if kind == "path":
        return str(_v(ctx, "path"))
    if kind == "webdav":
        return _strip_auth(str(_v(ctx, "webdav_url")))
    if kind == "s3":
        return f"{_v(ctx, 's3_bucket')}/{_v(ctx, 's3_prefix')}"
    if kind == "email":
        return f"kanaal {_v(ctx, 'email_channel')}"
    return ""


def _strip_auth(url: str) -> str:
    p = urlparse(url)
    return p._replace(netloc=p.hostname or "").geturl() if p.username else url


def _latest_bundle(ctx: AppContext) -> tuple[Path, Path | None] | None:
    folder = bundle.bundle_dir(ctx)
    files = sorted(folder.glob("mediaguard-bundel-*.mgbundle"))
    if not files:
        return None
    key = folder / KEYFILE
    return files[-1], key if key.exists() else None


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def status(ctx: AppContext) -> dict[str, Any]:
    rows = ctx.db.fetch_dicts("SELECT * FROM offsite_copies ORDER BY id DESC LIMIT 10")
    latest = _latest_bundle(ctx)
    last_ok = next((r for r in rows if r["status"] == "ok"), None)
    pending = bool(latest and str(_v(ctx, "kind")) != "off" and
                   (last_ok is None or last_ok["bundle_name"] != latest[0].name))
    return {"config": config(ctx), "target": target_label(ctx), "history": rows,
            "last_ok": last_ok, "latest_bundle": latest[0].name if latest else None,
            "pending": pending}


async def maybe_run(ctx: AppContext) -> dict[str, Any] | None:
    """Kopieer de nieuwste bundel als die nog niet op het huidige doel staat."""
    kind = str(_v(ctx, "kind"))
    if kind == "off":
        return None
    latest = _latest_bundle(ctx)
    if latest is None:
        return None
    target = target_label(ctx)
    current = generation(ctx)
    if current is None:
        return None
    done = ctx.db.fetch_one(
        "SELECT 1 FROM offsite_copies WHERE bundle_name=? AND target_kind=? AND target=? "
        "AND status='ok' AND (EXISTS (SELECT 1 FROM offsite_verification "
        "WHERE copy_id=offsite_copies.id AND generation=?) OR ?)",
        (latest[0].name, kind, target, current,
         kind == "email" or not bundle.has_pair_proof(latest[0])),
    )
    if done is not None:
        return None
    # na een mislukte poging pas na een uur opnieuw
    recent_fail = ctx.db.fetch_one(
        "SELECT created_at FROM offsite_copies WHERE bundle_name=? AND target_kind=? AND "
        "status='failed' ORDER BY id DESC LIMIT 1", (latest[0].name, kind))
    if recent_fail is not None:
        age = datetime.now(UTC) - datetime.fromisoformat(recent_fail["created_at"])
        if age.total_seconds() < 3600:
            return None
    return await run(ctx)


async def run(ctx: AppContext, *, actor: str = "svc:offsite") -> dict[str, Any]:
    current = generation(ctx)
    if current is None:
        return {"ok": False, "error": "Het doel kan pas na de herstart worden geverifieerd"}
    ctx.config.invalidate()
    kind = str(_v(ctx, "kind"))
    latest = _latest_bundle(ctx)
    if kind == "off":
        return {"ok": False, "error": "Geen doel ingesteld"}
    if latest is None:
        return {"ok": False, "error": "Er is nog geen herstelbundel om te kopiëren"}
    bundle_path, keyfile = latest
    target = target_label(ctx)
    expected = None
    try:
        if keyfile is not None:
            _, expected = await asyncio.to_thread(bundle.verify_pair, bundle_path, keyfile,
                                                  require_content=False)
        if bundle.has_pair_proof(bundle_path) and expected is None:
            raise OffsiteError("Aanmaakbewijs van bundel en herstelsleutel klopt niet")
        if keyfile is None or keyfile.is_symlink() or bundle_path.is_symlink():
            raise OffsiteError("Bundel of herstelsleutel ontbreekt of is geen regulier bestand")
        files = await asyncio.to_thread(_copies, bundle_path, keyfile, expected)
        destination = _destination(ctx, kind) if kind in READBACK_KINDS else None
        if current != generation(ctx):
            raise OffsiteError("Het offsite-doel is gewijzigd")
        if isinstance(destination, Path):
            await asyncio.to_thread(_copy_path, destination, files, int(_v(ctx, "keep")))
        elif isinstance(destination, _Remote):
            await _transfer_http(ctx, destination, files, upload=True)
        elif kind == "email":
            digest = await _copy_email(ctx, bundle_path, keyfile)
            if digest != files[0].sha256:
                raise OffsiteError("Bronbestand is gewijzigd")
        else:
            raise OffsiteError(f"Onbekend doel: {kind}")
        if current != generation(ctx):
            raise OffsiteError("Het offsite-doel is gewijzigd")
    except (OffsiteError, OSError, httpx.HTTPError) as exc:
        detail = redact(str(exc) or type(exc).__name__)
        ctx.db.execute_insert(
            "INSERT INTO offsite_copies(bundle_name,target_kind,target,status,detail,created_at)"
            " VALUES (?,?,?,?,?,?)", (bundle_path.name, kind, target, "failed", detail, utcnow()))
        from mediaguard.api.system import notify

        notify(ctx, "warning", "Herstelbundel niet buiten de NAS gekopieerd",
               f"{KIND_LABELS_NL.get(kind, kind)}: {detail}. MediaGuard probeert het over een "
               "uur opnieuw.", link="/systeem#back-ups", dedupe_key="backup.offsite.failed")
        return {"ok": False, "error": detail}
    primary, key = files
    size, digest = primary.size, primary.sha256
    with ctx.db.transaction() as db:
        copy = db.execute(
            "INSERT INTO offsite_copies(bundle_name,target_kind,target,status,size,sha256,detail,"
            "created_at) VALUES (?,?,?,?,?,?,?,?)",
            (bundle_path.name, kind, target, "ok", size, digest,
             "Gekopieerd en gecontroleerd" if destination is not None else "Verzonden", utcnow()),
        )
        if destination is not None and expected is not None:
            db.execute(
                "INSERT INTO offsite_verification(copy_id,generation,key_sha256,key_size) "
                "SELECT ?,generation,?,? FROM offsite_target_generation WHERE generation=?",
                (copy.lastrowid, key.sha256, key.size, current),
            )
    from mediaguard.api.system import resolve_notifications
    from mediaguard.audit import audit

    resolve_notifications(ctx, "backup.offsite.failed")
    audit(ctx.db, actor=actor, action="backup.offsite.copied", target_type="backup",
          target_id=bundle_path.name, new_value={"kind": kind, "target": target, "size": size})
    return {"ok": True, "bundle": bundle_path.name, "size": size, "sha256": digest}


# -- doelen -------------------------------------------------------------------------------------
@dataclass(frozen=True)
class _Copy:
    path: Path
    sha256: str
    size: int


def _copies(path: Path, keyfile: Path, expected: dict[str, str] | None) -> tuple[_Copy, _Copy]:
    def describe(source: Path, label: str) -> _Copy:
        digest = expected[label] if expected is not None else _sha(source)
        return _Copy(source, digest, source.stat().st_size)

    return describe(path, "bundle"), describe(keyfile, "key")


def _verify_path(dest: Path, files: tuple[_Copy, _Copy], *, require_complete: bool = False) -> None:
    for copy in files:
        path = dest / copy.path.name
        if path.is_symlink() or not path.is_file() or path.stat().st_size != copy.size:
            raise OffsiteError("Controle mislukt: teruggelezen kopie wijkt af")
        with path.open("rb") as handle:
            if require_complete and copy is files[0]:
                bundle.require_complete(handle)
                handle.seek(0)
            if hashlib.file_digest(handle, "sha256").hexdigest() != copy.sha256:
                raise OffsiteError("Controle mislukt: teruggelezen kopie wijkt af")


def _copy_path(dest: Path, files: tuple[_Copy, _Copy], keep: int) -> None:
    for copy in files:
        with tempfile.NamedTemporaryFile(dir=dest, delete=False) as handle:
            tmp = Path(handle.name)
            try:
                with copy.path.open("rb") as source:
                    shutil.copyfileobj(source, handle)
                handle.flush()
                os.fsync(handle.fileno())
                if tmp.stat().st_size != copy.size or _sha(tmp) != copy.sha256:
                    raise OffsiteError("Bronbestand is gewijzigd")
                tmp.replace(dest / copy.path.name)
            finally:
                tmp.unlink(missing_ok=True)
    _verify_path(dest, files)
    for old in sorted(dest.glob("mediaguard-bundel-*.mgbundle"))[:-keep]:
        old.unlink(missing_ok=True)


def _client(ctx: AppContext) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=httpx.Timeout(300, connect=10),
                             transport=ctx.services.get("notify_transport"))


def _sign(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode(), hashlib.sha256).digest()


def sigv4_headers(method: str, url: str, body: bytes, *, access_key: str, secret_key: str,
                  region: str, now: datetime | None = None) -> dict[str, str]:
    """AWS Signature V4 (path-style) voor S3-compatibele opslag."""
    p = urlparse(url)
    t = now or datetime.now(UTC)
    amz_date, day = t.strftime("%Y%m%dT%H%M%SZ"), t.strftime("%Y%m%d")
    payload_hash = hashlib.sha256(body).hexdigest()
    host = p.netloc
    canonical_headers = f"host:{host}\nx-amz-content-sha256:{payload_hash}\nx-amz-date:{amz_date}\n"
    signed = "host;x-amz-content-sha256;x-amz-date"
    canonical = "\n".join([method, p.path or "/", p.query, canonical_headers, signed,
                           payload_hash])
    scope = f"{day}/{region}/s3/aws4_request"
    to_sign = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope,
                         hashlib.sha256(canonical.encode()).hexdigest()])
    k = _sign(_sign(_sign(_sign(f"AWS4{secret_key}".encode(), day), region), "s3"),
              "aws4_request")
    signature = hmac.new(k, to_sign.encode(), hashlib.sha256).hexdigest()
    return {"x-amz-date": amz_date, "x-amz-content-sha256": payload_hash,
            "Authorization": f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, "
                             f"SignedHeaders={signed}, Signature={signature}"}


@dataclass(frozen=True)
class _Remote:
    base: str
    auth: tuple[str, str] | None = None
    access: str = ""
    secret: str = ""
    region: str = ""

    def headers(self, method: str, url: str, body: bytes) -> dict[str, str]:
        if self.auth is not None:
            return {}
        return sigv4_headers(method, url, body, access_key=self.access,
                             secret_key=self.secret, region=self.region)


def _destination(ctx: AppContext, kind: str) -> Path | _Remote:
    if kind == "path":
        dest = Path(str(_v(ctx, "path")).strip())
        if not dest.is_absolute():
            raise OffsiteError("Kies een map (een volledig pad dat met / begint)")
        if dest.resolve() == bundle.bundle_dir(ctx).resolve():
            raise OffsiteError("Dit is dezelfde map als de herstelbundels zelf")
        if not dest.is_dir():
            raise OffsiteError("De gekozen map bestaat niet (koppel eerst een share in de app)")
        return dest
    if kind == "webdav":
        base = str(_v(ctx, "webdav_url")).strip().rstrip("/")
        if not base.startswith(("https://", "http://")):
            raise OffsiteError("Vul een WebDAV-adres in (https://…)")
        auth = (str(_v(ctx, "webdav_user")), ctx.secrets.get(secret_name("webdav_password")) or "")
        return _Remote(base, auth=auth)
    endpoint = str(_v(ctx, "s3_endpoint")).strip().rstrip("/")
    bucket = str(_v(ctx, "s3_bucket")).strip()
    access = str(_v(ctx, "s3_access_key")).strip()
    secret = ctx.secrets.get(secret_name("s3_secret_key")) or ""
    if not (endpoint.startswith(("https://", "http://")) and bucket and access and secret):
        raise OffsiteError("Vul S3-adres, bucket, toegangssleutel en geheime sleutel in")
    region = str(_v(ctx, "s3_region") or "us-east-1")
    prefix = str(_v(ctx, "s3_prefix") or "").strip("/")
    base = f"{endpoint}/{bucket}" + (f"/{quote(prefix)}" if prefix else "")
    return _Remote(base, access=access, secret=secret, region=region)


async def _transfer_http(ctx: AppContext, dest: _Remote, files: tuple[_Copy, _Copy], *,
                         upload: bool = False, require_complete: bool = False) -> None:
    async with _client(ctx) as client:
        for copy in files:
            url = f"{dest.base}/{quote(copy.path.name)}"
            if upload:
                body = await asyncio.to_thread(copy.path.read_bytes)
                if len(body) != copy.size or hashlib.sha256(body).hexdigest() != copy.sha256:
                    raise OffsiteError("Bronbestand is gewijzigd")
                put = await client.put(url, content=body, auth=dest.auth,
                                       headers=dest.headers("PUT", url, body))
                if put.status_code not in (200, 201, 204):
                    raise OffsiteError(f"Offsite-doel weigerde upload ({put.status_code})")
            digest = hashlib.sha256()
            size = 0
            prefix = bytearray() if require_complete and copy is files[0] else None
            async with client.stream("GET", url, auth=dest.auth,
                                     headers=dest.headers("GET", url, b"")) as got:
                if got.status_code != 200:
                    raise OffsiteError("Offsite-bestand kan niet worden teruggelezen")
                async for chunk in got.aiter_bytes():
                    size += len(chunk)
                    if size > copy.size:
                        raise OffsiteError("Controle mislukt: teruggelezen kopie is te groot")
                    digest.update(chunk)
                    if prefix is not None:
                        remaining = len(bundle.MAGIC) + 4 + bundle.MAX_HEADER - len(prefix)
                        prefix.extend(chunk[:remaining])
            if size != copy.size or digest.hexdigest() != copy.sha256:
                raise OffsiteError("Controle mislukt: teruggelezen kopie wijkt af")
            if prefix is not None:
                bundle.require_complete(io.BytesIO(prefix))


async def verified_copy(ctx: AppContext, kind: str) -> dict[str, Any] | None:
    """Bewijs voor de huidige inrichting en herlezing van exact dezelfde twee bestanden."""
    if kind not in READBACK_KINDS:
        return None
    current = generation(ctx)
    if current is None:
        return None
    ctx.config.invalidate()
    if _v(ctx, "kind") != kind:
        return None
    row = ctx.db.fetch_one(
        "SELECT c.bundle_name,c.created_at,c.size,c.sha256,v.key_size,v.key_sha256 "
        "FROM offsite_copies AS c JOIN offsite_verification AS v ON v.copy_id=c.id "
        "WHERE c.status='ok' AND c.target_kind=? AND c.target=? AND v.generation=? "
        "ORDER BY c.id DESC LIMIT 1", (kind, target_label(ctx), current),
    )
    if row is None or not BUNDLE_NAME.fullmatch(row["bundle_name"]):
        return None
    files = (_Copy(Path(row["bundle_name"]), row["sha256"], row["size"]),
             _Copy(Path(KEYFILE), row["key_sha256"], row["key_size"]))
    if any(type(f.size) is not int or f.size <= 0 or not isinstance(f.sha256, str)
           or not _SHA256.fullmatch(f.sha256) for f in files):
        return None
    try:
        dest = _destination(ctx, kind)
        if current != generation(ctx):
            return None
        if isinstance(dest, Path):
            await asyncio.to_thread(_verify_path, dest, files, require_complete=True)
        else:
            async with asyncio.timeout(10):
                await _transfer_http(ctx, dest, files, require_complete=True)
    except (OffsiteError, bundle.BundleError, OSError, httpx.HTTPError, ValueError, TimeoutError):
        return None
    return dict(row) if current == generation(ctx) else None


async def _copy_email(ctx: AppContext, bundle_path: Path, keyfile: Path | None) -> str:
    from mediaguard.notify import channels as ch
    from mediaguard.notify import service

    channel = service.get_channel(ctx, int(_v(ctx, "email_channel") or 0))
    if channel is None or channel["kind"] != "email":
        raise OffsiteError("Kies een e-mailkanaal (Instellingen → Meldingen)")
    if bundle_path.stat().st_size > EMAIL_MAX:
        raise OffsiteError("Bundel is te groot voor e-mail (meer dan 20 MB); kies een map, "
                           "WebDAV of S3")
    digest = _sha(bundle_path)
    msg = ch.Message(title=f"Herstelbundel {bundle_path.name}",
                     body="Bewaar deze bijlage buiten je NAS. Je hebt de herstelcode nodig om "
                          f"hem te openen. SHA-256: {digest}", attachment=bundle_path)
    try:
        await ch.send(ctx, channel, msg)
        if keyfile is not None:  # zonder sleutelbestand is de bundel niet te openen
            await ch.send(ctx, channel, ch.Message(
                title="Herstelsleutel (versleuteld)",
                body="Hoort bij je herstelbundels; alleen te gebruiken met je herstelcode.",
                attachment=keyfile))
    except ch.ChannelError as exc:
        raise OffsiteError(f"E-mail: {exc}") from exc
    return digest
