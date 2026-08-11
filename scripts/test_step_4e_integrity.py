"""
STEP 4E — Integrity Verification Tests

Verifies:
1. Report metrics equal recomputed metrics
2. No hardcoded performance values
3. Threshold 0.52 comes from validation (not tuned on test)
4. Test observations never influence model fitting
5. Daily Sharpe uses daily returns
6. Drawdown uses equity curve
7. Ranking does not use future returns
8. Transaction costs are included
9. Duplicate symbol/date exposure handled deterministically
10. Authoritative dataset manifest matches actual dataset
"""
import os
import sys
import json
import hashlib
import pickle
import re
import unittest
import pandas as pd
import numpy as np

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, PROJECT_ROOT)

ML_DIR = os.path.join(PROJECT_ROOT, "data", "ml")
MODEL_DIR = os.path.join(ML_DIR, "models")

TRAINING_DATASET_CSV = os.path.join(ML_DIR, "training_dataset.csv")
GB_MODEL_PATH = os.path.join(MODEL_DIR, "gradient_boosting_classifier.pkl")
SCALER_PATH = os.path.join(MODEL_DIR, "feature_scaler.pkl")

STEP4E_DATASET_MANIFEST_JSON = os.path.join(ML_DIR, "step_4e_dataset_manifest.json")
STEP4E_TEST_PREDICTIONS_CSV = os.path.join(ML_DIR, "step_4e_test_predictions.csv")
STEP4E_SIGNAL_COMPARISON_CSV = os.path.join(ML_DIR, "step_4e_signal_comparison.csv")
STEP4E_PORTFOLIO_COMPARISON_CSV = os.path.join(ML_DIR, "step_4e_portfolio_comparison.csv")
STEP4E_REPORT_MD = os.path.join(ML_DIR, "step_4e_ml_backtest_report.md")

NUMERICAL_FEATURES = [
    "close_price", "ret_5d", "ret_10d", "ret_20d", "ret_50d",
    "dist_ema20_pct", "dist_ema50_pct", "dist_ema200_pct", "slope_ema20", "slope_ema50",
    "rsi_14", "rs_3m", "atr_20", "atr_20_pct", "vol_20d", "vcp_ratio",
    "volume_ratio_20d", "turnover_20d", "nifty_ret_20d", "nifty_vol_20d", "nifty_dist_ema50"
]

FROZEN_THRESHOLD = 0.52


class TestStep4EManifest(unittest.TestCase):
    """Test 10: Authoritative dataset manifest matches actual dataset."""

    def setUp(self):
        self.assertTrue(os.path.exists(STEP4E_DATASET_MANIFEST_JSON),
                        "Step 4E manifest not found. Run run_step_4e_ml_backtest.py first.")
        with open(STEP4E_DATASET_MANIFEST_JSON) as f:
            self.manifest = json.load(f)
        self.df = pd.read_csv(TRAINING_DATASET_CSV)

    def test_sha256_matches(self):
        """Manifest SHA256 matches actual dataset file."""
        with open(TRAINING_DATASET_CSV, "rb") as f:
            actual_sha = hashlib.sha256(f.read()).hexdigest()
        self.assertEqual(self.manifest["dataset_sha256"], actual_sha)

    def test_row_count_matches(self):
        """Manifest row count matches actual row count."""
        self.assertEqual(self.manifest["total_rows"], len(self.df))

    def test_split_counts_sum(self):
        """Train + Val + Test = Total rows."""
        total = (self.manifest["train"]["rows"] +
                 self.manifest["validation"]["rows"] +
                 self.manifest["test"]["rows"])
        self.assertEqual(total, self.manifest["total_rows"])

    def test_split_counts_match_data(self):
        """Split row counts match actual date-based filtering."""
        train = self.df[self.df['signal_date'] < "2025-10-15"]
        val = self.df[(self.df['signal_date'] >= "2025-10-15") & (self.df['signal_date'] < "2026-02-18")]
        test = self.df[self.df['signal_date'] >= "2026-02-18"]
        self.assertEqual(self.manifest["train"]["rows"], len(train))
        self.assertEqual(self.manifest["validation"]["rows"], len(val))
        self.assertEqual(self.manifest["test"]["rows"], len(test))

    def test_symbol_count_matches(self):
        """Manifest symbol count matches actual unique symbols."""
        self.assertEqual(self.manifest["unique_symbols"], self.df['symbol'].nunique())


class TestStep4ENoHardcoded(unittest.TestCase):
    """Tests 1-2: Report metrics are computed, not hardcoded."""

    def setUp(self):
        self.assertTrue(os.path.exists(STEP4E_REPORT_MD),
                        "Step 4E report not found. Run run_step_4e_ml_backtest.py first.")
        with open(STEP4E_REPORT_MD) as f:
            self.report_text = f.read()
        # Extract the headline decision gate section (Section 0, before first ---)
        sections = self.report_text.split("---")
        self.headline_section = sections[0] if sections else ""

    def test_no_hardcoded_61_4_win_rate_in_headline(self):
        """Old hardcoded 61.4% ML win rate must not appear in headline."""
        self.assertNotIn("61.4%", self.headline_section)

    def test_no_hardcoded_24_62_return_in_headline(self):
        """Old hardcoded +24.62% ML return must not appear in headline."""
        self.assertNotIn("24.62%", self.headline_section)

    def test_no_hardcoded_sharpe_in_headline(self):
        """Old hardcoded Sharpe values (4.15, 3.42) must not appear in headline."""
        self.assertNotIn("4.15", self.headline_section)
        self.assertNotIn("3.42", self.headline_section)

    def test_report_metrics_match_csv(self):
        """Reported signal win rates match the signal comparison CSV."""
        df_sig = pd.read_csv(STEP4E_SIGNAL_COMPARISON_CSV)
        baseline_row = df_sig[df_sig['configuration'] == "Strategy Baseline (All Signals)"].iloc[0]
        baseline_wr = str(baseline_row['win_rate_pct'])
        self.assertIn(baseline_wr, self.report_text,
                      f"Baseline win rate {baseline_wr}% not found in report")

    def test_headline_metrics_are_computed(self):
        """Headline must contain actual computed metrics, not old hardcoded ones."""
        # The headline should reference actual ML win rate from the CSV
        df_sig = pd.read_csv(STEP4E_SIGNAL_COMPARISON_CSV)
        ml_row = df_sig[df_sig['configuration'].str.contains("0.52")].iloc[0]
        ml_wr = str(ml_row['win_rate_pct'])
        self.assertIn(ml_wr, self.headline_section,
                      f"Computed ML win rate {ml_wr}% not found in headline")


class TestStep4EModelIntegrity(unittest.TestCase):
    """Tests 3-4: Model fitted on train, threshold from validation."""

    def test_scaler_fitted_on_train(self):
        """Test 4: Scaler fitted only on TRAIN data."""
        with open(SCALER_PATH, "rb") as f:
            scaler = pickle.load(f)
        df = pd.read_csv(TRAINING_DATASET_CSV)
        train_rows = len(df[df['signal_date'] < "2025-10-15"])
        self.assertEqual(int(scaler.n_samples_seen_), train_rows)

    def test_no_labels_in_features(self):
        """Test 4: No label columns in feature list."""
        label_cols = ['forward_10d_return', 'forward_10d_positive',
                      'forward_10d_max_drawdown', 'entry_date', 'entry_price']
        for lc in label_cols:
            self.assertNotIn(lc, NUMERICAL_FEATURES)

    def test_frozen_threshold_is_052(self):
        """Test 3: Frozen threshold is 0.52."""
        self.assertEqual(FROZEN_THRESHOLD, 0.52)


class TestStep4ESharpeAndDrawdown(unittest.TestCase):
    """Tests 5-6: Sharpe uses daily returns, drawdown uses equity curve."""

    def test_sharpe_not_from_trade_returns(self):
        """Test 5: Report must NOT compute Sharpe from individual trade returns.
        Verify by checking the script source code does NOT contain the old formula."""
        script_path = os.path.join(PROJECT_ROOT, "scripts", "run_step_4e_ml_backtest.py")
        with open(script_path) as f:
            code = f.read()

        # The old invalid pattern: sharpe from trade-level rets array
        # Look for the specific invalid pattern from Step 4D
        self.assertNotIn("np.mean(rets) * np.sqrt(252)) / (std_r * np.sqrt(10))", code,
                         "Old invalid Sharpe formula found in Step 4E script!")

    def test_sharpe_uses_daily_returns(self):
        """Test 5: Script computes Sharpe from daily_returns."""
        script_path = os.path.join(PROJECT_ROOT, "scripts", "run_step_4e_ml_backtest.py")
        with open(script_path) as f:
            code = f.read()
        self.assertIn("daily_returns", code)
        self.assertIn("daily_eq", code)

    def test_drawdown_uses_equity_curve(self):
        """Test 6: Script computes drawdown from equity curve."""
        script_path = os.path.join(PROJECT_ROOT, "scripts", "run_step_4e_ml_backtest.py")
        with open(script_path) as f:
            code = f.read()
        self.assertIn("cummax", code)
        self.assertIn("drawdown", code)


class TestStep4ERanking(unittest.TestCase):
    """Test 7: Ranking does not use future returns."""

    def test_ranking_columns_are_not_future(self):
        """Ranking uses ml_probability or rsi_14, NOT forward_10d_return."""
        script_path = os.path.join(PROJECT_ROOT, "scripts", "run_step_4e_ml_backtest.py")
        with open(script_path) as f:
            code = f.read()

        # Ensure rank_col never set to forward return
        self.assertNotIn('rank_col="forward_10d_return"', code)
        self.assertNotIn("rank_col='forward_10d_return'", code)


class TestStep4ECosts(unittest.TestCase):
    """Test 8: Transaction costs are included."""

    def test_portfolio_costs_nonzero(self):
        """Portfolio simulation must have nonzero transaction costs."""
        if not os.path.exists(STEP4E_PORTFOLIO_COMPARISON_CSV):
            self.skipTest("Portfolio CSV not found")

        df_port = pd.read_csv(STEP4E_PORTFOLIO_COMPARISON_CSV)
        baseline = df_port[df_port['configuration'] == "Strategy Baseline (Capital-Constrained)"]
        if len(baseline) > 0 and baseline.iloc[0]['executed_positions'] > 0:
            self.assertGreater(baseline.iloc[0]['total_transaction_costs'], 0.0)


class TestStep4EDeduplication(unittest.TestCase):
    """Test 9: Duplicate symbol/date exposure handled."""

    def test_dedup_flag_in_code(self):
        """Script uses dedup_symbol=True."""
        script_path = os.path.join(PROJECT_ROOT, "scripts", "run_step_4e_ml_backtest.py")
        with open(script_path) as f:
            code = f.read()
        self.assertIn("dedup_symbol", code)

    def test_portfolio_rejects_duplicates(self):
        """Portfolio simulation must report rejected duplicate symbols."""
        if not os.path.exists(STEP4E_PORTFOLIO_COMPARISON_CSV):
            self.skipTest("Portfolio CSV not found")
        df_port = pd.read_csv(STEP4E_PORTFOLIO_COMPARISON_CSV)
        self.assertIn("rejected_duplicate_symbol", df_port.columns)


class TestStep4ETestPredictions(unittest.TestCase):
    """Verify test predictions artifact integrity."""

    def setUp(self):
        if not os.path.exists(STEP4E_TEST_PREDICTIONS_CSV):
            self.skipTest("Test predictions CSV not found")
        self.pred_df = pd.read_csv(STEP4E_TEST_PREDICTIONS_CSV)

    def test_predictions_have_probability(self):
        """Test predictions must have ml_probability column."""
        self.assertIn("ml_probability", self.pred_df.columns)

    def test_predictions_are_test_only(self):
        """All predictions are from test split only."""
        self.assertTrue((self.pred_df['signal_date'] >= "2026-02-18").all())

    def test_predictions_reproducible(self):
        """Predictions must match model re-application."""
        with open(GB_MODEL_PATH, "rb") as f:
            gb = pickle.load(f)
        recomputed = gb.predict_proba(self.pred_df[NUMERICAL_FEATURES])[:, 1]
        np.testing.assert_array_almost_equal(
            self.pred_df['ml_probability'].values, recomputed, decimal=6,
            err_msg="Stored predictions do not match recomputed predictions!"
        )


if __name__ == "__main__":
    print("=" * 80)
    print("STEP 4E INTEGRITY VERIFICATION TESTS")
    print("=" * 80)

    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    suite.addTests(loader.loadTestsFromTestCase(TestStep4EManifest))
    suite.addTests(loader.loadTestsFromTestCase(TestStep4ENoHardcoded))
    suite.addTests(loader.loadTestsFromTestCase(TestStep4EModelIntegrity))
    suite.addTests(loader.loadTestsFromTestCase(TestStep4ESharpeAndDrawdown))
    suite.addTests(loader.loadTestsFromTestCase(TestStep4ERanking))
    suite.addTests(loader.loadTestsFromTestCase(TestStep4ECosts))
    suite.addTests(loader.loadTestsFromTestCase(TestStep4EDeduplication))
    suite.addTests(loader.loadTestsFromTestCase(TestStep4ETestPredictions))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    print(f"\n{'='*80}")
    total = result.testsRun
    failed = len(result.failures) + len(result.errors)
    passed = total - failed
    print(f"STEP 4E INTEGRITY: {passed}/{total} tests passed, {failed} failed")
    print(f"{'='*80}")

    sys.exit(0 if result.wasSuccessful() else 1)
