import json
from pathlib import Path

from app.alerts import AlertEngine, format_alert
from app.config import Settings
from app.exchanges import BUY, SELL, Rate
from app.exchanges import binance, bitget, bybit
from app.exchanges.links import p2p_url
from app.patterns import Event, PatternAgent

FX = Path(__file__).parent / "fixtures"


def test_p2p_urls():
    assert p2p_url("binance", BUY) == "https://p2p.binance.com/en/trade/all-payments/USDT?fiat=MMK"
    assert p2p_url("binance", SELL) == "https://p2p.binance.com/en/trade/sell/USDT?fiat=MMK"
    assert p2p_url("binance", BUY, "12949060405433573376") == "https://c2c.binance.com/en/adv?code=12949060405433573376"
    assert p2p_url("bybit", BUY) == "https://www.bybit.com/en/p2p/buy/USDT/MMK"
    assert p2p_url("bybit", SELL, "ignored-no-single-ad-format") == "https://www.bybit.com/en/p2p/sell/USDT/MMK"
    assert p2p_url("bitget", BUY) == "https://www.bitget.com/p2p-trade/buy/USDT?fiatName=MMK"
    assert p2p_url("bitget", SELL) == "https://www.bitget.com/p2p-trade/sell/USDT?fiatName=MMK"
    assert p2p_url("kraken", BUY) is None


def test_parsers_keep_ad_ids():
    by = bybit.parse(json.loads((FX / "bybit_side1.json").read_text(encoding="utf-8")))
    bg = bitget.parse(json.loads((FX / "bitget_side1.json").read_text(encoding="utf-8")))
    assert all(a.ad_id for a in by) and all(a.ad_id for a in bg)
    bn = binance.parse({"code": "000000", "data": [{"adv": {"advNo": "123", "price": "4843"}, "advertiser": {}}]})
    assert bn[0].ad_id == "123"


def test_price_alert_has_exchange_link():
    rule = dict(id=3, exchange="bybit", side="BUY", metric="best", op="<=", threshold=4850,
                enabled=1, cooldown_sec=300, last_fired_at=None)
    fired = AlertEngine().evaluate([rule], [Rate("bybit", "BUY", 4820, 4831, 12)], 0)[0]
    msg = format_alert(fired, 0, "Asia/Yangon")
    assert '<a href="https://www.bybit.com/en/p2p/buy/USDT/MMK">Buy USDT on Bybit ↗</a>' in msg


def test_pattern_alert_links_each_market_once():
    agent = PatternAgent(Settings(_env_file=None), db=None, poller=None)
    events = [Event("bybit", "BUY", "spike", 5, 0.6, 4830, 4860, 4.1),
              Event("bybit", "BUY", "trend_up", 12, 0.4, 4840, 4860),
              Event("bitget", "BUY", "drop", 1, -0.5, 4900, 4875, -3.5)]
    msg = agent.format(events, None, 0)
    assert msg.count("Buy USDT on Bybit ↗") == 1 and msg.count("Buy USDT on Bitget ↗") == 1
