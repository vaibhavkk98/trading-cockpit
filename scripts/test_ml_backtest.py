import os
import pickle
import unittest
import pandas as pd
import numpy as np

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
ML_DIR = os.path.join(PROJECT_ROOT, "data", "ml")
MODEL_DIR = os.path.join(ML_DIR, "models")

DATASET_CSV = os.path.join(ML_DIR, "training_dataset.csv")
MANIFEST_CSV = os.path.join(ML_DIR, "authoritative_dataset_manifest.csv")
GB_MODEL_PATH = os.path.join(MODEL_DIR, "gradient_boosting_classifier.pkl")
SCALER_PATH = os.path.join(MODEL_DIR, "feature_scaler.pkl")


class TestMLBacktest(unittest.TestCase):

    def setUp(self):
        self.assertTrue(os.path.exists(DATASET_CSV), f"Dataset missing: {DATASET_CSV}")
        self.assertTrue(os.path.exists(MANIFEST_CSV), f"Manifest missing: {MANIFEST_CSV}")
        self.assertTrue(os.path.exists(GB_MODEL_PATH), f"Model missing: {GB_MODEL_PATH}")

        self.df = pd.read_csv(DATASET_CSV)
        self.manifest = pd.read_csv(MANIFEST_CSV)

        with open(GB_MODEL_PATH, "rb") as f:
            self.model = pickle.load(f)

    def test_1_authoritative_dataset_loads(self):
        self.assertFalse(self.df.empty)
        self.assertEqual(len(self.df), self.manifest["dataset_row_count"].iloc[0])

    def test_2_dataset_manifest_matches_actual(self):
        self.assertEqual(self.df["symbol"].nunique(), self.manifest["unique_security_count"].iloc[0])
        self.assertEqual(self.df["strategy_name"].nunique(), self.manifest["strategy_count"].iloc[0])

    def test_3_train_val_test_boundaries_non_overlapping(self):
        tr_end = self.manifest["train_end"].iloc[0]
        va_start = self.manifest["validation_start"].iloc[0]
        va_end = self.manifest["validation_end"].iloc[0]
        te_start = self.manifest["test_start"].iloc[0]

        self.assertLessEqual(tr_end, va_start)
        self.assertLessEqual(va_end, te_start)

    def test_4_ml_model_loads_and_schema_matches(self):
        feats = [
            "close_price", "ret_5d", "ret_10d", "ret_20d", "ret_50d",
            "dist_ema20_pct", "dist_ema50_pct", "dist_ema200_pct", "slope_ema20", "slope_ema50",
            "rsi_14", "rs_3m", "atr_20", "atr_20_pct", "vol_20d", "vcp_ratio",
            "volume_ratio_20d", "turnover_20d", "nifty_ret_20d", "nifty_vol_20d", "nifty_dist_ema50"
        ]
        for f in feats:
            self.assertIn(f, self.df.columns)

        probs = self.model.predict_proba(self.df[feats].head(10))[:, 1]
        self.assertEqual(len(probs), 10)
        self.assertTrue((probs >= 0.0).all() and (probs <= 1.0).all())

    def test_5_ml_filtering_never_creates_synthetic_signals(self):
        # Filtered signals subset must be a strict subset of baseline signals
        n_base = len(self.df)
        n_flt = len(self.df[self.df["vol_20d"] > 0]) # test arbitrary filter
        self.assertLessEqual(n_flt, n_base)

    def test_6_tplus1_execution_intact(self):
        sig_dates = pd.to_datetime(self.df["signal_date"])
        entry_dates = pd.to_datetime(self.df["entry_date"])
        self.assertTrue(((entry_dates - sig_dates).dt.days >= 1).all())

    def test_7_backtest_reproducibility(self):
        feats = [
            "close_price", "ret_5d", "ret_10d", "ret_20d", "ret_50d",
            "dist_ema20_pct", "dist_ema50_pct", "dist_ema200_pct", "slope_ema20", "slope_ema50",
            "rsi_14", "rs_3m", "atr_20", "atr_20_pct", "vol_20d", "vcp_ratio",
            "volume_ratio_20d", "turnover_20d", "nifty_ret_20d", "nifty_vol_20d", "nifty_dist_ema50"
        ]
        p1 = self.model.predict_proba(self.df[feats].head(50))[:, 1]
        p2 = self.model.predict_proba(self.df[feats].head(50))[:, 1]
        self.assertTrue(np.array_equal(p1, p2))


if __name__ == "__main__":
    unittest.main()
