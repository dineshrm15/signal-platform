# trading-vol-regime — Launch Kit (W3)

Drafts and checklists for publishing service 01. **Nothing here has been posted or
submitted.** Each external step is listed for the owner to do or approve.

## 1. Pre-publish checklist (blocking)

- [ ] **Cboe redistribution terms.** Displaying delayed index values is not the same as
      redistributing them through an API, and a paid Pro tier is clearly redistribution.
      Read Cboe's delayed-data and market-data policies before turning on paid tiers. The
      free tier is lower risk but still worth confirming. Until then, launch free-only.
- [x] **Public naming.** Renamed from `vix-regime` to `trading-vol-regime` (package, CLI,
      module, folder, MCP server name) before any public release, per the spec's rule of
      no "VIX" in the brand name. Check that the name is free on PyPI before publishing there.
- [x] **License.** MIT (root `LICENSE`; `license = "MIT"` in each package). When
      publishing a package to PyPI, copy `LICENSE` into that package folder so the wheel
      includes it.
- [x] **Commit identity.** Commits use the GitHub noreply address (set in this repo's
      git config).
- [x] **Repo visibility.** Public at https://github.com/dineshrm15/signal-platform; CI green.
- [ ] **PyPI release.** Packaging is ready (`.github/workflows/publish.yml`). Both names
      (`signal-platform`, `trading-vol-regime`) were free on PyPI as of 2026-10-10.
      1. pypi.org: create an account and enable 2FA.
      2. Account → Publishing → "Add a new pending publisher", once per project
         (`signal-platform` and `trading-vol-regime`): owner `dineshrm15`, repository
         `signal-platform`, workflow `publish.yml`, environment `pypi`.
      3. Optional dry run: same on test.pypi.org with environment `testpypi`, then run the
         Publish workflow manually from the Actions tab.
      4. Release: `git tag v0.1.0 && git push origin v0.1.0`. The workflow tests, checks
         the tag matches the version, builds, smoke-tests the wheels, and uploads both.
      5. Later releases: bump `version` in the package's `pyproject.toml`, commit, tag.

## 2. MCP directory listings

Submit after the repo is public. Listing copy (reuse across directories):

**Name:** trading-vol-regime
**One-liner:** Volatility regime for agents: one tool call returns the current vol
regime (calm carry → capitulation) from Cboe's VIX term structure and VX futures curve.
**Description:**
> Turns public, 15-minute-delayed Cboe data (VIX9D, VIX, VIX3M, VIX6M, VIX1Y, VVIX, SKEW,
> and the VX futures curve) into a regime label with confidence and rationale. The
> classifier is a published, deterministic rule set — no black box, no LLM. Includes term
> structure detail with 1y/5y percentiles, a regime transition log, plain-English
> explanations, and push alerts via ntfy or webhook. Informational only, not financial
> advice. Not affiliated with Cboe.

**Tools:** get_vol_regime, get_term_structure, get_regime_transitions, explain_regime,
subscribe, get_subscription, unsubscribe
**Tags:** finance, trading, volatility, options, market-data

| Directory | How to list | Needs |
|-----------|-------------|-------|
| Official MCP Registry (registry.modelcontextprotocol.io) | `server.json` + `mcp-publisher` CLI | a published package (PyPI) or a remote URL |
| Smithery | submit GitHub repo | public repo |
| PulseMCP | submission form | public repo |
| mcp.so | submission form / GitHub issue | public repo |
| Glama | auto-indexes public GitHub MCP servers; claim the listing | public repo |

## 3. Launch posts (drafts)

Post timing: a weekday morning ET, ideally on a day the regime is doing something
interesting. Check each subreddit's self-promotion rules first; several require
participation history or a specific flair, and r/options removes most tool promotion.

### r/VolatilityTrading (primary)

**Title:** I built an open-source "vol regime" signal for AI agents — methodology fully published

> I trade around vol regimes and got tired of assembling the same picture every morning
> (VIX/VIX3M, 9D vs 30D, front futures roll, VVIX/SKEW context), so I turned it into a
> small open-source service.
>
> It pulls Cboe's public 15-min delayed data and classifies one of five regimes —
> CALM_CARRY, COMPRESSED_SPRING, STRESS_BUILDING, RISK_OFF, CAPITULATION — with a
> confidence score and the specific rules that fired. The classifier is a plain,
> ordered rule list; every threshold is in METHODOLOGY.md, so you can disagree with it
> line by line (please do).
>
> It's exposed as an MCP server, so Claude/Cursor/any agent can ask "what regime are we
> in?" as one tool call, and it can push regime changes or curve inversions to your phone
> via ntfy.
>
> Example from today: COMPRESSED_SPRING — VIX near the bottom of its 1y range, but SKEW
> in the 90s percentile: calm surface, expensive tails.
>
> Repo: <link>. Not financial advice, not affiliated with Cboe. Feedback on the
> thresholds is the most useful thing you could give me.

### X / Twitter thread

1. Built an open-source MCP server that answers one question for AI agents: what
   volatility regime are we in? Methodology fully published. 🧵
2. Inputs: Cboe's public delayed VIX family (9D → 1Y), VVIX, SKEW, and the VX futures
   curve. Output: one of 5 regimes + confidence + the exact rules that fired.
3. Today it says COMPRESSED_SPRING: VIX near 1y lows, SKEW in the 90s percentile. Calm
   surface, expensive tails.
4. Rules, not ML: every threshold is in METHODOLOGY.md. Disagree with a number? Open an
   issue.
5. Push alerts to your phone (ntfy) on regime changes and curve inversions. Works with
   Claude, Cursor, or any MCP client. <link> — informational only, not advice.

### Show HN (optional, after a week of stable running)

**Title:** Show HN: Vol-regime – an MCP server that classifies the volatility regime

Lead with the build: public data only, the read-only-server/single-writer architecture,
the published rule set, and the starter-kit angle (the platform layer is reusable for
other "derived signal" services).

## 4. Before the launch post: strengthen the methodology

The spec's mitigation for "regime taxonomy seen as arbitrary" is publishing backtested
transition statistics. The cash-index history needed for that is already in the local
DB (~5 years per index). A replay of the classifier over daily closes (futures rules
excluded) would give: time spent in each regime, median regime duration, and forward
SPX/VIX behavior after each transition. One evening of work, and it makes the launch
post far more credible than "trust my thresholds".
