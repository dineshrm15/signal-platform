"""CBOE delayed index quotes (15-min delayed, public CDN).

Endpoint per symbol: https://cdn-api.cboe.com/api/global/delayed_quotes/quotes/_{SYM}.json
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx

from vix_regime.models import IndexQuote

INDEX_SYMBOLS = ["VIX", "VIX9D", "VIX3M", "VIX6M", "VIX1Y", "VVIX", "SKEW"]

_QUOTE_URL = "https://cdn-api.cboe.com/api/global/delayed_quotes/quotes/_{symbol}.json"


def parse_index_quote(symbol: str, raw: dict[str, Any]) -> IndexQuote:
    data = raw["data"]
    price = float(data["current_price"])
    prev_close = float(data["prev_day_close"])
    change_pct = float(data.get("price_change_percent") or 0.0)
    last_trade = data.get("last_trade_time")
    ts = raw.get("timestamp")
    return IndexQuote(
        symbol=symbol,
        price=price,
        prev_close=prev_close,
        change_pct=change_pct,
        last_trade_time=datetime.fromisoformat(last_trade) if last_trade else None,
        source_timestamp=datetime.fromisoformat(ts) if ts else None,
    )


def fetch_index_quotes(
    client: httpx.Client, symbols: list[str] | None = None
) -> dict[str, IndexQuote]:
    quotes: dict[str, IndexQuote] = {}
    for symbol in symbols or INDEX_SYMBOLS:
        resp = client.get(_QUOTE_URL.format(symbol=symbol))
        resp.raise_for_status()
        quotes[symbol] = parse_index_quote(symbol, resp.json())
    return quotes
