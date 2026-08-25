#!/usr/bin/env python3
"""Focused global positive close-to-close qualification gate regressions."""

import os
from pathlib import Path
import tempfile

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
temporary = tempfile.TemporaryDirectory()
os.environ.pop("DATABASE_URL", None)
os.environ["TRADING_COCKPIT_DB_PATH"] = str(Path(temporary.name) / "positive-gate.sqlite")

import database  # noqa: E402
import latent_state_vector as lsv  # noqa: E402
from adapters import ExecutionAdapter, PortfolioAllocationEngine  # noqa: E402
from qualification_contract import (  # noqa: E402
    filter_current_qualified_decisions, positive_price_change_evidence,
)
from screener import evaluate_swing_criteria  # noqa: E402
from streamlit.testing.v1 import AppTest  # noqa: E402


STRATEGIES = [
    "Donchian Channel Breakout", "EMA Pullback / Bounce", "RS Momentum Breakout",
    "VCP Volatility Contraction Breakout", "True NR7 Volatility Expansion Breakout",
    "True Connors RSI Mean Reversion",
]


def row(symbol, strategy, close, previous):
    return {
        "Symbol": symbol, "Setup_Type": strategy, "Data_As_Of": "2026-08-25",
        "Close": close, "Previous_Close": previous, "EMA_20": close - 10,
        "EMA_50": close - 15, "EMA_200": close - 25, "ATR_20": 2.0,
        "RS_Score": 8.0, "Volume_Ratio_20": 2.5, "Current_Volume": 2_500,
        "Volume_20D_Avg": 1_000, "Volume_Confirmed": True, "Price_Confirmed": True,
    }


def evaluation_row(close, previous):
    return pd.Series({
        "Close": close, "Previous_Close": previous, "EMA_20": 90.0, "EMA_50": 80.0,
        "EMA_200": 70.0, "RSI_14": 65.0, "ATR_20": 2.0, "ATR_60": 2.0,
        "Turnover_20D_Avg": 30_000_000.0, "VCP_Ratio": 1.0,
        "Volume_DryUp_Ratio": 0.8, "Volume_DryUp": True, "VCP_Active": True,
        "EMA20_Bounce": False, "EMA50_Bounce": False, "Donchian_20_Breakout": True,
        "Donchian_50_Breakout": False, "Donchian_High_20": 100.0,
        "Donchian_High_50": 100.0, "RS_1M": 5.0, "RS_3M": 5.0,
        "RS_6M": 5.0, "RS_Score": 5.0, "Volume": 2_500.0,
        "Volume_20D_Avg": 1_000.0,
    })


def run():
    allocator = PortfolioAllocationEngine(max_positions=20, max_trend=20, max_vol=20)
    positive = allocator.allocate_candidates(pd.DataFrame([row("POSITIVE", STRATEGIES[0], 101.0, 100.0)]), {"regime": "BULLISH"}, [])[0]
    evaluated_positive = evaluate_swing_criteria(evaluation_row(101.0, 100.0))
    assert positive["is_qualified"] is True
    assert evaluated_positive["Passed"] is True
    assert positive["positive_price_change_gate_pass"] is True
    assert positive["daily_close_to_close_return_pct"] == 1.0
    passed = 1

    zero = allocator.allocate_candidates(pd.DataFrame([row("ZERO", STRATEGIES[0], 100.0, 100.0)]), {"regime": "BULLISH"}, [])[0]
    assert zero["is_qualified"] is False and zero["status"] == "REJECTED — NON_POSITIVE_DAILY_CHANGE"
    assert evaluate_swing_criteria(evaluation_row(100.0, 100.0))["Passed"] is False
    assert positive_price_change_evidence(100, 100)["positive_price_change_gate_pass"] is False
    passed += 1

    negative = allocator.allocate_candidates(pd.DataFrame([row("NEGATIVE", STRATEGIES[0], 99.0, 100.0)]), {"regime": "BULLISH"}, [])[0]
    assert negative["is_qualified"] is False and negative["daily_close_to_close_return_pct"] == -1.0
    assert evaluate_swing_criteria(evaluation_row(99.0, 100.0))["Passed"] is False
    passed += 1

    all_strategy_rows = [row(f"S{index}", strategy, 101.0, 100.0) for index, strategy in enumerate(STRATEGIES)]
    all_strategy_rows += [row(f"F{index}", strategy, 99.0, 100.0) for index, strategy in enumerate(STRATEGIES)]
    strategy_results = allocator.allocate_candidates(pd.DataFrame(all_strategy_rows), {"regime": "BULLISH"}, [])
    assert {item["strategy"] for item in strategy_results if item["is_qualified"]} == set(STRATEGIES)
    assert all(not item["is_qualified"] for item in strategy_results if item["symbol"].startswith("F"))
    passed += 1

    app = AppTest.from_file(str(ROOT / "app.py")).run(timeout=30)
    app.session_state["live_decisions"] = [positive, negative]
    app.session_state["qualified_candidates"] = [positive, negative]
    app.run(timeout=30)
    dashboard_symbols = set().union(*(set(frame.value.get("Symbol", [])) for frame in app.dataframe))
    assert "POSITIVE" in dashboard_symbols and "NEGATIVE" not in dashboard_symbols
    app.radio[0].set_value("Opportunities").run(timeout=30)
    opportunity_symbols = set().union(*(set(frame.value.get("Symbol", [])) for frame in app.dataframe))
    assert "POSITIVE" in opportunity_symbols and "NEGATIVE" not in opportunity_symbols
    passed += 1

    vector = lsv.empty_vector("2026-01-02")
    database.persist_recommendation_snapshot({
        "opportunity_id": "HISTORICAL:IMMUTABLE", "signal_date": "2026-01-02",
        "signal_timestamp": "2026-01-02T11:00:00+00:00", "symbol": "HISTORICAL",
        "strategy": "VCP", "reference_price": 100.0, "lsv_v1": vector,
        "historical_analog": {}, "market_context": {}, "methodologies": {},
        "source_timestamps": {}, "missingness": vector["missingness"], "provenance": [],
    })
    before = database.load_recommendation_snapshot("HISTORICAL:IMMUTABLE", lsv.LSV_METHOD_HASH)
    assert filter_current_qualified_decisions([positive, negative]) == [positive]
    after = database.load_recommendation_snapshot("HISTORICAL:IMMUTABLE", lsv.LSV_METHOD_HASH)
    assert before == after
    passed += 1

    execution = ExecutionAdapter()
    assert execution.preview_paper_trade(positive, 1_000.0)["valid"] is True
    assert execution.preview_paper_trade(negative, 1_000.0)["valid"] is False
    assert len(database.get_open_trades()) == 0
    passed += 1

    assert passed == 7
    print(f"Positive price-change qualification tests: PASS ({passed}/7 scenarios)")


if __name__ == "__main__":
    try:
        run()
    finally:
        temporary.cleanup()
