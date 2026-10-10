"""Local scheduling via a macOS launchd user agent.

The agent keeps `vix-regime run` alive: launchd restarts it on crash and starts it at
login. The loop itself owns cadence (15-min market hours, 30-min off hours, nightly
history refresh), so launchd only has to keep one process running.

The hosted equivalent is EventBridge → Lambda (docs/00-platform-architecture.md).
"""

from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
import time
from pathlib import Path

LABEL = "com.signalplatform.vix-regime"


def plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def log_path() -> Path:
    return Path.home() / ".vix-regime" / "logs" / "run.log"


def build_plist(python: str, db: str | None = None) -> dict:
    env = {"PYTHONUNBUFFERED": "1"}
    if db:
        env["VIX_REGIME_DB"] = db
    log = str(log_path())
    return {
        "Label": LABEL,
        "ProgramArguments": [python, "-m", "vix_regime.cli", "run"],
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
    return Path.home() / ".vix-regime" / "runtime"


def _repo_root() -> Path:
    # services/vix-regime/src/vix_regime/schedule.py -> repo root
    root = Path(__file__).resolve().parents[4]
    if not (root / "libs" / "signal-platform").is_dir():
        raise RuntimeError(
            f"schedule install must run from a source checkout (looked for libs/ under {root})"
        )
    return root


def build_runtime() -> Path:
    """Install a non-editable copy of the service into ~/.vix-regime/runtime.

    macOS privacy protection (TCC) blocks launchd agents from reading ~/Documents,
    ~/Desktop, and ~/Downloads without an interactive grant, so the agent cannot run from
    a checkout or editable venv in those folders. A copied runtime avoids granting the
    Python binary access to the whole folder. Re-run `schedule install` after code changes.
    """
    uv = os.environ.get("UV") or shutil.which("uv")
    if not uv:
        raise RuntimeError("uv not found; install uv or run via `uv run vix-regime ...`")
    root = _repo_root()
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
            str(root / "services" / "vix-regime"),
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
    python = build_runtime()
    with path.open("wb") as fh:
        plistlib.dump(build_plist(str(python), db or os.environ.get("VIX_REGIME_DB")), fh)
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
