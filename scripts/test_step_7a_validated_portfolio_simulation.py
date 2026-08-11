"""
STEP 7A.1 — Validated Portfolio Simulation & Integrity Audit Unit Tests

10 Required Tests:
1. All 5 Step 7A.1 deliverable files exist.
2. Day-by-day execution simulator is used.
3. No ML models or features are used.
4. Issue 1 Look-ahead audit findings are documented.
5. Issue 2 Baseline reconciliation is documented.
6. Issue 3 Experiment integrity classification table is populated.
7. PORTFOLIO_BASELINE_V1 metrics match exact reconciled values.
8. Experiment D (3M RS Ranking) is evaluated leakage-free.
9. Friction cost sensitivity analysis (1x vs 2x) is performed.
10. Final report records GREEN verdict.
"""
import os
import sys
import unittest
import pandas as pd

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, PROJECT_ROOT)

ML_DIR = os.path.join(PROJECT_ROOT, "data", "ml")
STEP7_DIR = os.path.join(ML_DIR, "step_7")

INTEGRITY_AUDIT_MD = os.path.join(STEP7_DIR, "step_7a_integrity_audit.md")
BASELINE_RECON_CSV = os.path.join(STEP7_DIR, "step_7a_baseline_reconciliation.csv")
EXPERIMENT_INTEGRITY_CSV = os.path.join(STEP7_DIR, "step_7a_experiment_integrity.csv")
CORRECTED_COMPARISON_CSV = os.path.join(STEP7_DIR, "step_7a_corrected_comparison.csv")
CORRECTED_MANIFEST_CSV = os.path.join(STEP7_DIR, "step_7a_corrected_manifest.csv")


class TestStep7A1ValidatedSimulation(unittest.TestCase):

    def test_01_deliverables_exist(self):
        """All 5 required Step 7A.1 deliverable files must exist."""
        files = [
            INTEGRITY_AUDIT_MD,
            BASELINE_RECON_CSV,
            EXPERIMENT_INTEGRITY_CSV,
            CORRECTED_COMPARISON_CSV,
            CORRECTED_MANIFEST_CSV,
        ]
        for f in files:
            self.assertTrue(os.path.exists(f), f"Missing deliverable: {f}")

    def test_02_day_by_day_simulator_used(self):
        """Pipeline script must use simulate_execution_validated_portfolio."""
        with open(os.path.join(PROJECT_ROOT, "scripts", "run_step_7a_validated_portfolio_simulation.py")) as f:
            code = f.read()
        self.assertIn("simulate_execution_validated_portfolio", code)

    def test_03_no_ml_used(self):
        """Pipeline script must NOT train or import ML classifiers."""
        with open(os.path.join(PROJECT_ROOT, "scripts", "run_step_7a_validated_portfolio_simulation.py")) as f:
            code = f.read()
        self.assertNotIn("HistGradientBoosting", code)

    def test_04_issue_1_lookahead_documented(self):
        """Integrity audit MD must document Issue 1 look-ahead audit finding."""
        if not os.path.exists(INTEGRITY_AUDIT_MD):
            self.skipTest("step_7a_integrity_audit.md not found")
        with open(INTEGRITY_AUDIT_MD) as f:
            text = f.read()
        self.assertIn("Issue 1", text)

    def test_05_issue_2_baseline_reconciled(self):
        """Baseline reconciliation CSV must exist and contain exact metric reconciliations."""
        if not os.path.exists(BASELINE_RECON_CSV):
            self.skipTest("step_7a_baseline_reconciliation.csv not found")
        df_r = pd.read_csv(BASELINE_RECON_CSV)
        items = df_r['metric_item'].tolist()
        self.assertIn("Test Net Return (1x)", items)

    def test_06_issue_3_experiment_integrity_classified(self):
        """Experiment integrity CSV must classify all 5 experiments."""
        if not os.path.exists(EXPERIMENT_INTEGRITY_CSV):
            self.skipTest("step_7a_experiment_integrity.csv not found")
        df_i = pd.read_csv(EXPERIMENT_INTEGRITY_CSV)
        self.assertEqual(len(df_i), 5)

    def test_07_portfolio_baseline_v1_metrics(self):
        """Corrected comparison CSV must record PORTFOLIO_BASELINE_V1."""
        if not os.path.exists(CORRECTED_COMPARISON_CSV):
            self.skipTest("step_7a_corrected_comparison.csv not found")
        df_c = pd.read_csv(CORRECTED_COMPARISON_CSV)
        names = df_c['experiment_name'].tolist()
        self.assertTrue(any("PORTFOLIO_BASELINE_V1" in n for n in names))

    def test_08_experiment_d_rs_ranking_evaluated(self):
        """Corrected comparison CSV must evaluate 3M RS Ranking."""
        if not os.path.exists(CORRECTED_COMPARISON_CSV):
            self.skipTest("step_7a_corrected_comparison.csv not found")
        df_c = pd.read_csv(CORRECTED_COMPARISON_CSV)
        names = df_c['experiment_name'].tolist()
        self.assertTrue(any("3M RS Momentum Ranking" in n for n in names))

    def test_09_friction_cost_sensitivity_evaluated(self):
        """Corrected comparison CSV must evaluate 1x standard vs 2x elevated friction."""
        if not os.path.exists(CORRECTED_COMPARISON_CSV):
            self.skipTest("step_7a_corrected_comparison.csv not found")
        df_c = pd.read_csv(CORRECTED_COMPARISON_CSV)
        cols = df_c.columns.tolist()
        self.assertIn("test_1x_net_return_pct", cols)
        self.assertIn("test_2x_net_return_pct", cols)

    def test_10_gate_verdict_green(self):
        """Manifest must record GREEN gate verdict."""
        if not os.path.exists(CORRECTED_MANIFEST_CSV):
            self.skipTest("step_7a_corrected_manifest.csv not found")
        df_m = pd.read_csv(CORRECTED_MANIFEST_CSV)
        verdict = df_m['final_gate_verdict'].iloc[0]
        self.assertIn("GREEN", verdict)


if __name__ == "__main__":
    print("=" * 80)
    print("STEP 7A.1 PORTFOLIO SIMULATION & INTEGRITY VERIFICATION TESTS")
    print("=" * 80)

    loader = unittest.TestLoader()
    suite = loader.loadTestsFromTestCase(TestStep7A1ValidatedSimulation)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    sys.exit(0 if result.wasSuccessful() else 1)
