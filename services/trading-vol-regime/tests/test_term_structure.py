from datetime import date

from trading_vol_regime.calc.term_structure import (
    build_cash_term_structure,
    build_futures_term_structure,
    classify_cash_slope,
    classify_futures_slope,
)
from trading_vol_regime.models import FuturesContract, FuturesCurve, IndexQuote, SlopeState


def _quote(symbol: str, price: float) -> IndexQuote:
    return IndexQuote(symbol=symbol, price=price, prev_close=price, change_pct=0.0)


def _contract(symbol: str, exp: date, price: float, prev: float, weekly=False):
    return FuturesContract(
        symbol=symbol,
        expiration=exp,
        price=price,
        settlement=price,
        prev_settlement=prev,
        volume=100,
        open_interest=1000,
        is_weekly=weekly,
    )


def test_cash_slope_boundaries():
    assert classify_cash_slope(0.80) == SlopeState.STEEP_CONTANGO
    assert classify_cash_slope(0.90) == SlopeState.CONTANGO
    assert classify_cash_slope(1.00) == SlopeState.FLAT
    assert classify_cash_slope(1.10) == SlopeState.BACKWARDATION
    assert classify_cash_slope(1.20) == SlopeState.INVERTED_CRISIS
    assert classify_cash_slope(None) == SlopeState.FLAT


def test_futures_slope_boundaries():
    assert classify_futures_slope(9.0) == SlopeState.STEEP_CONTANGO
    assert classify_futures_slope(4.0) == SlopeState.CONTANGO
    assert classify_futures_slope(0.0) == SlopeState.FLAT
    assert classify_futures_slope(-4.0) == SlopeState.BACKWARDATION
    assert classify_futures_slope(-10.0) == SlopeState.INVERTED_CRISIS


def test_build_cash_term_structure():
    indices = {
        "VIX": _quote("VIX", 15.0),
        "VIX9D": _quote("VIX9D", 13.5),
        "VIX3M": _quote("VIX3M", 17.0),
        "VIX6M": _quote("VIX6M", 18.0),
    }
    ts = build_cash_term_structure(indices)
    assert ts.ratio_vix9d_vix == 0.9
    assert ts.ratio_vix_vix3m == round(15.0 / 17.0, 4)
    assert ts.ratio_vix3m_vix6m == round(17.0 / 18.0, 4)
    assert ts.slope == SlopeState.CONTANGO
    assert ts.values["VIX"] == 15.0


def test_build_futures_term_structure_skips_weeklies():
    curve = FuturesCurve(
        contracts=[
            _contract("VX32/Q6", date(2026, 8, 12), 16.8, 16.9, weekly=True),
            _contract("VX/Q6", date(2026, 8, 19), 17.0, 17.1),
            _contract("VX/U6", date(2026, 9, 16), 18.5, 18.4),
        ]
    )
    ts = build_futures_term_structure(curve, vix_spot=15.0)
    assert ts is not None
    assert ts.m1_symbol == "VX/Q6"
    assert ts.m2_symbol == "VX/U6"
    assert ts.m1_m2_roll_yield_pct == round((18.5 - 17.0) / 17.0 * 100, 2)
    assert ts.front_basis_pct == round((17.0 - 15.0) / 15.0 * 100, 2)
    # twist: today's spread 1.5 vs prev settlement spread 1.3
    assert ts.curve_twist_pct == round(1.5 - 1.3, 4)
    assert ts.slope == SlopeState.STEEP_CONTANGO


def test_build_futures_needs_two_monthlies():
    curve = FuturesCurve(contracts=[_contract("VX/Q6", date(2026, 8, 19), 17.0, 17.0)])
    assert build_futures_term_structure(curve, vix_spot=15.0) is None
