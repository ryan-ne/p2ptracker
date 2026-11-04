"""Dashboard auth: signed session cookie (login page) with HTTP Basic as a fallback for scripts."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import re
import secrets
import time
from collections import defaultdict, deque

from fastapi import Request

COOKIE = "p2p_session"
SESSION_TTL = 30 * 86400
PUBLIC_PATHS = {"/login", "/api/login", "/api/logout", "/healthz", "/favicon.ico"}
PUBLIC_PREFIXES = ("/static/",)

MAX_FAILS = 10
FAIL_WINDOW = 15 * 60


USERNAME_RE = re.compile(r"^[a-z0-9._-]{2,32}$")
MIN_PASSWORD = 8

# scrypt parameters (~50 ms per hash): slow enough to resist guessing, fast enough for logins.
_N, _R, _P = 2 ** 14, 8, 1


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    h = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    return f"scrypt${_N}${_R}${_P}${salt.hex()}${h.hex()}"


def check_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt, want = stored.split("$")
        got = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p),
                             dklen=len(want) // 2)
    except (ValueError, TypeError):
        return False
    return algo == "scrypt" and hmac.compare_digest(got.hex(), want)


# Spent on unknown usernames so a login takes the same time whether or not the user exists.
_DUMMY_HASH = hash_password(secrets.token_hex(8))


def burn_time(password: str) -> None:
    check_password(password, _DUMMY_HASH)


def make_token(user: str, key: str, now: int | None = None) -> str:
    """`key` is per user (server secret + that user's password material), so a password change or
    user removal invalidates their sessions without touching anyone else's."""
    exp = (now or int(time.time())) + SESSION_TTL
    payload = f"{user}|{exp}"
    sig = hmac.new(key.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode(f"{payload}|{sig}".encode()).decode()


def token_user(token: str) -> str | None:
    """Unverified username from a token — only used to look up which key to verify it with."""
    try:
        user, _exp, _sig = base64.urlsafe_b64decode(token.encode()).decode().rsplit("|", 2)
        return user
    except (ValueError, binascii.Error, UnicodeDecodeError):
        return None


def verify_token(token: str, user: str, key: str, now: int | None = None) -> bool:
    try:
        tok_user, exp, sig = base64.urlsafe_b64decode(token.encode()).decode().rsplit("|", 2)
    except (ValueError, binascii.Error, UnicodeDecodeError):
        return False
    expected = hmac.new(key.encode(), f"{tok_user}|{exp}".encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected) or not hmac.compare_digest(tok_user, user):
        return False
    return exp.isdigit() and int(exp) > (now or int(time.time()))


def check_credentials(user: str, pw: str, want_user: str, want_pw: str) -> bool:
    return secrets.compare_digest(user, want_user) & secrets.compare_digest(pw, want_pw)


def parse_basic(header: str) -> tuple[str, str] | None:
    if not header.lower().startswith("basic "):
        return None
    try:
        user, sep, pw = base64.b64decode(header[6:]).decode().partition(":")
    except (binascii.Error, UnicodeDecodeError):
        return None
    return (user, pw) if sep else None


def is_public(path: str) -> bool:
    return path in PUBLIC_PATHS or path.startswith(PUBLIC_PREFIXES)


def is_https(request: Request) -> bool:
    return request.headers.get("x-forwarded-proto", request.url.scheme) == "https"


def client_key(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    return fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "?")


class LoginLimiter:
    """At most MAX_FAILS failed logins per client per FAIL_WINDOW seconds."""

    def __init__(self) -> None:
        self.fails: dict[str, deque[float]] = defaultdict(deque)

    def blocked(self, key: str, now: float | None = None) -> bool:
        now = now or time.time()
        q = self.fails[key]
        while q and now - q[0] > FAIL_WINDOW:
            q.popleft()
        return len(q) >= MAX_FAILS

    def fail(self, key: str, now: float | None = None) -> None:
        self.fails[key].append(now or time.time())

    def reset(self, key: str) -> None:
        self.fails.pop(key, None)
