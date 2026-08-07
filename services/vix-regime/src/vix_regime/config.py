"""Service constants and local configuration."""

from __future__ import annotations

import os
from pathlib import Path

SERVICE_NAME = "vix-regime"

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


def db_path() -> Path:
    """Snapshot DB location. Override with VIX_REGIME_DB; default lives under the
    user's home so the MCP server finds the same DB regardless of spawn cwd."""
    env = os.environ.get("VIX_REGIME_DB")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".vix-regime" / "vix_regime.db"
