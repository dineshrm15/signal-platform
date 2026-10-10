# 01 — VIX Regime Server

**Status:** Draft v0.1 · Builds on: `00-platform-architecture.md`

## 1. Problem

Traders and trading agents need a single, reliable answer to "what volatility regime are we in?" before making any decision. The inputs (VIX family indices, VX futures curve) are public but scattered; the interpretation (contango/backwardation, regime classification, roll yield) requires assembly. No MCP-native source exists — vixcentral serves humans, not agents.

## 2. Users

- 0DTE / options traders using agents or scripting their workflow (primary; overlaps GEX audience)
- Trading agent builders who want vol context as one tool call
- Dinesh's own pa_scanner (first consumer: regime gate input)

## 3. Data Sources (all free)

| Source | Data | Cadence |
|--------|------|---------|
| CBOE index dissemination (delayed, public pages/downloads) | VIX9D, VIX, VIX3M, VIX6M, VIX1Y, VVIX, SKEW | 15-min delayed intraday |
| CBOE CFE public quotes | VX futures curve (all listed expiries), volume, OI | Delayed intraday |
| CBOE historical downloads (free myCboe account) | Full history for backfill | Daily |
| `vix_utils` (OSS) | Reference implementation for term-structure assembly | — |

Licensing: display with CBOE attribution; VIX is a CBOE trademark — name the product carefully (no "VIX" in the service's brand name), no claim of affiliation, delayed data only in v1.

## 4. Derived Signals

- **Cash term structure state:** VIX9D/VIX, VIX/VIX3M, VIX3M/VIX6M ratios; slope classification (steep contango / contango / flat / backwardation / inverted crisis)
- **Futures curve state:** front-month basis, M1/M2 roll yield, curve twist vs. prior day
- **Regime label (the headline output):** enum such as `CALM_CARRY`, `COMPRESSED_SPRING` (low VIX + high SKEW/VVIX), `STRESS_BUILDING`, `RISK_OFF`, `CAPITULATION`, with confidence + one-paragraph rationale
- **Divergence flags:** VVIX/VIX divergence, SKEW extremes, 9D>30D inversions
- **Percentile context:** each metric vs. 1y/5y distribution
- **Regime transitions:** timestamped log of label changes (this drives push)

Methodology published openly in the repo (differentiator vs. black boxes).

## 5. MCP Tool Interface

| Tool | Params | Returns |
|------|--------|---------|
| `get_vol_regime` | — | regime label, confidence, rationale, key ratios, as_of |
| `get_term_structure` | `kind: cash\|futures` | full curve with values, slopes, percentiles |
| `get_metric_history` | `metric, lookback_days` | time series (Pro) |
| `get_regime_transitions` | `lookback_days` | transition log |
| `explain_regime` | — | plain-English narrative for the current state |
| `subscribe` / `unsubscribe` | rule (e.g., on transition, on inversion) | subscription id |

## 6. Push Triggers

- Regime label transition (default)
- Curve inversion / re-normalization
- VVIX or SKEW crossing configurable percentile thresholds

## 7. Compute Schedule

- Market hours: every 15 min (matches delayed data freshness)
- Off hours: 30-min heartbeat + end-of-day summary snapshot
- Nightly: historical backfill/repair job

## 8. Tiers

- **Free:** current regime + term structure, 15-min delayed, no push
- **Pro $9/mo:** push alerts, history endpoints, transition log
- (Real-time upgrade only if paid demand appears — new data cost decision)

## 9. Milestones

1. **W1:** ingestion + term-structure assembly + DynamoDB snapshots (port vix_utils patterns)
2. **W2:** regime classifier + FastMCP server with 3 core tools, deployed
3. **W3:** push pipeline, MCP directory listings, README + methodology doc, launch post (r/options, r/VolatilityTrading, X)
4. **W4+:** history endpoints, pa_scanner integration as showcase

## 10. Risks

- CBOE changes public page/download formats (mitigate: adapter layer, alerts on parse failure)
- Regime taxonomy seen as arbitrary (mitigate: publish methodology + backtested transition stats)
- Low willingness-to-pay (acceptable: primary ROI is template validation + portfolio)
