# Signal Platform — Shared Architecture Spec

**Status:** Draft v0.1 · **Owner:** Dinesh · **Last updated:** 2026-08-06

## 1. Purpose

A reusable template for "derived-signal agents": services that periodically ingest public/low-cost data, compute derived intelligence, and expose it to AI agents via MCP with optional push notifications to subscribers. Four services build on this platform:

| # | Service | Domain | Data cost |
|---|---------|--------|-----------|
| 01 | VIX Regime Server | Volatility / trading | $0 (CBOE public) |
| 02 | Integration Platform Watcher | Enterprise ops (B2B) | $0 (vendor notes, NVD) |
| 03 | Grid/Energy Intelligence | Home energy / resilience | $0 (CAISO, PG&E, NWS) |
| 04 | Product Recall Agent | Consumer safety | $0 (openFDA, NHTSA) |

## 2. Reference Architecture

```
┌─────────────┐   ┌──────────────┐   ┌───────────┐   ┌──────────────┐
│ EventBridge  │──▶│ Calc Workers │──▶│ Signal    │◀──│ MCP Server   │◀── Agents
│ (schedules)  │   │ (Fargate /   │   │ Store     │   │ (stateless,  │    (Claude,
└─────────────┘   │  Lambda)     │   │ (DynamoDB │   │  streamable  │     custom)
                  └──────┬───────┘   │  + S3)    │   │  HTTP)       │
                         │           └───────────┘   └──────────────┘
                         ▼
                  ┌──────────────┐   ┌──────────────────────────────┐
                  │ Change       │──▶│ Push Fan-out                 │──▶ Subscribers
                  │ Detector     │   │ (SNS → webhook / email /     │
                  └──────────────┘   │  ntfy.sh / Telegram)         │
                                     └──────────────────────────────┘
```

### Components

- **Scheduler:** EventBridge rules per service (cron for market hours / daily / interval).
- **Calc workers:** Python 3.12. Lambda for <15-min jobs; Fargate for longer batch (reuse pa_scanner deployment pattern). One container image per service, shared base image.
- **Signal store:** DynamoDB single-table per service — latest snapshot (hot key) + time-series history (TTL for cost control). S3 for large artifacts (historical curves, raw pulls).
- **MCP server:** FastMCP (Python) behind API Gateway / ALB, stateless, reads only from the signal store — never computes on request path. Streamable HTTP transport. Tools return JSON with `as_of` timestamps and data-freshness labels on every payload.
- **Push channel:** MCP has no offline-subscriber notification model, so push runs beside MCP: change detector diffs consecutive snapshots against per-subscriber rules → SNS → webhook/email/Telegram/ntfy. Subscriptions managed via MCP tools (`subscribe`, `list_subscriptions`, `unsubscribe`) + REST fallback.

### Cross-cutting

- **Auth:** API keys (free tier: rate-limited anonymous; paid: keyed). OAuth 2.1 later.
- **Rate limiting:** API Gateway usage plans; free tier ~60 req/hr.
- **Observability:** CloudWatch metrics + structured logs; per-tool invocation counts (adoption analytics = product signal).
- **Registry presence:** publish to MCP directories; each repo has README with one-line connect instructions for Claude, Cursor, and generic clients.
- **Disclaimers:** every payload includes `disclaimer` field. Trading services: informational, not financial advice. Safety-adjacent services (03): "planning tool — always follow official utility/CAL FIRE/NWS alerts."

## 3. Shared Tech Stack

- Python 3.12, FastMCP, pydantic v2, httpx, pandas (calc only)
- IaC: Terraform (portfolio value: shows infra competence)
- CI: GitHub Actions — lint, tests, container build, deploy
- Repos: public monorepo `signal-platform` (shared libs + IaC) + one public repo per service, or single monorepo — decide at kickoff

## 4. Cost Envelope (all four running)

| Item | Est. monthly |
|------|-------------|
| Lambda + Fargate compute | $15–40 |
| DynamoDB (on-demand) + S3 | $5–15 |
| API Gateway + ALB | $10–25 |
| SNS / email | $1–5 |
| **Total** | **~$30–85/mo** |

No data licensing costs in v1. Real-time options data (GEX expansion) is a later, revenue-gated decision.

## 5. Monetization Model (shared)

- **Free tier:** delayed/daily signals, rate-limited, no push.
- **Pro tier ($9–29/mo depending on service):** fresh signals, push notifications, history endpoints.
- **B2B/API tier (service 02, 03):** custom pricing, SLA, higher limits.
- Billing: Stripe; keys provisioned on webhook. Not built until a service shows organic pull.

## 6. Sequencing

1. **01 VIX** — proves the full template end-to-end (target: 2–3 weekends)
2. **02 Watcher** — B2B (target: 4 weekends, LLM summarization layer)
3. **03 Grid** — largest build; ship in modules (rates → grid stress → outage risk)
4. **04 Recall** — simplest calc; can parallelize with 03 if momentum is good

## 7. Open Questions

- Monorepo vs. per-service repos
- ntfy.sh vs. Telegram bot as the free push channel
- Whether to publish the platform template itself as an open-source "derived-signal MCP starter kit" (strong portfolio angle)
