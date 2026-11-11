"""
actions_state.py — Move pipeline state in and out of a GitHub Actions run
─────────────────────────────────────────────────────────────────────────
Runners keep nothing between runs and this code repo is public, so the
database, designs, spend ledger and run logs live in a private state repo
that .github/workflows/commerce.yml checks out next to the code.

Run:
    python scripts/actions_state.py restore STATE_DIR   — copy state into the working tree
    python scripts/actions_state.py save STATE_DIR      — copy it back after the run
    python scripts/actions_state.py write-env           — write .env from environment variables
    python scripts/actions_state.py env-get KEY         — print one value from .env

Only the folders in STATE_PATHS move. .env never goes to the state repo:
secrets come from repo secrets on every run.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

from dotenv import dotenv_values

_ROOT = Path(__file__).resolve().parent.parent

# Folders that hold pipeline state between runs
STATE_PATHS = ("outputs", "02_Products", "03_Canva_Exports", "04_Assets", "logs")

# .env keys taken from the environment when set (repo secrets and variables)
ENV_KEYS = (
    "ANTHROPIC_API_KEY", "SERPER_API_KEY", "STRIPE_SECRET_KEY",
    "CANVA_CLIENT_ID", "CANVA_CLIENT_SECRET", "CANVA_REFRESH_TOKEN",
    "STOREFRONT_BASE_URL", "STOREFRONT_GIT_REMOTE", "STOREFRONT_GIT_BRANCH",
    "STOREFRONT_TAGLINE", "SUPPORT_EMAIL", "BRAND_NAME",
    "ATLAS_FEED_URL", "ATLAS_FEED_TOKEN",
    "MAX_ANTHROPIC_USD_PER_DAY", "MAX_ANTHROPIC_USD_PER_MONTH",
    "WEEKLY_PUBLISH_LIMIT", "PUBLISH_COOLDOWN_HOURS",
)


def _copy_tree(src: Path, dst: Path) -> None:
    if not src.is_dir():
        return
    shutil.copytree(src, dst, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns(".gitkeep", "*.lock"))


def restore(state_dir: Path, root: Path = _ROOT) -> None:
    for name in STATE_PATHS:
        _copy_tree(state_dir / name, root / name)
    print(f"[state] restored from {state_dir.name}/")


def save(state_dir: Path, root: Path = _ROOT) -> None:
    for name in STATE_PATHS:
        _copy_tree(root / name, state_dir / name)
    print(f"[state] saved to {state_dir.name}/")


def write_env(environ: dict[str, str] | None = None, root: Path = _ROOT) -> int:
    environ = os.environ if environ is None else environ
    lines = []
    for key in ENV_KEYS:
        value = (environ.get(key) or "").strip()
        if not value:
            continue
        if "\n" in value:
            raise ValueError(f"{key} must be one line")
        lines.append(f"{key}={value}\n")
    path = root / ".env"
    path.write_text("".join(lines), encoding="utf-8")
    path.chmod(0o600)
    print(f"[state] .env written with {len(lines)} settings")
    return len(lines)


def env_get(key: str, root: Path = _ROOT) -> str:
    return dotenv_values(root / ".env").get(key) or ""


def main(argv: list[str]) -> int:
    if len(argv) >= 2 and argv[0] in ("restore", "save"):
        state_dir = Path(argv[1]).resolve()
        if not state_dir.is_dir():
            print(f"[state] {argv[1]} is not a folder", file=sys.stderr)
            return 2
        (restore if argv[0] == "restore" else save)(state_dir)
        return 0
    if argv[:1] == ["write-env"]:
        write_env()
        return 0
    if len(argv) == 2 and argv[0] == "env-get":
        print(env_get(argv[1]))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
