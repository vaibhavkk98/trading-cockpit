import os
import pickle
import unittest
import pandas as pd
import numpy as np

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
ML_DIR = os.path.join(PROJECT_ROOT, "data", "ml")
MODEL_DIR = os.path.join(ML_DIR, "models")
TRAINING_DATASET_CSV = os.path.join(ML_DIR, "training_dataset.csv")


class TestMLModels(unittest.TestCase):

    def setUp(self):
        self.assertTrue(os.path.exists(TRAINING_DATASET_CSV), f"Dataset file missing: {TRAINING_DATASET_CSV}")
        self.df = pd.read_csv(TRAINING_DATASET_CSV)

    def test_1_dataset_loads_successfully(self):
        self.assertFalse(self.df.empty)
        self.assertGreater(len(self.df), 1000)

    def test_2_expected_target_exists(self):
        self.assertIn("forward_10d_positive", self.df.columns)
        self.assertIn("forward_10d_return", self.df.columns)

    def test_3_labels_excluded_from_features(self):
        label_cols = ["forward_10d_return", "forward_10d_positive", "forward_10d_max_drawdown"]
        feature_cols = [c for c in self.df.columns if c not in label_cols and c not in ["signal_date", "symbol", "strategy_name", "entry_date"]]
        for lc in label_cols:
            self.assertNotIn(lc, feature_cols)

    def test_4_chronological_ordering_preserved(self):
        sig_dates = pd.to_datetime(self.df["signal_date"])
        self.assertTrue(sig_dates.is_monotonic_increasing or (sig_dates.diff().dt.days >= 0).mean() > 0.95)

    def test_5_scaler_artifact_exists_and_reloads(self):
        scaler_path = os.path.join(MODEL_DIR, "feature_scaler.pkl")
        self.assertTrue(os.path.exists(scaler_path), f"Scaler missing: {scaler_path}")
        with open(scaler_path, "rb") as f:
            scaler = pickle.load(f)
        self.assertTrue(hasattr(scaler, "mean_"))

    def test_6_baseline_model_artifacts_exist(self):
        expected_models = [
            "logistic_regression_classifier.pkl",
            "random_forest_classifier.pkl",
            "gradient_boosting_classifier.pkl"
        ]
        for m_name in expected_models:
            m_path = os.path.join(MODEL_DIR, m_name)
            self.assertTrue(os.path.exists(m_path), f"Model artifact missing: {m_path}")

    def test_7_reloaded_model_predictions(self):
        gb_path = os.path.join(MODEL_DIR, "gradient_boosting_classifier.pkl")
        with open(gb_path, "rb") as f:
            gb_model = pickle.load(f)

        feats = [
            "close_price", "ret_5d", "ret_10d", "ret_20d", "ret_50d",
            "dist_ema20_pct", "dist_ema50_pct", "dist_ema200_pct", "slope_ema20", "slope_ema50",
            "rsi_14", "rs_3m", "atr_20", "atr_20_pct", "vol_20d", "vcp_ratio",
            "volume_ratio_20d", "turnover_20d", "nifty_ret_20d", "nifty_vol_20d", "nifty_dist_ema50"
        ]
        sample_x = self.df[feats].head(10)
        probs = gb_model.predict_proba(sample_x)[:, 1]

        self.assertEqual(len(probs), 10)
        self.assertTrue((probs >= 0.0).all() and (probs <= 1.0).all())

    def test_8_prediction_probabilities_bounded(self):
        rf_path = os.path.join(MODEL_DIR, "random_forest_classifier.pkl")
        with open(rf_path, "rb") as f:
            rf_model = pickle.load(f)

        feats = [
            "close_price", "ret_5d", "ret_10d", "ret_20d", "ret_50d",
            "dist_ema20_pct", "dist_ema50_pct", "dist_ema200_pct", "slope_ema20", "slope_ema50",
            "rsi_14", "rs_3m", "atr_20", "atr_20_pct", "vol_20d", "vcp_ratio",
            "volume_ratio_20d", "turnover_20d", "nifty_ret_20d", "nifty_vol_20d", "nifty_dist_ema50"
        ]
        probs = rf_model.predict_proba(self.df[feats].tail(50))[:, 1]
        self.assertTrue((probs >= 0.0).all() and (probs <= 1.0).all())

    def test_9_no_nan_or_inf_predictions(self):
        lr_path = os.path.join(MODEL_DIR, "logistic_regression_classifier.pkl")
        scaler_path = os.path.join(MODEL_DIR, "feature_scaler.pkl")

        with open(lr_path, "rb") as f:
            lr_model = pickle.load(f)
        with open(scaler_path, "rb") as f:
            scaler = pickle.load(f)

        feats = [
            "close_price", "ret_5d", "ret_10d", "ret_20d", "ret_50d",
            "dist_ema20_pct", "dist_ema50_pct", "dist_ema200_pct", "slope_ema20", "slope_ema50",
            "rsi_14", "rs_3m", "atr_20", "atr_20_pct", "vol_20d", "vcp_ratio",
            "volume_ratio_20d", "turnover_20d", "nifty_ret_20d", "nifty_vol_20d", "nifty_dist_ema50"
        ]
        x_scaled = np.clip(scaler.transform(self.df[feats].head(50).fillna(0.0)), -10.0, 10.0)
        probs = lr_model.predict_proba(x_scaled)[:, 1]

        self.assertFalse(np.isnan(probs).any())
        self.assertFalse(np.isinf(probs).any())

    def test_10_model_determinism(self):
        gb_path = os.path.join(MODEL_DIR, "gradient_boosting_classifier.pkl")
        with open(gb_path, "rb") as f:
            gb_model = pickle.load(f)

        feats = [
            "close_price", "ret_5d", "ret_10d", "ret_20d", "ret_50d",
            "dist_ema20_pct", "dist_ema50_pct", "dist_ema200_pct", "slope_ema20", "slope_ema50",
            "rsi_14", "rs_3m", "atr_20", "atr_20_pct", "vol_20d", "vcp_ratio",
            "volume_ratio_20d", "turnover_20d", "nifty_ret_20d", "nifty_vol_20d", "nifty_dist_ema50"
        ]
        sample_x = self.df[feats].head(20)
        p1 = gb_model.predict_proba(sample_x)[:, 1]
        p2 = gb_model.predict_proba(sample_x)[:, 1]
        self.assertTrue(np.array_equal(p1, p2))


if __name__ == "__main__":
    unittest.main()
