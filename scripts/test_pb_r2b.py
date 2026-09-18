#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pb_native_features import FEATURES, _outcomes, historical_security_state, live_security_state


class PBR2BContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.out = ROOT/"data/research/predictability/pb_r2b"
        cls.data = pd.read_parquet(cls.out/"production_native_dataset.parquet")
        cls.result = json.loads((cls.out/"pb_r2b_results.json").read_text())

    def test_manifest_is_complete_and_single_builder_is_shared(self):
        manifest = json.loads((self.out/"production_native_feature_manifest.json").read_text())
        self.assertEqual(manifest["feature_count"], 35)
        self.assertEqual(tuple(row["new_name"] for row in manifest["features"]), FEATURES)
        self.assertIs(historical_security_state, live_security_state)
        self.assertTrue(all(row["historical_reconstruction_function"] == "pb_native_features.build_dataset" for row in manifest["features"]))

    def test_population_contract_and_causal_cutoff(self):
        self.assertGreaterEqual(len(self.data), 5_000)
        self.assertTrue(self.data.positive_price_gate_pass.all())
        self.assertTrue((pd.to_datetime(self.data.feature_source_max_date) <= pd.to_datetime(self.data.signal_date)).all())
        self.assertFalse(self.data.duplicated(["canonical_security_id", "signal_date"]).any())
        self.assertTrue(set(FEATURES).issubset(self.data.columns))

    def test_cross_section_is_same_date_native_qualified_set(self):
        expected = self.data.groupby("signal_date").native_excess_20d.rank(pct=True, method="average")*100
        pd.testing.assert_series_equal(expected.reset_index(drop=True), self.data.native_rs_percentile_20d.reset_index(drop=True), check_names=False)
        expected_liquidity = self.data.groupby("signal_date").native_log_traded_value.rank(pct=True, method="average")*100
        pd.testing.assert_series_equal(expected_liquidity.reset_index(drop=True), self.data.native_traded_value_percentile.reset_index(drop=True), check_names=False)

    def test_corrected_barrier_tie_and_neither(self):
        base = pd.DataFrame({"trade_date": pd.date_range("2026-01-01", periods=21, freq="D"), "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0})
        tie = base.copy(); tie.loc[1, ["high", "low"]] = [106.0, 96.0]
        out = _outcomes(tie, np.array([0]), set())
        self.assertEqual(out["success_5_before_3_5d"][0], 0.0)
        self.assertEqual(out["adverse_first_5d"][0], 1.0)
        neither = _outcomes(base, np.array([0]), set())
        self.assertEqual(neither["success_5_before_3_5d"][0], 0.0)
        self.assertEqual(neither["adverse_first_5d"][0], 0.0)

    def test_corporate_action_outcome_is_excluded(self):
        frame = pd.DataFrame({"trade_date": pd.date_range("2026-01-01", periods=21, freq="D"), "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0})
        out = _outcomes(frame, np.array([0]), {pd.Timestamp("2026-01-03")})
        self.assertTrue(np.isnan(out["mfe_5d"][0]))

    def test_purged_chronological_contract(self):
        locked = pd.read_parquet(self.out/"locked_chronological_predictions.parquet")
        self.assertTrue(locked.signal_date.dt.year.isin([2024, 2025, 2026]).all())
        self.assertEqual(set(locked.horizon), {5, 10, 20})
        self.assertEqual(set(locked.target), {"mfe", "mae", "forward_volatility", "success_5_before_3", "adverse_first"})

    def test_no_go_is_fail_closed(self):
        self.assertEqual(self.result["verdict"], "NO-GO — PRODUCTION-NATIVE PB DOES NOT RETAIN SUFFICIENT PREDICTIVE VALUE")
        self.assertEqual(self.result["activation"]["status"], "INACTIVE")
        self.assertFalse(self.result["activation"]["decision_authority"])
        self.assertFalse((ROOT/"data/production/predictability/pb_r2b/pb_r2b_manifest.json").exists())

    def test_old_frozen_identities_unchanged(self):
        manifest = json.loads((ROOT/"data/production/predictability/pb_p1_manifest.json").read_text())
        self.assertEqual(manifest["pb_r2_methodology_hash"], "cd82dc5e6a354445506306a1a98c54de8f7287b248e89bf3f6044342d68b8e27")
        self.assertEqual(manifest["inference_artifact"]["sha256"], "5d06576fb89ea3d9d6619d07dbf6b08fc5a07e88ad4456fb623b5d7cf53ea0fd")
        self.assertEqual(manifest["pb_r3_methodology_hash"], "7534d1bc782c12e03bf726dcb45a5cd104693c8263c6446f80785fe6638eaf9c")
        artifact = ROOT/manifest["inference_artifact"]["path"]
        self.assertEqual(hashlib.sha256(artifact.read_bytes()).hexdigest(), manifest["inference_artifact"]["sha256"])

    def test_autopaper_holdout_and_authority_guards(self):
        self.assertFalse(self.result["autopaper_holdout_opened"])
        self.assertEqual(self.result["baseline_fingerprint_expected"], "b93e8c2dd1a99fba712f89a38ba3a5689595891a47375e5c4f20a31d567d3640")


if __name__ == "__main__":
    unittest.main(verbosity=2)
