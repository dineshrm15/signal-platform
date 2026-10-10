# signal-platform

Building blocks for **derived-signal MCP services**: services that periodically ingest
public data, compute derived intelligence, and expose it to AI agents over
[MCP](https://modelcontextprotocol.io), with optional push notifications.

Used by [trading-vol-regime](https://pypi.org/project/trading-vol-regime/). Architecture:
[docs/00-platform-architecture.md](https://github.com/dineshrm15/signal-platform/blob/main/docs/00-platform-architecture.md).

## What's in it

| Module | Purpose |
|--------|---------|
| `signal_platform.store` | `SnapshotStore` protocol + SQLite implementation. Calc workers are the only writers; MCP servers only read. Upserts on `(service, kind, as_of)` so re-runs are idempotent. |
| `signal_platform.payload` | Response envelope: every payload carries `as_of`, a `data_freshness` label, `attribution`, and `disclaimer`. |
| `signal_platform.push` | Subscriptions and delivery to ntfy topics or webhooks, with per-event cooldowns, auto-deactivation after repeated failures, and SSRF guards (public https targets only, redirects never followed). |

## Example

```python
from datetime import UTC, datetime

import httpx
from signal_platform import SqliteSnapshotStore, envelope
from signal_platform.push import Notification, send

store = SqliteSnapshotStore("signals.db")
store.put("my-service", "snapshot", datetime.now(UTC), {"level": 42})

latest = store.get_latest("my-service", "snapshot")
payload = envelope(
    latest.payload,
    as_of=latest.as_of,
    attribution="Data from Example Source",
    disclaimer="Informational only.",
)

with httpx.Client() as client:
    send(
        client,
        "ntfy",
        "my-random-topic",
        Notification(key="demo", title="Hi", message="Signal changed"),
    )
```

## License

MIT
