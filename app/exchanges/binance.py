from .base import BUY, Ad, Exchange, ExchangeError

URL = "https://p2p.binance.com/bapi/c2c/v2/friendly/c2c/adv/search"


class Binance(Exchange):
    """Binance P2P public web endpoint.

    tradeType is from the user's perspective (BUY = user buys USDT from sell ads).
    Note: Binance returns an empty list (not an error) to requests from some regions.
    """

    name = "binance"

    async def fetch(self, side: str) -> list[Ad]:
        body = {
            "asset": "USDT",
            "fiat": "MMK",
            "tradeType": "BUY" if side == BUY else "SELL",
            "page": 1,
            "rows": min(self.rows, 20),
            "payTypes": [],
            "countries": [],
            "publisherType": None,
            "proMerchantAds": False,
            "shieldMerchantAds": False,
            "filterType": "all",
            "classifies": ["mass", "profession", "fiat_trade"],
        }
        data = await self._post(URL, body, headers={"clienttype": "web", "lang": "en"})
        return parse(data)


def parse(data: dict) -> list[Ad]:
    if data.get("code") != "000000":
        raise ExchangeError(f"binance: code={data.get('code')} msg={data.get('message')}")
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
