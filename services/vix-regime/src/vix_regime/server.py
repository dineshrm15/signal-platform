"""MCP server: read-only view over the snapshot store, plus subscription management.

Platform rule (docs/00-platform-architecture.md): the server never computes signals on
the request path — the pipeline loop is the only snapshot writer. Subscription tools
write only to the subscription store.

Run:  python -m vix_regime.server            (stdio, for local MCP clients)
      python -m vix_regime.server --http     (streamable HTTP on :8000)
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import httpx
from fastmcp import FastMCP
from signal_platform import SnapshotStore, SqliteSnapshotStore, envelope
from signal_platform.push import InvalidTarget, SqliteSubscriptionStore, SubscriptionStore

from vix_regime.alerts import TriggerSpec, create_subscription
from vix_regime.config import (
    ATTRIBUTION,
    DISCLAIMER,
    SERVICE_NAME,
    allow_private_targets,
    db_path,
    push_client,
)
from vix_regime.pipeline import KIND_SNAPSHOT, KIND_TRANSITION

mcp = FastMCP(
    name="vol-regime",
    instructions=(
        "Volatility regime intelligence derived from Cboe public data (15-min delayed). "
        "Call get_vol_regime first for the headline state; use get_term_structure for "
        "curve detail and explain_regime for a plain-English narrative. "
        "Use subscribe to get push alerts (ntfy or webhook) when the regime changes. "
        "All payloads are informational only, not financial advice."
    ),
)

_store: SnapshotStore | None = None
_subs: SubscriptionStore | None = None
# Over stdio the caller is the local user. Over HTTP, anyone who can reach the server
# could make it send traffic to arbitrary targets, so subscriptions are opt-in there.
_subscriptions_enabled = True


def get_store() -> SnapshotStore:
    global _store
    if _store is None:
        _store = SqliteSnapshotStore(db_path())
    return _store


def get_subs() -> SubscriptionStore:
    global _subs
    if _subs is None:
        _subs = SqliteSubscriptionStore(db_path())
    return _subs


def set_store(store: SnapshotStore | None, subs: SubscriptionStore | None = None) -> None:
    """Test seam: inject seeded stores."""
    global _store, _subs
    _store = store
    _subs = subs


def set_subscriptions_enabled(enabled: bool) -> None:
    global _subscriptions_enabled
    _subscriptions_enabled = enabled


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
    return _wrap({"futures": snap.get("futures"), "curve": snap.get("futures_curve", [])}, as_of)


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
            "transitions": [{"at": r.as_of.isoformat(), **r.payload} for r in reversed(rows)],
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


_SUBS_DISABLED = {
    "error": "subscriptions_disabled",
    "detail": "Push subscriptions are not enabled on this server.",
}


@mcp.tool
def subscribe(
    channel: Literal["ntfy", "webhook"],
    target: str,
    triggers: list[TriggerSpec] | None = None,
) -> dict[str, Any]:
    """Subscribe to push alerts when the volatility state changes.

    channel="ntfy": target is an ntfy topic name (delivered via ntfy.sh; the user
    subscribes to the same topic in the ntfy app) or an https URL to a self-hosted topic.
    channel="webhook": target is an https URL that receives a JSON POST per alert.

    triggers defaults to [{"type": "regime_change"}]. Other options:
    {"type": "curve_inversion", "curve": "cash"|"futures"|"any"} and
    {"type": "percentile_cross", "metric": "VVIX", "threshold": 0.9, "direction": "up"}.

    A welcome message is sent immediately; the subscription is only created if it is
    delivered. Keep the returned subscription_id — it is required to manage or cancel it.
    """
    if not _subscriptions_enabled:
        return _SUBS_DISABLED
    try:
        with push_client() as client:
            sub = create_subscription(
                get_subs(),
                client,
                channel,
                target,
                triggers,
                allow_private=allow_private_targets(),
            )
    except InvalidTarget as exc:
        return {"error": "invalid_target", "detail": str(exc)}
    except httpx.HTTPError as exc:
        return {"error": "delivery_failed", "detail": f"welcome message not delivered: {exc}"}
    return {**sub.public_view(), "disclaimer": DISCLAIMER}


@mcp.tool
def get_subscription(subscription_id: str) -> dict[str, Any]:
    """Look up a subscription's channel, triggers, and delivery health by its id."""
    if not _subscriptions_enabled:
        return _SUBS_DISABLED
    sub = get_subs().get(subscription_id)
    if sub is None or sub.service != SERVICE_NAME:
        return {"error": "not_found"}
    return sub.public_view()


@mcp.tool
def unsubscribe(subscription_id: str) -> dict[str, Any]:
    """Cancel a push subscription."""
    if not _subscriptions_enabled:
        return _SUBS_DISABLED
    sub = get_subs().get(subscription_id)
    if sub is None or sub.service != SERVICE_NAME:
        return {"error": "not_found"}
    get_subs().deactivate(subscription_id)
    return {"subscription_id": subscription_id, "active": False}


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(prog="vix-regime-server")
    parser.add_argument("--http", action="store_true", help="serve streamable HTTP")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--allow-subscriptions",
        action="store_true",
        help="expose subscribe/unsubscribe over HTTP (off by default; always on for stdio)",
    )
    args = parser.parse_args()

    if args.http:
        set_subscriptions_enabled(args.allow_subscriptions)
        mcp.run(transport="http", host=args.host, port=args.port)
    else:
        mcp.run()


if __name__ == "__main__":
    main()
