"""Threshold alert engine: fires when a rule's condition *becomes* true (crossing), with a cooldown."""

from __future__ import annotations

import html
import operator
from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from .exchanges import Rate
from .exchanges.links import exchange_title, p2p_url

OPS = {"<=": operator.le, ">=": operator.ge}
METRICS = ("best", "avg5")
EXCHANGE_ANY = "any"


@dataclass
class Fired:
    rule: dict
    rate: Rate
    value: float


class AlertEngine:
    def __init__(self) -> None:
        self._matched: dict[tuple[int, str], bool] = {}   # (rule_id, exchange) -> condition held last poll
        self._last_fired: dict[tuple[int, str], int] = {}

    def evaluate(self, rules: list[dict], rates: list[Rate], now: int) -> list[Fired]:
        fired: list[Fired] = []
        for rule in rules:
            if not rule.get("enabled", 1):
                continue
            for rate in rates:
                if rate.side != rule["side"] or rule["exchange"] not in (EXCHANGE_ANY, rate.exchange):
                    continue
                value = rate.best if rule["metric"] == "best" else rate.avg5
                cond = OPS[rule["op"]](value, rule["threshold"])
                key = (rule["id"], rate.exchange)
                was = self._matched.get(key, False)
                self._matched[key] = cond
                if not cond or was:
                    continue
                last = self._last_fired.get(key, rule.get("last_fired_at"))
                if last is not None and now - last < rule["cooldown_sec"]:
                    continue
                self._last_fired[key] = now
                fired.append(Fired(rule, rate, value))
        return fired

    def forget(self, rule_id: int) -> None:
        for d in (self._matched, self._last_fired):
            for k in [k for k in d if k[0] == rule_id]:
                del d[k]


def fmt_price(v: float) -> str:
    return f"{v:,.2f}".rstrip("0").rstrip(".")


def fmt_time(ts: int, tz: str) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).astimezone(ZoneInfo(tz)).strftime("%Y-%m-%d %H:%M:%S")


def format_alert(f: Fired, now: int, tz: str) -> str:
    r, rate = f.rule, f.rate
    sym = "≤" if r["op"] == "<=" else "≥"
    return (
        f"🔔 <b>{rate.exchange.capitalize()} {rate.side}</b> {r['metric']} "
        f"<b>{fmt_price(f.value)} MMK</b> {sym} {fmt_price(r['threshold'])}\n"
        f"best {fmt_price(rate.best)} · avg5 {fmt_price(rate.avg5)} · {rate.ads} ads\n"
        f"rule #{r['id']} · {fmt_time(now, tz)}"
        + exchange_link_line(rate.exchange, rate.side)
    )


def exchange_link_line(exchange: str, side: str) -> str:
    url = p2p_url(exchange, side)
    if not url:
        return ""
    verb = "Buy" if side == "BUY" else "Sell"
    return f'\n<a href="{html.escape(url)}">{verb} USDT on {exchange_title(exchange)} ↗</a>'


def format_rule(r: dict) -> str:
    state = "" if r.get("enabled", 1) else " (off)"
    return (f"#{r['id']} {r['exchange']} {r['side']} {r['metric']} {r['op']} "
            f"{fmt_price(r['threshold'])} · cd {r['cooldown_sec']}s{state}")


def validate_rule(exchange: str, side: str, metric: str, op: str, valid_exchanges: list[str]) -> str | None:
    if exchange not in (*valid_exchanges, EXCHANGE_ANY):
        return f"exchange must be one of {', '.join([*valid_exchanges, EXCHANGE_ANY])}"
    if side not in ("BUY", "SELL"):
        return "side must be BUY or SELL"
    if metric not in METRICS:
        return "metric must be best or avg5"
    if op not in OPS:
        return "op must be <= or >="
    return None
