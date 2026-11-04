"""Pattern agent: every minute, look for sudden price behaviour and post it to the Telegram chat.

Detection is rule-based (fast, free, predictable); Claude only writes a short plain-language
summary of what was detected. If the AI call fails or no API key is set, the rule-based
message is sent on its own.
"""

from __future__ import annotations

import asyncio
import html
import logging
import math
import statistics
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

from .alerts import fmt_price, fmt_time
from .config import Settings
from .db import Database

if TYPE_CHECKING:
    from .poller import Poller

log = logging.getLogger("patterns")

HISTORY_MIN = 60          # minutes of 1-min closes used as the "normal behaviour" baseline
MIN_HISTORY_MIN = 20      # need at least this much before judging spikes
SPIKE_WINDOWS = (1, 5, 15)
SIGMA_FLOOR_PCT = 0.05    # MMK P2P prices often sit flat; stop a tiny std-dev from exaggerating z-scores


@dataclass
class Event:
    exchange: str
    side: str
    kind: str              # "spike" | "drop" | "trend_up" | "trend_down"
    minutes: int           # window (spike/drop) or run length (trend)
    pct: float             # signed % change over that window
    start: float
    end: float
    z: float | None = None

    @property
    def key(self) -> tuple[str, str, str]:
        family = "trend" if self.kind.startswith("trend") else "move"
        return (self.exchange, self.side, family + ("+" if self.pct > 0 else "-"))

    def describe(self) -> str:
        arrow = "▲" if self.pct > 0 else "▼"
        market = f"{self.exchange.capitalize()} {self.side}"
        move = f"{self.pct:+.2f}% ({fmt_price(self.start)} → {fmt_price(self.end)})"
        if self.kind in ("spike", "drop"):
            what = "Sudden spike" if self.kind == "spike" else "Sudden drop"
            return f"{arrow} <b>{market}</b> · {what} {move} in {self.minutes} min · {self.z:.1f}σ"
        what = "Rising" if self.kind == "trend_up" else "Falling"
        return f"{arrow} <b>{market}</b> · {what} for {self.minutes} min straight {move}"


@dataclass
class Thresholds:
    spike_pct: float = 0.4
    spike_z: float = 3.0
    trend_minutes: int = 10
    trend_pct: float = 0.3

    @classmethod
    def from_settings(cls, s: Settings) -> "Thresholds":
        return cls(s.pattern_spike_pct, s.pattern_spike_z, s.pattern_trend_minutes, s.pattern_trend_pct)


def pct(a: float, b: float) -> float:
    return (b - a) / a * 100 if a else 0.0


def minute_closes(rows: list[tuple[int, float]], end_minute: int, minutes: int = HISTORY_MIN) -> list[float]:
    """Last price in each minute, forward-filled onto a regular grid ending at `end_minute`.

    `rows` are (ts, price) sorted by ts. Minutes before the first observation are dropped.
    """
    last_by_min: dict[int, float] = {}
    for ts, price in rows:
        last_by_min[ts // 60] = price
    grid, cur = [], None
    for m in range(end_minute - minutes, end_minute + 1):
        if m in last_by_min:
            cur = last_by_min[m]
        elif cur is None:
            earlier = [k for k in last_by_min if k < m]
            cur = last_by_min[max(earlier)] if earlier else None
        if cur is not None:
            grid.append(cur)
    return grid


def detect_spike(closes: list[float], exchange: str, side: str, th: Thresholds) -> Event | None:
    """Biggest 1/5/15-min move that is both large in % and unusual vs the last hour's 1-min noise."""
    if len(closes) < MIN_HISTORY_MIN + 1:
        return None
    best: Event | None = None
    for w in SPIKE_WINDOWS:
        if len(closes) <= w + 2:
            continue
        move = pct(closes[-1 - w], closes[-1])
        baseline = [pct(a, b) for a, b in zip(closes[:-w - 1], closes[1:-w])]  # excludes the move itself
        sigma = max(statistics.pstdev(baseline) if len(baseline) > 1 else 0.0, SIGMA_FLOOR_PCT)
        z = move / (sigma * math.sqrt(w))
        if abs(move) >= th.spike_pct and abs(z) >= th.spike_z and (best is None or abs(z) > abs(best.z)):
            best = Event(exchange, side, "spike" if move > 0 else "drop", w, move,
                         closes[-1 - w], closes[-1], z)
    return best


def detect_trend(closes: list[float], exchange: str, side: str, th: Thresholds) -> Event | None:
    """Price has moved one way (never against it) for at least `trend_minutes` and `trend_pct`."""
    if len(closes) < th.trend_minutes + 1:
        return None
    last_dir = 0
    for a, b in zip(reversed(closes[:-1]), reversed(closes[1:])):
        if b != a:
            last_dir = 1 if b > a else -1
            break
    if last_dir == 0:
        return None
    run, nonflat = 0, 0
    for i in range(len(closes) - 1, 0, -1):
        step = closes[i] - closes[i - 1]
        if step * last_dir < 0:
            break
        run += 1
        nonflat += step != 0
    start = closes[-1 - run]
    move = pct(start, closes[-1])
    if run >= th.trend_minutes and nonflat >= 3 and abs(move) >= th.trend_pct:
        return Event(exchange, side, "trend_up" if last_dir > 0 else "trend_down", run, move, start, closes[-1])
    return None


class Summarizer(Protocol):
    async def summarize(self, events: list[Event], context: str) -> str | None: ...


BASE_PROMPT = (
    "You watch USDT/MMK peer-to-peer (P2P) exchange rates on Bybit and Bitget for traders in Myanmar. "
    "BUY is the price a user pays for 1 USDT; SELL is what a user receives for 1 USDT. "
    "A rule-based detector has flagged unusual price behaviour. Write 2-3 short sentences of plain text "
    "for a Telegram group: what happened, whether it shows up on both exchanges or one, and anything "
    "notable in the recent prices (e.g. the buy/sell spread widening, a reversal of an earlier move). "
    "Describe; do not tell anyone to buy or sell, and do not predict prices. No markdown, no emojis."
)

BURMESE_RULES = (
    " Write the summary in Burmese, in standard Unicode Myanmar script (not Zawgyi), using simple everyday "
    "words a market trader would use. Keep exchange names (Bybit, Bitget), BUY/SELL, USDT, MMK and all "
    "numbers exactly as they appear in the data (Western digits, same thousands separators), so they "
    "match the alert lines above the summary."
)


def system_prompt(language: str) -> str:
    lang = (language or "english").strip().lower()
    if lang in ("burmese", "myanmar", "my"):
        return BASE_PROMPT + BURMESE_RULES
    if lang in ("english", "en"):
        return BASE_PROMPT
    return BASE_PROMPT + f" Write the summary in {language}; keep exchange names and numbers as in the data."


SYSTEM_PROMPT = system_prompt("english")  # kept for reference/tests


class ClaudeSummarizer:
    def __init__(self, api_key: str, model: str, language: str = "burmese"):
        from anthropic import AsyncAnthropic  # imported lazily: only needed when the AI summary is on
        self.client = AsyncAnthropic(api_key=api_key, timeout=45.0, max_retries=1)
        self.model = model
        self.system = system_prompt(language)

    async def summarize(self, events: list[Event], context: str) -> str | None:
        import anthropic

        detected = "\n".join(html.unescape(e.describe()).replace("<b>", "").replace("</b>", "") for e in events)
        try:
            resp = await self.client.beta.messages.create(
                model=self.model,
                max_tokens=2000,
                system=self.system,
                output_config={"effort": "low"},
                # Server-side fallback: if the model declines, the API retries on a fallback model.
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                messages=[{"role": "user", "content": f"Detected:\n{detected}\n\nRecent data:\n{context}"}],
            )
        except anthropic.RateLimitError:
            log.warning("AI summary skipped: rate limited")
            return None
        except anthropic.APIStatusError as e:
            log.warning("AI summary failed (%s): %s", e.status_code, e.message)
            return None
        except anthropic.APIConnectionError as e:
            log.warning("AI summary failed: connection error: %s", e)
            return None
        if resp.stop_reason == "refusal":
            log.warning("AI summary declined")
            return None
        text = "".join(b.text for b in resp.content if b.type == "text").strip()
        return text or None


@dataclass
class PatternAgent:
    settings: Settings
    db: Database
    poller: "Poller"
    summarizer: Summarizer | None = None
    last_sent: dict[tuple[str, str, str], int] = field(default_factory=dict)

    async def run(self) -> None:
        log.info("pattern agent: checking every %ss (AI summary %s)", self.settings.pattern_interval,
                 "on" if self.summarizer else "off")
        await asyncio.sleep(10)  # let the first polls land
        while True:
            try:
                await self.tick()
            except Exception:
                log.exception("pattern check failed")
            await asyncio.sleep(self.settings.pattern_interval)

    async def series(self, now: int) -> dict[tuple[str, str], list[float]]:
        start = now - (HISTORY_MIN + 2) * 60
        cur = await self.db.conn.execute(
            "SELECT ts, exchange, side, best FROM rate_snapshots WHERE ts >= ? ORDER BY ts", (start,))
        rows: dict[tuple[str, str], list[tuple[int, float]]] = {}
        for r in await cur.fetchall():
            rows.setdefault((r["exchange"], r["side"]), []).append((r["ts"], r["best"]))
        return {k: minute_closes(v, now // 60) for k, v in rows.items()}

    async def tick(self, now: int | None = None) -> list[Event]:
        now = now or int(time.time())
        th = Thresholds.from_settings(self.settings)
        series = await self.series(now)
        events: list[Event] = []
        for (ex, side), closes in sorted(series.items()):
            for ev in (detect_spike(closes, ex, side, th), detect_trend(closes, ex, side, th)):
                if ev is None:
                    continue
                last = self.last_sent.get(ev.key)
                if last is not None and now - last < self.settings.pattern_cooldown_min * 60:
                    continue
                events.append(ev)
        if not events or await self.db.get_setting("alerts_paused", False):
            return events
        for ev in events:
            self.last_sent[ev.key] = now
            await self.db.add_pattern_event(now, ev.exchange, ev.side, ev.kind, ev.minutes, ev.pct,
                                            ev.start, ev.end, ev.z)
        summary = None
        if self.summarizer:
            summary = await self.summarizer.summarize(events, self.context(series))
        await self.poller._notify(self.format(events, summary, now))
        return events

    @staticmethod
    def context(series: dict[tuple[str, str], list[float]]) -> str:
        lines = []
        for (ex, side), closes in sorted(series.items()):
            tail = closes[-30:]
            lines.append(f"{ex} {side} last {len(tail)} one-minute closes (oldest→newest): "
                         + ", ".join(fmt_price(c) for c in tail))
        for ex in sorted({k[0] for k in series}):
            b, s = series.get((ex, "BUY")), series.get((ex, "SELL"))
            if b and s:
                lines.append(f"{ex} spread now (buy − sell): {fmt_price(b[-1] - s[-1])} MMK")
        return "\n".join(lines)

    def format(self, events: list[Event], summary: str | None, now: int) -> str:
        parts = ["🚨 <b>Unusual price movement</b>", *[f"• {e.describe()}" for e in events]]
        if summary:
            parts += ["", f"🤖 {html.escape(summary)}"]  # plain text: italics render Myanmar script poorly
        parts.append(f"<i>{fmt_time(now, self.settings.display_tz)}</i>")
        return "\n".join(parts)
