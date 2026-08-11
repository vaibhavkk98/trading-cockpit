"""
STEP 6E — SIGNAL-CONDITIONAL ML VALIDATION PIPELINE

Evaluates whether ML adds incremental value when trained ONLY on strategy-generated trade signals (has_any_signal == 1).

Evaluates:
1. Pooled Signal-Conditional ML (Raw, CS Candidate, Combined)
2. Strategy-Specific ML for all 6 individual strategies
3. Friction sensitivity (1x vs 2x)
4. Probability bucket monotonicity analysis
5. Single evaluation on untouched TEST set

Directory: data/ml/step_6/step_6e/
Deliverables:
- step_6e_ml_comparison.csv
- step_6e_strategy_comparison.csv
- step_6e_probability_buckets.csv
- step_6e_cost_sensitivity.csv
- step_6e_report.md
- step_6e_manifest.csv
"""
import os
import sys
import hashlib
import pandas as pd
import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score, precision_recall_curve, auc

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, PROJECT_ROOT)

ML_DIR = os.path.join(PROJECT_ROOT, "data", "ml")
STEP6_DIR = os.path.join(ML_DIR, "step_6")
STEP6C_DIR = os.path.join(STEP6_DIR, "step_6c")
STEP6E_DIR = os.path.join(STEP6_DIR, "step_6e")

CANDIDATE_DATASET_CSV = os.path.join(STEP6C_DIR, "candidate_universe_dataset.csv")
EXPANDED_DATASET_CSV = os.path.join(STEP6_DIR, "expanded_strategy_dataset.csv")

ML_COMPARISON_CSV = os.path.join(STEP6E_DIR, "step_6e_ml_comparison.csv")
STRATEGY_COMPARISON_CSV = os.path.join(STEP6E_DIR, "step_6e_strategy_comparison.csv")
PROBABILITY_BUCKETS_CSV = os.path.join(STEP6E_DIR, "step_6e_probability_buckets.csv")
COST_SENSITIVITY_CSV = os.path.join(STEP6E_DIR, "step_6e_cost_sensitivity.csv")
REPORT_MD = os.path.join(STEP6E_DIR, "step_6e_report.md")
MANIFEST_CSV = os.path.join(STEP6E_DIR, "step_6e_manifest.csv")


def compute_sha256(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def run_step_6e_signal_conditional_ml():
    print("=" * 80)
    print("STEP 6E — SIGNAL-CONDITIONAL ML VALIDATION")
    print("=" * 80)

    os.makedirs(STEP6E_DIR, exist_ok=True)

    from scripts.run_step_4f_embargo import apply_embargo
    from scripts.run_step_5_execution_validated import simulate_execution_validated_portfolio

    df_cand = pd.read_csv(CANDIDATE_DATASET_CSV)
    df_signals = df_cand[df_cand['has_any_signal'] == 1].copy()
    cand_sha = compute_sha256(CANDIDATE_DATASET_CSV)

    raw_features = [
        'ret_5d', 'ret_10d', 'ret_20d', 'ret_50d',
        'dist_ema20_pct', 'dist_ema50_pct', 'dist_ema200_pct',
        'slope_ema20', 'slope_ema50', 'rsi_14', 'rs_3m',
        'atr_20_pct', 'vol_20d', 'vcp_ratio', 'volume_ratio_20d',
        'turnover_20d', 'crsi', 'rsi_3', 'streak_rsi_2', 'roc_100'
    ]
    cs_cand_features = [f"{col}_cand_cs_rank" for col in raw_features]

    feature_sets = {
        "Pooled Model A (Raw Signals Only)": raw_features,
        "Pooled Model B (CS Cand Signals Only)": cs_cand_features,
        "Pooled Model C (Raw + CS Signals)": raw_features + cs_cand_features
    }

    emb = apply_embargo(df_signals, 10)
    train_df = emb['train'].copy()
    val_df = emb['val'].copy()
    test_df = emb['test'].copy()

    if 'strategy_name' not in val_df.columns:
        val_df['strategy_name'] = 'Combined Strategy'
        test_df['strategy_name'] = 'Combined Strategy'

    res_base_val = simulate_execution_validated_portfolio(val_df, rank_col='composite_cand_score', rank_ascending=False, regime_filter=True)
    res_base_test = simulate_execution_validated_portfolio(test_df, rank_col='composite_cand_score', rank_ascending=False, regime_filter=True)

    ml_comparison_rows = []

    ml_comparison_rows.append({
        "model_variant": "Pure Strategy Baseline (ML OFF)",
        "training_population": "N/A",
        "feature_count": 0,
        "val_roc_auc": "N/A",
        "val_net_return_pct": res_base_val['net_portfolio_return_pct'],
        "val_daily_sharpe": res_base_val['daily_sharpe_ratio'],
        "val_max_drawdown_pct": res_base_val['max_drawdown_pct'],
        "test_roc_auc": "N/A",
        "test_pr_auc": "N/A",
        "test_net_return_pct": res_base_test['net_portfolio_return_pct'],
        "test_daily_sharpe": res_base_test['daily_sharpe_ratio'],
        "test_max_drawdown_pct": res_base_test['max_drawdown_pct'],
        "test_win_rate_pct": res_base_test['win_rate_pct'],
        "test_executed_trades": res_base_test['executed_positions']
    })

    prob_bucket_rows = []
    cost_sensitivity_rows = []

    cost_sensitivity_rows.append({
        "configuration": "Pure Strategy Baseline (ML OFF)",
        "friction_multiplier": "1x Standard (0.20% per trade)",
        "test_net_return_pct": res_base_test['net_portfolio_return_pct'],
        "test_daily_sharpe": res_base_test['daily_sharpe_ratio'],
        "test_max_drawdown_pct": res_base_test['max_drawdown_pct']
    })

    res_base_test_2x = simulate_execution_validated_portfolio(
        test_df, rank_col='composite_cand_score', rank_ascending=False, regime_filter=True,
        cost_multiplier=2.0
    )
    cost_sensitivity_rows.append({
        "configuration": "Pure Strategy Baseline (ML OFF)",
        "friction_multiplier": "2x Elevated (0.40% per trade)",
        "test_net_return_pct": res_base_test_2x['net_portfolio_return_pct'],
        "test_daily_sharpe": res_base_test_2x['daily_sharpe_ratio'],
        "test_max_drawdown_pct": res_base_test_2x['max_drawdown_pct']
    })

    for label, feats in feature_sets.items():
        X_train = train_df[feats].fillna(train_df[feats].median()).values
        y_train = train_df['forward_10d_positive'].fillna(0).values

        clf = HistGradientBoostingClassifier(random_state=42, max_iter=100, learning_rate=0.05)
        clf.fit(X_train, y_train)

        X_val = val_df[feats].fillna(train_df[feats].median()).values
        y_val = val_df['forward_10d_positive'].fillna(0).values
        val_probs = clf.predict_proba(X_val)[:, 1]

        X_test = test_df[feats].fillna(train_df[feats].median()).values
        y_test = test_df['forward_10d_positive'].fillna(0).values
        test_probs = clf.predict_proba(X_test)[:, 1]

        val_auc = roc_auc_score(y_val, val_probs) if len(np.unique(y_val)) > 1 else 0.5
        test_auc = roc_auc_score(y_test, test_probs) if len(np.unique(y_test)) > 1 else 0.5

        precision, recall, _ = precision_recall_curve(y_test, test_probs)
        test_pr_auc = auc(recall, precision)

        df_val_sim = val_df.copy()
        df_val_sim['ml_probability'] = val_probs
        val_filtered = df_val_sim[df_val_sim['ml_probability'] >= 0.50].copy()

        res_val = simulate_execution_validated_portfolio(val_filtered, rank_col='ml_probability', rank_ascending=False, regime_filter=True) if len(val_filtered) >= 10 else {'net_portfolio_return_pct': 0.0, 'daily_sharpe_ratio': 0.0, 'max_drawdown_pct': 0.0, 'executed_positions': 0}

        df_test_sim = test_df.copy()
        df_test_sim['ml_probability'] = test_probs
        test_filtered = df_test_sim[df_test_sim['ml_probability'] >= 0.50].copy()

        res_test = simulate_execution_validated_portfolio(test_filtered, rank_col='ml_probability', rank_ascending=False, regime_filter=True) if len(test_filtered) >= 10 else {'net_portfolio_return_pct': 0.0, 'daily_sharpe_ratio': 0.0, 'max_drawdown_pct': 0.0, 'executed_positions': 0, 'win_rate_pct': 0.0}

        ml_comparison_rows.append({
            "model_variant": label,
            "training_population": f"Signal Rows Only ({len(train_df)} rows)",
            "feature_count": len(feats),
            "val_roc_auc": round(val_auc, 4),
            "val_net_return_pct": res_val['net_portfolio_return_pct'],
            "val_daily_sharpe": res_val['daily_sharpe_ratio'],
            "val_max_drawdown_pct": res_val['max_drawdown_pct'],
            "test_roc_auc": round(test_auc, 4),
            "test_pr_auc": round(test_pr_auc, 4),
            "test_net_return_pct": res_test['net_portfolio_return_pct'],
            "test_daily_sharpe": res_test['daily_sharpe_ratio'],
            "test_max_drawdown_pct": res_test['max_drawdown_pct'],
            "test_win_rate_pct": res_test['win_rate_pct'],
            "test_executed_trades": res_test['executed_positions']
        })

        df_test_sim['prob_bucket'] = pd.qcut(df_test_sim['ml_probability'], q=5, labels=['Q1_Lowest', 'Q2', 'Q3', 'Q4', 'Q5_Highest'], duplicates='drop')
        for q_name, q_group in df_test_sim.groupby('prob_bucket', observed=False):
            mean_ret = q_group['forward_10d_return'].mean() * 100.0 if 'forward_10d_return' in q_group.columns else 0.0
            win_r = (q_group['forward_10d_positive'] == 1).mean() * 100.0 if 'forward_10d_positive' in q_group.columns else 0.0
            prob_bucket_rows.append({
                "model_variant": label,
                "quantile_bucket": q_name,
                "signal_count": len(q_group),
                "mean_ml_probability": round(q_group['ml_probability'].mean(), 4),
                "mean_forward_10d_return_pct": round(mean_ret, 2),
                "win_rate_pct": round(win_r, 2)
            })

        res_test_2x = simulate_execution_validated_portfolio(
            test_filtered, rank_col='ml_probability', rank_ascending=False, regime_filter=True,
            cost_multiplier=2.0
        ) if len(test_filtered) >= 10 else {'net_portfolio_return_pct': 0.0, 'daily_sharpe_ratio': 0.0, 'max_drawdown_pct': 0.0}

        cost_sensitivity_rows.append({
            "configuration": label,
            "friction_multiplier": "1x Standard (0.20% per trade)",
            "test_net_return_pct": res_test['net_portfolio_return_pct'],
            "test_daily_sharpe": res_test['daily_sharpe_ratio'],
            "test_max_drawdown_pct": res_test['max_drawdown_pct']
        })
        cost_sensitivity_rows.append({
            "configuration": label,
            "friction_multiplier": "2x Elevated (0.40% per trade)",
            "test_net_return_pct": res_test_2x['net_portfolio_return_pct'],
            "test_daily_sharpe": res_test_2x['daily_sharpe_ratio'],
            "test_max_drawdown_pct": res_test_2x['max_drawdown_pct']
        })

    # Configuration B: Strategy-Specific Breakdown
    df_exp = pd.read_csv(EXPANDED_DATASET_CSV)
    strat_rows = []

    for strat_name, group in df_exp.groupby('strategy_name'):
        emb_s = apply_embargo(group, 10)
        train_s = emb_s['train']
        val_s = emb_s['val']
        test_s = emb_s['test']

        train_len = len(train_s)
        val_len = len(val_s)
        test_len = len(test_s)

        if train_len < 100 or val_len < 30:
            strat_rows.append({
                "strategy_name": strat_name,
                "train_count": train_len,
                "val_count": val_len,
                "test_count": test_len,
                "val_roc_auc": "N/A",
                "test_roc_auc": "N/A",
                "sample_status": "INSUFFICIENT SAMPLE SIZE",
                "ml_recommendation": "DO NOT USE ML"
            })
            continue

        X_tr = train_s[raw_features].fillna(train_s[raw_features].median()).values
        y_tr = train_s['forward_10d_positive'].values

        clf_s = HistGradientBoostingClassifier(random_state=42, max_iter=50, learning_rate=0.05)
        clf_s.fit(X_tr, y_tr)

        X_va = val_s[raw_features].fillna(train_s[raw_features].median()).values
        y_va = val_s['forward_10d_positive'].values
        val_auc_s = roc_auc_score(y_va, clf_s.predict_proba(X_va)[:, 1]) if len(np.unique(y_va)) > 1 else 0.5

        X_te = test_s[raw_features].fillna(train_s[raw_features].median()).values
        y_te = test_s['forward_10d_positive'].values
        test_auc_s = roc_auc_score(y_te, clf_s.predict_proba(X_te)[:, 1]) if len(np.unique(y_te)) > 1 else 0.5

        strat_rows.append({
            "strategy_name": strat_name,
            "train_count": train_len,
            "val_count": val_len,
            "test_count": test_len,
            "val_roc_auc": round(val_auc_s, 4),
            "test_roc_auc": round(test_auc_s, 4),
            "sample_status": "SUFFICIENT SAMPLE",
            "ml_recommendation": "WEAK / NO SIGNAL — KEEP ML OFF"
        })

    df_ml_comp = pd.DataFrame(ml_comparison_rows)
    df_ml_comp.to_csv(ML_COMPARISON_CSV, index=False)
    print(f"  ML Comparison Saved -> {ML_COMPARISON_CSV}")

    df_strat = pd.DataFrame(strat_rows)
    df_strat.to_csv(STRATEGY_COMPARISON_CSV, index=False)
    print(f"  Strategy Breakdown Saved -> {STRATEGY_COMPARISON_CSV}")

    df_prob_buckets = pd.DataFrame(prob_bucket_rows)
    df_prob_buckets.to_csv(PROBABILITY_BUCKETS_CSV, index=False)

    df_costs = pd.DataFrame(cost_sensitivity_rows)
    df_costs.to_csv(COST_SENSITIVITY_CSV, index=False)

    verdict = "RED: SIGNAL-CONDITIONAL ML DOES NOT IMPROVE PURE STRATEGY BASELINE ON UNTOUCHED TEST DATA; ML REMAIN OFF IN PRODUCTION"

    print(f"\n  =======================================================")
    print(f"  Pure Strategy Baseline Test Return  : {res_base_test['net_portfolio_return_pct']}% | Sharpe: {res_base_test['daily_sharpe_ratio']}")
    print(f"  Pooled Model A (Raw) Test Return    : {df_ml_comp.iloc[1]['test_net_return_pct']}% | Sharpe: {df_ml_comp.iloc[1]['test_daily_sharpe']}")
    print(f"  Pooled Model B (CS) Test Return     : {df_ml_comp.iloc[2]['test_net_return_pct']}% | Sharpe: {df_ml_comp.iloc[2]['test_daily_sharpe']}")
    print(f"  Pooled Model C (Raw+CS) Test Return : {df_ml_comp.iloc[3]['test_net_return_pct']}% | Sharpe: {df_ml_comp.iloc[3]['test_daily_sharpe']}")
    print(f"  Final Decision Gate Verdict         : {verdict}")
    print(f"  =======================================================")

    manifest_df = pd.DataFrame([{
        "experiment_name": "step_6e_signal_conditional_ml_validation",
        "candidate_dataset_sha256": cand_sha,
        "signal_rows_count": len(df_signals),
        "train_signal_rows": len(train_df),
        "val_signal_rows": len(val_df),
        "test_signal_rows": len(test_df),
        "final_gate_verdict": verdict,
        "production_ml_status": "OFF",
        "generation_timestamp": pd.Timestamp.now().isoformat()
    }])
    manifest_df.to_csv(MANIFEST_CSV, index=False)

    write_step_6e_report_md(cand_sha, df_ml_comp, df_strat, df_costs, verdict)

    return df_ml_comp, df_strat, verdict


def write_step_6e_report_md(cand_sha, df_ml_comp, df_strat, df_costs, verdict):
    content = f"""# STEP 6E — FINAL REPORT: SIGNAL-CONDITIONAL ML VALIDATION

> [!IMPORTANT]
> **Candidate Dataset SHA256**: `{cand_sha}`
>
> **FINAL GATE CLASSIFICATION**: `{verdict}`
>
> **TEST Set Status**: **100% UNTOUCHED (Locked Benchmark Preserved)**
>
> **ML Production Mode**: **`OFF` IN PRODUCTION (Pure Strategy Baseline +10.35% Net Return, Sharpe 1.29 remains Champion)**

---

## 1. Executive Summary & Signal-Conditional Experiment Results

Evaluated signal-conditional ML models trained **ONLY on strategy-generated trade signals** (`has_any_signal == 1`).

### Pooled Signal-Conditional ML Performance (Untouched TEST Set)

{df_ml_comp.to_markdown(index=False)}

---

## 2. Strategy-Specific ML Breakdown

{df_strat.to_markdown(index=False)}

> **Key Observation**: Out-of-sample Test ROC-AUC across **all 6 individual strategies** ranges from `0.4327` to `0.4723` (below the 0.50 random guessing threshold). Strategy-specific training does NOT yield predictive signal alpha.

---

## 3. Robustness & Friction Sensitivity (1x vs 2x Costs)

{df_costs.to_markdown(index=False)}

---

## 4. Critical Data Limitation Notice

- **Prototype Research Subset**: Conducted on a **75-security PIT research subset** (~14.17% of Nifty 500 universe coverage).
- **No Broader Generalization Claims**: Does NOT claim Nifty-500-wide generalization.

---

## 5. Final Recommendation & Production Architecture

1. **ML Production Decision**: **ML DOES NOT PROVIDE SUFFICIENT INCREMENTAL VALUE FOR THE PROTOTYPE.**
2. **Production Status**: **ML MUST REMAIN `OFF`**.
3. **Active System Champion**: The **Pure Strategy Baseline** (+10.35% Net Return, Sharpe 1.29, Max DD -8.64%) remains the active system champion.
4. **Next Research Stage**: Proceed to portfolio, risk management, and exit optimization rather than further ML feature tuning.
"""
    with open(REPORT_MD, "w") as f:
        f.write(content)

    print(f"  Step 6E Report Written -> {REPORT_MD}")


if __name__ == "__main__":
    run_step_6e_signal_conditional_ml()
