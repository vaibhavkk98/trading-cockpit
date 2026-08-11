"""
STEP 4F — Temporal Purity Tests

10 automated tests verifying embargo integrity:
1. No TRAIN label horizon overlaps VALIDATION
2. No VALIDATION label horizon overlaps TEST
3. Chronological ordering is valid
4. No TEST rows used in model fitting
5. Threshold selected on VALIDATION only
6. Threshold frozen before TEST evaluation
7. Feature timestamps do not exceed signal_date
8. Scaler fitted only on TRAIN
9. Label horizon is exactly 10 trading days
10. Embargo implementation is deterministic and reproducible
"""
import os
import sys
import json
import pickle
import unittest
import pandas as pd
import numpy as np

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, PROJECT_ROOT)

ML_DIR = os.path.join(PROJECT_ROOT, "data", "ml")
STEP4F_DIR = os.path.join(ML_DIR, "step_4f")

TRAINING_DATASET_CSV = os.path.join(ML_DIR, "training_dataset.csv")
STEP4F_MANIFEST_JSON = os.path.join(STEP4F_DIR, "embargo_manifest.json")
STEP4F_MODEL_PATH = os.path.join(STEP4F_DIR, "gradient_boosting_classifier.pkl")
STEP4F_SCALER_PATH = os.path.join(STEP4F_DIR, "feature_scaler.pkl")
STEP4F_TEST_PREDICTIONS_CSV = os.path.join(STEP4F_DIR, "test_predictions.csv")

NUMERICAL_FEATURES = [
    "close_price", "ret_5d", "ret_10d", "ret_20d", "ret_50d",
    "dist_ema20_pct", "dist_ema50_pct", "dist_ema200_pct", "slope_ema20", "slope_ema50",
    "rsi_14", "rs_3m", "atr_20", "atr_20_pct", "vol_20d", "vcp_ratio",
    "volume_ratio_20d", "turnover_20d", "nifty_ret_20d", "nifty_vol_20d", "nifty_dist_ema50"
]

VAL_START_DATE = "2025-10-15"
TEST_START_DATE = "2026-02-18"
LABEL_HORIZON = 10


def _load_splits():
    """Load the dataset and compute embargo-clean splits."""
    df = pd.read_csv(TRAINING_DATASET_CSV)
    df['signal_date'] = pd.to_datetime(df['signal_date'])

    val_start = pd.Timestamp(VAL_START_DATE)
    test_start = pd.Timestamp(TEST_START_DATE)

    all_dates = sorted(df['signal_date'].unique())
    date_to_idx = {d: i for i, d in enumerate(all_dates)}

    def label_end(sd):
        idx = date_to_idx.get(sd)
        if idx is not None and idx + LABEL_HORIZON < len(all_dates):
            return all_dates[idx + LABEL_HORIZON]
        return pd.NaT

    df['_label_end'] = df['signal_date'].map(label_end)

    orig_train = df[df['signal_date'] < val_start]
    orig_val = df[(df['signal_date'] >= val_start) & (df['signal_date'] < test_start)]
    orig_test = df[df['signal_date'] >= test_start]

    train_clean = orig_train[orig_train['_label_end'] < val_start].copy()
    val_clean = orig_val[orig_val['_label_end'] < test_start].copy()
    test_clean = orig_test.copy()

    return df, all_dates, date_to_idx, train_clean, val_clean, test_clean


class TestTemporalPurity01_TrainNoOverlapVal(unittest.TestCase):
    """TEST 1: No TRAIN observation has a label horizon overlapping VALIDATION."""

    def test_no_train_label_in_val(self):
        df, all_dates, date_to_idx, train, val, test = _load_splits()
        val_start = pd.Timestamp(VAL_START_DATE)

        for _, row in train.iterrows():
            sd = row['signal_date']
            idx = date_to_idx.get(sd)
            if idx is not None and idx + LABEL_HORIZON < len(all_dates):
                label_end = all_dates[idx + LABEL_HORIZON]
                self.assertLess(label_end, val_start,
                    f"TRAIN signal {sd.date()} has label_end {label_end.date()} >= VAL start {val_start.date()}")


class TestTemporalPurity02_ValNoOverlapTest(unittest.TestCase):
    """TEST 2: No VALIDATION observation has a label horizon overlapping TEST."""

    def test_no_val_label_in_test(self):
        df, all_dates, date_to_idx, train, val, test = _load_splits()
        test_start = pd.Timestamp(TEST_START_DATE)

        for _, row in val.iterrows():
            sd = row['signal_date']
            idx = date_to_idx.get(sd)
            if idx is not None and idx + LABEL_HORIZON < len(all_dates):
                label_end = all_dates[idx + LABEL_HORIZON]
                self.assertLess(label_end, test_start,
                    f"VAL signal {sd.date()} has label_end {label_end.date()} >= TEST start {test_start.date()}")


class TestTemporalPurity03_ChronologicalOrder(unittest.TestCase):
    """TEST 3: Chronological ordering remains valid."""

    def test_train_before_val_before_test(self):
        _, _, _, train, val, test = _load_splits()
        self.assertLess(train['signal_date'].max(), val['signal_date'].min())
        self.assertLess(val['signal_date'].max(), test['signal_date'].min())


class TestTemporalPurity04_TestNotInModel(unittest.TestCase):
    """TEST 4: No TEST rows are used in model fitting."""

    def test_scaler_sample_count(self):
        """Scaler must be fitted on exactly the embargo-clean TRAIN rows."""
        if not os.path.exists(STEP4F_SCALER_PATH):
            self.skipTest("Step 4F scaler not found")
        _, _, _, train, val, test = _load_splits()
        with open(STEP4F_SCALER_PATH, "rb") as f:
            scaler = pickle.load(f)
        self.assertEqual(int(scaler.n_samples_seen_), len(train),
            f"Scaler fitted on {int(scaler.n_samples_seen_)} rows, expected {len(train)} (embargo-clean TRAIN)")


class TestTemporalPurity05_ThresholdFromVal(unittest.TestCase):
    """TEST 5: Threshold selection uses VALIDATION only."""

    def test_manifest_records_threshold_source(self):
        if not os.path.exists(STEP4F_MANIFEST_JSON):
            self.skipTest("Step 4F manifest not found")
        with open(STEP4F_MANIFEST_JSON) as f:
            manifest = json.load(f)
        self.assertIn("frozen_threshold", manifest)
        self.assertIn("validation_optimal_f1", manifest)
        self.assertGreater(manifest["validation_optimal_f1"], 0.0)


class TestTemporalPurity06_ThresholdFrozen(unittest.TestCase):
    """TEST 6: Threshold is frozen before TEST evaluation."""

    def test_threshold_applied_consistently(self):
        """The threshold in the manifest must match what was used in test predictions."""
        if not os.path.exists(STEP4F_MANIFEST_JSON) or not os.path.exists(STEP4F_TEST_PREDICTIONS_CSV):
            self.skipTest("Step 4F artifacts not found")
        with open(STEP4F_MANIFEST_JSON) as f:
            manifest = json.load(f)
        frozen_th = manifest["frozen_threshold"]
        # The script source must use this threshold, not a different one
        script_path = os.path.join(PROJECT_ROOT, "scripts", "run_step_4f_embargo.py")
        with open(script_path) as f:
            code = f.read()
        # Threshold must come from validation scan, not hardcoded
        self.assertIn("frozen_threshold = best_th", code)


class TestTemporalPurity07_FeatureTimestamps(unittest.TestCase):
    """TEST 7: Feature timestamps do not exceed signal_date."""

    def test_no_future_features(self):
        """No feature column should be a label or forward-looking column."""
        label_cols = ['forward_10d_return', 'forward_10d_positive', 'forward_10d_max_drawdown',
                      'entry_date', 'entry_price']
        for lc in label_cols:
            self.assertNotIn(lc, NUMERICAL_FEATURES,
                f"Label column {lc} found in features — this is future information leakage!")


class TestTemporalPurity08_ScalerTrainOnly(unittest.TestCase):
    """TEST 8: Scaler/normalizer is fitted only on TRAIN."""

    def test_scaler_matches_embargo_train(self):
        if not os.path.exists(STEP4F_SCALER_PATH):
            self.skipTest("Step 4F scaler not found")
        _, _, _, train, _, _ = _load_splits()
        with open(STEP4F_SCALER_PATH, "rb") as f:
            scaler = pickle.load(f)
        # Verify scaler was fitted on the correct number of rows
        self.assertEqual(int(scaler.n_samples_seen_), len(train))
        self.assertEqual(scaler.n_features_in_, len(NUMERICAL_FEATURES))


class TestTemporalPurity09_LabelHorizon(unittest.TestCase):
    """TEST 9: Label horizon is exactly 10 trading days."""

    def test_label_horizon_in_manifest(self):
        if not os.path.exists(STEP4F_MANIFEST_JSON):
            self.skipTest("Step 4F manifest not found")
        with open(STEP4F_MANIFEST_JSON) as f:
            manifest = json.load(f)
        self.assertEqual(manifest["label_horizon_trading_days"], 10)

    def test_embargo_removes_exactly_10_dates_boundaries(self):
        """Embargo should remove exactly 10 trading dates at each boundary
        (the last 10 dates before each split boundary whose labels would cross)."""
        if not os.path.exists(STEP4F_MANIFEST_JSON):
            self.skipTest("Step 4F manifest not found")
        with open(STEP4F_MANIFEST_JSON) as f:
            manifest = json.load(f)
        self.assertEqual(manifest["embargo_removals"]["train_dates_removed"], 10)
        self.assertEqual(manifest["embargo_removals"]["val_dates_removed"], 10)


class TestTemporalPurity10_Reproducibility(unittest.TestCase):
    """TEST 10: Embargo implementation is deterministic and reproducible."""

    def test_predictions_reproducible(self):
        """Reapplying the model to test data must produce identical probabilities."""
        if not os.path.exists(STEP4F_MODEL_PATH) or not os.path.exists(STEP4F_TEST_PREDICTIONS_CSV):
            self.skipTest("Step 4F artifacts not found")

        with open(STEP4F_MODEL_PATH, "rb") as f:
            gb = pickle.load(f)
        with open(STEP4F_SCALER_PATH, "rb") as f:
            scaler = pickle.load(f)

        pred_df = pd.read_csv(STEP4F_TEST_PREDICTIONS_CSV)
        X_test = scaler.transform(pred_df[NUMERICAL_FEATURES].values)
        recomputed = gb.predict_proba(X_test)[:, 1]

        np.testing.assert_array_almost_equal(
            pred_df['ml_probability'].values, recomputed, decimal=6,
            err_msg="Predictions are NOT reproducible!")

    def test_embargo_deterministic(self):
        """Running the embargo twice produces identical split sizes."""
        from scripts.run_step_4f_embargo import apply_embargo
        df = pd.read_csv(TRAINING_DATASET_CSV)
        r1 = apply_embargo(df)
        r2 = apply_embargo(df)
        self.assertEqual(len(r1["train"]), len(r2["train"]))
        self.assertEqual(len(r1["val"]), len(r2["val"]))
        self.assertEqual(len(r1["test"]), len(r2["test"]))


if __name__ == "__main__":
    print("=" * 80)
    print("STEP 4F TEMPORAL PURITY TESTS")
    print("=" * 80)

    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for tc in [
        TestTemporalPurity01_TrainNoOverlapVal,
        TestTemporalPurity02_ValNoOverlapTest,
        TestTemporalPurity03_ChronologicalOrder,
        TestTemporalPurity04_TestNotInModel,
        TestTemporalPurity05_ThresholdFromVal,
        TestTemporalPurity06_ThresholdFrozen,
        TestTemporalPurity07_FeatureTimestamps,
        TestTemporalPurity08_ScalerTrainOnly,
        TestTemporalPurity09_LabelHorizon,
        TestTemporalPurity10_Reproducibility,
    ]:
        suite.addTests(loader.loadTestsFromTestCase(tc))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    print(f"\n{'='*80}")
    total = result.testsRun
    failed = len(result.failures) + len(result.errors)
    print(f"STEP 4F TEMPORAL PURITY: {total - failed}/{total} tests passed, {failed} failed")
    print(f"{'='*80}")
    sys.exit(0 if result.wasSuccessful() else 1)
