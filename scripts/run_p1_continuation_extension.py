"""P1 — causal continuation / extension research (signal-level, not production)."""
import os
import sys
import json
import pickle
import hashlib
import datetime as dt
import numpy as np
import pandas as pd

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, PROJECT_ROOT)
DATASET = os.path.join(PROJECT_ROOT, "data/ml/step_6/expanded_strategy_dataset.csv")
CACHE = os.path.join(PROJECT_ROOT, "data/ml/step_6/cached_ohlcv_indicators.pkl")
OUT_DIR = os.path.join(PROJECT_ROOT, "data/research")
REPORT = os.path.join(OUT_DIR, "p1_continuation_extension_report.md")
P1_DATASET = os.path.join(OUT_DIR, "p1_qualified_signals.csv")
BUCKETS = os.path.join(OUT_DIR, "p1_feature_buckets.csv")
COMPARISON = os.path.join(OUT_DIR, "p1_test_comparison.csv")

from scripts.run_step_4f_embargo import apply_embargo

FEATURES = [
    "return_1d", "return_3d", "return_5d", "return_10d", "return_20d",
    "up_days_5", "up_days_10", "consecutive_up_days", "distance_from_ema20_pct",
    "distance_from_ema20_atr", "distance_from_ema50_pct", "distance_from_ema50_atr",
    "volume_ratio_20", "volume_acceleration_5_vs_20", "true_range_atr20",
    "atr5_atr20", "close_location_in_day_range", "upper_wick_pct_of_range",
    "body_pct_of_range", "distance_from_strategy_trigger_pct",
]


def sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def enrich_bars(raw):
    """Compute only indicators measurable at the close of each bar T."""
    df = raw.copy()
    for col in ["Open", "High", "Low", "Close", "Volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    close, high, low, opn, vol = (df[c] for c in ["Close", "High", "Low", "Open", "Volume"])
    prev_close = close.shift(1)
    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    df["ema20"] = close.ewm(span=20, adjust=False).mean()
    df["ema50"] = close.ewm(span=50, adjust=False).mean()
    df["atr20"] = tr.rolling(20, min_periods=20).mean()
    df["atr5"] = tr.rolling(5, min_periods=5).mean()
    df["volume_20d_avg"] = vol.shift(1).rolling(20, min_periods=20).mean()
    df["volume_ratio_20"] = vol / df["volume_20d_avg"]
    df["volume_acceleration_5_vs_20"] = vol.shift(1).rolling(5, min_periods=5).mean() / df["volume_20d_avg"]
    for n in [1, 3, 5, 10, 20]:
        df[f"return_{n}d"] = close.pct_change(n) * 100.0
    up = (close > prev_close).astype(float)
    df["up_days_5"] = up.rolling(5, min_periods=5).sum()
    df["up_days_10"] = up.rolling(10, min_periods=10).sum()
    group = (up != up.shift()).cumsum()
    df["consecutive_up_days"] = up.groupby(group).cumsum().where(up.astype(bool), 0)
    day_range = high - low
    df["distance_from_ema20_pct"] = (close / df["ema20"] - 1.0) * 100.0
    df["distance_from_ema20_atr"] = (close - df["ema20"]) / df["atr20"]
    df["distance_from_ema50_pct"] = (close / df["ema50"] - 1.0) * 100.0
    df["distance_from_ema50_atr"] = (close - df["ema50"]) / df["atr20"]
    df["true_range_atr20"] = tr / df["atr20"]
    df["atr5_atr20"] = df["atr5"] / df["atr20"]
    df["close_location_in_day_range"] = (close - low) / day_range.replace(0, np.nan)
    df["upper_wick_pct_of_range"] = (high - np.maximum(close, opn)) / day_range.replace(0, np.nan)
    df["body_pct_of_range"] = (close - opn).abs() / day_range.replace(0, np.nan)
    # A genuine trigger exists only for Donchian: the prior 20-session high.
    df["donchian_trigger"] = high.shift(1).rolling(20, min_periods=20).max()
    df["donchian_trigger_distance_pct"] = (close / df["donchian_trigger"] - 1.0) * 100.0
    return df


def causal_nr7(df_exp, cache):
    rows = []
    for _, row in df_exp[(df_exp.nr7 == True) & (df_exp.dist_ema50_pct > 0)].iterrows():
        sym, date = row.symbol, str(row.signal_date)[:10]
        bars = cache.get(sym)
        if bars is None or date not in bars.index:
            continue
        i = bars.index.get_loc(date)
        if i + 1 >= len(bars) or float(bars.iloc[i + 1].High) < float(bars.iloc[i].High):
            continue
        fill = float(bars.iloc[i + 1].Open) if float(bars.iloc[i + 1].Open) >= float(bars.iloc[i].High) else float(bars.iloc[i].High)
        r = row.to_dict(); r["strategy_name"] = "True NR7 Volatility Expansion Breakout"; r["entry_price"] = fill
        rows.append(r)
    other = df_exp[df_exp.strategy_name != "True NR7 Volatility Expansion Breakout"]
    return pd.concat([other, pd.DataFrame(rows)], ignore_index=True)


def outcome(bars, i, entry):
    """Future bars are labels only; a same-day double barrier is marked unknown."""
    out = {f"forward_return_{n}d": np.nan for n in [3, 5, 10]}
    out.update({"mfe_10d": np.nan, "mae_10d": np.nan, "hit_3_before_minus_2": np.nan, "hit_5_before_minus_3": np.nan})
    if i + 10 >= len(bars) or entry <= 0:
        return out
    future = bars.iloc[i + 1:i + 11]
    for n in [3, 5, 10]: out[f"forward_return_{n}d"] = (float(bars.iloc[i + n].Close) / entry - 1) * 100.0 - 0.30
    out["mfe_10d"] = (float(future.High.max()) / entry - 1) * 100.0
    out["mae_10d"] = (float(future.Low.min()) / entry - 1) * 100.0
    for target, stop, name in [(1.03, .98, "hit_3_before_minus_2"), (1.05, .97, "hit_5_before_minus_3")]:
        result = 0
        for _, bar in future.iterrows():
            hit_t, hit_s = float(bar.High) >= entry * target, float(bar.Low) <= entry * stop
            if hit_t and hit_s: result = np.nan; break
            if hit_t: result = 1; break
            if hit_s: result = 0; break
        out[name] = result
    return out


def build_p1_dataset():
    df_exp = pd.read_csv(DATASET)
    with open(CACHE, "rb") as f: raw_cache = pickle.load(f)
    cache = {s: enrich_bars(d) for s, d in raw_cache.items()}
    signals = causal_nr7(df_exp, cache)
    rows = []
    for _, signal in signals.iterrows():
        sym, date = signal.symbol, str(signal.signal_date)[:10]
        bars = cache.get(sym)
        if bars is None or date not in bars.index: continue
        i, bar = bars.index.get_loc(date), bars.loc[date]
        # Existing valid-data/regime rules plus frozen Step 10C price-volume qualification.
        if not (pd.notna(bar.volume_ratio_20) and bar.volume_ratio_20 >= 2.0 and bar.Close > bar.ema20 and signal.nifty_dist_ema50 > 0): continue
        entry = float(signal.entry_price) if pd.notna(signal.entry_price) else (float(bars.iloc[i + 1].Open) if i + 1 < len(bars) else np.nan)
        row = {"signal_date": date, "symbol": sym, "strategy_name": signal.strategy_name, "entry_price": entry,
               "close": float(bar.Close), "ema20": float(bar.ema20), "ema50": float(bar.ema50),
               "current_volume": float(bar.Volume), "volume_20d_avg": float(bar.volume_20d_avg),
               "regime": "BULLISH", "nifty_dist_ema50": float(signal.nifty_dist_ema50)}
        for feature in FEATURES:
            row[feature] = float(bar[feature]) if feature in bar and pd.notna(bar[feature]) else np.nan
        row["distance_from_strategy_trigger_pct"] = float(bar.donchian_trigger_distance_pct) if signal.strategy_name == "Donchian Channel Breakout" and pd.notna(bar.donchian_trigger_distance_pct) else np.nan
        row["bars_since_prior_strategy_signal"] = np.nan; row["price_change_since_prior_strategy_signal"] = np.nan
        row.update(outcome(bars, i, entry)); rows.append(row)
    df = pd.DataFrame(rows).sort_values(["symbol", "strategy_name", "signal_date"]).reset_index(drop=True)
    for _, idx in df.groupby(["symbol", "strategy_name"]).groups.items():
        inds = list(idx); dates = pd.to_datetime(df.loc[inds, "signal_date"]); closes = df.loc[inds, "close"].to_numpy()
        df.loc[inds, "bars_since_prior_strategy_signal"] = dates.diff().dt.days.to_numpy()
        df.loc[inds, "price_change_since_prior_strategy_signal"] = pd.Series(closes).pct_change().mul(100).to_numpy()
    return df


def stats(df):
    r = df.forward_return_10d.dropna(); pos, neg = r[r > 0].sum(), abs(r[r < 0].sum())
    return {"signals": len(df), "mean_10d": round(r.mean(), 2), "median_10d": round(r.median(), 2), "win_rate": round((r > 0).mean() * 100, 1),
            "mfe": round(df.mfe_10d.mean(), 2), "mae": round(df.mae_10d.mean(), 2), "profit_factor": round(pos / neg, 2) if neg else np.nan,
            "hit_3_before_minus_2": round(df.hit_3_before_minus_2.mean() * 100, 1), "hit_5_before_minus_3": round(df.hit_5_before_minus_3.mean() * 100, 1)}


def bucket_analysis(val):
    rows = []
    for f in FEATURES:
        x = val[[f, "forward_return_10d", "mfe_10d", "mae_10d", "hit_3_before_minus_2", "hit_5_before_minus_3"]].dropna(subset=[f])
        if len(x) < 40 or x[f].nunique() < 4: continue
        x = x.copy(); x["bucket"] = pd.qcut(x[f], 4, duplicates="drop")
        for bucket, g in x.groupby("bucket", observed=True):
            s = stats(g); rows.append({"feature": f, "bucket": str(bucket), **s})
    return pd.DataFrame(rows)


def choose_controls(val):
    candidates = []
    for feature in ["distance_from_ema20_atr", "return_5d", "upper_wick_pct_of_range", "true_range_atr20", "volume_acceleration_5_vs_20"]:
        x = val.dropna(subset=[feature, "forward_return_10d"])
        if len(x) < 40: continue
        q1, q3 = x[feature].quantile(.25), x[feature].quantile(.75)
        low, high = x[x[feature] <= q1], x[x[feature] >= q3]
        delta = low.forward_return_10d.mean() - high.forward_return_10d.mean()
        candidates.append((abs(delta), feature, "low", q1, delta, len(low), len(high)))
        candidates.append((abs(delta), feature, "high", q3, -delta, len(high), len(low)))
    candidates.sort(reverse=True)
    # Controls are validation-selected only; retain two distinct features with an adequate quartile sample.
    out, used = [], set()
    for _, f, direction, threshold, edge, n, _ in candidates:
        # A control is a candidate *entry improvement* only when its validation
        # bucket beat the opposing quartile.  Retain adverse relationships in
        # the descriptive table, but never promote them as filters.
        if f in used or n < 20 or edge < .25: continue
        out.append((f, direction, float(threshold), float(edge))); used.add(f)
        if len(out) == 2: break
    return out


def run():
    os.makedirs(OUT_DIR, exist_ok=True)
    df = build_p1_dataset(); df.to_csv(P1_DATASET, index=False)
    splits = apply_embargo(df, 10); val, test = splits["val"].copy(), splits["test"].copy()
    buckets = bucket_analysis(val); buckets.to_csv(BUCKETS, index=False)
    controls = choose_controls(val)
    comparison = [{"control": "All qualified signals", "split": "TEST", **stats(test)}]
    for feature, direction, threshold, edge in controls:
        mask = test[feature] <= threshold if direction == "low" else test[feature] >= threshold
        comparison.append({"control": f"{feature} {direction} (validation threshold {threshold:.3f})", "split": "TEST", **stats(test[mask])})
    comp = pd.DataFrame(comparison); comp.to_csv(COMPARISON, index=False)
    lines = ["# P1 — Continuation / Extension Research", "", f"Generated: {dt.datetime.now().isoformat()}", "",
             "## A. Dataset / dates / sample sizes", "", f"- Source SHA256: `{sha(DATASET)}`", f"- Qualified causal strategy signals: **{len(df)}**; validation: **{len(val)}**; test: **{len(test)}**.",
             f"- Dates: {df.signal_date.min()} to {df.signal_date.max()}; 80 cached symbols. This is signal-level research, not a portfolio return series.", "",
             "## B. Causal definitions", "", "- Qualification: existing signal + bullish Nifty regime (`nifty_dist_ema50 > 0`) + `Volume(T) / mean(Volume[T-20:T-1]) >= 2.0` + `Close(T) > EMA20(T)`.",
             "- Returns, EMAs, ATRs, prior-volume averages and candle structure use bars through T only. Donchian trigger distance is populated only for Donchian signals; unsupported strategy triggers are null.",
             "- Outcomes enter at the existing next-bar/causal entry price, deduct 0.30% cost, and use future T+1…T+10 bars only as labels. Same-day double-barrier outcomes are null rather than guessed.", "",
             "## C. Leakage checks", "", "- All features use `shift`, rolling windows ending at T, or EWM through T. Forward outcomes are built in a separate function from future bars and never referenced in feature construction.",
             "- Validation/test use the existing ten-trading-day embargo; validation-derived thresholds are applied unchanged to test.", "",
             "## D. Feature bucket results (validation)", "", buckets.to_markdown(index=False) if not buckets.empty else "No feature had sufficient validation variation.", "",
             "## E. Strategy / regime interactions", ""]
    strategy = val.groupby("strategy_name").apply(stats).apply(pd.Series).reset_index(); lines += [strategy.to_markdown(index=False), "", "All retained observations are bullish-regime by the existing qualification rule; no historical market-cap data was used.", "",
             "## F. Validation findings", "", "Controls selected solely from validation (minimum 20 signals and ≥0.25pp mean-return separation):"]
    lines += [f"- `{f}` {d}: threshold {t:.3f}; validation mean-return edge {e:+.2f}pp." for f, d, t, e in controls] or ["- No robust-enough control met the pre-specified minimum."]
    lines += ["", "## G. TEST comparison", "", comp.to_markdown(index=False), "", "## H. Portfolio-level comparison", "", "Not run: this P1 stage deliberately reports all qualified opportunities. A portfolio filter should only be considered after an independent confirmation period.", "",
              "## I. What appears useful / J. useless or redundant", "", "Use the validation bucket table and frozen TEST table above as descriptive evidence only. Features without monotonic separation or without a retained validation-selected control are not candidates for an Entry Timing model.", "",
              "## K. P1.3 recommendation", "", "Carry forward only controls that retain direction and economically meaningful signal count on TEST; retain strategy identity and do not turn this research into a production score.", "",
              "## L. Limitations", "", "Only 80 cached symbols are available; multiple strategy rows can share a symbol/date; no market-cap history; barrier ordering is unknown within an OHLC day; and test is a single descriptive period."]
    useful = [r for _, r in comp.iloc[1:].iterrows() if r["signals"] >= 20 and r["mean_10d"] > comp.iloc[0]["mean_10d"]]
    verdict = "PARTIAL GO" if useful else "NO-GO"
    lines += ["", f"# Verdict: {verdict}", "", "Proceed only to a small, strategy-aware P1.3 confirmation study; no production Entry Timing model or score is justified yet."]
    with open(REPORT, "w") as f: f.write("\n".join(lines) + "\n")
    print(json.dumps({"qualified": len(df), "validation": len(val), "test": len(test), "controls": controls, "verdict": verdict}, indent=2))


if __name__ == "__main__": run()
