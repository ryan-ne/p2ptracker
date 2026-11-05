from .base import BUY, Ad, Exchange, ExchangeError

URL = "https://www.bitget.com/v1/p2p/pub/adv/queryAdvList"


class Bitget(Exchange):
    """Bitget P2P public web endpoint.

    `side`: 1 = merchant sells USDT (user BUY), 2 = merchant buys USDT (user SELL).
    Verified live 2026-11-04: side 1 prices ascending, side 2 descending.
    """

    name = "bitget"

    async def fetch(self, side: str) -> list[Ad]:
        body = {
            "side": 1 if side == BUY else 2,
            "pageNo": 1,
            "pageSize": self.rows,
            "coinCode": "USDT",
            "fiatCode": "MMK",
            "languageType": 0,
        }
        return parse(await self._post(URL, body, headers={"locale": "en_US"}))


def parse(data: dict) -> list[Ad]:
    if str(data.get("code")) != "00000":
        raise ExchangeError(f"bitget: code={data.get('code')} msg={data.get('msg')}")
    ads = []
    for it in (data.get("data") or {}).get("dataList") or []:
        pays = it.get("paymethodInfo") or []
        ads.append(Ad(
            price=float(it["price"]),
            min_amount=float(it.get("minAmount") or 0),
            max_amount=float(it.get("maxAmount") or float("inf")),
            available=float(it.get("lastAmount") or 0),
            merchant=(it.get("nickName") or "").strip(),
            pay_methods=[(p.get("paymethodName") or str(p.get("paymethodId", ""))).strip()
                         for p in pays if isinstance(p, dict)],
            ad_id=str(it.get("adNo") or ""),
        ))
    return ads
