"""Links that open an exchange's own USDT/MMK P2P page, so a price seen here is one tap from buying.

Verified 2026-11-05:
- Bybit buy/sell pages load the USDT/MMK ad list.
- Bitget buy/sell pages are served directly (invalid paths redirect to the home page).
  Bitget hides P2P for UK visitors; the link still works elsewhere.
- Binance single-ad links use the format from Binance's official P2P skill docs.
Bybit and Bitget have no public single-ad link format, so their ads open the list page.
"""

from __future__ import annotations

from urllib.parse import quote

from .base import BUY

ASSET, FIAT = "USDT", "MMK"


def p2p_url(exchange: str, side: str, ad_id: str | None = None) -> str | None:
    """Exchange page for the user's side (BUY = user buys USDT). None for unknown exchanges."""
    buy = side == BUY
    if exchange == "binance":
        if ad_id:
            return f"https://c2c.binance.com/en/adv?code={quote(str(ad_id))}"
        path = "all-payments" if buy else "sell"
        return f"https://p2p.binance.com/en/trade/{path}/{ASSET}?fiat={FIAT}"
    if exchange == "bybit":
        return f"https://www.bybit.com/en/p2p/{'buy' if buy else 'sell'}/{ASSET}/{FIAT}"
    if exchange == "bitget":
        return f"https://www.bitget.com/p2p-trade/{'buy' if buy else 'sell'}/{ASSET}?fiatName={FIAT}"
    return None


def exchange_title(exchange: str) -> str:
    return exchange[:1].upper() + exchange[1:]
