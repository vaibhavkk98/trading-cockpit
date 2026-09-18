"""Frozen, causal Systematic Engine V1C opportunity-selection primitives.

This module has no database or execution side effects.  It ranks an already
qualified candidate set; B1 remains solely responsible for sizing/admission.
"""
from __future__ import annotations

import hashlib
import json
import math
from statistics import mean
from typing import Any, Iterable, Mapping

import numpy as np
import pandas as pd

from latent_state_vector import NOT_AVAILABLE, build_causal_vector
from portfolio_risk_engine import candidate_risk_proxy, realized_volatility_20
from provider_symbols import yahoo_nse_symbol


VERSION = "SYSTEMATIC_ENGINE_V1C_OPPORTUNITY_SELECTION_V1"
FEATURE_MANIFEST = {
    "version": "selection_feature_manifest_v1",
    "causal_cutoff": "completed signal session T; never reads T+1",
    "candidate_universe": "active qualified candidates competing in the same admission event",
    "percentile": "midrank=(count(lower)+0.5*count(equal))/N; range [0,1]",
    "minimum_quality_coverage": 3,
    "features": {
        "relative_demand_20d_pct": {
            "family": "Relative Demand", "direction": "HIGHER_BETTER", "lookback": 20,
            "formula": "100*(Close[T]/Close[T-20]-1) - 100*(NIFTY500[T]/NIFTY500[T-20]-1)",
            "source": "latent_state_vector.build_causal_vector.relative_demand.stock_vs_nifty500_return_20d_pct",
            "missing": "NOT_AVAILABLE if either series lacks 21 completed observations",
            "rationale": "direct medium-horizon demand versus the frozen NIFTY 500 benchmark",
        },
        "volume_ratio_20d": {
            "family": "Participation", "direction": "HIGHER_BETTER", "lookback": 20,
            "formula": "Volume[T] / mean(Volume[T-20:T-1])",
            "source": "latent_state_vector.build_causal_vector.participation.volume_ratio_20d",
            "missing": "NOT_AVAILABLE if the prior-volume mean is unavailable or non-positive",
            "rationale": "canonical contemporaneous participation measure",
        },
        "close_location_value": {
            "family": "Price Response", "direction": "HIGHER_BETTER", "lookback": 1,
            "formula": "((Close-Low)-(High-Close))/(High-Low) on T",
            "source": "latent_state_vector.build_causal_vector.price_response.close_location_value",
            "missing": "NOT_AVAILABLE for a zero/invalid session range",
            "rationale": "direct close strength within the completed-session range",
        },
        "ema20_extension_pct": {
            "family": "Trend", "direction": "HIGHER_BETTER", "lookback": 20,
            "formula": "100*(Close[T]/EMA20[T]-1), pandas EWM span=20 adjust=False min_periods=20",
            "source": "latent_state_vector.build_causal_vector.price_state.ema20_extension_pct",
            "missing": "NOT_AVAILABLE without 20 valid completed closes",
            "rationale": "direct canonical medium-horizon trend measure; not a composite or proxy",
        },
    },
}
RANDOM_SEEDS = (104729, 130363, 155921, 181081, 205019, 230003, 256019, 280001, 305023, 330017,
                355009, 380041, 405031, 430007, 455003, 480017, 505027, 530017, 555029, 580031)
RANDOM_MANIFEST = {
    "version": "SELECTION_RANDOM_CONTROL_V1", "seeds": list(RANDOM_SEEDS),
    "algorithm": "SHA256(seed|selection_event_id|opportunity_id), ascending digest",
    "ordering_input": "immutable constrained candidate-set opportunity IDs",
    "tie_break": "opportunity_id ascending", "live_authority": False,
}
C1_CONFIG = {"policy": "C1", "formula": "mean available four-feature midrank percentiles",
             "minimum_available": 3, "invalid_order": "P0 freshness then opportunity_id", "decision_authority": False}
C2_CONFIG = {"policy": "C2", "formula": "0.5*quality_score+0.5*(1-risk_percentile)",
             "risk_proxy": "max(ATR%,RV20_daily%)", "rv20_window": 20, "rv20_min_returns": 15,
             "atr_required": True, "decision_authority": False}


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


FEATURE_MANIFEST_HASH = _digest(FEATURE_MANIFEST)
RANDOM_MANIFEST_HASH = _digest(RANDOM_MANIFEST)
C1_CONFIG_HASH = _digest(C1_CONFIG)
C2_CONFIG_HASH = _digest(C2_CONFIG)
C1_METHODOLOGY_HASH = _digest({"version": VERSION, "feature_manifest": FEATURE_MANIFEST_HASH, "config": C1_CONFIG_HASH})
C2_METHODOLOGY_HASH = _digest({"version": VERSION, "feature_manifest": FEATURE_MANIFEST_HASH, "config": C2_CONFIG_HASH})


def _frame(histories: Mapping[str, pd.DataFrame], symbol: str) -> pd.DataFrame | None:
    result = histories.get(yahoo_nse_symbol(symbol))
    if result is None:
        result = histories.get(symbol)
    if result is None or result.empty:
        return None
    result = result.copy()
    result.columns = [str(column).title().replace("Adjusted_", "") for column in result.columns]
    return result


def _finite(value: Any) -> float | None:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def extract_candidate_features(candidate: Mapping[str, Any], histories: Mapping[str, pd.DataFrame]) -> dict[str, Any]:
    """Freeze the four reward fields and B1 adversity fields at signal T."""
    signal_date = str(candidate.get("signal_date") or "")[:10]
    stock = _frame(histories, str(candidate.get("symbol") or ""))
    benchmark = _frame(histories, "NIFTY500")
    vector = build_causal_vector(stock, benchmark, signal_date) if stock is not None else {}
    price = vector.get("price_state") or {}; participation = vector.get("participation") or {}
    relative = vector.get("relative_demand") or {}; response = vector.get("price_response") or {}
    reward = {
        "relative_demand_20d_pct": relative.get("stock_vs_nifty500_return_20d_pct", NOT_AVAILABLE),
        "volume_ratio_20d": participation.get("volume_ratio_20d", NOT_AVAILABLE),
        "close_location_value": response.get("close_location_value", NOT_AVAILABLE),
        "ema20_extension_pct": price.get("ema20_extension_pct", NOT_AVAILABLE),
    }
    atr_pct = _finite(candidate.get("atr_pct"))
    if atr_pct is None:
        entry, atr = _finite(candidate.get("reference_price")), _finite(candidate.get("atr_20"))
        atr_pct = atr / entry * 100 if entry and atr and entry > 0 else None
    rv = realized_volatility_20(histories, str(candidate.get("symbol") or ""), pd.Timestamp(signal_date).date())
    risk = candidate_risk_proxy(atr_pct if atr_pct is not None else NOT_AVAILABLE, rv["rv20_pct"])
    return {"features": reward, "atr_pct": atr_pct if atr_pct is not None else NOT_AVAILABLE,
            "rv20_daily_pct": rv["rv20_pct"], "rv20_valid_returns": rv["valid_returns"],
            **risk, "source_timestamp": signal_date, "feature_manifest_hash": FEATURE_MANIFEST_HASH}


def _midranks(values: Mapping[str, Any]) -> dict[str, Any]:
    numeric = {key: _finite(value) for key, value in values.items()}
    valid = {key: value for key, value in numeric.items() if value is not None}
    return {key: ((sum(other < value for other in valid.values()) +
                   .5 * sum(other == value for other in valid.values())) / len(valid))
            for key, value in valid.items()}


def score_candidate_set(candidates: Iterable[Mapping[str, Any]], policy: str) -> list[dict[str, Any]]:
    """Rank one active candidate set deterministically under frozen C1/C2."""
    rows = [dict(row) for row in candidates]
    # Percentiles are defined only over candidates that can genuinely compete
    # for admission.  Keeping operationally invalid queue rows out of the
    # denominator prevents a stale/duplicate/illiquid extreme from changing a
    # valid candidate's selection score.
    eligible_rows = [row for row in rows if bool(row.get("ranking_eligible", True))]
    feature_names = tuple(FEATURE_MANIFEST["features"])
    percentile_by_feature = {name: _midranks({str(row["opportunity_id"]):
        (row.get("selection_inputs") or {}).get("features", {}).get(name) for row in eligible_rows}) for name in feature_names}
    risk_percentiles = _midranks({str(row["opportunity_id"]):
        (row.get("selection_inputs") or {}).get("risk_proxy_pct") for row in eligible_rows})
    for row in rows:
        oid = str(row["opportunity_id"])
        percentiles = {name: percentile_by_feature[name].get(oid, NOT_AVAILABLE) for name in feature_names}
        values = [value for value in percentiles.values() if _finite(value) is not None]
        ranking_eligible = bool(row.get("ranking_eligible", True))
        quality = mean(values) if ranking_eligible and len(values) >= 3 else NOT_AVAILABLE
        risk_pct = risk_percentiles.get(oid, NOT_AVAILABLE)
        c2 = (0.5 * float(quality) + 0.5 * (1 - float(risk_pct))
              if _finite(quality) is not None and _finite(risk_pct) is not None and
              bool((row.get("selection_inputs") or {}).get("eligible")) else NOT_AVAILABLE)
        row.update({"feature_percentiles": percentiles, "quality_score": quality,
                    "quality_feature_coverage": len(values), "risk_percentile": risk_pct,
                    "risk_adjusted_quality": c2, "ranking_eligible": ranking_eligible})
    def freshness(row):
        return (-pd.Timestamp(str(row.get("signal_date"))[:10]).toordinal(), str(row["opportunity_id"]))
    if policy == "C1":
        rows.sort(key=lambda row: ((0, -float(row["quality_score"]), *freshness(row))
            if _finite(row["quality_score"]) is not None else (1, 0., *freshness(row))))
    elif policy == "C2":
        rows.sort(key=lambda row: ((0, -float(row["risk_adjusted_quality"]), -float(row["quality_score"]), *freshness(row))
            if _finite(row["risk_adjusted_quality"]) is not None else
            (1, 0., -float(row["quality_score"]) if _finite(row["quality_score"]) is not None else 0., *freshness(row))))
    else:
        raise ValueError("policy must be C1 or C2")
    eligible_rank = 0
    for operational_rank, row in enumerate(rows, 1):
        if row["ranking_eligible"]:
            eligible_rank += 1
            row["selection_rank"] = eligible_rank
        else:
            row["selection_rank"] = None
        row["operational_rank"] = operational_rank; row["selection_policy"] = policy
    return rows


def selection_event_id(market_date: Any, opportunity_ids: Iterable[str]) -> str:
    return _digest(["SELECTION_EVENT_V1", str(market_date)[:10], sorted(str(value) for value in opportunity_ids)])


def random_orderings(event_id: str, opportunity_ids: Iterable[str]) -> dict[int, list[str]]:
    ids = sorted(str(value) for value in opportunity_ids)
    return {seed: sorted(ids, key=lambda oid: (hashlib.sha256(f"{seed}|{event_id}|{oid}".encode()).hexdigest(), oid))
            for seed in RANDOM_SEEDS}


def _metric(row: Mapping[str, Any], key: str) -> float | None:
    outcome = row.get("outcome") or {}
    if key == "h10_net_return_pct":
        return _finite(outcome.get(key, outcome.get("close_return_pct")))
    if key == "plus_5_before_minus_3":
        value = outcome.get(key)
        if isinstance(value, Mapping): value = value.get("value")
        if isinstance(value, str):
            normalized = value.strip().upper()
            if normalized in {"TARGET_FIRST", "TRUE", "YES", "1"}: return 1.
            if normalized in {"STOP_FIRST", "FALSE", "NO", "0", "NEITHER", "SAME_SESSION_AMBIGUOUS"}: return 0.
        return float(value) if isinstance(value, bool) else _finite(value)
    if key == "realized_efficiency":
        explicit = _finite(outcome.get(key))
        if explicit is not None: return explicit
        result, mae = _finite(outcome.get("close_return_pct")), _finite(outcome.get("mae_pct"))
        return result / max(abs(mae), .25) if result is not None and mae is not None else None
    return _finite(outcome.get(key))


def selection_attribution(rows: Iterable[Mapping[str, Any]], score_field: str) -> dict[str, Any]:
    """Mature-only event-level lift, regret, rank IC and adaptive buckets."""
    mature = [dict(row) for row in rows if isinstance(row.get("outcome"), Mapping)]
    selected = [row for row in mature if row.get("admitted")]
    rejected = [row for row in mature if not row.get("admitted")]
    metrics = ("h10_net_return_pct", "mfe_pct", "mae_pct", "plus_5_before_minus_3", "realized_efficiency")
    lift = {}
    for key in metrics:
        left = [_metric(row, key) for row in selected]; right = [_metric(row, key) for row in rejected]
        left = [x for x in left if x is not None]; right = [x for x in right if x is not None]
        lift[key] = mean(left) - mean(right) if left and right else NOT_AVAILABLE
    selected_returns = [_metric(row, "h10_net_return_pct") for row in selected]
    rejected_returns = [_metric(row, "h10_net_return_pct") for row in rejected]
    selected_returns = [x for x in selected_returns if x is not None]; rejected_returns = [x for x in rejected_returns if x is not None]
    selected_mean = mean(selected_returns) if selected_returns else None
    cohort_size = len(selected_returns)
    best_same_sized = (mean(sorted(rejected_returns, reverse=True)[:cohort_size])
                       if cohort_size and len(rejected_returns) >= cohort_size else None)
    regret = {"selected_minus_best_rejected": selected_mean - best_same_sized
              if selected_mean is not None and best_same_sized is not None else NOT_AVAILABLE,
              "selected_minus_average_rejected": selected_mean - mean(rejected_returns)
              if selected_mean is not None and rejected_returns else NOT_AVAILABLE,
              "comparison": "best same-sized rejected cohort" if cohort_size > 1 else "best rejected candidate"}
    pairs = [(_finite(row.get(score_field)), _metric(row, "h10_net_return_pct")) for row in mature]
    pairs = [(x, y) for x, y in pairs if x is not None and y is not None]
    rank_ic = float(pd.Series([x for x, _ in pairs]).corr(pd.Series([y for _, y in pairs]), method="spearman")) if len(pairs) >= 3 else NOT_AVAILABLE
    ordered = sorted((row for row in mature if _finite(row.get(score_field)) is not None),
                     key=lambda row: float(row[score_field]), reverse=True)
    groups = 4 if len(ordered) >= 8 else 2 if len(ordered) >= 4 else 0
    buckets = []
    if groups:
        for index, subset in enumerate(np.array_split(ordered, groups), 1):
            values = [_metric(row, "h10_net_return_pct") for row in subset]
            values = [value for value in values if value is not None]
            buckets.append({"bucket": index, "n": len(values), "mean_h10_net_return_pct": mean(values) if values else NOT_AVAILABLE})
    return {"mature_comparisons": len(mature), "selection_lift": lift, "selection_regret": regret,
            "event_rank_ic": rank_ic, "rank_buckets": buckets}


def selection_stability(rows: Iterable[Mapping[str, Any]], score_field: str) -> dict[str, Any]:
    """Predeclared descriptive slices; empty/immature slices stay explicit."""
    records = list(rows); result = {}
    for dimension in ("strategy", "signal_date", "sector"):
        groups: dict[str, list[Mapping[str, Any]]] = {}
        for row in records:
            groups.setdefault(str(row.get(dimension) or NOT_AVAILABLE), []).append(row)
        result[dimension] = {key: selection_attribution(values, score_field)
                             for key, values in sorted(groups.items())}
    return result
