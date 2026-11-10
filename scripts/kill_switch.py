"""
kill_switch.py — Stop the whole commerce loop, buyer delivery included
──────────────────────────────────────────────────────────────────────
PAUSE (./run.sh pause) is the soft stop: buyer delivery keeps running. The
kill switch is the hard stop: while it is on, loop.sh exits cleanly at the
next check (systemd does not restart a clean exit) and scoped_env.py refuses
to start any pipeline script, so no model call, publish, deploy or email
starts.

It is on when any of these is true:

    1. /etc/commerce-pipeline/disabled exists. Root-owned, so the service
       user cannot clear it, and the unit's ConditionPathExists keeps
       systemd from starting the service at all.
    2. A KILL file exists in the project folder (./run.sh kill).
    3. COMMERCE_LOOP_ENABLED is false, 0, no or off (environment or .env).

Check from the shell:
    python scripts/kill_switch.py     # exit 0 = clear, exit 1 = on (prints why)

The fastest stop is still `sudo systemctl stop commerce-pipeline`, which ends
a phase that is already running. The switch makes sure nothing starts again.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import dotenv_values

_ROOT = Path(__file__).resolve().parent.parent
PROJECT_FILE = _ROOT / "KILL"
SYSTEM_FILE = Path("/etc/commerce-pipeline/disabled")
ENABLED_VAR = "COMMERCE_LOOP_ENABLED"
_OFF = {"0", "false", "no", "off"}

# Checks that still run with the switch on, so you can diagnose, plus the
# Atlas status feed so the dashboard shows the loop as stopped.
ALLOWED_WHILE_KILLED = frozenset({"preflight.py", "health_check.py", "spend_cap.py",
                                  "kill_switch.py", "atlas_feed.py"})


def reason(root: Path | None = None, system_file: Path | None = None,
           environ: dict[str, str] | None = None) -> str | None:
    """Why the switch is on, or None when it is off."""
    root = root or _ROOT
    system_file = system_file or Path(os.getenv("COMMERCE_KILL_SYSTEM_FILE", str(SYSTEM_FILE)))
    environ = os.environ if environ is None else environ
    if system_file.exists():
        return f"{system_file} exists (remove it as root to re-enable)"
    project_file = root / "KILL"
    if project_file.exists():
        return f"{project_file} exists (./run.sh unkill to re-enable)"
    raw = environ.get(ENABLED_VAR) or dotenv_values(root / ".env").get(ENABLED_VAR) or ""
    if raw.strip().lower() in _OFF:
        return f"{ENABLED_VAR}={raw.strip()} (set it to true in .env to re-enable)"
    return None


def main() -> int:
    why = reason()
    if why:
        print(f"[kill_switch] ON: {why}")
        return 1
    print("[kill_switch] off")
    return 0


if __name__ == "__main__":
    sys.exit(main())
