#!/usr/bin/env python3
"""Regression: an allocator regime constraint must not erase qualified names."""

import os
from pathlib import Path
import tempfile

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
temporary = tempfile.TemporaryDirectory()
os.environ.pop("DATABASE_URL", None)
os.environ["TRADING_COCKPIT_DB_PATH"] = str(Path(temporary.name) / "regime-visibility.sqlite")

from adapters import PortfolioAllocationEngine  # noqa: E402
from live_decision_adapter import assemble_live_decisions  # noqa: E402
from qualification_contract import filter_current_qualified_decisions  # noqa: E402
from streamlit.testing.v1 import AppTest  # noqa: E402


def qualified_row():
    return {
        "Symbol": "VISIBLE",
        "Setup_Type": "Donchian Channel Breakout",
        "Data_As_Of": "2026-09-11",
        "Close": 101.0,
        "Previous_Close": 100.0,
        "EMA_20": 90.0,
        "EMA_50": 85.0,
        "EMA_200": 75.0,
        "ATR_20": 2.0,
        "RS_Score": 8.0,
        "Volume_Ratio_20": 2.5,
        "Current_Volume": 2_500.0,
        "Volume_20D_Avg": 1_000.0,
        "Volume_Confirmed": True,
        "Price_Confirmed": True,
    }


def rendered_symbols(app):
    return set().union(*(set(frame.value.get("Symbol", [])) for frame in app.dataframe))


def run():
    allocator = PortfolioAllocationEngine(max_positions=10, max_trend=7, max_vol=3)
    candidate = allocator.allocate_candidates(
        pd.DataFrame([qualified_row()]), {"regime": "BEARISH"}, []
    )[0]
    assert candidate["is_qualified"] is True
    assert candidate["status"] == "REJECTED — REGIME"

    decisions = assemble_live_decisions([candidate])
    assert len(decisions) == 1
    assert decisions[0]["qualification_status"] == "QUALIFIED"
    assert decisions[0]["allocation_status"] == "QUALIFIED_NOT_ALLOCATED_OTHER_FROZEN_CONSTRAINT"
    assert filter_current_qualified_decisions(decisions)[0]["symbol"] == "VISIBLE"

    app = AppTest.from_file(str(ROOT / "app.py")).run(timeout=30)
    app.session_state["live_decisions"] = decisions
    app.session_state["qualified_candidates"] = decisions
    app.run(timeout=30)
    assert "VISIBLE" in rendered_symbols(app)

    app.radio[0].set_value("Opportunities").run(timeout=30)
    assert "VISIBLE" in rendered_symbols(app)
    print("Qualified visibility across regimes: PASS (6/6 assertions)")


if __name__ == "__main__":
    try:
        run()
    finally:
        temporary.cleanup()
