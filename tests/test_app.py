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


def test_ads_merged_and_sorted(client):
    tick(client)
    buy = client.get("/api/ads?side=BUY").json()["ads"]
    assert [a["price"] for a in buy] == [4835, 4840, 4850, 4870, 4875]
    assert buy[0]["exchange"] == "binance" and buy[0]["pay_methods"] == ["KBZPay"]
    sell = client.get("/api/ads?side=SELL&exchange=bybit").json()["ads"]
    assert [a["price"] for a in sell] == [4826, 4820] and {a["exchange"] for a in sell} == {"bybit"}


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


def test_poll_interval_setting(client):
    p = client.app_.state.poller
    assert client.get("/api/settings").json()["poll_interval"] == 5
    assert client.put("/api/settings", json={"poll_interval": 3}).status_code == 422      # min 5s
    assert client.put("/api/settings", json={"poll_interval": 5000}).status_code == 422   # max 1h
    assert client.put("/api/settings", json={"poll_interval": 30}).json() == {"ok": True}
    assert p.interval == 30 and p._wake.is_set()        # running loop is woken to apply it now
    assert client.get("/api/latest").json()["interval"] == 30
    # persisted: a fresh poller on the same DB picks it up
    p.interval = 5
    client.portal.call(p.load_interval)
    assert p.interval == 30


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


def auth_app(tmp_path, pw="p"):
    s = Settings(db_path=str(tmp_path / "a.db"), dashboard_user="u", dashboard_pass=pw, _env_file=None)
    return create_app(s, start_background=False)


def test_login_flow(tmp_path):
    with TestClient(auth_app(tmp_path), follow_redirects=False) as c:
        # unauthenticated: pages redirect to login, API gets 401, public paths open
        assert c.get("/").status_code == 200                # dashboard is public to view
        r = c.get("/docs?x=1")                              # any other page still needs login
        assert r.status_code == 303 and r.headers["location"] == "/login?next=/docs%3Fx%3D1"
        assert c.get("/api/alerts").status_code == 401
        assert c.get("/healthz").status_code == 200
        assert c.get("/login").status_code == 200
        assert c.get("/static/style.css").status_code == 200

        assert c.post("/api/login", json={"username": "u", "password": "x"}).status_code == 401
        r = c.post("/api/login", json={"username": "u", "password": "p"})
        assert r.status_code == 200
        cookie = r.headers["set-cookie"]
        assert "HttpOnly" in cookie and "samesite=lax" in cookie.lower()
        assert c.get("/api/alerts").status_code == 200
        assert c.get("/login").status_code == 303           # already signed in -> dashboard
        assert c.get("/api/me").json() == {"auth": True, "user": "u", "can_edit": True}

        c.post("/api/logout")
        assert c.get("/api/alerts").status_code == 401


def test_tampered_or_stale_session_rejected(tmp_path):
    with TestClient(auth_app(tmp_path)) as c:
        c.post("/api/login", json={"username": "u", "password": "p"})
        good = c.cookies.get("p2p_session")
        c.cookies.clear()                      # else the real cookie is still sent alongside
        c.cookies.set("p2p_session", good[:-8] + "AAAAAAAA")
        assert c.get("/api/alerts").status_code == 401
        c.cookies.clear()
        c.cookies.set("p2p_session", "not-base64!")
        assert c.get("/api/alerts").status_code == 401
    # same DB (same secret) but password changed -> old session invalid
    with TestClient(auth_app(tmp_path, pw="new")) as c:
        c.cookies.set("p2p_session", good)
        assert c.get("/api/alerts").status_code == 401


def test_login_rate_limit(tmp_path, monkeypatch):
    monkeypatch.setattr("app.main.asyncio.sleep", _no_sleep)
    with TestClient(auth_app(tmp_path)) as c:
        for _ in range(10):
            assert c.post("/api/login", json={"username": "u", "password": "x"}).status_code == 401
        assert c.post("/api/login", json={"username": "u", "password": "p"}).status_code == 429


async def _no_sleep(_):
    return None


def test_basic_auth_fallback(tmp_path):
    with TestClient(auth_app(tmp_path)) as c:
        good = base64.b64encode(b"u:p").decode()
        bad = base64.b64encode(b"u:x").decode()
        assert c.get("/api/alerts", headers={"Authorization": f"Basic {good}"}).status_code == 200
        assert c.get("/api/alerts", headers={"Authorization": f"Basic {bad}"}).status_code == 401


def test_browser_cached_basic_auth_ignored(tmp_path):
    # Regression: a browser that used the old Basic popup keeps sending the header; logout must still work.
    basic = {"Authorization": "Basic " + base64.b64encode(b"u:p").decode()}
    browser = {**basic, "Sec-Fetch-Site": "same-origin", "Sec-Fetch-Mode": "cors"}
    with TestClient(auth_app(tmp_path), follow_redirects=False) as c:
        assert c.get("/api/alerts", headers=browser).status_code == 401
        assert c.get("/api/me", headers=browser).json()["user"] is None   # cached Basic not accepted from browsers
        c.post("/api/login", json={"username": "u", "password": "p"})
        assert c.get("/api/alerts", headers=browser).status_code == 200      # cookie session works
        r = c.post("/api/logout", headers=browser)
        assert "p2p_session=" in r.headers["set-cookie"] and "Max-Age=0" in r.headers["set-cookie"]
        assert c.get("/api/alerts", headers=browser).status_code == 401     # logged out despite Basic header
        assert c.get("/login", headers=browser).status_code == 200           # login page shown, not bounced
        assert c.get("/api/alerts", headers=basic).status_code == 200        # curl -u still works


def test_no_auth_configured(tmp_path):
    s = Settings(db_path=str(tmp_path / "n.db"), _env_file=None)
    with TestClient(create_app(s, start_background=False), follow_redirects=False) as c:
        assert c.get("/api/alerts").status_code == 200
        assert c.get("/login").status_code == 303
        assert c.get("/api/me").json()["auth"] is False


def test_public_view_login_to_change(tmp_path):
    with TestClient(auth_app(tmp_path), follow_redirects=False) as c:
        # anyone can view market data
        for path in ("/", "/api/latest", "/api/ads?side=BUY", "/api/history?range=1h",
                     "/api/pattern-events", "/api/settings"):
            assert c.get(path).status_code == 200, path
        assert c.get("/api/me").json() == {"auth": True, "user": None, "can_edit": False}
        # alert rules / alert history stay private (they reveal the prices you watch)
        assert c.get("/api/alerts").status_code == 401
        assert c.get("/api/alert-events").status_code == 401
        # every change needs login
        assert c.post("/api/alerts", json={"exchange": "any", "side": "BUY", "op": "<=", "threshold": 1}).status_code == 401
        assert c.patch("/api/alerts/1", json={"enabled": False}).status_code == 401
        assert c.delete("/api/alerts/1").status_code == 401
        assert c.put("/api/settings", json={"alerts_paused": True}).status_code == 401
        assert c.put("/api/settings", json={"poll_interval": 3600}).status_code == 401
        assert c.get("/api/settings").json()["alerts_paused"] is False   # nothing changed
        # after signing in, changes work
        c.post("/api/login", json={"username": "u", "password": "p"})
        assert c.put("/api/settings", json={"alerts_paused": True}).status_code == 200
        assert c.get("/api/alerts").status_code == 200
