# Volatility Regime Methodology

This document specifies, exactly, how the service classifies the volatility regime.
Every threshold below appears verbatim in the code
([term_structure.py](src/trading_vol_regime/calc/term_structure.py),
[regime.py](src/trading_vol_regime/calc/regime.py)) — the two are kept in sync by review and by
tests. Nothing here is a black box, an ML model, or an LLM opinion: the classifier is a
deterministic, ordered rule list over public CBOE data (15-min delayed).

**This is informational tooling, not financial advice.**

## Inputs

| Input | Source | Notes |
|-------|--------|-------|
| VIX, VIX9D, VIX3M, VIX6M, VIX1Y | CBOE delayed index quotes | cash term structure |
| VVIX, SKEW | CBOE delayed index quotes | vol-of-vol and tail-pricing context |
| VX futures curve | CBOE delayed futures quotes | standard monthlies only (weeklies ingested but excluded from regime math) |
| Daily history per index | CBOE history CSVs | percentile context |

Illiquid futures with no traded price fall back to settlement. Percentiles are computed
against trailing 1-year (252 trading days) and 5-year (1260) windows of daily closes and
require at least 60 observations, else they are reported as `null` and rules that need
them do not fire.

## Derived metrics

- **Cash ratios:** `VIX9D/VIX`, `VIX/VIX3M`, `VIX3M/VIX6M`
- **Cash slope** (from `VIX/VIX3M`):

  | VIX/VIX3M | Slope state |
  |-----------|-------------|
  | < 0.85 | STEEP_CONTANGO |
  | 0.85 – 0.97 | CONTANGO |
  | 0.97 – 1.03 | FLAT |
  | 1.03 – 1.15 | BACKWARDATION |
  | ≥ 1.15 | INVERTED_CRISIS |

- **Futures metrics** (M1, M2 = first two standard monthlies by expiration):
  - front basis: `(M1 − VIX) / VIX × 100`
  - M1→M2 roll yield: `(M2 − M1) / M1 × 100`
  - curve twist: today's `(M2 − M1)` spread minus yesterday's settlement spread, in vol points
    (negative = front bid relative to back)
- **Futures slope** (from M1→M2 roll yield %):

  | Roll yield | Slope state |
  |------------|-------------|
  | > +7.5% | STEEP_CONTANGO |
  | +1.5% – +7.5% | CONTANGO |
  | −1.5% – +1.5% | FLAT |
  | −8.0% – −1.5% | BACKWARDATION |
  | < −8.0% | INVERTED_CRISIS |

## Regime classification

Labels are evaluated in this order; the first label with **any trigger firing** wins.
If none fire, the label is `CALM_CARRY`.

### 1. CAPITULATION — any of:
- cash slope = INVERTED_CRISIS (VIX/VIX3M ≥ 1.15)
- futures slope = INVERTED_CRISIS (roll yield < −8%)
- VIX ≥ 99th percentile of its 5-year range

Supporting signals (raise confidence): VIX9D/VIX > 1.10; VVIX ≥ 95th percentile (1y).

### 2. RISK_OFF — any of:
- cash slope = BACKWARDATION
- futures slope = BACKWARDATION
- VIX9D/VIX > 1.0 **and** VIX ≥ 70th percentile (1y)

Supporting: VVIX ≥ 80th percentile (1y); curve twist < −0.5 vol points.

### 3. STRESS_BUILDING — any of:
- cash slope = FLAT
- VIX ≥ 60th percentile (1y) while cash slope is still CONTANGO or FLAT
- VIX9D/VIX > 0.95 (short end near inversion)
- futures slope = FLAT

Supporting: curve twist < −0.5 vol points; VVIX ≥ 80th percentile (1y).

### 4. COMPRESSED_SPRING — trigger:
- VIX ≤ 35th percentile (1y) **and** (VVIX ≥ 80th percentile (1y) **or** SKEW ≥ 80th
  percentile (1y))

Supporting: VVIX and SKEW both rich simultaneously.

### 5. CALM_CARRY — default.
Confidence rises with: cash slope = STEEP_CONTANGO; VIX ≤ 35th percentile (1y); futures
slope in contango.

## Confidence

- Triggered labels: `0.55 + 0.10 × (extra triggers) + 0.10 × (supports fired)`, capped at 0.95.
- CALM_CARRY: `0.50 + 0.13 × (supports fired)`, capped at 0.90.

Confidence expresses rule agreement, **not** probability of future market direction.

## Transitions

A transition is recorded whenever the regime label of a new snapshot differs from the
previous snapshot's label. The transition log (timestamp, from, to, rationale) is exposed
via the `get_regime_transitions` tool.

## Alerts

Each subscriber chooses triggers; every trigger compares the previous snapshot with the
new one ([alerts.py](src/trading_vol_regime/alerts.py)). Nothing is sent on the first snapshot.

| Trigger | Fires when | Priority | Cooldown |
|---------|------------|----------|----------|
| `regime_change` (default) | regime label differs from the previous snapshot; optional `to` filter limits it to moves into specific labels | `high` into STRESS_BUILDING / RISK_OFF, `urgent` into CAPITULATION, else `default` | none |
| `curve_inversion` | cash and/or futures slope crosses between non-inverted and inverted, where inverted = BACKWARDATION or INVERTED_CRISIS. Fires on inversion and on re-normalization. Deepening from BACKWARDATION to INVERTED_CRISIS is not a new event (the regime label covers that). A missing futures curve is never treated as re-normalization. | `high` on inversion, `default` on re-normalization | none |
| `percentile_cross` | a metric's 1y or 5y percentile crosses the subscriber's threshold between snapshots (`up`, `down`, or `both`) | `default` | 6 hours per metric/threshold/direction, so noise around a threshold cannot spam |

If one subscriber has overlapping triggers that describe the same event, it is sent once.

Delivery: ntfy (topic on ntfy.sh, or a self-hosted https topic URL) or a webhook (JSON
POST to an https URL). A subscription is created only if its welcome message is
delivered; it is deactivated after 5 consecutive failed deliveries. Webhook targets that
resolve to private, loopback, or link-local addresses are rejected, and deliveries never
follow redirects.

## Known limitations

- 15-minute delayed data: labels can lag fast intraday breaks.
- Percentile windows need history backfill; fresh deployments report `null` percentiles
  and lean on curve-shape rules only.
- Thresholds are hand-set from well-known vol stylized facts, not optimized; transition
  statistics will be published as history accumulates.
