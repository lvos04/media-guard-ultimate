"""RFC 6238 TOTP (SHA-1, 6 cijfers, 30 s) en herstelcodes, zonder externe afhankelijkheden."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote

STEP = 30
DIGITS = 6


def new_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _key(secret: str) -> bytes:
    s = secret.strip().replace(" ", "").upper()
    return base64.b32decode(s + "=" * (-len(s) % 8))


def hotp(secret: str, counter: int, digits: int = DIGITS) -> str:
    digest = hmac.new(_key(secret), struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = (struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF) % (10 ** digits)
    return str(code).zfill(digits)


def counter_at(ts: float | None = None) -> int:
    return int((time.time() if ts is None else ts) // STEP)


def verify(secret: str, code: str, *, ts: float | None = None, window: int = 1,
           last_counter: int | None = None) -> int | None:
    """Teller van de geaccepteerde code, of None. Een code wordt nooit twee keer
    geaccepteerd (``last_counter``)."""
    code = "".join(c for c in str(code) if c.isdigit())
    if len(code) != DIGITS:
        return None
    now = counter_at(ts)
    for c in range(now - window, now + window + 1):
        if last_counter is not None and c <= last_counter:
            continue
        if hmac.compare_digest(hotp(secret, c), code):
            return c
    return None


def otpauth_uri(secret: str, username: str, issuer: str = "MediaGuard") -> str:
    label = quote(f"{issuer}:{username}")
    return (f"otpauth://totp/{label}?secret={secret}&issuer={quote(issuer)}"
            f"&algorithm=SHA1&digits={DIGITS}&period={STEP}")


_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def new_recovery_codes(n: int = 10) -> list[str]:
    def one() -> str:
        raw = "".join(secrets.choice(_ALPHABET) for _ in range(10))
        return f"{raw[:5]}-{raw[5:]}"
    return [one() for _ in range(n)]


def hash_recovery(code: str) -> str:
    norm = "".join(c for c in code.upper() if c.isalnum())
    return hashlib.sha256(norm.encode()).hexdigest()
