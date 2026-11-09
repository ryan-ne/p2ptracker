"""Pattern agent: every minute, look for sudden price behaviour and post it to the Telegram chat.

Detection is rule-based: fast, free and predictable.
"""

from __future__ import annotations

import asyncio
import logging
import math
import statistics
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .alerts import exchange_link_line, fmt_price, fmt_time
from .config import Settings
from .db import Database

if TYPE_CHECKING:
    from .poller import Poller

log = logging.getLogger("patterns")

HISTORY_MIN = 60          # minutes of 1-min closes used as the "normal behaviour" baseline
MIN_HISTORY_MIN = 20      # need at least this much before judging spikes
SPIKE_WINDOWS = (1, 5, 15)
MAX_FILL_MIN = 3          # forward-fill at most this many missing minutes; a longer gap restarts the series
SIGMA_FLOOR_PCT = 0.05   # MMK P2P prices often sit flat; stop a tiny std-dev from exaggerating z-scores


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
    grid, cur, last_seen = [], None, None
    for m in range(end_minute - minutes, end_minute + 1):
        if m in last_by_min:
            if last_seen is not None and m - last_seen > MAX_FILL_MIN:
                # Data gap (server restart, exchange outage): don't treat the price before and after the
                # gap as a move within minutes - start the series fresh after the gap.
                grid = []
            cur, last_seen = last_by_min[m], m
        elif cur is None:
            earlier = [k for k in last_by_min if k < m]
            if earlier:
                cur, last_seen = last_by_min[max(earlier)], max(earlier)
        elif m - last_seen > MAX_FILL_MIN:
            continue  # inside a gap: don't forward-fill
        if cur is not None and (last_seen is None or m - last_seen <= MAX_FILL_MIN):
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


@dataclass
class PatternAgent:
    settings: Settings
    db: Database
    poller: "Poller"
    last_sent: dict[tuple[str, str, str], int] = field(default_factory=dict)

    async def run(self) -> None:
        log.info("pattern agent: checking every %ss", self.settings.pattern_interval)
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
        watched = self.settings.pattern_side_list            # default: BUY only
        series = {k: v for k, v in (await self.series(now)).items() if k[1] in watched}
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
        await self.poller._notify(self.format(events, now))
        return events

    def format(self, events: list[Event], now: int) -> str:
        parts = ["🚨 <b>Unusual price movement</b>", *[f"• {e.describe()}" for e in events]]
        markets = dict.fromkeys((e.exchange, e.side) for e in events)          # unique, in order
        links = [exchange_link_line(ex, side).strip() for ex, side in markets]
        if any(links):
            parts.append(" · ".join(link for link in links if link))
        parts.append(f"<i>{fmt_time(now, self.settings.display_tz)}</i>")
        return "\n".join(parts)
