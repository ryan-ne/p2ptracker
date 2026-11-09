import asyncio

import pytest

from app.config import Settings
from app.db import Database
from app.patterns import PatternAgent, Thresholds, detect_spike, detect_trend, minute_closes

TH = Thresholds(spike_pct=0.4, spike_z=3.0, trend_minutes=10, trend_pct=0.3)


def noisy(n, base=4850.0):
    """Quiet market: alternates by 1 MMK (~0.02%)."""
    return [base + (i % 2) for i in range(n)]


# ---------- minute_closes ----------
def test_minute_closes_last_per_minute_and_forward_fill():
    rows = [(600, 1.0), (630, 2.0), (720, 5.0)]  # minutes 10, 10, 12 (11 missing)
    assert minute_closes(rows, end_minute=13, minutes=4) == [2.0, 2.0, 5.0, 5.0]


def test_minute_closes_restarts_after_long_gap():
    rows = [(m * 60, 100.0) for m in range(0, 30)] + [(m * 60, 110.0) for m in range(40, 45)]  # 10-min gap
    closes = minute_closes(rows, end_minute=44, minutes=60)
    assert closes == [110.0] * 5          # only the unbroken segment after the gap


def test_no_false_spike_after_server_restart():
    rows = [(m * 60, 4850.0 + (m % 2)) for m in range(0, 50)]          # 50 quiet minutes
    rows += [(m * 60, 4900.0) for m in range(58, 61)]                  # 8-min outage, price moved meanwhile
    closes = minute_closes(rows, end_minute=60, minutes=60)
    assert detect_spike(closes, "bybit", "BUY", TH) is None             # too little unbroken history
    assert detect_trend(closes, "bybit", "BUY", TH) is None


# ---------- spikes ----------
def test_spike_detected_on_sudden_jump():
    closes = noisy(60) + [4850 * 1.01]          # +1% in the last minute
    ev = detect_spike(closes, "bybit", "BUY", TH)
    assert ev and ev.kind == "spike" and ev.minutes == 1 and ev.pct == pytest.approx(1.0, abs=0.03)
    assert ev.z >= 3


def test_drop_detected_over_five_minutes():
    closes = noisy(55) + [4850, 4840, 4830, 4815, 4805, 4800]   # about -1% over 5 min
    ev = detect_spike(closes, "bitget", "SELL", TH)
    assert ev and ev.kind == "drop" and ev.pct < -0.4


def test_no_spike_when_market_is_normally_this_volatile():
    swing = [4850 * (1 + (0.006 if i % 2 else -0.006)) for i in range(60)]  # ±0.6% every minute
    closes = swing + [swing[-1] * 1.005]
    assert detect_spike(closes, "bybit", "BUY", TH) is None


def test_no_spike_below_min_pct_or_without_history():
    assert detect_spike(noisy(60) + [4850 * 1.002], "x", "BUY", TH) is None   # 0.2% < 0.4%
    assert detect_spike(noisy(10) + [4850 * 1.02], "x", "BUY", TH) is None    # too little history


# ---------- trends ----------
def test_trend_up_with_flat_minutes():
    run = [4800, 4802, 4802, 4805, 4808, 4808, 4810, 4813, 4815, 4815, 4818, 4822]
    ev = detect_trend(noisy(30, 4800)[:-1] + run, "bybit", "SELL", TH)
    assert ev and ev.kind == "trend_up" and ev.minutes >= 10 and ev.pct >= 0.3


def test_trend_broken_by_counter_move():
    run = [4800, 4805, 4810, 4815, 4812, 4818, 4822, 4826, 4830]   # dip after 3 min
    assert detect_trend(noisy(30, 4800) + run, "bybit", "SELL", TH) is None


def test_falling_trend():
    run = [4900 - 2 * i for i in range(14)]
    ev = detect_trend(run, "bitget", "BUY", TH)
    assert ev and ev.kind == "trend_down" and ev.minutes == 13


# ---------- agent ----------
class FakePoller:
    def __init__(self):
        self.sent = []

    async def _notify(self, text):
        self.sent.append(text)


async def seed(db, now):
    """60 quiet minutes for bybit BUY, then a +1% jump in the current minute."""
    rows = []
    for m in range(61):
        ts = now - (60 - m) * 60
        price = 4850 + (m % 2) if m < 60 else 4850 * 1.01
        rows.append((ts, "bybit", "BUY", price, price, 10))
        rows.append((ts, "bitget", "BUY", 4870.0, 4870.0, 8))
    await db.insert_snapshots(rows)


def run(coro):
    return asyncio.run(coro)


def make_agent(db):
    s = Settings(_env_file=None, pattern_cooldown_min=30)
    return PatternAgent(s, db, FakePoller())


def test_agent_sends_once_and_respects_cooldown():
    async def go():
        db = Database(":memory:"); await db.connect()
        now = 1_800_000_000
        await seed(db, now)
        agent = make_agent(db)
        events = await agent.tick(now)
        assert [(e.exchange, e.kind) for e in events] == [("bybit", "spike")]
        msg = agent.poller.sent[0]
        assert "Unusual price movement" in msg and "Bybit BUY" in msg and "Sudden spike" in msg
        assert "🤖" not in msg                             # no AI summary any more
        assert (await db.list_pattern_events())[0]["kind"] == "spike"
        await agent.tick(now + 60)                        # same pattern within cooldown -> silent
        assert len(agent.poller.sent) == 1
        await db.close()
    run(go())


def test_agent_respects_pause():
    async def go():
        db = Database(":memory:"); await db.connect()
        now = 1_800_000_000
        await seed(db, now)
        await db.set_setting("alerts_paused", True)
        agent = make_agent(db)
        await agent.tick(now)
        assert agent.poller.sent == []                    # paused: nothing sent
        await db.set_setting("alerts_paused", False)
        await agent.tick(now)
        assert len(agent.poller.sent) == 1 and "🤖" not in agent.poller.sent[0]
        await db.close()
    run(go())


async def seed_sell_spike(db, now):
    """Quiet BUY and SELL for 60 min, then a +1% jump on bybit SELL only."""
    rows = []
    for m in range(61):
        ts = now - (60 - m) * 60
        sell = 4830 + (m % 2) if m < 60 else 4830 * 1.01
        rows.append((ts, "bybit", "BUY", 4860.0 + (m % 2), 4865.0, 10))
        rows.append((ts, "bybit", "SELL", sell, sell, 10))
    await db.insert_snapshots(rows)


def test_agent_watches_buy_only_by_default():
    async def go():
        db = Database(":memory:"); await db.connect()
        now = 1_800_000_000
        await seed_sell_spike(db, now)
        agent = PatternAgent(Settings(_env_file=None), db, FakePoller())
        assert await agent.tick(now) == []                 # SELL spike ignored
        assert agent.poller.sent == []

        both = PatternAgent(Settings(_env_file=None, pattern_sides="BUY,SELL"), db, FakePoller())
        events = await both.tick(now)
        assert [(e.side, e.kind) for e in events] == [("SELL", "spike")]
        await db.close()
    run(go())


def test_pattern_side_setting_parsing():
    assert Settings(_env_file=None).pattern_side_list == {"BUY"}
    assert Settings(_env_file=None, pattern_sides="sell, buy").pattern_side_list == {"BUY", "SELL"}
    assert Settings(_env_file=None, pattern_sides="nonsense").pattern_side_list == {"BUY"}
