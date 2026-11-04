from __future__ import annotations

import time
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from .alerts import validate_rule

router = APIRouter(prefix="/api")

RANGES = {"1h": 3600, "6h": 6 * 3600, "24h": 86400, "7d": 7 * 86400, "30d": 30 * 86400, "90d": 90 * 86400,
          "1y": 365 * 86400, "all": 20 * 365 * 86400}


def pick_bucket(span: int) -> int:
    if span <= 6 * 3600:
        return 0          # raw 5s points
    if span <= 2 * 86400:
        return 60
    if span <= 14 * 86400:
        return 300
    return 3600


class RuleIn(BaseModel):
    exchange: str
    side: Literal["BUY", "SELL"]
    metric: Literal["best", "avg5"] = "best"
    op: Literal["<=", ">="]
    threshold: float = Field(gt=0)
    cooldown_sec: int = Field(default=300, ge=0, le=7 * 86400)


class RulePatch(BaseModel):
    enabled: bool


class ExchangeFilter(BaseModel):
    pay_methods: list[str] = []
    amount: float | None = Field(default=None, gt=0)


class SettingsIn(BaseModel):
    filters: dict[str, ExchangeFilter] | None = None
    alerts_paused: bool | None = None


@router.get("/latest")
async def latest(request: Request):
    p = request.app.state.poller
    out = {}
    for ex, sides in p.latest.items():
        st = p.status[ex]
        out[ex] = {**sides, "status": {k: st[k] for k in ("ok", "error", "fails", "last_ok", "empty")}}
    buys = [(s["BUY"]["best"], ex) for ex, s in p.latest.items() if "BUY" in s]
    sells = [(s["SELL"]["best"], ex) for ex, s in p.latest.items() if "SELL" in s]
    return {
        "ts": p.last_tick,
        "interval": request.app.state.settings.poll_interval,
        "exchanges": out,
        "best_buy": dict(zip(("price", "exchange"), min(buys))) if buys else None,
        "best_sell": dict(zip(("price", "exchange"), max(sells))) if sells else None,
    }


@router.get("/history")
async def history(
    request: Request,
    range: str = Query("24h"),
    exchange: str | None = None,
    side: Literal["BUY", "SELL"] | None = None,
    start: int | None = Query(None, alias="from"),
    end: int | None = Query(None, alias="to"),
):
    now = int(time.time())
    end = end or now
    if start is None:
        if range not in RANGES:
            raise HTTPException(400, f"range must be one of {list(RANGES)}")
        start = end - RANGES[range]
    if start >= end:
        raise HTTPException(400, "from must be before to")
    bucket = pick_bucket(end - start)
    rows = await request.app.state.db.history(exchange, side, start, end, bucket)
    series: dict[tuple[str, str], list] = {}
    for r in rows:
        series.setdefault((r["exchange"], r["side"]), []).append(
            [r["t"], round(r["best"], 2), round(r["avg5"], 2) if r["avg5"] is not None else None])
    return {
        "from": start, "to": end, "bucket": bucket,
        "series": [{"exchange": ex, "side": sd, "points": pts} for (ex, sd), pts in sorted(series.items())],
    }


@router.get("/alerts")
async def list_alerts(request: Request):
    return await request.app.state.db.list_rules()


@router.post("/alerts", status_code=201)
async def create_alert(rule: RuleIn, request: Request):
    exchange = rule.exchange.lower()
    err = validate_rule(exchange, rule.side, rule.metric, rule.op, request.app.state.settings.exchange_list)
    if err:
        raise HTTPException(422, err)
    rid = await request.app.state.db.add_rule(exchange, rule.side, rule.metric, rule.op, rule.threshold,
                                              rule.cooldown_sec, int(time.time()))
    return {"id": rid}


@router.patch("/alerts/{rule_id}")
async def patch_alert(rule_id: int, body: RulePatch, request: Request):
    if not await request.app.state.db.set_rule_enabled(rule_id, body.enabled):
        raise HTTPException(404, "rule not found")
    return {"ok": True}


@router.delete("/alerts/{rule_id}")
async def delete_alert(rule_id: int, request: Request):
    if not await request.app.state.db.delete_rule(rule_id):
        raise HTTPException(404, "rule not found")
    request.app.state.poller.engine.forget(rule_id)
    return {"ok": True}


@router.get("/alert-events")
async def alert_events(request: Request, limit: int = Query(50, ge=1, le=500)):
    return await request.app.state.db.list_events(limit)


@router.get("/settings")
async def get_settings(request: Request):
    db, p = request.app.state.db, request.app.state.poller
    return {
        "filters": await db.get_setting("filters", {}),
        "alerts_paused": await db.get_setting("alerts_paused", False),
        "pay_methods_seen": {ex: sorted(v) for ex, v in p.pay_methods_seen.items()},
        "exchanges": request.app.state.settings.exchange_list,
        "telegram_enabled": request.app.state.bot is not None,
    }


@router.put("/settings")
async def put_settings(body: SettingsIn, request: Request):
    db = request.app.state.db
    if body.filters is not None:
        valid = set(request.app.state.settings.exchange_list)
        bad = set(body.filters) - valid
        if bad:
            raise HTTPException(422, f"unknown exchanges: {sorted(bad)}")
        await db.set_setting("filters", {k: v.model_dump() for k, v in body.filters.items()})
    if body.alerts_paused is not None:
        await db.set_setting("alerts_paused", body.alerts_paused)
    return {"ok": True}
