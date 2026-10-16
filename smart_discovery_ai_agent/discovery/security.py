"""Password hashing, opaque session tokens and small abuse guards (stdlib only)."""
from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
import time
from collections import defaultdict, deque

_SCRYPT = dict(n=2**14, r=8, p=1, dklen=32)
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$")


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(digest).decode()


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, salt_b64, digest_b64 = stored.split("$")
        if scheme != "scrypt":
            return False
        salt, expected = base64.b64decode(salt_b64), base64.b64decode(digest_b64)
        actual = hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    """Only the hash is stored, so a database leak does not leak live sessions."""
    return hashlib.sha256(token.encode()).hexdigest()


def validate_registration(name: str, email: str, password: str) -> dict[str, str]:
    errors: dict[str, str] = {}
    if not (1 <= len(name.strip()) <= 80):
        errors["name"] = "Name is required (max 80 characters)."
    if not EMAIL_RE.match(email.strip()) or len(email) > 254:
        errors["email"] = "Enter a valid email address."
    if len(password) < 8:
        errors["password"] = "Password must be at least 8 characters."
    elif len(password) > 128:
        errors["password"] = "Password must be at most 128 characters."
    elif password.isdigit() or password.isalpha():
        errors["password"] = "Use a mix of letters and numbers."
    return errors


class RateLimiter:
    """Sliding-window limiter (in-memory; per process)."""

    def __init__(self, limit: int, window_s: float):
        self.limit, self.window = limit, window_s
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        q = self._hits[key]
        while q and now - q[0] > self.window:
            q.popleft()
        if len(q) >= self.limit:
            return False
        q.append(now)
        return True

    def reset(self) -> None:
        self._hits.clear()
