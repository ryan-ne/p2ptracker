from __future__ import annotations

import asyncio
import base64
import binascii
import contextlib
import logging
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

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


def create_app(settings: Settings | None = None, start_background: bool = True) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db = Database(settings.db_path)
        await db.connect()
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
            log.info("polling %s every %.1fs", settings.exchange_list, settings.poll_interval)
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

    @app.middleware("http")
    async def basic_auth(request: Request, call_next):
        if settings.dashboard_user and settings.dashboard_pass and request.url.path != "/healthz":
            if not _auth_ok(request.headers.get("authorization", ""), settings):
                return Response("Unauthorized", 401, {"WWW-Authenticate": 'Basic realm="p2ptracker"'})
        return await call_next(request)

    app.include_router(router)
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/", include_in_schema=False)
    async def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/healthz", include_in_schema=False)
    async def healthz(request: Request):
        return {"ok": True, "last_tick": request.app.state.poller.last_tick}

    return app


def _auth_ok(header: str, s: Settings) -> bool:
    if not header.lower().startswith("basic "):
        return False
    try:
        user, _, pw = base64.b64decode(header[6:]).decode().partition(":")
    except (binascii.Error, UnicodeDecodeError):
        return False
    return secrets.compare_digest(user, s.dashboard_user) & secrets.compare_digest(pw, s.dashboard_pass)


app = create_app()
