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

Every payload carries `as_of`, `data_freshness`, `attribution`, and `disclaimer`.

## Quick start

```bash
# from the repo root
uv sync --all-packages

# load history (percentile context), then take a snapshot
uv run vix-regime backfill
uv run vix-regime snapshot

# keep it fresh: every 15 min during market hours, 30-min heartbeat off-hours
uv run vix-regime run
```

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
writer (ingest → derive → classify → SQLite snapshot), and MCP tools read the latest
snapshot. Storage sits behind a small protocol so the hosted deployment can swap SQLite
for DynamoDB without touching calc code. See
[docs/00-platform-architecture.md](../../docs/00-platform-architecture.md).

DB location: `~/.vix-regime/vix_regime.db` (override with `VIX_REGIME_DB`).

## Development

```bash
uv run pytest          # unit + in-memory server tests
uv run ruff check .    # lint
```
