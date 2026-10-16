"""Small shared helpers: ids, clock, canonical JSON digests, token hashing."""
from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime, timezone
from typing import Any, Callable

_now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)  # noqa: E731


def set_clock(fn: Callable[[], datetime] | None) -> None:
    """Tests and the evaluation pin the clock so dates resolve the same way every run."""
    global _now
    _now = fn or (lambda: datetime.now(timezone.utc))


def now() -> datetime:
    return _now()


def iso_now() -> str:
    """UTC timestamp with millisecond precision, sortable as text."""
    return now().astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(5)}"


def canonical_json(value: Any) -> str:
    """Stable JSON: sorted keys, no whitespace, so equal content gives an equal digest."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def hash_secret(secret: str) -> str:
    """For random server-made secrets (tokens, nonces): a plain digest is enough because they cannot be guessed."""
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def hash_passcode(passcode: str) -> str:
    """For secrets a person chooses: salted scrypt, so a copied database does not hand out passcodes."""
    salt = secrets.token_bytes(16)
    key = hashlib.scrypt(passcode.encode("utf-8"), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return f"scrypt${salt.hex()}${key.hex()}"


def verify_passcode(passcode: str, stored: str | None) -> bool:
    """Checks scrypt hashes, and the plain digests the demo world's fixed passcodes were stored with."""
    import hmac

    if not stored:
        return False
    if stored.startswith("scrypt$"):
        _, salt, key = stored.split("$", 2)
        got = hashlib.scrypt(passcode.encode("utf-8"), salt=bytes.fromhex(salt), n=2 ** 14, r=8, p=1, dklen=32)
        return hmac.compare_digest(got.hex(), key)
    return hmac.compare_digest(hash_secret(passcode), stored)


def new_secret(nbytes: int = 24) -> str:
    return secrets.token_urlsafe(nbytes)
