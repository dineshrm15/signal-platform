"""CLI: `vix-regime backfill | snapshot | run [--interval N] [--once]`."""

from __future__ import annotations

import argparse
import logging
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
from signal_platform import SqliteSnapshotStore

from vix_regime.config import db_path
from vix_regime.pipeline import run_backfill, run_snapshot

log = logging.getLogger(__name__)

_ET = ZoneInfo("America/New_York")

# Spec §7: market hours every 15 min, off hours 30-min heartbeat.
OFF_HOURS_INTERVAL_S = 1800


def market_is_open(now: datetime | None = None) -> bool:
    """Regular equity session incl. the 16:15 ET index settlement tail."""
    now_et = (now or datetime.now(_ET)).astimezone(_ET)
    if now_et.weekday() >= 5:
        return False
    minutes = now_et.hour * 60 + now_et.minute
    return 9 * 60 + 30 <= minutes <= 16 * 60 + 15


def _client() -> httpx.Client:
    return httpx.Client(timeout=30, headers={"User-Agent": "vix-regime/0.1"})


def cmd_backfill(store: SqliteSnapshotStore) -> None:
    with _client() as client:
        counts = run_backfill(store, client)
    total = sum(counts.values())
    print(f"Backfilled {total} daily closes: " + ", ".join(f"{s}={n}" for s, n in counts.items()))


def cmd_snapshot(store: SqliteSnapshotStore) -> None:
    with _client() as client:
        snapshot, transitioned = run_snapshot(store, client)
    regime = snapshot.regime
    print(
        f"[{snapshot.as_of.isoformat()}] {regime.label} "
        f"(confidence {regime.confidence:.0%})"
        + (" — TRANSITION" if transitioned else "")
    )
    print(f"  VIX {snapshot.cash.values.get('VIX')} | "
          f"VIX/VIX3M {snapshot.cash.ratio_vix_vix3m} ({snapshot.cash.slope}) | "
          f"9D/VIX {snapshot.cash.ratio_vix9d_vix}")
    if snapshot.futures:
        print(f"  M1 {snapshot.futures.m1_symbol} {snapshot.futures.m1_price} | "
              f"roll {snapshot.futures.m1_m2_roll_yield_pct}% ({snapshot.futures.slope})")
    print(f"  {regime.rationale}")


def cmd_run(store: SqliteSnapshotStore, interval: int, once: bool) -> None:
    while True:
        try:
            cmd_snapshot(store)
        except Exception:
            log.exception("snapshot run failed; will retry next cycle")
        if once:
            return
        sleep_s = interval if market_is_open() else OFF_HOURS_INTERVAL_S
        time.sleep(sleep_s)


def main() -> None:
    parser = argparse.ArgumentParser(prog="vix-regime")
    parser.add_argument("--db", default=None, help="snapshot DB path (default: config)")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("backfill", help="load daily history for percentile context")
    sub.add_parser("snapshot", help="one ingest+classify cycle")
    run_p = sub.add_parser("run", help="snapshot loop (market-hours aware)")
    run_p.add_argument("--interval", type=int, default=900, help="market-hours interval seconds")
    run_p.add_argument("--once", action="store_true", help="single cycle, then exit")

    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

    store = SqliteSnapshotStore(args.db or db_path())
    try:
        if args.command == "backfill":
            cmd_backfill(store)
        elif args.command == "snapshot":
            cmd_snapshot(store)
        elif args.command == "run":
            cmd_run(store, args.interval, args.once)
    finally:
        store.close()


if __name__ == "__main__":
    main()
