from .base import BUY, Ad, Exchange, ExchangeError

URL = "https://api2.bybit.com/fiat/otc/item/online"

# Bybit's ad list returns numeric payment-method IDs only. Names below come from Bybit's
# configuration/queryAllPaymentList (verified 2026-11-04). That endpoint is bot-protected, so it
# can't be fetched from the server; unknown IDs show as "#<id>" — add them here when they appear.
PAYMENT_NAMES: dict[str, str] = {
    "14": "Bank Transfer",
    "18": "Cash Deposit to Bank",
    "351": "WavePay",
    "431": "AYA Pay",
    "601": "KBZPay",
    "602": "CB Pay",
    "603": "Wave Money",
    "605": "uabpay",
    "607": "Spring Development Bank",
    "755": "NUG Pay",
    "773": "MMQR",
}


def payment_name(pid) -> str:
    return PAYMENT_NAMES.get(str(pid), f"#{pid}")


class Bybit(Exchange):
    """Bybit P2P public web endpoint.

    `side` is the ad's side: "1" = merchant sells USDT (user BUY), "0" = merchant buys USDT (user SELL).
    Verified live 2026-11-04: side 1 prices ascending, side 0 descending.
    """

    name = "bybit"

    async def fetch(self, side: str) -> list[Ad]:
        body = {
            "tokenId": "USDT",
            "currencyId": "MMK",
            "side": "1" if side == BUY else "0",
            "size": str(self.rows),
            "page": "1",
            "amount": "",
            "payment": [],
            "authMaker": False,
            "canTrade": False,
        }
        return parse(await self._post(URL, body))


def parse(data: dict) -> list[Ad]:
    if data.get("ret_code") not in (0, None) or "result" not in data:
        raise ExchangeError(f"bybit: ret_code={data.get('ret_code')} msg={data.get('ret_msg')}")
    ads = []
    for it in (data.get("result") or {}).get("items") or []:
        ads.append(Ad(
            price=float(it["price"]),
            min_amount=float(it.get("minAmount") or 0),
            max_amount=float(it.get("maxAmount") or float("inf")),
            available=float(it.get("lastQuantity") or 0),
            merchant=it.get("nickName", ""),
            pay_methods=[payment_name(p) for p in it.get("payments") or []],
        ))
    return ads
