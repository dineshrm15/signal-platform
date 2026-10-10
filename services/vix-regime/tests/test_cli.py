import argparse
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from vix_regime.cli import _parse_triggers, backfill_due, market_is_open
from vix_regime.schedule import LABEL, build_plist

ET = ZoneInfo("America/New_York")


def _et(y, m, d, hh, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=ET).astimezone(UTC)


FRESH = _et(2026, 10, 8, 0)  # Thursday's close recorded


def test_market_hours():
    assert market_is_open(_et(2026, 10, 9, 10))  # Friday 10:00 ET
    assert market_is_open(_et(2026, 10, 9, 16, 15))  # settlement tail
    assert not market_is_open(_et(2026, 10, 9, 16, 30))
    assert not market_is_open(_et(2026, 10, 10, 11))  # Saturday


def test_backfill_when_history_missing_or_stale():
    now = _et(2026, 10, 9, 10)
    assert backfill_due(None, None, now)
    assert backfill_due(None, now - timedelta(days=60), now)


def test_backfill_nightly_on_weekdays_only():
    assert not backfill_due(None, FRESH, _et(2026, 10, 9, 18))  # before 19:00
    assert backfill_due(None, FRESH, _et(2026, 10, 9, 19, 5))
    assert not backfill_due(None, FRESH, _et(2026, 10, 10, 20))  # Saturday, data fresh


def test_backfill_at_most_once_per_day_even_when_stale():
    # holiday: history stays stale after a backfill, which must not retry every cycle
    now = _et(2026, 10, 13, 10)
    assert not backfill_due(date(2026, 10, 13), now - timedelta(days=5), now)


def test_plist_runs_loop_with_keepalive():
    plist = build_plist("/venv/bin/python", db="/data/v.db")
    assert plist["Label"] == LABEL
    assert plist["ProgramArguments"] == ["/venv/bin/python", "-m", "vix_regime.cli", "run"]
    assert plist["KeepAlive"] is True
    assert plist["EnvironmentVariables"]["VIX_REGIME_DB"] == "/data/v.db"


def _args(**kw):
    defaults = {"regime": False, "curve": False, "percentile": None}
    return argparse.Namespace(**{**defaults, **kw})


def test_parse_triggers_default_and_flags():
    assert [t.type for t in _parse_triggers(_args())] == ["regime_change"]
    triggers = _parse_triggers(_args(curve=True, percentile=["vvix:0.9", "SKEW:0.8:both"]))
    assert [t.type for t in triggers] == ["curve_inversion", "percentile_cross", "percentile_cross"]
    assert triggers[1].metric == "VVIX" and triggers[1].threshold == 0.9
    assert triggers[2].direction == "both"
