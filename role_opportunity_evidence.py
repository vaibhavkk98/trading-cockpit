"""ROLE-R2 transparent per-opportunity calibration; read-only and advisory."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
from statistics import mean
from typing import Any, Iterable, Mapping

import database
from role_learning_analytics import (
    FAMILY_ANCHORS, NOT_AVAILABLE, build_role_r1_analytics, evidence_quality, family_state,
)
from role_outcome_engine import OUTCOME_METHOD_HASH


ROLE_R2_VERSION = "ROLE_R2_EMPIRICAL_BAYES_V1"
PRIMARY_HORIZON = 10
PRIOR_STRENGTH = 20.0
QUALITY_ORDER = {"INSUFFICIENT": 0, "EARLY": 1, "DEVELOPING": 2, "STRONG": 3}
ROLE_R2_METHOD_HASH = hashlib.sha256(json.dumps({
    "version": ROLE_R2_VERSION,
    "primary_horizon": PRIMARY_HORIZON,
    "prior_strength": PRIOR_STRENGTH,
    "drivers": ["strategy", *FAMILY_ANCHORS],
    "combination": "normalized_precision_weighted_posterior_deltas",
    "effective_n": "minimum_supported_cohort_n",
    "self_outcome_excluded": True,
}, sort_keys=True).encode()).hexdigest()


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _origin(row: Mapping[str, Any]) -> str:
    methods = row.get("methodologies") or {}
    contract = str(methods.get("decision_contract") or "").upper()
    provenance = json.dumps(row.get("provenance") or [], sort_keys=True).upper()
    return "BACKFILL" if "BACKFILL" in contract or "CANONICAL_DAILY_OPPORTUNITY" in provenance else "PROSPECTIVE"


def _cohort_by_label(cohorts: Iterable[Mapping[str, Any]], label: str) -> Mapping[str, Any] | None:
    return next((cohort for cohort in cohorts if str(cohort.get("cohort")) == str(label)), None)


def _cap_quality(left: str, right: str) -> str:
    return left if QUALITY_ORDER.get(left, 0) <= QUALITY_ORDER.get(right, 0) else right


def _posterior_driver(name: str, state: str, cohort: Mapping[str, Any],
                      baseline: Mapping[str, Any]) -> dict[str, Any] | None:
    n = int(cohort.get("mature_sample_size") or 0)
    baseline_success = _number(baseline.get("plus_5_before_minus_3_success_pct"))
    cohort_success = _number(cohort.get("plus_5_before_minus_3_success_pct"))
    if n <= 0 or baseline_success is None or cohort_success is None:
        return None
    weight = n / (n + PRIOR_STRENGTH)
    result = {
        "driver": name, "state": state, "sample_size": n,
        "raw_evidence_quality": cohort.get("evidence_quality"),
        "shrinkage_weight": round(weight, 6),
        "posterior_success_pct": round(baseline_success + weight * (cohort_success - baseline_success), 6),
    }
    for metric in ("median_mfe_pct", "median_mae_pct", "median_close_return_pct"):
        base = _number(baseline.get(metric)); observed = _number(cohort.get(metric))
        result[f"posterior_{metric}"] = round(base + weight * (observed - base), 6) if base is not None and observed is not None else None
    result["success_difference_vs_baseline_pp"] = round(result["posterior_success_pct"] - baseline_success, 6)
    result["origin_10d_counts"] = (cohort.get("data_coverage") or {}).get("origin_10d_counts") or {}
    result["coverage_pct"] = _number((cohort.get("data_coverage") or {}).get("evidence_coverage_pct")) or 0.0
    return result


def _insufficient_report(target: Mapping[str, Any], analytics: Mapping[str, Any], reasons: list[str],
                         unsupported: list[dict[str, Any]], supported: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    baseline = analytics.get("baseline") or {}
    return {
        "opportunity_id": target.get("opportunity_id"), "symbol": target.get("symbol"),
        "signal_date": target.get("signal_date"), "primary_horizon_sessions": PRIMARY_HORIZON,
        "status": "INSUFFICIENT EVIDENCE", "evidence_quality": "INSUFFICIENT",
        "system_baseline": baseline,
        "effective_comparable_sample_size": min((driver["sample_size"] for driver in (supported or [])), default=0),
        "estimated_plus_5_before_minus_3_success_pct": None,
        "estimated_mfe_pct": None, "estimated_mae_pct": None,
        "estimated_close_return_pct": None,
        "positive_learned_drivers": [], "negative_learned_drivers": [],
        "neutral_learned_drivers": [], "unavailable_or_unsupported_drivers": unsupported,
        "supported_driver_diagnostics": supported or [],
        "insufficiency_reasons": sorted(set(reasons)),
        "data_coverage": baseline.get("data_coverage") or {},
        "prospective_vs_backfilled_evidence": (baseline.get("data_coverage") or {}).get("origin_10d_counts") or {},
        "methodology_version": ROLE_R2_VERSION, "methodology_hash": ROLE_R2_METHOD_HASH,
        "advisory_only": True,
    }


def build_role_r2_report(target: Mapping[str, Any], evidence_rows: Iterable[Mapping[str, Any]],
                         as_of_date: Any = None) -> dict[str, Any]:
    """Estimate coarse-state evidence without using the target's own outcome."""
    cutoff = str(as_of_date or dt.date.today())[:10]
    target_id = str(target.get("opportunity_id") or "")
    history = [dict(row) for row in evidence_rows if str(row.get("opportunity_id") or "") != target_id]
    analytics = build_role_r1_analytics(history, cutoff)
    baseline = analytics.get("baseline") or {}
    baseline_quality = str(baseline.get("evidence_quality") or "INSUFFICIENT")
    vector = target.get("lsv_v1") or {}
    unsupported: list[dict[str, Any]] = []
    supported: list[dict[str, Any]] = []

    candidates = [("strategy", str(target.get("strategy") or NOT_AVAILABLE), analytics.get("strategy_cohorts") or [])]
    family_analytics = analytics.get("family_cohorts") or {}
    for family in FAMILY_ANCHORS:
        state = family_state(vector, family)
        candidates.append((family, state, (family_analytics.get(family) or {}).get("cohorts") or []))

    for name, state, cohorts in candidates:
        if state == NOT_AVAILABLE:
            unsupported.append({"driver": name, "state": state, "reason": "STATE_NOT_AVAILABLE"})
            continue
        cohort = _cohort_by_label(cohorts, state)
        if not cohort:
            unsupported.append({"driver": name, "state": state, "reason": "NO_MATURE_COMPARABLE_COHORT"})
            continue
        if str(cohort.get("evidence_quality")) == "INSUFFICIENT":
            unsupported.append({"driver": name, "state": state, "reason": "INSUFFICIENT_COHORT_EVIDENCE",
                                "sample_size": int(cohort.get("mature_sample_size") or 0)})
            continue
        posterior = _posterior_driver(name, state, cohort, baseline)
        if posterior:
            supported.append(posterior)
        else:
            unsupported.append({"driver": name, "state": state, "reason": "REQUIRED_OUTCOME_METRICS_UNAVAILABLE"})

    reasons = []
    if baseline_quality == "INSUFFICIENT":
        reasons.append("SYSTEM_BASELINE_EVIDENCE_IS_INSUFFICIENT")
    if not supported:
        reasons.append("NO_SUPPORTED_COMPARABLE_DRIVERS")
    if reasons:
        return _insufficient_report(target, analytics, reasons, unsupported, supported)

    baseline_success = _number(baseline.get("plus_5_before_minus_3_success_pct"))

    def combined(key: str, baseline_key: str) -> float | None:
        base = _number(baseline.get(baseline_key))
        values = [(driver["shrinkage_weight"], _number(driver.get(key))) for driver in supported]
        values = [(weight, value) for weight, value in values if value is not None]
        if base is None or not values:
            return None
        denominator = sum(weight for weight, _ in values)
        return round(base + sum(weight * (value - base) for weight, value in values) / denominator, 6)

    estimated_success = combined("posterior_success_pct", "plus_5_before_minus_3_success_pct")
    estimated_success = min(100.0, max(0.0, estimated_success)) if estimated_success is not None else None
    effective_n = min(driver["sample_size"] for driver in supported)
    evidence_coverage = min(driver["coverage_pct"] for driver in supported)
    quality = _cap_quality(evidence_quality(effective_n, evidence_coverage), baseline_quality)
    if quality == "INSUFFICIENT" or baseline_success is None:
        return _insufficient_report(
            target, analytics, ["COMBINED_EVIDENCE_FAILED_QUALITY_GATE"], unsupported, supported
        )

    positive = [driver for driver in supported if driver["success_difference_vs_baseline_pp"] > 0]
    negative = [driver for driver in supported if driver["success_difference_vs_baseline_pp"] < 0]
    neutral = [driver for driver in supported if driver["success_difference_vs_baseline_pp"] == 0]
    origins = (baseline.get("data_coverage") or {}).get("origin_10d_counts") or {}
    return {
        "opportunity_id": target.get("opportunity_id"), "symbol": target.get("symbol"),
        "signal_date": target.get("signal_date"), "primary_horizon_sessions": PRIMARY_HORIZON,
        "status": quality, "evidence_quality": quality, "system_baseline": baseline,
        "effective_comparable_sample_size": effective_n,
        "estimated_plus_5_before_minus_3_success_pct": estimated_success,
        "estimated_mfe_pct": combined("posterior_median_mfe_pct", "median_mfe_pct"),
        "estimated_mae_pct": combined("posterior_median_mae_pct", "median_mae_pct"),
        "estimated_close_return_pct": combined("posterior_median_close_return_pct", "median_close_return_pct"),
        "positive_learned_drivers": positive, "negative_learned_drivers": negative,
        "neutral_learned_drivers": neutral, "unavailable_or_unsupported_drivers": unsupported,
        "supported_driver_diagnostics": supported,
        "insufficiency_reasons": [],
        "data_coverage": {"evidence_coverage_pct": evidence_coverage,
                          "baseline_outcome_10d_pct": (baseline.get("data_coverage") or {}).get("outcome_10d_pct")},
        "prospective_vs_backfilled_evidence": origins,
        "methodology_version": ROLE_R2_VERSION, "methodology_hash": ROLE_R2_METHOD_HASH,
        "advisory_only": True,
    }


def load_role_r2_report(opportunity_id: str, as_of_date: Any = None) -> dict[str, Any] | None:
    """Reusable Stock Intelligence API over persisted recommendation/outcome state."""
    rows = database.load_role_learning_rows(OUTCOME_METHOD_HASH)
    target = next((row for row in rows if str(row.get("opportunity_id")) == str(opportunity_id)), None)
    return build_role_r2_report(target, rows, as_of_date) if target else None


def validate_role_r2_calibration(rows: Iterable[Mapping[str, Any]], as_of_date: Any = None) -> dict[str, Any]:
    """Causal retrospective validation; no threshold tuning or model fitting."""
    rows = [dict(row) for row in rows]
    cutoff = str(as_of_date or dt.date.today())[:10]
    realized = []
    for target in rows:
        horizon = (target.get("horizons") or {}).get("10") or {}
        observation_date = str(horizon.get("observation_date") or "9999-12-31")[:10]
        if observation_date > cutoff:
            continue
        actual = horizon.get("payload") or {}
        report = build_role_r2_report(target, rows, target.get("signal_date"))
        if report.get("estimated_plus_5_before_minus_3_success_pct") is None:
            continue
        barrier = actual.get("plus_5_before_minus_3") or {}
        if not isinstance(barrier.get("value"), bool):
            continue
        realized.append((report, actual, float(barrier["value"])))

    def average(values: list[float]) -> float | None:
        return round(mean(values), 6) if values else None

    brier = [((item[0]["estimated_plus_5_before_minus_3_success_pct"] / 100.0) - item[2]) ** 2 for item in realized]
    baseline_brier = [((item[0]["system_baseline"]["plus_5_before_minus_3_success_pct"] / 100.0) - item[2]) ** 2 for item in realized]
    mfe_errors = [abs(item[0]["estimated_mfe_pct"] - item[1]["mfe_pct"]) for item in realized if item[0]["estimated_mfe_pct"] is not None and _number(item[1].get("mfe_pct")) is not None]
    mae_errors = [abs(item[0]["estimated_mae_pct"] - item[1]["mae_pct"]) for item in realized if item[0]["estimated_mae_pct"] is not None and _number(item[1].get("mae_pct")) is not None]
    return {
        "realized_10d_opportunities": sum(
            str(((row.get("horizons") or {}).get("10") or {}).get("observation_date") or "9999-12-31")[:10] <= cutoff
            for row in rows
        ),
        "calibrated_predictions": len(realized),
        "coverage_pct": round(100.0 * len(realized) / len(rows), 6) if rows else 0.0,
        "success_brier_score": average(brier), "system_baseline_brier_score": average(baseline_brier),
        "mfe_mean_absolute_error_pct": average(mfe_errors), "mae_mean_absolute_error_pct": average(mae_errors),
        "validation_mode": "CAUSAL_SIGNAL_DATE_CUTOFF",
        "methodology_hash": ROLE_R2_METHOD_HASH,
    }
