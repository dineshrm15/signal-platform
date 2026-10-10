"""Local scheduling via a macOS launchd user agent.

The agent keeps `trading-vol-regime run` alive: launchd restarts it on crash and starts it at
login. The loop itself owns cadence (15-min market hours, 30-min off hours, nightly
history refresh), so launchd only has to keep one process running.

The hosted equivalent is EventBridge → Lambda (docs/00-platform-architecture.md).
"""

from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
import sys
import time
from pathlib import Path

LABEL = "com.signalplatform.trading-vol-regime"


def plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def log_path() -> Path:
    return Path.home() / ".trading-vol-regime" / "logs" / "run.log"


def build_plist(python: str, db: str | None = None) -> dict:
    env = {"PYTHONUNBUFFERED": "1"}
    if db:
        env["TRADING_VOL_REGIME_DB"] = db
    log = str(log_path())
    return {
        "Label": LABEL,
        "ProgramArguments": [python, "-m", "trading_vol_regime.cli", "run"],
        "EnvironmentVariables": env,
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 60,  # back off crash loops (e.g. offline at boot)
        "ProcessType": "Background",
        "StandardOutPath": log,
        "StandardErrorPath": log,
    }


def _domain() -> str:
    return f"gui/{os.getuid()}"


def runtime_dir() -> Path:
    return Path.home() / ".trading-vol-regime" / "runtime"


# macOS privacy protection (TCC) blocks launchd agents from reading these folders without
# an interactive grant, so the agent's Python environment must live elsewhere.
_PROTECTED_DIRS = ("Documents", "Desktop", "Downloads")


def _source_checkout() -> Path | None:
    # services/trading-vol-regime/src/trading_vol_regime/schedule.py -> repo root
    parents = Path(__file__).resolve().parents
    root = parents[4] if len(parents) > 4 else None
    if root is not None and (root / "libs" / "signal-platform").is_dir():
        return root
    return None


def check_installed_env(prefix: Path, home: Path) -> None:
    """Raise if an installed (non-checkout) environment can't host a long-lived agent."""
    if any(part.startswith("archive-v") for part in prefix.parts):
        raise RuntimeError(
            "this looks like a temporary uvx environment, which uv may delete. "
            "Install persistently first: `uv tool install trading-vol-regime`, "
            "then run `trading-vol-regime schedule install`."
        )
    try:
        top = prefix.relative_to(home).parts[0]
    except (ValueError, IndexError):
        return
    if top in _PROTECTED_DIRS:
        raise RuntimeError(
            f"this environment is under ~/{top}, which macOS blocks background jobs from "
            "reading. Install with `uv tool install trading-vol-regime` (or into a venv "
            "outside ~/Documents, ~/Desktop, ~/Downloads) and run schedule install from there."
        )


def runtime_python() -> Path:
    """The interpreter the launchd agent will run.

    From a source checkout: a copied runtime in ~/.trading-vol-regime/runtime, since a
    checkout usually lives in ~/Documents. From a PyPI install: the installed environment
    itself, after checking that launchd can read it.
    """
    root = _source_checkout()
    if root is not None:
        return build_runtime(root)
    check_installed_env(Path(sys.prefix), Path.home())
    return Path(sys.executable)


def build_runtime(root: Path) -> Path:
    """Install a non-editable copy of the checkout into ~/.trading-vol-regime/runtime.

    A copied runtime avoids granting the Python binary access to a whole protected
    folder. Re-run `schedule install` after code changes.
    """
    uv = os.environ.get("UV") or shutil.which("uv")
    if not uv:
        raise RuntimeError("uv not found; install uv or run via `uv run trading-vol-regime ...`")
    runtime = runtime_dir()
    if runtime.exists():
        shutil.rmtree(runtime)
    runtime.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([uv, "venv", "--quiet", "--python", "3.12", str(runtime)], check=True)
    python = runtime / "bin" / "python"
    subprocess.run(
        [
            uv,
            "pip",
            "install",
            "--quiet",
            "--python",
            str(python),
            str(root / "libs" / "signal-platform"),
            str(root / "services" / "trading-vol-regime"),
        ],
        check=True,
    )
    return python


def install(db: str | None = None) -> Path:
    path = plist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    log_path().parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        uninstall()
    python = runtime_python()
    with path.open("wb") as fh:
        plistlib.dump(build_plist(str(python), db or os.environ.get("TRADING_VOL_REGIME_DB")), fh)
    subprocess.run(["launchctl", "bootstrap", _domain(), str(path)], check=True)
    return path


def _loaded() -> bool:
    proc = subprocess.run(
        ["launchctl", "print", f"{_domain()}/{LABEL}"], check=False, capture_output=True
    )
    return proc.returncode == 0


def uninstall() -> bool:
    path = plist_path()
    subprocess.run(
        ["launchctl", "bootout", f"{_domain()}/{LABEL}"],
        check=False,
        capture_output=True,
    )
    # bootout can return before the job is gone; with KeepAlive, launchd may briefly
    # relaunch it, which would overlap with a reinstalled agent and double-send alerts.
    deadline = time.monotonic() + 15
    while _loaded() and time.monotonic() < deadline:
        time.sleep(0.5)
    if path.exists():
        path.unlink()
        return True
    return False


def status() -> str:
    if not plist_path().exists():
        return "not installed"
    proc = subprocess.run(
        ["launchctl", "print", f"{_domain()}/{LABEL}"],
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return "installed but not loaded"
    fields = {}
    for line in proc.stdout.splitlines():
        key, sep, value = line.strip().partition(" = ")
        if sep and key in ("state", "pid", "last exit code", "runs"):
            fields.setdefault(key, value)
    return ", ".join(f"{k}={v}" for k, v in fields.items()) or "loaded"
