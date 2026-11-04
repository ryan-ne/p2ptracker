from app.alerts import AlertEngine, format_alert
from app.exchanges import Rate


def rule(**kw):
    base = dict(id=1, exchange="binance", side="BUY", metric="best", op="<=", threshold=4550,
                enabled=1, cooldown_sec=300, last_fired_at=None)
    return {**base, **kw}


def rate(best, ex="binance", side="BUY", avg5=None):
    return Rate(ex, side, best=best, avg5=avg5 if avg5 is not None else best + 5, ads=10)


def test_fires_on_crossing_only():
    e, r = AlertEngine(), [rule()]
    assert e.evaluate(r, [rate(4600)], 0) == []
    fired = e.evaluate(r, [rate(4540)], 5)
    assert len(fired) == 1 and fired[0].value == 4540
    # still below threshold -> no repeat
    assert e.evaluate(r, [rate(4530)], 1000) == []


def test_cooldown_blocks_recross():
    e, r = AlertEngine(), [rule(cooldown_sec=300)]
    assert len(e.evaluate(r, [rate(4500)], 0)) == 1
    e.evaluate(r, [rate(4600)], 10)                       # back above
    assert e.evaluate(r, [rate(4500)], 20) == []          # re-cross within cooldown
    e.evaluate(r, [rate(4600)], 400)
    assert len(e.evaluate(r, [rate(4500)], 410)) == 1     # after cooldown


def test_persisted_last_fired_respected_after_restart():
    e = AlertEngine()
    assert e.evaluate([rule(last_fired_at=100)], [rate(4500)], 200) == []
    assert len(AlertEngine().evaluate([rule(last_fired_at=100)], [rate(4500)], 500)) == 1


def test_any_exchange_side_metric_and_ge():
    e = AlertEngine()
    r = [rule(exchange="any", side="SELL", metric="avg5", op=">=", threshold=4800)]
    rates = [rate(4900, "bybit", "SELL", avg5=4810), rate(4700, "bitget", "SELL", avg5=4700),
             rate(4900, "bybit", "BUY", avg5=4900)]
    fired = e.evaluate(r, rates, 0)
    assert [(f.rate.exchange, f.rate.side) for f in fired] == [("bybit", "SELL")]
    # different exchange crossing later fires independently
    fired = e.evaluate(r, [rate(4900, "bitget", "SELL", avg5=4850)], 10)
    assert [f.rate.exchange for f in fired] == ["bitget"]


def test_disabled_rule_and_forget():
    e = AlertEngine()
    assert e.evaluate([rule(enabled=0)], [rate(1)], 0) == []
    e.evaluate([rule()], [rate(1)], 0)
    e.forget(1)
    assert e._matched == {} and e._last_fired == {}


def test_format_alert():
    e = AlertEngine()
    f = e.evaluate([rule()], [rate(4520.5)], 0)[0]
    msg = format_alert(f, 0, "Asia/Yangon")
    assert "Binance BUY" in msg and "4,520.5 MMK" in msg and "≤ 4,550" in msg and "06:30:00" in msg
