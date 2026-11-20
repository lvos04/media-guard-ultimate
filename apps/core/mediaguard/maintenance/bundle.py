"""Versleutelde herstelbundel: alles wat nodig is om MediaGuard op een nieuwe schijf terug
te zetten, ook de sleutels.

Wat erin zit:
- een consistente kopie van de core-database (SQLite backup-API);
- de database(s) van de AV1-engine (``/config/encode/*.db``);
- ``secret.key`` (ontsleutelt de opgeslagen wachtwoorden/API-sleutels);
- de map met gegenereerde sleutels (``/config/secrets``).

Ontwerp (veilig én zonder dat iemand 's nachts een wachtwoord hoeft in te typen):
- Bij het aanzetten maakt MediaGuard een **herstelcode** (eenmalig getoond, nergens
  bewaard) en een X25519-sleutelpaar. De **publieke** sleutel blijft in MediaGuard; de
  **privésleutel** wordt met de herstelcode versleuteld (scrypt + AES-GCM) en naast de
  bundels bewaard.
- Elke bundel krijgt een eigen willekeurige sleutel (AES-256-GCM); die sleutel wordt voor
  de publieke sleutel ingepakt (X25519 + HKDF). Nachtelijk versleutelen kan dus altijd;
  **openen kan alleen met de herstelcode**, ook als iemand de hele back-upmap heeft.
- Herstellen: ``python -m mediaguard.maintenance.bundle restore`` (zie
  docs/DATA_RECOVERY.md); werkt zonder draaiende MediaGuard.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
import secrets
import sqlite3
import sys
import tarfile
import tempfile
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from mediaguard.maintenance import preupdate
from mediaguard.schema import SchemaTooNew

if TYPE_CHECKING:
    from mediaguard.context import AppContext

MAGIC = b"MGBUNDLE1\n"
MAX_HEADER = 1024 * 1024
STATE_KEY = "backup.bundle"
KEYFILE = "herstelsleutel.json"
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # geen 0/O/1/I


# -- sleutels -------------------------------------------------------------------------------
def new_recovery_code() -> str:
    raw = "".join(secrets.choice(CODE_ALPHABET) for _ in range(24))
    return "-".join(raw[i:i + 4] for i in range(0, 24, 4))


def _norm_code(code: str) -> bytes:
    return "".join(c for c in code.upper() if c.isalnum()).encode()


def _code_key(code: str, salt: bytes) -> bytes:
    return Scrypt(salt=salt, length=32, n=2 ** 15, r=8, p=1).derive(_norm_code(code))


def setup(ctx: AppContext, actor: str) -> dict[str, Any]:
    """Nieuwe herstelcode + sleutelpaar. De code wordt alleen nu teruggegeven."""
    with ctx.db.startup_lock():
        ctx.db.require_writable()
        return _setup(ctx, actor)


def _setup(ctx: AppContext, actor: str) -> dict[str, Any]:
    code = new_recovery_code()
    priv = X25519PrivateKey.generate()
    pub = priv.public_key().public_bytes(serialization.Encoding.Raw,
                                         serialization.PublicFormat.Raw)
    salt, nonce = os.urandom(16), os.urandom(12)
    priv_raw = priv.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                                  serialization.NoEncryption())
    wrapped = AESGCM(_code_key(code, salt)).encrypt(nonce, priv_raw, b"mediaguard-bundle-key")
    keyfile = {"v": 1, "salt": _b64(salt), "nonce": _b64(nonce), "wrapped": _b64(wrapped),
               "public": _b64(pub), "created_at": _now()}
    folder = bundle_dir(ctx)
    folder.mkdir(parents=True, exist_ok=True)
    raw_key = json.dumps(keyfile, indent=2).encode()
    _publish(folder / KEYFILE, raw_key)
    ctx.state_set(STATE_KEY, {"public": _b64(pub), "enabled": True, "created_at": _now(),
                              "key_sha256": _digest(raw_key),
                              "fingerprint": hashlib.sha256(pub).hexdigest()[:16]})
    from mediaguard.audit import audit

    audit(ctx.db, actor=actor, action="backup.bundle.setup", target_type="backup",
          target_id="bundle", new_value={"fingerprint": hashlib.sha256(pub).hexdigest()[:16]})
    return {"recovery_code": code, "fingerprint": hashlib.sha256(pub).hexdigest()[:16]}


def status(ctx: AppContext) -> dict[str, Any]:
    st = ctx.state_get(STATE_KEY, {}) or {}
    files = sorted(bundle_dir(ctx).glob("mediaguard-bundel-*.mgbundle"), reverse=True)
    return {"enabled": bool(st.get("enabled") and st.get("public")),
            "fingerprint": st.get("fingerprint"), "created_at": st.get("created_at"),
            "bundles": [{"file": f.name, "size": f.stat().st_size} for f in files[:10]]}


# -- maken ------------------------------------------------------------------------------------
def bundle_dir(ctx: AppContext) -> Path:
    return ctx.settings.backup_dir / "herstelbundel"


def _publish(path: Path, data: bytes) -> None:
    with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".tmp", delete=False) as handle:
        tmp = Path(handle.name)
    try:
        if tmp.write_bytes(data) != len(data):
            raise OSError("Herstelbestand onvolledig weggeschreven")
        with tmp.open("rb") as handle:
            os.fsync(handle.fileno())
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


def _config_root(ctx: AppContext) -> Path:
    return Path(os.environ.get("MG_AIO_CONFIG") or ctx.settings.data_dir.parent)


def _sqlite_copy(src: Path, *, primary: bool) -> tuple[bytes, bool]:
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "copy.db"
        with closing(sqlite3.connect(src.resolve().as_uri() + "?mode=ro", uri=True)) as s, \
                closing(sqlite3.connect(dest)) as d:
            s.backup(d)
            try:
                preupdate.encoder_copy_versions(d, preupdate.encoder_migrations(os.environ),
                                               primary=primary)
                usable = d.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
            except (sqlite3.Error, RuntimeError, preupdate.SetError):
                usable = False
        return dest.read_bytes(), usable


def _core_readable(core: Path, key: bytes | None) -> bool:
    """Toets de versleutelde rijen met precies de sleutelbytes die meegaan in het archief."""
    with closing(sqlite3.connect(core.as_uri() + "?immutable=1", uri=True)) as db:
        try:
            if db.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                return False
            cipher = Fernet(key.strip()) if key is not None else None
            for (token,) in db.execute("SELECT ciphertext FROM secrets"):
                if cipher is None:
                    return False
                cipher.decrypt(token).decode("utf-8")
        except (InvalidToken, ValueError, TypeError, sqlite3.Error):
            return False
    return True


def _add(tar: tarfile.TarFile, name: str, data: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size, info.mtime, info.mode = len(data), int(datetime.now(UTC).timestamp()), 0o600
    tar.addfile(info, io.BytesIO(data))


def _payload(ctx: AppContext) -> tuple[bytes, list[str], bool]:
    buf, names = io.BytesIO(), []
    key_path = ctx.settings.secret_key_path
    key = key_path.read_bytes() if key_path.exists() else None
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        with tempfile.TemporaryDirectory() as tmp:
            core = Path(tmp) / "core.db"
            ctx.db.backup_to(core)
            usable = _core_readable(core, key)
            _add(tar, "core/mediaguard.db", core.read_bytes())
            names.append("core/mediaguard.db")
        root = _config_root(ctx)
        encoder = preupdate.encoder_backup_dir(ctx.settings.data_dir)
        primary = preupdate.primary_encoder_db(encoder)
        primary_included = False
        for db in sorted(encoder.glob("*.db")):
            is_primary = primary is not None and db.name == primary.name
            data, db_ok = _sqlite_copy(db, primary=is_primary)
            primary_included |= is_primary
            usable = usable and db_ok
            _add(tar, f"encode/{db.name}", data)
            names.append(f"encode/{db.name}")
        if key is not None:
            _add(tar, "core/secret.key", key)
            names.append("core/secret.key")
        index = ctx.settings.download_index_path
        if index.is_symlink():
            raise BundleError("Ongeldig bestand voor download-ID's")
        if index.is_file():
            _add(tar, f"core/{index.name}", index.read_bytes())
            names.append(f"core/{index.name}")
        sec = Path(os.environ.get("MG_SECRETS_DIR") or root / "secrets")
        if sec.is_dir():
            for f in sorted(p for p in sec.rglob("*") if p.is_file()):
                rel = f"secrets/{f.relative_to(sec)}"
                _add(tar, rel, f.read_bytes())
                names.append(rel)
    return buf.getvalue(), names, usable and primary_included


def create(ctx: AppContext, *, actor: str = "system") -> dict[str, Any] | None:
    """Maakt een versleutelde bundel als de herstelcode is ingesteld; anders None."""
    with ctx.db.startup_lock():
        ctx.db.require_writable()
        return _create(ctx, actor)


def _create(ctx: AppContext, actor: str) -> dict[str, Any] | None:
    st = ctx.state_get(STATE_KEY, {}) or {}
    if not st.get("public"):
        return None
    payload, names, complete = _payload(ctx)
    pub = X25519PublicKey.from_public_bytes(base64.b64decode(st["public"]))
    eph = X25519PrivateKey.generate()
    eph_pub = eph.public_key().public_bytes(serialization.Encoding.Raw,
                                            serialization.PublicFormat.Raw)
    shared = eph.exchange(pub)
    kek = HKDF(hashes.SHA256(), 32, salt=eph_pub, info=b"mediaguard-bundle").derive(shared)
    data_key, n1, n2 = AESGCM.generate_key(bit_length=256), os.urandom(12), os.urandom(12)
    header = {"v": 1, "created_at": _now(), "files": names, "complete": complete,
              "eph": _b64(eph_pub),
              "n1": _b64(n1), "n2": _b64(n2),
              "wrapped": _b64(AESGCM(kek).encrypt(n1, data_key, b"key")),
              "fingerprint": st.get("fingerprint")}
    hdr = json.dumps(header).encode()
    body = AESGCM(data_key).encrypt(n2, payload, hdr)
    folder = bundle_dir(ctx)
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    target = folder / f"mediaguard-bundel-{stamp}.mgbundle"
    blob = MAGIC + len(hdr).to_bytes(4, "big") + hdr + body
    proof = _proof_path(target)
    proof.unlink(missing_ok=True)
    _publish(target, blob)
    # Een bestaand sleutelbestand zonder aanmaakbewijs kan nog worden gebruikt
    # met de herstelcode. Het levert geen passieve verificatie op.
    if key_digest := st.get("key_sha256"):
        _publish(proof, json.dumps({"bundle": _digest(blob), "key": key_digest}).encode())
    _sweep_partials(folder)
    keep = int(ctx.config.value("backup.bundle_retention"))
    # alleen opruimen na een geslaagde nieuwe bundel; tellen doen alleen complete bundels
    for old in sorted(folder.glob("mediaguard-bundel-*.mgbundle"))[:-keep]:
        old.unlink(missing_ok=True)
        _proof_path(old).unlink(missing_ok=True)
    return {"file": target.name, "size": target.stat().st_size, "files": names}


def _sweep_partials(folder: Path, grace_s: float = 3600) -> int:
    """Halve ``.tmp``-bestanden van een eerder afgebroken bundel (ouder dan een uur)."""
    import time

    removed = 0
    for tmp in folder.glob("mediaguard-bundel-*.tmp"):
        try:
            if time.time() - tmp.stat().st_mtime > grace_s:
                tmp.unlink(missing_ok=True)
                removed += 1
        except OSError:
            continue
    return removed


# -- openen / herstellen ------------------------------------------------------------------
class BundleError(Exception):
    pass


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _proof_path(path: Path) -> Path:
    return path.with_suffix(".sha256.json")


def _read_header(handle: io.BufferedIOBase) -> tuple[dict[str, Any], bytes]:
    if handle.read(len(MAGIC)) != MAGIC:
        raise BundleError("Geen MediaGuard-herstelbundel")
    size = int.from_bytes(handle.read(4), "big")
    if not 0 < size <= MAX_HEADER:
        raise BundleError("Ongeldige kop van de herstelbundel")
    raw = handle.read(size)
    header = _read_fields(raw, {"eph": 32, "n1": 12, "n2": 12, "wrapped": 48})
    return header, raw


def require_complete(handle: io.BufferedIOBase) -> None:
    header, _ = _read_header(handle)
    if header.get("complete") is not True:
        raise BundleError("De herstelinhoud van deze bundel is niet geverifieerd")


def _read_fields(raw: bytes, fields: dict[str, int]) -> dict[str, Any]:
    try:
        value = json.loads(raw)
        if (not isinstance(value, dict) or value.get("v") != 1
                or any(len(base64.b64decode(value[name], validate=True)) != size
                       for name, size in fields.items())):
            raise ValueError()
        return value
    except (KeyError, TypeError, ValueError) as exc:
        raise BundleError("Ongeldig formaat van bundel of herstelsleutel") from exc


def _read_key(raw: bytes) -> dict[str, Any]:
    return _read_fields(raw, {"salt": 16, "nonce": 12, "wrapped": 48, "public": 32})


def _key_matches(header: dict[str, Any], key: dict[str, Any]) -> bool:
    return header.get("fingerprint") == _digest(base64.b64decode(key["public"]))[:16]


def _check_pair_proof(path: Path, digests: dict[str, str]) -> bool:
    try:
        proof = json.loads(_proof_path(path).read_bytes())
    except FileNotFoundError:
        return False
    except ValueError as exc:
        raise BundleError("Ongeldig aanmaakbewijs van de herstelbundel") from exc
    if proof != digests:
        raise BundleError("Aanmaakbewijs van bundel en herstelsleutel klopt niet")
    return True


def verify_pair(
    path: Path, keyfile: Path, *, require_content: bool = True,
) -> tuple[bool | None, dict[str, str] | None]:
    """Een inhoudsbewijs vereist ook de controle van koppelingen en AV1 bij het maken."""
    try:
        if any(p.is_symlink() or not p.is_file() for p in (path, keyfile)):
            return None, None
        raw_key = keyfile.read_bytes()
        with path.open("rb") as handle:
            header, _ = _read_header(handle)
            if len(handle.read(17)) < 17:
                return None, None
            matches = _key_matches(header, _read_key(raw_key))
            if (not matches or not has_pair_proof(path)
                    or (require_content and header.get("complete") is not True)):
                return matches, None
            proof = {"bundle": "", "key": _digest(raw_key)}
            handle.seek(0)
            proof["bundle"] = hashlib.file_digest(handle, "sha256").hexdigest()
            try:
                if _check_pair_proof(path, proof):
                    return matches, {**proof, "fingerprint": header["fingerprint"]}
            except BundleError:
                pass
            return matches, None
    except (BundleError, OSError, ValueError):
        return None, None


def has_pair_proof(path: Path) -> bool:
    return _proof_path(path).exists()


def open_bundle(path: Path, code: str, keyfile: Path) -> tuple[dict[str, Any], bytes]:
    with path.open("rb") as handle:
        header, hdr = _read_header(handle)
        body = handle.read()
    raw_key = keyfile.read_bytes()
    k = _read_key(raw_key)
    if not _key_matches(header, k):
        raise BundleError("Bundel hoort niet bij deze herstelsleutel")
    if has_pair_proof(path):
        digest = hashlib.sha256(MAGIC + len(hdr).to_bytes(4, "big") + hdr)
        digest.update(body)
        _check_pair_proof(path, {"bundle": digest.hexdigest(), "key": _digest(raw_key)})
    try:
        priv_raw = AESGCM(_code_key(code, base64.b64decode(k["salt"]))).decrypt(
            base64.b64decode(k["nonce"]), base64.b64decode(k["wrapped"]),
            b"mediaguard-bundle-key")
    except Exception as exc:  # noqa: BLE001 - InvalidTag: verkeerde code
        raise BundleError("Herstelcode klopt niet bij deze herstelsleutel") from exc
    priv = X25519PrivateKey.from_private_bytes(priv_raw)
    eph = base64.b64decode(header["eph"])
    shared = priv.exchange(X25519PublicKey.from_public_bytes(eph))
    kek = HKDF(hashes.SHA256(), 32, salt=eph, info=b"mediaguard-bundle").derive(shared)
    try:
        data_key = AESGCM(kek).decrypt(base64.b64decode(header["n1"]),
                                       base64.b64decode(header["wrapped"]), b"key")
        payload = AESGCM(data_key).decrypt(base64.b64decode(header["n2"]),
                                           body, hdr)
    except Exception as exc:  # noqa: BLE001
        raise BundleError("Bundel hoort niet bij deze herstelsleutel of is beschadigd") from exc
    return header, payload


def restore(path: Path, code: str, keyfile: Path, target: Path) -> list[str]:
    """Pakt de bundel uit in ``target`` (bv. een lege /config). Bestaande bestanden worden
    niet overschreven maar als ``.voor-herstel`` bewaard."""
    with preupdate.config_locks(target):
        preupdate.inspect_schema(
            target, encoder_migrations=preupdate.encoder_migrations(os.environ),
        )
        return _restore(path, code, keyfile, target)


def _restore(path: Path, code: str, keyfile: Path, target: Path) -> list[str]:
    _header, payload = open_bundle(path, code, keyfile)
    written = []
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as tar:
        for m in tar.getmembers():
            if not m.isfile() or m.name.startswith("/") or ".." in Path(m.name).parts:
                continue
            dest = target / m.name
            dest.parent.mkdir(parents=True, exist_ok=True)
            if dest.exists():
                dest.rename(dest.with_name(dest.name + ".voor-herstel"))
            f = tar.extractfile(m)
            assert f is not None
            dest.write_bytes(f.read())
            os.chmod(dest, 0o600)
            written.append(m.name)
    return written


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode()


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - CLI
    p = argparse.ArgumentParser(prog="python -m mediaguard.maintenance.bundle")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("restore", help="herstelbundel uitpakken")
    r.add_argument("--bundel", required=True, type=Path)
    r.add_argument("--sleutel", required=True, type=Path, help="herstelsleutel.json")
    r.add_argument("--naar", required=True, type=Path, help="doelmap, bv. /config")
    r.add_argument("--code", help="herstelcode (anders gevraagd)")
    c = sub.add_parser("check", help="alleen controleren of code en bundel kloppen")
    c.add_argument("--bundel", required=True, type=Path)
    c.add_argument("--sleutel", required=True, type=Path)
    c.add_argument("--code")
    a = p.parse_args(argv)
    code = a.code or input("Herstelcode: ")
    try:
        if a.cmd == "check":
            header, _ = open_bundle(a.bundel, code, a.sleutel)
            print(f"In orde: bundel van {header['created_at']}, {len(header['files'])} bestanden")
        else:
            for name in restore(a.bundel, code, a.sleutel, a.naar):
                print(f"hersteld: {name}")
    except (BundleError, SchemaTooNew) as exc:
        print(f"FOUT: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
