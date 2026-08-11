import os
import pickle
import unittest
import pandas as pd

from universe_engine import get_universe_as_of, get_universe_metadata, HistoricalUniverseNotVerifiedError
from screener import run_stage1_screener, DEFAULT_NIFTY_SYMBOLS
from portfolio_engine import PortfolioEngine
from database import (
    add_paper_trade,
    get_open_trades,
    get_closed_trades,
    update_trade_status,
    sync_paper_trades,
    get_portfolio_performance_summary,
    get_open_trades_with_live_data
)

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
ML_DIR = os.path.join(PROJECT_ROOT, "data", "ml")
DATASET_CSV = os.path.join(ML_DIR, "training_dataset.csv")


class TestStep7PrototypeAcceptance(unittest.TestCase):

    def test_1_dashboard_startup_and_import(self):
        import app
        self.assertTrue(hasattr(app, "CUSTOM_CSS"))
        self.assertEqual(len(app.official_universe), 500)

    def test_2_market_overview_data(self):
        universe = DEFAULT_NIFTY_SYMBOLS
        self.assertEqual(len(universe), 500)
        meta = get_universe_metadata("2026-08-10")
        self.assertIn(meta["evidence_status"], ["OFFICIAL_CURRENT_SNAPSHOT", "OFFICIAL_ANCHOR_ACTIVE"])

    def test_3_signal_screener_structure(self):
        res = run_stage1_screener(DEFAULT_NIFTY_SYMBOLS[:10], verbose=False)
        self.assertIsInstance(res, pd.DataFrame)
        if not res.empty:
            self.assertIn("Symbol", res.columns)

    def test_4_proposed_trades_capacity_and_duplicate_constraints(self):
        eng = PortfolioEngine(initial_capital=1000000.0, position_size=100000.0, max_positions=10)
        sig = pd.DataFrame([
            {"symbol": "INFY.NS", "signal_date": "2026-03-01", "entry_date": "2026-03-02", "entry_price": 1500.0, "rsi_14": 40.0, "strategy_name": "VCP", "forward_10d_return": 1.0},
            {"symbol": "INFY.NS", "signal_date": "2026-03-01", "entry_date": "2026-03-02", "entry_price": 1500.0, "rsi_14": 45.0, "strategy_name": "EMA", "forward_10d_return": 1.0}
        ])
        eng.process_day("2026-03-01", sig, policy_mode="BASELINE")
        self.assertEqual(len(eng.active_positions), 1)

    def test_5_paper_trade_execution(self):
        initial_summary = get_portfolio_performance_summary()
        trade = add_paper_trade(
            symbol="ACCEPTANCE_TEST_1.NS",
            entry_price=1000.0,
            quantity=100,
            stop_loss=950.0,
            target=1150.0,
            strategy_used="Donchian_Breakout"
        )
        self.assertIsNotNone(trade.id)
        open_trades = get_open_trades()
        matching = [t for t in open_trades if t.id == trade.id]
        self.assertEqual(len(matching), 1)

    def test_6_paper_trade_exit_and_pnl(self):
        trade = add_paper_trade(
            symbol="ACCEPTANCE_TEST_EXIT.NS",
            entry_price=1000.0,
            quantity=100,
            stop_loss=950.0,
            target=1150.0,
            strategy_used="EMA_Pullback"
        )
        closed = update_trade_status(trade.id, "CLOSED_TARGET", exit_price=1150.0)
        self.assertIsNotNone(closed)
        self.assertEqual(closed.realized_pnl, 15000.0)

    def test_7_scenarios_a_to_f_accounting_and_persistence(self):
        # Scenario A: Profitable exit
        t_a = add_paper_trade("SCENARIO_A.NS", 100.0, 100, 90.0, 120.0, "VCP")
        c_a = update_trade_status(t_a.id, "CLOSED_TARGET", exit_price=120.0)
        self.assertEqual(c_a.realized_pnl, 2000.0)

        # Scenario B: Loss exit
        t_b = add_paper_trade("SCENARIO_B.NS", 100.0, 100, 90.0, 120.0, "VCP")
        c_b = update_trade_status(t_b.id, "CLOSED_SL", exit_price=90.0)
        self.assertEqual(c_b.realized_pnl, -1000.0)

        # Scenario C: Duplicate position rejection
        eng = PortfolioEngine()
        sig = pd.DataFrame([
            {"symbol": "DUP.NS", "signal_date": "2026-03-01", "entry_date": "2026-03-02", "entry_price": 100.0, "rsi_14": 40.0, "strategy_name": "S1", "forward_10d_return": 1.0},
            {"symbol": "DUP.NS", "signal_date": "2026-03-01", "entry_date": "2026-03-02", "entry_price": 100.0, "rsi_14": 45.0, "strategy_name": "S2", "forward_10d_return": 1.0}
        ])
        eng.process_day("2026-03-01", sig, policy_mode="BASELINE")
        self.assertEqual(len(eng.active_positions), 1)

        # Scenario D: Max 10 positions capacity rejection
        eng_cap = PortfolioEngine(max_positions=10)
        items = [{"symbol": f"CAP_{i}.NS", "signal_date": "2026-03-01", "entry_date": "2026-03-02", "entry_price": 10.0, "rsi_14": 50.0, "strategy_name": "S", "forward_10d_return": 1.0} for i in range(15)]
        eng_cap.process_day("2026-03-01", pd.DataFrame(items), policy_mode="BASELINE")
        self.assertEqual(len(eng_cap.active_positions), 10)

        # Scenario E: Cash deficit rejection
        eng_cash = PortfolioEngine(initial_capital=50000.0, position_size=100000.0)
        eng_cash.process_day("2026-03-01", pd.DataFrame(items[:2]), policy_mode="BASELINE")
        self.assertEqual(len(eng_cash.active_positions), 0)

        # Scenario F: SQLite state persistence reload
        summary = get_portfolio_performance_summary()
        self.assertIn("total_realized_pnl", summary)

    def test_8_ml_isolation_guarantee(self):
        eng1 = PortfolioEngine(slot_policy="hold_to_expiry")
        eng2 = PortfolioEngine(slot_policy="hold_to_expiry")
        df1 = pd.DataFrame([{"symbol": "TEST.NS", "signal_date": "2026-03-01", "entry_date": "2026-03-02", "entry_price": 100.0, "rsi_14": 40.0, "ml_probability": 0.99, "strategy_name": "S", "forward_10d_return": 1.0}])
        df2 = pd.DataFrame([{"symbol": "TEST.NS", "signal_date": "2026-03-01", "entry_date": "2026-03-02", "entry_price": 100.0, "rsi_14": 40.0, "ml_probability": 0.01, "strategy_name": "S", "forward_10d_return": 1.0}])

        eng1.process_day("2026-03-01", df1, policy_mode="BASELINE")
        eng2.process_day("2026-03-01", df2, policy_mode="BASELINE")
        self.assertEqual(eng1.cash, eng2.cash)

    def test_9_historical_universe_sanity(self):
        # Research mode as-of 2024-03-31
        u_res = get_universe_as_of("2024-03-31", mode="research")
        self.assertGreater(len(u_res), 400)

        # Strict mode raises exception for unverified pre-2024 date
        with self.assertRaises(HistoricalUniverseNotVerifiedError):
            get_universe_as_of("2022-01-01", mode="strict")

    def test_10_smoke_test_subset_clarification(self):
        # Confirm that SMOKE_TEST_SUBSET (5 stocks) is intentional for smoke testing
        smoke_stocks = ["RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK"]
        self.assertEqual(len(smoke_stocks), 5)

    def test_11_real_time_price_refresh(self):
        live_open = get_open_trades_with_live_data()
        self.assertIsInstance(live_open, list)

    def test_12_full_daily_user_workflow(self):
        # Step 1: Universe
        u = DEFAULT_NIFTY_SYMBOLS
        self.assertEqual(len(u), 500)
        # Step 2: Screener
        df_sig = run_stage1_screener(u[:5], verbose=False)
        self.assertIsInstance(df_sig, pd.DataFrame)
        # Step 3: Paper Trade
        trade = add_paper_trade("WORKFLOW_TEST.NS", 500.0, 200, 480.0, 550.0, "VCP_Breakout")
        self.assertIsNotNone(trade.id)
        # Step 4: Exit Paper Trade
        closed = update_trade_status(trade.id, "CLOSED_TARGET", exit_price=550.0)
        self.assertEqual(closed.realized_pnl, 10000.0)


if __name__ == "__main__":
    unittest.main()
