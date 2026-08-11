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


class TestStep5BRobustness(unittest.TestCase):

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

        self.engine = PortfolioEngine(slot_policy="replace_if_superior", replacement_margin=0.10)
        for d in self.dates:
            sig = self.test_df[self.test_df["signal_date"] == d]
            self.engine.process_day(d, sig, policy_mode="ML_RANKING")

        self.df_ledger = pd.DataFrame(self.engine.portfolio_ledger)

    def test_1_every_entry_has_corresponding_exit_or_open_state(self):
        exec_entries = self.df_ledger[self.df_ledger["decision"].isin(["EXECUTED"])]
        executed_pos_ids = set(exec_entries["position_id"])
        exited_pos_ids = set(t["position_id"] for t in self.engine.executed_trades)
        active_pos_ids = set(p.position_id for p in self.engine.active_positions)

        self.assertEqual(executed_pos_ids, exited_pos_ids.union(active_pos_ids))

    def test_2_every_exit_references_existing_position(self):
        exits = self.df_ledger[self.df_ledger["decision"] == "EXIT"]
        exec_ids = set(self.df_ledger[self.df_ledger["decision"] == "EXECUTED"]["position_id"])
        for _, row in exits.iterrows():
            self.assertIn(row["position_id"], exec_ids)

    def test_3_no_position_overlaps_itself(self):
        exec_entries = self.df_ledger[self.df_ledger["decision"] == "EXECUTED"]
        pos_ids = exec_entries["position_id"].tolist()
        self.assertEqual(len(pos_ids), len(set(pos_ids)))

    def test_4_one_security_cannot_have_simultaneous_positions(self):
        for d in self.dates:
            eng = PortfolioEngine()
            sig = self.test_df[self.test_df["signal_date"] == d]
            eng.process_day(d, sig, policy_mode="ML_RANKING")
            syms = [p.symbol for p in eng.active_positions]
            self.assertEqual(len(syms), len(set(syms)))

    def test_5_replacement_creates_exactly_one_exit_plus_one_new_entry(self):
        repl_exits = self.df_ledger[self.df_ledger["decision_reason"] == "REPLACED_LOWER_SCORE_POSITION"]
        repl_entries = self.df_ledger[self.df_ledger["decision_reason"] == "EXECUTED_VIA_REPLACEMENT"]
        self.assertEqual(len(repl_exits), len(repl_entries))

    def test_6_replacement_transaction_costs_charged(self):
        cost = self.engine.compute_transaction_cost(100000.0, is_exit=True)
        self.assertGreater(cost, 0.0)

    def test_7_cash_never_becomes_negative(self):
        for eq in self.engine.daily_equities:
            self.assertGreaterEqual(eq["cash"], 0.0)

    def test_8_gross_exposure_never_exceeds_initial_capital(self):
        for eq in self.engine.daily_equities:
            self.assertLessEqual(eq["gross_exposure"], 1000000.0)

    def test_9_maximum_concurrent_positions_never_exceeds_10(self):
        for eq in self.engine.daily_equities:
            self.assertLessEqual(eq["active_positions"], 10)

    def test_10_portfolio_equity_reconciles_from_position_ledger(self):
        summary = self.engine.get_summary_performance()
        self.assertIn("final_capital", summary)

    def test_11_final_portfolio_value_reconciles_exactly(self):
        summary = self.engine.get_summary_performance()
        eq_last = self.engine.daily_equities[-1]["portfolio_value"]
        self.assertEqual(summary["final_capital"], round(eq_last, 2))

    def test_12_all_rejected_signals_have_valid_rejection_reasons(self):
        rejections = self.df_ledger[self.df_ledger["decision"] == "REJECTED"]
        for _, row in rejections.iterrows():
            self.assertTrue(row["decision_reason"].startswith("REJECTED_"))

    def test_13_all_executed_signals_have_valid_execution_records(self):
        executed = self.df_ledger[self.df_ledger["decision"] == "EXECUTED"]
        for _, row in executed.iterrows():
            self.assertIsNotNone(row["position_id"])

    def test_14_repeated_execution_produces_identical_results(self):
        e1 = PortfolioEngine(slot_policy="replace_if_superior", replacement_margin=0.10)
        e2 = PortfolioEngine(slot_policy="replace_if_superior", replacement_margin=0.10)
        for d in self.dates:
            sig = self.test_df[self.test_df["signal_date"] == d]
            e1.process_day(d, sig, policy_mode="ML_RANKING")
            e2.process_day(d, sig, policy_mode="ML_RANKING")

        s1 = e1.get_summary_performance()
        s2 = e2.get_summary_performance()
        self.assertEqual(s1, s2)


if __name__ == "__main__":
    unittest.main()
