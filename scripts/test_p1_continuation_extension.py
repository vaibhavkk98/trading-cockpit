import os
import sys
import hashlib
import unittest
import numpy as np
import pandas as pd

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..")); sys.path.insert(0, ROOT)
from scripts.run_p1_continuation_extension import enrich_bars, build_p1_dataset

MVP_FILES = ["scripts/run_mvp.py", "config/mvp_config.yaml", "data/ml/step_10c/step_10c_comparison.csv"]
def digest(path):
    with open(os.path.join(ROOT, path), "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()

class TestP1ContinuationExtension(unittest.TestCase):
    def test_01_features_are_causal(self):
        close = np.arange(100., 131.); vol = np.array([100.] * 29 + [1000., 2000.])
        raw = pd.DataFrame({"Open": close, "High": close + 1, "Low": close - 1, "Close": close, "Volume": vol})
        a = enrich_bars(raw); altered = raw.copy(); altered.loc[30, "Close"] = 9999; altered.loc[30, "Volume"] = 99999
        b = enrich_bars(altered)
        for col in ["ema20", "atr20", "volume_20d_avg", "volume_ratio_20", "return_5d"]:
            self.assertAlmostEqual(float(a.loc[29, col]), float(b.loc[29, col]), places=8)

    def test_02_outcomes_do_not_enter_features(self):
        df = build_p1_dataset()
        self.assertTrue(all(not c.startswith("forward_") and c not in {"mfe_10d", "mae_10d"} for c in ["return_1d", "distance_from_ema20_atr", "volume_ratio_20"]))
        self.assertTrue((df.volume_ratio_20 >= 2).all()); self.assertTrue((df.close > df.ema20).all())

    def test_03_signal_count_reconciles_and_protected_files_unchanged(self):
        before = {p: digest(p) for p in MVP_FILES}; df = build_p1_dataset(); after = {p: digest(p) for p in MVP_FILES}
        self.assertEqual(before, after); self.assertEqual(len(df), len(df.dropna(subset=["signal_date", "symbol", "strategy_name"]))); self.assertGreater(len(df), 0)

if __name__ == "__main__": unittest.main()
