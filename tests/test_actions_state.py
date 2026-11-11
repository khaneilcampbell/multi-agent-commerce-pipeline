"""Tests for scripts/actions_state.py (state in and out of a GitHub Actions run)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import actions_state


def test_restore_then_save_round_trips_state(tmp_path: Path) -> None:
    state = tmp_path / "state"
    root = tmp_path / "work"
    (state / "outputs").mkdir(parents=True)
    (state / "outputs" / "master.txt").write_text("1. Planner ($9)\n")
    (state / "logs").mkdir()
    (state / "logs" / "anthropic_spend.json").write_text("{}")
    root.mkdir()

    actions_state.restore(state, root)
    assert (root / "outputs" / "master.txt").read_text() == "1. Planner ($9)\n"

    (root / "outputs" / "pipeline.db").write_bytes(b"db")
    (root / "outputs" / "x.lock").write_text("")
    actions_state.save(state, root)
    assert (state / "outputs" / "pipeline.db").read_bytes() == b"db"
    assert not (state / "outputs" / "x.lock").exists()


def test_env_is_never_saved(tmp_path: Path) -> None:
    state, root = tmp_path / "state", tmp_path / "work"
    state.mkdir()
    root.mkdir()
    (root / ".env").write_text("STRIPE_SECRET_KEY=sk_test_x\n")
    actions_state.save(state, root)
    assert not any(p.name == ".env" for p in state.rglob("*"))


def test_write_env_keeps_only_known_set_keys(tmp_path: Path) -> None:
    count = actions_state.write_env(
        {"STRIPE_SECRET_KEY": "sk_test_x", "SERPER_API_KEY": "", "HOME": "/root"}, tmp_path)
    assert count == 1
    assert (tmp_path / ".env").read_text() == "STRIPE_SECRET_KEY=sk_test_x\n"
    assert actions_state.env_get("STRIPE_SECRET_KEY", tmp_path) == "sk_test_x"
    assert actions_state.env_get("MISSING", tmp_path) == ""


def test_write_env_rejects_multiline_values(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        actions_state.write_env({"BRAND_NAME": "a\nANTHROPIC_API_KEY=x"}, tmp_path)


def test_cycle_does_nothing_while_killed() -> None:
    import os
    import subprocess
    root = Path(__file__).resolve().parent.parent
    env = dict(os.environ, COMMERCE_LOOP_ENABLED="false", PYTHON=sys.executable)
    res = subprocess.run([str(root / "cycle.sh")], cwd=root, env=env,
                         capture_output=True, text=True, timeout=60, check=False)
    assert res.returncode == 0
    assert "Kill switch on" in res.stdout
