from __future__ import annotations

from dataclasses import dataclass, field
from statistics import fmean

import httpx

BUY = "BUY"    # user buys USDT  -> merchants' sell ads, lower price is better
SELL = "SELL"  # user sells USDT -> merchants' buy ads, higher price is better
SIDES = (BUY, SELL)

BROWSER_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/json",
}


@dataclass
class Ad:
    price: float
    min_amount: float = 0.0          # MMK
    max_amount: float = float("inf")  # MMK
    available: float = 0.0           # USDT
    merchant: str = ""
    pay_methods: list[str] = field(default_factory=list)
    ad_id: str = ""                  # exchange's ad number (used to link straight to the ad where possible)


@dataclass
class Filters:
    pay_methods: list[str] = field(default_factory=list)  # match any (case-insensitive, name or id)
    amount: float | None = None                            # MMK trade size the ad must accept

    @classmethod
    def from_dict(cls, d: dict | None) -> "Filters":
        d = d or {}
        amt = d.get("amount")
        return cls(pay_methods=list(d.get("pay_methods") or []), amount=float(amt) if amt else None)


@dataclass
class Rate:
    exchange: str
    side: str
    best: float
    avg5: float
    ads: int


class ExchangeError(Exception):
    pass


class Exchange:
    name: str = ""

    def __init__(self, client: httpx.AsyncClient, rows: int = 20):
        self.client = client
        self.rows = rows

    async def fetch(self, side: str) -> list[Ad]:
        raise NotImplementedError

    async def _post(self, url: str, body: dict, headers: dict | None = None) -> dict:
        try:
            r = await self.client.post(url, json=body, headers={**BROWSER_HEADERS, **(headers or {})})
            r.raise_for_status()
            return r.json()
        except (httpx.HTTPError, ValueError) as e:
            raise ExchangeError(f"{self.name}: {type(e).__name__}: {e}") from e


def drop_non_cash(ads: list[Ad], excluded: list[str]) -> list[Ad]:
    """Drop ads whose ONLY payment methods are non-cash ones (e.g. airtime top-up). Those trade at a
    premium because the payment isn't real money, and would otherwise show up as the "best" price."""
    bad = {e.strip().lower() for e in excluded if e.strip()}
    if not bad:
        return ads
    return [a for a in ads if not a.pay_methods or any(p.lower() not in bad for p in a.pay_methods)]


def apply_filters(ads: list[Ad], f: Filters) -> list[Ad]:
    wanted = {p.lower() for p in f.pay_methods}
    out = []
    for ad in ads:
        if wanted and not wanted.intersection(p.lower() for p in ad.pay_methods):
            continue
        if f.amount is not None and not (ad.min_amount <= f.amount <= ad.max_amount):
            continue
        out.append(ad)
    return out


def compute_rate(exchange: str, side: str, ads: list[Ad], top_n: int = 5) -> Rate | None:
    """Best price and average of the top-N ads, ordered best-first for the user's side."""
    ads = [a for a in ads if a.price > 0]
    if not ads:
        return None
    ads = sorted(ads, key=lambda a: a.price, reverse=(side == SELL))
    top = ads[:top_n]
    return Rate(exchange, side, best=top[0].price, avg5=round(fmean(a.price for a in top), 4), ads=len(ads))
