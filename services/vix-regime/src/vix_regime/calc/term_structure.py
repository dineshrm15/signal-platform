"""Term-structure assembly: cash ratios and futures curve state.

Thresholds are documented 1:1 in METHODOLOGY.md — change both together.
"""

from __future__ import annotations

from vix_regime.models import (
    CashTermStructure,
    FuturesCurve,
    FuturesTermStructure,
    IndexQuote,
    SlopeState,
)

# VIX/VIX3M ratio boundaries for slope classification (cash curve).
CASH_SLOPE_BOUNDS = {
    "steep_contango_below": 0.85,
    "contango_below": 0.97,
    "flat_below": 1.03,
    "backwardation_below": 1.15,
    # >= backwardation_below -> INVERTED_CRISIS
}

# M1->M2 roll yield (%) boundaries for the futures curve.
FUTURES_SLOPE_BOUNDS = {
    "steep_contango_above_pct": 7.5,
    "contango_above_pct": 1.5,
    "flat_above_pct": -1.5,
    "backwardation_above_pct": -8.0,
    # < backwardation_above_pct -> INVERTED_CRISIS
}


def classify_cash_slope(ratio_vix_vix3m: float | None) -> SlopeState:
    if ratio_vix_vix3m is None:
        return SlopeState.FLAT
    b = CASH_SLOPE_BOUNDS
    if ratio_vix_vix3m < b["steep_contango_below"]:
        return SlopeState.STEEP_CONTANGO
    if ratio_vix_vix3m < b["contango_below"]:
        return SlopeState.CONTANGO
    if ratio_vix_vix3m < b["flat_below"]:
        return SlopeState.FLAT
    if ratio_vix_vix3m < b["backwardation_below"]:
        return SlopeState.BACKWARDATION
    return SlopeState.INVERTED_CRISIS


def classify_futures_slope(m1_m2_roll_yield_pct: float) -> SlopeState:
    b = FUTURES_SLOPE_BOUNDS
    if m1_m2_roll_yield_pct > b["steep_contango_above_pct"]:
        return SlopeState.STEEP_CONTANGO
    if m1_m2_roll_yield_pct > b["contango_above_pct"]:
        return SlopeState.CONTANGO
    if m1_m2_roll_yield_pct > b["flat_above_pct"]:
        return SlopeState.FLAT
    if m1_m2_roll_yield_pct > b["backwardation_above_pct"]:
        return SlopeState.BACKWARDATION
    return SlopeState.INVERTED_CRISIS


def _ratio(indices: dict[str, IndexQuote], num: str, den: str) -> float | None:
    a, b = indices.get(num), indices.get(den)
    if a is None or b is None or b.price == 0:
        return None
    return round(a.price / b.price, 4)


def build_cash_term_structure(indices: dict[str, IndexQuote]) -> CashTermStructure:
    ratio_vix_vix3m = _ratio(indices, "VIX", "VIX3M")
    return CashTermStructure(
        values={sym: q.price for sym, q in indices.items()},
        ratio_vix9d_vix=_ratio(indices, "VIX9D", "VIX"),
        ratio_vix_vix3m=ratio_vix_vix3m,
        ratio_vix3m_vix6m=_ratio(indices, "VIX3M", "VIX6M"),
        slope=classify_cash_slope(ratio_vix_vix3m),
    )


def build_futures_term_structure(
    curve: FuturesCurve, vix_spot: float
) -> FuturesTermStructure | None:
    monthlies = curve.monthlies
    if len(monthlies) < 2:
        return None
    m1, m2 = monthlies[0], monthlies[1]
    roll_yield_pct = (m2.price - m1.price) / m1.price * 100
    spread_today = m2.price - m1.price
    spread_prev = (
        m2.prev_settlement - m1.prev_settlement
        if m1.prev_settlement > 0 and m2.prev_settlement > 0
        else spread_today
    )
    return FuturesTermStructure(
        m1_symbol=m1.symbol,
        m2_symbol=m2.symbol,
        m1_price=m1.price,
        m2_price=m2.price,
        front_basis_pct=round((m1.price - vix_spot) / vix_spot * 100, 2) if vix_spot else 0.0,
        m1_m2_roll_yield_pct=round(roll_yield_pct, 2),
        curve_twist_pct=round(spread_today - spread_prev, 4),
        slope=classify_futures_slope(roll_yield_pct),
    )
