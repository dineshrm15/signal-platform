"""Rules-based volatility regime classifier.

Ordered decision list — the first label whose trigger conditions fire wins.
Every threshold here is documented 1:1 in METHODOLOGY.md; change both together.
No ML, no LLM: deterministic, auditable, backtestable.
"""

from __future__ import annotations

from dataclasses import dataclass

from vix_regime.models import (
    CashTermStructure,
    FuturesTermStructure,
    Regime,
    RegimeAssessment,
    SlopeState,
)

# Percentile thresholds (fractions of the trailing window, 0..1).
THRESHOLDS = {
    "vix_5y_extreme": 0.99,  # CAPITULATION: VIX above 99th pctl of 5y
    "vix9d_vix_panic": 1.10,  # CAPITULATION support: short-vol demand spike
    "vix_1y_elevated": 0.70,  # RISK_OFF: elevated VIX context
    "vvix_1y_high": 0.80,  # RISK_OFF / COMPRESSED_SPRING: vol-of-vol rich
    "vix_1y_stress": 0.60,  # STRESS_BUILDING: VIX creeping up
    "vix9d_vix_flattening": 0.95,  # STRESS_BUILDING: near-inversion of 9D/30D
    "twist_front_bid": -0.50,  # STRESS_BUILDING: front rising vs back (vol pts)
    "vix_1y_calm": 0.35,  # COMPRESSED_SPRING: calm surface
    "skew_1y_rich": 0.80,  # COMPRESSED_SPRING: tail hedging rich
}


@dataclass(frozen=True)
class Signal:
    name: str
    fired: bool
    text: str


@dataclass(frozen=True)
class RegimeInputs:
    cash: CashTermStructure
    futures: FuturesTermStructure | None
    percentiles: dict[str, dict[str, float | None]]

    def pct(self, symbol: str, window: str) -> float | None:
        return self.percentiles.get(symbol, {}).get(window)


def _fmt_pct(p: float | None) -> str:
    return f"{p:.0%}" if p is not None else "n/a"


def _signals(inputs: RegimeInputs) -> dict[Regime, tuple[list[Signal], list[Signal]]]:
    """Per label: (triggers — any one fires the label; supports — add confidence)."""
    cash, fut = inputs.cash, inputs.futures
    t = THRESHOLDS
    r9d = cash.ratio_vix9d_vix
    r3m = cash.ratio_vix_vix3m
    vix_1y = inputs.pct("VIX", "1y")
    vix_5y = inputs.pct("VIX", "5y")
    vvix_1y = inputs.pct("VVIX", "1y")
    skew_1y = inputs.pct("SKEW", "1y")
    fut_slope = fut.slope if fut else None
    twist = fut.curve_twist_pct if fut else None

    capitulation_triggers = [
        Signal(
            "cash_inverted_crisis",
            cash.slope == SlopeState.INVERTED_CRISIS,
            f"cash curve deeply inverted (VIX/VIX3M = {r3m})",
        ),
        Signal(
            "futures_inverted_crisis",
            fut_slope == SlopeState.INVERTED_CRISIS,
            f"futures curve deeply inverted (M1→M2 roll {fut.m1_m2_roll_yield_pct}%)"
            if fut
            else "",
        ),
        Signal(
            "vix_5y_extreme",
            vix_5y is not None and vix_5y >= t["vix_5y_extreme"],
            f"VIX at {_fmt_pct(vix_5y)} of its 5-year range",
        ),
    ]
    capitulation_supports = [
        Signal(
            "vix9d_panic",
            r9d is not None and r9d > t["vix9d_vix_panic"],
            f"extreme short-dated vol demand (VIX9D/VIX = {r9d})",
        ),
        Signal(
            "vvix_extreme",
            vvix_1y is not None and vvix_1y >= 0.95,
            f"VVIX at {_fmt_pct(vvix_1y)} of 1-year range",
        ),
    ]

    risk_off_triggers = [
        Signal(
            "cash_backwardation",
            cash.slope == SlopeState.BACKWARDATION,
            f"cash curve in backwardation (VIX/VIX3M = {r3m})",
        ),
        Signal(
            "futures_backwardation",
            fut_slope == SlopeState.BACKWARDATION,
            f"futures curve in backwardation (M1→M2 roll {fut.m1_m2_roll_yield_pct}%)"
            if fut
            else "",
        ),
        Signal(
            "short_end_inverted_elevated",
            r9d is not None and r9d > 1.0 and vix_1y is not None and vix_1y >= t["vix_1y_elevated"],
            f"9D>30D inversion (VIX9D/VIX = {r9d}) with VIX at {_fmt_pct(vix_1y)} of 1y range",
        ),
    ]
    risk_off_supports = [
        Signal(
            "vvix_high",
            vvix_1y is not None and vvix_1y >= t["vvix_1y_high"],
            f"VVIX rich at {_fmt_pct(vvix_1y)} of 1y range",
        ),
        Signal(
            "front_bid_twist",
            twist is not None and twist < t["twist_front_bid"],
            f"front of the futures curve bid vs back (twist {twist} vol pts)",
        ),
    ]

    stress_triggers = [
        Signal(
            "cash_flat",
            cash.slope == SlopeState.FLAT,
            f"cash curve has flattened (VIX/VIX3M = {r3m})",
        ),
        Signal(
            "vix_creeping",
            vix_1y is not None
            and vix_1y >= t["vix_1y_stress"]
            and cash.slope in (SlopeState.CONTANGO, SlopeState.FLAT),
            f"VIX elevated at {_fmt_pct(vix_1y)} of 1y range while curve still holds",
        ),
        Signal(
            "short_end_flattening",
            r9d is not None and r9d > t["vix9d_vix_flattening"],
            f"short end near inversion (VIX9D/VIX = {r9d})",
        ),
        Signal(
            "futures_flat",
            fut_slope == SlopeState.FLAT,
            f"futures curve flat (M1→M2 roll {fut.m1_m2_roll_yield_pct}%)" if fut else "",
        ),
    ]
    stress_supports = [
        Signal(
            "front_bid_twist",
            twist is not None and twist < t["twist_front_bid"],
            f"front futures bid vs back (twist {twist} vol pts)",
        ),
        Signal(
            "vvix_high",
            vvix_1y is not None and vvix_1y >= t["vvix_1y_high"],
            f"VVIX rich at {_fmt_pct(vvix_1y)} of 1y range",
        ),
    ]

    vvix_rich = vvix_1y is not None and vvix_1y >= t["vvix_1y_high"]
    skew_rich = skew_1y is not None and skew_1y >= t["skew_1y_rich"]
    rich_parts = [
        f"{name} {_fmt_pct(p)}"
        for name, p, rich in (("VVIX", vvix_1y, vvix_rich), ("SKEW", skew_1y, skew_rich))
        if rich
    ]
    spring_triggers = [
        Signal(
            "calm_surface_rich_tails",
            vix_1y is not None and vix_1y <= t["vix_1y_calm"] and (vvix_rich or skew_rich),
            f"VIX calm ({_fmt_pct(vix_1y)} of 1y range) but hedging demand rich "
            f"({', '.join(rich_parts)} of 1y range)",
        ),
    ]
    spring_supports = [
        Signal(
            "both_rich",
            (vvix_1y is not None and vvix_1y >= t["vvix_1y_high"])
            and (skew_1y is not None and skew_1y >= t["skew_1y_rich"]),
            "both VVIX and SKEW rich simultaneously",
        ),
    ]

    calm_supports = [
        Signal(
            "steep_contango",
            cash.slope == SlopeState.STEEP_CONTANGO,
            f"steep contango (VIX/VIX3M = {r3m})",
        ),
        Signal(
            "vix_low",
            vix_1y is not None and vix_1y <= t["vix_1y_calm"],
            f"VIX at {_fmt_pct(vix_1y)} of its 1-year range",
        ),
        Signal(
            "futures_carry",
            fut_slope in (SlopeState.STEEP_CONTANGO, SlopeState.CONTANGO),
            f"futures roll-down carry intact (M1→M2 {fut.m1_m2_roll_yield_pct}%)" if fut else "",
        ),
    ]

    return {
        Regime.CAPITULATION: (capitulation_triggers, capitulation_supports),
        Regime.RISK_OFF: (risk_off_triggers, risk_off_supports),
        Regime.STRESS_BUILDING: (stress_triggers, stress_supports),
        Regime.COMPRESSED_SPRING: (spring_triggers, spring_supports),
        Regime.CALM_CARRY: ([], calm_supports),
    }


_ORDER = [
    Regime.CAPITULATION,
    Regime.RISK_OFF,
    Regime.STRESS_BUILDING,
    Regime.COMPRESSED_SPRING,
]


def _sentence_case(text: str) -> str:
    # str.capitalize() would lowercase tickers like VIX/VVIX/SKEW mid-sentence.
    return text[:1].upper() + text[1:]


def classify(inputs: RegimeInputs) -> RegimeAssessment:
    signal_map = _signals(inputs)

    for label in _ORDER:
        triggers, supports = signal_map[label]
        fired_triggers = [s for s in triggers if s.fired]
        if not fired_triggers:
            continue
        fired_supports = [s for s in supports if s.fired]
        confidence = min(0.95, 0.55 + 0.10 * (len(fired_triggers) - 1) + 0.10 * len(fired_supports))
        fired = fired_triggers + fired_supports
        rationale = "; ".join(s.text for s in fired if s.text) + "."
        return RegimeAssessment(
            label=label,
            confidence=round(confidence, 2),
            rationale=_sentence_case(rationale),
            fired_rules=[s.name for s in fired],
        )

    _, calm_supports = signal_map[Regime.CALM_CARRY]
    fired_supports = [s for s in calm_supports if s.fired]
    confidence = min(0.9, 0.50 + 0.13 * len(fired_supports))
    rationale = (
        "; ".join(s.text for s in fired_supports if s.text) + "."
        if fired_supports
        else "No stress, inversion, or compression signals firing; curve in normal carry."
    )
    return RegimeAssessment(
        label=Regime.CALM_CARRY,
        confidence=round(confidence, 2),
        rationale=_sentence_case(rationale),
        fired_rules=[s.name for s in fired_supports],
    )


_REGIME_DESCRIPTIONS = {
    Regime.CALM_CARRY: (
        "a benign volatility environment: the term structure is in contango and "
        "short-vol / roll-down carry strategies have the wind at their back"
    ),
    Regime.COMPRESSED_SPRING: (
        "a calm surface with tension underneath: headline VIX is low but hedging "
        "demand (VVIX/SKEW) is rich, a setup that can unwind sharply"
    ),
    Regime.STRESS_BUILDING: (
        "early deterioration: the curve is flattening and short-dated vol is "
        "catching up to longer-dated, often the transition zone before risk-off"
    ),
    Regime.RISK_OFF: (
        "active de-risking: the term structure has inverted at the front, meaning "
        "the market is paying up for near-term protection"
    ),
    Regime.CAPITULATION: (
        "crisis-level stress: deep curve inversion and/or extreme VIX readings "
        "historically associated with forced selling and washout conditions"
    ),
}


def build_narrative(assessment: RegimeAssessment, cash: CashTermStructure) -> str:
    vix = cash.values.get("VIX")
    return (
        f"Current regime: {assessment.label} "
        f"(confidence {assessment.confidence:.0%}). "
        f"This indicates {_REGIME_DESCRIPTIONS[assessment.label]}. "
        f"VIX is at {vix} with VIX/VIX3M at {cash.ratio_vix_vix3m} "
        f"({cash.slope.value.replace('_', ' ').lower()}) and VIX9D/VIX at "
        f"{cash.ratio_vix9d_vix}. Basis for the label: {assessment.rationale}"
    )
