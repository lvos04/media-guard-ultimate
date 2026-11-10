"""Versleutelde opslag van API-sleutels en wachtwoorden (Fernet, sleutel op /config met 0600).

Geheimen verlaten de backend nooit: API-responses tonen alleen ``is_set`` en de laatste
vier tekens zijn bewust NIET zichtbaar (ook die lekken informatie bij korte keys).
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from mediaguard.db import Database, utcnow

_PREFIXED = [
    # key=waarde in querystrings/URL's
    re.compile(r"(?i)((?:api[_-]?key|apikey|token|password|passkey|secret)=)([^&\s\"']+)"),
    # "apiKey": "waarde" in JSON
    re.compile(r"(?i)(\"(?:api[_-]?key|apikey|token|password|passkey|secret)\"\s*:\s*\")([^\"]+)"),
    re.compile(r"(?i)(x-api-key:\s*)(\S+)"),
    re.compile(r"(?i)(authorization:\s*bearer\s+)(\S+)"),
]
_BARE = [re.compile(r"sk-(?:ant-|proj-)?[A-Za-z0-9_\-]{12,}"),
         # MediaGuard API-sleutels (mgk_ + token_urlsafe)
         re.compile(r"mgk_[A-Za-z0-9_\-]{8,}")]


def redact(text: str) -> str:
    """Verwijdert sleutels/tokens uit logregels en foutmeldingen."""
    out = text
    for pattern in _PREFIXED:
        out = pattern.sub(lambda m: f"{m.group(1)}***", out)
    for pattern in _BARE:
        out = pattern.sub("***", out)
    return out


class RedactingFilter(logging.Filter):
    """Maskeert sleutels in logregels (o.a. ``X-Api-Key: …`` en alles wat met ``mgk_``
    begint), ook als ze in de argumenten van een logaanroep zitten."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 - kapotte format-string: ongemoeid laten
            return True
        clean = redact(message)
        if clean != message:
            record.msg, record.args = clean, None
        return True


def install_log_redaction() -> None:
    """Maskeert sleutels in élk logrecord, ongeacht logger of handler (idempotent): via de
    record-factory, zodat ook records van child-loggers en later toegevoegde handlers
    (TrueNAS-log, pytest) gefilterd zijn."""
    current = logging.getLogRecordFactory()
    if getattr(current, "_mg_redacting", False):
        return
    redactor = RedactingFilter()

    def factory(*args: object, **kwargs: object) -> logging.LogRecord:
        record = current(*args, **kwargs)  # type: ignore[arg-type]
        redactor.filter(record)
        return record

    factory._mg_redacting = True  # type: ignore[attr-defined]
    logging.setLogRecordFactory(factory)


class SecretStore:
    def __init__(self, db: Database, key_path: Path) -> None:
        self.db = db
        self.key_path = key_path
        self._fernet: Fernet | None = None

    def _cipher(self) -> Fernet:
        if self._fernet is None:
            if not self.key_path.exists():
                self.key_path.parent.mkdir(parents=True, exist_ok=True)
                key = Fernet.generate_key()
                fd = os.open(self.key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "wb") as handle:
                    handle.write(key)
            self._fernet = Fernet(self.key_path.read_bytes().strip())
        return self._fernet

    def set(self, name: str, value: str, actor: str | None = None) -> None:
        token = self._cipher().encrypt(value.encode("utf-8"))
        self.db.execute(
            "INSERT INTO secrets(name,ciphertext,updated_at,updated_by) VALUES (?,?,?,?) "
            "ON CONFLICT(name) DO UPDATE SET ciphertext=excluded.ciphertext, "
            "updated_at=excluded.updated_at, updated_by=excluded.updated_by",
            (name, token, utcnow(), actor),
        )

    def _decrypt(self, ciphertext: bytes | str) -> str | None:
        try:
            return self._cipher().decrypt(ciphertext).decode("utf-8")
        except (InvalidToken, ValueError, TypeError):
            return None

    def get(self, name: str) -> str | None:
        row = self.db.fetch_one("SELECT ciphertext FROM secrets WHERE name=?", (name,))
        if row is None:
            return None
        return self._decrypt(row["ciphertext"])

    def has(self, name: str) -> bool:
        """Ingesteld én leesbaar. Een geheim dat met een andere secret.key is versleuteld,
        telt niet als 'ingesteld': het werkt niet (zie ``health``)."""
        return self.get(name) is not None

    def exists(self, name: str) -> bool:
        """Staat er iets in de database, ook als het niet te ontsleutelen is?"""
        return self.db.fetch_one("SELECT 1 FROM secrets WHERE name=?", (name,)) is not None

    def readable(self, name: str) -> bool:
        return self.has(name)

    def health(self) -> dict[str, object]:
        """Welke opgeslagen geheimen kunnen niet ontsleuteld worden? Allemaal (of veel) =
        meestal een verkeerde of vervangen secret.key."""
        rows = self.db.fetch_all("SELECT name, ciphertext FROM secrets ORDER BY name")
        try:
            self._cipher()
            key_ok = True
        except (OSError, ValueError):
            key_ok = False
        bad = [str(r["name"]) for r in rows
               if not key_ok or self._decrypt(r["ciphertext"]) is None]
        return {"ok": not bad, "total": len(rows), "unreadable": bad, "key_ok": key_ok}

    def raw(self, name: str) -> bytes | str | None:
        """Versleutelde waarde zoals opgeslagen (om tijdelijk te vervangen en terug te zetten
        zonder te ontsleutelen)."""
        row = self.db.fetch_one("SELECT ciphertext FROM secrets WHERE name=?", (name,))
        return None if row is None else row["ciphertext"]

    def restore_raw(self, name: str, ciphertext: bytes | str | None,
                    actor: str | None = None) -> None:
        if ciphertext is None:
            self.delete(name)
            return
        self.db.execute(
            "INSERT INTO secrets(name,ciphertext,updated_at,updated_by) VALUES (?,?,?,?) "
            "ON CONFLICT(name) DO UPDATE SET ciphertext=excluded.ciphertext, "
            "updated_at=excluded.updated_at, updated_by=excluded.updated_by",
            (name, ciphertext, utcnow(), actor),
        )

    def delete(self, name: str) -> None:
        self.db.execute("DELETE FROM secrets WHERE name=?", (name,))
