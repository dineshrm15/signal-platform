"""Percentile context: where does a value sit in its own recent history?"""

from __future__ import annotations

from bisect import bisect_left

TRADING_DAYS_1Y = 252
TRADING_DAYS_5Y = 1260


def percentile_of(value: float, history: list[float]) -> float | None:
    """Fraction of historical values strictly below `value`, in [0, 1].

    `history` is the raw (unsorted) window of daily closes. Returns None when
    the window is too thin to be meaningful (< 60 observations).
    """
    if len(history) < 60:
        return None
    ordered = sorted(history)
    return bisect_left(ordered, value) / len(ordered)


def percentile_context(value: float, daily_closes: list[float]) -> dict[str, float | None]:
    """`daily_closes` ordered oldest -> newest. Windows: trailing 1y and 5y."""
    return {
        "1y": percentile_of(value, daily_closes[-TRADING_DAYS_1Y:]),
        "5y": percentile_of(value, daily_closes[-TRADING_DAYS_5Y:]),
    }
