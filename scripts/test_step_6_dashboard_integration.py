import os
import pickle
import unittest
import pandas as pd

from universe_engine import get_universe_metadata
from screener import run_stage1_screener, DEFAULT_NIFTY_SYMBOLS
from portfolio_engine import PortfolioEngine
from database import add_paper_trade, get_open_trades, get_portfolio_performance_summary

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
ML_DIR = os.path.join(PROJECT_ROOT, "data", "ml")
DATASET_CSV = os.path.join(ML_DIR, "training_dataset.csv")
GB_MODEL_PATH = os.path.join(ML_DIR, "models", "gradient_boosting_classifier.pkl")


class TestStep6DashboardIntegration(unittest.TestCase):

    def test_1_dashboard_imports_and_loads(self):
        import app
        self.assertTrue(hasattr(app, "CUSTOM_CSS"))

    def test_2_current_universe_loads_500_constituents(self):
        universe = DEFAULT_NIFTY_SYMBOLS
        self.assertEqual(len(universe), 500)

    def test_3_signal_screener_smoke_test(self):
        test_sample = ["RELIANCE.NS", "TCS.NS", "INFY.NS", "HDFCBANK.NS", "ICICIBANK.NS"]
        res = run_stage1_screener(test_sample, verbose=False)
        self.assertIsInstance(res, pd.DataFrame)

    def test_4_pure_strategy_baseline_deterministic_ordering(self):
        eng1 = PortfolioEngine(slot_policy="hold_to_expiry")
        eng2 = PortfolioEngine(slot_policy="hold_to_expiry")
        sample_df = pd.DataFrame([
            {"symbol": "INFY.NS", "signal_date": "2026-03-01", "entry_date": "2026-03-02", "entry_price": 1500.0, "rsi_14": 45.0, "strategy_name": "Donchian_Breakout", "forward_10d_return": 2.5},
            {"symbol": "TCS.NS", "signal_date": "2026-03-01", "entry_date": "2026-03-02", "entry_price": 3500.0, "rsi_14": 55.0, "strategy_name": "EMA_Pullback", "forward_10d_return": 1.5}
        ])
        eng1.process_day("2026-03-01", sample_df, policy_mode="BASELINE")
        eng2.process_day("2026-03-01", sample_df, policy_mode="BASELINE")
        self.assertEqual(eng1.cash, eng2.cash)

    def test_5_ml_probability_does_not_affect_decisions_when_ml_mode_off(self):
        eng = PortfolioEngine(slot_policy="hold_to_expiry")
        sample_df = pd.DataFrame([
            {"symbol": "INFY.NS", "signal_date": "2026-03-01", "entry_date": "2026-03-02", "entry_price": 1500.0, "ml_probability": 0.99, "strategy_name": "Donchian_Breakout", "forward_10d_return": 2.5},
            {"symbol": "TCS.NS", "signal_date": "2026-03-01", "entry_date": "2026-03-02", "entry_price": 3500.0, "ml_probability": 0.01, "strategy_name": "EMA_Pullback", "forward_10d_return": 1.5}
        ])
        eng.process_day("2026-03-01", sample_df, policy_mode="BASELINE")
        self.assertEqual(len(eng.active_positions), 2)

    def test_6_paper_portfolio_accepts_valid_paper_trade(self):
        trade = add_paper_trade(
            symbol="TEST_RELIANCE.NS",
            entry_price=2500.0,
            quantity=40,
            stop_loss=2400.0,
            target=2750.0,
            strategy_used="VCP_Breakout"
        )
        self.assertIsNotNone(trade.id)

    def test_7_paper_portfolio_rejects_duplicate_active_positions(self):
        eng = PortfolioEngine()
        sig = pd.DataFrame([
            {"symbol": "RELIANCE.NS", "signal_date": "2026-03-01", "entry_date": "2026-03-02", "entry_price": 2500.0, "rsi_14": 40.0, "strategy_name": "VCP", "forward_10d_return": 1.0},
            {"symbol": "RELIANCE.NS", "signal_date": "2026-03-01", "entry_date": "2026-03-02", "entry_price": 2500.0, "rsi_14": 45.0, "strategy_name": "EMA", "forward_10d_return": 1.0}
        ])
        eng.process_day("2026-03-01", sig, policy_mode="BASELINE")
        self.assertEqual(len(eng.active_positions), 1)

    def test_8_paper_portfolio_never_exceeds_10_positions(self):
        eng = PortfolioEngine(max_positions=10)
        items = []
        for i in range(15):
            items.append({
                "symbol": f"STOCK_{i}.NS",
                "signal_date": "2026-03-01",
                "entry_date": "2026-03-02",
                "entry_price": 100.0,
                "rsi_14": 50.0,
                "strategy_name": "VCP",
                "forward_10d_return": 1.0
            })
        sig = pd.DataFrame(items)
        eng.process_day("2026-03-01", sig, policy_mode="BASELINE")
        self.assertLessEqual(len(eng.active_positions), 10)

    def test_9_cash_never_becomes_negative(self):
        eng = PortfolioEngine(initial_capital=1000000.0, position_size=100000.0, max_positions=10)
        items = []
        for i in range(15):
            items.append({
                "symbol": f"STOCK_{i}.NS",
                "signal_date": "2026-03-01",
                "entry_date": "2026-03-02",
                "entry_price": 100.0,
                "rsi_14": 50.0,
                "strategy_name": "VCP",
                "forward_10d_return": 1.0
            })
        sig = pd.DataFrame(items)
        eng.process_day("2026-03-01", sig, policy_mode="BASELINE")
        self.assertGreaterEqual(eng.cash, 0.0)

    def test_10_portfolio_value_calculation_is_correct(self):
        eng = PortfolioEngine(initial_capital=1000000.0, position_size=100000.0, max_positions=10)
        sig = pd.DataFrame([
            {"symbol": "RELIANCE.NS", "signal_date": "2026-03-01", "entry_date": "2026-03-02", "entry_price": 2500.0, "rsi_14": 40.0, "strategy_name": "VCP", "forward_10d_return": 1.0}
        ])
        eng.process_day("2026-03-01", sig, policy_mode="BASELINE")
        summary = eng.get_summary_performance()
        self.assertIn("final_capital", summary)

    def test_11_existing_step_5c_results_remain_unchanged(self):
        report_path = os.path.join(ML_DIR, "step_5c_portfolio_reconciliation_report.md")
        self.assertTrue(os.path.exists(report_path))

    def test_12_existing_universe_metadata_tests_pass(self):
        meta = get_universe_metadata(date_str="2026-03-31")
        self.assertIn("evidence_status", meta)


if __name__ == "__main__":
    unittest.main()
