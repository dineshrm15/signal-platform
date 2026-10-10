"""Service constants and local configuration."""

from __future__ import annotations

import logging
import os
from pathlib import Path

import httpx

log = logging.getLogger(__name__)

SERVICE_NAME = "trading-vol-regime"

ATTRIBUTION = (
    "Index and futures data from Cboe public delayed feeds (15-min delayed). "
    "VIX is a registered trademark of Cboe Global Markets. "
    "This service is not affiliated with, endorsed by, or sponsored by Cboe."
)

DISCLAIMER = (
    "Informational only — not financial, investment, or trading advice. "
    "Signals are derived from delayed public data and a published rules-based "
    "methodology; verify against primary sources before acting."
)


def allow_private_targets() -> bool:
    """Local-dev escape hatch: permit http:// and private-network push targets
    (e.g. a webhook receiver on localhost). Never enable on a hosted deployment."""
    return os.environ.get("TRADING_VOL_REGIME_ALLOW_PRIVATE_TARGETS") == "1"


_USER_AGENT = {"User-Agent": "trading-vol-regime/0.1"}


def _warn_on_redirect(response: httpx.Response) -> None:
    if response.is_redirect:
        log.warning(
            "upstream endpoint moved: %s -> %s (update the adapter URL)",
            response.request.url,
            response.headers.get("location"),
        )


def data_client() -> httpx.Client:
    """For upstream data sources. Follows redirects so a Cboe host move degrades to a
    logged warning instead of an outage."""
    return httpx.Client(
        timeout=30,
        headers=_USER_AGENT,
        follow_redirects=True,
        event_hooks={"response": [_warn_on_redirect]},
    )


def push_client() -> httpx.Client:
    """For subscriber deliveries. Never follows redirects: a webhook could otherwise
    redirect to an internal address and bypass the target validation."""
    return httpx.Client(timeout=15, headers=_USER_AGENT, follow_redirects=False)


def db_path() -> Path:
    """Snapshot DB location. Override with TRADING_VOL_REGIME_DB; default lives under the
    user's home so the MCP server finds the same DB regardless of spawn cwd."""
    env = os.environ.get("TRADING_VOL_REGIME_DB")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".trading-vol-regime" / "trading_vol_regime.db"
