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
