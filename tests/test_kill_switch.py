from pathlib import Path
import subprocess
import sys

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import kill_switch


def test_off_by_default(tmp_path: Path) -> None:
    assert kill_switch.reason(tmp_path, tmp_path / "disabled", {}) is None


def test_kill_file_turns_it_on(tmp_path: Path) -> None:
    (tmp_path / "KILL").touch()
    assert "KILL" in kill_switch.reason(tmp_path, tmp_path / "disabled", {})


def test_root_owned_system_file_wins(tmp_path: Path) -> None:
    system = tmp_path / "disabled"
    system.touch()
    assert str(system) in kill_switch.reason(tmp_path, system, {})


def test_enabled_flag_from_environment_or_dotenv(tmp_path: Path) -> None:
    for value in ("false", "0", "No", "OFF"):
        assert kill_switch.reason(tmp_path, tmp_path / "d", {"COMMERCE_LOOP_ENABLED": value})
    assert kill_switch.reason(tmp_path, tmp_path / "d", {"COMMERCE_LOOP_ENABLED": "true"}) is None
    (tmp_path / ".env").write_text("COMMERCE_LOOP_ENABLED=false\n")
    assert kill_switch.reason(tmp_path, tmp_path / "d", {})


def test_scoped_env_refuses_to_start_scripts_while_killed(tmp_path: Path) -> None:
    system = tmp_path / "disabled"
    system.touch()
    env = {"PATH": "/usr/bin:/bin", "COMMERCE_KILL_SYSTEM_FILE": str(system)}
    result = subprocess.run([sys.executable, str(SCRIPTS_DIR / "scoped_env.py"), "scripts/order_delivery.py"],
                            env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 78
    assert "Kill switch is on" in result.stderr


def test_diagnostics_still_run_while_killed() -> None:
    assert {"preflight.py", "spend_cap.py", "kill_switch.py"} <= kill_switch.ALLOWED_WHILE_KILLED
    assert "order_delivery.py" not in kill_switch.ALLOWED_WHILE_KILLED
    assert "stripe_publisher.py" not in kill_switch.ALLOWED_WHILE_KILLED
