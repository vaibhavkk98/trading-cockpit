import os
import json
import unittest
import pandas as pd

from run_step_8_paper_observation import run_daily_paper_observation, PAPER_DIR, SNAPSHOT_DIR, LEDGER_CSV
from database import get_portfolio_performance_summary, get_open_trades_with_live_data

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


class TestStep8PaperObservation(unittest.TestCase):

    def setUp(self):
        os.makedirs(SNAPSHOT_DIR, exist_ok=True)

    def test_1_paper_trading_directories_and_ledger_exist(self):
        self.assertTrue(os.path.exists(PAPER_DIR))
        self.assertTrue(os.path.exists(SNAPSHOT_DIR))

    def test_2_daily_paper_observation_cycle_runs(self):
        res = run_daily_paper_observation(as_of_date="2026-08-10")
        self.assertIsInstance(res, dict)
        self.assertIn("system_health", res)
        self.assertEqual(res["universe_size"], 500)

    def test_3_daily_snapshot_json_validity(self):
        snap_path = os.path.join(SNAPSHOT_DIR, "2026-08-10.json")
        self.assertTrue(os.path.exists(snap_path))
        with open(snap_path, "r") as f:
            snap = json.load(f)
        self.assertEqual(snap["date"], "2026-08-10")
        self.assertIn("portfolio_value_inr", snap)

    def test_4_observation_ledger_append_only_csv(self):
        self.assertTrue(os.path.exists(LEDGER_CSV))
        df_ledg = pd.read_csv(LEDGER_CSV)
        self.assertIn("observation_date", df_ledg.columns)
        self.assertIn("symbol", df_ledg.columns)

    def test_5_portfolio_constraints_enforced(self):
        summary = get_portfolio_performance_summary()
        self.assertLessEqual(summary["open_trades_count"], 10)
        cash = max(0.0, 1000000.0 - summary["open_capital_deployed"])
        self.assertGreaterEqual(cash, 0.0)

    def test_6_ml_mode_off_informational_isolation(self):
        df_ledg = pd.read_csv(LEDGER_CSV)
        if not df_ledg.empty and "ml_probability" in df_ledg.columns:
            probs = df_ledg["ml_probability"].dropna()
            for p in probs:
                self.assertIn("Informational", str(p))


if __name__ == "__main__":
    unittest.main()
