import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx
from signal_platform.push import (
    MAX_CONSECUTIVE_FAILURES,
    InvalidTarget,
    Notification,
    SqliteSubscriptionStore,
    dispatch,
    in_cooldown,
    send,
    validate_target,
)

NOW = datetime(2026, 10, 9, 15, 0, tzinfo=UTC)


def _note(key="k", cooldown=0):
    return Notification(key=key, title="T → U", message="m", cooldown_minutes=cooldown)


# -------------------------------------------------------------- validation


def test_ntfy_topic_names_pass_without_dns():
    assert validate_target("ntfy", "vol-alerts_42") == "vol-alerts_42"


@pytest.mark.parametrize(
    "channel,target",
    [
        ("webhook", "http://example.com/hook"),  # not https
        ("webhook", "ftp://example.com/hook"),
        ("webhook", "not a url"),
        ("webhook", "https://127.0.0.1/hook"),  # loopback
        ("webhook", "https://10.0.0.5/hook"),  # private
        ("webhook", "https://169.254.169.254/latest"),  # link-local / cloud metadata
        ("ntfy", "has spaces"),
    ],
)
def test_rejects_unsafe_targets(channel, target):
    with pytest.raises(InvalidTarget):
        validate_target(channel, target)


def test_allow_private_permits_local_dev_targets():
    assert validate_target("webhook", "http://127.0.0.1:8765/hook", allow_private=True)


# -------------------------------------------------------------- channels


@respx.mock
def test_ntfy_uses_json_publish_with_unicode_title():
    route = respx.post("https://ntfy.sh/").mock(return_value=httpx.Response(200))
    with httpx.Client() as client:
        send(client, "ntfy", "my-topic", _note())
    body = json.loads(route.calls.last.request.content)
    assert body["topic"] == "my-topic"
    assert body["title"] == "T → U"
    assert body["priority"] == 3


@respx.mock
def test_ntfy_self_hosted_url():
    route = respx.post("https://ntfy.example.org/").mock(return_value=httpx.Response(200))
    with httpx.Client() as client:
        send(client, "ntfy", "https://ntfy.example.org/alerts", _note())
    assert json.loads(route.calls.last.request.content)["topic"] == "alerts"


@respx.mock
def test_webhook_posts_json():
    route = respx.post("https://hooks.example.org/x").mock(return_value=httpx.Response(204))
    with httpx.Client() as client:
        send(client, "webhook", "https://hooks.example.org/x", _note(key="regime"))
    assert json.loads(route.calls.last.request.content)["key"] == "regime"


# -------------------------------------------------------------- store


def test_create_get_deactivate():
    store = SqliteSubscriptionStore(":memory:")
    sub = store.create("svc", "ntfy", "topic", [{"type": "regime_change"}])
    assert sub.id.startswith("sub_") and len(sub.id) > 20
    assert store.get(sub.id).triggers == [{"type": "regime_change"}]
    assert [s.id for s in store.list_active("svc")] == [sub.id]
    assert store.deactivate(sub.id) is True
    assert store.deactivate(sub.id) is False
    assert store.list_active("svc") == []


def test_repeated_failures_deactivate():
    store = SqliteSubscriptionStore(":memory:")
    sub = store.create("svc", "ntfy", "topic", [])
    for _ in range(MAX_CONSECUTIVE_FAILURES - 1):
        store.record_delivery(sub.id, ok=False, key=None, at=NOW)
    assert store.get(sub.id).active
    store.record_delivery(sub.id, ok=True, key="k", at=NOW)  # success resets the count
    assert store.get(sub.id).consecutive_failures == 0
    for _ in range(MAX_CONSECUTIVE_FAILURES):
        store.record_delivery(sub.id, ok=False, key=None, at=NOW)
    assert not store.get(sub.id).active


# -------------------------------------------------------------- dispatch


def test_cooldown_window():
    store = SqliteSubscriptionStore(":memory:")
    sub = store.create("svc", "ntfy", "topic", [])
    sub.last_notified["k"] = (NOW - timedelta(minutes=30)).isoformat()
    assert in_cooldown(sub, _note("k", cooldown=60), NOW)
    assert not in_cooldown(sub, _note("k", cooldown=20), NOW)
    assert not in_cooldown(sub, _note("other", cooldown=60), NOW)


@respx.mock
def test_dispatch_isolates_failing_targets_and_applies_cooldown():
    respx.post("https://ntfy.sh/").mock(
        side_effect=lambda req: httpx.Response(
            500 if json.loads(req.content)["topic"] == "broken" else 200
        )
    )
    store = SqliteSubscriptionStore(":memory:")
    good = store.create("svc", "ntfy", "good", [])
    bad = store.create("svc", "ntfy", "broken", [])

    with httpx.Client() as client:
        first = dispatch(store, client, "svc", lambda s: [_note("k", cooldown=60)], now=NOW)
        second = dispatch(
            store,
            client,
            "svc",
            lambda s: [_note("k", cooldown=60)],
            now=NOW + timedelta(minutes=5),
        )

    assert (first.delivered, first.failed) == (1, 1)
    assert second.suppressed == 1  # good is in cooldown
    assert second.failed == 1  # broken never delivered, so it retries
    assert store.get(good.id).last_notified["k"] == NOW.isoformat()
    assert store.get(bad.id).consecutive_failures == 2
