# Signal Platform

Monorepo for "derived-signal agents": services that periodically ingest public data, compute
derived intelligence, and expose it to AI agents via [MCP](https://modelcontextprotocol.io).

See [docs/00-platform-architecture.md](docs/00-platform-architecture.md) for the shared
architecture spec.

## Services

| Service | Status | Description |
|---------|--------|-------------|
| [trading-vol-regime](services/trading-vol-regime/) | In development | Volatility regime intelligence from CBOE public data ([spec](docs/01-trading-vol-regime-server.md)) |
| integration-platform-watcher | Planned | Enterprise integration ops (B2B) |
| grid-energy-intelligence | Planned | Home energy and grid resilience |
| product-recall-agent | Planned | Household product recall matching |

## Layout

- `libs/signal-platform` — shared platform library (snapshot store, payload envelope)
- `services/<name>` — one package per service; each has its own README with connect instructions
- `docs/` — platform + service specs

## Development

Requires [uv](https://docs.astral.sh/uv/).

```bash
uv sync          # create venv, install all workspace packages
uv run pytest    # run all tests
```
