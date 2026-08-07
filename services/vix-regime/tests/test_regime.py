from vix_regime.calc.regime import RegimeInputs, build_narrative, classify
from vix_regime.calc.term_structure import build_cash_term_structure
from vix_regime.models import IndexQuote, Regime


def _quote(symbol: str, price: float) -> IndexQuote:
    return IndexQuote(symbol=symbol, price=price, prev_close=price, change_pct=0.0)


def _inputs(
    vix: float,
    vix9d: float,
    vix3m: float,
    vix_1y: float | None = 0.3,
    vix_5y: float | None = 0.3,
    vvix_1y: float | None = 0.4,
    skew_1y: float | None = 0.4,
) -> RegimeInputs:
    cash = build_cash_term_structure(
        {
            "VIX": _quote("VIX", vix),
            "VIX9D": _quote("VIX9D", vix9d),
            "VIX3M": _quote("VIX3M", vix3m),
        }
    )
    return RegimeInputs(
        cash=cash,
        futures=None,
        percentiles={
            "VIX": {"1y": vix_1y, "5y": vix_5y},
            "VVIX": {"1y": vvix_1y},
            "SKEW": {"1y": skew_1y},
        },
    )


def test_calm_carry():
    result = classify(_inputs(vix=13, vix9d=11.7, vix3m=16, vix_1y=0.2))
    assert result.label == Regime.CALM_CARRY
    assert 0 < result.confidence <= 0.9
    assert result.rationale


def test_compressed_spring():
    result = classify(_inputs(vix=13, vix9d=11.7, vix3m=15, vix_1y=0.2, vvix_1y=0.9))
    assert result.label == Regime.COMPRESSED_SPRING
    assert "calm_surface_rich_tails" in result.fired_rules


def test_stress_building_on_flat_curve():
    result = classify(_inputs(vix=15, vix9d=14, vix3m=15, vix_1y=0.5))
    assert result.label == Regime.STRESS_BUILDING
    assert "cash_flat" in result.fired_rules


def test_stress_building_on_short_end_flattening():
    # 9D/VIX = 0.96 crosses the 0.95 flattening threshold while curve still contango
    result = classify(_inputs(vix=16, vix9d=15.36, vix3m=17.5, vix_1y=0.5))
    assert result.label == Regime.STRESS_BUILDING
    assert "short_end_flattening" in result.fired_rules


def test_risk_off_on_backwardation():
    result = classify(_inputs(vix=20, vix9d=19, vix3m=18.5, vix_1y=0.85, vix_5y=0.8))
    assert result.label == Regime.RISK_OFF
    assert "cash_backwardation" in result.fired_rules


def test_capitulation():
    result = classify(
        _inputs(vix=45, vix9d=55, vix3m=30, vix_1y=1.0, vix_5y=0.995, vvix_1y=0.99)
    )
    assert result.label == Regime.CAPITULATION
    assert result.confidence >= 0.7
    assert "cash_inverted_crisis" in result.fired_rules


def test_missing_percentiles_do_not_crash():
    result = classify(_inputs(vix=13, vix9d=11.7, vix3m=16, vix_1y=None, vix_5y=None,
                              vvix_1y=None, skew_1y=None))
    assert result.label == Regime.CALM_CARRY


def test_narrative_mentions_label_and_ratio():
    inputs = _inputs(vix=13, vix9d=11.7, vix3m=16, vix_1y=0.2)
    assessment = classify(inputs)
    text = build_narrative(assessment, inputs.cash)
    assert assessment.label.value in text
    assert str(inputs.cash.ratio_vix_vix3m) in text
