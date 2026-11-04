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
from .exchanges import SIDES, Exchange, Filters, Rate, apply_filters, compute_rate

log = logging.getLogger("poller")


class Notifier(Protocol):
    async def send(self, text: str) -> None: ...


class LogNotifier:
    async def send(self, text: str) -> None:
        log.warning("ALERT (telegram disabled): %s", text)


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
        self.last_tick: int | None = None

    async def run(self) -> None:
        while True:
            started = time.monotonic()
            try:
                await self.tick()
            except Exception:
                log.exception("poll tick failed")
            elapsed = time.monotonic() - started
            await asyncio.sleep(max(0.5, self.s.poll_interval - elapsed) + random.uniform(0, 0.3))

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
                continue
            for ad in res:
                self.pay_methods_seen[ex.name].update(ad.pay_methods)
            ads = apply_filters(res, Filters.from_dict(filters_cfg.get(ex.name)))
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
