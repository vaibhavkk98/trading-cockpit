"""ROLE-R1 explainable learning analytics; descriptive and advisory only."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
from statistics import median
from typing import Any, Iterable, Mapping

import database
from role_outcome_engine import OUTCOME_METHOD_HASH


ROLE_R1_VERSION = "ROLE_R1_ANALYTICS_V1_1"
NOT_AVAILABLE = "NOT_AVAILABLE"

# One predeclared, interpretable anchor per LSV family.  These are frozen
# before outcome analysis and are not selected based on observed performance.
FAMILY_ANCHORS = {
    "price_state": "return_20d_pct",
    "participation": "volume_ratio_20d",
    "relative_demand": "cross_sectional_rs_percentile",
    "volatility_state": "short_long_volatility_ratio",
    "price_response": "close_location_value",
    "positioning": "futures_basis_pct",
    "liquidity": "traded_value_percentile_252d",
    "environment": "breadth",
}

INTERACTIONS = {
    "relative_demand_x_participation": ("relative_demand", "participation"),
    "price_response_x_participation": ("price_response", "participation"),
    "strategy_x_breadth": ("strategy", "environment"),
    "volatility_x_strategy": ("volatility_state", "strategy"),
}

ANALYTICS_METHOD_HASH = hashlib.sha256(json.dumps({
    "version": ROLE_R1_VERSION,
    "primary_horizon": 10,
    "secondary_horizons": [5, 20],
    "family_anchors": FAMILY_ANCHORS,
    "interactions": INTERACTIONS,
    "evidence_bands": {"insufficient": 10, "early": 30, "developing": 100},
    "coverage_gates_pct": [50, 70, 90],
}, sort_keys=True).encode()).hexdigest()


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _three_band(value: Any, low: float, high: float,
                labels: tuple[str, str, str]) -> str:
    numeric = _number(value)
    if numeric is None:
        return NOT_AVAILABLE
    if numeric < low:
        return labels[0]
    if numeric > high:
        return labels[2]
    return labels[1]


def family_state(vector: Mapping[str, Any], family: str) -> str:
    """Map a frozen LSV anchor to a fixed, outcome-independent state."""
    values = vector.get(family) if isinstance(vector, Mapping) else None
    values = values if isinstance(values, Mapping) else {}
    field = FAMILY_ANCHORS[family]
    value = values.get(field)
    if family == "price_state":
        return _three_band(value, -5.0, 5.0, ("NEGATIVE", "RANGE", "POSITIVE"))
    if family in ("participation", "volatility_state"):
        return _three_band(value, 0.8, 1.2, ("LOW", "NORMAL", "HIGH"))
    if family in ("relative_demand", "liquidity"):
        return _three_band(value, 100.0 / 3.0, 200.0 / 3.0, ("LOW", "MIDDLE", "HIGH"))
    if family == "price_response":
        return _three_band(value, -1.0 / 3.0, 1.0 / 3.0, ("WEAK", "BALANCED", "STRONG"))
    if family == "positioning":
        return _three_band(value, -0.5, 0.5, ("DISCOUNT", "FLAT", "PREMIUM"))
    state = str(value or NOT_AVAILABLE).strip().upper()
    return state if state and state != "NONE" else NOT_AVAILABLE


def evidence_quality(sample_size: int, feature_coverage_pct: float) -> str:
    """Fixed confidence bands; never infer a favorable/caution state."""
    if sample_size < 10 or feature_coverage_pct < 50.0:
        return "INSUFFICIENT"
    if sample_size < 30 or feature_coverage_pct < 70.0:
        return "EARLY"
    if sample_size < 100 or feature_coverage_pct < 90.0:
        return "DEVELOPING"
    return "STRONG"


def _origin(row: Mapping[str, Any]) -> str:
    methodologies = row.get("methodologies") or {}
    contract = str(methodologies.get("decision_contract") or "").upper()
    provenance = json.dumps(row.get("provenance") or [], sort_keys=True).upper()
    return "BACKFILL" if "BACKFILL" in contract or "CANONICAL_DAILY_OPPORTUNITY" in provenance else "PROSPECTIVE"


def _as_of_rows(rows: Iterable[Mapping[str, Any]], as_of_date: Any) -> list[dict[str, Any]]:
    cutoff = str(as_of_date)[:10]
    result = []
    for original in rows:
        if str(original.get("signal_date"))[:10] > cutoff:
            continue
        row = dict(original)
        row["origin"] = _origin(row)
        row["horizons"] = {
            str(horizon): dict(item.get("payload") or {})
            for horizon, item in (row.get("horizons") or {}).items()
            if str(item.get("observation_date") or "9999-12-31")[:10] <= cutoff
        }
        result.append(row)
    return result


def _metric_values(rows: Iterable[Mapping[str, Any]], horizon: int, key: str) -> list[float]:
    values = []
    for row in rows:
        value = _number((row.get("horizons") or {}).get(str(horizon), {}).get(key))
        if value is not None:
            values.append(value)
    return values


def _horizon_summary(rows: Iterable[Mapping[str, Any]], horizon: int) -> dict[str, Any]:
    rows = [row for row in rows if str(horizon) in (row.get("horizons") or {})]
    successes = []
    for row in rows:
        barrier = row["horizons"][str(horizon)].get("plus_5_before_minus_3") or {}
        if isinstance(barrier, Mapping) and isinstance(barrier.get("value"), bool):
            successes.append(barrier["value"])
    def med(key: str) -> float | None:
        values = _metric_values(rows, horizon, key)
        return round(float(median(values)), 6) if values else None
    return {
        "mature_sample_size": len(rows),
        "plus_5_before_minus_3_success_pct": round(100.0 * sum(successes) / len(successes), 6) if successes else None,
        "median_mfe_pct": med("mfe_pct"),
        "median_mae_pct": med("mae_pct"),
        "median_close_return_pct": med("close_return_pct"),
    }


def _differences(metrics: Mapping[str, Any], baseline: Mapping[str, Any]) -> dict[str, Any]:
    result = {}
    for key in ("plus_5_before_minus_3_success_pct", "median_mfe_pct", "median_mae_pct", "median_close_return_pct"):
        left = _number(metrics.get(key)); right = _number(baseline.get(key))
        result[f"{key}_difference"] = round(left - right, 6) if left is not None and right is not None else None
    return result


def _cohort_payload(label: str, rows: list[dict[str, Any]], all_rows: list[dict[str, Any]],
                    eligible_count: int, total_count: int, baseline: Mapping[str, Any]) -> dict[str, Any]:
    primary = _horizon_summary(rows, 10)
    feature_coverage = 100.0 * eligible_count / total_count if total_count else 0.0
    outcome_coverage = _number((baseline.get("data_coverage") or {}).get("outcome_10d_pct")) or 0.0
    evidence_coverage = min(feature_coverage, outcome_coverage)
    origins = {origin: sum(row["origin"] == origin and "10" in row["horizons"] for row in rows)
               for origin in ("PROSPECTIVE", "BACKFILL")}
    return {
        "cohort": label,
        **primary,
        "difference_vs_system_baseline": _differences(primary, baseline),
        "data_coverage": {
            "feature_coverage_pct": round(feature_coverage, 6),
            "evidence_coverage_pct": round(evidence_coverage, 6),
            "cohort_share_of_eligible_pct": round(100.0 * primary["mature_sample_size"] / eligible_count, 6) if eligible_count else 0.0,
            "origin_10d_counts": origins,
        },
        "evidence_quality": evidence_quality(primary["mature_sample_size"], evidence_coverage),
        "secondary_context": {"5d": _horizon_summary(all_rows, 5), "20d": _horizon_summary(all_rows, 20)},
    }


def _grouped(rows: list[dict[str, Any]], labeler, baseline: Mapping[str, Any],
             total_10d: int) -> tuple[list[dict[str, Any]], int]:
    groups: dict[str, list[dict[str, Any]]] = {}
    eligible = []
    for row in rows:
        label = labeler(row)
        if label == NOT_AVAILABLE or NOT_AVAILABLE in label.split(" × "):
            continue
        eligible.append(row)
        groups.setdefault(label, []).append(row)
    eligible_10d = sum("10" in row["horizons"] for row in eligible)
    cohorts = [
        _cohort_payload(label, group, group, eligible_10d, total_10d, baseline)
        for label, group in sorted(groups.items())
    ]
    return cohorts, eligible_10d


def build_role_r1_analytics(rows: Iterable[Mapping[str, Any]], as_of_date: Any = None) -> dict[str, Any]:
    """Build deterministic advisory analytics from already-matured outcomes."""
    cutoff = str(as_of_date or dt.date.today())[:10]
    records = _as_of_rows(rows, cutoff)
    baseline = _horizon_summary(records, 10)
    total_10d = baseline["mature_sample_size"]
    total = len(records)
    outcome_coverage = round(100.0 * total_10d / total, 6) if total else 0.0
    baseline["data_coverage"] = {
        "outcome_10d_pct": outcome_coverage,
        "origin_10d_counts": {origin: sum(row["origin"] == origin and "10" in row["horizons"] for row in records)
                              for origin in ("PROSPECTIVE", "BACKFILL")},
    }
    baseline["evidence_quality"] = evidence_quality(total_10d, outcome_coverage)
    baseline["secondary_context"] = {"5d": _horizon_summary(records, 5), "20d": _horizon_summary(records, 20)}

    strategy_cohorts, _ = _grouped(records, lambda row: str(row.get("strategy") or NOT_AVAILABLE), baseline, total_10d)
    family_analytics = {}
    for family, anchor in FAMILY_ANCHORS.items():
        cohorts, eligible = _grouped(records, lambda row, name=family: family_state(row.get("lsv_v1") or {}, name), baseline, total_10d)
        family_analytics[family] = {"anchor_field": anchor, "eligible_10d": eligible, "cohorts": cohorts}

    def interaction_label(row: Mapping[str, Any], left: str, right: str) -> str:
        def value(part: str) -> str:
            return str(row.get("strategy") or NOT_AVAILABLE) if part == "strategy" else family_state(row.get("lsv_v1") or {}, part)
        return f"{value(left)} × {value(right)}"

    interaction_analytics = {}
    for name, (left, right) in INTERACTIONS.items():
        cohorts, eligible = _grouped(records, lambda row, a=left, b=right: interaction_label(row, a, b), baseline, total_10d)
        interaction_analytics[name] = {"dimensions": [left, right], "eligible_10d": eligible, "cohorts": cohorts}

    return {
        "contract_version": ROLE_R1_VERSION,
        "analytics_methodology_hash": ANALYTICS_METHOD_HASH,
        "outcome_methodology_hashes": sorted({str(row.get("outcome_methodology_hash")) for row in records if row.get("outcome_methodology_hash")}),
        "lsv_methodology_hashes": sorted({str(row.get("lsv_methodology_hash")) for row in records if row.get("lsv_methodology_hash")}),
        "as_of_date": cutoff,
        "primary_horizon_sessions": 10,
        "secondary_horizons_sessions": [5, 20],
        "recommendation_count": total,
        "maturity_coverage": {f"{horizon}d": _horizon_summary(records, horizon)["mature_sample_size"] for horizon in (5, 10, 20)},
        "baseline": baseline,
        "origin_baselines": {origin: _horizon_summary([row for row in records if row["origin"] == origin], 10)
                             for origin in ("PROSPECTIVE", "BACKFILL")},
        "strategy_cohorts": strategy_cohorts,
        "family_cohorts": family_analytics,
        "interaction_cohorts": interaction_analytics,
        "advisory_only": True,
    }


def load_live_role_r1_analytics(as_of_date: Any = None) -> dict[str, Any]:
    """Reusable read-only service contract for future UI/evidence consumers."""
    return build_role_r1_analytics(database.load_role_learning_rows(OUTCOME_METHOD_HASH), as_of_date)
