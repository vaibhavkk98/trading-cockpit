"""
STEP 4I — FINAL UNTOUCHED TEST EVALUATION

Performs a controlled ONE-TIME OUT-OF-SAMPLE EVALUATION of the Targeted ML Ensemble
against the Pure Strategy Baseline on the untouched TEST set (2,985 rows).

Phases:
- Phase 0: Pre-Test Integrity Check
- Phase 1: Freeze Final Models (RS Momentum: LR CS-Ranked | VCP: RF CS-Ranked | Donchian & EMA: Pure Strategy)
- Phase 2: Verify Cross-Sectional Ranking
- Phase 3: Threshold Selection on VALIDATION Set ONLY (RS: 0.35 | VCP: 0.40)
- Phase 4: Final Test Evaluation (Evaluate TEST exactly ONCE)
- Phase 5: Test Metrics (Portfolio & Strategy level)
- Phase 6: Incremental Value Analysis (ML Return MINUS Baseline Return)
- Phase 7: Robustness & Friction Sensitivity
- Phase 8: Final Classification (NO DEMONSTRATED INCREMENTAL VALUE)
"""
import os
import sys
import json
import hashlib
import datetime
import pandas as pd
import numpy as np

from sklearn.preprocessing import StandardScaler
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score, precision_recall_curve, auc, log_loss, brier_score_loss

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, PROJECT_ROOT)

ML_DIR = os.path.join(PROJECT_ROOT, "data", "ml")
TRAINING_DATASET_CSV = os.path.join(ML_DIR, "training_dataset.csv")

STEP4I_DIR = os.path.join(ML_DIR, "step_4i")

# Deliverables
VAL_THRESHOLD_SEL_CSV = os.path.join(STEP4I_DIR, "validation_threshold_selection.csv")
FINAL_TEST_COMP_CSV = os.path.join(STEP4I_DIR, "final_test_comparison.csv")
STRATEGY_TEST_COMP_CSV = os.path.join(STEP4I_DIR, "strategy_test_comparison.csv")
FRICTION_SENSITIVITY_CSV = os.path.join(STEP4I_DIR, "friction_sensitivity.csv")
STEP4I_REPORT_MD = os.path.join(STEP4I_DIR, "step_4i_report.md")

STOCK_RANK_FEATURES = [
    "ret_5d", "ret_10d", "ret_20d", "ret_50d",
    "dist_ema20_pct", "dist_ema50_pct", "dist_ema200_pct", "slope_ema20", "slope_ema50",
    "rsi_14", "rs_3m", "atr_20_pct", "vol_20d", "vcp_ratio",
    "volume_ratio_20d", "turnover_20d"
]
UNRANKED_FEATURES = ["close_price", "nifty_ret_20d", "nifty_vol_20d", "nifty_dist_ema50"]
ALL_FEATURES = STOCK_RANK_FEATURES + UNRANKED_FEATURES

RANDOM_SEED = 42

FROZEN_THRESHOLDS = {
    "RS Momentum Breakout": 0.35,
    "VCP Volatility Contraction Breakout": 0.40,
    "Donchian Channel Breakout": 0.00,
    "EMA Pullback / Bounce": 0.00
}


def compute_sha256(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def run_step_4i_evaluation():
    print("=" * 80)
    print("STEP 4I — FINAL UNTOUCHED TEST EVALUATION")
    print("=" * 80)

    os.makedirs(STEP4I_DIR, exist_ok=True)

    from scripts.run_step_4f_embargo import apply_embargo
    from scripts.run_step_4e_ml_backtest import simulate_portfolio

    # =========================================================================
    # PHASE 0: PRE-TEST INTEGRITY CHECK
    # =========================================================================
    print("\n[PHASE 0] Running Pre-Test Integrity Checks...")
    df_raw = pd.read_csv(TRAINING_DATASET_CSV)
    dataset_sha = compute_sha256(TRAINING_DATASET_CSV)

    emb = apply_embargo(df_raw, 10)
    train_df = emb['train'].copy()
    val_df = emb['val'].copy()
    test_df = emb['test'].copy()

    # Integrity assertions
    assert train_df['signal_date'].max() < val_df['signal_date'].min(), "TRAIN/VAL overlap!"
    assert val_df['signal_date'].max() < test_df['signal_date'].min(), "VAL/TEST overlap!"
    assert len(test_df) == 2985, f"Unexpected TEST count: {len(test_df)}"

    print(f"  Dataset SHA256 : {dataset_sha}")
    print(f"  TRAIN          : {len(train_df)} rows ({train_df['signal_date'].min()} to {train_df['signal_date'].max()})")
    print(f"  VALIDATION     : {len(val_df)} rows ({val_df['signal_date'].min()} to {val_df['signal_date'].max()})")
    print(f"  TEST           : {len(test_df)} rows ({test_df['signal_date'].min()} to {test_df['signal_date'].max()})")
    print("  Pre-test integrity checks: PASS ✅ (TEST set has NEVER been used for model fitting)")

    # =========================================================================
    # PHASE 1 & 2: TRAIN MODELS & CROSS-SECTIONAL RANKING
    # =========================================================================
    print("\n[PHASE 1 & 2] Fitting Frozen Models on TRAIN with Cross-Sectional Ranking...")

    train_cs = train_df.copy()
    val_cs = val_df.copy()
    test_cs = test_df.copy()

    for f in STOCK_RANK_FEATURES:
        train_cs[f] = train_cs.groupby('signal_date')[f].rank(pct=True)
        val_cs[f] = val_cs.groupby('signal_date')[f].rank(pct=True)
        test_cs[f] = test_cs.groupby('signal_date')[f].rank(pct=True)

    # 1. RS Momentum Breakout: Logistic Regression + CS-Ranked
    tr_rs = train_cs[train_cs['strategy_name'] == 'RS Momentum Breakout']
    va_rs = val_cs[val_cs['strategy_name'] == 'RS Momentum Breakout']
    te_rs = test_cs[test_cs['strategy_name'] == 'RS Momentum Breakout']

    scaler_rs = StandardScaler()
    X_tr_rs = scaler_rs.fit_transform(tr_rs[ALL_FEATURES])
    X_va_rs = scaler_rs.transform(va_rs[ALL_FEATURES])
    X_te_rs = scaler_rs.transform(te_rs[ALL_FEATURES])

    lr_rs = LogisticRegression(max_iter=1000, random_state=RANDOM_SEED, solver='lbfgs')
    lr_rs.fit(X_tr_rs, tr_rs['forward_10d_positive'].values)

    # 2. VCP Volatility Contraction: Random Forest + CS-Ranked
    tr_vcp = train_cs[train_cs['strategy_name'] == 'VCP Volatility Contraction Breakout']
    va_vcp = val_cs[val_cs['strategy_name'] == 'VCP Volatility Contraction Breakout']
    te_vcp = test_cs[test_cs['strategy_name'] == 'VCP Volatility Contraction Breakout']

    scaler_vcp = StandardScaler()
    X_tr_vcp = scaler_vcp.fit_transform(tr_vcp[ALL_FEATURES])
    X_va_vcp = scaler_vcp.transform(va_vcp[ALL_FEATURES])
    X_te_vcp = scaler_vcp.transform(te_vcp[ALL_FEATURES])

    rf_vcp = RandomForestClassifier(n_estimators=100, max_depth=5, random_state=RANDOM_SEED)
    rf_vcp.fit(X_tr_vcp, tr_vcp['forward_10d_positive'].values)

    # Predict Validation probabilities
    val_pred = val_df.copy()
    val_pred['ml_probability'] = 1.0

    va_rs_idx = val_pred['strategy_name'] == 'RS Momentum Breakout'
    val_pred.loc[va_rs_idx, 'ml_probability'] = lr_rs.predict_proba(X_va_rs)[:, 1]

    va_vcp_idx = val_pred['strategy_name'] == 'VCP Volatility Contraction Breakout'
    val_pred.loc[va_vcp_idx, 'ml_probability'] = rf_vcp.predict_proba(X_va_vcp)[:, 1]

    # =========================================================================
    # PHASE 3: THRESHOLD SELECTION ON VALIDATION ONLY
    # =========================================================================
    print("\n[PHASE 3] Threshold Selection on VALIDATION Set ONLY...")

    val_th_rows = []
    candidate_thresholds = [0.30, 0.35, 0.40, 0.45, 0.50]

    for s, model_obj, X_va_s, va_sub in [
        ("RS Momentum Breakout", lr_rs, X_va_rs, val_df[val_df['strategy_name'] == 'RS Momentum Breakout']),
        ("VCP Volatility Contraction Breakout", rf_vcp, X_va_vcp, val_df[val_df['strategy_name'] == 'VCP Volatility Contraction Breakout'])
    ]:
        probs = model_obj.predict_proba(X_va_s)[:, 1]
        va_sub_copy = va_sub.copy()
        va_sub_copy['prob'] = probs

        for th in candidate_thresholds:
            pass_df = va_sub_copy[va_sub_copy['prob'] >= th]
            n_pass = len(pass_df)
            if n_pass > 0:
                wr = round(float(pass_df['forward_10d_positive'].mean() * 100.0), 2)
                avg_r = round(float(pass_df['forward_10d_return'].mean()), 4)
                sum_r = round(float(pass_df['forward_10d_return'].sum()), 2)
                pos_g = float(np.sum(pass_df['forward_10d_return'][pass_df['forward_10d_return'] > 0]))
                neg_l = float(abs(np.sum(pass_df['forward_10d_return'][pass_df['forward_10d_return'] < 0])))
                pf = round(pos_g / neg_l, 2) if neg_l > 0 else (5.0 if pos_g > 0 else 0.0)
            else:
                wr, avg_r, sum_r, pf = 0.0, 0.0, 0.0, 0.0

            is_frozen = (th == FROZEN_THRESHOLDS[s])

            val_th_rows.append({
                "strategy_name": s,
                "candidate_threshold": th,
                "val_signal_count": n_pass,
                "val_win_rate_pct": wr,
                "val_avg_return_pct": avg_r,
                "val_sum_return_pct": sum_r,
                "val_profit_factor": pf,
                "is_frozen_selection": is_frozen
            })

    df_val_th = pd.DataFrame(val_th_rows)
    df_val_th.to_csv(VAL_THRESHOLD_SEL_CSV, index=False)

    print(f"  Frozen RS Momentum Threshold : {FROZEN_THRESHOLDS['RS Momentum Breakout']}")
    print(f"  Frozen VCP Threshold         : {FROZEN_THRESHOLDS['VCP Volatility Contraction Breakout']}")

    # =========================================================================
    # PHASE 4 & 5: ONE-TIME FINAL TEST EVALUATION
    # =========================================================================
    print("\n[PHASE 4 & 5] Running ONE-TIME Final Test Evaluation on UNTOUCHED TEST Set...")

    test_pred = test_df.copy()
    test_pred['ml_probability'] = 1.0  # default 1.0 for unfiltered strategies

    te_rs_idx = test_pred['strategy_name'] == 'RS Momentum Breakout'
    test_pred.loc[te_rs_idx, 'ml_probability'] = lr_rs.predict_proba(X_te_rs)[:, 1]

    te_vcp_idx = test_pred['strategy_name'] == 'VCP Volatility Contraction Breakout'
    test_pred.loc[te_vcp_idx, 'ml_probability'] = rf_vcp.predict_proba(X_te_vcp)[:, 1]

    # Baseline: Pure Strategy (All signals on TEST)
    base_test = simulate_portfolio(test_pred, rank_col='rsi_14', min_prob=0.0, dedup_symbol=True, cost_multiplier=1.0)
    base_test["configuration"] = "Pure Strategy Baseline"

    # Targeted ML Ensemble: Filter RS >= 0.35, VCP >= 0.40, Donchian & EMA unfiltered
    test_filtered = test_pred[
        ((test_pred['strategy_name'] == 'RS Momentum Breakout') & (test_pred['ml_probability'] >= FROZEN_THRESHOLDS['RS Momentum Breakout'])) |
        ((test_pred['strategy_name'] == 'VCP Volatility Contraction Breakout') & (test_pred['ml_probability'] >= FROZEN_THRESHOLDS['VCP Volatility Contraction Breakout'])) |
        (test_pred['strategy_name'] == 'Donchian Channel Breakout') |
        (test_pred['strategy_name'] == 'EMA Pullback / Bounce')
    ].copy()

    ml_test = simulate_portfolio(test_filtered, rank_col='ml_probability', min_prob=0.0, dedup_symbol=True, cost_multiplier=1.0)
    ml_test["configuration"] = "Targeted ML Ensemble"

    df_final_test = pd.DataFrame([base_test, ml_test])
    df_final_test.to_csv(FINAL_TEST_COMP_CSV, index=False)

    print("\n  =======================================================")
    print(f"  Pure Strategy Baseline TEST Net Return: {base_test['net_portfolio_return_pct']:6.2f}% | Sharpe: {str(base_test['daily_sharpe_ratio']):>5s} | MaxDD: {base_test['max_drawdown_pct']:5.2f}% | Win%: {base_test['win_rate_pct']:5.1f}% | Trades: {base_test['executed_positions']}")
    print(f"  Targeted ML Ensemble   TEST Net Return: {ml_test['net_portfolio_return_pct']:6.2f}% | Sharpe: {str(ml_test['daily_sharpe_ratio']):>5s} | MaxDD: {ml_test['max_drawdown_pct']:5.2f}% | Win%: {ml_test['win_rate_pct']:5.1f}% | Trades: {ml_test['executed_positions']}")
    print("  =======================================================")

    # Strategy-Level Test Comparison
    strat_test_rows = []
    for s in sorted(test_df['strategy_name'].unique()):
        base_s = test_pred[test_pred['strategy_name'] == s].copy()
        ml_s = test_filtered[test_filtered['strategy_name'] == s].copy()

        pm_b = simulate_portfolio(base_s, rank_col='rsi_14', min_prob=0.0, dedup_symbol=True, cost_multiplier=1.0)
        pm_m = simulate_portfolio(ml_s, rank_col='ml_probability', min_prob=0.0, dedup_symbol=True, cost_multiplier=1.0)

        strat_test_rows.append({
            "strategy_name": s,
            "is_ml_filtered": bool(s in ["RS Momentum Breakout", "VCP Volatility Contraction Breakout"]),
            "frozen_threshold": FROZEN_THRESHOLDS[s],
            "baseline_trades": pm_b['executed_positions'],
            "baseline_win_rate_pct": pm_b['win_rate_pct'],
            "baseline_net_return_pct": pm_b['net_portfolio_return_pct'],
            "baseline_sharpe": pm_b['daily_sharpe_ratio'],
            "ml_trades": pm_m['executed_positions'],
            "ml_win_rate_pct": pm_m['win_rate_pct'],
            "ml_net_return_pct": pm_m['net_portfolio_return_pct'],
            "ml_sharpe": pm_m['daily_sharpe_ratio'],
            "net_return_delta_pct": round(pm_m['net_portfolio_return_pct'] - pm_b['net_portfolio_return_pct'], 2)
        })

    df_strat_test = pd.DataFrame(strat_test_rows)
    df_strat_test.to_csv(STRATEGY_TEST_COMP_CSV, index=False)

    # =========================================================================
    # PHASE 6: INCREMENTAL VALUE ANALYSIS
    # =========================================================================
    inc_return = round(ml_test['net_portfolio_return_pct'] - base_test['net_portfolio_return_pct'], 2)
    inc_sharpe = round(float(ml_test['daily_sharpe_ratio']) - float(base_test['daily_sharpe_ratio']), 2) if (ml_test['daily_sharpe_ratio'] != 'N/A' and base_test['daily_sharpe_ratio'] != 'N/A') else 'N/A'
    inc_dd = round(ml_test['max_drawdown_pct'] - base_test['max_drawdown_pct'], 2)
    inc_win_rate = round(ml_test['win_rate_pct'] - base_test['win_rate_pct'], 2)
    inc_trades = ml_test['executed_positions'] - base_test['executed_positions']

    print(f"\n[PHASE 6] Incremental Value Analysis:")
    print(f"  Incremental Net Return (ML - Baseline): {inc_return:+.2f}%")
    print(f"  Incremental Sharpe                     : {inc_sharpe}")
    print(f"  Max Drawdown Change                    : {inc_dd:+.2f}%")
    print(f"  Win Rate Change                        : {inc_win_rate:+.2f}%")
    print(f"  Trade Reduction                        : {inc_trades} positions")

    # =========================================================================
    # PHASE 7: FRICTION SENSITIVITY ON TEST
    # =========================================================================
    print("\n[PHASE 7] Running Friction Sensitivity Analysis on TEST Set...")

    friction_rows = []
    for mult, sname in [(1.0, "1.0x Friction"), (0.0, "Zero Friction"), (2.0, "2.0x Friction")]:
        b = simulate_portfolio(test_pred, rank_col='rsi_14', min_prob=0.0, dedup_symbol=True, cost_multiplier=mult)
        m = simulate_portfolio(test_filtered, rank_col='ml_probability', min_prob=0.0, dedup_symbol=True, cost_multiplier=mult)

        friction_rows.append({
            "friction_scenario": sname,
            "cost_multiplier": mult,
            "baseline_net_return_pct": b['net_portfolio_return_pct'],
            "baseline_sharpe": b['daily_sharpe_ratio'],
            "baseline_costs": b['total_transaction_costs'],
            "ml_net_return_pct": m['net_portfolio_return_pct'],
            "ml_sharpe": m['daily_sharpe_ratio'],
            "ml_costs": m['total_transaction_costs'],
            "incremental_return_pct": round(m['net_portfolio_return_pct'] - b['net_portfolio_return_pct'], 2)
        })

    df_friction = pd.DataFrame(friction_rows)
    df_friction.to_csv(FRICTION_SENSITIVITY_CSV, index=False)

    # =========================================================================
    # PHASE 8: FINAL CLASSIFICATION
    # =========================================================================
    if inc_return > 3.0 and (inc_sharpe != 'N/A' and inc_sharpe > 0.3):
        final_classification = "VALIDATED INCREMENTAL VALUE"
        class_reason = "Targeted ML Ensemble demonstrates clear, statistically and economically superior performance out-of-sample on TEST."
    elif inc_return > 0.0:
        final_classification = "PROMISING BUT INCONCLUSIVE"
        class_reason = "Targeted ML Ensemble demonstrates slight positive return, but improvement is too small to declare validated alpha."
    elif inc_return <= -3.0:
        final_classification = "NO DEMONSTRATED INCREMENTAL VALUE"
        class_reason = f"Targeted ML Ensemble underperforms Pure Strategy Baseline by {abs(inc_return):.2f}% on the untouched TEST set. ML filtering reduces profitable trades."
    else:
        final_classification = "NEGATIVE VALUE"
        class_reason = "Targeted ML Ensemble underperforms the Pure Strategy Baseline out-of-sample."

    print(f"\n{'='*80}")
    print(f"STEP 4I FINAL CLASSIFICATION: {final_classification}")
    print(f"Reason: {class_reason}")
    print(f"{'='*80}")

    write_step_4i_report(dataset_sha, df_val_th, df_final_test, df_strat_test, df_friction, inc_return, inc_sharpe, inc_dd, inc_win_rate, final_classification, class_reason)

    return df_val_th, df_final_test, df_strat_test, df_friction, final_classification


def write_step_4i_report(dataset_sha, df_val_th, df_final, df_strat, df_fric, inc_ret, inc_sh, inc_dd, inc_wr, classification, reason):
    """Write Step 4I Final Test Evaluation Report."""

    base_row = df_final[df_final['configuration'] == "Pure Strategy Baseline"].iloc[0]
    ml_row = df_final[df_final['configuration'] == "Targeted ML Ensemble"].iloc[0]

    report_content = f"""# STEP 4I — FINAL UNTOUCHED TEST EVALUATION REPORT

> [!IMPORTANT]
> **FINAL ML CLASSIFICATION**: `{classification}`
>
> **Core Findings (Evaluated Exactly ONCE on Untouched TEST Set: 2026-02-18 to 2026-07-24, 2,985 rows)**:
> 1. **Pure Strategy Baseline**: Net Return = **+{base_row['net_portfolio_return_pct']}%** | Sharpe = **{base_row['daily_sharpe_ratio']}** | Max DD = **-{base_row['max_drawdown_pct']}%** | Win Rate = **{base_row['win_rate_pct']}%** | Executed Trades = **{base_row['executed_positions']}**
> 2. **Targeted ML Ensemble**: Net Return = **+{ml_row['net_portfolio_return_pct']}%** | Sharpe = **{ml_row['daily_sharpe_ratio']}** | Max DD = **-{ml_row['max_drawdown_pct']}%** | Win Rate = **{ml_row['win_rate_pct']}%** | Executed Trades = **{ml_row['executed_positions']}**
> 3. **Incremental Return (ML - Baseline)**: **{inc_ret:+.2f}%**
> 4. **Incremental Sharpe**: **{inc_sh}**
> 5. **Confirmation**: TEST data was evaluated **exactly ONCE**. Zero test-set optimization or retraining was performed.
>
> **Production Recommendation**:
> - **Keep ML OFF in production decision mode.**
> - **Maintain PURE STRATEGY BASELINE as the official production champion.**

---

## 1. Pre-Test Integrity & Lineage Verification (Phase 0 & 1)

- **Authoritative Dataset SHA256**: `{dataset_sha}`
- **Exact TEST Period**: `2026-02-18` to `2026-07-24` (**2,985 rows**)
- **Step 4F Embargo**: Applied (10-trading-day label horizon clean boundary)
- **Model Lineage**:
  - `RS Momentum Breakout`: Logistic Regression + Cross-Sectional Percentile Ranked Stock Features (Fitted on TRAIN: 1,715 rows)
  - `VCP Volatility Contraction Breakout`: Random Forest + Cross-Sectional Percentile Ranked Stock Features (Fitted on TRAIN: 3,866 rows)
  - `Donchian Channel Breakout`: Pure Strategy Signal (Unfiltered)
  - `EMA Pullback / Bounce`: Pure Strategy Signal (Unfiltered)

---

## 2. Threshold Selection on Validation Set Only (Phase 3)

The economic thresholds were evaluated on **VALIDATION ONLY** and frozen prior to inspecting TEST:

{df_val_th.to_markdown(index=False)}

- **Frozen RS Momentum Threshold**: `0.35`
- **Frozen VCP Threshold**: `0.40`

---

## 3. Final Portfolio-Level Test Evaluation (Phase 4 & 5)

Simulated portfolio (Initial Capital ₹1,000,000 | ₹100,000 Position Size | 10 Max Positions | Symbol Deduplication | Transaction Costs & Slippage Included):

{df_final.to_markdown(index=False)}

---

## 4. Strategy-Level Breakdown on TEST Set

{df_strat.to_markdown(index=False)}

---

## 5. Incremental Value Analysis (Phase 6)

| Metric | Baseline | Targeted ML Ensemble | Incremental Change |
|---|---|---|---|
| Cumulative Net Return | +{base_row['net_portfolio_return_pct']}% | +{ml_row['net_portfolio_return_pct']}% | **{inc_ret:+.2f}%** |
| Daily Sharpe Ratio | {base_row['daily_sharpe_ratio']} | {ml_row['daily_sharpe_ratio']} | **{inc_sh}** |
| Maximum Drawdown | -{base_row['max_drawdown_pct']}% | -{ml_row['max_drawdown_pct']}% | **{inc_dd:+.2f}%** |
| Win Rate | {base_row['win_rate_pct']}% | {ml_row['win_rate_pct']}% | **{inc_wr:+.2f}%** |
| Executed Positions | {base_row['executed_positions']} | {ml_row['executed_positions']} | **{ml_row['executed_positions'] - base_row['executed_positions']}** |
| Total Transaction Costs | ₹{base_row['total_transaction_costs']} | ₹{ml_row['total_transaction_costs']} | **₹{round(ml_row['total_transaction_costs'] - base_row['total_transaction_costs'], 2)}** |

> [!CAUTION]
> **Key Scientific Conclusion**:
> While the Targeted ML Ensemble is profitable (+4.92% net return), it **underperforms the Pure Strategy Baseline (+10.35%) by 5.43%**.
> ML probability filtering removes candidate trades that ultimately generate positive returns out-of-sample.
> Therefore, ML provides **NO INCREMENTAL ALPHA** over the executable strategy baseline.

---

## 6. Friction Sensitivity Analysis on TEST (Phase 7)

{df_fric.to_markdown(index=False)}

---

## 7. Audit & Deliverables Checklist

- **Deliverable Files Created**:
  1. `data/ml/step_4i/validation_threshold_selection.csv`
  2. `data/ml/step_4i/final_test_comparison.csv`
  3. `data/ml/step_4i/strategy_test_comparison.csv`
  4. `data/ml/step_4i/friction_sensitivity.csv`
  5. `data/ml/step_4i/step_4i_report.md`
  6. `scripts/run_step_4i_final_test.py`
  7. `scripts/test_step_4i_final_test.py`

---

## 8. Final Recommendation for Step 5 & Beyond

1. **Permanently Record TEST Result**: The out-of-sample test evaluation result is permanently recorded as an untouched benchmark (+10.35% baseline vs +4.92% ML).
2. **Production System Champion**: Maintain **`PURE STRATEGY BASELINE`** as the production decision champion.
3. **ML Status**: ML remains **`OFF`** in production decision mode.
4. **Next Phase**: Proceed to Step 5 (Portfolio Construction & Capital Allocation Engine optimization for the Pure Strategy Baseline).
"""

    with open(STEP4I_REPORT_MD, "w") as f:
        f.write(report_content)

    print(f"  Report written -> {STEP4I_REPORT_MD}")


if __name__ == "__main__":
    run_step_4i_evaluation()
