"""Shared response envelope: every tool payload carries as_of, freshness, disclaimer,
and attribution (docs/00-platform-architecture.md, cross-cutting rules)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

# Freshness thresholds in minutes. "delayed" matches the 15-min delayed upstream data;
# anything older than a trading session is "historical".
_DELAYED_MAX_AGE_MIN = 25
_STALE_MAX_AGE_MIN = 8 * 60


def freshness_label(as_of: datetime, now: datetime | None = None) -> str:
    now = now or datetime.now(UTC)
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=UTC)
    age_min = (now - as_of).total_seconds() / 60
    if age_min <= _DELAYED_MAX_AGE_MIN:
        return "delayed_15min"
    if age_min <= _STALE_MAX_AGE_MIN:
        return "stale"
    return "historical"


def envelope(
    data: dict[str, Any],
    *,
    as_of: datetime,
    attribution: str,
    disclaimer: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=UTC)
    return {
        "as_of": as_of.astimezone(UTC).isoformat(),
        "data_freshness": freshness_label(as_of, now),
        **data,
        "attribution": attribution,
        "disclaimer": disclaimer,
    }
