import json
from datetime import UTC, datetime, timedelta

import pytest
from fastmcp import Client
from signal_platform import SqliteSnapshotStore
from vix_regime.calc.regime import RegimeInputs, build_narrative, classify
from vix_regime.calc.term_structure import build_cash_term_structure
from vix_regime.config import SERVICE_NAME
from vix_regime.models import IndexQuote, Snapshot
from vix_regime.pipeline import KIND_SNAPSHOT, KIND_TRANSITION
from vix_regime.server import mcp, set_store

AS_OF = datetime(2026, 8, 6, 20, 15, tzinfo=UTC)


def _quote(symbol: str, price: float) -> IndexQuote:
    return IndexQuote(symbol=symbol, price=price, prev_close=price, change_pct=0.0)


def _seeded_store() -> SqliteSnapshotStore:
    store = SqliteSnapshotStore(":memory:")
    indices = {
        "VIX": _quote("VIX", 15.15),
        "VIX9D": _quote("VIX9D", 13.9),
        "VIX3M": _quote("VIX3M", 17.2),
    }
    cash = build_cash_term_structure(indices)
    percentiles = {"VIX": {"1y": 0.3, "5y": 0.25}, "VVIX": {"1y": 0.4}, "SKEW": {"1y": 0.5}}
    assessment = classify(RegimeInputs(cash=cash, futures=None, percentiles=percentiles))
    snapshot = Snapshot(
        as_of=AS_OF,
        indices=indices,
        cash=cash,
        futures=None,
        percentiles=percentiles,
        regime=assessment,
        narrative=build_narrative(assessment, cash),
    )
    store.put(SERVICE_NAME, KIND_SNAPSHOT, AS_OF, snapshot.model_dump(mode="json"))
    store.put(
        SERVICE_NAME,
        KIND_TRANSITION,
        AS_OF - timedelta(days=2),
        {"from": "CALM_CARRY", "to": "STRESS_BUILDING", "confidence": 0.6, "rationale": "x"},
    )
    store.put(
        SERVICE_NAME,
        KIND_TRANSITION,
        AS_OF - timedelta(days=1),
        {"from": "STRESS_BUILDING", "to": "CALM_CARRY", "confidence": 0.7, "rationale": "y"},
    )
    return store


def _payload(result) -> dict:
    if isinstance(result.data, dict):
        return result.data
    return json.loads(result.content[0].text)


@pytest.fixture
def seeded():
    set_store(_seeded_store())
    yield
    set_store(None)


async def test_get_vol_regime(seeded):
    async with Client(mcp) as client:
        out = _payload(await client.call_tool("get_vol_regime", {}))
    assert out["regime"]["label"] == "CALM_CARRY"
    assert out["vix"] == 15.15
    assert out["key_ratios"]["vix_vix3m"] == round(15.15 / 17.2, 4)
    for field in ("as_of", "data_freshness", "attribution", "disclaimer"):
        assert field in out


async def test_get_term_structure_cash_and_futures(seeded):
    async with Client(mcp) as client:
        cash = _payload(await client.call_tool("get_term_structure", {"kind": "cash"}))
        fut = _payload(await client.call_tool("get_term_structure", {"kind": "futures"}))
    assert cash["cash"]["slope"] == "CONTANGO"
    assert cash["percentiles"]["VIX"]["1y"] == 0.3
    assert fut["futures"] is None
    assert fut["curve"] == []


async def test_get_regime_transitions(seeded):
    async with Client(mcp) as client:
        out = _payload(
            await client.call_tool("get_regime_transitions", {"lookback_days": 30})
        )
    assert out["count"] == 2
    # newest first
    assert out["transitions"][0]["to"] == "CALM_CARRY"
    assert out["transitions"][1]["to"] == "STRESS_BUILDING"


async def test_explain_regime(seeded):
    async with Client(mcp) as client:
        out = _payload(await client.call_tool("explain_regime", {}))
    assert "CALM_CARRY" in out["narrative"]


async def test_no_snapshot_error():
    set_store(SqliteSnapshotStore(":memory:"))
    try:
        async with Client(mcp) as client:
            out = _payload(await client.call_tool("get_vol_regime", {}))
        assert out["error"] == "no_snapshot_available"
    finally:
        set_store(None)
