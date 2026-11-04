from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import auth
from .alerts import AlertEngine
from .api import router
from .config import Settings, get_settings
from .db import Database
from .exchanges import build_exchanges
from .poller import LogNotifier, Poller
from .retention import retention_loop

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("app")

STATIC = Path(__file__).parent / "static"


class LoginIn(BaseModel):
    username: str
    password: str


def create_app(settings: Settings | None = None, start_background: bool = True) -> FastAPI:
    settings = settings or get_settings()
    auth_on = bool(settings.dashboard_user and settings.dashboard_pass)
    limiter = auth.LoginLimiter()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db = Database(settings.db_path)
        await db.connect()
        # Session-signing secret: persisted so logins survive restarts.
        secret = await db.get_setting("session_secret")
        if not secret:
            secret = secrets.token_hex(32)
            await db.set_setting("session_secret", secret)
        app.state.session_secret = secret

        client = httpx.AsyncClient(timeout=settings.request_timeout, http2=False)
        exchanges = build_exchanges(settings.exchange_list, client, settings.ads_per_fetch)

        bot = None
        if settings.telegram_bot_token:
            from .bot import TelegramBot
            bot = TelegramBot(settings.telegram_bot_token, settings.telegram_chat_id, db,
                              settings.exchange_list, settings.display_tz)
        poller = Poller(settings, db, exchanges, bot or LogNotifier(), AlertEngine())
        if bot:
            bot.poller = poller

        app.state.settings, app.state.db, app.state.poller, app.state.bot = settings, db, poller, bot
        tasks: list[asyncio.Task] = []
        if start_background:
            if bot:
                try:
                    await bot.start()
                except Exception:
                    log.exception("telegram bot failed to start; alerts will only be logged")
                    app.state.bot = bot = None
                    poller.notifier = LogNotifier()
            tasks = [asyncio.create_task(poller.run(), name="poller"),
                     asyncio.create_task(retention_loop(db, settings.raw_retention_days), name="retention")]
            await poller.load_interval()
            log.info("polling %s every %.0fs", settings.exchange_list, poller.interval)
            if not auth_on:
                log.warning("dashboard has NO password (DASHBOARD_USER/DASHBOARD_PASS empty) - "
                            "do not expose it publicly")
        try:
            yield
        finally:
            for t in tasks:
                t.cancel()
            for t in tasks:
                with contextlib.suppress(asyncio.CancelledError):
                    await t
            if bot and start_background:
                await bot.stop()
            await client.aclose()
            await db.close()

    app = FastAPI(title="P2P USDT/MMK Tracker", lifespan=lifespan)

    def sign_key(request: Request) -> str:
        # Password is part of the key: changing DASHBOARD_PASS invalidates existing sessions.
        return f"{request.app.state.session_secret}:{settings.dashboard_pass}"

    def authed(request: Request) -> bool:
        token = request.cookies.get(auth.COOKIE)
        if token and auth.verify_token(token, settings.dashboard_user, sign_key(request)):
            return True
        # Basic auth is only for scripts (curl -u). Browsers that signed in via the old Basic popup keep
        # resending those credentials, which would make Log out impossible — so ignore Basic from browsers
        # (they always send Sec-Fetch-* headers; curl and other scripts don't).
        if any(h in request.headers for h in ("sec-fetch-site", "sec-fetch-mode")):
            return False
        return auth.basic_ok(request.headers.get("authorization", ""), settings.dashboard_user, settings.dashboard_pass)

    @app.middleware("http")
    async def require_login(request: Request, call_next):
        path = request.url.path
        if auth_on and not auth.is_public(path) and not authed(request):
            if path.startswith("/api/"):
                return JSONResponse({"detail": "login required"}, 401)
            nxt = request.url.path + (f"?{request.url.query}" if request.url.query else "")
            return RedirectResponse(f"/login?next={quote(nxt)}", 303)
        resp = await call_next(request)
        if path.startswith("/static/"):
            # Revalidate (cheap 304 via ETag) so a deploy never mixes old JS/CSS with new HTML.
            resp.headers["Cache-Control"] = "no-cache"
        return resp

    app.include_router(router)
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/", include_in_schema=False)
    async def index():
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})

    @app.get("/login", include_in_schema=False)
    async def login_page(request: Request):
        if not auth_on or authed(request):
            return RedirectResponse("/", 303)
        return FileResponse(STATIC / "login.html", headers={"Cache-Control": "no-cache"})

    @app.post("/api/login", include_in_schema=False)
    async def login(body: LoginIn, request: Request):
        if not auth_on:
            return {"ok": True}
        key = auth.client_key(request)
        if limiter.blocked(key):
            return JSONResponse({"detail": "Too many attempts. Try again in 15 minutes."}, 429)
        if not auth.check_credentials(body.username, body.password, settings.dashboard_user, settings.dashboard_pass):
            limiter.fail(key)
            await asyncio.sleep(0.5)  # slow down guessing
            return JSONResponse({"detail": "Incorrect username or password."}, 401)
        limiter.reset(key)
        resp = JSONResponse({"ok": True})
        resp.set_cookie(auth.COOKIE, auth.make_token(settings.dashboard_user, sign_key(request)),
                        max_age=auth.SESSION_TTL, httponly=True, samesite="lax", secure=auth.is_https(request))
        return resp

    @app.post("/api/logout", include_in_schema=False)
    async def logout(request: Request):
        resp = JSONResponse({"ok": True})
        # Same attributes as when set, so every browser actually drops it.
        resp.delete_cookie(auth.COOKIE, path="/", httponly=True, samesite="lax", secure=auth.is_https(request))
        return resp

    @app.get("/api/me", include_in_schema=False)
    async def me():
        return {"auth": auth_on, "user": settings.dashboard_user if auth_on else None}

    @app.get("/healthz", include_in_schema=False)
    async def healthz(request: Request):
        return {"ok": True, "last_tick": request.app.state.poller.last_tick}

    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon():
        return Response(status_code=204)

    return app


app = create_app()
