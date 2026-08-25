#!/usr/bin/env python3
"""Focused ROLE-R2 causal calibration contracts."""

import inspect
import unittest

import cockpit_ui
import database
import latent_state_vector as lsv
import role_opportunity_evidence as role


def row(index, *, strategy="VCP", success=False, signal="2026-01-01", observed="2026-01-15",
        origin="PROSPECTIVE", mfe=5.0, mae=-3.0, include_outcome=True):
    horizons = {}
    if include_outcome:
        horizons["10"] = {
            "observation_date": observed,
            "payload": {"plus_5_before_minus_3": {"value": success}, "mfe_pct": mfe,
                        "mae_pct": mae, "close_return_pct": mfe + mae},
        }
    return {
        "opportunity_id": f"R2:{index}", "symbol": f"S{index}", "signal_date": signal,
        "strategy": strategy, "lsv_v1": lsv.empty_vector(signal),
        "lsv_methodology_hash": lsv.LSV_METHOD_HASH, "outcome_methodology_hash": "OUTCOME",
        "methodologies": {"decision_contract": "LIVE" if origin == "PROSPECTIVE" else "HISTORICAL_CANONICAL_PAYLOAD_BACKFILL"},
        "provenance": [], "horizons": horizons,
    }


def supported_history():
    rows = []
    for index in range(20):
        is_vcp = index < 10
        success = index < 8 if is_vcp else index < 12
        rows.append(row(index, strategy="VCP" if is_vcp else "OTHER", success=success,
                        mfe=10.0 if is_vcp else 2.0, mae=-2.0 if is_vcp else -6.0,
                        origin="PROSPECTIVE" if index < 12 else "BACKFILL"))
    return rows


class RoleR2Tests(unittest.TestCase):
    def test_fixed_empirical_bayes_shrinkage(self):
        history = supported_history(); target = row(99, strategy="VCP", include_outcome=False)
        report = role.build_role_r2_report(target, history, "2026-02-01")
        # Baseline=50%, VCP cohort=80%, N=10, prior strength=20 => posterior=60%.
        self.assertEqual(report["estimated_plus_5_before_minus_3_success_pct"], 60.0)
        self.assertEqual(report["effective_comparable_sample_size"], 10)
        self.assertEqual(report["evidence_quality"], "EARLY")

    def test_causal_as_of_excludes_unmatured_outcomes(self):
        history = supported_history()
        for item in history[10:]:
            item["horizons"]["10"]["observation_date"] = "2026-03-01"
        target = row(99, strategy="VCP", signal="2026-02-01", include_outcome=False)
        early = role.build_role_r2_report(target, history, "2026-02-01")
        late = role.build_role_r2_report(target, history, "2026-04-01")
        self.assertEqual(early["system_baseline"]["mature_sample_size"], 10)
        self.assertEqual(late["system_baseline"]["mature_sample_size"], 20)

    def test_small_sample_returns_explicit_insufficient(self):
        history = [row(index, success=index % 2 == 0) for index in range(5)]
        report = role.build_role_r2_report(row(99, include_outcome=False), history, "2026-02-01")
        self.assertEqual(report["status"], "INSUFFICIENT EVIDENCE")
        self.assertIsNone(report["estimated_plus_5_before_minus_3_success_pct"])
        self.assertIn("SYSTEM_BASELINE_EVIDENCE_IS_INSUFFICIENT", report["insufficiency_reasons"])

    def test_unsupported_features_are_explicit(self):
        report = role.build_role_r2_report(row(99, include_outcome=False), supported_history(), "2026-02-01")
        unsupported = {item["driver"] for item in report["unavailable_or_unsupported_drivers"]}
        self.assertEqual(unsupported, set(lsv.FAMILIES))
        self.assertTrue(all(item["reason"] == "STATE_NOT_AVAILABLE" for item in report["unavailable_or_unsupported_drivers"]))

    def test_prospective_and_backfill_are_distinguished_without_double_counting(self):
        report = role.build_role_r2_report(row(99, include_outcome=False), supported_history(), "2026-02-01")
        self.assertEqual(report["prospective_vs_backfilled_evidence"], {"PROSPECTIVE": 12, "BACKFILL": 8})

    def test_outputs_are_deterministic_and_target_outcome_is_excluded(self):
        history = supported_history(); target = row(99, success=True, observed="2026-01-20")
        first = role.build_role_r2_report(target, [*history, target], "2026-02-01")
        target["horizons"]["10"]["payload"]["plus_5_before_minus_3"]["value"] = False
        second = role.build_role_r2_report(target, [*history, target], "2026-02-01")
        self.assertEqual(first, second)

    def test_causal_calibration_pipeline(self):
        history = supported_history()
        target = row(99, strategy="VCP", success=True, signal="2026-02-01", observed="2026-02-15", mfe=8.0, mae=-2.0)
        validation = role.validate_role_r2_calibration([*history, target], "2026-03-01")
        self.assertEqual(validation["realized_10d_opportunities"], 21)
        self.assertEqual(validation["calibrated_predictions"], 1)
        self.assertIsNotNone(validation["success_brier_score"])
        self.assertEqual(validation["validation_mode"], "CAUSAL_SIGNAL_DATE_CUTOFF")

    def test_reusable_loader_uses_persisted_rows(self):
        rows = [*supported_history(), row(99, include_outcome=False)]
        original = database.load_role_learning_rows
        database.load_role_learning_rows = lambda methodology_hash: rows
        try:
            report = role.load_role_r2_report("R2:99", "2026-02-01")
        finally:
            database.load_role_learning_rows = original
        self.assertEqual(report["opportunity_id"], "R2:99")
        self.assertEqual(report["primary_horizon_sessions"], 10)

    def test_no_recommendation_trading_or_navigation_influence(self):
        source = inspect.getsource(role)
        for forbidden in ("execute_paper_trade", "allocate_candidates", "qualification_status", "position_size"):
            self.assertNotIn(forbidden, source)
        self.assertNotIn("role_opportunity_evidence", inspect.getsource(cockpit_ui))
        self.assertNotIn("fetch_", source)
        self.assertNotIn("persist_", source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
