#!/usr/bin/env python3
"""Regression for initial cached hydration with the production adapter contract."""
import os
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
temporary = tempfile.TemporaryDirectory()
os.environ.pop("DATABASE_URL", None)
os.environ["TRADING_COCKPIT_DB_PATH"] = str(Path(temporary.name) / "cache_hotfix.sqlite")

from adapters import ExecutionAdapter  # noqa: E402
from cockpit_cache import load_portfolio_state  # noqa: E402


def run():
    execution = ExecutionAdapter()
    load_portfolio_state.clear()
    state = load_portfolio_state(execution)
    assert state["positions"] == []
    assert state["summary"]["configured_portfolio_capital_inr"] == 1_000_000.0
    assert state["summary"]["current_cash_inr"] == 1_000_000.0
    source = (ROOT / "cockpit_cache.py").read_text()
    assert "_execution.get_open_positions()" in source
    assert "_execution.get_portfolio_summary()" in source
    assert "_execution.get_portfolio_state()" not in source
    print("Portfolio cache hotfix regression: PASS (1/1 scenario)")


if __name__ == "__main__":
    try:
        run()
    finally:
        temporary.cleanup()
