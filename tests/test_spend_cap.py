from datetime import UTC, datetime
from pathlib import Path
import sys

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import spend_cap

DAY1 = datetime(2026, 11, 10, 9, 0, tzinfo=UTC)
DAY2 = datetime(2026, 11, 11, 0, 1, tzinfo=UTC)
NEXT_MONTH = datetime(2026, 12, 1, 0, 1, tzinfo=UTC)


def test_prices_match_by_prefix_and_unknown_models_pay_the_top_rate() -> None:
    assert spend_cap.price_for("claude-haiku-4-5") == (1.00, 5.00)
    assert spend_cap.price_for("claude-haiku-4-5-20251001") == (1.00, 5.00)
    assert spend_cap.price_for("anthropic/claude-opus-4-5") == (5.00, 25.00)
    assert spend_cap.price_for("claude-opus-4-1-20250805") == (15.00, 75.00)
    assert spend_cap.price_for("some-new-model") == spend_cap.FALLBACK_PRICE


def test_cost_reads_http_sdk_and_crewai_usage_shapes() -> None:
    # 1M input + 1M output on Opus 4.5 = $5 + $25
    assert spend_cap.cost_usd("claude-opus-4-5", {"input_tokens": 1_000_000, "output_tokens": 1_000_000}) == pytest.approx(30.0)

    class SdkUsage:
        input_tokens = 1_000_000
        output_tokens = 0
        cache_creation_input_tokens = 1_000_000
        cache_read_input_tokens = 1_000_000

    # Haiku: 1.00 + 1.25 (cache write) + 0.10 (cache read)
    assert spend_cap.cost_usd("claude-haiku-4-5", SdkUsage()) == pytest.approx(2.35)

    class CrewUsage:
        prompt_tokens = 1_000_000
        completion_tokens = 200_000

    assert spend_cap.cost_usd("claude-haiku-4-5", CrewUsage()) == pytest.approx(2.0)


def test_daily_cap_blocks_the_next_call_and_resets_next_day(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MAX_ANTHROPIC_USD_PER_DAY", "1")
    monkeypatch.setenv("MAX_ANTHROPIC_USD_PER_MONTH", "100")
    ledger = tmp_path / "spend.json"
    spend_cap.require(DAY1, ledger)
    spend_cap.record("claude-opus-4-5", {"input_tokens": 0, "output_tokens": 40_000}, now=DAY1, ledger=ledger)  # $1.00
    with pytest.raises(spend_cap.SpendCapReached, match="daily"):
        spend_cap.require(DAY1, ledger)
    spend_cap.require(DAY2, ledger)
    assert spend_cap.spent(DAY2, ledger) == (0.0, pytest.approx(1.0))


def test_monthly_cap_holds_across_days_and_resets_next_month(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MAX_ANTHROPIC_USD_PER_DAY", "10")
    monkeypatch.setenv("MAX_ANTHROPIC_USD_PER_MONTH", "1.5")
    ledger = tmp_path / "spend.json"
    spend_cap.record("claude-opus-4-5", {"output_tokens": 40_000}, now=DAY1, ledger=ledger)  # $1
    spend_cap.record("claude-opus-4-5", {"output_tokens": 40_000}, now=DAY2, ledger=ledger)  # $1
    with pytest.raises(spend_cap.SpendCapReached, match="monthly"):
        spend_cap.require(DAY2, ledger)
    spend_cap.require(NEXT_MONTH, ledger)


def test_spend_cap_is_a_runtime_error_so_phase_scripts_fail_cleanly() -> None:
    assert issubclass(spend_cap.SpendCapReached, RuntimeError)


def test_bad_limits_fall_back_to_defaults_and_none_usage_costs_nothing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MAX_ANTHROPIC_USD_PER_DAY", "lots")
    monkeypatch.setenv("MAX_ANTHROPIC_USD_PER_MONTH", "-3")
    assert spend_cap.daily_limit() == spend_cap.DAY_DEFAULT
    assert spend_cap.monthly_limit() == spend_cap.MONTH_DEFAULT
    assert spend_cap.record("claude-haiku-4-5", None, now=DAY1, ledger=tmp_path / "s.json") == 0.0


def test_corrupt_ledger_starts_fresh(tmp_path: Path) -> None:
    ledger = tmp_path / "spend.json"
    ledger.write_text("{not json")
    assert spend_cap.spent(DAY1, ledger) == (0.0, 0.0)
