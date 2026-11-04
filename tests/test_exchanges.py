import json
from pathlib import Path

import pytest

from app.exchanges import BUY, SELL, ExchangeError, Filters, apply_filters, compute_rate
from app.exchanges import binance, bitget, bybit

FX = Path(__file__).parent / "fixtures"


def load(name):
    return json.loads((FX / name).read_text(encoding="utf-8"))


def test_bybit_parse_and_side_ordering():
    buy = bybit.parse(load("bybit_side1.json"))    # merchants selling -> user BUY
    sell = bybit.parse(load("bybit_side0.json"))   # merchants buying  -> user SELL
    assert len(buy) == 5 and len(sell) == 5
    assert buy[0].price == 4870 and buy[0].min_amount == 8000
    assert all(a.pay_methods for a in buy)
    assert buy[1].pay_methods == ["KBZPay"]                       # id 601
    assert bybit.payment_name("99999") == "#99999"                # unknown id stays visible
    rb, rs = compute_rate("bybit", BUY, buy), compute_rate("bybit", SELL, sell)
    assert rb.best == min(a.price for a in buy)
    assert rs.best == max(a.price for a in sell)
    assert rb.best > rs.best  # you pay more to buy than you get when selling


def test_bitget_parse():
    buy = bitget.parse(load("bitget_side1.json"))
    sell = bitget.parse(load("bitget_side2.json"))
    assert buy[0].price == 4900 and buy[0].pay_methods == ["KBZPay"]
    assert compute_rate("bitget", SELL, sell).best == 4820
    assert compute_rate("bitget", BUY, buy).best == 4900


def test_binance_parse_synthetic():
    ads = binance.parse(load("binance_buy_synthetic.json"))
    assert [a.price for a in ads] == [4835, 4830, 4840]
    assert ads[1].pay_methods == ["Wave Pay"]
    r = compute_rate("binance", BUY, ads)
    assert r.best == 4830 and r.avg5 == pytest.approx(4835) and r.ads == 3


def test_binance_empty_is_not_error():
    assert binance.parse({"code": "000000", "data": [], "total": 0}) == []


@pytest.mark.parametrize("mod,payload", [
    (binance, {"code": "100001", "message": "bad"}),
    (bybit, {"ret_code": 10001, "ret_msg": "bad"}),
    (bitget, {"code": "40001", "msg": "bad"}),
])
def test_error_codes_raise(mod, payload):
    with pytest.raises(ExchangeError):
        mod.parse(payload)


def test_filters():
    ads = binance.parse(load("binance_buy_synthetic.json"))
    assert {a.merchant for a in apply_filters(ads, Filters(pay_methods=["kbzpay"]))} == {"merchantA", "merchantC"}
    assert {a.merchant for a in apply_filters(ads, Filters(amount=2_000_000))} == {"merchantA", "merchantC"}
    assert {a.merchant for a in apply_filters(ads, Filters(pay_methods=["AYA Pay"], amount=200_000))} == {"merchantC"}
    assert apply_filters(ads, Filters()) == ads


def test_compute_rate_top5_and_empty():
    from app.exchanges import Ad
    ads = [Ad(price=p) for p in (10, 9, 8, 7, 6, 5, 1)]
    r = compute_rate("x", SELL, ads, top_n=5)
    assert r.best == 10 and r.avg5 == 8 and r.ads == 7
    assert compute_rate("x", BUY, []) is None


# ---------- Binance signed C2C agent API ----------
import asyncio
import hashlib
import hmac
from urllib.parse import parse_qsl, urlsplit

import httpx


def test_binance_signed_query_keeps_order_and_signs():
    q = binance.signed_query("s3cret", now_ms=1700000000000)
    base, sig = q.rsplit("&signature=", 1)
    assert base == "recvWindow=60000&timestamp=1700000000000"          # insertion order, not sorted
    assert sig == hmac.new(b"s3cret", base.encode(), hashlib.sha256).hexdigest()


def test_binance_authenticated_fetch_uses_sapi_and_parses():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = request.url
        seen["headers"] = request.headers
        seen["body"] = request.read()
        return httpx.Response(200, json=load("binance_buy_synthetic.json"))

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            ex = binance.Binance(client, 20, api_key="KEY", api_secret="SECRET")
            return await ex.fetch(BUY)

    ads = asyncio.run(go())
    assert [a.price for a in ads] == [4835, 4830, 4840]
    u = urlsplit(str(seen["url"]))
    assert u.netloc == "api.binance.com" and u.path == "/sapi/v1/c2c/agent/ads/search"
    params = dict(parse_qsl(u.query))
    base = f"recvWindow={params['recvWindow']}&timestamp={params['timestamp']}"
    assert params["signature"] == hmac.new(b"SECRET", base.encode(), hashlib.sha256).hexdigest()
    assert seen["headers"]["X-MBX-APIKEY"] == "KEY"
    assert seen["headers"]["User-Agent"] == "binance-wallet/1.0.0 (Skill)"
    body = json.loads(seen["body"])
    assert body["fiat"] == "MMK" and body["asset"] == "USDT" and body["tradeType"] == "BUY"


def test_binance_api_error_message_surfaces():
    def handler(request):
        return httpx.Response(401, json={"code": -2015, "msg": "Invalid API-key, IP, or permissions for action."})

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await binance.Binance(client, 20, "KEY", "SECRET").fetch(SELL)

    with pytest.raises(ExchangeError, match="Invalid API-key, IP"):
        asyncio.run(go())


def test_binance_auto_enabled_when_key_set():
    from app.config import Settings
    assert Settings(_env_file=None).exchange_list == ["bybit", "bitget"]
    s = Settings(_env_file=None, binance_api_key="k", binance_api_secret="s")
    assert s.exchange_list == ["binance", "bybit", "bitget"]
    s = Settings(_env_file=None, binance_api_key="k", binance_api_secret="s", exchanges="bitget,binance")
    assert s.exchange_list == ["bitget", "binance"]                  # explicit order respected, no duplicate


def test_binance_backs_off_after_rate_limit():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(429, headers={"Retry-After": "120"}, json={"code": -1003, "msg": "Too many requests"})

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            ex = binance.Binance(client, 20, "KEY", "SECRET")
            for _ in range(3):
                with pytest.raises(ExchangeError, match="rate limited"):
                    await ex.fetch(BUY)
            return ex

    ex = asyncio.run(go())
    assert len(calls) == 1                       # no further calls during the back-off
    assert ex.backoff_until - __import__("time").time() > 100
