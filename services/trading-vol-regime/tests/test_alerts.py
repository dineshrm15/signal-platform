import json

import httpx
import pytest
import respx
from pydantic import ValidationError
from signal_platform.push import InvalidTarget, SqliteSubscriptionStore
from trading_vol_regime.alerts import (
    TriggerSpec,
    create_subscription,
    notifications_for,
    run_alerts,
)
from trading_vol_regime.config import SERVICE_NAME


def _snap(
    label="CALM_CARRY",
    cash_slope="CONTANGO",
    futures_slope="CONTANGO",
    vvix_1y=0.5,
    vvix=90.0,
):
    return {
        "regime": {"label": label, "confidence": 0.7, "rationale": "Because."},
        "cash": {"values": {"VIX": 15.0}, "ratio_vix_vix3m": 0.9, "slope": cash_slope},
        "futures": {"slope": futures_slope} if futures_slope else None,
        "percentiles": {"VVIX": {"1y": vvix_1y, "5y": vvix_1y}},
        "indices": {"VVIX": {"price": vvix}},
    }


def _sub(store, *triggers):
    return store.create(
        SERVICE_NAME,
        "ntfy",
        "topic",
        [TriggerSpec(**t).model_dump(mode="json", exclude_none=True) for t in triggers],
    )


@pytest.fixture
def store():
    return SqliteSubscriptionStore(":memory:")


# -------------------------------------------------------------- trigger spec


def test_percentile_trigger_requires_metric_and_threshold():
    with pytest.raises(ValidationError):
        TriggerSpec(type="percentile_cross", metric="VVIX")
    with pytest.raises(ValidationError):
        TriggerSpec(type="percentile_cross", metric="VVIX", threshold=1.5)


# -------------------------------------------------------------- regime change


def test_regime_change_fires_with_priority(store):
    sub = _sub(store, {"type": "regime_change"})
    notes = notifications_for(sub, _snap("CALM_CARRY"), _snap("CAPITULATION"))
    assert len(notes) == 1
    assert notes[0].title == "Vol regime: CALM_CARRY → CAPITULATION"
    assert notes[0].priority == "urgent"
    assert "not financial advice" in notes[0].message


def test_regime_change_silent_when_unchanged(store):
    sub = _sub(store, {"type": "regime_change"})
    assert notifications_for(sub, _snap(), _snap()) == []


def test_regime_change_to_filter(store):
    sub = _sub(store, {"type": "regime_change", "to": ["RISK_OFF"]})
    assert notifications_for(sub, _snap("CALM_CARRY"), _snap("STRESS_BUILDING")) == []
    assert len(notifications_for(sub, _snap("STRESS_BUILDING"), _snap("RISK_OFF"))) == 1


# -------------------------------------------------------------- curve inversion


def test_cash_inversion_and_renormalization(store):
    sub = _sub(store, {"type": "curve_inversion", "curve": "cash"})
    inverted = notifications_for(sub, _snap(cash_slope="FLAT"), _snap(cash_slope="BACKWARDATION"))
    assert [n.key for n in inverted] == ["curve_inversion:cash"]
    assert inverted[0].priority == "high"

    renormal = notifications_for(
        sub, _snap(cash_slope="INVERTED_CRISIS"), _snap(cash_slope="CONTANGO")
    )
    assert [n.key for n in renormal] == ["curve_renormalization:cash"]


def test_deepening_inversion_is_not_a_new_event(store):
    sub = _sub(store, {"type": "curve_inversion"})
    assert (
        notifications_for(
            sub,
            _snap(cash_slope="BACKWARDATION", futures_slope="BACKWARDATION"),
            _snap(cash_slope="INVERTED_CRISIS", futures_slope="INVERTED_CRISIS"),
        )
        == []
    )


def test_missing_futures_is_not_renormalization(store):
    sub = _sub(store, {"type": "curve_inversion", "curve": "futures"})
    assert (
        notifications_for(sub, _snap(futures_slope="BACKWARDATION"), _snap(futures_slope=None))
        == []
    )


# -------------------------------------------------------------- percentile cross


def test_percentile_cross_up_only_by_default(store):
    sub = _sub(store, {"type": "percentile_cross", "metric": "VVIX", "threshold": 0.9})
    up = notifications_for(sub, _snap(vvix_1y=0.85), _snap(vvix_1y=0.92))
    assert len(up) == 1
    assert up[0].cooldown_minutes > 0
    assert notifications_for(sub, _snap(vvix_1y=0.92), _snap(vvix_1y=0.85)) == []
    assert notifications_for(sub, _snap(vvix_1y=0.92), _snap(vvix_1y=0.95)) == []


def test_percentile_cross_both_directions(store):
    sub = _sub(
        store,
        {"type": "percentile_cross", "metric": "VVIX", "threshold": 0.9, "direction": "both"},
    )
    down = notifications_for(sub, _snap(vvix_1y=0.92), _snap(vvix_1y=0.85))
    assert len(down) == 1 and "below" in down[0].title


def test_percentile_missing_data_is_silent(store):
    sub = _sub(store, {"type": "percentile_cross", "metric": "SKEW", "threshold": 0.8})
    assert notifications_for(sub, _snap(), _snap()) == []


def test_overlapping_triggers_do_not_double_send(store):
    sub = _sub(store, {"type": "regime_change"}, {"type": "regime_change", "to": ["RISK_OFF"]})
    assert len(notifications_for(sub, _snap("CALM_CARRY"), _snap("RISK_OFF"))) == 1


# -------------------------------------------------------------- run_alerts / subscribe


@respx.mock
def test_run_alerts_end_to_end(store):
    route = respx.post("https://ntfy.sh/").mock(return_value=httpx.Response(200))
    _sub(store, {"type": "regime_change"})
    with httpx.Client() as client:
        result = run_alerts(store, client, _snap("CALM_CARRY"), _snap("RISK_OFF"))
    assert result.delivered == 1
    assert json.loads(route.calls.last.request.content)["title"].endswith("RISK_OFF")


def test_run_alerts_noop_without_previous_snapshot(store):
    _sub(store, {"type": "regime_change"})
    with httpx.Client() as client:
        assert run_alerts(store, client, None, _snap()).delivered == 0


@respx.mock
def test_create_subscription_requires_welcome_delivery(store):
    respx.post("https://ntfy.sh/").mock(return_value=httpx.Response(500))
    with httpx.Client() as client, pytest.raises(httpx.HTTPStatusError):
        create_subscription(store, client, "ntfy", "topic")
    assert store.list_active(SERVICE_NAME) == []


@respx.mock
def test_create_subscription_defaults_to_regime_change(store):
    route = respx.post("https://ntfy.sh/").mock(return_value=httpx.Response(200))
    with httpx.Client() as client:
        sub = create_subscription(store, client, "ntfy", "topic")
    assert sub.triggers == [
        {"type": "regime_change", "curve": "any", "window": "1y", "direction": "up"}
    ]
    assert json.loads(route.calls.last.request.content)["title"].endswith("subscribed")


def test_create_subscription_rejects_unsafe_webhook(store):
    with httpx.Client() as client, pytest.raises(InvalidTarget):
        create_subscription(store, client, "webhook", "https://127.0.0.1/hook")
