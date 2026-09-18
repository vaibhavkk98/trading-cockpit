"""Frozen, causal Systematic Engine V1B portfolio-risk primitives.

This module is deliberately free of database and execution side effects.  It
turns completed-session observations into risk inputs used by isolated paper
research accounts only.
"""
from __future__ import annotations

import datetime as dt
import math
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from provider_symbols import yahoo_nse_symbol


VERSION = "SYSTEMATIC_ENGINE_V1B_PORTFOLIO_RISK_V1"
RV20_WINDOW = 20
RV20_MIN_RETURNS = 15
TARGET_POSITION_RISK = .004
NORMAL_HEAT_LIMIT = .036
HARD_HEAT_LIMIT = .040
MAX_SECTOR_HEAT_SHARE = .35
MAX_SIGNAL_DATE_HEAT_SHARE = .35
CORRELATION_WINDOW = 20
CORRELATION_MIN_OVERLAP = 10


def _causal_closes(histories: Mapping[str, pd.DataFrame], symbol: str,
                    market_date: dt.date) -> pd.Series | None:
    frame = histories.get(yahoo_nse_symbol(symbol))
    if frame is None:
        frame = histories.get(symbol)
    if frame is None or frame.empty:
        return None
    data = frame.copy()
    data.columns = [str(column).lower().replace("adjusted_", "") for column in data.columns]
    if "close" not in data.columns:
        return None
    data.index = pd.to_datetime(data.index).tz_localize(None).normalize()
    values = pd.to_numeric(data.loc[data.index <= pd.Timestamp(market_date), "close"], errors="coerce")
    values = values.replace([np.inf, -np.inf], np.nan).dropna()
    return values if not values.empty else None


def realized_volatility_20(histories: Mapping[str, pd.DataFrame], symbol: str,
                           market_date: dt.date) -> dict[str, Any]:
    """Trailing daily realized volatility percentage, using only data through T."""
    closes = _causal_closes(histories, symbol, market_date)
    returns = closes.pct_change(fill_method=None).dropna().tail(RV20_WINDOW) if closes is not None else pd.Series(dtype=float)
    if len(returns) < RV20_MIN_RETURNS:
        return {"rv20_pct": "NOT_AVAILABLE", "valid_returns": int(len(returns)),
                "window": RV20_WINDOW, "minimum_returns": RV20_MIN_RETURNS}
    value = float(returns.std(ddof=1) * 100)
    if not math.isfinite(value) or value < 0:
        return {"rv20_pct": "NOT_AVAILABLE", "valid_returns": int(len(returns)),
                "window": RV20_WINDOW, "minimum_returns": RV20_MIN_RETURNS}
    return {"rv20_pct": value, "valid_returns": int(len(returns)),
            "window": RV20_WINDOW, "minimum_returns": RV20_MIN_RETURNS}


def candidate_risk_proxy(atr_pct: Any, rv20_pct: Any) -> dict[str, Any]:
    """Frozen max(ATR%, RV20 daily %) proxy; ATR is mandatory."""
    try:
        atr = float(atr_pct)
    except (TypeError, ValueError):
        atr = float("nan")
    if not math.isfinite(atr) or atr <= 0:
        return {"risk_proxy_pct": "NOT_AVAILABLE", "risk_proxy_coverage": "NOT_AVAILABLE",
                "eligible": False}
    try:
        rv = float(rv20_pct)
    except (TypeError, ValueError):
        rv = float("nan")
    if math.isfinite(rv) and rv >= 0:
        return {"risk_proxy_pct": max(atr, rv), "risk_proxy_coverage": "ATR_AND_RV20",
                "eligible": True}
    return {"risk_proxy_pct": atr, "risk_proxy_coverage": "ATR_ONLY", "eligible": True}


def causal_correlation(histories: Mapping[str, pd.DataFrame], left_symbol: str,
                       right_symbol: str, market_date: dt.date) -> dict[str, Any]:
    """Causal trailing correlation with an explicit minimum-overlap contract."""
    left = _causal_closes(histories, left_symbol, market_date)
    right = _causal_closes(histories, right_symbol, market_date)
    if left is None or right is None:
        return {"correlation": "NOT_AVAILABLE", "overlap": 0}
    pair = pd.concat([left.pct_change(fill_method=None), right.pct_change(fill_method=None)], axis=1,
                     join="inner").dropna().tail(CORRELATION_WINDOW)
    if len(pair) < CORRELATION_MIN_OVERLAP:
        return {"correlation": "NOT_AVAILABLE", "overlap": int(len(pair))}
    value = float(pair.iloc[:, 0].corr(pair.iloc[:, 1]))
    if not math.isfinite(value):
        return {"correlation": "NOT_AVAILABLE", "overlap": int(len(pair))}
    return {"correlation": max(-1., min(1., value)), "overlap": int(len(pair))}


def weighted_average_correlation(histories: Mapping[str, pd.DataFrame], candidate_symbol: str,
                                 existing: Sequence[Mapping[str, Any]],
                                 market_date: dt.date) -> dict[str, Any]:
    weighted_sum = weight_sum = 0.
    observations = []
    for item in existing:
        result = causal_correlation(histories, candidate_symbol, str(item.get("symbol") or ""), market_date)
        value = result["correlation"]
        weight = max(0., float(item.get("risk_rupees") or 0.))
        observations.append({"symbol": item.get("symbol"), **result, "risk_weight": weight})
        if value != "NOT_AVAILABLE" and weight > 0:
            weighted_sum += float(value) * weight
            weight_sum += weight
    if weight_sum <= 0:
        return {"weighted_avg_corr": "NOT_AVAILABLE", "correlation_state": "NOT_AVAILABLE",
                "correlation_multiplier": 1., "observations": observations}
    value = weighted_sum / weight_sum
    multiplier = min(1.5, 1. + .5 * max(0., value))
    return {"weighted_avg_corr": value, "correlation_state": "AVAILABLE",
            "correlation_multiplier": multiplier, "observations": observations}


def frozen_position_risk(position) -> dict[str, Any]:
    """Risk heat uses current market value and the immutable admission-time proxy."""
    import json
    payload = json.loads(position.payload or "{}")
    try:
        proxy = float(payload["risk_proxy_pct"])
    except (KeyError, TypeError, ValueError):
        proxy = float("nan")
    multiplier = float(payload.get("correlation_multiplier") or 1.)
    market_value = float(position.quantity * position.current_mark)
    raw = market_value * proxy / 100 if math.isfinite(proxy) and proxy > 0 else 0.
    return {"symbol": position.symbol, "sector": payload.get("sector") or "UNKNOWN",
            "signal_date": payload.get("signal_date") or "NOT_AVAILABLE",
            "market_value": market_value, "risk_proxy_pct": proxy if math.isfinite(proxy) else "NOT_AVAILABLE",
            "raw_risk_rupees": raw, "correlation_multiplier": multiplier,
            "weighted_avg_corr": payload.get("weighted_avg_corr", "NOT_AVAILABLE"),
            "risk_rupees": raw * multiplier}


def portfolio_heat_state(positions, nav: float) -> dict[str, Any]:
    rows = [frozen_position_risk(position) for position in positions]
    total = sum(row["risk_rupees"] for row in rows)
    sector, signal = {}, {}
    for row in rows:
        sector[row["sector"]] = sector.get(row["sector"], 0.) + row["risk_rupees"]
        signal[row["signal_date"]] = signal.get(row["signal_date"], 0.) + row["risk_rupees"]
    return {"positions": rows, "heat_rupees": total, "portfolio_heat_pct": total / nav * 100 if nav else None,
            "remaining_normal_heat_rupees": max(0., nav * NORMAL_HEAT_LIMIT - total),
            "sector_heat": sector, "signal_date_heat": signal}
