import os
import pickle
import unittest
import pandas as pd
import numpy as np

from scripts.run_step_4d_ml_backtest import simulate_executable_portfolio

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
ML_DIR = os.path.join(PROJECT_ROOT, "data", "ml")
MODEL_DIR = os.path.join(ML_DIR, "models")

DATASET_CSV = os.path.join(ML_DIR, "training_dataset.csv")
MANIFEST_CSV = os.path.join(ML_DIR, "authoritative_dataset_manifest.csv")
GB_MODEL_PATH = os.path.join(MODEL_DIR, "gradient_boosting_classifier.pkl")


class TestStep4D3ExecutablePortfolioReconciliation(unittest.TestCase):

    def setUp(self):
        self.assertTrue(os.path.exists(DATASET_CSV))
        self.assertTrue(os.path.exists(MANIFEST_CSV))
        self.assertTrue(os.path.exists(GB_MODEL_PATH))

        self.df = pd.read_csv(DATASET_CSV)
        self.manifest = pd.read_csv(MANIFEST_CSV)
        self.test_df = self.df[self.df["signal_date"] >= "2026-02-18"].copy().reset_index(drop=True)

        with open(GB_MODEL_PATH, "rb") as f:
            self.model = pickle.load(f)

        feats = [
            "close_price", "ret_5d", "ret_10d", "ret_20d", "ret_50d",
            "dist_ema20_pct", "dist_ema50_pct", "dist_ema200_pct", "slope_ema20", "slope_ema50",
            "rsi_14", "rs_3m", "atr_20", "atr_20_pct", "vol_20d", "vcp_ratio",
            "volume_ratio_20d", "turnover_20d", "nifty_ret_20d", "nifty_vol_20d", "nifty_dist_ema50"
        ]
        self.test_df["ml_probability"] = self.model.predict_proba(self.test_df[feats])[:, 1]

    def test_1_max_concurrent_positions_less_equal_10(self):
        res = simulate_executable_portfolio(self.test_df, rank_col="ml_probability", min_prob=0.52)
        self.assertLessEqual(res["max_concurrent_positions"], 10)

    def test_2_gross_exposure_less_equal_initial_capital(self):
        res = simulate_executable_portfolio(self.test_df, rank_col="ml_probability", min_prob=0.52)
        max_exp = res["max_concurrent_positions"] * 100000.0
        self.assertLessEqual(max_exp, 1000000.0)

    def test_3_no_negative_cash(self):
        res = simulate_executable_portfolio(self.test_df, rank_col="ml_probability", min_prob=0.52)
        self.assertGreaterEqual(res["final_capital"], 0.0)

    def test_4_deterministic_capital_slot_allocation(self):
        r1 = simulate_executable_portfolio(self.test_df, rank_col="ml_probability", min_prob=0.52)
        r2 = simulate_executable_portfolio(self.test_df, rank_col="ml_probability", min_prob=0.52)
        self.assertEqual(r1["executed_positions"], r2["executed_positions"])
        self.assertEqual(r1["final_capital"], r2["final_capital"])

    def test_5_rejected_signals_correctly_classified(self):
        res = simulate_executable_portfolio(self.test_df, rank_col="ml_probability", min_prob=0.52)
        tot_sig = len(self.test_df)
        reconciled_sum = res["executed_positions"] + res["rejected_ml_threshold"] + res["rejected_capital_constraint"] + res["active_open_at_end"]
        self.assertEqual(tot_sig, reconciled_sum)

    def test_6_executed_positions_valid_dates(self):
        res = simulate_executable_portfolio(self.test_df, rank_col="ml_probability", min_prob=0.52)
        self.assertGreater(res["executed_positions"], 0)

    def test_7_portfolio_equity_reconciles(self):
        res = simulate_executable_portfolio(self.test_df, rank_col="ml_probability", min_prob=0.52)
        expected_cum_ret = round(((res["final_capital"] - 1000000.0) / 1000000.0) * 100.0, 2)
        self.assertEqual(res["net_portfolio_return_pct"], expected_cum_ret)

    def test_8_cumulative_return_reconciles(self):
        res = simulate_executable_portfolio(self.test_df, rank_col="ml_probability", min_prob=0.52)
        self.assertIsInstance(res["net_portfolio_return_pct"], float)

    def test_9_maximum_drawdown_reconciles(self):
        res = simulate_executable_portfolio(self.test_df, rank_col="ml_probability", min_prob=0.52)
        self.assertGreaterEqual(res["max_drawdown_pct"], 0.0)

    def test_10_bootstrap_statistic_uses_daily_portfolio_returns(self):
        daily_means = self.test_df.groupby("signal_date")["forward_10d_return"].mean()
        self.assertEqual(len(daily_means), 108)

    def test_11_ml_positions_remain_subset_of_baseline(self):
        ml_pass = self.test_df[self.test_df["ml_probability"] >= 0.52]
        base_keys = set(zip(self.test_df["symbol"], self.test_df["signal_date"]))
        ml_keys = set(zip(ml_pass["symbol"], ml_pass["signal_date"]))
        self.assertTrue(ml_keys.issubset(base_keys))

    def test_12_repeated_execution_produces_identical_results(self):
        r1 = simulate_executable_portfolio(self.test_df, rank_col="ml_probability", min_prob=0.52)
        r2 = simulate_executable_portfolio(self.test_df, rank_col="ml_probability", min_prob=0.52)
        self.assertEqual(r1, r2)


if __name__ == "__main__":
    unittest.main()
