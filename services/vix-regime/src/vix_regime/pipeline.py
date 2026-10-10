"""Ingest → calc → snapshot write. The only writer to the snapshot store."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from signal_platform import SnapshotStore

from vix_regime.calc.percentiles import percentile_context
from vix_regime.calc.regime import RegimeInputs, build_narrative, classify
from vix_regime.calc.term_structure import (
    build_cash_term_structure,
    build_futures_term_structure,
)
from vix_regime.config import SERVICE_NAME
from vix_regime.ingest.cboe_futures import fetch_futures_curve
from vix_regime.ingest.cboe_indices import INDEX_SYMBOLS, fetch_index_quotes
from vix_regime.ingest.history import fetch_history
from vix_regime.models import Snapshot

log = logging.getLogger(__name__)

KIND_SNAPSHOT = "snapshot"
KIND_TRANSITION = "transition"
KIND_DAILY = "daily:{symbol}"

# Calendar days that comfortably cover a 5y trading-day window.
_HISTORY_WINDOW_DAYS = 5 * 366 + 30


def run_backfill(store: SnapshotStore, client: httpx.Client) -> dict[str, int]:
    """Idempotent: history rows upsert on (service, kind, as_of)."""
    counts: dict[str, int] = {}
    for symbol in INDEX_SYMBOLS:
        rows = fetch_history(client, symbol)
        cutoff = (datetime.now(UTC) - timedelta(days=_HISTORY_WINDOW_DAYS)).date()
        items = [
            (
                SERVICE_NAME,
                KIND_DAILY.format(symbol=symbol),
                datetime(day.year, day.month, day.day, tzinfo=UTC),
                {"close": close},
            )
            for day, close in rows
            if day >= cutoff
        ]
        store.put_many(items)
        counts[symbol] = len(items)
        log.info("backfilled %s: %d daily closes", symbol, len(items))
    return counts


def _daily_closes(store: SnapshotStore, symbol: str) -> list[float]:
    since = datetime.now(UTC) - timedelta(days=_HISTORY_WINDOW_DAYS)
    rows = store.get_since(SERVICE_NAME, KIND_DAILY.format(symbol=symbol), since, limit=2000)
    return [float(r.payload["close"]) for r in rows]


def latest_daily_as_of(store: SnapshotStore, symbol: str = "VIX") -> datetime | None:
    row = store.get_latest(SERVICE_NAME, KIND_DAILY.format(symbol=symbol))
    return row.as_of if row else None


@dataclass(frozen=True)
class SnapshotResult:
    snapshot: Snapshot
    previous: dict[str, Any] | None  # prior snapshot payload, for change detection
    transitioned: bool


def run_snapshot(store: SnapshotStore, client: httpx.Client) -> SnapshotResult:
    indices = fetch_index_quotes(client)
    try:
        curve = fetch_futures_curve(client)
    except httpx.HTTPError as exc:  # curve is enriching, not required
        log.warning("futures curve fetch failed, continuing without: %s", exc)
        curve = None

    cash = build_cash_term_structure(indices)
    futures = build_futures_term_structure(curve, indices["VIX"].price) if curve else None
    percentiles = {
        symbol: percentile_context(quote.price, _daily_closes(store, symbol))
        for symbol, quote in indices.items()
    }
    assessment = classify(RegimeInputs(cash=cash, futures=futures, percentiles=percentiles))

    as_of = datetime.now(UTC)
    snapshot = Snapshot(
        as_of=as_of,
        indices=indices,
        cash=cash,
        futures=futures,
        futures_curve=curve.contracts if curve else [],
        percentiles=percentiles,
        regime=assessment,
        narrative=build_narrative(assessment, cash),
    )

    prev = store.get_latest(SERVICE_NAME, KIND_SNAPSHOT)
    transitioned = False
    if prev is not None:
        prev_label = prev.payload["regime"]["label"]
        if prev_label != assessment.label.value:
            transitioned = True
            store.put(
                SERVICE_NAME,
                KIND_TRANSITION,
                as_of,
                {
                    "from": prev_label,
                    "to": assessment.label.value,
                    "confidence": assessment.confidence,
                    "rationale": assessment.rationale,
                },
            )
            log.info("regime transition: %s -> %s", prev_label, assessment.label.value)

    store.put(SERVICE_NAME, KIND_SNAPSHOT, as_of, snapshot.model_dump(mode="json"))
    return SnapshotResult(
        snapshot=snapshot,
        previous=prev.payload if prev else None,
        transitioned=transitioned,
    )
