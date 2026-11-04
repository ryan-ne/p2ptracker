"""Telegram bot: sends alerts and accepts commands from the configured chat only."""

from __future__ import annotations

import html
import logging
import time
from typing import TYPE_CHECKING

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes, filters

from .alerts import fmt_price, fmt_time, format_rule, validate_rule
from .db import Database

if TYPE_CHECKING:
    from .poller import Poller

log = logging.getLogger("bot")

HELP = (
    "<b>P2P USDT/MMK tracker</b>\n"
    "/now — current rates\n"
    "/alerts — list alert rules\n"
    "/add &lt;exchange|any&gt; &lt;buy|sell&gt; &lt;best|avg5&gt; &lt;&lt;=|&gt;=&gt; &lt;price&gt; [cooldown_sec]\n"
    "   e.g. <code>/add bybit buy best &lt;= 4850</code>\n"
    "/del &lt;id&gt; — delete rule\n"
    "/on &lt;id&gt; · /off &lt;id&gt; — enable/disable rule\n"
    "/patterns — recent unusual moves found by the pattern agent\n"
    "/pause · /resume — mute/unmute all alerts\n"
    "/chatid — show this chat's id"
)


def rates_table(poller: "Poller", tz: str) -> str:
    lines = ["<b>USDT/MMK P2P</b>  (BUY = you buy USDT)", "<pre>"]
    lines.append(f"{'':8}{'BUY best':>10}{'avg5':>9}{'SELL best':>11}{'avg5':>9}")
    for ex, sides in poller.latest.items():
        b, s = sides.get("BUY"), sides.get("SELL")
        cell = lambda d, k: fmt_price(d[k]) if d else "-"  # noqa: E731
        lines.append(f"{ex:8}{cell(b, 'best'):>10}{cell(b, 'avg5'):>9}{cell(s, 'best'):>11}{cell(s, 'avg5'):>9}")
    lines.append("</pre>")
    buys = [(sides["BUY"]["best"], ex) for ex, sides in poller.latest.items() if "BUY" in sides]
    sells = [(sides["SELL"]["best"], ex) for ex, sides in poller.latest.items() if "SELL" in sides]
    if buys:
        p, ex = min(buys); lines.append(f"Cheapest buy: <b>{fmt_price(p)}</b> on {ex}")
    if sells:
        p, ex = max(sells); lines.append(f"Best sell: <b>{fmt_price(p)}</b> on {ex}")
    for ex, st in poller.status.items():
        if st["ok"] is False:
            lines.append(f"⚠️ {ex}: {html.escape(st['error'] or '')[:120]}")
        elif st["empty"]:
            lines.append(f"ℹ️ {ex}: no ads returned (region-blocked or filters too strict?)")
    if poller.last_tick:
        lines.append(f"<i>{fmt_time(poller.last_tick, tz)}</i>")
    return "\n".join(lines)


class TelegramBot:
    def __init__(self, token: str, chat_id: str, db: Database, valid_exchanges: list[str], tz: str):
        self.chat_id = chat_id
        self.db = db
        self.valid_exchanges = valid_exchanges
        self.tz = tz
        self.poller: Poller | None = None
        self.app = Application.builder().token(token).build()
        # Without a chat id, nobody may run commands (empty Chat filter matches nothing); only /chatid works.
        chat_filter = filters.Chat(chat_id=int(chat_id)) if chat_id else filters.Chat()
        cmds = {
            "start": self.cmd_help, "help": self.cmd_help, "now": self.cmd_now, "alerts": self.cmd_alerts,
            "add": self.cmd_add, "del": self.cmd_del, "on": self.cmd_on, "off": self.cmd_off,
            "pause": self.cmd_pause, "resume": self.cmd_resume, "patterns": self.cmd_patterns,
        }
        for name, fn in cmds.items():
            self.app.add_handler(CommandHandler(name, fn, filters=chat_filter))
        # /chatid answers anyone so you can discover your chat id during setup.
        self.app.add_handler(CommandHandler("chatid", self.cmd_chatid))

    async def start(self) -> None:
        await self.app.initialize()
        await self.app.start()
        await self.app.updater.start_polling(drop_pending_updates=True)
        log.info("telegram bot started")

    async def stop(self) -> None:
        try:
            await self.app.updater.stop()
            await self.app.stop()
            await self.app.shutdown()
        except Exception:
            log.exception("bot shutdown error")

    async def send(self, text: str) -> None:
        if not self.chat_id:
            log.warning("TELEGRAM_CHAT_ID not set; alert not sent: %s", text)
            return
        await self.app.bot.send_message(chat_id=self.chat_id, text=text, parse_mode=ParseMode.HTML)

    async def _reply(self, update: Update, text: str) -> None:
        await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML)

    # ---- commands ----------------------------------------------------------
    async def cmd_help(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        await self._reply(update, HELP)

    async def cmd_chatid(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        await self._reply(update, f"chat id: <code>{update.effective_chat.id}</code>")

    async def cmd_now(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.poller or not self.poller.last_tick:
            await self._reply(update, "No data yet — first poll pending.")
            return
        await self._reply(update, rates_table(self.poller, self.tz))

    async def cmd_alerts(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        rules = await self.db.list_rules()
        paused = await self.db.get_setting("alerts_paused", False)
        body = "\n".join(html.escape(format_rule(r)) for r in rules) or "No rules. Use /add."
        await self._reply(update, ("⏸ Alerts paused\n" if paused else "") + body)

    async def cmd_add(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        a = ctx.args or []
        if len(a) not in (5, 6):
            await self._reply(update, "Usage: <code>/add bybit buy best &lt;= 4850 [cooldown_sec]</code>")
            return
        exchange, side, metric, op = a[0].lower(), a[1].upper(), a[2].lower(), a[3]
        err = validate_rule(exchange, side, metric, op, self.valid_exchanges)
        try:
            threshold = float(a[4].replace(",", ""))
            cooldown = int(a[5]) if len(a) == 6 else 300
        except ValueError:
            err = err or "price and cooldown must be numbers"
        if err:
            await self._reply(update, html.escape(err))
            return
        rid = await self.db.add_rule(exchange, side, metric, op, threshold, cooldown, int(time.time()))
        await self._reply(update, f"✅ Added rule #{rid}")

    async def _rule_id(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int | None:
        try:
            return int((ctx.args or [""])[0])
        except ValueError:
            await self._reply(update, "Give a rule id, e.g. /del 3")
            return None

    async def cmd_del(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if (rid := await self._rule_id(update, ctx)) is None:
            return
        ok = await self.db.delete_rule(rid)
        if ok and self.poller:
            self.poller.engine.forget(rid)
        await self._reply(update, f"🗑 Deleted #{rid}" if ok else f"No rule #{rid}")

    async def _toggle(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE, on: bool) -> None:
        if (rid := await self._rule_id(update, ctx)) is None:
            return
        ok = await self.db.set_rule_enabled(rid, on)
        await self._reply(update, (f"Rule #{rid} {'enabled' if on else 'disabled'}") if ok else f"No rule #{rid}")

    async def cmd_on(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        await self._toggle(update, ctx, True)

    async def cmd_off(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        await self._toggle(update, ctx, False)

    async def cmd_patterns(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        from .patterns import Event
        rows = await self.db.list_pattern_events(10)
        if not rows:
            await self._reply(update, "No unusual moves detected yet.")
            return
        lines = ["<b>Recent unusual moves</b>"]
        for r in rows:
            ev = Event(r["exchange"], r["side"], r["kind"], r["minutes"], r["pct"],
                       r["start_price"], r["end_price"], r["z"])
            lines.append(f"{fmt_time(r['ts'], self.tz)[5:16]} {ev.describe()}")
        await self._reply(update, "\n".join(lines))

    async def cmd_pause(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        await self.db.set_setting("alerts_paused", True)
        await self._reply(update, "⏸ Alerts paused. /resume to unmute.")

    async def cmd_resume(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        await self.db.set_setting("alerts_paused", False)
        await self._reply(update, "▶️ Alerts resumed.")
