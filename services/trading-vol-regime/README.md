# trading-vol-regime — Volatility Regime MCP Server

One tool call answers: **"what volatility regime are we in?"**

Derived from Cboe public data (15-min delayed): VIX family indices (VIX9D, VIX, VIX3M,
VIX6M, VIX1Y, VVIX, SKEW) and the VX futures curve. Classification is a published,
deterministic rule set — see
[METHODOLOGY.md](https://github.com/dineshrm15/signal-platform/blob/main/services/trading-vol-regime/METHODOLOGY.md).
No black box, no LLM.

> Informational only — not financial advice. VIX® is a registered trademark of Cboe
> Global Markets; this project is not affiliated with or endorsed by Cboe.

## Tools

| Tool | Returns |
|------|---------|
| `get_vol_regime` | regime label (`CALM_CARRY` / `COMPRESSED_SPRING` / `STRESS_BUILDING` / `RISK_OFF` / `CAPITULATION`), confidence, rationale, key ratios |
| `get_term_structure(kind)` | `cash`: index levels, ratios, slope, 1y/5y percentiles · `futures`: full VX curve, roll yield, basis, twist |
| `get_regime_transitions(lookback_days)` | timestamped log of regime changes |
| `explain_regime` | plain-English narrative of the current state |
| `subscribe(channel, target, triggers)` | push alerts via ntfy or webhook; returns a `subscription_id` |
| `get_subscription(subscription_id)` / `unsubscribe(subscription_id)` | manage a subscription |

Every data payload carries `as_of`, `data_freshness`, `attribution`, and `disclaimer`.

## Install

Requires Python 3.12+. With [uv](https://docs.astral.sh/uv/):

```bash
uv tool install trading-vol-regime
```

This installs two commands: `trading-vol-regime` (data pipeline, alerts, scheduling) and
`trading-vol-regime-server` (the MCP server). `pip install trading-vol-regime` works too.

## Quick start

The MCP server only reads snapshots; the pipeline produces them. Load history (for
percentile context) and take a first snapshot:

```bash
trading-vol-regime backfill
trading-vol-regime snapshot
```

## Connect an MCP client

**Claude Code**

```bash
claude mcp add trading-vol-regime -- uvx --from trading-vol-regime trading-vol-regime-server
```

**Claude Desktop / Cursor / generic stdio client** — add to your MCP config:

```json
{
  "mcpServers": {
    "trading-vol-regime": {
      "command": "uvx",
      "args": ["--from", "trading-vol-regime", "trading-vol-regime-server"]
    }
  }
}
```

If you installed with `uv tool install`, `"command": "trading-vol-regime-server"` with no
args also works.

**Streamable HTTP**

```bash
trading-vol-regime-server --http --port 8000
# endpoint: http://127.0.0.1:8000/mcp
```

## Keep it running

`trading-vol-regime run` loops on a fixed cadence: every 15 min during market hours, a
30-min heartbeat off-hours, and a history refresh each weekday evening (or immediately if
history is more than 4 days old).

On macOS, install it as a launchd agent so it starts at login and restarts on crash:

```bash
trading-vol-regime schedule install     # status | uninstall
tail -f ~/.trading-vol-regime/logs/run.log
```

macOS blocks background jobs from reading `~/Documents`, `~/Desktop`, and `~/Downloads`,
so the agent must run from an environment outside them. A `uv tool install` qualifies;
`schedule install` refuses temporary `uvx` environments and environments inside those
folders, and explains what to do instead.

Elsewhere, run `trading-vol-regime run` under systemd, supervisord, or a container.

## Push alerts

Alerts are evaluated after every snapshot. Triggers: regime changes (default), curve
inversion / re-normalization, and percentile crossings — rules and cooldowns are in
[METHODOLOGY.md](https://github.com/dineshrm15/signal-platform/blob/main/services/trading-vol-regime/METHODOLOGY.md#alerts).

**Phone notifications with [ntfy](https://ntfy.sh):** install the ntfy app, subscribe to a
hard-to-guess topic name, then:

```bash
trading-vol-regime subscribe ntfy my-vol-alerts-x7k2 --regime --curve --percentile VVIX:0.9
trading-vol-regime subscriptions
trading-vol-regime unsubscribe <subscription_id>
```

ntfy.sh topics are public: anyone who knows the name can read them, so use a random name
(alerts contain only public market data). **Webhook:** `subscribe webhook https://...`
POSTs JSON with `key`, `title`, `message`, `priority`, and structured `data`.

Agents can do the same through the `subscribe` MCP tool. Over HTTP the subscription tools
are disabled unless the server is started with `--allow-subscriptions`, so a hosted
instance can't be used as an open relay.

## Architecture

The server never computes on the request path: the `trading-vol-regime` pipeline is the
only snapshot writer (ingest → derive → classify → SQLite snapshot → alert fan-out), and
MCP data tools read the latest snapshot. Storage sits behind a small protocol so a hosted
deployment can swap SQLite for DynamoDB without touching calc code. Built on
[signal-platform](https://pypi.org/project/signal-platform/); see the
[platform architecture](https://github.com/dineshrm15/signal-platform/blob/main/docs/00-platform-architecture.md).

DB location: `~/.trading-vol-regime/trading_vol_regime.db` (override with
`TRADING_VOL_REGIME_DB`).

## Development

From a checkout of [the monorepo](https://github.com/dineshrm15/signal-platform):

```bash
uv sync --all-packages
uv run trading-vol-regime backfill && uv run trading-vol-regime snapshot
uv run pytest                 # unit + in-memory server tests (no network)
uv run ruff check .           # lint
uv run ruff format --check .  # formatting (enforced in CI)
```

Connect a client to the checkout with
`uv --directory /path/to/signal-platform run trading-vol-regime-server`. From a checkout,
`schedule install` copies the code into `~/.trading-vol-regime/runtime` (checkouts usually
live in `~/Documents`), so **re-run it after changing the code**.

For local webhook testing, `TRADING_VOL_REGIME_ALLOW_PRIVATE_TARGETS=1` permits `http://`
and localhost targets. Never set it on a hosted deployment.

## License

MIT
