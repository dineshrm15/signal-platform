"""Historical daily closes for percentile context.

CBOE publishes full daily history per index:
https://cdn-api.cboe.com/api/global/us_indices/daily_prices/{SYM}_History.csv
Format: DATE,OPEN,HIGH,LOW,CLOSE with DATE as mm/dd/yyyy.
Some series (e.g. SKEW) may expose fewer columns; the CLOSE column is located by header.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterator
from datetime import date, datetime

import httpx

_HISTORY_URL = "https://cdn-api.cboe.com/api/global/us_indices/daily_prices/{symbol}_History.csv"


def parse_history_csv(text: str) -> Iterator[tuple[date, float]]:
    reader = csv.reader(io.StringIO(text))
    header = next(reader)
    cols = {name.strip().upper(): i for i, name in enumerate(header)}
    date_idx = cols.get("DATE", 0)
    close_idx = cols.get("CLOSE", len(header) - 1)
    for row in reader:
        if len(row) <= max(date_idx, close_idx):
            continue
        try:
            # date-only field; tz irrelevant
            day = datetime.strptime(row[date_idx].strip(), "%m/%d/%Y").date()  # noqa: DTZ007
            close = float(row[close_idx])
        except ValueError:
            continue
        yield day, close


def fetch_history(client: httpx.Client, symbol: str) -> list[tuple[date, float]]:
    """Returns [] if CBOE has no history file for the symbol."""
    resp = client.get(_HISTORY_URL.format(symbol=symbol))
    if resp.status_code in (403, 404):
        return []
    resp.raise_for_status()
    return list(parse_history_csv(resp.text))
