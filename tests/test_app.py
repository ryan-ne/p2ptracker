import base64
import time

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.exchanges import Ad, ExchangeError
from app.main import create_app


class FakeExchange:
    def __init__(self, name, prices=None, error=None):
        self.name, self.prices, self.error = name, prices or {}, error

    async def fetch(self, side):
        if self.error:
            raise ExchangeError(self.error)
        return [Ad(price=p, min_amount=1000, max_amount=1_000_000, pay_methods=["KBZPay"])
                for p in self.prices.get(side, [])]


class Collector:
    def __init__(self):
        self.sent = []

    async def send(self, text):
        self.sent.append(text)


@pytest.fixture
def client(tmp_path):
    s = Settings(db_path=str(tmp_path / "t.db"), telegram_bot_token="", fail_alert_after=2,
                 exchanges="binance,bybit,bitget", _env_file=None)
    app = create_app(s, start_background=False)
    with TestClient(app) as c:
        p = app.state.poller
        p.notifier = Collector()
        p.exchanges = [
            FakeExchange("binance", {"BUY": [4840, 4835, 4850], "SELL": [4800, 4790]}),
            FakeExchange("bybit", {"BUY": [4870, 4875], "SELL": [4826, 4820]}),
            FakeExchange("bitget", error="boom"),
        ]
        c.app_ = app
        yield c


def tick(c):
    return c.portal.call(c.app_.state.poller.tick)


def test_poll_latest_history(client):
    rates = tick(client)
    assert len(rates) == 4
    d = client.get("/api/latest").json()
    assert d["exchanges"]["binance"]["BUY"]["best"] == 4835
    assert d["best_buy"] == {"price": 4835, "exchange": "binance"}
    assert d["best_sell"] == {"price": 4826, "exchange": "bybit"}
    assert d["exchanges"]["bitget"]["status"]["ok"] is False

    h = client.get("/api/history?range=1h").json()
    assert h["bucket"] == 0 and len(h["series"]) == 4
    h = client.get("/api/history?range=7d&side=BUY").json()
    assert h["bucket"] == 300 and {s["side"] for s in h["series"]} == {"BUY"}
    assert client.get("/api/history?range=nope").status_code == 400


def test_exchange_down_alert_once(client):
    for _ in range(4):
        tick(client)
    sent = client.app_.state.poller.notifier.sent
    assert sum("bitget" in m and "failing" in m for m in sent) == 1


def test_alert_rule_flow(client):
    assert client.post("/api/alerts", json={"exchange": "nope", "side": "BUY", "op": "<=", "threshold": 1}).status_code == 422
    r = client.post("/api/alerts", json={"exchange": "binance", "side": "BUY", "op": "<=", "threshold": 4840})
    assert r.status_code == 201
    rid = r.json()["id"]
    tick(client)
    sent = client.app_.state.poller.notifier.sent
    assert any("Binance BUY" in m for m in sent)
    ev = client.get("/api/alert-events").json()
    assert ev[0]["rule_id"] == rid and ev[0]["price"] == 4835
    assert client.get("/api/alerts").json()[0]["last_fired_at"] is not None

    assert client.patch(f"/api/alerts/{rid}", json={"enabled": False}).json() == {"ok": True}
    assert client.delete(f"/api/alerts/{rid}").json() == {"ok": True}
    assert client.delete(f"/api/alerts/{rid}").status_code == 404


def test_pause_suppresses_alerts(client):
    client.put("/api/settings", json={"alerts_paused": True})
    client.post("/api/alerts", json={"exchange": "any", "side": "BUY", "op": "<=", "threshold": 99999})
    tick(client)
    assert client.get("/api/alert-events").json() == []


def test_filters_applied(client):
    assert client.put("/api/settings", json={"filters": {"binance": {"pay_methods": ["WavePay"]}}}).json() == {"ok": True}
    tick(client)
    d = client.get("/api/latest").json()
    assert "BUY" not in d["exchanges"]["binance"] and d["exchanges"]["binance"]["status"]["empty"] is True
    s = client.get("/api/settings").json()
    assert s["pay_methods_seen"]["binance"] == ["KBZPay"]
    assert client.put("/api/settings", json={"filters": {"kraken": {}}}).status_code == 422


def test_retention_rollup(client):
    db = client.app_.state.db
    old = (int(time.time()) // 3600 - 24 * 40) * 3600
    rows = [(old + i * 600, "bybit", "BUY", 4800 + i, 4810 + i, 5) for i in range(6)]
    client.portal.call(db.insert_snapshots, rows)
    removed = client.portal.call(db.rollup_and_prune, int(time.time()) - 30 * 86400)
    assert removed == 6
    h = client.get(f"/api/history?from={old - 10}&to={old + 7200}").json()
    pts = h["series"][0]["points"]
    assert pts == [[old, 4805.0, 4812.5]]  # hourly close + mean avg5


def test_basic_auth(tmp_path):
    s = Settings(db_path=str(tmp_path / "a.db"), dashboard_user="u", dashboard_pass="p", _env_file=None)
    with TestClient(create_app(s, start_background=False)) as c:
        assert c.get("/api/alerts").status_code == 401
        assert c.get("/healthz").status_code == 200
        good = base64.b64encode(b"u:p").decode()
        bad = base64.b64encode(b"u:x").decode()
        assert c.get("/api/alerts", headers={"Authorization": f"Basic {good}"}).status_code == 200
        assert c.get("/api/alerts", headers={"Authorization": f"Basic {bad}"}).status_code == 401
