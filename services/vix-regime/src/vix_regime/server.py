"""MCP server: read-only view over the snapshot store.

Platform rule (docs/00-platform-architecture.md): the server never computes on the
request path — the pipeline loop is the only writer, tools only read snapshots.

Run:  python -m vix_regime.server            (stdio, for local MCP clients)
      python -m vix_regime.server --http     (streamable HTTP on :8000)
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from fastmcp import FastMCP
from signal_platform import SnapshotStore, SqliteSnapshotStore, envelope

from vix_regime.config import ATTRIBUTION, DISCLAIMER, SERVICE_NAME, db_path
from vix_regime.pipeline import KIND_SNAPSHOT, KIND_TRANSITION

mcp = FastMCP(
    name="vol-regime",
    instructions=(
        "Volatility regime intelligence derived from Cboe public data (15-min delayed). "
        "Call get_vol_regime first for the headline state; use get_term_structure for "
        "curve detail and explain_regime for a plain-English narrative. "
        "All payloads are informational only, not financial advice."
    ),
)

_store: SnapshotStore | None = None


def get_store() -> SnapshotStore:
    global _store
    if _store is None:
        _store = SqliteSnapshotStore(db_path())
    return _store


def set_store(store: SnapshotStore | None) -> None:
    """Test seam: inject a seeded store."""
    global _store
    _store = store


_NO_DATA = {
    "error": "no_snapshot_available",
    "detail": "No snapshot in store yet. Run `vix-regime backfill && vix-regime snapshot`.",
}


def _latest() -> tuple[dict[str, Any], datetime] | None:
    row = get_store().get_latest(SERVICE_NAME, KIND_SNAPSHOT)
    if row is None:
        return None
    return row.payload, row.as_of


def _wrap(data: dict[str, Any], as_of: datetime) -> dict[str, Any]:
    return envelope(data, as_of=as_of, attribution=ATTRIBUTION, disclaimer=DISCLAIMER)


@mcp.tool
def get_vol_regime() -> dict[str, Any]:
    """Current volatility regime: label, confidence, rationale, and key ratios.

    Labels: CALM_CARRY, COMPRESSED_SPRING, STRESS_BUILDING, RISK_OFF, CAPITULATION.
    Methodology: rules-based and published (METHODOLOGY.md in the repo).
    """
    latest = _latest()
    if latest is None:
        return _NO_DATA
    snap, as_of = latest
    cash = snap["cash"]
    futures = snap.get("futures")
    return _wrap(
        {
            "regime": snap["regime"],
            "vix": cash["values"].get("VIX"),
            "key_ratios": {
                "vix9d_vix": cash["ratio_vix9d_vix"],
                "vix_vix3m": cash["ratio_vix_vix3m"],
                "vix3m_vix6m": cash["ratio_vix3m_vix6m"],
                "cash_slope": cash["slope"],
                "m1_m2_roll_yield_pct": futures["m1_m2_roll_yield_pct"] if futures else None,
                "front_basis_pct": futures["front_basis_pct"] if futures else None,
                "futures_slope": futures["slope"] if futures else None,
            },
        },
        as_of,
    )


@mcp.tool
def get_term_structure(kind: Literal["cash", "futures"] = "cash") -> dict[str, Any]:
    """Volatility term structure detail.

    kind="cash": VIX family index levels, ratios, slope state, and 1y/5y percentiles.
    kind="futures": full VX futures curve (incl. weeklies), roll yield, basis, twist.
    """
    latest = _latest()
    if latest is None:
        return _NO_DATA
    snap, as_of = latest
    if kind == "cash":
        return _wrap({"cash": snap["cash"], "percentiles": snap["percentiles"]}, as_of)
    return _wrap(
        {"futures": snap.get("futures"), "curve": snap.get("futures_curve", [])}, as_of
    )


@mcp.tool
def get_regime_transitions(lookback_days: int = 30) -> dict[str, Any]:
    """Timestamped log of regime label changes over the lookback window."""
    lookback_days = max(1, min(lookback_days, 365))
    since = datetime.now(UTC) - timedelta(days=lookback_days)
    rows = get_store().get_since(SERVICE_NAME, KIND_TRANSITION, since)
    latest = _latest()
    as_of = latest[1] if latest else datetime.now(UTC)
    return _wrap(
        {
            "lookback_days": lookback_days,
            "count": len(rows),
            "transitions": [
                {"at": r.as_of.isoformat(), **r.payload} for r in reversed(rows)
            ],
        },
        as_of,
    )


@mcp.tool
def explain_regime() -> dict[str, Any]:
    """Plain-English narrative of the current volatility state, built from the
    published rules — suitable for direct display to a human."""
    latest = _latest()
    if latest is None:
        return _NO_DATA
    snap, as_of = latest
    return _wrap({"narrative": snap["narrative"]}, as_of)


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(prog="vix-regime-server")
    parser.add_argument("--http", action="store_true", help="serve streamable HTTP")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    if args.http:
        mcp.run(transport="http", host=args.host, port=args.port)
    else:
        mcp.run()


if __name__ == "__main__":
    main()
