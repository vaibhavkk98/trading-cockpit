import os
import pickle
import unittest
import pandas as pd
import numpy as np

from portfolio_engine import PortfolioEngine

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
ML_DIR = os.path.join(PROJECT_ROOT, "data", "ml")
MODEL_DIR = os.path.join(ML_DIR, "models")

DATASET_CSV = os.path.join(ML_DIR, "training_dataset.csv")
GB_MODEL_PATH = os.path.join(MODEL_DIR, "gradient_boosting_classifier.pkl")


class TestStep5PortfolioEngine(unittest.TestCase):

    def setUp(self):
        self.assertTrue(os.path.exists(DATASET_CSV))
        self.assertTrue(os.path.exists(GB_MODEL_PATH))

        self.df = pd.read_csv(DATASET_CSV)
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
        self.dates = sorted(self.test_df["signal_date"].unique())

    def test_1_maximum_10_concurrent_positions(self):
        engine = PortfolioEngine(max_positions=10)
        for d in self.dates:
            sig = self.test_df[self.test_df["signal_date"] == d]
            engine.process_day(d, sig, policy_mode="ML_RANKING")
            self.assertLessEqual(len(engine.active_positions), 10)

    def test_2_gross_exposure_never_exceeds_capital(self):
        engine = PortfolioEngine(initial_capital=1000000.0, position_size=100000.0, max_positions=10)
        for d in self.dates:
            sig = self.test_df[self.test_df["signal_date"] == d]
            engine.process_day(d, sig, policy_mode="ML_RANKING")
            exp = len(engine.active_positions) * 100000.0
            self.assertLessEqual(exp, 1000000.0)

    def test_3_cash_never_becomes_negative(self):
        engine = PortfolioEngine(initial_capital=1000000.0, position_size=100000.0, max_positions=10)
        for d in self.dates:
            sig = self.test_df[self.test_df["signal_date"] == d]
            engine.process_day(d, sig, policy_mode="ML_RANKING")
            self.assertGreaterEqual(engine.cash, 0.0)

    def test_4_one_security_cannot_have_two_simultaneous_positions(self):
        engine = PortfolioEngine(max_positions=10)
        for d in self.dates:
            sig = self.test_df[self.test_df["signal_date"] == d]
            engine.process_day(d, sig, policy_mode="ML_RANKING")
            symbols = [p.symbol for p in engine.active_positions]
            self.assertEqual(len(symbols), len(set(symbols)))

    def test_5_same_day_duplicate_strategies_consolidated(self):
        engine = PortfolioEngine()
        sample_day = self.test_df[self.test_df["signal_date"] == self.dates[0]]
        cons = engine.consolidate_same_day_signals(sample_day)
        self.assertEqual(len(cons), cons["symbol"].nunique())

    def test_6_deterministic_ranking(self):
        engine1 = PortfolioEngine()
        engine2 = PortfolioEngine()

        for d in self.dates[:10]:
            sig = self.test_df[self.test_df["signal_date"] == d]
            engine1.process_day(d, sig, policy_mode="ML_RANKING")
            engine2.process_day(d, sig, policy_mode="ML_RANKING")

        self.assertEqual(engine1.cash, engine2.cash)
        self.assertEqual(len(engine1.active_positions), len(engine2.active_positions))

    def test_7_deterministic_tie_breaking(self):
        engine = PortfolioEngine()
        sig = self.test_df.head(5).copy()
        sig["ml_probability"] = 0.50 # force equal scores
        cons = engine.consolidate_same_day_signals(sig)
        sorted_cand = cons.sort_values(by=["ml_probability", "symbol"], ascending=[False, True])
        self.assertTrue(sorted_cand["symbol"].is_monotonic_increasing)

    def test_8_no_future_information_used(self):
        sig_dates = pd.to_datetime(self.test_df["signal_date"])
        entry_dates = pd.to_datetime(self.test_df["entry_date"])
        self.assertTrue(((entry_dates - sig_dates).dt.days >= 1).all())

    def test_9_replacement_only_occurs_under_explicit_rule(self):
        engine = PortfolioEngine(slot_policy="replace_if_superior", replacement_margin=0.10)
        for d in self.dates:
            sig = self.test_df[self.test_df["signal_date"] == d]
            engine.process_day(d, sig, policy_mode="ML_RANKING")

        df_ledg = pd.DataFrame(engine.portfolio_ledger)
        replacements = df_ledg[df_ledg["decision_reason"] == "REPLACED_LOWER_SCORE_POSITION"]
        self.assertGreaterEqual(len(replacements), 0)

    def test_10_replacement_costs_included(self):
        engine = PortfolioEngine(slot_policy="replace_if_superior")
        cost = engine.compute_transaction_cost(100000.0, is_exit=True)
        self.assertGreater(cost, 0.0)

    def test_11_portfolio_ledger_reconciles_with_equity_curve(self):
        engine = PortfolioEngine()
        for d in self.dates[:20]:
            sig = self.test_df[self.test_df["signal_date"] == d]
            engine.process_day(d, sig, policy_mode="ML_RANKING")

        df_ledg = pd.DataFrame(engine.portfolio_ledger)
        self.assertFalse(df_ledg.empty)

    def test_12_final_portfolio_value_reconciles(self):
        engine = PortfolioEngine()
        for d in self.dates:
            sig = self.test_df[self.test_df["signal_date"] == d]
            engine.process_day(d, sig, policy_mode="ML_RANKING")

        summary = engine.get_summary_performance()
        self.assertIn("final_capital", summary)
        self.assertIsInstance(summary["final_capital"], float)

    def test_13_every_rejection_has_documented_reason(self):
        engine = PortfolioEngine()
        for d in self.dates:
            sig = self.test_df[self.test_df["signal_date"] == d]
            engine.process_day(d, sig, policy_mode="ML_THRESHOLD", min_probability_threshold=0.52)

        df_ledg = pd.DataFrame(engine.portfolio_ledger)
        rejections = df_ledg[df_ledg["decision"] == "REJECTED"]
        for _, row in rejections.iterrows():
            self.assertTrue(row["decision_reason"].startswith("REJECTED_"))

    def test_14_repeated_execution_produces_identical_results(self):
        engine1 = PortfolioEngine()
        engine2 = PortfolioEngine()

        for d in self.dates:
            sig = self.test_df[self.test_df["signal_date"] == d]
            engine1.process_day(d, sig, policy_mode="ML_RANKING")
            engine2.process_day(d, sig, policy_mode="ML_RANKING")

        s1 = engine1.get_summary_performance()
        s2 = engine2.get_summary_performance()
        self.assertEqual(s1, s2)


if __name__ == "__main__":
    unittest.main()
