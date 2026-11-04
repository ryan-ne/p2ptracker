"""Binance P2P.

Binance only shows MMK ads to Myanmar-verified accounts, so anonymous requests (the public web
endpoint) return an empty list. With a read-only API key from such an account, the official signed
C2C agent endpoint `POST /sapi/v1/c2c/agent/ads/search` returns the real MMK ads.

Key setup: Binance -> API Management -> create a key with ONLY "Enable Reading", restricted to
the server's IP. Put it in .env as BINANCE_API_KEY / BINANCE_API_SECRET.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from urllib.parse import urlencode

import httpx

from .base import BROWSER_HEADERS, BUY, Ad, Exchange, ExchangeError

PUBLIC_URL = "https://p2p.binance.com/bapi/c2c/v2/friendly/c2c/adv/search"
SAPI_BASE = "https://api.binance.com"
ADS_SEARCH_PATH = "/sapi/v1/c2c/agent/ads/search"
SKILL_UA = "binance-wallet/1.0.0 (Skill)"
RECV_WINDOW = 60000


def sign(query: str, secret: str) -> str:
    return hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()


def signed_query(secret: str, now_ms: int | None = None) -> str:
    # SAPI signs the query string in insertion order (do NOT sort). The JSON body is not signed.
    query = urlencode({"recvWindow": RECV_WINDOW, "timestamp": now_ms or int(time.time() * 1000)})
    return f"{query}&signature={sign(query, secret)}"


class Binance(Exchange):
    """tradeType is from the user's perspective (BUY = user buys USDT, i.e. merchants' sell ads)."""

    name = "binance"

    def __init__(self, client: httpx.AsyncClient, rows: int = 20, api_key: str = "", api_secret: str = ""):
        super().__init__(client, rows)
        self.api_key, self.api_secret = api_key, api_secret
        self.backoff_until = 0.0  # set after HTTP 429/418; Binance bans IPs that keep calling

    @property
    def authenticated(self) -> bool:
        return bool(self.api_key and self.api_secret)

    async def fetch(self, side: str) -> list[Ad]:
        trade_type = "BUY" if side == BUY else "SELL"
        if self.authenticated:
            return parse(await self._signed_search(trade_type))
        body = {
            "asset": "USDT", "fiat": "MMK", "tradeType": trade_type, "page": 1, "rows": min(self.rows, 20),
            "payTypes": [], "countries": [], "publisherType": None, "proMerchantAds": False,
            "shieldMerchantAds": False, "filterType": "all", "classifies": ["mass", "profession", "fiat_trade"],
        }
        return parse(await self._post(PUBLIC_URL, body, headers={"clienttype": "web", "lang": "en"}))

    async def _signed_search(self, trade_type: str) -> dict:
        wait = self.backoff_until - time.time()
        if wait > 0:
            raise ExchangeError(f"binance: rate limited, pausing {wait:.0f}s more")
        url = f"{SAPI_BASE}{ADS_SEARCH_PATH}?{signed_query(self.api_secret)}"
        body = {"fiat": "MMK", "asset": "USDT", "tradeType": trade_type, "page": 1, "rows": min(self.rows, 20)}
        headers = {**BROWSER_HEADERS, "User-Agent": SKILL_UA, "X-MBX-APIKEY": self.api_key}
        try:
            r = await self.client.post(url, json=body, headers=headers)
        except httpx.HTTPError as e:
            raise ExchangeError(f"binance: {type(e).__name__}: {e}") from e
        if r.status_code in (418, 429):
            try:
                retry = float(r.headers.get("Retry-After", ""))
            except ValueError:
                retry = 0
            self.backoff_until = time.time() + max(retry, 60 if r.status_code == 429 else 300)
            raise ExchangeError(f"binance: HTTP {r.status_code} rate limited; pausing until Retry-After")
        try:
            data = r.json()
        except ValueError:
            raise ExchangeError(f"binance: HTTP {r.status_code}: non-JSON response") from None
        if r.status_code >= 400:
            # e.g. {"code":-2015,"msg":"Invalid API-key, IP, or permissions for action."}; 429/418 = rate limited
            raise ExchangeError(f"binance: HTTP {r.status_code}: {data.get('msg') or data.get('message') or data}")
        return data


def parse(data: dict) -> list[Ad]:
    if data.get("code") != "000000":
        raise ExchangeError(f"binance: code={data.get('code')} msg={data.get('message') or data.get('msg')}")
    ads = []
    for item in data.get("data") or []:
        adv, user = item.get("adv") or {}, item.get("advertiser") or {}
        ads.append(Ad(
            price=float(adv["price"]),
            min_amount=float(adv.get("minSingleTransAmount") or 0),
            max_amount=float(adv.get("maxSingleTransAmount") or float("inf")),
            available=float(adv.get("surplusAmount") or adv.get("tradableQuantity") or 0),
            merchant=user.get("nickName", ""),
            pay_methods=[m.get("tradeMethodName") or m.get("identifier") or ""
                         for m in adv.get("tradeMethods") or []],
        ))
    return ads
