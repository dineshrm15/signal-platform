from datetime import UTC, datetime, timedelta

from signal_platform import SqliteSnapshotStore, envelope, freshness_label


def test_put_get_latest_roundtrip():
    store = SqliteSnapshotStore(":memory:")
    t1 = datetime(2026, 8, 6, 20, 0, tzinfo=UTC)
    t2 = datetime(2026, 8, 6, 20, 15, tzinfo=UTC)
    store.put("svc", "snapshot", t1, {"v": 1})
    store.put("svc", "snapshot", t2, {"v": 2})
    latest = store.get_latest("svc", "snapshot")
    assert latest is not None
    assert latest.payload == {"v": 2}
    assert latest.as_of == t2


def test_upsert_is_idempotent():
    store = SqliteSnapshotStore(":memory:")
    ts = datetime(2026, 8, 6, tzinfo=UTC)
    store.put("svc", "daily:VIX", ts, {"close": 15.0})
    store.put("svc", "daily:VIX", ts, {"close": 15.5})
    rows = store.get_since("svc", "daily:VIX", ts - timedelta(days=1))
    assert len(rows) == 1
    assert rows[0].payload == {"close": 15.5}


def test_get_since_orders_and_filters():
    store = SqliteSnapshotStore(":memory:")
    base = datetime(2026, 8, 1, tzinfo=UTC)
    store.put_many([("svc", "transition", base + timedelta(days=i), {"i": i}) for i in range(5)])
    rows = store.get_since("svc", "transition", base + timedelta(days=2))
    assert [r.payload["i"] for r in rows] == [2, 3, 4]


def test_freshness_labels():
    now = datetime(2026, 8, 6, 21, 0, tzinfo=UTC)
    assert freshness_label(now - timedelta(minutes=10), now) == "delayed_15min"
    assert freshness_label(now - timedelta(hours=3), now) == "stale"
    assert freshness_label(now - timedelta(days=2), now) == "historical"


def test_envelope_fields():
    as_of = datetime(2026, 8, 6, 20, 50, tzinfo=UTC)
    now = datetime(2026, 8, 6, 21, 0, tzinfo=UTC)
    out = envelope({"x": 1}, as_of=as_of, attribution="attr", disclaimer="disc", now=now)
    assert out["x"] == 1
    assert out["as_of"] == as_of.isoformat()
    assert out["data_freshness"] == "delayed_15min"
    assert out["attribution"] == "attr"
    assert out["disclaimer"] == "disc"
