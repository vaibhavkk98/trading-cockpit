"""Causal LSV-V1 feature contract for qualified-opportunity snapshots.

This module is descriptive only.  It does not qualify, rank, allocate, size, or
execute opportunities.  Every calculation is bounded by the supplied signal
date and missing inputs remain explicit.
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Iterable, Mapping

import numpy as np
import pandas as pd


NOT_AVAILABLE = "NOT_AVAILABLE"
LSV_VERSION = "LSV_V1"
FAMILIES = (
    "price_state", "participation", "relative_demand", "volatility_state",
    "price_response", "positioning", "liquidity", "environment",
)
LSV_METHOD_HASH = hashlib.sha256(
    json.dumps({"version": LSV_VERSION, "families": FAMILIES}, sort_keys=True).encode()
).hexdigest()


def _number(value: Any) -> float | str:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return NOT_AVAILABLE
    return result if math.isfinite(result) else NOT_AVAILABLE


def _pct_change(series: pd.Series, sessions: int) -> float | str:
    values = pd.to_numeric(series, errors="coerce").dropna()
    if len(values) <= sessions or values.iloc[-sessions - 1] <= 0:
        return NOT_AVAILABLE
    return _number((values.iloc[-1] / values.iloc[-sessions - 1] - 1.0) * 100.0)


def _percentile(prior: pd.Series, value: Any, minimum: int = 20) -> float | str:
    numeric = pd.to_numeric(prior, errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
    value = _number(value)
    if value == NOT_AVAILABLE or len(numeric) < minimum:
        return NOT_AVAILABLE
    array = numeric.to_numpy(dtype=float)
    return float(100.0 * ((array < value).sum() + 0.5 * (array == value).sum()) / len(array))


def _slice(frame: pd.DataFrame | None, as_of_date: Any) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame()
    result = frame.copy().sort_index()
    cutoff = pd.Timestamp(as_of_date).normalize()
    index = pd.to_datetime(result.index).tz_localize(None).normalize()
    result = result.loc[index <= cutoff].copy()
    result.index = index[index <= cutoff]
    return result


def empty_vector(signal_date: Any = None) -> dict[str, Any]:
    fields = {
        "price_state": ("return_5d_pct", "return_10d_pct", "return_20d_pct", "return_60d_pct", "ema20_extension_pct", "ema50_extension_pct", "recent_drawdown_pct", "distance_from_20d_high_pct", "distance_from_60d_high_pct"),
        "participation": ("volume_ratio_20d", "turnover_ratio_20d", "volume_persistence_5d", "up_volume_down_volume_ratio_20d", "delivery_pct", "delivery_ratio"),
        "relative_demand": ("stock_vs_nifty500_return_5d_pct", "stock_vs_nifty500_return_10d_pct", "stock_vs_nifty500_return_20d_pct", "cross_sectional_rs_percentile"),
        "volatility_state": ("atr_pct", "realized_volatility_5d_ann_pct", "realized_volatility_20d_ann_pct", "short_long_volatility_ratio", "range_compression_5d_vs_20d", "range_expansion_ratio"),
        "price_response": ("close_location_value", "overnight_gap_pct", "intraday_return_pct", "upper_wick_ratio", "lower_wick_ratio", "range_expansion_ratio"),
        "positioning": ("futures_open_interest", "futures_basis_pct"),
        "liquidity": ("traded_value", "traded_value_ratio_20d", "traded_value_percentile_252d", "amihud_price_impact"),
        "environment": ("trend", "breadth", "volatility", "sector_participation", "investor_participation", "cross_asset", "external_event_risk"),
    }
    vector = {family: {field: NOT_AVAILABLE for field in names} for family, names in fields.items()}
    missingness = [f"{family}.{field}" for family, names in fields.items() for field in names]
    vector.update({"contract_version": LSV_VERSION, "methodology_hash": LSV_METHOD_HASH,
                   "signal_date": str(signal_date)[:10] if signal_date else None,
                   "source_timestamp": None, "missingness": missingness, "provenance": []})
    return vector


def build_causal_vector(stock_history: pd.DataFrame, benchmark_history: pd.DataFrame | None,
                        signal_date: Any) -> dict[str, Any]:
    """Build one vector from completed bars at or before ``signal_date``."""
    stock = _slice(stock_history, signal_date)
    benchmark = _slice(benchmark_history, signal_date)
    vector = empty_vector(signal_date)
    required = {"Open", "High", "Low", "Close", "Volume"}
    if stock.empty or not required.issubset(stock.columns):
        vector["missingness"] = ["stock_ohlcv"]
        return vector

    close = pd.to_numeric(stock["Close"], errors="coerce")
    high = pd.to_numeric(stock["High"], errors="coerce")
    low = pd.to_numeric(stock["Low"], errors="coerce")
    open_ = pd.to_numeric(stock["Open"], errors="coerce")
    volume = pd.to_numeric(stock["Volume"], errors="coerce")
    latest_close = _number(close.iloc[-1])
    ema20 = close.ewm(span=20, adjust=False, min_periods=20).mean().iloc[-1]
    ema50 = close.ewm(span=50, adjust=False, min_periods=50).mean().iloc[-1]
    high20 = high.tail(20).max(); high60 = high.tail(60).max()
    returns = {n: _pct_change(close, n) for n in (5, 10, 20, 60)}
    vector["price_state"] = {
        "return_5d_pct": returns[5], "return_10d_pct": returns[10],
        "return_20d_pct": returns[20], "return_60d_pct": returns[60],
        "ema20_extension_pct": _number((latest_close / ema20 - 1) * 100) if latest_close != NOT_AVAILABLE and pd.notna(ema20) and ema20 > 0 else NOT_AVAILABLE,
        "ema50_extension_pct": _number((latest_close / ema50 - 1) * 100) if latest_close != NOT_AVAILABLE and pd.notna(ema50) and ema50 > 0 else NOT_AVAILABLE,
        "recent_drawdown_pct": _number((latest_close / high20 - 1) * 100) if latest_close != NOT_AVAILABLE and pd.notna(high20) and high20 > 0 else NOT_AVAILABLE,
        "distance_from_20d_high_pct": _number((latest_close / high20 - 1) * 100) if latest_close != NOT_AVAILABLE and pd.notna(high20) and high20 > 0 else NOT_AVAILABLE,
        "distance_from_60d_high_pct": _number((latest_close / high60 - 1) * 100) if latest_close != NOT_AVAILABLE and pd.notna(high60) and high60 > 0 else NOT_AVAILABLE,
    }

    traded = close * volume
    prior_volume = volume.shift(1).rolling(20).mean()
    prior_turnover = traded.shift(1).rolling(20).mean()
    up = volume.tail(20)[close.diff().tail(20) > 0]
    down = volume.tail(20)[close.diff().tail(20) <= 0]
    persistence = (volume.tail(5) > prior_volume.tail(5)).mean() if prior_volume.tail(5).notna().all() else np.nan
    vector["participation"].update({
        "volume_ratio_20d": _number(volume.iloc[-1] / prior_volume.iloc[-1]) if pd.notna(prior_volume.iloc[-1]) and prior_volume.iloc[-1] > 0 else NOT_AVAILABLE,
        "turnover_ratio_20d": _number(traded.iloc[-1] / prior_turnover.iloc[-1]) if pd.notna(prior_turnover.iloc[-1]) and prior_turnover.iloc[-1] > 0 else NOT_AVAILABLE,
        "volume_persistence_5d": _number(persistence),
        "up_volume_down_volume_ratio_20d": _number(up.mean() / down.mean()) if len(up) and len(down) and down.mean() > 0 else NOT_AVAILABLE,
    })

    benchmark_close = pd.to_numeric(benchmark.get("Close"), errors="coerce") if not benchmark.empty and "Close" in benchmark else pd.Series(dtype=float)
    for sessions in (5, 10, 20):
        stock_return = returns[sessions]
        benchmark_return = _pct_change(benchmark_close, sessions)
        value = _number(stock_return - benchmark_return) if stock_return != NOT_AVAILABLE and benchmark_return != NOT_AVAILABLE else NOT_AVAILABLE
        vector["relative_demand"][f"stock_vs_nifty500_return_{sessions}d_pct"] = value

    previous_close = close.shift(1)
    true_range = pd.concat([(high - low), (high - previous_close).abs(), (low - previous_close).abs()], axis=1).max(axis=1)
    atr20 = true_range.rolling(20).mean().iloc[-1]
    log_returns = np.log(close / close.shift(1))
    rv5 = log_returns.tail(5).std(ddof=1) * np.sqrt(252) * 100
    rv20 = log_returns.tail(20).std(ddof=1) * np.sqrt(252) * 100
    range_pct = (high - low) / previous_close.replace(0, np.nan) * 100
    range_expansion = true_range.iloc[-1] / true_range.shift(1).rolling(20).mean().iloc[-1]
    vector["volatility_state"] = {
        "atr_pct": _number(atr20 / latest_close * 100) if latest_close != NOT_AVAILABLE and pd.notna(atr20) and latest_close > 0 else NOT_AVAILABLE,
        "realized_volatility_5d_ann_pct": _number(rv5), "realized_volatility_20d_ann_pct": _number(rv20),
        "short_long_volatility_ratio": _number(rv5 / rv20) if pd.notna(rv20) and rv20 > 0 else NOT_AVAILABLE,
        "range_compression_5d_vs_20d": _number(range_pct.tail(5).mean() / range_pct.tail(20).mean()) if range_pct.tail(20).mean() > 0 else NOT_AVAILABLE,
        "range_expansion_ratio": _number(range_expansion),
    }

    day_range = high.iloc[-1] - low.iloc[-1]
    vector["price_response"] = {
        "close_location_value": _number(((close.iloc[-1] - low.iloc[-1]) - (high.iloc[-1] - close.iloc[-1])) / day_range) if day_range > 0 else NOT_AVAILABLE,
        "overnight_gap_pct": _number((open_.iloc[-1] / previous_close.iloc[-1] - 1) * 100) if pd.notna(previous_close.iloc[-1]) and previous_close.iloc[-1] > 0 else NOT_AVAILABLE,
        "intraday_return_pct": _number((close.iloc[-1] / open_.iloc[-1] - 1) * 100) if open_.iloc[-1] > 0 else NOT_AVAILABLE,
        "upper_wick_ratio": _number((high.iloc[-1] - max(open_.iloc[-1], close.iloc[-1])) / day_range) if day_range > 0 else NOT_AVAILABLE,
        "lower_wick_ratio": _number((min(open_.iloc[-1], close.iloc[-1]) - low.iloc[-1]) / day_range) if day_range > 0 else NOT_AVAILABLE,
        "range_expansion_ratio": _number(range_expansion),
    }
    traded_value = _number(traded.iloc[-1])
    vector["liquidity"] = {
        "traded_value": traded_value,
        "traded_value_ratio_20d": _number(traded.iloc[-1] / prior_turnover.iloc[-1]) if pd.notna(prior_turnover.iloc[-1]) and prior_turnover.iloc[-1] > 0 else NOT_AVAILABLE,
        "traded_value_percentile_252d": _percentile(traded.iloc[max(0, len(traded) - 253):-1], traded.iloc[-1]),
        "amihud_price_impact": _number(abs(close.pct_change(fill_method=None).iloc[-1]) / traded.iloc[-1]) if traded.iloc[-1] > 0 else NOT_AVAILABLE,
    }
    vector["source_timestamp"] = stock.index[-1].isoformat()
    vector["provenance"] = [{"source": "completed_session_ohlcv", "as_of": stock.index[-1].date().isoformat()},
                            {"source": "nifty500_completed_session_ohlcv", "as_of": benchmark.index[-1].date().isoformat() if not benchmark.empty else NOT_AVAILABLE}]
    vector["missingness"] = [
        f"{family}.{field}" for family in FAMILIES for field, value in vector[family].items()
        if value == NOT_AVAILABLE
    ]
    return vector


def assign_cross_sectional_rs_percentiles(opportunities: Iterable[Mapping[str, Any]]) -> None:
    """Assign same-signal-date percentiles in place, using qualified rows only."""
    rows = list(opportunities)
    groups: dict[str, list[Mapping[str, Any]]] = {}
    for row in rows:
        groups.setdefault(str(row.get("signal_date") or row.get("data_as_of"))[:10], []).append(row)
    for group in groups.values():
        values = []
        for row in group:
            value = ((row.get("lsv_v1") or {}).get("relative_demand") or {}).get("stock_vs_nifty500_return_20d_pct")
            numeric = _number(value)
            if numeric != NOT_AVAILABLE:
                values.append((row, numeric))
        array = np.array([value for _, value in values], dtype=float)
        for row, value in values:
            percentile = float(100.0 * ((array < value).sum() + 0.5 * (array == value).sum()) / len(array))
            row["lsv_v1"]["relative_demand"]["cross_sectional_rs_percentile"] = percentile
            missing = "relative_demand.cross_sectional_rs_percentile"
            row["lsv_v1"]["missingness"] = [item for item in row["lsv_v1"].get("missingness", []) if item != missing]


def reconstruct_from_decision_payload(decision: Mapping[str, Any], signal_date: Any) -> dict[str, Any]:
    """Conservative backfill from values already frozen in a decision payload."""
    vector = empty_vector(signal_date)
    features = decision.get("ha_features") if isinstance(decision.get("ha_features"), Mapping) else {}
    for days in (5, 10, 20):
        value = features.get(f"ret_{days}d")
        vector["price_state"][f"return_{days}d_pct"] = _number(value)
    close = _number(decision.get("entry_price") or decision.get("close"))
    for days, source in ((10, "stock_minus_nifty500_ret_10d"), (20, "stock_minus_nifty500_ret_20d")):
        vector["relative_demand"][f"stock_vs_nifty500_return_{days}d_pct"] = _number(features.get(source))
    vector["participation"]["volume_ratio_20d"] = _number(decision.get("volume_ratio_20"))
    ema20 = _number(decision.get("ema20") or decision.get("ema_20"))
    if close != NOT_AVAILABLE and ema20 != NOT_AVAILABLE and ema20 > 0:
        vector["price_state"]["ema20_extension_pct"] = _number((close / ema20 - 1) * 100)
    vector["provenance"] = [{"source": "canonical_daily_opportunity_payload", "as_of": str(signal_date)[:10]}]
    vector["missingness"] = [f"{family}.{field}" for family in FAMILIES for field, value in vector[family].items() if value == NOT_AVAILABLE]
    return vector
