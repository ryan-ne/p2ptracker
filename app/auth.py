"""Dashboard auth: signed session cookie (login page) with HTTP Basic as a fallback for scripts."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
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


def make_token(user: str, secret: str, now: int | None = None) -> str:
    exp = (now or int(time.time())) + SESSION_TTL
    payload = f"{user}|{exp}"
    sig = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode(f"{payload}|{sig}".encode()).decode()


def verify_token(token: str, user: str, secret: str, now: int | None = None) -> bool:
    try:
        tok_user, exp, sig = base64.urlsafe_b64decode(token.encode()).decode().rsplit("|", 2)
    except (ValueError, binascii.Error, UnicodeDecodeError):
        return False
    expected = hmac.new(secret.encode(), f"{tok_user}|{exp}".encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected) or not hmac.compare_digest(tok_user, user):
        return False
    return exp.isdigit() and int(exp) > (now or int(time.time()))


def check_credentials(user: str, pw: str, want_user: str, want_pw: str) -> bool:
    return secrets.compare_digest(user, want_user) & secrets.compare_digest(pw, want_pw)


def basic_ok(header: str, want_user: str, want_pw: str) -> bool:
    if not header.lower().startswith("basic "):
        return False
    try:
        user, _, pw = base64.b64decode(header[6:]).decode().partition(":")
    except (binascii.Error, UnicodeDecodeError):
        return False
    return check_credentials(user, pw, want_user, want_pw)


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
