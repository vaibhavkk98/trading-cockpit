"""Frozen, causal Systematic Engine V1D dynamic-exposure calculations.

This module contains no execution logic.  It converts completed-session
portfolio and NIFTY 500 state into a cap on *new* B1 risk admission.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from typing import Mapping

import numpy as np
import pandas as pd


VERSION = "SYSTEMATIC_ENGINE_DYNAMIC_EXPOSURE_V1D"
BASE_NORMAL_HEAT = .036
HARD_HEAT_LIMIT = .04
VOL_WINDOW = 20
VOL_PERCENTILE_WINDOW = 252
MIN_BENCHMARK_CLOSES = VOL_WINDOW + VOL_PERCENTILE_WINDOW
BENCHMARK_KEYS = ("NIFTY500", "NIFTY_500", "^CRSLDX", "NIFTY 500")


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str,
        separators=(",", ":")).encode()).hexdigest()


CONFIGS = {
    "SHADOW_EXPOSURE_PORTFOLIO": {
        "policy": "D1", "control_account": "SHADOW_RISK_BUDGET",
        "portfolio_drawdown_breaks": [-.05, -.10, -.15, -.20],
        "portfolio_multipliers": [1., .8, .5, .25],
        "market_layer": False, "normal_heat": BASE_NORMAL_HEAT,
        "hard_heat": HARD_HEAT_LIMIT, "target_position_risk": .004,
        "priority": "P0_FRESHNESS", "hold_sessions": 10,
        "replacement": False, "decision_authority": False,
    },
    "SHADOW_EXPOSURE_COMBINED": {
        "policy": "D2", "control_account": "SHADOW_RISK_BUDGET",
        "portfolio_drawdown_breaks": [-.05, -.10, -.15, -.20],
        "portfolio_multipliers": [1., .8, .5, .25],
        "market_layer": True, "benchmark": "NIFTY500",
        "benchmark_trend": "CLOSE_OVER_EMA50_MINUS_1",
        "benchmark_volatility": "RV20_PERCENTILE_OVER_CAUSAL_252_RV20_VALUES",
        "market_fallback": "PORTFOLIO_MULTIPLIER_ONLY",
        "combined_floor": .20, "normal_heat": BASE_NORMAL_HEAT,
        "hard_heat": HARD_HEAT_LIMIT, "target_position_risk": .004,
        "priority": "P0_FRESHNESS", "hold_sessions": 10,
        "replacement": False, "decision_authority": False,
    },
}
CONFIG_HASHES = {key: _digest(value) for key, value in CONFIGS.items()}
METHODOLOGY_HASHES = {key: _digest({"version": VERSION, "account": key,
    "config_hash": CONFIG_HASHES[key], "code_identity": "dynamic_exposure_engine.py:v1"})
    for key in CONFIGS}


def portfolio_multiplier(drawdown: float) -> float:
    """Frozen piecewise-linear D1 schedule."""
    value = min(0., float(drawdown))
    if value >= -.05:
        return 1.
    if value >= -.10:
        return 1. + (value + .05) * 4.
    if value >= -.15:
        return .8 + (value + .10) * 6.
    if value >= -.20:
        return .5 + (value + .15) * 5.
    return .25


def trend_multiplier(trend: float) -> float:
    value = float(trend)
    if value >= 0.:
        return 1.
    if value <= -.05:
        return .75
    return 1. + value * 5.


def volatility_multiplier(percentile: float) -> float:
    value = float(percentile)
    if value <= 70.:
        return 1.
    if value >= 90.:
        return .8
    return 1. - (value - 70.) * .01


def combined_multiplier(portfolio: float, market: float) -> float:
    return max(.20, float(portfolio) * float(market))


def _benchmark_frame(histories: Mapping[str, pd.DataFrame], as_of: dt.date):
    frame = next((histories.get(key) for key in BENCHMARK_KEYS
                  if histories.get(key) is not None), None)
    if frame is None or frame.empty:
        return None
    data = frame.copy()
    data.columns = [str(column).lower().replace("adjusted_", "") for column in data.columns]
    if "close" not in data:
        return None
    data.index = pd.to_datetime(data.index).tz_localize(None).normalize()
    close = pd.to_numeric(data["close"], errors="coerce").dropna()
    close = close.loc[close.index <= pd.Timestamp(as_of)]
    return close[~close.index.duplicated(keep="last")].sort_index()


def market_state(histories: Mapping[str, pd.DataFrame], as_of: dt.date) -> dict:
    """Causal D2 market state; missing data is explicit and never imputed."""
    close = _benchmark_frame(histories, as_of)
    unavailable = {"status": "NOT_AVAILABLE", "benchmark": "NIFTY500",
        "benchmark_close": None, "ema50": None, "trend": None,
        "trend_multiplier": None, "benchmark_rv20": None,
        "volatility_percentile": None, "volatility_multiplier": None,
        "market_multiplier": None, "observations": 0 if close is None else len(close)}
    if close is None or len(close) < MIN_BENCHMARK_CLOSES:
        unavailable["reason"] = "INSUFFICIENT_BENCHMARK_HISTORY"
        return unavailable
    returns = close.pct_change()
    rv20 = returns.rolling(VOL_WINDOW, min_periods=VOL_WINDOW).std(ddof=1) * np.sqrt(252.) * 100.
    rv_history = rv20.dropna().iloc[-VOL_PERCENTILE_WINDOW:]
    if len(rv_history) != VOL_PERCENTILE_WINDOW:
        unavailable["reason"] = "INSUFFICIENT_VOLATILITY_HISTORY"
        return unavailable
    current_rv = float(rv_history.iloc[-1])
    # Inclusive mid-rank makes ties deterministic and uses no future values.
    less = float((rv_history < current_rv).sum())
    equal = float((rv_history == current_rv).sum())
    percentile = (less + .5 * equal) / len(rv_history) * 100.
    ema50 = float(close.ewm(span=50, adjust=False, min_periods=50).mean().iloc[-1])
    current_close = float(close.iloc[-1]); trend = current_close / ema50 - 1.
    trend_mult = trend_multiplier(trend); vol_mult = volatility_multiplier(percentile)
    return {"status": "AVAILABLE", "benchmark": "NIFTY500",
        "benchmark_close": current_close, "ema50": ema50, "trend": trend,
        "trend_multiplier": trend_mult, "benchmark_rv20": current_rv,
        "volatility_percentile": percentile, "volatility_multiplier": vol_mult,
        "market_multiplier": min(trend_mult, vol_mult), "observations": len(close),
        "as_of": as_of.isoformat()}


def exposure_state(nav: float, prior_peak_nav: float | None,
                   histories: Mapping[str, pd.DataFrame], as_of: dt.date,
                   include_market: bool) -> dict:
    nav = float(nav)
    prior_peak = max(nav, float(prior_peak_nav or nav))
    drawdown = nav / prior_peak - 1. if prior_peak > 0 else 0.
    portfolio = portfolio_multiplier(drawdown)
    market = market_state(histories, as_of) if include_market else {
        "status": "NOT_APPLICABLE", "market_multiplier": 1., "benchmark": "NIFTY500"}
    fallback = bool(include_market and market["status"] != "AVAILABLE")
    market_mult = 1. if fallback else float(market["market_multiplier"])
    combined = combined_multiplier(portfolio, market_mult)
    return {"nav": nav, "prior_peak_nav": prior_peak, "current_drawdown": drawdown,
        "portfolio_multiplier": portfolio, "market": market,
        "market_multiplier": None if fallback else market_mult,
        "market_fallback": fallback,
        "fallback_policy": "PORTFOLIO_MULTIPLIER_ONLY" if fallback else None,
        "combined_multiplier": combined,
        "base_normal_heat_pct": BASE_NORMAL_HEAT * 100.,
        "dynamic_normal_heat_pct": BASE_NORMAL_HEAT * combined * 100.,
        "dynamic_normal_heat_fraction": BASE_NORMAL_HEAT * combined,
        "hard_heat_pct": HARD_HEAT_LIMIT * 100., "as_of": as_of.isoformat()}
