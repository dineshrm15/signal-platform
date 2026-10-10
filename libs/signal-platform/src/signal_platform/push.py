"""Push fan-out: subscriptions, channels, and delivery bookkeeping.

MCP has no offline-subscriber notification model, so push runs beside MCP
(docs/00-platform-architecture.md §2). Services decide *what* happened (events);
this module decides *who* hears about it and delivers it.

Channels:
  - ntfy:    target is a topic name (public ntfy.sh) or a full https URL to a topic
             on a self-hosted server
  - webhook: target is an https URL that receives a JSON POST

Safety rules for an endpoint that sends traffic on behalf of callers:
  - webhook targets must be https and must not resolve to private, loopback, or
    link-local addresses (SSRF guard), unless explicitly allowed for local dev
  - a subscription is only created if its welcome message is delivered, so
    unreachable targets never enter the store
  - subscriptions are auto-deactivated after repeated delivery failures
  - subscription ids are unguessable tokens and act as the capability to manage them
"""

from __future__ import annotations

import ipaddress
import json
import logging
import re
import secrets
import socket
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, Protocol
from urllib.parse import urlparse

import httpx

log = logging.getLogger(__name__)

Channel = Literal["ntfy", "webhook"]

NTFY_DEFAULT_SERVER = "https://ntfy.sh"
MAX_CONSECUTIVE_FAILURES = 5

_NTFY_TOPIC_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_PRIORITY = {"low": 2, "default": 3, "high": 4, "urgent": 5}


class InvalidTarget(ValueError):
    pass


@dataclass(frozen=True)
class Notification:
    key: str  # stable identity of the condition, used for cooldowns
    title: str
    message: str
    priority: Literal["low", "default", "high", "urgent"] = "default"
    tags: list[str] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)
    cooldown_minutes: int = 0


@dataclass
class Subscription:
    id: str
    service: str
    channel: Channel
    target: str
    triggers: list[dict[str, Any]]
    created_at: datetime
    active: bool = True
    consecutive_failures: int = 0
    last_notified: dict[str, str] = field(default_factory=dict)  # key -> iso ts

    def public_view(self) -> dict[str, Any]:
        return {
            "subscription_id": self.id,
            "channel": self.channel,
            "target": self.target,
            "triggers": self.triggers,
            "created_at": self.created_at.isoformat(),
            "active": self.active,
            "consecutive_failures": self.consecutive_failures,
        }


# ---------------------------------------------------------------- target validation


def _is_public_ip(raw: str) -> bool:
    ip = ipaddress.ip_address(raw)
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def validate_target(channel: Channel, target: str, *, allow_private: bool = False) -> str:
    """Returns the normalized target or raises InvalidTarget."""
    target = target.strip()
    if channel == "ntfy":
        if _NTFY_TOPIC_RE.match(target):
            return target
        url = target
    elif channel == "webhook":
        url = target
    else:
        raise InvalidTarget(f"unknown channel {channel!r}")

    parsed = urlparse(url)
    if parsed.scheme not in ("https", "http") or not parsed.hostname:
        raise InvalidTarget(
            "ntfy target must be a topic name (letters, digits, - and _) or an https URL"
            if channel == "ntfy"
            else "webhook target must be an https URL"
        )
    if allow_private:
        return url
    if parsed.scheme != "https":
        raise InvalidTarget("target must use https")
    try:
        infos = socket.getaddrinfo(parsed.hostname, parsed.port or 443)
    except socket.gaierror as exc:
        raise InvalidTarget(f"cannot resolve host {parsed.hostname!r}") from exc
    for info in infos:
        if not _is_public_ip(info[4][0]):
            raise InvalidTarget("target resolves to a private or local address")
    return url


# ---------------------------------------------------------------- channels


def _send_ntfy(client: httpx.Client, target: str, note: Notification) -> None:
    if _NTFY_TOPIC_RE.match(target):
        server, topic = NTFY_DEFAULT_SERVER, target
    else:
        parsed = urlparse(target)
        topic = parsed.path.strip("/").split("/")[-1]
        server = f"{parsed.scheme}://{parsed.netloc}"
    # JSON publishing: avoids latin-1 header limits on titles with non-ASCII text.
    resp = client.post(
        server + "/",
        json={
            "topic": topic,
            "title": note.title,
            "message": note.message,
            "priority": _PRIORITY[note.priority],
            "tags": note.tags,
        },
    )
    resp.raise_for_status()


def _send_webhook(client: httpx.Client, target: str, note: Notification) -> None:
    resp = client.post(
        target,
        json={
            "key": note.key,
            "title": note.title,
            "message": note.message,
            "priority": note.priority,
            "tags": note.tags,
            "data": note.data,
        },
    )
    resp.raise_for_status()


SENDERS: dict[str, Callable[[httpx.Client, str, Notification], None]] = {
    "ntfy": _send_ntfy,
    "webhook": _send_webhook,
}


def send(client: httpx.Client, channel: Channel, target: str, note: Notification) -> None:
    SENDERS[channel](client, target, note)


# ---------------------------------------------------------------- store


class SubscriptionStore(Protocol):
    def create(
        self, service: str, channel: Channel, target: str, triggers: list[dict[str, Any]]
    ) -> Subscription: ...

    def get(self, subscription_id: str) -> Subscription | None: ...

    def deactivate(self, subscription_id: str) -> bool: ...

    def list_active(self, service: str) -> list[Subscription]: ...

    def record_delivery(
        self, subscription_id: str, *, ok: bool, key: str | None, at: datetime
    ) -> None: ...


_SUB_SCHEMA = """
CREATE TABLE IF NOT EXISTS subscriptions (
    id                   TEXT PRIMARY KEY,
    service              TEXT NOT NULL,
    channel              TEXT NOT NULL,
    target               TEXT NOT NULL,
    triggers             TEXT NOT NULL,
    created_at           TEXT NOT NULL,
    active               INTEGER NOT NULL DEFAULT 1,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    last_notified        TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_subscriptions_active ON subscriptions (service, active);
"""


class SqliteSubscriptionStore:
    def __init__(self, path: str | Path) -> None:
        self._path = str(path)
        if self._path != ":memory:":
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._conn.executescript(_SUB_SCHEMA)
        self._conn.commit()

    @staticmethod
    def _row(row: tuple) -> Subscription:
        return Subscription(
            id=row[0],
            service=row[1],
            channel=row[2],
            target=row[3],
            triggers=json.loads(row[4]),
            created_at=datetime.fromisoformat(row[5]),
            active=bool(row[6]),
            consecutive_failures=row[7],
            last_notified=json.loads(row[8]),
        )

    def create(
        self, service: str, channel: Channel, target: str, triggers: list[dict[str, Any]]
    ) -> Subscription:
        sub = Subscription(
            id="sub_" + secrets.token_urlsafe(18),
            service=service,
            channel=channel,
            target=target,
            triggers=triggers,
            created_at=datetime.now(UTC),
        )
        with self._conn:
            self._conn.execute(
                "INSERT INTO subscriptions (id, service, channel, target, triggers, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    sub.id,
                    service,
                    channel,
                    target,
                    json.dumps(triggers),
                    sub.created_at.isoformat(),
                ),
            )
        return sub

    def get(self, subscription_id: str) -> Subscription | None:
        row = self._conn.execute(
            "SELECT * FROM subscriptions WHERE id = ?", (subscription_id,)
        ).fetchone()
        return self._row(row) if row else None

    def deactivate(self, subscription_id: str) -> bool:
        with self._conn:
            cur = self._conn.execute(
                "UPDATE subscriptions SET active = 0 WHERE id = ? AND active = 1",
                (subscription_id,),
            )
        return cur.rowcount > 0

    def list_active(self, service: str) -> list[Subscription]:
        rows = self._conn.execute(
            "SELECT * FROM subscriptions WHERE service = ? AND active = 1 ORDER BY created_at",
            (service,),
        ).fetchall()
        return [self._row(r) for r in rows]

    def record_delivery(
        self, subscription_id: str, *, ok: bool, key: str | None, at: datetime
    ) -> None:
        sub = self.get(subscription_id)
        if sub is None:
            return
        if ok:
            if key:
                sub.last_notified[key] = at.isoformat()
            failures = 0
        else:
            failures = sub.consecutive_failures + 1
        active = sub.active and failures < MAX_CONSECUTIVE_FAILURES
        with self._conn:
            self._conn.execute(
                "UPDATE subscriptions SET consecutive_failures = ?, last_notified = ?, "
                "active = ? WHERE id = ?",
                (failures, json.dumps(sub.last_notified), int(active), subscription_id),
            )
        if sub.active and not active:
            log.warning(
                "subscription %s deactivated after %d consecutive failures",
                subscription_id,
                failures,
            )

    def close(self) -> None:
        self._conn.close()


# ---------------------------------------------------------------- dispatch


@dataclass
class DispatchResult:
    delivered: int = 0
    failed: int = 0
    suppressed: int = 0  # cooldown


def in_cooldown(sub: Subscription, note: Notification, now: datetime) -> bool:
    if note.cooldown_minutes <= 0:
        return False
    last = sub.last_notified.get(note.key)
    if last is None:
        return False
    return now - datetime.fromisoformat(last) < timedelta(minutes=note.cooldown_minutes)


def dispatch(
    store: SubscriptionStore,
    client: httpx.Client,
    service: str,
    notifications_for: Callable[[Subscription], list[Notification]],
    now: datetime | None = None,
) -> DispatchResult:
    """Evaluate each active subscription and deliver what it should hear about.

    `notifications_for` is service-specific: it turns a subscription's triggers into
    the notifications that subscriber should receive for the current change.
    A failing target never blocks delivery to other subscribers.
    """
    now = now or datetime.now(UTC)
    result = DispatchResult()
    for sub in store.list_active(service):
        for note in notifications_for(sub):
            if in_cooldown(sub, note, now):
                result.suppressed += 1
                continue
            try:
                send(client, sub.channel, sub.target, note)
            except httpx.HTTPError as exc:
                log.warning("delivery to %s failed: %s", sub.id, exc)
                store.record_delivery(sub.id, ok=False, key=None, at=now)
                result.failed += 1
                continue
            store.record_delivery(sub.id, ok=True, key=note.key, at=now)
            sub.last_notified[note.key] = now.isoformat()
            result.delivered += 1
    return result
