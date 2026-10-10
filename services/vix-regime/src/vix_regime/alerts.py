"""Push triggers (spec §6) evaluated between consecutive snapshots.

Triggers are per-subscriber (thresholds differ per person), so evaluation happens per
subscription against the (previous, new) snapshot pair. Rules are documented in
METHODOLOGY.md under "Alerts".
"""

from __future__ import annotations

from typing import Any, Literal

import httpx
from pydantic import BaseModel, Field, model_validator
from signal_platform.push import (
    Channel,
    DispatchResult,
    Notification,
    Subscription,
    SubscriptionStore,
    dispatch,
    send,
    validate_target,
)

from vix_regime.config import SERVICE_NAME
from vix_regime.models import Regime, SlopeState

INVERTED_STATES = {SlopeState.BACKWARDATION.value, SlopeState.INVERTED_CRISIS.value}
PERCENTILE_COOLDOWN_MIN = 6 * 60

_SHORT_DISCLAIMER = "Informational only, not financial advice."

_REGIME_PRIORITY: dict[str, Literal["low", "default", "high", "urgent"]] = {
    Regime.CALM_CARRY: "default",
    Regime.COMPRESSED_SPRING: "default",
    Regime.STRESS_BUILDING: "high",
    Regime.RISK_OFF: "high",
    Regime.CAPITULATION: "urgent",
}


class TriggerSpec(BaseModel):
    """One alert rule. `type` selects which other fields apply."""

    type: Literal["regime_change", "curve_inversion", "percentile_cross"] = Field(
        description=(
            "regime_change: regime label changes. "
            "curve_inversion: cash or futures curve inverts (backwardation) or re-normalizes. "
            "percentile_cross: a metric's percentile crosses a threshold."
        )
    )
    to: list[Regime] | None = Field(
        default=None, description="regime_change only: alert only on moves into these labels"
    )
    curve: Literal["cash", "futures", "any"] = Field(
        default="any", description="curve_inversion only"
    )
    metric: Literal["VIX", "VIX9D", "VIX3M", "VVIX", "SKEW"] | None = Field(
        default=None, description="percentile_cross only"
    )
    threshold: float | None = Field(
        default=None, gt=0, lt=1, description="percentile_cross only, e.g. 0.9 = 90th pctl"
    )
    window: Literal["1y", "5y"] = Field(default="1y", description="percentile_cross only")
    direction: Literal["up", "down", "both"] = Field(
        default="up", description="percentile_cross only"
    )

    @model_validator(mode="after")
    def _check_percentile_fields(self) -> TriggerSpec:
        if self.type == "percentile_cross" and (self.metric is None or self.threshold is None):
            raise ValueError("percentile_cross requires metric and threshold")
        return self


DEFAULT_TRIGGERS = [TriggerSpec(type="regime_change")]


def _vix_context(snap: dict[str, Any]) -> str:
    cash = snap["cash"]
    return f"VIX {cash['values'].get('VIX')}, VIX/VIX3M {cash['ratio_vix_vix3m']}."


def _regime_change(t: TriggerSpec, prev: dict[str, Any], new: dict[str, Any]) -> list[Notification]:
    before, after = prev["regime"]["label"], new["regime"]["label"]
    if before == after:
        return []
    if t.to and after not in {r.value for r in t.to}:
        return []
    regime = new["regime"]
    return [
        Notification(
            key=f"regime_change:{before}->{after}",
            title=f"Vol regime: {before} → {after}",
            message=(
                f"{regime['rationale']} Confidence {regime['confidence']:.0%}. "
                f"{_vix_context(new)} {_SHORT_DISCLAIMER}"
            ),
            priority=_REGIME_PRIORITY.get(after, "default"),
            tags=["chart_with_downwards_trend"],
            data={"type": "regime_change", "from": before, "to": after, "regime": regime},
        )
    ]


def _curve_slopes(snap: dict[str, Any]) -> dict[str, str | None]:
    futures = snap.get("futures")
    return {
        "cash": snap["cash"]["slope"],
        "futures": futures["slope"] if futures else None,
    }


def _curve_inversion(
    t: TriggerSpec, prev: dict[str, Any], new: dict[str, Any]
) -> list[Notification]:
    before, after = _curve_slopes(prev), _curve_slopes(new)
    notes = []
    for curve in ("cash", "futures"):
        if t.curve not in (curve, "any"):
            continue
        if before[curve] is None or after[curve] is None:
            continue  # missing futures data is not a re-normalization
        was_inverted = before[curve] in INVERTED_STATES
        is_inverted = after[curve] in INVERTED_STATES
        if was_inverted == is_inverted:
            continue
        event = "inversion" if is_inverted else "renormalization"
        verb = "inverted" if is_inverted else "re-normalized"
        notes.append(
            Notification(
                key=f"curve_{event}:{curve}",
                title=f"{curve.capitalize()} vol curve {verb}",
                message=(
                    f"{curve.capitalize()} term structure moved {before[curve]} → "
                    f"{after[curve]}. {_vix_context(new)} {_SHORT_DISCLAIMER}"
                ),
                priority="high" if is_inverted else "default",
                tags=["warning"] if is_inverted else ["white_check_mark"],
                data={
                    "type": f"curve_{event}",
                    "curve": curve,
                    "from": before[curve],
                    "to": after[curve],
                },
            )
        )
    return notes


def _percentile_cross(
    t: TriggerSpec, prev: dict[str, Any], new: dict[str, Any]
) -> list[Notification]:
    assert t.metric is not None and t.threshold is not None
    p0 = prev["percentiles"].get(t.metric, {}).get(t.window)
    p1 = new["percentiles"].get(t.metric, {}).get(t.window)
    if p0 is None or p1 is None:
        return []
    crossed_up = p0 < t.threshold <= p1
    crossed_down = p0 >= t.threshold > p1
    if crossed_up and t.direction in ("up", "both"):
        direction = "above"
    elif crossed_down and t.direction in ("down", "both"):
        direction = "below"
    else:
        return []
    level = new["indices"].get(t.metric, {}).get("price")
    return [
        Notification(
            key=f"percentile_cross:{t.metric}:{t.window}:{t.threshold}:{direction}",
            title=f"{t.metric} crossed {direction} {t.threshold:.0%} ({t.window})",
            message=(
                f"{t.metric} at {level} moved from the {p0:.0%} to the {p1:.0%} percentile "
                f"of its {t.window} range. {_SHORT_DISCLAIMER}"
            ),
            priority="default",
            tags=["bar_chart"],
            data={
                "type": "percentile_cross",
                "metric": t.metric,
                "window": t.window,
                "threshold": t.threshold,
                "direction": direction,
                "from_percentile": p0,
                "to_percentile": p1,
                "level": level,
            },
            cooldown_minutes=PERCENTILE_COOLDOWN_MIN,
        )
    ]


_EVALUATORS = {
    "regime_change": _regime_change,
    "curve_inversion": _curve_inversion,
    "percentile_cross": _percentile_cross,
}


def notifications_for(
    sub: Subscription, prev: dict[str, Any], new: dict[str, Any]
) -> list[Notification]:
    notes: list[Notification] = []
    seen: set[str] = set()
    for raw in sub.triggers:
        trigger = TriggerSpec.model_validate(raw)
        for note in _EVALUATORS[trigger.type](trigger, prev, new):
            if note.key not in seen:  # overlapping triggers must not double-send
                seen.add(note.key)
                notes.append(note)
    return notes


def run_alerts(
    subs: SubscriptionStore,
    client: httpx.Client,
    prev: dict[str, Any] | None,
    new: dict[str, Any],
) -> DispatchResult:
    if prev is None:
        return DispatchResult()
    return dispatch(subs, client, SERVICE_NAME, lambda sub: notifications_for(sub, prev, new))


def create_subscription(
    subs: SubscriptionStore,
    client: httpx.Client,
    channel: Channel,
    target: str,
    triggers: list[TriggerSpec] | None = None,
    *,
    allow_private: bool = False,
) -> Subscription:
    """Validate the target, prove it is reachable with a welcome message, then store.

    Raises InvalidTarget for bad targets and httpx.HTTPError if delivery fails.
    """
    triggers = triggers or DEFAULT_TRIGGERS
    target = validate_target(channel, target, allow_private=allow_private)
    send(client, channel, target, welcome_notification(triggers))
    return subs.create(
        SERVICE_NAME,
        channel,
        target,
        [t.model_dump(mode="json", exclude_none=True) for t in triggers],
    )


def welcome_notification(triggers: list[TriggerSpec]) -> Notification:
    rules = ", ".join(t.type for t in triggers)
    return Notification(
        key="welcome",
        title="Vol regime alerts: subscribed",
        message=(
            f"You'll be notified on: {rules}. Data is 15-min delayed Cboe public data. "
            f"{_SHORT_DISCLAIMER}"
        ),
        priority="low",
        tags=["bell"],
        data={"type": "welcome"},
    )
