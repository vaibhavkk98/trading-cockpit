"""ROLE-D1 causal outcome observation; no learning or decision behavior."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
from typing import Any, Mapping

import numpy as np
import pandas as pd

import database
from provider_symbols import yahoo_nse_symbol


ROLE_OUTCOME_VERSION = "ROLE_D1_OUTCOME_V1"
HORIZONS = (1, 3, 5, 10, 20)
BARRIER_ORDERING = "SAME_BAR_ADVERSE_FIRST"
OUTCOME_METHOD_HASH = hashlib.sha256(json.dumps({
    "version": ROLE_OUTCOME_VERSION, "horizons": HORIZONS,
    "barriers": {"targets_pct": [3, 5], "adverse_pct": [2, 3]},
    "same_bar_ordering": BARRIER_ORDERING,
    "sessions": "completed_trading_sessions_T_plus_1_onward",
}, sort_keys=True).encode()).hexdigest()


def _normalized_history(frame: pd.DataFrame | None, observation_date: Any) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame()
    result = frame.copy()
    result.columns = [str(column).lower() for column in result.columns]
    required = {"open", "high", "low", "close"}
    if not required.issubset(result.columns):
        return pd.DataFrame()
    index = pd.to_datetime(result.index).tz_localize(None).normalize()
    result.index = index
    cutoff = pd.Timestamp(observation_date).normalize()
    result = result.loc[result.index <= cutoff].sort_index(kind="mergesort")
    return result[~result.index.duplicated(keep="last")]


def _first_session(window: pd.DataFrame, column: str, threshold: float, direction: str) -> int | str:
    values = pd.to_numeric(window[column], errors="coerce").to_numpy(dtype=float)
    hits = values >= threshold if direction == "UP" else values <= threshold
    positions = np.flatnonzero(hits)
    return int(positions[0] + 1) if len(positions) else "NOT_REACHED"


def canonical_barrier_outcome(window: pd.DataFrame, reference_price: float,
                              target_pct: float, adverse_pct: float) -> dict[str, Any]:
    """Use the frozen conservative rule: adverse wins a same-session tie."""
    window = window.copy(); window.columns = [str(column).lower() for column in window.columns]
    target = reference_price * (1.0 + target_pct / 100.0)
    adverse = reference_price * (1.0 - adverse_pct / 100.0)
    target_session = _first_session(window, "high", target, "UP")
    adverse_session = _first_session(window, "low", adverse, "DOWN")
    first_hit = "NEITHER"
    success = False
    for session, (_, bar) in enumerate(window.iterrows(), 1):
        hit_target = float(bar["high"]) >= target
        hit_adverse = float(bar["low"]) <= adverse
        if hit_target and hit_adverse:
            first_hit = "SAME_BAR_ADVERSE_FIRST"; break
        if hit_adverse:
            first_hit = "ADVERSE_FIRST"; break
        if hit_target:
            first_hit = "TARGET_FIRST"; success = True; break
    return {
        "value": success, "first_hit": first_hit,
        "target_session": target_session, "adverse_session": adverse_session,
        "same_bar_ordering": BARRIER_ORDERING,
    }


def calculate_horizon_outcome(future_sessions: pd.DataFrame, reference_price: float,
                              horizon: int) -> dict[str, Any]:
    """Calculate one immutable matured horizon from the first T+1..T+h bars."""
    if horizon not in HORIZONS or len(future_sessions) < horizon:
        raise ValueError("Requested ROLE horizon has not matured")
    if not math.isfinite(reference_price) or reference_price <= 0:
        raise ValueError("Reference price is unavailable")
    window = future_sessions.iloc[:horizon].copy()
    window.columns = [str(column).lower() for column in window.columns]
    close = pd.to_numeric(window["close"], errors="coerce")
    high = pd.to_numeric(window["high"], errors="coerce")
    low = pd.to_numeric(window["low"], errors="coerce")
    if close.isna().any() or high.isna().any() or low.isna().any():
        raise ValueError("Matured horizon contains missing OHLC values")
    path = np.concatenate(([reference_price], close.to_numpy(dtype=float)))
    running_peak = np.maximum.accumulate(path)
    drawdown = path / running_peak - 1.0
    plus3 = _first_session(window, "high", reference_price * 1.03, "UP")
    plus5 = _first_session(window, "high", reference_price * 1.05, "UP")
    minus2 = _first_session(window, "low", reference_price * 0.98, "DOWN")
    minus3 = _first_session(window, "low", reference_price * 0.97, "DOWN")
    return {
        "horizon_sessions": horizon,
        "close_return_pct": float((close.iloc[-1] / reference_price - 1.0) * 100.0),
        "mfe_pct": float((high.max() / reference_price - 1.0) * 100.0),
        "mae_pct": float((low.min() / reference_price - 1.0) * 100.0),
        "maximum_gain_pct": float((close.max() / reference_price - 1.0) * 100.0),
        "maximum_drawdown_pct": float(drawdown.min() * 100.0),
        "plus_3_pct_reached": plus3 != "NOT_REACHED",
        "plus_5_pct_reached": plus5 != "NOT_REACHED",
        "minus_2_pct_reached": minus2 != "NOT_REACHED",
        "minus_3_pct_reached": minus3 != "NOT_REACHED",
        "sessions_to_plus_3_pct": plus3, "sessions_to_plus_5_pct": plus5,
        "sessions_to_minus_2_pct": minus2, "sessions_to_minus_3_pct": minus3,
        "plus_5_before_minus_3": canonical_barrier_outcome(window, reference_price, 5.0, 3.0),
        "source_start_date": window.index[0].date().isoformat(),
        "source_end_date": window.index[-1].date().isoformat(),
        "outcome_methodology_hash": OUTCOME_METHOD_HASH,
    }


def _history_for_symbol(histories: Mapping[str, pd.DataFrame], symbol: str) -> pd.DataFrame | None:
    for key in (symbol, symbol.upper(), yahoo_nse_symbol(symbol)):
        if key in histories:
            return histories[key]
    return None


def observe_pending_recommendations(histories: Mapping[str, pd.DataFrame],
                                    observation_date: Any) -> dict[str, Any]:
    """Advance all non-mature recommendation outcomes from supplied daily bars."""
    recommendations = database.load_recommendations_for_role_observation(OUTCOME_METHOD_HASH)
    horizon_saved = horizon_idempotent = failed = 0
    for recommendation in recommendations:
        try:
            try:
                reference = float(recommendation.get("reference_price"))
            except (TypeError, ValueError):
                reference = float("nan")
            signal_date = pd.Timestamp(recommendation["signal_date"]).normalize()
            raw = _history_for_symbol(histories, str(recommendation["symbol"]))
            frame = _normalized_history(raw, observation_date)
            missingness = []
            if frame.empty or not math.isfinite(reference) or reference <= 0:
                lifecycle = "NOT_AVAILABLE"; future = pd.DataFrame(); missingness = ["completed_session_ohlc" if frame.empty else "reference_price"]
            else:
                future = frame.loc[frame.index > signal_date].iloc[:20]
                lifecycle = "PENDING" if len(future) == 0 else "PARTIAL"
            sessions = int(len(future))
            matured = [horizon for horizon in HORIZONS if sessions >= horizon]
            last_date = future.index[-1].date().isoformat() if sessions else None
            state = {
                **recommendation, "outcome_contract_version": ROLE_OUTCOME_VERSION,
                "outcome_methodology_hash": OUTCOME_METHOD_HASH,
                "lifecycle_state": lifecycle, "sessions_observed": sessions,
                "last_observation_date": last_date,
                "source": {"provider": "existing_completed_session_OHLCV", "observation_cutoff": str(observation_date)[:10],
                           "source_max_date": frame.index[-1].date().isoformat() if not frame.empty else "NOT_AVAILABLE",
                           "recommendation_snapshot_hash": recommendation["recommendation_snapshot_hash"]},
                "completeness": {"matured_horizons": matured, "required_horizons": list(HORIZONS)},
                "missingness": missingness,
            }
            if not math.isfinite(reference) or reference <= 0:
                state["reference_price"] = None
            header = database.persist_role_observation_state(state)
            for horizon in matured:
                payload = calculate_horizon_outcome(future, reference, horizon)
                result = database.persist_role_outcome_horizon(
                    header["observation_id"], horizon, payload["source_end_date"], payload
                )
                horizon_saved += int(result["saved"]); horizon_idempotent += int(not result["saved"])
            if sessions >= 20:
                state["lifecycle_state"] = "MATURE"
                database.persist_role_observation_state(state)
        except Exception:
            failed += 1
    return {
        "recommendations_considered": len(recommendations), "horizons_saved": horizon_saved,
        "horizons_idempotent": horizon_idempotent, "failed": failed,
        "lifecycle_counts": database.role_outcome_lifecycle_counts(OUTCOME_METHOD_HASH),
        "outcome_methodology_hash": OUTCOME_METHOD_HASH,
    }
