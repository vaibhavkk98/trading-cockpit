import os
import unittest
import pandas as pd
from typing import Dict, Any

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
ML_DIR = os.path.join(PROJECT_ROOT, "data", "ml")
TRAINING_DATASET_CSV = os.path.join(ML_DIR, "training_dataset.csv")
SPLIT_MANIFEST_CSV = os.path.join(ML_DIR, "dataset_split_manifest.csv")


class TestMLDataset(unittest.TestCase):

    def setUp(self):
        self.assertTrue(os.path.exists(TRAINING_DATASET_CSV), f"Dataset file missing: {TRAINING_DATASET_CSV}")
        self.df = pd.read_csv(TRAINING_DATASET_CSV)
        self.split_df = pd.read_csv(SPLIT_MANIFEST_CSV) if os.path.exists(SPLIT_MANIFEST_CSV) else pd.DataFrame()

    def test_1_dataset_exists_and_non_empty(self):
        self.assertFalse(self.df.empty, "Training dataset CSV is empty!")
        self.assertGreater(len(self.df), 100, f"Dataset has too few rows ({len(self.df)})")

    def test_2_required_columns_exist(self):
        req_cols = [
            "signal_date", "symbol", "strategy_name", "signal_type",
            "ret_5d", "ret_10d", "ret_20d", "dist_ema20_pct", "rsi_14", "atr_20_pct",
            "entry_date", "entry_price", "forward_10d_return", "forward_10d_positive", "forward_10d_max_drawdown",
            "universe_evidence_status", "survivorship_bias_risk"
        ]
        for col in req_cols:
            self.assertIn(col, self.df.columns, f"Required column '{col}' missing from training_dataset.csv!")

    def test_3_no_duplicate_observation_keys(self):
        dups = self.df.duplicated(subset=["signal_date", "symbol", "strategy_name"]).sum()
        self.assertEqual(dups, 0, f"Found {dups} duplicate observation keys (signal_date, symbol, strategy_name)!")

    def test_4_point_in_time_timestamps(self):
        # Entry date must occur strictly after signal date
        sig_dates = pd.to_datetime(self.df["signal_date"])
        entry_dates = pd.to_datetime(self.df["entry_date"])

        diff_days = (entry_dates - sig_dates).dt.days
        self.assertTrue((diff_days >= 1).all(), "Found entry_date occurring on or before signal_date!")

    def test_5_valid_outcomes_and_no_missing_features(self):
        self.assertEqual(self.df.isnull().sum().sum(), 0, "Found null/NaN values in dataset!")
        pos_vals = set(self.df["forward_10d_positive"].unique())
        self.assertTrue(pos_vals.issubset({0, 1}), f"Invalid values in forward_10d_positive: {pos_vals}")

    def test_6_chronological_split_non_overlapping(self):
        self.assertFalse(self.split_df.empty, "Split manifest CSV missing or empty!")
        splits = self.split_df.set_index("split").to_dict(orient="index")

        train_end = splits["TRAIN"]["end_date"]
        val_start = splits["VALIDATION"]["start_date"]
        val_end = splits["VALIDATION"]["end_date"]
        test_start = splits["TEST"]["start_date"]

        self.assertLessEqual(train_end, val_start, "Train split end date overlaps with Validation split start date!")
        self.assertLessEqual(val_end, test_start, "Validation split end date overlaps with Test split start date!")


if __name__ == "__main__":
    unittest.main()
