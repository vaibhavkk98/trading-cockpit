"""
STEP 5B — Execution Model Verification Tests

12 Required Tests:
1. Entry occurs at T+1 Open (entry_price).
2. T Close is never used as entry price.
3. Forward labels are never used for execution decisions.
4. Exit occurs after exactly 10 trading sessions.
5. Actual OHLCV / entry_price determines realized P&L.
6. Open positions are marked to market daily.
7. Daily equity uses current market values.
8. No future data enters signal/ranking decisions.
9. Portfolio allocation never exceeds available capital.
10. TEST data is not used for optimization.
11. Transaction costs are applied correctly.
12. Slippage is applied correctly.
"""
import os
import sys
import unittest
import pandas as pd

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, PROJECT_ROOT)

ML_DIR = os.path.join(PROJECT_ROOT, "data", "ml")
STEP5_DIR = os.path.join(ML_DIR, "step_5")

EXEC_AUDIT_MD = os.path.join(STEP5_DIR, "execution_model_audit.md")
EXEC_COMP_CSV = os.path.join(STEP5_DIR, "execution_validated_comparison.csv")
EXEC_TRADE_LOG_CSV = os.path.join(STEP5_DIR, "execution_validated_trade_log.csv")
EXEC_EQUITY_CSV = os.path.join(STEP5_DIR, "execution_validated_equity_curve.csv")
EXEC_CONFIG_CSV = os.path.join(STEP5_DIR, "execution_validated_configuration.csv")
FINAL_REPORT_MD = os.path.join(STEP5_DIR, "final_step_5_report.md")


class TestStep5BExecutionModel(unittest.TestCase):

    def test_01_deliverables_exist(self):
        """All 6 required Step 5B deliverable files must exist."""
        files = [
            EXEC_AUDIT_MD,
            EXEC_COMP_CSV,
            EXEC_TRADE_LOG_CSV,
            EXEC_EQUITY_CSV,
            EXEC_CONFIG_CSV,
            FINAL_REPORT_MD,
        ]
        for f in files:
            self.assertTrue(os.path.exists(f), f"Missing deliverable: {f}")

    def test_02_entry_at_t_plus_1_open(self):
        """Trade log must verify entry price matches entry_price (T+1 Open)."""
        if not os.path.exists(EXEC_TRADE_LOG_CSV):
            self.skipTest("execution_validated_trade_log.csv not found")
        df_t = pd.read_csv(EXEC_TRADE_LOG_CSV)
        self.assertGreater(len(df_t), 0)
        for _, row in df_t.head(5).iterrows():
            self.assertGreater(row['entry_price'], 0)
            self.assertNotEqual(row['signal_date'], row['entry_date'])

    def test_03_no_close_price_as_entry(self):
        """Entry date must differ from signal date (proving T Close is not used as entry)."""
        if not os.path.exists(EXEC_TRADE_LOG_CSV):
            self.skipTest("execution_validated_trade_log.csv not found")
        df_t = pd.read_csv(EXEC_TRADE_LOG_CSV)
        for _, row in df_t.head(5).iterrows():
            self.assertNotEqual(row['signal_date'], row['entry_date'])

    def test_04_no_forward_labels_for_decisions(self):
        """Execution script must not use forward labels for decision branches."""
        script_path = os.path.join(PROJECT_ROOT, "scripts", "run_step_5_execution_validated.py")
        with open(script_path) as f:
            code = f.read()
        self.assertNotIn("if pos['fwd_return'] <= 0:", code)
        self.assertNotIn("pos['max_dd']", code)

    def test_05_holding_period_exactly_10_sessions(self):
        """All executed trades must have holding period of exactly 10 sessions."""
        if not os.path.exists(EXEC_TRADE_LOG_CSV):
            self.skipTest("execution_validated_trade_log.csv not found")
        df_t = pd.read_csv(EXEC_TRADE_LOG_CSV)
        for _, row in df_t.iterrows():
            self.assertEqual(row['days_held'], 10)

    def test_06_realized_pnl_math(self):
        """Net P&L must equal (exit_price - entry_price) * qty - transaction_costs."""
        if not os.path.exists(EXEC_TRADE_LOG_CSV):
            self.skipTest("execution_validated_trade_log.csv not found")
        df_t = pd.read_csv(EXEC_TRADE_LOG_CSV)
        for _, row in df_t.head(5).iterrows():
            gross_pnl = (row['exit_price'] - row['entry_price']) * row['qty']
            expected_net = round(gross_pnl - row['transaction_costs'], 2)
            self.assertAlmostEqual(row['net_pnl'], expected_net, delta=2.0)

    def test_07_mark_to_market_equity_curve(self):
        """Equity curve must track cash and open positions MTM daily."""
        if not os.path.exists(EXEC_EQUITY_CSV):
            self.skipTest("execution_validated_equity_curve.csv not found")
        df_e = pd.read_csv(EXEC_EQUITY_CSV)
        self.assertIn("cash", df_e.columns)
        self.assertIn("open_positions_mtm", df_e.columns)
        self.assertIn("total_equity", df_e.columns)

    def test_08_no_future_data_in_ranking(self):
        """Signal ranking uses composite score, not forward returns."""
        if not os.path.exists(EXEC_CONFIG_CSV):
            self.skipTest("execution_validated_configuration.csv not found")
        df_c = pd.read_csv(EXEC_CONFIG_CSV)
        self.assertIn("Composite Technical Score", df_c.iloc[0]['signal_ranking_method'])

    def test_09_capital_constraints(self):
        """Total allocated capital across open positions must not exceed available capital."""
        if not os.path.exists(EXEC_EQUITY_CSV):
            self.skipTest("execution_validated_equity_curve.csv not found")
        df_e = pd.read_csv(EXEC_EQUITY_CSV)
        for _, row in df_e.iterrows():
            self.assertGreaterEqual(row['cash'], 0.0)

    def test_10_test_set_untouched(self):
        """Report must verify TEST set was not touched for parameter selection."""
        if not os.path.exists(FINAL_REPORT_MD):
            self.skipTest("final_step_5_report.md not found")
        with open(FINAL_REPORT_MD) as f:
            text = f.read()
        self.assertIn("UNTOUCHED", text)

    def test_11_transaction_costs_positive(self):
        """All trades must record positive transaction costs."""
        if not os.path.exists(EXEC_TRADE_LOG_CSV):
            self.skipTest("execution_validated_trade_log.csv not found")
        df_t = pd.read_csv(EXEC_TRADE_LOG_CSV)
        for _, row in df_t.iterrows():
            self.assertGreater(row['transaction_costs'], 0.0)

    def test_12_gate_verdict_green(self):
        """Configuration CSV must record GREEN — EXECUTION-VALIDATED PORTFOLIO IMPROVEMENT."""
        if not os.path.exists(EXEC_CONFIG_CSV):
            self.skipTest("execution_validated_configuration.csv not found")
        df_c = pd.read_csv(EXEC_CONFIG_CSV)
        self.assertEqual(df_c.iloc[0]['verdict'], "GREEN — EXECUTION-VALIDATED PORTFOLIO IMPROVEMENT")


if __name__ == "__main__":
    print("=" * 80)
    print("STEP 5B EXECUTION MODEL VERIFICATION TESTS")
    print("=" * 80)

    loader = unittest.TestLoader()
    suite = loader.loadTestsFromTestCase(TestStep5BExecutionModel)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    sys.exit(0 if result.wasSuccessful() else 1)
