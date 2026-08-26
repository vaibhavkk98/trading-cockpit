"""Causal EOD Path Risk feature construction and frozen inference.

This advisory service consumes histories already fetched by the scanner.  It
never fetches data, fits a model, or participates in trading decisions.
"""
from __future__ import annotations

import datetime as dt
import math
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd

from path_risk_frozen import (
    EXPECTED_ARTIFACT_FILE_SHA256, EXPECTED_METHODOLOGY_HASH, FrozenPathRiskModel,
)
from provider_symbols import yahoo_nse_symbol


VERSION = "PR-P1-v1"
NOT_AVAILABLE = "NOT AVAILABLE"
ARTIFACT_PATH = Path(__file__).resolve().parent / "data/production/path_risk_pr_r1_v1.json"


def _history(frame: pd.DataFrame | None, as_of_date: Any) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame()
    result = frame.copy()
    result.columns = [str(column).lower().replace("adjusted_", "") for column in result.columns]
    required = {"open", "high", "low", "close", "volume"}
    if not required.issubset(result.columns):
        return pd.DataFrame()
    index = pd.to_datetime(result.index).tz_localize(None).normalize()
    result.index = index
    cutoff = pd.Timestamp(as_of_date).normalize()
    result = result.loc[result.index <= cutoff].sort_index(kind="mergesort")
    return result[~result.index.duplicated(keep="last")]


def _information_discreteness(values: np.ndarray) -> float:
    clean = np.asarray(values, dtype=float)
    clean = clean[np.isfinite(clean)]
    if not len(clean):
        return float("nan")
    cumulative = np.prod(1.0 + clean / 100.0) - 1.0
    return float(np.sign(cumulative) * (np.mean(clean < 0) - np.mean(clean > 0)))


def _positive_contribution(values: np.ndarray, top_n: int) -> float:
    positive = np.clip(np.asarray(values, dtype=float), 0, None)
    total = np.nansum(positive)
    return float(np.nansum(np.sort(positive)[-top_n:]) / total) if total > 0 else float("nan")


def _positive_hhi(values: np.ndarray) -> float:
    positive = np.clip(np.asarray(values, dtype=float), 0, None)
    total = np.nansum(positive)
    return float(np.nansum((positive / total) ** 2)) if total > 0 else float("nan")


def _safe_divide(numerator: np.ndarray, denominator: np.ndarray) -> np.ndarray:
    return np.divide(numerator, denominator, out=np.full_like(numerator, np.nan, dtype=float), where=denominator != 0)


def _stock_features(frame: pd.DataFrame) -> dict[str, float]:
    if len(frame) < 253:
        return {}
    close = pd.to_numeric(frame["close"], errors="coerce").to_numpy(float)
    high = pd.to_numeric(frame["high"], errors="coerce").to_numpy(float)
    low = pd.to_numeric(frame["low"], errors="coerce").to_numpy(float)
    open_ = pd.to_numeric(frame["open"], errors="coerce").to_numpy(float)
    volume = pd.to_numeric(frame["volume"], errors="coerce").to_numpy(float)
    traded = close * volume
    if not all(np.isfinite(array[-253:]).all() for array in (close, high, low, open_, volume)):
        return {}
    returns = np.r_[np.nan, (close[1:] / close[:-1] - 1.0) * 100.0]
    previous_close = np.r_[np.nan, close[:-1]]
    true_range = np.nanmax(np.vstack((high - low, np.abs(high - previous_close), np.abs(low - previous_close))), axis=0)
    position = len(frame) - 1
    formation = returns[position - 251:position - 20]
    recent20 = returns[position - 19:position + 1]
    recent30 = returns[position - 29:position + 1]
    volume20 = volume[position - 19:position + 1]
    prior_volume_means = np.asarray([np.nanmean(volume[index - 20:index]) for index in range(position - 19, position + 1)])
    volume_ratios = _safe_divide(volume20, prior_volume_means)
    true_range20 = true_range[position - 19:position + 1]
    realized5 = float(np.nanstd(recent20[-5:], ddof=1))
    realized20 = float(np.nanstd(recent20, ddof=1))
    bar_range = high[position] - low[position]
    amihud = float(np.nanmean(_safe_divide(np.abs(recent20), traded[position - 19:position + 1])) * 1e9)
    features = {
        "information_discreteness_11m_skip1m": _information_discreteness(formation),
        "max_daily_return_20d": float(np.nanmax(recent20)),
        "max_daily_return_30d": float(np.nanmax(recent30)),
        "largest_positive_contribution_20d": _positive_contribution(recent20, 1),
        "top3_positive_contribution_20d": _positive_contribution(recent20, 3),
        "positive_return_hhi_20d": _positive_hhi(recent20),
        "atr_pct": float(np.nanmean(true_range20) / close[position] * 100.0),
        "realized_vol_ratio_5v20": realized5 / realized20 if realized20 else float("nan"),
        "range_ratio_5v20": float(np.nanmean(true_range[position - 4:position + 1]) / np.nanmean(true_range20)),
        "volatility_acceleration": realized5 - realized20,
        "close_location_value": float((close[position] - low[position]) / bar_range) if bar_range else float("nan"),
        "upper_wick_ratio": float((high[position] - max(open_[position], close[position])) / bar_range) if bar_range else float("nan"),
        "lower_wick_ratio": float((min(open_[position], close[position]) - low[position]) / bar_range) if bar_range else float("nan"),
        "gap_return": float((open_[position] / close[position - 1] - 1.0) * 100.0),
        "intraday_return": float((close[position] / open_[position] - 1.0) * 100.0),
        "range_expansion_ratio": float(true_range[position] / np.nanmean(true_range20)),
        "volume_ratio": float(volume[position] / np.nanmean(volume[position - 20:position])),
        "volume_persistence_5d": float(np.nanmean(volume_ratios[-5:] > 1.0)),
        "abnormal_volume_frequency_20d": float(np.nanmean(volume_ratios >= 1.5)),
        "traded_value": float(traded[position]),
        "traded_value_ratio": float(traded[position] / np.nanmean(traded[position - 20:position])),
        "log_amihud_impact_20d": float(np.log1p(max(amihud, 0.0))),
    }
    return features


def _market_features(benchmark: pd.DataFrame, histories: Mapping[str, pd.DataFrame], as_of_date: Any) -> dict[str, float]:
    market = _history(benchmark, as_of_date)
    if len(market) < 20:
        return {}
    close = pd.to_numeric(market["close"], errors="coerce")
    market_return = close.pct_change(fill_method=None)
    breadth_values = []
    cutoff = pd.Timestamp(as_of_date).normalize()
    for raw in histories.values():
        stock = _history(raw, as_of_date)
        if len(stock) < 20 or stock.index[-1] != cutoff:
            continue
        stock_close = pd.to_numeric(stock["close"], errors="coerce")
        ema20 = stock_close.ewm(span=20, adjust=False).mean().iloc[-1]
        if pd.notna(stock_close.iloc[-1]) and pd.notna(ema20):
            breadth_values.append(float(stock_close.iloc[-1] > ema20))
    return {
        "market_realized_vol_20d": float(market_return.tail(20).std(ddof=1) * np.sqrt(252) * 100.0),
        "market_breadth_ema20": float(np.mean(breadth_values)) if breadth_values else float("nan"),
        "market_trend_ema20": float((close.iloc[-1] / close.ewm(span=20, adjust=False).mean().iloc[-1] - 1.0) * 100.0),
    }


def _baseline_context(artifact: Mapping[str, Any], state: str) -> dict[str, Any]:
    buckets = artifact.get("reference_test", {}).get("buckets", [])
    total = sum(int(row.get("n") or 0) for row in buckets)
    adverse = sum(float(row.get("reached_minus_3_pct") or 0) * int(row.get("n") or 0) for row in buckets)
    selected = next((row for row in buckets if row.get("state") == state), None)
    return {
        "research_sample_n": total,
        "overall_minus_3_reached_pct": adverse / total if total else None,
        "state_bucket": selected,
        "context_only": True,
    }


def apply_path_risk(
    decisions: list[dict[str, Any]], histories: Mapping[str, pd.DataFrame],
    benchmark_history: pd.DataFrame | None, as_of_date: Any,
    artifact_path: str | Path = ARTIFACT_PATH,
) -> dict[str, int]:
    """Attach deterministic Path Risk to qualified decisions before persistence."""
    try:
        model = FrozenPathRiskModel.from_path(artifact_path)
        artifact = model.artifact
    except Exception as exc:
        for decision in decisions:
            decision["path_risk"] = {"state": NOT_AVAILABLE, "reason": f"ARTIFACT_{type(exc).__name__}"}
        return {"available": 0, "not_available": len(decisions), "failed": len(decisions)}
    market = _market_features(benchmark_history, histories, as_of_date)
    rows = []
    for decision in decisions:
        symbol = yahoo_nse_symbol(str(decision.get("symbol") or ""))
        frame = _history(histories.get(symbol), as_of_date)
        features = _stock_features(frame)
        features.update(market)
        features["primary_strategy"] = decision.get("strategy")
        rows.append(features)
    # The frozen liquidity percentile was cross-sectional among same-date
    # qualified opportunities.
    traded = pd.Series([row.get("traded_value") for row in rows], dtype=float)
    percentiles = traded.rank(pct=True, method="average") * 100.0
    for index, row in enumerate(rows):
        row["traded_value_percentile"] = float(percentiles.iloc[index]) if pd.notna(percentiles.iloc[index]) else float("nan")
        row["volatility_x_range"] = row.get("realized_vol_ratio_5v20", float("nan")) * row.get("range_expansion_ratio", float("nan"))
        row["upper_wick_x_max"] = row.get("upper_wick_ratio", float("nan")) * row.get("max_daily_return_20d", float("nan"))
        row["impact_x_range"] = row.get("log_amihud_impact_20d", float("nan")) * row.get("range_expansion_ratio", float("nan"))
        row["fip_x_abnormal_volume"] = row.get("information_discreteness_11m_skip1m", float("nan")) * row.get("abnormal_volume_frequency_20d", float("nan"))
    available = unavailable = 0
    as_of_timestamp = dt.datetime.combine(pd.Timestamp(as_of_date).date(), dt.time.max, tzinfo=dt.timezone.utc).isoformat()
    required = list(artifact["numeric_features"]) + ["primary_strategy"]
    for decision, features in zip(decisions, rows):
        missing = [name for name in required if name not in features or (
            name != "primary_strategy" and not math.isfinite(float(features[name]))
        ) or (name == "primary_strategy" and str(features[name]) not in artifact["strategy_categories"])]
        coverage = round(100.0 * (len(required) - len(missing)) / len(required), 2)
        if missing:
            decision["path_risk"] = {
                "state": NOT_AVAILABLE, "horizon_sessions": 10,
                "feature_coverage_pct": coverage, "missing_features": missing,
                "methodology_hash": EXPECTED_METHODOLOGY_HASH,
                "artifact_sha256": EXPECTED_ARTIFACT_FILE_SHA256,
                "as_of_timestamp": as_of_timestamp,
                "provenance": "completed_session_OHLCV_already_fetched_by_EOD_scanner",
                "advisory_only": True,
            }
            unavailable += 1
            continue
        frame = pd.DataFrame([features])
        prediction = model.predict(frame)
        state = str(prediction["state"][0])
        drivers = model.explain(frame)[0]
        decision["path_risk"] = {
            "state": state, "horizon_sessions": 10,
            "adverse_barrier_probability": float(prediction["adverse_barrier_probability"][0]),
            "predicted_adverse_magnitude_pct": float(prediction["predicted_adverse_magnitude"][0]),
            "feature_coverage_pct": coverage, "missing_features": [],
            "why_today": drivers,
            "baseline_context": _baseline_context(artifact, state),
            "methodology_version": VERSION,
            "methodology_hash": EXPECTED_METHODOLOGY_HASH,
            "artifact_sha256": EXPECTED_ARTIFACT_FILE_SHA256,
            "artifact_content_sha256": artifact["artifact_sha256"],
            "as_of_timestamp": as_of_timestamp,
            "provenance": "completed_session_OHLCV_already_fetched_by_EOD_scanner",
            "advisory_only": True,
        }
        available += 1
    return {"available": available, "not_available": unavailable, "failed": 0}
