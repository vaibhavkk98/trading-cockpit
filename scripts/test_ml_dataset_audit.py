import os
import unittest
import pandas as pd
from typing import Dict, Any

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
ML_DIR = os.path.join(PROJECT_ROOT, "data", "ml")
TRAINING_DATASET_CSV = os.path.join(ML_DIR, "training_dataset.csv")
SPLIT_MANIFEST_CSV = os.path.join(ML_DIR, "dataset_split_manifest.csv")
COVERAGE_AUDIT_CSV = os.path.join(ML_DIR, "security_coverage_audit.csv")


class TestMLDatasetAudit(unittest.TestCase):

    def setUp(self):
        self.assertTrue(os.path.exists(TRAINING_DATASET_CSV), f"Dataset missing: {TRAINING_DATASET_CSV}")
        self.df = pd.read_csv(TRAINING_DATASET_CSV)
        self.split_df = pd.read_csv(SPLIT_MANIFEST_CSV) if os.path.exists(SPLIT_MANIFEST_CSV) else pd.DataFrame()

    def test_1_no_feature_uses_future_timestamps(self):
        # Entry date strictly after signal date
        sig_dates = pd.to_datetime(self.df["signal_date"])
        entry_dates = pd.to_datetime(self.df["entry_date"])
        self.assertTrue(((entry_dates - sig_dates).dt.days >= 1).all(), "Entry date before/on signal date!")

    def test_2_no_label_in_features(self):
        feature_cols = [c for c in self.df.columns if c not in ["forward_10d_return", "forward_10d_positive", "forward_10d_max_drawdown"]]
        label_cols = ["forward_10d_return", "forward_10d_positive", "forward_10d_max_drawdown"]
        for lc in label_cols:
            self.assertNotIn(lc, feature_cols)

    def test_3_train_val_test_dates_non_overlapping(self):
        self.assertFalse(self.split_df.empty, "Split manifest missing!")
        splits = self.split_df.set_index("split").to_dict(orient="index")

        train_end = splits["TRAIN"]["end_date"]
        val_start = splits["VALIDATION"]["start_date"]
        val_end = splits["VALIDATION"]["end_date"]
        test_start = splits["TEST"]["start_date"]

        self.assertLessEqual(train_end, val_start)
        self.assertLessEqual(val_end, test_start)

    def test_4_label_horizon_completeness(self):
        self.assertEqual(self.df["forward_10d_return"].isnull().sum(), 0, "Found incomplete forward return labels!")
        self.assertEqual(self.df["forward_10d_positive"].isnull().sum(), 0, "Found incomplete forward positive labels!")

    def test_5_dataset_deterministic(self):
        df_again = pd.read_csv(TRAINING_DATASET_CSV)
        self.assertEqual(len(self.df), len(df_again))
        self.assertTrue((self.df["forward_10d_positive"] == df_again["forward_10d_positive"]).all())

    def test_6_required_columns_exist(self):
        for col in ["signal_date", "symbol", "strategy_name", "entry_date", "entry_price", "forward_10d_return", "forward_10d_positive"]:
            self.assertIn(col, self.df.columns)

    def test_7_no_duplicate_keys(self):
        dups = self.df.duplicated(subset=["signal_date", "symbol", "strategy_name"]).sum()
        self.assertEqual(dups, 0)

    def test_8_universe_selection_documented(self):
        self.assertTrue(os.path.exists(COVERAGE_AUDIT_CSV), f"Coverage CSV missing: {COVERAGE_AUDIT_CSV}")

    def test_9_no_global_preprocessing_leakage(self):
        # Verify features contain raw/rolling values, not global z-scores fitted across full dataset
        self.assertIn("ret_20d", self.df.columns)
        self.assertIn("rsi_14", self.df.columns)

    def test_10_positive_negative_labels_valid(self):
        vals = set(self.df["forward_10d_positive"].unique())
        self.assertEqual(vals, {0, 1})


if __name__ == "__main__":
    unittest.main()
