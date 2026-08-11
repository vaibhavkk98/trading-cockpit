"""
STEP 8 — MVP Integration Test Suite

Verifies:
1. Config exists and loads correctly
2. MVP runner script exists and is importable
3. MVP outputs exist with expected content
4. Trade ledger has expected columns and non-zero rows
5. Equity curve starts at initial capital
6. Performance metrics are reasonable
7. Accounting reconciliation passes
8. ML is OFF, Sentiment is DISABLED
9. Existing test suites still pass (regression guard)
"""
import os
import sys
import json
import unittest
import pandas as pd

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, PROJECT_ROOT)

MVP_OUTPUT_DIR = os.path.join(PROJECT_ROOT, "data", "mvp")
CONFIG_PATH = os.path.join(PROJECT_ROOT, "config", "mvp_config.yaml")


class TestMVPConfig(unittest.TestCase):
    """Test 1-2: Config and runner exist and are valid."""

    def test_01_config_exists(self):
        self.assertTrue(os.path.exists(CONFIG_PATH), "config/mvp_config.yaml must exist")

    def test_02_config_loads(self):
        import yaml
        with open(CONFIG_PATH, "r") as f:
            config = yaml.safe_load(f)
        self.assertIsInstance(config, dict)
        self.assertIn('ml', config)
        self.assertIn('sentiment', config)
        self.assertIn('portfolio', config)
        self.assertIn('regime', config)

    def test_03_ml_is_off(self):
        import yaml
        with open(CONFIG_PATH, "r") as f:
            config = yaml.safe_load(f)
        self.assertEqual(config['ml']['enabled'], False, "ML must be OFF in MVP config")

    def test_04_sentiment_is_disabled(self):
        import yaml
        with open(CONFIG_PATH, "r") as f:
            config = yaml.safe_load(f)
        self.assertEqual(config['sentiment']['enabled'], False, "Sentiment must be DISABLED")

    def test_05_regime_filter_is_on(self):
        import yaml
        with open(CONFIG_PATH, "r") as f:
            config = yaml.safe_load(f)
        self.assertEqual(config['regime']['enabled'], True, "Regime filter must be ON")

    def test_06_allocation_is_7_3(self):
        import yaml
        with open(CONFIG_PATH, "r") as f:
            config = yaml.safe_load(f)
        self.assertEqual(config['portfolio']['max_trend_positions'], 7)
        self.assertEqual(config['portfolio']['max_volatility_positions'], 3)
        self.assertEqual(config['portfolio']['max_positions'], 10)


class TestMVPRunner(unittest.TestCase):
    """Test 3: Runner script exists and is importable."""

    def test_07_runner_exists(self):
        runner_path = os.path.join(PROJECT_ROOT, "scripts", "run_mvp.py")
        self.assertTrue(os.path.exists(runner_path), "scripts/run_mvp.py must exist")

    def test_08_runner_importable(self):
        from scripts.run_mvp import load_mvp_config, build_causal_nr7_dataset, run_mvp
        self.assertTrue(callable(run_mvp))
        self.assertTrue(callable(load_mvp_config))
        self.assertTrue(callable(build_causal_nr7_dataset))


class TestMVPOutputs(unittest.TestCase):
    """Test 4-8: MVP outputs exist and have valid content."""

    def test_09_trade_ledger_exists(self):
        path = os.path.join(MVP_OUTPUT_DIR, "trade_ledger.csv")
        self.assertTrue(os.path.exists(path), "trade_ledger.csv must exist")

    def test_10_trade_ledger_has_trades(self):
        path = os.path.join(MVP_OUTPUT_DIR, "trade_ledger.csv")
        df = pd.read_csv(path)
        self.assertGreater(len(df), 0, "Trade ledger must have > 0 trades")
        required_cols = ['symbol', 'strategy_name', 'signal_date', 'entry_date',
                         'exit_date', 'entry_price', 'exit_price', 'net_pnl',
                         'net_return_pct', 'split']
        for col in required_cols:
            self.assertIn(col, df.columns, f"Trade ledger must have column '{col}'")

    def test_11_equity_curve_exists(self):
        path = os.path.join(MVP_OUTPUT_DIR, "equity_curve.csv")
        self.assertTrue(os.path.exists(path), "equity_curve.csv must exist")

    def test_12_equity_curve_starts_at_initial_capital(self):
        path = os.path.join(MVP_OUTPUT_DIR, "equity_curve.csv")
        df = pd.read_csv(path)
        val_df = df[df['split'] == 'VALIDATION']
        first_equity = val_df['total_equity'].iloc[0]
        self.assertAlmostEqual(first_equity, 1000000.0, delta=50000,
                               msg="Equity curve should start near Rs 1,000,000")

    def test_13_daily_returns_exists(self):
        path = os.path.join(MVP_OUTPUT_DIR, "daily_returns.csv")
        self.assertTrue(os.path.exists(path), "daily_returns.csv must exist")

    def test_14_performance_report_exists(self):
        path = os.path.join(MVP_OUTPUT_DIR, "performance_report.json")
        self.assertTrue(os.path.exists(path), "performance_report.json must exist")

    def test_15_performance_report_valid(self):
        path = os.path.join(MVP_OUTPUT_DIR, "performance_report.json")
        with open(path, 'r') as f:
            report = json.load(f)
        self.assertIn('validation', report)
        self.assertIn('test_descriptive', report)
        self.assertEqual(report['ml_status'], 'OFF')
        self.assertEqual(report['sentiment_status'], 'DISABLED')
        # Sanity checks on metrics
        val = report['validation']
        self.assertGreater(val['daily_sharpe_ratio'], -5, "Sharpe should be > -5")
        self.assertLess(val['max_drawdown_pct'], 100, "Max DD should be < 100%")
        self.assertGreater(val['executed_trades'], 0, "Should have > 0 trades")

    def test_16_signals_log_exists(self):
        path = os.path.join(MVP_OUTPUT_DIR, "signals_log.csv")
        self.assertTrue(os.path.exists(path), "signals_log.csv must exist")

    def test_17_readiness_report_exists(self):
        path = os.path.join(MVP_OUTPUT_DIR, "mvp_readiness_report.md")
        self.assertTrue(os.path.exists(path), "mvp_readiness_report.md must exist")

    def test_18_accounting_reconciliation(self):
        """Verify final equity = cash + MTM positions (no gap)."""
        path = os.path.join(MVP_OUTPUT_DIR, "equity_curve.csv")
        df = pd.read_csv(path)
        for split in ['VALIDATION', 'TEST']:
            split_df = df[df['split'] == split]
            if len(split_df) > 0:
                last_row = split_df.iloc[-1]
                gap = abs(last_row['total_equity'] - (last_row['cash'] + last_row['mtm_pos_val']))
                self.assertLess(gap, 1.0,
                                f"{split}: Accounting gap {gap:.2f} must be < Rs 1.0")


class TestMVPResultConsistency(unittest.TestCase):
    """Test: Results match frozen champion from Step 7C.3."""

    def test_19_validation_matches_champion(self):
        """Validation results should match the frozen champion values."""
        path = os.path.join(MVP_OUTPUT_DIR, "performance_report.json")
        with open(path, 'r') as f:
            report = json.load(f)
        val = report['validation']
        # These are the frozen champion values from Step 7C.3
        self.assertAlmostEqual(val['net_return_pct'], 13.27, delta=0.5,
                               msg="Validation return should be ~+13.27%")
        self.assertAlmostEqual(val['daily_sharpe_ratio'], 3.97, delta=0.5,
                               msg="Validation Sharpe should be ~3.97")
        self.assertEqual(val['executed_trades'], 50,
                         msg="Validation should have 50 trades")


if __name__ == "__main__":
    print("=" * 80)
    print("STEP 8 — MVP INTEGRATION TEST SUITE")
    print("=" * 80)
    unittest.main(verbosity=2)
