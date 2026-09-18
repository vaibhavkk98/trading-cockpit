"""Canonical PB-R2B production-native feature and outcome contract.

The same functions consume immutable NSE EQ bhavcopy rows in historical
replay and live EOD.  They never read adjusted research bars, provider data,
future universe membership, or outcomes while constructing features.
"""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from historical_analogs_expanded import _enrich_group, _nifty50_return_context


VERSION = "PB_NATIVE_FEATURES_V1"
HORIZONS = (5, 10, 20)
QUALIFICATION_VERSION = "LIVE_SCREENER_FROZEN_2026_08_NATIVE_RAW_V1"
FEATURES = (
    "native_return_5d", "native_return_10d", "native_return_20d", "native_return_60d",
    "native_ema20_extension", "native_ema50_extension", "native_drawdown_20d",
    "native_distance_high_20d", "native_distance_high_60d", "native_volume_ratio",
    "native_turnover_ratio", "native_volume_persistence_5d", "native_up_down_volume_ratio_20d",
    "native_excess_5d", "native_excess_10d", "native_excess_20d", "native_rs_percentile_20d",
    "native_atr_pct", "native_realized_vol_5d", "native_realized_vol_20d",
    "native_realized_vol_ratio_5v20", "native_range_expansion_ratio",
    "native_close_location_value", "native_gap_return", "native_intraday_return",
    "native_upper_wick_ratio", "native_lower_wick_ratio", "native_log_traded_value",
    "native_traded_value_ratio", "native_traded_value_percentile",
    "native_log_amihud_impact_20d", "native_market_return_20d",
    "native_market_trend_ema20", "native_market_realized_vol_20d",
    "native_market_breadth_ema20",
)


def _safe_div(a: pd.Series, b: pd.Series) -> pd.Series:
    return a.div(b.where(b.ne(0)))


def _finite(value: object) -> float:
    number = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    return float(number) if pd.notna(number) and np.isfinite(number) else np.nan


def security_state(raw: pd.DataFrame, nifty50_returns: pd.DataFrame) -> pd.DataFrame:
    """Return causal qualification and PB state for every raw session."""
    required = {"trade_date", "open", "high", "low", "close", "volume"}
    missing = sorted(required.difference(raw.columns))
    if missing:
        raise ValueError(f"missing raw NSE fields: {missing}")
    frame = _enrich_group(raw, nifty50_returns)
    close = pd.to_numeric(frame.close, errors="coerce")
    opn = pd.to_numeric(frame.open, errors="coerce")
    high = pd.to_numeric(frame.high, errors="coerce")
    low = pd.to_numeric(frame.low, errors="coerce")
    volume = pd.to_numeric(frame.volume, errors="coerce")
    traded = close * volume
    ret = close.pct_change(fill_method=None)
    prev = close.shift(1)
    true_range = pd.concat([high-low, (high-prev).abs(), (low-prev).abs()], axis=1).max(axis=1)
    prior_volume20 = volume.shift(1).rolling(20, min_periods=15).mean()
    prior_traded20 = traded.shift(1).rolling(20, min_periods=15).mean()
    volume_ratio = _safe_div(volume, prior_volume20)
    up_volume = volume.where(ret.gt(0), 0).rolling(20, min_periods=15).sum()
    down_volume = volume.where(ret.lt(0), 0).rolling(20, min_periods=15).sum()
    bar_range = high-low
    for n in (5, 10, 20, 60):
        frame[f"native_return_{n}d"] = (close/close.shift(n)-1)*100
    frame["native_ema20_extension"] = (close/close.ewm(span=20, adjust=False).mean()-1)*100
    frame["native_ema50_extension"] = (close/close.ewm(span=50, adjust=False).mean()-1)*100
    frame["native_drawdown_20d"] = (close/close.rolling(20, min_periods=20).max()-1)*100
    frame["native_distance_high_20d"] = (close/high.rolling(20, min_periods=20).max()-1)*100
    frame["native_distance_high_60d"] = (close/high.rolling(60, min_periods=60).max()-1)*100
    frame["native_volume_ratio"] = volume_ratio
    frame["native_turnover_ratio"] = _safe_div(traded, prior_traded20)
    frame["native_volume_persistence_5d"] = volume_ratio.gt(1).rolling(5, min_periods=5).mean()
    frame["native_up_down_volume_ratio_20d"] = _safe_div(up_volume, down_volume)
    frame["native_atr_pct"] = _safe_div(true_range.rolling(14, min_periods=14).mean(), close)*100
    frame["native_realized_vol_5d"] = ret.rolling(5, min_periods=5).std(ddof=1)*np.sqrt(252)*100
    frame["native_realized_vol_20d"] = ret.rolling(20, min_periods=20).std(ddof=1)*np.sqrt(252)*100
    frame["native_realized_vol_ratio_5v20"] = _safe_div(frame.native_realized_vol_5d, frame.native_realized_vol_20d)
    frame["native_range_expansion_ratio"] = _safe_div(true_range, true_range.shift(1).rolling(20, min_periods=15).mean())
    frame["native_close_location_value"] = _safe_div(close-low, bar_range)
    frame["native_gap_return"] = (opn/prev-1)*100
    frame["native_intraday_return"] = (close/opn-1)*100
    frame["native_upper_wick_ratio"] = _safe_div(high-pd.concat([opn, close], axis=1).max(axis=1), bar_range)
    frame["native_lower_wick_ratio"] = _safe_div(pd.concat([opn, close], axis=1).min(axis=1)-low, bar_range)
    frame["native_log_traded_value"] = np.log1p(traded.clip(lower=0))
    frame["native_traded_value_ratio"] = _safe_div(traded, prior_traded20)
    frame["native_log_amihud_impact_20d"] = np.log1p(_safe_div(ret.abs()*100, traded).rolling(20, min_periods=15).mean()*1e9)

    uptrend = close.gt(frame.ema50_live) & frame.ema50_live.gt(frame.ema200_live)
    rs_pass = frame.rs_3m_live.gt(0) | frame.rs_score_live.gt(0)
    vcp = frame.vcp_active_live | frame.vcp_ratio_live.le(1.05)
    breakout = frame.donchian20_breakout_live | frame.donchian50_breakout_live
    bounce = frame.ema20_bounce_live | frame.ema50_bounce_live
    rs_momentum = rs_pass & frame.rsi14_live.ge(60)
    technical = uptrend & rs_pass & frame.turnover20_live.ge(20_000_000) & (vcp | breakout | bounce | rs_momentum)
    positive = close.gt(prev)
    history_ready = frame.index.to_series().ge(199)
    # A raw discontinuity invalidates T and the following 19 observed sessions.
    jump = ret.abs().ge(0.40).fillna(False).astype(int)
    frame["pb_raw_discontinuity_quarantine"] = jump.rolling(20, min_periods=1).max().astype(bool)
    frame["pb_qualified"] = (
        technical & frame.volume_ratio_live.round(2).ge(2.0) & close.gt(frame.ema20_live)
        & positive & history_ready & frame.eligible.fillna(False).astype(bool)
        & ~frame.pb_raw_discontinuity_quarantine
    )
    frame["positive_price_gate_pass"] = positive
    frame["primary_strategy"] = np.select(
        [breakout, vcp, bounce, rs_momentum],
        ["Donchian Channel Breakout", "VCP Volatility Contraction Breakout", "EMA Pullback / Bounce", "RS Momentum Breakout"],
        default="NOT_AVAILABLE",
    )
    return frame


def benchmark_state(benchmarks: pd.DataFrame) -> pd.DataFrame:
    frame = benchmarks[benchmarks.index_name.eq("Nifty 500")].copy()
    frame.trade_date = pd.to_datetime(frame.trade_date, errors="raise").dt.normalize()
    frame = frame.sort_values("trade_date", kind="mergesort").drop_duplicates("trade_date", keep="last")
    close = pd.to_numeric(frame.close, errors="coerce")
    out = frame[["trade_date"]].copy()
    out["native_market_return_20d"] = (close/close.shift(20)-1)*100
    out["native_market_trend_ema20"] = (close/close.ewm(span=20, adjust=False).mean()-1)*100
    out["native_market_realized_vol_20d"] = close.pct_change(fill_method=None).rolling(20, min_periods=20).std(ddof=1)*np.sqrt(252)*100
    return out


def _outcomes(frame: pd.DataFrame, positions: np.ndarray, action_dates: set[pd.Timestamp]) -> dict[str, np.ndarray]:
    close = pd.to_numeric(frame.close, errors="coerce").to_numpy(float)
    high = pd.to_numeric(frame.high, errors="coerce").to_numpy(float)
    low = pd.to_numeric(frame.low, errors="coerce").to_numpy(float)
    dates = pd.to_datetime(frame.trade_date).dt.normalize().to_numpy()
    result: dict[str, np.ndarray] = {}
    for horizon in HORIZONS:
        values = {name: np.full(len(positions), np.nan) for name in ("mfe", "mae", "forward_volatility", "success_5_before_3", "adverse_first")}
        end_dates = np.full(len(positions), np.datetime64("NaT"), dtype="datetime64[ns]")
        eligible = positions+horizon < len(frame)
        for out_i, pos in enumerate(positions):
            if not eligible[out_i]:
                continue
            future_dates = pd.to_datetime(dates[pos+1:pos+horizon+1]).normalize()
            if any(day in action_dates for day in future_dates):
                continue
            ref = close[pos]
            hs, ls = high[pos+1:pos+horizon+1], low[pos+1:pos+horizon+1]
            target, adverse = hs >= ref*1.05, ls <= ref*.97
            first_target = int(np.argmax(target)) if target.any() else 999
            first_adverse = int(np.argmax(adverse)) if adverse.any() else 999
            values["mfe"][out_i] = (np.nanmax(hs)/ref-1)*100
            values["mae"][out_i] = (np.nanmin(ls)/ref-1)*100
            returns = np.diff(np.r_[ref, close[pos+1:pos+horizon+1]])/np.r_[ref, close[pos+1:pos+horizon]]
            values["forward_volatility"][out_i] = np.std(returns, ddof=1)*np.sqrt(252)*100
            values["success_5_before_3"][out_i] = float(first_target < first_adverse)
            values["adverse_first"][out_i] = float(first_adverse <= first_target and first_adverse < 999)
            end_dates[out_i] = dates[pos+horizon]
        for name, array in values.items():
            result[f"{name}_{horizon}d"] = array
        result[f"label_end_date_{horizon}d"] = end_dates
    return result


def build_dataset(raw_bars: pd.DataFrame, eligibility: pd.DataFrame, benchmarks: pd.DataFrame,
                  corporate_actions: pd.DataFrame | None = None) -> pd.DataFrame:
    """Materialize the complete native qualified population and causal labels."""
    bars = raw_bars.copy()
    bars.trade_date = pd.to_datetime(bars.trade_date, errors="raise").dt.normalize()
    bars = bars[bars.identity_status.eq("RESOLVED_ISIN")].drop_duplicates(["canonical_security_id", "trade_date"], keep="last")
    elig = eligibility[["trade_date", "canonical_security_id", "eligible"]].copy()
    elig.trade_date = pd.to_datetime(elig.trade_date, errors="raise").dt.normalize()
    bars = bars.merge(elig, on=["trade_date", "canonical_security_id"], how="left", validate="one_to_one")
    bars.eligible = bars.eligible.fillna(False).astype(bool)
    nifty50 = _nifty50_return_context(benchmarks)
    actions: dict[str, set[pd.Timestamp]] = {}
    if corporate_actions is not None and len(corporate_actions):
        ca = corporate_actions[corporate_actions.action_type.isin(["SPLIT", "BONUS"])].copy()
        ca.ex_date = pd.to_datetime(ca.ex_date, errors="coerce").dt.normalize()
        actions = {str(k): set(pd.to_datetime(g.ex_date).dropna()) for k, g in ca.groupby("canonical_security_id")}
    rows: list[pd.DataFrame] = []
    for sid, raw in bars.groupby("canonical_security_id", sort=True, observed=True):
        frame = security_state(raw.sort_values("trade_date", kind="mergesort").reset_index(drop=True), nifty50)
        positions = np.flatnonzero(frame.pb_qualified.to_numpy())
        if not len(positions):
            continue
        picked = frame.iloc[positions].copy()
        picked["canonical_security_id"] = str(sid)
        picked["signal_date"] = pd.to_datetime(picked.trade_date).dt.normalize()
        picked["symbol"] = picked.exchange_symbol.astype(str)
        picked["opportunity_id"] = ["PBNATIVE_"+sha256(f"{sid}|{d:%Y-%m-%d}".encode()).hexdigest()[:20] for d in picked.signal_date]
        picked["qualification_version"] = QUALIFICATION_VERSION
        picked["reference_price"] = pd.to_numeric(picked.close, errors="coerce")
        for key, value in _outcomes(frame, positions, actions.get(str(sid), set())).items():
            picked[key] = value
        rows.append(picked[["opportunity_id", "canonical_security_id", "symbol", "signal_date", "primary_strategy", "qualification_version", "positive_price_gate_pass", "reference_price", *[f for f in FEATURES if f in picked], *[c for c in picked if c.startswith(("mfe_", "mae_", "forward_volatility_", "success_5_before_3_", "adverse_first_", "label_end_date_"))]]])
    if not rows:
        raise RuntimeError("native qualification produced no opportunities")
    data = pd.concat(rows, ignore_index=True)
    data = data.merge(benchmark_state(benchmarks), left_on="signal_date", right_on="trade_date", how="left", validate="many_to_one").drop(columns="trade_date")
    for horizon in (5, 10, 20):
        data[f"native_excess_{horizon}d"] = data[f"native_return_{horizon}d"] - data.native_market_return_20d*(horizon/20)
    data["native_rs_percentile_20d"] = data.groupby("signal_date").native_excess_20d.rank(pct=True, method="average")*100
    data["native_traded_value_percentile"] = data.groupby("signal_date").native_log_traded_value.rank(pct=True, method="average")*100
    data["native_market_breadth_ema20"] = data.groupby("signal_date").native_ema20_extension.transform(lambda x: float((x>0).mean())*100)
    data["feature_source_max_date"] = data.signal_date
    data["source_contract"] = VERSION
    data = data.sort_values(["signal_date", "canonical_security_id"], kind="mergesort").reset_index(drop=True)
    if data.duplicated(["canonical_security_id", "signal_date"]).any():
        raise RuntimeError("native opportunity identity is not unique")
    if (data.feature_source_max_date > data.signal_date).any():
        raise RuntimeError("future feature leakage")
    return data


def load_historical_inputs(root: Path) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    layers = root/"data/ha_d1/layers"
    raw_cols = ["trade_date", "canonical_security_id", "identity_status", "exchange_symbol", "open", "high", "low", "close", "volume"]
    bars = pd.concat([pq.read_table(path, columns=raw_cols).to_pandas() for path in sorted((layers/"01_raw_exchange_observations").glob("equity_eq_*.parquet"))], ignore_index=True)
    elig_cols = ["trade_date", "canonical_security_id", "eligible"]
    eligibility = pd.concat([pq.read_table(path, columns=elig_cols).to_pandas() for path in sorted((layers/"05_historical_liquid_nse_universe").glob("eligibility_*.parquet"))], ignore_index=True)
    benchmarks = pq.read_table(layers/"06_benchmark_series/benchmarks.parquet").to_pandas()
    actions = pq.read_table(layers/"03_corporate_actions/corporate_actions.parquet").to_pandas()
    return bars, eligibility, benchmarks, actions


def build_live_eod_snapshot(raw_bars: pd.DataFrame, eligibility: pd.DataFrame, benchmarks: pd.DataFrame,
                            as_of_session: object) -> pd.DataFrame:
    """The live adapter is the historical builder constrained through T."""
    cutoff = pd.Timestamp(as_of_session).normalize()
    bars = raw_bars[pd.to_datetime(raw_bars.trade_date).dt.normalize().le(cutoff)]
    elig = eligibility[pd.to_datetime(eligibility.trade_date).dt.normalize().le(cutoff)]
    bench = benchmarks[pd.to_datetime(benchmarks.trade_date).dt.normalize().le(cutoff)]
    dataset = build_dataset(bars, elig, bench, None)
    return dataset[dataset.signal_date.eq(cutoff)].drop(columns=[c for c in dataset if c.startswith(("mfe_", "mae_", "forward_volatility_", "success_5_before_3_", "adverse_first_", "label_end_date_"))])


# Explicit aliases make the parity contract inspectable: both environments
# invoke the identical state implementation, not parallel formula copies.
historical_security_state = security_state
live_security_state = security_state
