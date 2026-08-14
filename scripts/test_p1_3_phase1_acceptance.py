"""Deterministic Phase 1 integration and acceptance gate; no live providers."""

import datetime as dt
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def run() -> None:
    with tempfile.TemporaryDirectory() as directory:
        database_path = Path(directory) / "phase1_acceptance.sqlite"
        os.environ.pop("DATABASE_URL", None)
        os.environ["TRADING_COCKPIT_DB_PATH"] = str(database_path)

        from adapters import ExecutionAdapter, PortfolioAllocationEngine
        from database import (
            get_open_trades,
            get_portfolio_configuration,
            refresh_open_trade_marks,
        )
        from eod_pipeline import execute_eod_pipeline

        execution = ExecutionAdapter()

        # A. Default configuration, persistence across adapter objects, and distinct no-limit mode.
        assert get_portfolio_configuration() == {"initial_capital": 1_000_000.0, "max_open_positions": 10}
        assert execution.configure_portfolio(500_000.0, 2)["success"] is True
        assert ExecutionAdapter().get_portfolio_summary()["configured_max_open_positions"] == 2
        assert execution.configure_portfolio(500_000.0, None)["success"] is True
        assert ExecutionAdapter().get_portfolio_summary()["configured_max_open_positions"] is None
        assert execution.configure_portfolio(500_000.0, 2)["success"] is True
        assert len(get_open_trades()) == 0

        class MarketFixture:
            def get_index_regime(self, as_of_date=None):
                return {"regime": "BULLISH", "data_as_of": as_of_date, "nifty_dist_ema50": 1.0}

        class UniverseFixture:
            def get_universe(self, date_str=None):
                return ["SELECTED.NS", "UNSELECTED.NS"]

        class SignalsFixture:
            def run_stage1_screening(self, **_kwargs):
                rows = [
                    {
                        "Symbol": "SELECTED.NS", "Setup_Type": "Donchian_Breakout",
                        "Close": 123.0, "EMA_20": 110.0, "EMA_50": 105.0,
                        "ATR_20": 4.0, "RS_Score": 8.0, "Strategy_Rank": 1,
                        "Volume_Ratio_20": 3.0, "Volume_Confirmed": True,
                        "Price_Confirmed": True, "Data_As_Of": "2026-08-14",
                    },
                    {
                        "Symbol": "UNSELECTED.NS", "Setup_Type": "EMA_Bounce",
                        "Close": 77.0, "EMA_20": 70.0, "EMA_50": 68.0,
                        "ATR_20": 3.0, "RS_Score": 6.0, "Strategy_Rank": 2,
                        "Volume_Ratio_20": 2.5, "Volume_Confirmed": True,
                        "Price_Confirmed": True, "Data_As_Of": "2026-08-14",
                    },
                ]
                return pd.DataFrame(rows), {
                    "valid_data_count": 2, "symbols_screened": 2,
                    "unique_signal_candidates": 2, "universe_count": 2,
                }

        class EventFixture:
            def enrich_candidates(self, candidates, cutoff=None):
                return candidates

        allocator = PortfolioAllocationEngine(max_positions=1, max_trend=1, max_vol=0)
        pipeline_result = execute_eod_pipeline(
            analysis_date=dt.date(2026, 8, 14), source="P1_ACCEPTANCE",
            dependencies={
                "market_data": MarketFixture(), "universe": UniverseFixture(),
                "signals": SignalsFixture(), "execution": execution,
                "allocator": allocator, "event_service": EventFixture(),
            },
        )
        assert pipeline_result["persisted"] is True
        assert len(get_open_trades()) == 0  # E. Scanning/EOD cannot create a trade.
        decisions = pipeline_result["decisions"]
        assert len(decisions) == 2 and all(row["qualification_status"] == "QUALIFIED" for row in decisions)
        selected = next(row for row in decisions if row["allocation_status"] == "ALLOCATED")
        unselected = next(row for row in decisions if row["allocation_status"] != "ALLOCATED")

        # B. Both qualified classes are visible to the same ticket contract; allocation is advisory.
        selected_preview = execution.preview_paper_trade(selected, 12_345.0)
        unselected_preview = execution.preview_paper_trade(unselected, 23_456.0)
        assert selected_preview["valid"] is True and selected_preview["allocator_selected"] is True
        assert unselected_preview["valid"] is True and unselected_preview["allocator_selected"] is False
        unqualified = dict(unselected, symbol="UNQUALIFIED", is_qualified=False, qualification_status="REJECTED")
        assert execution.preview_paper_trade(unqualified, 1_000.0)["valid"] is False
        assert len(get_open_trades()) == 0  # Preview/selection/amount changes are read-only.

        # E. A real render and input changes remain read-only.
        from streamlit.testing.v1 import AppTest

        app = AppTest.from_file(str(ROOT / "app.py")).run(timeout=30)
        assert not app.exception
        ticket_select = next(widget for widget in app.selectbox if widget.label == "Qualified opportunity")
        ticket_amount = next(widget for widget in app.number_input if widget.label == "Investment amount (₹)")
        ticket_select.select_index(1).run(timeout=30)
        ticket_amount.set_value(13_579.0).run(timeout=30)
        capital_input = next(widget for widget in app.number_input if widget.label == "Paper portfolio capital (₹)")
        capital_input.set_value(600_000.0).run(timeout=30)
        assert len(get_open_trades()) == 0

        # One confirmed UI submission creates exactly one row; its rerender does not replay it.
        ticket_select = next(widget for widget in app.selectbox if widget.label == "Qualified opportunity")
        ticket_select.select_index(0).run(timeout=30)
        ticket_amount = next(widget for widget in app.number_input if widget.label == "Investment amount (₹)")
        ticket_amount.set_value(12_345.0).run(timeout=30)
        confirmation = next(widget for widget in app.checkbox if widget.label == "I confirm this is a manual paper-trade record")
        confirmation.check().run(timeout=30)
        submit = next(widget for widget in app.button if widget.label == "Record paper trade")
        submit.click().run(timeout=30)
        assert not app.exception and len(get_open_trades()) == 1

        # C. Multiple non-round explicit executions reconcile after every trade.
        first_trade = get_open_trades()[0]
        assert first_trade.symbol == selected["symbol"] and first_trade.quantity == 100
        assert first_trade.position_value == 12_300.0
        first_summary = execution.get_portfolio_summary()
        assert first_summary["open_positions_count"] == 1
        assert first_summary["open_capital_deployed_inr"] == 12_300.0
        assert first_summary["current_cash_inr"] == 487_700.0
        assert first_summary["total_portfolio_value_inr"] == 500_000.0

        second = execution.execute_paper_trade(unselected, 23_456.0)
        assert second["success"] is True and second["quantity"] == 304
        assert second["executed_position_value_inr"] == 23_408.0
        second_summary = execution.get_portfolio_summary()
        assert second_summary["open_positions_count"] == 2
        assert second_summary["open_capital_deployed_inr"] == 35_708.0
        assert second_summary["current_cash_inr"] == 464_292.0
        assert second_summary["total_portfolio_value_inr"] == 500_000.0
        assert second_summary["current_cash_inr"] >= 0

        # D. Cash, sub-share, duplicate, limit, lowering, and no-limit boundaries.
        assert execution.execute_paper_trade(selected, 1_000.0)["success"] is False
        below_share = dict(unselected, symbol="BELOW", entry_price=1_000.0)
        assert execution.execute_paper_trade(below_share, 999.0)["success"] is False
        third = dict(unselected, symbol="THIRD", entry_price=101.0)
        assert execution.execute_paper_trade(third, 10_101.0)["success"] is False
        assert execution.execute_paper_trade(third, 500_000.0)["success"] is False
        assert len(get_open_trades()) == 2

        assert execution.configure_portfolio(500_000.0, 1)["success"] is True
        assert len(get_open_trades()) == 2
        assert execution.execute_paper_trade(third, 10_101.0)["success"] is False
        assert execution.configure_portfolio(500_000.0, None)["success"] is True
        third_result = execution.execute_paper_trade(third, 10_101.0)
        assert third_result["success"] is True and third_result["quantity"] == 100
        assert third_result["executed_position_value_inr"] == 10_100.0
        before_read_paths = len(get_open_trades())
        execution.preview_paper_trade(dict(unselected, symbol="READONLY"), 1_234.0)
        execution.get_open_positions()
        execution.get_portfolio_summary()
        assert len(get_open_trades()) == before_read_paths == 3

        # G. Deterministic marks alter only unrealized P&L/NAV.
        before_rows = {
            trade.symbol: (trade.quantity, trade.entry_price, trade.position_value)
            for trade in get_open_trades()
        }
        cash_before_marks = execution.get_portfolio_summary()["current_cash_inr"]

        def mark_fixture(symbols):
            entries = {trade.symbol: trade.entry_price for trade in get_open_trades()}
            return {
                "marks": {
                    symbol: {"mark_price": entries[symbol] + 10.0, "mark_date": "2026-08-14"}
                    for symbol in symbols
                },
                "failed_symbols": [], "unique_symbols": len(symbols),
                "provider_calls": 1, "elapsed_seconds": 0.0,
            }

        mark_result = refresh_open_trade_marks(source_run_id="P1_ACCEPTANCE_MARK", mark_fetcher=mark_fixture)
        assert mark_result["successful_marks"] == 3 and len(get_open_trades()) == 3
        after_rows = {
            trade.symbol: (trade.quantity, trade.entry_price, trade.position_value)
            for trade in get_open_trades()
        }
        assert after_rows == before_rows
        marked_summary = execution.get_portfolio_summary()
        assert marked_summary["current_cash_inr"] == cash_before_marks == 454_192.0
        assert marked_summary["open_capital_deployed_inr"] == 45_808.0
        assert marked_summary["unrealized_pnl_inr"] == 5_040.0
        assert marked_summary["total_portfolio_value_inr"] == 505_040.0

        # F. A fresh interpreter must recover configuration, ledger, marks, and constraints.
        restart_code = r'''
from adapters import ExecutionAdapter
from database import get_open_trades, get_portfolio_configuration

execution = ExecutionAdapter()
configuration = get_portfolio_configuration()
assert configuration == {"initial_capital": 500_000.0, "max_open_positions": None}
assert len(get_open_trades()) == 3
summary = execution.get_portfolio_summary()
assert summary["current_cash_inr"] == 454_192.0
assert summary["open_capital_deployed_inr"] == 45_808.0
assert summary["unrealized_pnl_inr"] == 5_040.0
assert summary["total_portfolio_value_inr"] == 505_040.0

duplicate = {"symbol":"SELECTED", "strategy":"Donchian Channel Breakout", "entry_price":123.0,
             "is_qualified":True, "qualification_status":"QUALIFIED", "allocation_status":"ALLOCATED"}
assert execution.execute_paper_trade(duplicate, 1_000.0)["success"] is False
assert execution.configure_portfolio(500_000.0, 3)["success"] is True
new_candidate = dict(duplicate, symbol="FOURTH", entry_price=100.0)
assert execution.execute_paper_trade(new_candidate, 1_000.0)["success"] is False
assert len(get_open_trades()) == 3
print("Phase 1 restart reconciliation: PASS")
'''
        environment = dict(os.environ, TRADING_COCKPIT_DB_PATH=str(database_path))
        environment.pop("DATABASE_URL", None)
        restarted = subprocess.run(
            [sys.executable, "-c", restart_code], cwd=ROOT, env=environment,
            capture_output=True, text=True,
        )
        assert restarted.returncode == 0, restarted.stderr or restarted.stdout
        assert "Phase 1 restart reconciliation: PASS" in restarted.stdout

        print("P1.3 Phase 1 acceptance scenarios: 7 PASS, 0 FAIL")


if __name__ == "__main__":
    run()
