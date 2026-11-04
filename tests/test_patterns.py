import asyncio
from types import SimpleNamespace

import pytest

from app.config import Settings
from app.db import Database
from app.patterns import (ClaudeSummarizer, PatternAgent, Thresholds, detect_spike, detect_trend,
                          minute_closes)

TH = Thresholds(spike_pct=0.4, spike_z=3.0, trend_minutes=10, trend_pct=0.3)


def noisy(n, base=4850.0):
    """Quiet market: alternates by 1 MMK (~0.02%)."""
    return [base + (i % 2) for i in range(n)]


# ---------- minute_closes ----------
def test_minute_closes_last_per_minute_and_forward_fill():
    rows = [(600, 1.0), (630, 2.0), (720, 5.0)]  # minutes 10, 10, 12 (11 missing)
    assert minute_closes(rows, end_minute=13, minutes=4) == [2.0, 2.0, 5.0, 5.0]


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


class FakeSummarizer:
    def __init__(self, text="Bybit buy jumped <1%> while Bitget stayed flat."):
        self.text, self.calls = text, []

    async def summarize(self, events, context):
        self.calls.append((events, context))
        return self.text


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


def make_agent(db, summarizer=None):
    s = Settings(_env_file=None, pattern_cooldown_min=30)
    return PatternAgent(s, db, FakePoller(), summarizer)


def test_agent_sends_once_with_escaped_ai_summary_and_respects_cooldown():
    async def go():
        db = Database(":memory:"); await db.connect()
        now = 1_800_000_000
        await seed(db, now)
        summ = FakeSummarizer()
        agent = make_agent(db, summ)
        events = await agent.tick(now)
        assert [(e.exchange, e.kind) for e in events] == [("bybit", "spike")]
        msg = agent.poller.sent[0]
        assert "Unusual price movement" in msg and "Bybit BUY" in msg and "Sudden spike" in msg
        assert "&lt;1%&gt;" in msg                       # AI text is HTML-escaped
        assert "bybit BUY last" in summ.calls[0][1]      # model gets the recent series as context
        assert (await db.list_pattern_events())[0]["kind"] == "spike"
        await agent.tick(now + 60)                        # same pattern within cooldown -> silent
        assert len(agent.poller.sent) == 1
        await db.close()
    run(go())


def test_agent_without_ai_and_when_paused():
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


# ---------- Claude summarizer (API mocked) ----------
def fake_response(stop_reason="end_turn", text="Both exchanges moved up together."):
    return SimpleNamespace(stop_reason=stop_reason,
                           content=[SimpleNamespace(type="thinking", thinking=""),
                                    SimpleNamespace(type="text", text=text)])


def test_claude_summarizer_request_shape_and_refusal(monkeypatch):
    s = ClaudeSummarizer(api_key="test-key", model="claude-opus-5-5")
    calls = []

    async def create(**kw):
        calls.append(kw)
        return fake_response(*state["resp"])

    state = {"resp": ("end_turn", "Both exchanges moved up together.")}
    monkeypatch.setattr(s.client.beta.messages, "create", create)
    from app.patterns import Event
    ev = [Event("bybit", "BUY", "spike", 5, 0.8, 4800, 4838.4, 4.2)]

    assert run(s.summarize(ev, "ctx")) == "Both exchanges moved up together."
    kw = calls[0]
    assert kw["model"] == "claude-opus-5-5" and kw["fallbacks"] == "default"
    assert kw["betas"] == ["server-side-fallback-2026-07-01"]
    assert "thinking" not in kw and "temperature" not in kw
    assert "<b>" not in kw["messages"][0]["content"]       # HTML stripped before sending

    state["resp"] = ("refusal", "")
    assert run(s.summarize(ev, "ctx")) is None


def test_claude_summarizer_api_error_returns_none(monkeypatch):
    import anthropic
    import httpx2
    s = ClaudeSummarizer(api_key="test-key", model="claude-opus-5-5")

    async def boom(**kw):
        raise anthropic.APIConnectionError(request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages"))

    monkeypatch.setattr(s.client.beta.messages, "create", boom)
    from app.patterns import Event
    assert run(s.summarize([Event("bybit", "BUY", "drop", 1, -1, 1, 0.99, -5)], "ctx")) is None
