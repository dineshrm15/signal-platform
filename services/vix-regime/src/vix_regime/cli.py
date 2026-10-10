"""CLI entry point. Run `vix-regime --help` for commands."""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from signal_platform import SqliteSnapshotStore
from signal_platform.push import InvalidTarget, SqliteSubscriptionStore

from vix_regime import schedule
from vix_regime.alerts import TriggerSpec, create_subscription, run_alerts
from vix_regime.config import (
    SERVICE_NAME,
    allow_private_targets,
    data_client,
    db_path,
    push_client,
)
from vix_regime.pipeline import latest_daily_as_of, run_backfill, run_snapshot

log = logging.getLogger(__name__)

_ET = ZoneInfo("America/New_York")

# Spec §7: market hours every 15 min, off hours 30-min heartbeat, nightly backfill.
OFF_HOURS_INTERVAL_S = 1800
NIGHTLY_BACKFILL_ET = (19, 0)  # after Cboe posts the day's closes
STALE_HISTORY_AFTER = timedelta(days=4)  # longer than any weekend + holiday


def market_is_open(now: datetime | None = None) -> bool:
    """Regular equity session incl. the 16:15 ET index settlement tail."""
    now_et = (now or datetime.now(_ET)).astimezone(_ET)
    if now_et.weekday() >= 5:
        return False
    minutes = now_et.hour * 60 + now_et.minute
    return 9 * 60 + 30 <= minutes <= 16 * 60 + 15


def backfill_due(
    last_backfill_day: date | None, latest_daily: datetime | None, now: datetime
) -> bool:
    """At most once per ET day: when history is stale, or on weekday evenings."""
    now_et = now.astimezone(_ET)
    if last_backfill_day == now_et.date():
        return False
    if latest_daily is None or now - latest_daily > STALE_HISTORY_AFTER:
        return True
    return now_et.weekday() < 5 and (now_et.hour, now_et.minute) >= NIGHTLY_BACKFILL_ET


def cmd_backfill(store: SqliteSnapshotStore) -> None:
    with data_client() as client:
        counts = run_backfill(store, client)
    total = sum(counts.values())
    print(f"Backfilled {total} daily closes: " + ", ".join(f"{s}={n}" for s, n in counts.items()))


def cmd_snapshot(store: SqliteSnapshotStore, subs: SqliteSubscriptionStore) -> None:
    with data_client() as client:
        result = run_snapshot(store, client)
    with push_client() as client:
        alerts = run_alerts(subs, client, result.previous, result.snapshot.model_dump(mode="json"))
    snapshot, regime = result.snapshot, result.snapshot.regime
    print(
        f"[{snapshot.as_of.isoformat()}] {regime.label} "
        f"(confidence {regime.confidence:.0%})" + (" — TRANSITION" if result.transitioned else "")
    )
    print(
        f"  VIX {snapshot.cash.values.get('VIX')} | "
        f"VIX/VIX3M {snapshot.cash.ratio_vix_vix3m} ({snapshot.cash.slope}) | "
        f"9D/VIX {snapshot.cash.ratio_vix9d_vix}"
    )
    if snapshot.futures:
        print(
            f"  M1 {snapshot.futures.m1_symbol} {snapshot.futures.m1_price} | "
            f"roll {snapshot.futures.m1_m2_roll_yield_pct}% ({snapshot.futures.slope})"
        )
    print(f"  {regime.rationale}")
    if alerts.delivered or alerts.failed or alerts.suppressed:
        print(
            f"  alerts: {alerts.delivered} delivered, {alerts.failed} failed, "
            f"{alerts.suppressed} in cooldown"
        )


def cmd_run(
    store: SqliteSnapshotStore, subs: SqliteSubscriptionStore, interval: int, once: bool
) -> None:
    logging.getLogger("httpx").setLevel(logging.WARNING)
    last_backfill_day: date | None = None
    while True:
        now = datetime.now(UTC)
        if backfill_due(last_backfill_day, latest_daily_as_of(store), now):
            try:
                cmd_backfill(store)
                last_backfill_day = now.astimezone(_ET).date()
            except Exception:
                log.exception("backfill failed; percentiles use existing history")
        try:
            cmd_snapshot(store, subs)
        except Exception:
            log.exception("snapshot run failed; will retry next cycle")
        if once:
            return
        time.sleep(interval if market_is_open() else OFF_HOURS_INTERVAL_S)


def _parse_triggers(args: argparse.Namespace) -> list[TriggerSpec]:
    triggers: list[TriggerSpec] = []
    if args.regime:
        triggers.append(TriggerSpec(type="regime_change"))
    if args.curve:
        triggers.append(TriggerSpec(type="curve_inversion"))
    for spec in args.percentile or []:
        parts = spec.split(":")
        if len(parts) not in (2, 3):
            raise SystemExit(f"--percentile expects METRIC:THRESHOLD[:up|down|both], got {spec!r}")
        triggers.append(
            TriggerSpec(
                type="percentile_cross",
                metric=parts[0].upper(),
                threshold=float(parts[1]),
                direction=parts[2] if len(parts) == 3 else "up",
            )
        )
    return triggers or [TriggerSpec(type="regime_change")]


def cmd_subscribe(subs: SqliteSubscriptionStore, args: argparse.Namespace) -> None:
    triggers = _parse_triggers(args)
    try:
        with push_client() as client:
            sub = create_subscription(
                subs,
                client,
                args.channel,
                args.target,
                triggers,
                allow_private=allow_private_targets(),
            )
    except InvalidTarget as exc:
        raise SystemExit(f"invalid target: {exc}") from exc
    except httpx.HTTPError as exc:
        raise SystemExit(f"welcome message could not be delivered: {exc}") from exc
    print(f"Subscribed {sub.id} ({sub.channel} → {sub.target})")
    print("  triggers: " + ", ".join(t["type"] for t in sub.triggers))


def cmd_subscriptions(subs: SqliteSubscriptionStore) -> None:
    active = subs.list_active(SERVICE_NAME)
    if not active:
        print("No active subscriptions.")
    for sub in active:
        rules = ", ".join(t["type"] for t in sub.triggers)
        print(f"{sub.id}  {sub.channel:8} {sub.target}  [{rules}]")


def main() -> None:
    parser = argparse.ArgumentParser(prog="vix-regime")
    parser.add_argument("--db", default=None, help="snapshot DB path (default: config)")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("backfill", help="load daily history for percentile context")
    sub.add_parser("snapshot", help="one ingest+classify+alert cycle")

    run_p = sub.add_parser("run", help="pipeline loop (market-hours aware, nightly backfill)")
    run_p.add_argument("--interval", type=int, default=900, help="market-hours interval seconds")
    run_p.add_argument("--once", action="store_true", help="single cycle, then exit")

    sched_p = sub.add_parser("schedule", help="manage the background launchd agent (macOS)")
    sched_p.add_argument("action", choices=["install", "uninstall", "status"])

    subs_p = sub.add_parser("subscribe", help="push alerts to ntfy or a webhook")
    subs_p.add_argument("channel", choices=["ntfy", "webhook"])
    subs_p.add_argument("target", help="ntfy topic (or https topic URL) / webhook https URL")
    subs_p.add_argument("--regime", action="store_true", help="regime label changes (default)")
    subs_p.add_argument("--curve", action="store_true", help="curve inversion / re-normalization")
    subs_p.add_argument(
        "--percentile",
        action="append",
        metavar="METRIC:THRESHOLD[:DIR]",
        help="percentile cross, e.g. VVIX:0.9 or SKEW:0.8:both (repeatable)",
    )
    unsub_p = sub.add_parser("unsubscribe", help="deactivate a subscription")
    unsub_p.add_argument("subscription_id")
    sub.add_parser("subscriptions", help="list active subscriptions")

    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

    if args.command == "schedule":
        if sys.platform != "darwin":
            raise SystemExit("schedule uses launchd and is macOS-only; use cron/systemd elsewhere")
        if args.action == "install":
            db = str(Path(args.db).resolve()) if args.db else None
            print(f"Installed {schedule.install(db)}; logs: {schedule.log_path()}")
        elif args.action == "uninstall":
            print("Uninstalled." if schedule.uninstall() else "Not installed.")
        print(f"status: {schedule.status()}")
        return

    path = args.db or db_path()
    store = SqliteSnapshotStore(path)
    subs = SqliteSubscriptionStore(path)
    try:
        if args.command == "backfill":
            cmd_backfill(store)
        elif args.command == "snapshot":
            cmd_snapshot(store, subs)
        elif args.command == "run":
            cmd_run(store, subs, args.interval, args.once)
        elif args.command == "subscribe":
            cmd_subscribe(subs, args)
        elif args.command == "unsubscribe":
            ok = subs.deactivate(args.subscription_id)
            print("Unsubscribed." if ok else "No active subscription with that id.")
        elif args.command == "subscriptions":
            cmd_subscriptions(subs)
    finally:
        store.close()
        subs.close()


if __name__ == "__main__":
    main()
