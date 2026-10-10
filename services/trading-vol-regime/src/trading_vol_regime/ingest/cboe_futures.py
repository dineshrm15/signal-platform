"""CBOE VX futures curve (delayed, public API).

Endpoint: https://www.cboe.com/us/futures/api/get_quotes_combined/?symbol=VX&rootsymbol=null

Symbols: "VX/Q6" = standard monthly; "VX32/Q6" = weekly (digits after VX).
Illiquid contracts show last_price 0.0 — fall back to settlement.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx

from trading_vol_regime.models import FuturesContract, FuturesCurve

_FUTURES_URL = (
    "https://www.cboe.com/us/futures/api/get_quotes_combined/"
    "?symbol=VX&rootsymbol=null&timezone=America%2FChicago"
)


def _is_weekly(symbol: str) -> bool:
    root = symbol.split("/", 1)[0]
    return root != "VX"


def parse_futures_curve(raw: dict[str, Any]) -> FuturesCurve:
    contracts = []
    for row in raw["data"]:
        symbol = row["symbol"]
        root = symbol.split("/", 1)[0]
        if not root.startswith("VX") or root.startswith("VXM"):
            continue
        last = float(row.get("last_price") or 0.0)
        settlement = float(row.get("settlement") or 0.0)
        price = last if last > 0 else settlement
        if price <= 0:
            continue
        contracts.append(
            FuturesContract(
                symbol=symbol,
                # date-only field; tz irrelevant
                expiration=datetime.strptime(row["expiration"], "%m/%d/%Y").date(),  # noqa: DTZ007
                price=price,
                settlement=settlement,
                prev_settlement=float(row.get("prev_settlement") or 0.0),
                volume=int(row.get("volume") or 0),
                open_interest=int(row.get("prev_open_int") or 0),
                is_weekly=_is_weekly(symbol),
            )
        )
    return FuturesCurve(contracts=sorted(contracts, key=lambda c: c.expiration))


def fetch_futures_curve(client: httpx.Client) -> FuturesCurve:
    resp = client.get(_FUTURES_URL)
    resp.raise_for_status()
    return parse_futures_curve(resp.json())
