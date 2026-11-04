import httpx

from .base import BUY, SELL, SIDES, Ad, Exchange, ExchangeError, Filters, Rate, apply_filters, compute_rate
from .binance import Binance
from .bitget import Bitget
from .bybit import Bybit

REGISTRY: dict[str, type[Exchange]] = {"binance": Binance, "bybit": Bybit, "bitget": Bitget}


def build_exchanges(names: list[str], client: httpx.AsyncClient, rows: int) -> list[Exchange]:
    unknown = [n for n in names if n not in REGISTRY]
    if unknown:
        raise ValueError(f"Unknown exchanges: {unknown}. Valid: {list(REGISTRY)}")
    return [REGISTRY[n](client, rows) for n in names]


__all__ = ["BUY", "SELL", "SIDES", "Ad", "Exchange", "ExchangeError", "Filters", "Rate",
           "apply_filters", "compute_rate", "build_exchanges", "REGISTRY"]
