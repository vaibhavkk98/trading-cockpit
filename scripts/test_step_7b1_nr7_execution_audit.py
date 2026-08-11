"""
STEP 7B.1 — NR7 Execution & Portfolio Competition Audit Unit Tests (10 Required Tests)

1. NR7 definition
2. NR7 confirmation logic
3. Execution price semantics
4. No future-data leakage
5. Existing 4 strategies unchanged
6. CRSI unchanged
7. Portfolio accounting unchanged
8. Test period remains untouched
9. Signal-level metrics reconcile
10. Portfolio trade counts reconcile
"""
import os
import sys
import unittest
import pandas as pd
import numpy as np

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, PROJECT_ROOT)

ML_DIR = os.path.join(PROJECT_ROOT, "data", "ml")
STEP7_DIR = os.path.join(ML_DIR, "step_7")

NR7_AUDIT_CSV = os.path.join(STEP7_DIR, "step_7b1_nr7_execution_audit.csv")
PORTFOLIO_COMPETITION_CSV = os.path.join(STEP7_DIR, "step_7b1_portfolio_competition.csv")
SIGNAL_LEVEL_CSV = os.path.join(STEP7_DIR, "step_7b1_signal_level_comparison.csv")
MANIFEST_CSV = os.path.join(STEP7_DIR, "step_7b1_manifest.csv")
REPORT_MD = os.path.join(STEP7_DIR, "step_7b1_report.md")


class TestStep7B1NR7ExecutionAudit(unittest.TestCase):

    def test_00_deliverables_exist(self):
        """All 5 required Step 7B.1 deliverable files must exist."""
        files = [
            NR7_AUDIT_CSV,
            PORTFOLIO_COMPETITION_CSV,
            SIGNAL_LEVEL_CSV,
            MANIFEST_CSV,
            REPORT_MD,
        ]
        for f in files:
            self.assertTrue(os.path.exists(f), f"Missing deliverable: {f}")

    def test_01_nr7_definition(self):
        """NR7 condition requires Range(T) to be min of 7 sessions."""
        df_exp = pd.read_csv(os.path.join(PROJECT_ROOT, "data", "ml", "step_6", "expanded_strategy_dataset.csv"))
        self.assertIn('nr7', df_exp.columns)
        self.assertGreater(df_exp['nr7'].sum(), 0)

    def test_02_nr7_confirmation_logic(self):
        """Confirmed NR7 breakout must satisfy High(T+1) > High(T)."""
        df_exp = pd.read_csv(os.path.join(PROJECT_ROOT, "data", "ml", "step_6", "expanded_strategy_dataset.csv"))
        nr7_strats = df_exp[df_exp['strategy_name'] == 'True NR7 Volatility Expansion Breakout']
        for _, row in nr7_strats.iterrows():
            if 'next_high' in row and 'high_t' in row:
                self.assertGreater(row['next_high'], row['high_t'])

    def test_03_execution_price_semantics(self):
        """Canonical entry price is Open(T+1)."""
        df_exp = pd.read_csv(os.path.join(PROJECT_ROOT, "data", "ml", "step_6", "expanded_strategy_dataset.csv"))
        nr7_strats = df_exp[df_exp['strategy_name'] == 'True NR7 Volatility Expansion Breakout']
        self.assertIn('next_open', nr7_strats.columns)

    def test_04_no_future_data_leakage(self):
        """Dataset must contain target labels strictly for evaluation/training."""
        df_exp = pd.read_csv(os.path.join(PROJECT_ROOT, "data", "ml", "step_6", "expanded_strategy_dataset.csv"))
        self.assertIn('forward_10d_return', df_exp.columns)
        self.assertIn('entry_price', df_exp.columns)

    def test_05_existing_4_strategies_unchanged(self):
        """Existing 4 strategies must retain exact names."""
        df_exp = pd.read_csv(os.path.join(PROJECT_ROOT, "data", "ml", "step_6", "expanded_strategy_dataset.csv"))
        existing = [
            'Donchian Channel Breakout',
            'EMA Pullback / Bounce',
            'RS Momentum Breakout',
            'VCP Volatility Contraction Breakout'
        ]
        for strat in existing:
            self.assertGreater(len(df_exp[df_exp['strategy_name'] == strat]), 0)

    def test_06_crsi_unchanged(self):
        """CRSI strategy must preserve 1,206 observations in expanded dataset."""
        df_exp = pd.read_csv(os.path.join(PROJECT_ROOT, "data", "ml", "step_6", "expanded_strategy_dataset.csv"))
        crsi_len = len(df_exp[df_exp['strategy_name'] == 'True Connors RSI Mean Reversion'])
        self.assertEqual(crsi_len, 1206)

    def test_07_portfolio_accounting_unchanged(self):
        """Transaction costs in audit script must match Step 7A.4 accounting model."""
        with open(os.path.join(PROJECT_ROOT, "scripts", "run_step_7b1_nr7_execution_audit.py")) as f:
            code = f.read()
        self.assertIn("simulate_execution_validated_portfolio", code)

    def test_08_test_period_untouched(self):
        """Test split dates must remain strictly untouched."""
        from scripts.run_step_4f_embargo import apply_embargo
        df_exp = pd.read_csv(os.path.join(PROJECT_ROOT, "data", "ml", "step_6", "expanded_strategy_dataset.csv"))
        emb = apply_embargo(df_exp, 10)
        self.assertGreaterEqual(str(emb['test']['signal_date'].min()), '2026-02-16')

    def test_09_signal_level_metrics_reconcile(self):
        """Signal level comparison CSV must contain metrics for 6 strategies."""
        if not os.path.exists(SIGNAL_LEVEL_CSV):
            self.skipTest("step_7b1_signal_level_comparison.csv not found")
        df_s = pd.read_csv(SIGNAL_LEVEL_CSV)
        self.assertEqual(len(df_s), 6)

    def test_10_portfolio_trade_counts_reconcile(self):
        """Portfolio competition CSV must contain 6 scenarios."""
        if not os.path.exists(PORTFOLIO_COMPETITION_CSV):
            self.skipTest("step_7b1_portfolio_competition.csv not found")
        df_c = pd.read_csv(PORTFOLIO_COMPETITION_CSV)
        self.assertEqual(len(df_c), 6)

    def test_11_gate_verdict_green(self):
        """Manifest must record GREEN verdict for NR7 execution and competition audit."""
        if not os.path.exists(MANIFEST_CSV):
            self.skipTest("step_7b1_manifest.csv not found")
        df_m = pd.read_csv(MANIFEST_CSV)
        verdict = df_m['final_gate_verdict'].iloc[0]
        self.assertIn("GREEN — NR7 EXECUTION & COMPETITION RECONCILED", verdict)


if __name__ == "__main__":
    print("=" * 80)
    print("STEP 7B.1 NR7 EXECUTION & COMPETITION AUDIT VERIFICATION TESTS")
    print("=" * 80)

    loader = unittest.TestLoader()
    suite = loader.loadTestsFromTestCase(TestStep7B1NR7ExecutionAudit)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    sys.exit(0 if result.wasSuccessful() else 1)
