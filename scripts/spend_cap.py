"""
spend_cap.py — Dollar cap on Anthropic API spend
────────────────────────────────────────────────
daily_caps.py counts how many model scripts start. This module counts what
they cost. Every Anthropic call records its token usage here, priced from
MODEL_PRICES, and no new call starts once a cap is spent:

    MAX_ANTHROPIC_USD_PER_DAY    (default 2.00)   resets at midnight UTC
    MAX_ANTHROPIC_USD_PER_MONTH  (default 15.00)  resets on the 1st, UTC

A call is checked before it starts, so the most a cap can be overshot by is
one call. The provider's own workspace spend limit is the hard backstop: a
process that can write logs/ can also edit this ledger, so keep the console
limit set as well (README "Spend cap and kill switch").

Ledger: logs/anthropic_spend.json, guarded by a file lock like daily_caps.

Check from the shell:
    python scripts/spend_cap.py status       # prints today and this month
    python scripts/spend_cap.py check        # exit 0 if under both caps, 75 if spent
"""

from __future__ import annotations

import fcntl
import json
import os
import sys
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

_ROOT = Path(__file__).resolve().parent.parent
LEDGER_FILE = _ROOT / "logs" / "anthropic_spend.json"

DAY_VAR, DAY_DEFAULT = "MAX_ANTHROPIC_USD_PER_DAY", 2.00
MONTH_VAR, MONTH_DEFAULT = "MAX_ANTHROPIC_USD_PER_MONTH", 15.00

# US dollars per million tokens: (input, output). Matched by model-id prefix.
# Cache writes bill at 1.25x input and cache reads at 0.1x input.
MODEL_PRICES: dict[str, tuple[float, float]] = {
    "claude-opus-4-5":   (5.00, 25.00),
    "claude-opus-4-1":   (15.00, 75.00),
    "claude-opus-4":     (15.00, 75.00),
    "claude-sonnet-4":   (3.00, 15.00),
    "claude-3-7-sonnet": (3.00, 15.00),
    "claude-haiku-4-5":  (1.00, 5.00),
    "claude-3-5-haiku":  (0.80, 4.00),
}
# A model not listed above is charged at the most expensive rate, so a new
# model id can only make the cap stricter, never looser.
FALLBACK_PRICE = max(MODEL_PRICES.values())

EX_TEMPFAIL = 75


class SpendCapReached(RuntimeError):
    """Raised before a call when today's or this month's Anthropic budget is spent."""


def _limit(var: str, default: float) -> float:
    # Environment first, then .env (read, not loaded, so no secret enters os.environ)
    raw = (os.getenv(var) or dotenv_values(_ROOT / ".env").get(var) or "").strip()
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value >= 0 else default


def daily_limit() -> float:
    return _limit(DAY_VAR, DAY_DEFAULT)


def monthly_limit() -> float:
    return _limit(MONTH_VAR, MONTH_DEFAULT)


def price_for(model: str) -> tuple[float, float]:
    name = model.split("/")[-1]  # CrewAI/LiteLLM ids look like "anthropic/claude-..."
    for prefix in sorted(MODEL_PRICES, key=len, reverse=True):
        if name.startswith(prefix):
            return MODEL_PRICES[prefix]
    return FALLBACK_PRICE


def _tokens(usage: Mapping[str, Any] | Any, *names: str) -> int:
    for name in names:
        value = usage.get(name) if isinstance(usage, Mapping) else getattr(usage, name, None)
        if isinstance(value, (int, float)) and value > 0:
            return int(value)
    return 0


def cost_usd(model: str, usage: Mapping[str, Any] | Any) -> float:
    """Price one response's `usage` (a dict from the HTTP API or an SDK/CrewAI usage object)."""
    price_in, price_out = price_for(model)
    tokens_in = _tokens(usage, "input_tokens", "prompt_tokens")
    tokens_out = _tokens(usage, "output_tokens", "completion_tokens")
    cache_write = _tokens(usage, "cache_creation_input_tokens")
    cache_read = _tokens(usage, "cache_read_input_tokens", "cached_prompt_tokens")
    return (tokens_in * price_in
            + cache_write * price_in * 1.25
            + cache_read * price_in * 0.10
            + tokens_out * price_out) / 1_000_000


def _locked_ledger(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(path, "a+", encoding="utf-8")
    fcntl.flock(fh, fcntl.LOCK_EX)
    fh.seek(0)
    try:
        state = json.loads(fh.read() or "{}")
    except json.JSONDecodeError:
        state = {}
    return fh, state if isinstance(state, dict) else {}


def _current(state: dict[str, Any], now: datetime) -> dict[str, Any]:
    day, month = now.date().isoformat(), now.strftime("%Y-%m")
    if state.get("month") != month:
        state = {"month": month, "month_usd": 0.0}
    if state.get("day") != day:
        state["day"], state["day_usd"] = day, 0.0
    state.setdefault("month_usd", 0.0)
    return state


def spent(now: datetime | None = None, ledger: Path | None = None) -> tuple[float, float]:
    """(today_usd, this_month_usd)."""
    fh, state = _locked_ledger(ledger or LEDGER_FILE)
    with fh:
        state = _current(state, now or datetime.now(UTC))
    return float(state["day_usd"]), float(state["month_usd"])


def remaining(now: datetime | None = None, ledger: Path | None = None) -> float:
    today, month = spent(now, ledger)
    return max(0.0, min(daily_limit() - today, monthly_limit() - month))


def require(now: datetime | None = None, ledger: Path | None = None) -> None:
    """Call before each Anthropic request. Raises SpendCapReached once a cap is spent."""
    today, month = spent(now, ledger)
    if today >= daily_limit():
        raise SpendCapReached(
            f"Anthropic daily spend cap reached (${today:.2f} of ${daily_limit():.2f}; "
            f"set {DAY_VAR} to change). Resets at midnight UTC.")
    if month >= monthly_limit():
        raise SpendCapReached(
            f"Anthropic monthly spend cap reached (${month:.2f} of ${monthly_limit():.2f}; "
            f"set {MONTH_VAR} to change). Resets on the 1st, UTC.")


def record(model: str, usage: Mapping[str, Any] | Any, *,
           now: datetime | None = None, ledger: Path | None = None) -> float:
    """Add one response's cost to today's and this month's totals. Returns the cost."""
    if usage is None:
        return 0.0
    cost = cost_usd(model, usage)
    fh, state = _locked_ledger(ledger or LEDGER_FILE)
    with fh:
        state = _current(state, now or datetime.now(UTC))
        state["day_usd"] = round(float(state["day_usd"]) + cost, 6)
        state["month_usd"] = round(float(state["month_usd"]) + cost, 6)
        fh.seek(0)
        fh.truncate()
        fh.write(json.dumps(state))
    return cost


def main(argv: list[str]) -> int:
    command = argv[0] if argv else "status"
    today, month = spent()
    print(f"[spend_cap] Anthropic today ${today:.2f} of ${daily_limit():.2f}, "
          f"this month ${month:.2f} of ${monthly_limit():.2f}")
    if command == "check":
        try:
            require()
        except SpendCapReached as exc:
            print(f"[spend_cap] {exc}", file=sys.stderr)
            return EX_TEMPFAIL
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
