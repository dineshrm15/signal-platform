# vol-regime — Volatility Regime MCP Server

One tool call answers: **"what volatility regime are we in?"**

Derived from Cboe public data (15-min delayed): VIX family indices (VIX9D, VIX, VIX3M,
VIX6M, VIX1Y, VVIX, SKEW) and the VX futures curve. Classification is a published,
deterministic rule set — see [METHODOLOGY.md](METHODOLOGY.md). No black box, no LLM.

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

## Quick start

```bash
# from the repo root
uv sync --all-packages

# load history (percentile context), then take a snapshot
uv run vix-regime backfill
uv run vix-regime snapshot
```

## Keep it running

`vix-regime run` loops on the spec's cadence: every 15 min during market hours, a 30-min
heartbeat off-hours, and a history refresh each weekday evening (or immediately if
history is more than 4 days old).

On macOS, install it as a launchd agent so it starts at login and restarts on crash:

```bash
uv run vix-regime schedule install     # status | uninstall
tail -f ~/.vix-regime/logs/run.log
```

The agent runs from a copy of the code in `~/.vix-regime/runtime`, not from the checkout:
macOS blocks background jobs from reading `~/Documents`, `~/Desktop`, and `~/Downloads`
without a privacy grant. **Re-run `schedule install` after changing the code** so the
agent picks it up.

Elsewhere, run `uv run vix-regime run` under systemd, supervisord, or a container.

## Push alerts

Alerts are evaluated after every snapshot. Triggers: regime changes (default), curve
inversion / re-normalization, and percentile crossings — rules and cooldowns are in
[METHODOLOGY.md](METHODOLOGY.md#alerts).

**Phone notifications with [ntfy](https://ntfy.sh):** install the ntfy app, subscribe to a
hard-to-guess topic name, then:

```bash
uv run vix-regime subscribe ntfy my-vol-alerts-x7k2 --regime --curve --percentile VVIX:0.9
uv run vix-regime subscriptions
uv run vix-regime unsubscribe <subscription_id>
```

ntfy.sh topics are public: anyone who knows the name can read them, so use a random name
(alerts contain only public market data). **Webhook:** `subscribe webhook https://...`
POSTs JSON with `key`, `title`, `message`, `priority`, and structured `data`.

Agents can do the same through the `subscribe` MCP tool. Over HTTP the subscription tools
are disabled unless the server is started with `--allow-subscriptions`, so a hosted
instance can't be used as an open relay.

## Connect an MCP client

**Claude Code**

```bash
claude mcp add vol-regime -- uv --directory /path/to/agents-mcp run python -m vix_regime.server
```

**Claude Desktop / Cursor / generic stdio client** — add to your MCP config:

```json
{
  "mcpServers": {
    "vol-regime": {
      "command": "uv",
      "args": ["--directory", "/path/to/agents-mcp", "run", "python", "-m", "vix_regime.server"]
    }
  }
}
```

**Streamable HTTP**

```bash
uv run python -m vix_regime.server --http --port 8000
# endpoint: http://127.0.0.1:8000/mcp
```

## Architecture

The server never computes on the request path: the `vix-regime` CLI pipeline is the only
snapshot writer (ingest → derive → classify → SQLite snapshot → alert fan-out), and MCP
data tools read the latest snapshot. Storage sits behind a small protocol so the hosted deployment can swap SQLite
for DynamoDB without touching calc code. See
[docs/00-platform-architecture.md](../../docs/00-platform-architecture.md).

DB location: `~/.vix-regime/vix_regime.db` (override with `VIX_REGIME_DB`).

## Development

```bash
uv run pytest                 # unit + in-memory server tests (no network)
uv run ruff check .           # lint
uv run ruff format --check .  # formatting (enforced in CI)
```

For local webhook testing, `VIX_REGIME_ALLOW_PRIVATE_TARGETS=1` permits `http://` and
localhost targets. Never set it on a hosted deployment.
