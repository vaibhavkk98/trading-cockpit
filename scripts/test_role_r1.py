#!/usr/bin/env python3
"""Focused ROLE-R1 live analytics contracts."""

import inspect
import os
from pathlib import Path
import tempfile
import unittest

_TEMP = tempfile.TemporaryDirectory()
os.environ.pop("DATABASE_URL", None)
os.environ["TRADING_COCKPIT_DB_PATH"] = str(Path(_TEMP.name) / "role-r1.db")

import database
import latent_state_vector as lsv
import role_learning_analytics as role


def outcome(close_return, mfe, mae, success, observation_date):
    return {
        "observation_date": observation_date,
        "payload": {
            "close_return_pct": close_return, "mfe_pct": mfe, "mae_pct": mae,
            "plus_5_before_minus_3": {"value": success},
        },
    }


def record(index, strategy="VCP", prospective=True, horizons=(5, 10), observation_date="2026-02-01"):
    vector = lsv.empty_vector("2026-01-02")
    vector["price_state"]["return_20d_pct"] = -8 + index * 4
    vector["participation"]["volume_ratio_20d"] = 0.7 + index * 0.2
    vector["relative_demand"]["cross_sectional_rs_percentile"] = 20 + index * 20
    vector["volatility_state"]["short_long_volatility_ratio"] = 0.7 + index * 0.2
    vector["price_response"]["close_location_value"] = -0.6 + index * 0.3
    vector["liquidity"]["traded_value_percentile_252d"] = 20 + index * 20
    vector["environment"]["breadth"] = "SUPPORTIVE" if index % 2 else "WEAK"
    return {
        "opportunity_id": f"R{index}", "signal_date": "2026-01-02", "strategy": strategy,
        "lsv_methodology_hash": lsv.LSV_METHOD_HASH, "outcome_methodology_hash": "OUTCOME",
        "lsv_v1": vector, "methodologies": {"decision_contract": "LIVE" if prospective else "HISTORICAL_CANONICAL_PAYLOAD_BACKFILL"},
        "provenance": [], "horizons": {
            str(horizon): outcome(index, index + horizon, -index, index % 2 == 0, observation_date)
            for horizon in horizons
        },
    }


class RoleR1Tests(unittest.TestCase):
    def test_correct_baseline_and_strategy_cohort_calculations(self):
        rows = [record(index, "VCP" if index < 3 else "EMA") for index in range(5)]
        result = role.build_role_r1_analytics(rows, "2026-02-01")
        self.assertEqual(result["baseline"]["mature_sample_size"], 5)
        self.assertEqual(result["baseline"]["plus_5_before_minus_3_success_pct"], 60.0)
        self.assertEqual(result["baseline"]["median_close_return_pct"], 2.0)
        vcp = next(item for item in result["strategy_cohorts"] if item["cohort"] == "VCP")
        self.assertEqual(vcp["mature_sample_size"], 3)
        self.assertAlmostEqual(vcp["difference_vs_system_baseline"]["median_close_return_pct_difference"], -1.0)

    def test_no_future_leakage(self):
        row = record(1, observation_date="2026-02-01")
        before = role.build_role_r1_analytics([row], "2026-01-31")
        after = role.build_role_r1_analytics([row], "2026-02-01")
        self.assertEqual(before["baseline"]["mature_sample_size"], 0)
        self.assertEqual(after["baseline"]["mature_sample_size"], 1)

    def test_primary_maturity_gate_and_secondary_context(self):
        five_only = record(1, horizons=(5,))
        result = role.build_role_r1_analytics([five_only], "2026-02-01")
        self.assertEqual(result["baseline"]["mature_sample_size"], 0)
        self.assertEqual(result["baseline"]["secondary_context"]["5d"]["mature_sample_size"], 1)

    def test_evidence_quality_is_fixed_and_conservative(self):
        self.assertEqual(role.evidence_quality(9, 100), "INSUFFICIENT")
        self.assertEqual(role.evidence_quality(20, 100), "EARLY")
        self.assertEqual(role.evidence_quality(50, 100), "DEVELOPING")
        self.assertEqual(role.evidence_quality(120, 95), "STRONG")
        self.assertEqual(role.evidence_quality(120, 40), "INSUFFICIENT")

    def test_low_outcome_maturity_caps_baseline_and_cohorts(self):
        rows = [record(index) for index in range(10)]
        rows.extend({**record(index + 20, horizons=()), "opportunity_id": f"P{index}"} for index in range(20))
        result = role.build_role_r1_analytics(rows, "2026-02-01")
        self.assertLess(result["baseline"]["data_coverage"]["outcome_10d_pct"], 50.0)
        self.assertEqual(result["baseline"]["evidence_quality"], "INSUFFICIENT")
        self.assertTrue(all(item["evidence_quality"] == "INSUFFICIENT" for item in result["strategy_cohorts"]))

    def test_prospective_and_backfill_are_separate(self):
        result = role.build_role_r1_analytics([record(1, prospective=True), record(2, prospective=False)], "2026-02-01")
        self.assertEqual(result["baseline"]["data_coverage"]["origin_10d_counts"], {"PROSPECTIVE": 1, "BACKFILL": 1})
        self.assertEqual(result["origin_baselines"]["PROSPECTIVE"]["mature_sample_size"], 1)
        self.assertEqual(result["origin_baselines"]["BACKFILL"]["mature_sample_size"], 1)

    def test_all_families_and_only_predefined_interactions(self):
        result = role.build_role_r1_analytics([record(1)], "2026-02-01")
        self.assertEqual(set(result["family_cohorts"]), set(lsv.FAMILIES))
        self.assertEqual(set(result["interaction_cohorts"]), set(role.INTERACTIONS))
        self.assertEqual(result["family_cohorts"]["positioning"]["eligible_10d"], 0)

    def test_outputs_are_deterministic(self):
        rows = [record(1), record(2)]
        self.assertEqual(role.build_role_r1_analytics(rows, "2026-02-01"), role.build_role_r1_analytics(rows, "2026-02-01"))

    def test_live_service_reads_existing_immutable_contracts(self):
        vector = record(7)["lsv_v1"]
        snapshot = {
            "opportunity_id": "ROLE-R1:DB", "signal_date": "2026-01-02",
            "signal_timestamp": "2026-01-02T11:00:00+00:00", "symbol": "ABC", "strategy": "VCP",
            "reference_price": 100.0, "lsv_v1": vector, "historical_analog": {}, "market_context": {},
            "methodologies": {"decision_contract": "LIVE"}, "source_timestamps": {},
            "missingness": vector["missingness"], "provenance": [],
        }
        database.persist_recommendation_snapshot(snapshot)
        header = database.persist_role_observation_state({
            "opportunity_id": "ROLE-R1:DB", "lsv_methodology_hash": lsv.LSV_METHOD_HASH,
            "signal_date": "2026-01-02", "reference_price": 100.0,
            "outcome_contract_version": "ROLE_D1_OUTCOME_V1", "outcome_methodology_hash": role.OUTCOME_METHOD_HASH,
            "lifecycle_state": "PARTIAL", "sessions_observed": 10, "last_observation_date": "2026-01-16",
            "source": {}, "completeness": {}, "missingness": [],
        })
        database.persist_role_outcome_horizon(header["observation_id"], 10, "2026-01-16", outcome(2, 6, -2, True, "2026-01-16")["payload"])
        result = role.load_live_role_r1_analytics("2026-02-01")
        self.assertEqual(result["recommendation_count"], 1)
        self.assertEqual(result["baseline"]["mature_sample_size"], 1)

    def test_no_production_decision_influence(self):
        source = inspect.getsource(role)
        for forbidden in ("execute_paper_trade", "allocate_candidates", "qualification_status", "FAVORABLE", "CAUTION"):
            self.assertNotIn(forbidden, source)
        self.assertTrue(role.build_role_r1_analytics([], "2026-02-01")["advisory_only"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
