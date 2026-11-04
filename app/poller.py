"""Polls every exchange/side concurrently, stores snapshots, runs alerts."""

from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Protocol

from .alerts import AlertEngine, format_alert
from .config import Settings
from .db import Database
from .exchanges import SIDES, Exchange, Filters, Rate, apply_filters, compute_rate, drop_non_cash

log = logging.getLogger("poller")


class Notifier(Protocol):
    async def send(self, text: str) -> None: ...


class LogNotifier:
    async def send(self, text: str) -> None:
        log.warning("ALERT (telegram disabled): %s", text)


MIN_INTERVAL = 5.0
MAX_INTERVAL = 3600.0


def clamp_interval(seconds: float) -> float:
    return float(min(MAX_INTERVAL, max(MIN_INTERVAL, float(seconds))))


class Poller:
    def __init__(self, settings: Settings, db: Database, exchanges: list[Exchange],
                 notifier: Notifier, engine: AlertEngine | None = None):
        self.s = settings
        self.db = db
        self.exchanges = exchanges
        self.notifier = notifier
        self.engine = engine or AlertEngine()
        self.latest: dict[str, dict[str, dict]] = {e.name: {} for e in exchanges}  # ex -> side -> rate dict
        self.status: dict[str, dict] = {
            e.name: {"ok": None, "error": None, "fails": 0, "alerted": False, "last_ok": None, "empty": False}
            for e in exchanges
        }
        self.pay_methods_seen: dict[str, set[str]] = {e.name: set() for e in exchanges}
        self.top_ads: dict[str, dict[str, list[dict]]] = {e.name: {} for e in exchanges}  # ex -> side -> ads
        self.last_tick: int | None = None
        self.interval: float = clamp_interval(settings.poll_interval)
        self._wake = asyncio.Event()

    async def load_interval(self) -> None:
        """Dashboard-set interval (stored in DB) overrides POLL_INTERVAL from .env."""
        saved = await self.db.get_setting("poll_interval")
        if saved:
            self.interval = clamp_interval(saved)

    async def set_interval(self, seconds: float) -> float:
        self.interval = clamp_interval(seconds)
        await self.db.set_setting("poll_interval", self.interval)
        self._wake.set()  # apply now instead of after the current (possibly long) sleep
        log.info("poll interval set to %.0fs", self.interval)
        return self.interval

    async def run(self) -> None:
        await self.load_interval()
        while True:
            started = time.monotonic()
            try:
                await self.tick()
            except Exception:
                log.exception("poll tick failed")
            delay = max(0.5, self.interval - (time.monotonic() - started)) + random.uniform(0, 0.3)
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass

    async def tick(self) -> list[Rate]:
        now = int(time.time())
        filters_cfg: dict = await self.db.get_setting("filters", {}) or {}
        jobs = [(ex, side) for ex in self.exchanges for side in SIDES]
        results = await asyncio.gather(*(ex.fetch(side) for ex, side in jobs), return_exceptions=True)

        rates: list[Rate] = []
        errors: dict[str, str] = {}
        empty: dict[str, bool] = {}
        for (ex, side), res in zip(jobs, results):
            if isinstance(res, BaseException):
                errors[ex.name] = str(res)[:300]
                self.top_ads[ex.name].pop(side, None)  # don't show stale ads as live
                continue
            for ad in res:
                self.pay_methods_seen[ex.name].update(ad.pay_methods)
            ads = drop_non_cash(res, self.s.excluded_pay_methods.split(","))
            ads = apply_filters(ads, Filters.from_dict(filters_cfg.get(ex.name)))
            ranked = sorted((a for a in ads if a.price > 0), key=lambda a: a.price, reverse=(side == "SELL"))
            self.top_ads[ex.name][side] = [
                {"price": a.price, "merchant": a.merchant, "available": a.available, "min": a.min_amount,
                 "max": a.max_amount if a.max_amount != float("inf") else None, "pay_methods": a.pay_methods}
                for a in ranked[:10]
            ]
            rate = compute_rate(ex.name, side, ads, self.s.top_n)
            if rate is None:
                empty[ex.name] = True
                self.latest[ex.name].pop(side, None)
                continue
            rates.append(rate)
            self.latest[ex.name][side] = {"best": rate.best, "avg5": rate.avg5, "ads": rate.ads, "ts": now}

        await self.db.insert_snapshots([(now, r.exchange, r.side, r.best, r.avg5, r.ads) for r in rates])
        await self._update_status(now, errors, empty)
        await self._run_alerts(rates, now)
        self.last_tick = now
        return rates

    async def _update_status(self, now: int, errors: dict[str, str], empty: dict[str, bool]) -> None:
        for name, st in self.status.items():
            st["empty"] = empty.get(name, False)
            if name in errors:
                st.update(ok=False, error=errors[name], fails=st["fails"] + 1)
                log.warning("%s fetch failed (%d): %s", name, st["fails"], errors[name])
                if st["fails"] == self.s.fail_alert_after and not st["alerted"]:
                    st["alerted"] = True
                    await self._notify(f"⚠️ <b>{name}</b> failing for {st['fails']} polls:\n{errors[name][:200]}")
            else:
                if st["alerted"]:
                    await self._notify(f"✅ <b>{name}</b> recovered")
                st.update(ok=True, error=None, fails=0, alerted=False, last_ok=now)

    async def _run_alerts(self, rates: list[Rate], now: int) -> None:
        if not rates or await self.db.get_setting("alerts_paused", False):
            return
        rules = await self.db.list_rules(only_enabled=True)
        for f in self.engine.evaluate(rules, rates, now):
            await self.db.mark_fired(f.rule["id"], now)
            await self.db.add_event(f.rule["id"], now, f.rate.exchange, f.rate.side, f.rule["metric"],
                                    f.rule["op"], f.value, f.rule["threshold"])
            await self._notify(format_alert(f, now, self.s.display_tz))

    async def _notify(self, text: str) -> None:
        try:
            await self.notifier.send(text)
        except Exception:
            log.exception("notification failed")
