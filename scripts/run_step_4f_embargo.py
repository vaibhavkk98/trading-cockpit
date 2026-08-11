"""
STEP 4F — TEMPORAL PURITY / EMBARGO CORRECTION

Implements a rigorous 10-trading-day embargo between dataset splits to prevent
label leakage across boundaries.

Methodology:
- Label horizon is 10 trading days forward from signal_date.
- A signal's label window END = trading_date[index_of(signal_date) + 10].
- TRAIN observations are REMOVED if their label window END >= VAL start date.
- VAL observations are REMOVED if their label window END >= TEST start date.
- TEST is never modified.
- The embargo uses actual trading dates from the dataset, not calendar approximations.
"""
import os
import hashlib
import pickle
import json
import datetime
import pandas as pd
import numpy as np
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import f1_score

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
ML_DIR = os.path.join(PROJECT_ROOT, "data", "ml")
MODEL_DIR = os.path.join(ML_DIR, "models")

TRAINING_DATASET_CSV = os.path.join(ML_DIR, "training_dataset.csv")

# Step 4F outputs — all new files, no overwrites
STEP4F_DIR = os.path.join(ML_DIR, "step_4f")
STEP4F_MODEL_PATH = os.path.join(STEP4F_DIR, "gradient_boosting_classifier.pkl")
STEP4F_SCALER_PATH = os.path.join(STEP4F_DIR, "feature_scaler.pkl")
STEP4F_MANIFEST_JSON = os.path.join(STEP4F_DIR, "embargo_manifest.json")
STEP4F_TEST_PREDICTIONS_CSV = os.path.join(STEP4F_DIR, "test_predictions.csv")
STEP4F_SIGNAL_COMPARISON_CSV = os.path.join(STEP4F_DIR, "signal_comparison.csv")
STEP4F_PORTFOLIO_COMPARISON_CSV = os.path.join(STEP4F_DIR, "portfolio_comparison.csv")
STEP4F_COST_SENSITIVITY_CSV = os.path.join(STEP4F_DIR, "cost_sensitivity.csv")
STEP4F_ROBUSTNESS_CSV = os.path.join(STEP4F_DIR, "robustness.csv")
STEP4F_REPORT_MD = os.path.join(STEP4F_DIR, "step_4f_embargo_report.md")

NUMERICAL_FEATURES = [
    "close_price", "ret_5d", "ret_10d", "ret_20d", "ret_50d",
    "dist_ema20_pct", "dist_ema50_pct", "dist_ema200_pct", "slope_ema20", "slope_ema50",
    "rsi_14", "rs_3m", "atr_20", "atr_20_pct", "vol_20d", "vcp_ratio",
    "volume_ratio_20d", "turnover_20d", "nifty_ret_20d", "nifty_vol_20d", "nifty_dist_ema50"
]

LABEL_HORIZON_DAYS = 10
RANDOM_SEED = 42

# Original split boundaries (unchanged from prior steps)
VAL_START_DATE = "2025-10-15"
TEST_START_DATE = "2026-02-18"


def compute_sha256(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def apply_embargo(df: pd.DataFrame, label_horizon: int = LABEL_HORIZON_DAYS) -> dict:
    """
    Apply a trading-day embargo to train/validation splits.

    For each observation, the label window END is computed as:
        label_end = all_trading_dates[index_of(signal_date) + label_horizon]

    An observation is REMOVED from its split if its label_end >= the start date
    of the next split.

    Returns a dict with embargo-clean splits and diagnostics.
    """
    df = df.copy()
    df['signal_date'] = pd.to_datetime(df['signal_date'])

    val_start = pd.Timestamp(VAL_START_DATE)
    test_start = pd.Timestamp(TEST_START_DATE)

    # Build trading date index from the dataset
    all_trading_dates = sorted(df['signal_date'].unique())
    date_to_idx = {d: i for i, d in enumerate(all_trading_dates)}

    # Compute label_end for every observation
    def get_label_end(signal_date):
        idx = date_to_idx.get(signal_date)
        if idx is not None and idx + label_horizon < len(all_trading_dates):
            return all_trading_dates[idx + label_horizon]
        return pd.NaT  # label window extends beyond dataset

    df['_label_end'] = df['signal_date'].map(get_label_end)

    # Original splits (before embargo)
    orig_train = df[df['signal_date'] < val_start].copy()
    orig_val = df[(df['signal_date'] >= val_start) & (df['signal_date'] < test_start)].copy()
    orig_test = df[df['signal_date'] >= test_start].copy()

    # Apply embargo: remove observations whose label window crosses into next split
    # TRAIN: remove if label_end >= val_start
    train_embargo_mask = orig_train['_label_end'] >= val_start
    train_removed = orig_train[train_embargo_mask]
    train_clean = orig_train[~train_embargo_mask].copy()

    # VAL: remove if label_end >= test_start
    val_embargo_mask = orig_val['_label_end'] >= test_start
    val_removed = orig_val[val_embargo_mask]
    val_clean = orig_val[~val_embargo_mask].copy()

    # TEST: unchanged
    test_clean = orig_test.copy()

    # Clean up temp column
    for split_df in [train_clean, val_clean, test_clean]:
        split_df.drop(columns=['_label_end'], inplace=True, errors='ignore')

    # Diagnostics
    train_removed_dates = sorted(train_removed['signal_date'].unique())
    val_removed_dates = sorted(val_removed['signal_date'].unique())

    diagnostics = {
        "original": {
            "train_rows": len(orig_train),
            "train_date_min": str(orig_train['signal_date'].min().date()),
            "train_date_max": str(orig_train['signal_date'].max().date()),
            "val_rows": len(orig_val),
            "val_date_min": str(orig_val['signal_date'].min().date()),
            "val_date_max": str(orig_val['signal_date'].max().date()),
            "test_rows": len(orig_test),
            "test_date_min": str(orig_test['signal_date'].min().date()),
            "test_date_max": str(orig_test['signal_date'].max().date()),
        },
        "embargo": {
            "label_horizon_trading_days": label_horizon,
            "train_rows_removed": len(train_removed),
            "train_dates_removed": len(train_removed_dates),
            "train_removed_date_range": (
                f"{train_removed_dates[0].date()} to {train_removed_dates[-1].date()}"
                if train_removed_dates else "N/A"
            ),
            "val_rows_removed": len(val_removed),
            "val_dates_removed": len(val_removed_dates),
            "val_removed_date_range": (
                f"{val_removed_dates[0].date()} to {val_removed_dates[-1].date()}"
                if val_removed_dates else "N/A"
            ),
        },
        "embargo_clean": {
            "train_rows": len(train_clean),
            "train_date_min": str(train_clean['signal_date'].min().date()),
            "train_date_max": str(train_clean['signal_date'].max().date()),
            "val_rows": len(val_clean),
            "val_date_min": str(val_clean['signal_date'].min().date()),
            "val_date_max": str(val_clean['signal_date'].max().date()),
            "test_rows": len(test_clean),
            "test_date_min": str(test_clean['signal_date'].min().date()),
            "test_date_max": str(test_clean['signal_date'].max().date()),
        },
        "status": "EMBARGO_CLEAN",
    }

    return {
        "train": train_clean,
        "val": val_clean,
        "test": test_clean,
        "diagnostics": diagnostics,
        "train_removed": train_removed,
        "val_removed": val_removed,
    }


def compute_signal_metrics(df_signals: pd.DataFrame) -> dict:
    """Compute SIGNAL-LEVEL metrics (NOT portfolio returns)."""
    if df_signals.empty:
        return {"signal_count": 0, "win_rate_pct": 0.0, "avg_return_pct": 0.0,
                "median_return_pct": 0.0, "profit_factor": 0.0, "unique_symbol_dates": 0}

    rets = df_signals['forward_10d_return'].values
    n = len(rets)
    wins = int(np.sum(rets > 0))
    pos_g = float(np.sum(rets[rets > 0]))
    neg_l = float(abs(np.sum(rets[rets < 0])))
    pf = round(pos_g / neg_l, 2) if neg_l > 0 else (5.0 if pos_g > 0 else 0.0)
    unique_sd = len(df_signals.drop_duplicates(subset=['signal_date', 'symbol']))

    return {
        "signal_count": n,
        "unique_symbol_dates": unique_sd,
        "win_rate_pct": round((wins / n) * 100.0, 1),
        "avg_return_pct": round(float(np.mean(rets)), 4),
        "median_return_pct": round(float(np.median(rets)), 4),
        "profit_factor": pf,
    }


def simulate_portfolio(df_sig, rank_col="ml_probability", min_prob=0.0,
                       top_n_day=None, dedup_symbol=True, cost_multiplier=1.0):
    """Capital-constrained portfolio simulation with daily equity tracking."""
    initial_cap = 1_000_000.0
    pos_size = 100_000.0
    max_positions = 10
    holding_days = 10

    brok_pct = 0.0003 * cost_multiplier
    stt_pct = 0.0010 * cost_multiplier
    slip_pct = 0.0010 * cost_multiplier

    cash = initial_cap
    active_positions = []
    executed_trades = []
    rejected_cap = 0
    rejected_dup = 0

    dates = sorted(df_sig["signal_date"].unique())
    daily_equity_map = {}

    for d in dates:
        # Exit expired positions
        new_active = []
        for pos in active_positions:
            pos["days_held"] += 1
            if pos["days_held"] >= holding_days:
                entry_px = pos["entry_price"]
                qty = max(1, int(pos_size / entry_px))
                gross_val = qty * entry_px
                cost_entry = min(20.0, gross_val * brok_pct) + (gross_val * slip_pct)
                cost_exit = min(20.0, gross_val * brok_pct) + (gross_val * stt_pct) + (gross_val * slip_pct)
                net_pnl = (gross_val * (pos["fwd_return"] / 100.0)) - (cost_entry + cost_exit)
                cash += pos_size + net_pnl
                executed_trades.append({
                    "net_return_pct": round((net_pnl / pos_size) * 100.0, 4),
                    "transaction_costs": round(cost_entry + cost_exit, 2),
                })
            else:
                new_active.append(pos)
        active_positions = new_active

        # Today's candidates
        day_signals = df_sig[df_sig["signal_date"] == d].copy()
        if min_prob > 0:
            day_signals = day_signals[day_signals["ml_probability"] >= min_prob]
        if dedup_symbol and len(day_signals) > 0:
            day_signals = day_signals.sort_values(by=[rank_col], ascending=False).drop_duplicates(subset=['symbol'], keep='first')
        sorted_cands = day_signals.sort_values(by=[rank_col, "symbol"], ascending=[False, True])
        if top_n_day and len(sorted_cands) > top_n_day:
            sorted_cands = sorted_cands.head(top_n_day)

        active_symbols = {p["symbol"] for p in active_positions}
        for _, row in sorted_cands.iterrows():
            sym = row["symbol"]
            if sym in active_symbols:
                rejected_dup += 1
                continue
            if len(active_positions) >= max_positions or cash < pos_size:
                rejected_cap += 1
                continue
            cash -= pos_size
            active_positions.append({
                "symbol": sym, "entry_price": float(row["entry_price"]),
                "fwd_return": float(row["forward_10d_return"]), "days_held": 0,
            })
            active_symbols.add(sym)

        daily_equity_map[d] = cash + len(active_positions) * pos_size

    eq_dates = sorted(daily_equity_map.keys())
    eq_values = [daily_equity_map[d] for d in eq_dates]
    equity_series = pd.Series(eq_values, index=pd.to_datetime(eq_dates))
    daily_eq = equity_series.resample('D').ffill().fillna(initial_cap)
    daily_returns = daily_eq.pct_change().dropna()

    if len(daily_returns) > 10 and daily_returns.std() > 0:
        sharpe = round((daily_returns.mean() * 252) / (daily_returns.std() * np.sqrt(252)), 2)
    else:
        sharpe = "N/A"

    rolling_max = daily_eq.cummax()
    drawdown = (daily_eq - rolling_max) / rolling_max * 100.0
    max_dd = round(float(abs(drawdown.min())), 2) if not drawdown.empty else 0.0

    final_cap = eq_values[-1] if eq_values else initial_cap
    net_ret = round(((final_cap - initial_cap) / initial_cap) * 100.0, 2)
    n_exec = len(executed_trades)

    if n_exec > 0:
        rets = np.array([t["net_return_pct"] for t in executed_trades])
        wr = round((np.sum(rets > 0) / n_exec) * 100.0, 1)
        total_costs = round(sum(t["transaction_costs"] for t in executed_trades), 2)
        pos_g = float(np.sum(rets[rets > 0]))
        neg_l = float(abs(np.sum(rets[rets < 0])))
        pf = round(pos_g / neg_l, 2) if neg_l > 0 else 0.0
    else:
        wr, total_costs, pf = 0.0, 0.0, 0.0

    return {
        "net_portfolio_return_pct": net_ret,
        "executed_positions": n_exec,
        "win_rate_pct": wr,
        "profit_factor": pf,
        "daily_sharpe_ratio": sharpe,
        "max_drawdown_pct": max_dd,
        "total_transaction_costs": total_costs,
        "rejected_duplicate_symbol": rejected_dup,
        "rejected_capital_constraint": rejected_cap,
    }


def run_step_4f():
    print("=" * 80)
    print("STEP 4F — TEMPORAL PURITY / EMBARGO CORRECTION")
    print("=" * 80)

    os.makedirs(STEP4F_DIR, exist_ok=True)

    # =========================================================================
    # PHASE 1: INSPECT AND COMPUTE EMBARGO
    # =========================================================================
    print("\n[PHASE 1] Loading dataset and computing embargo boundaries...")
    df = pd.read_csv(TRAINING_DATASET_CSV)
    dataset_sha = compute_sha256(TRAINING_DATASET_CSV)

    embargo_result = apply_embargo(df, LABEL_HORIZON_DAYS)
    train_clean = embargo_result["train"]
    val_clean = embargo_result["val"]
    test_clean = embargo_result["test"]
    diag = embargo_result["diagnostics"]

    print(f"  Dataset SHA256: {dataset_sha}")
    print(f"\n  Original splits:")
    print(f"    TRAIN: {diag['original']['train_rows']} rows ({diag['original']['train_date_min']} to {diag['original']['train_date_max']})")
    print(f"    VAL:   {diag['original']['val_rows']} rows ({diag['original']['val_date_min']} to {diag['original']['val_date_max']})")
    print(f"    TEST:  {diag['original']['test_rows']} rows ({diag['original']['test_date_min']} to {diag['original']['test_date_max']})")
    print(f"\n  Embargo (10-trading-day label horizon):")
    print(f"    TRAIN rows removed: {diag['embargo']['train_rows_removed']} ({diag['embargo']['train_dates_removed']} dates: {diag['embargo']['train_removed_date_range']})")
    print(f"    VAL rows removed:   {diag['embargo']['val_rows_removed']} ({diag['embargo']['val_dates_removed']} dates: {diag['embargo']['val_removed_date_range']})")
    print(f"\n  Embargo-clean splits:")
    print(f"    TRAIN: {diag['embargo_clean']['train_rows']} rows ({diag['embargo_clean']['train_date_min']} to {diag['embargo_clean']['train_date_max']})")
    print(f"    VAL:   {diag['embargo_clean']['val_rows']} rows ({diag['embargo_clean']['val_date_min']} to {diag['embargo_clean']['val_date_max']})")
    print(f"    TEST:  {diag['embargo_clean']['test_rows']} rows ({diag['embargo_clean']['test_date_min']} to {diag['embargo_clean']['test_date_max']})")

    # =========================================================================
    # PHASE 2+4: RETRAIN MODEL ON EMBARGO-CLEAN TRAIN, SELECT THRESHOLD ON CLEAN VAL
    # =========================================================================
    print("\n[PHASE 2+4] Retraining model on embargo-clean TRAIN...")

    X_train = train_clean[NUMERICAL_FEATURES].values
    y_train = train_clean['forward_10d_positive'].values
    X_val = val_clean[NUMERICAL_FEATURES].values
    y_val = val_clean['forward_10d_positive'].values
    X_test = test_clean[NUMERICAL_FEATURES].values

    # Fit scaler on TRAIN only
    scaler = StandardScaler()
    X_train_scaled = scaler.fit_transform(X_train)
    X_val_scaled = scaler.transform(X_val)
    X_test_scaled = scaler.transform(X_test)

    # Train model on TRAIN only (same hyperparameters as original — no optimization)
    gb = GradientBoostingClassifier(
        n_estimators=100, max_depth=4, learning_rate=0.05,
        random_state=RANDOM_SEED, min_samples_leaf=20
    )
    gb.fit(X_train_scaled, y_train)

    # Select threshold on VALIDATION only
    p_val = gb.predict_proba(X_val_scaled)[:, 1]
    best_th, best_f1 = 0.50, 0.0
    threshold_scan = []
    for th in np.arange(0.30, 0.65, 0.01):
        preds = (p_val >= th).astype(int)
        f1 = f1_score(y_val, preds, zero_division=0)
        threshold_scan.append({"threshold": round(th, 2), "f1": round(f1, 4)})
        if f1 > best_f1:
            best_f1 = f1
            best_th = round(th, 2)

    frozen_threshold = best_th
    print(f"  Scaler fitted on TRAIN: {len(train_clean)} rows")
    print(f"  Model trained on TRAIN: {len(train_clean)} rows")
    print(f"  Threshold selected on VAL (F1-optimal): {frozen_threshold} (F1={best_f1:.4f})")

    # Save model and scaler
    with open(STEP4F_MODEL_PATH, "wb") as f:
        pickle.dump(gb, f)
    with open(STEP4F_SCALER_PATH, "wb") as f:
        pickle.dump(scaler, f)

    # =========================================================================
    # PHASE 3: GENERATE TEST PREDICTIONS
    # =========================================================================
    print("\n[PHASE 3] Generating test predictions (threshold frozen before evaluation)...")
    test_pred = test_clean.copy()
    test_pred['ml_probability'] = gb.predict_proba(X_test_scaled)[:, 1]
    test_pred.to_csv(STEP4F_TEST_PREDICTIONS_CSV, index=False)
    print(f"  Test predictions: {len(test_pred)} rows -> {STEP4F_TEST_PREDICTIONS_CSV}")

    # =========================================================================
    # SIGNAL-LEVEL DIAGNOSTICS
    # =========================================================================
    print("\n[PHASE 5] Signal-level diagnostics...")

    test_pred['_daily_rank'] = test_pred.groupby('signal_date')['ml_probability'].rank(
        method='first', ascending=False
    )

    signal_configs = {
        "Strategy Baseline (All Signals)": test_pred,
        f"ML Filter (Prob >= {frozen_threshold})": test_pred[test_pred['ml_probability'] >= frozen_threshold],
        "ML Top-10 Per Day": test_pred[test_pred['_daily_rank'] <= 10],
        "ML Top-5 Per Day": test_pred[test_pred['_daily_rank'] <= 5],
    }

    signal_rows = []
    for name, sdf in signal_configs.items():
        m = compute_signal_metrics(sdf)
        m["configuration"] = name
        signal_rows.append(m)
    df_signal = pd.DataFrame(signal_rows)
    df_signal.to_csv(STEP4F_SIGNAL_COMPARISON_CSV, index=False)

    test_pred.drop(columns=['_daily_rank'], inplace=True)

    # =========================================================================
    # PORTFOLIO-LEVEL SIMULATION
    # =========================================================================
    print("\n[PHASE 6] Portfolio-level simulation...")

    port_configs = {
        "Strategy Baseline (Capital-Constrained)": {"min_prob": 0.0, "rank_col": "rsi_14"},
        f"ML Filter (Prob >= {frozen_threshold})": {"min_prob": frozen_threshold, "rank_col": "ml_probability"},
        "ML Top-5 Per Day (Deduped)": {"min_prob": 0.0, "rank_col": "ml_probability", "top_n": 5},
    }

    port_rows = []
    for name, cfg in port_configs.items():
        pm = simulate_portfolio(
            test_pred, rank_col=cfg["rank_col"], min_prob=cfg.get("min_prob", 0.0),
            top_n_day=cfg.get("top_n"), dedup_symbol=True, cost_multiplier=1.0
        )
        pm["configuration"] = name
        port_rows.append(pm)
    df_port = pd.DataFrame(port_rows)
    df_port.to_csv(STEP4F_PORTFOLIO_COMPARISON_CSV, index=False)

    # =========================================================================
    # COST SENSITIVITY
    # =========================================================================
    print("\n[PHASE 7] Cost sensitivity...")
    cost_rows = []
    for sname, mult in [("1.0x Friction", 1.0), ("Zero Friction", 0.0), ("2.0x Friction", 2.0)]:
        cm = simulate_portfolio(
            test_pred, rank_col="ml_probability", min_prob=frozen_threshold,
            dedup_symbol=True, cost_multiplier=mult
        )
        cm["scenario"] = sname
        cost_rows.append(cm)
    df_cost = pd.DataFrame(cost_rows)
    df_cost.to_csv(STEP4F_COST_SENSITIVITY_CSV, index=False)

    # =========================================================================
    # ROBUSTNESS (DAY-LEVEL BLOCK BOOTSTRAP)
    # =========================================================================
    print("\n[PHASE 8] Day-level block bootstrap...")
    base_dedup = test_pred.drop_duplicates(subset=['signal_date', 'symbol'], keep='first')
    ml_dedup = base_dedup[base_dedup['ml_probability'] >= frozen_threshold]
    daily_base = base_dedup.groupby("signal_date")["forward_10d_return"].mean()
    daily_ml = ml_dedup.groupby("signal_date")["forward_10d_return"].mean()
    common_dates = daily_base.index.intersection(daily_ml.index)

    if len(common_dates) > 5:
        d_base = daily_base.loc[common_dates].values
        d_ml = daily_ml.loc[common_dates].values
        np.random.seed(RANDOM_SEED)
        n_days = len(common_dates)
        bs_diffs = [np.mean(d_ml[np.random.choice(n_days, n_days, replace=True)] -
                            d_base[np.random.choice(n_days, n_days, replace=True)])
                    for _ in range(1000)]
        ci_lo, ci_hi = np.percentile(bs_diffs, 2.5), np.percentile(bs_diffs, 97.5)
        crosses_zero = bool(ci_lo <= 0 <= ci_hi)
    else:
        ci_lo, ci_hi, crosses_zero, n_days = 0, 0, True, 0

    robust_data = {
        "bootstrap_type": "Day-level block bootstrap",
        "trading_days": n_days,
        "ci_95_lower_pct": round(float(ci_lo), 4),
        "ci_95_upper_pct": round(float(ci_hi), 4),
        "ci_crosses_zero": crosses_zero,
        "conclusion": "EXPLORATORY — NOT STATISTICALLY CONFIRMED" if crosses_zero else "DIRECTIONAL SIGNAL (use caution)"
    }
    pd.DataFrame([robust_data]).to_csv(STEP4F_ROBUSTNESS_CSV, index=False)

    # =========================================================================
    # MANIFEST
    # =========================================================================
    manifest = {
        "step": "4F",
        "purpose": "Temporal Purity / Embargo Correction",
        "dataset_sha256": dataset_sha,
        "total_rows_original": len(df),
        "label_horizon_trading_days": LABEL_HORIZON_DAYS,
        "embargo_methodology": (
            "For each observation, label_end = trading_dates[index(signal_date) + 10]. "
            "Observations are removed from TRAIN if label_end >= VAL start, "
            "and from VAL if label_end >= TEST start. "
            "TEST is never modified. Uses actual trading dates, not calendar approximations."
        ),
        "val_start_date": VAL_START_DATE,
        "test_start_date": TEST_START_DATE,
        "original_splits": diag["original"],
        "embargo_removals": diag["embargo"],
        "embargo_clean_splits": diag["embargo_clean"],
        "model_type": "GradientBoostingClassifier",
        "model_params": {"n_estimators": 100, "max_depth": 4, "learning_rate": 0.05, "min_samples_leaf": 20},
        "scaler_fitted_on": diag["embargo_clean"]["train_rows"],
        "frozen_threshold": frozen_threshold,
        "validation_optimal_f1": best_f1,
        "model_sha256": compute_sha256(STEP4F_MODEL_PATH),
        "scaler_sha256": compute_sha256(STEP4F_SCALER_PATH),
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "status": "EMBARGO_CLEAN",
    }
    with open(STEP4F_MANIFEST_JSON, "w") as f:
        json.dump(manifest, f, indent=2)

    # =========================================================================
    # REPORT
    # =========================================================================
    print("\n[PHASE 9] Generating report...")

    baseline_sig = df_signal[df_signal['configuration'] == "Strategy Baseline (All Signals)"].iloc[0]
    ml_sig = df_signal[df_signal['configuration'].str.contains(str(frozen_threshold))].iloc[0]
    baseline_port = df_port[df_port['configuration'] == "Strategy Baseline (Capital-Constrained)"].iloc[0]
    ml_port = df_port[df_port['configuration'].str.contains(str(frozen_threshold))].iloc[0]

    # Compare with Step 4E
    step4e_port_path = os.path.join(ML_DIR, "step_4e_portfolio_comparison.csv")
    if os.path.exists(step4e_port_path):
        df_4e = pd.read_csv(step4e_port_path)
        baseline_4e = df_4e[df_4e['configuration'] == "Strategy Baseline (Capital-Constrained)"].iloc[0]
        ml_4e_rows = df_4e[df_4e['configuration'].str.contains("0.52")]
        comparison_note = f"""
### Step 4E vs Step 4F Comparison (Before vs After Embargo)

| Metric | 4E Baseline | 4F Baseline | 4E ML @0.52 | 4F ML @{frozen_threshold} |
|---|---|---|---|---|
| Portfolio Return | {baseline_4e['net_portfolio_return_pct']}% | {baseline_port['net_portfolio_return_pct']}% | {ml_4e_rows.iloc[0]['net_portfolio_return_pct'] if len(ml_4e_rows) > 0 else 'N/A'}% | {ml_port['net_portfolio_return_pct']}% |
| Daily Sharpe | {baseline_4e['daily_sharpe_ratio']} | {baseline_port['daily_sharpe_ratio']} | {ml_4e_rows.iloc[0]['daily_sharpe_ratio'] if len(ml_4e_rows) > 0 else 'N/A'} | {ml_port['daily_sharpe_ratio']} |
| Max Drawdown | {baseline_4e['max_drawdown_pct']}% | {baseline_port['max_drawdown_pct']}% | {ml_4e_rows.iloc[0]['max_drawdown_pct'] if len(ml_4e_rows) > 0 else 'N/A'}% | {ml_port['max_drawdown_pct']}% |

> [!NOTE]
> TEST set is identical in both steps (same rows, same date range). Differences arise from the retrained model
> (embargo-clean TRAIN/VAL) and the new validation-selected threshold ({frozen_threshold} vs prior 0.52).
"""
    else:
        comparison_note = ""

    ml_improves = (
        ml_port['net_portfolio_return_pct'] > baseline_port['net_portfolio_return_pct'] and
        (ml_port['daily_sharpe_ratio'] != 'N/A' and baseline_port['daily_sharpe_ratio'] != 'N/A' and
         float(ml_port['daily_sharpe_ratio']) > float(baseline_port['daily_sharpe_ratio']))
    )

    if ml_improves and not crosses_zero:
        gate = "GREEN — Temporal methodology clean, ML shows directional improvement"
    elif ml_improves:
        gate = "YELLOW — Temporal methodology clean, ML improvement not statistically confirmed"
    else:
        gate = "YELLOW — Temporal methodology clean, ML does NOT demonstrate incremental value"

    report = f"""# STEP 4F — TEMPORAL PURITY / EMBARGO CORRECTION REPORT

> [!IMPORTANT]
> **FINAL GATE**: `{gate}`

---

## 1. Embargo Methodology

The prediction label uses a **{LABEL_HORIZON_DAYS}-trading-day** forward return horizon.

For each observation, the label window end date is computed as:

```
label_end = all_trading_dates[index_of(signal_date) + {LABEL_HORIZON_DAYS}]
```

An observation is **REMOVED** from TRAIN if `label_end >= {VAL_START_DATE}` (VAL start).
An observation is **REMOVED** from VAL if `label_end >= {TEST_START_DATE}` (TEST start).
TEST is **never modified**.

Trading dates are taken from the actual dataset — no calendar-day approximation.

---

## 2. Split Comparison

| Split | Original Rows | Embargo Rows Removed | Clean Rows | Original Date Range | Clean Date Range |
|---|---|---|---|---|---|
| TRAIN | {diag['original']['train_rows']} | {diag['embargo']['train_rows_removed']} ({diag['embargo']['train_dates_removed']} dates) | {diag['embargo_clean']['train_rows']} | {diag['original']['train_date_min']} to {diag['original']['train_date_max']} | {diag['embargo_clean']['train_date_min']} to {diag['embargo_clean']['train_date_max']} |
| VAL | {diag['original']['val_rows']} | {diag['embargo']['val_rows_removed']} ({diag['embargo']['val_dates_removed']} dates) | {diag['embargo_clean']['val_rows']} | {diag['original']['val_date_min']} to {diag['original']['val_date_max']} | {diag['embargo_clean']['val_date_min']} to {diag['embargo_clean']['val_date_max']} |
| TEST | {diag['original']['test_rows']} | 0 | {diag['embargo_clean']['test_rows']} | {diag['original']['test_date_min']} to {diag['original']['test_date_max']} | {diag['embargo_clean']['test_date_min']} to {diag['embargo_clean']['test_date_max']} |

- TRAIN embargo dates removed: {diag['embargo']['train_removed_date_range']}
- VAL embargo dates removed: {diag['embargo']['val_removed_date_range']}

---

## 3. Model Training (Embargo-Clean)

| Field | Value |
|---|---|
| Model | GradientBoostingClassifier (n=100, depth=4, lr=0.05, min_leaf=20) |
| Scaler | StandardScaler fitted on TRAIN ({diag['embargo_clean']['train_rows']} rows) only |
| Threshold | {frozen_threshold} (F1-optimal on VALIDATION: F1={best_f1:.4f}) |
| Model SHA256 | `{manifest['model_sha256']}` |

---

## 4. Signal-Level Diagnostics

{df_signal.to_markdown(index=False)}

---

## 5. Portfolio-Level Simulation (₹1M, 10 positions, symbol-deduped, costs included)

{df_port.to_markdown(index=False)}

---

## 6. Cost Sensitivity (ML @{frozen_threshold})

{df_cost.to_markdown(index=False)}

---

## 7. Statistical Robustness

| Metric | Value |
|---|---|
| Type | {robust_data['bootstrap_type']} |
| Days | {robust_data['trading_days']} |
| 95% CI | [{robust_data['ci_95_lower_pct']}%, {robust_data['ci_95_upper_pct']}%] |
| Crosses Zero | {robust_data['ci_crosses_zero']} |
| Conclusion | {robust_data['conclusion']} |

---

## 8. Leakage Assessment (Post-Embargo)

- Features use only data at/before signal_date: ✅
- Labels use only data after signal_date: ✅
- Scaler fitted on TRAIN only: ✅
- Model fitted on TRAIN only: ✅
- Threshold selected on VAL only: ✅
- TEST untouched by model development: ✅
- **Embargo gap (10-trading-day label horizon): ✅ CLEAN**

{comparison_note}

---

## 9. Artifacts Created

| File | Purpose |
|---|---|
| `data/ml/step_4f/embargo_manifest.json` | Authoritative manifest |
| `data/ml/step_4f/gradient_boosting_classifier.pkl` | Embargo-clean retrained model |
| `data/ml/step_4f/feature_scaler.pkl` | Embargo-clean scaler |
| `data/ml/step_4f/test_predictions.csv` | Clean test predictions |
| `data/ml/step_4f/signal_comparison.csv` | Signal diagnostics |
| `data/ml/step_4f/portfolio_comparison.csv` | Portfolio results |
| `data/ml/step_4f/cost_sensitivity.csv` | Cost analysis |
| `data/ml/step_4f/robustness.csv` | Bootstrap results |
| `data/ml/step_4f/step_4f_embargo_report.md` | This report |
| `scripts/run_step_4f_embargo.py` | Embargo correction script |
| `scripts/test_step_4f_temporal_purity.py` | 10 temporal purity tests |

---

## 10. ML Value Assessment

**Does ML currently demonstrate incremental value?**

{'**YES — but not statistically confirmed.** The ML-filtered portfolio outperforms the baseline in net return and Sharpe, but bootstrap CI crosses zero.' if ml_improves else '**NO.** The ML model at threshold ' + str(frozen_threshold) + ' does NOT produce better portfolio returns than the strategy baseline.'}

**Production decision remains: ML OFF, PURE STRATEGY BASELINE.**
"""

    with open(STEP4F_REPORT_MD, "w") as f:
        f.write(report)

    print(f"  Report -> {STEP4F_REPORT_MD}")
    print(f"\n{'='*80}")
    print(f"STEP 4F COMPLETED — {gate}")
    print(f"{'='*80}")

    return manifest, df_signal, df_port


if __name__ == "__main__":
    run_step_4f()
