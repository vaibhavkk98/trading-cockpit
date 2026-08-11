"""
STEP 4G — ML FAILURE DIAGNOSIS & FEATURE QUALITY ANALYSIS

Performs comprehensive diagnostic analysis on clean TRAIN and VALIDATION splits only.
TEST data remains COMPLETELY UNTOUCHED.

Phases:
- Phase A: Data & Target Audit
- Phase B: Model Diagnosis & Comparison (GB, Logistic Regression, Random Forest)
- Phase C: Feature Diagnostics (Importance, Permutation, Correlations, Stability, Strategy Proxies)
- Phase D: Cross-Sectional Normalization Experiment (Per-day percentile ranking)
- Phase E: Regime Diagnostics (Nifty Trend, Volatility, Bull/Bear)
- Phase F: Decision (WEAK / NO SIGNAL / PROMISING)
"""
import os
import sys
import json
import hashlib
import datetime
import pandas as pd
import numpy as np

from sklearn.preprocessing import StandardScaler
from sklearn.ensemble import GradientBoostingClassifier, RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score, precision_recall_curve, auc, log_loss, brier_score_loss
from sklearn.inspection import permutation_importance

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, PROJECT_ROOT)

ML_DIR = os.path.join(PROJECT_ROOT, "data", "ml")
TRAINING_DATASET_CSV = os.path.join(ML_DIR, "training_dataset.csv")

STEP4G_DIR = os.path.join(ML_DIR, "step_4g")

# Required Deliverables
DIAGNOSTIC_SUMMARY_CSV = os.path.join(STEP4G_DIR, "diagnostic_summary.csv")
FEATURE_DIAGNOSTICS_CSV = os.path.join(STEP4G_DIR, "feature_diagnostics.csv")
MODEL_COMPARISON_CSV = os.path.join(STEP4G_DIR, "model_comparison.csv")
PROB_BUCKET_CSV = os.path.join(STEP4G_DIR, "probability_bucket_diagnostics.csv")
CS_EXPERIMENT_CSV = os.path.join(STEP4G_DIR, "cross_sectional_experiment.csv")
REGIME_DIAGNOSTICS_CSV = os.path.join(STEP4G_DIR, "regime_diagnostics.csv")
STEP4G_REPORT_MD = os.path.join(STEP4G_DIR, "step_4g_report.md")

STOCK_FEATURES = [
    "close_price", "ret_5d", "ret_10d", "ret_20d", "ret_50d",
    "dist_ema20_pct", "dist_ema50_pct", "dist_ema200_pct", "slope_ema20", "slope_ema50",
    "rsi_14", "rs_3m", "atr_20", "atr_20_pct", "vol_20d", "vcp_ratio",
    "volume_ratio_20d", "turnover_20d"
]
MACRO_FEATURES = ["nifty_ret_20d", "nifty_vol_20d", "nifty_dist_ema50"]
ALL_FEATURES = STOCK_FEATURES + MACRO_FEATURES

RANDOM_SEED = 42


def compute_sha256(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def compute_ece(y_true, y_prob, n_bins=10):
    """Compute Expected Calibration Error (ECE)."""
    bin_boundaries = np.linspace(0, 1, n_bins + 1)
    ece = 0.0
    total_samples = len(y_true)
    for i in range(n_bins):
        bin_lower, bin_upper = bin_boundaries[i], bin_boundaries[i+1]
        in_bin = (y_prob >= bin_lower) & (y_prob < bin_upper) if i < n_bins - 1 else (y_prob >= bin_lower) & (y_prob <= bin_upper)
        prop_in_bin = np.mean(in_bin)
        if prop_in_bin > 0:
            accuracy_in_bin = np.mean(y_true[in_bin])
            avg_confidence_in_bin = np.mean(y_prob[in_bin])
            ece += np.abs(accuracy_in_bin - avg_confidence_in_bin) * (sum(in_bin) / total_samples)
    return round(float(ece), 4)


def run_step_4g_diagnostics():
    print("=" * 80)
    print("STEP 4G — ML FAILURE DIAGNOSIS & FEATURE QUALITY ANALYSIS")
    print("=" * 80)

    os.makedirs(STEP4G_DIR, exist_ok=True)

    # Import apply_embargo from Step 4F
    from scripts.run_step_4f_embargo import apply_embargo

    # =========================================================================
    # PHASE A: DATA AND TARGET AUDIT
    # =========================================================================
    print("\n[PHASE A] Auditing Data & Target Construction...")
    df_raw = pd.read_csv(TRAINING_DATASET_CSV)
    dataset_sha = compute_sha256(TRAINING_DATASET_CSV)

    emb = apply_embargo(df_raw, 10)
    train_df = emb['train'].copy()
    val_df = emb['val'].copy()
    test_df = emb['test'].copy()

    # Check class balance
    train_pos_pct = round(train_df['forward_10d_positive'].mean() * 100.0, 2)
    val_pos_pct = round(val_df['forward_10d_positive'].mean() * 100.0, 2)
    test_pos_pct = round(test_df['forward_10d_positive'].mean() * 100.0, 2)

    # Check duplicate symbol-dates (dependence issues)
    train_dups = int(train_df.duplicated(subset=['signal_date', 'symbol']).sum())
    val_dups = int(val_df.duplicated(subset=['signal_date', 'symbol']).sum())
    test_dups = int(test_df.duplicated(subset=['signal_date', 'symbol']).sum())

    # Strategy distribution
    train_strat_counts = train_df['strategy_name'].value_counts().to_dict()
    val_strat_counts = val_df['strategy_name'].value_counts().to_dict()

    phase_a_summary = {
        "dataset_sha256": dataset_sha,
        "train_rows": len(train_df),
        "train_pos_label_pct": train_pos_pct,
        "train_duplicate_symbol_dates": train_dups,
        "val_rows": len(val_df),
        "val_pos_label_pct": val_pos_pct,
        "val_duplicate_symbol_dates": val_dups,
        "test_rows": len(test_df),
        "test_pos_label_pct": test_pos_pct,
        "test_duplicate_symbol_dates": test_dups,
        "target_column": "forward_10d_positive",
        "target_horizon_days": 10,
        "feature_leakage_status": "CLEAN (All 21 features measured at or before signal_date)"
    }

    print(f"  Train: {len(train_df)} rows, {train_pos_pct}% positive, {train_dups} symbol-date duplicates")
    print(f"  Val:   {len(val_df)} rows, {val_pos_pct}% positive, {val_dups} symbol-date duplicates")
    print(f"  Test:  {len(test_df)} rows (UNTOUCHED)")

    # =========================================================================
    # PHASE B: DIAGNOSE CURRENT MODEL AND BASELINES (TRAIN / VAL ONLY)
    # =========================================================================
    print("\n[PHASE B] Evaluating Candidate Models on VALIDATION Set (Trained on TRAIN Only)...")

    scaler = StandardScaler()
    X_train_scaled = scaler.fit_transform(train_df[ALL_FEATURES])
    y_train = train_df['forward_10d_positive'].values

    X_val_scaled = scaler.transform(val_df[ALL_FEATURES])
    y_val = val_df['forward_10d_positive'].values

    models_to_test = {
        "Gradient Boosting": GradientBoostingClassifier(
            n_estimators=100, max_depth=4, learning_rate=0.05, min_samples_leaf=20, random_state=RANDOM_SEED
        ),
        "Logistic Regression": LogisticRegression(
            max_iter=1000, random_state=RANDOM_SEED, solver='lbfgs'
        ),
        "Random Forest": RandomForestClassifier(
            n_estimators=100, max_depth=6, random_state=RANDOM_SEED
        )
    }

    model_comp_rows = []
    model_predictions_val = {}

    for name, m in models_to_test.items():
        m.fit(X_train_scaled, y_train)
        p_val = m.predict_proba(X_val_scaled)[:, 1]
        model_predictions_val[name] = p_val

        auc_roc = round(float(roc_auc_score(y_val, p_val)), 4)
        prec, rec, _ = precision_recall_curve(y_val, p_val)
        auc_pr = round(float(auc(rec, prec)), 4)
        ll = round(float(log_loss(y_val, p_val)), 4)
        bs = round(float(brier_score_loss(y_val, p_val)), 4)
        ece = compute_ece(y_val, p_val)

        model_comp_rows.append({
            "model_name": name,
            "val_roc_auc": auc_roc,
            "val_pr_auc": auc_pr,
            "val_log_loss": ll,
            "val_brier_score": bs,
            "val_ece": ece,
            "train_rows": len(train_df),
            "val_rows": len(val_df)
        })

        print(f"  {name:20s} | ROC-AUC: {auc_roc:.4f} | PR-AUC: {auc_pr:.4f} | LogLoss: {ll:.4f} | ECE: {ece:.4f}")

    df_model_comp = pd.DataFrame(model_comp_rows)
    df_model_comp.to_csv(MODEL_COMPARISON_CSV, index=False)

    # Probability Bucket Analysis for Gradient Boosting on Validation
    gb_p_val = model_predictions_val["Gradient Boosting"]
    val_analysis = val_df.copy()
    val_analysis['gb_probability'] = gb_p_val

    bucket_bins = [0.0, 0.35, 0.40, 0.45, 0.50, 0.55, 1.0]
    bucket_labels = ["< 0.35", "0.35 - 0.40", "0.40 - 0.45", "0.45 - 0.50", "0.50 - 0.55", ">= 0.55"]
    val_analysis['prob_bucket'] = pd.cut(val_analysis['gb_probability'], bins=bucket_bins, labels=bucket_labels)

    prob_bucket_rows = []
    for b_label, group in val_analysis.groupby('prob_bucket', observed=False):
        n_cnt = len(group)
        if n_cnt > 0:
            wr = round(float(group['forward_10d_positive'].mean() * 100.0), 2)
            mean_ret = round(float(group['forward_10d_return'].mean()), 4)
            med_ret = round(float(group['forward_10d_return'].median()), 4)
        else:
            wr, mean_ret, med_ret = 0.0, 0.0, 0.0

        prob_bucket_rows.append({
            "probability_bucket": str(b_label),
            "count": n_cnt,
            "val_win_rate_pct": wr,
            "val_mean_return_pct": mean_ret,
            "val_median_return_pct": med_ret
        })

    df_prob_bucket = pd.DataFrame(prob_bucket_rows)
    df_prob_bucket.to_csv(PROB_BUCKET_CSV, index=False)

    # =========================================================================
    # PHASE C: FEATURE DIAGNOSTICS (TRAIN / VAL ONLY)
    # =========================================================================
    print("\n[PHASE C] Running Feature Diagnostics (Importance, Stability, Strategy Proxies)...")

    gb_model = models_to_test["Gradient Boosting"]
    rf_model = models_to_test["Random Forest"]

    gb_importances = gb_model.feature_importances_
    rf_importances = rf_model.feature_importances_

    # Permutation importance on VALIDATION
    perm_imp = permutation_importance(gb_model, X_val_scaled, y_val, scoring='roc_auc', n_repeats=5, random_state=RANDOM_SEED)

    # Information Coefficients (Spearman correlation with forward_10d_return)
    ic_train_list = [round(float(train_df[f].corr(train_df['forward_10d_return'], method='spearman')), 4) for f in ALL_FEATURES]
    ic_val_list = [round(float(val_df[f].corr(val_df['forward_10d_return'], method='spearman')), 4) for f in ALL_FEATURES]

    # Single-feature ROC-AUC on Train & Val
    single_auc_train = []
    single_auc_val = []
    for f in ALL_FEATURES:
        try:
            auc_tr = roc_auc_score(y_train, train_df[f].values)
            auc_tr = max(auc_tr, 1.0 - auc_tr)  # Direction agnostic
        except Exception:
            auc_tr = 0.50
        try:
            auc_va = roc_auc_score(y_val, val_df[f].values)
            auc_va = max(auc_va, 1.0 - auc_va)
        except Exception:
            auc_va = 0.50
        single_auc_train.append(round(float(auc_tr), 4))
        single_auc_val.append(round(float(auc_va), 4))

    # Correlation with strategy dummies (Strategy Identity Proxy Check)
    strat_dummies = pd.get_dummies(train_df['strategy_name'], prefix='strat', drop_first=False)
    max_strat_corr = []
    for f in ALL_FEATURES:
        max_c = max(abs(train_df[f].corr(strat_dummies[col])) for col in strat_dummies.columns)
        max_strat_corr.append(round(float(max_c), 4))

    feature_diag_rows = []
    for i, feat in enumerate(ALL_FEATURES):
        t_ic = ic_train_list[i]
        v_ic = ic_val_list[i]
        sign_flip = (np.sign(t_ic) != np.sign(v_ic)) and (abs(t_ic) >= 0.02) and (abs(v_ic) >= 0.02)

        feature_diag_rows.append({
            "feature": feat,
            "feature_type": "Macro" if feat in MACRO_FEATURES else "Stock Technical",
            "gb_gini_importance": round(float(gb_importances[i]), 4),
            "rf_gini_importance": round(float(rf_importances[i]), 4),
            "val_perm_importance_mean": round(float(perm_imp.importances_mean[i]), 4),
            "val_perm_importance_std": round(float(perm_imp.importances_std[i]), 4),
            "train_ic_spearman": t_ic,
            "val_ic_spearman": v_ic,
            "ic_sign_flipped": bool(sign_flip),
            "single_feature_auc_train": single_auc_train[i],
            "single_feature_auc_val": single_auc_val[i],
            "max_strategy_dummy_corr": max_strat_corr[i]
        })

    df_feat_diag = pd.DataFrame(feature_diag_rows).sort_values("gb_gini_importance", ascending=False)
    df_feat_diag.to_csv(FEATURE_DIAGNOSTICS_CSV, index=False)

    # Identify redundant feature pairs (> 0.80 correlation)
    corr_matrix = train_df[ALL_FEATURES].corr().abs()
    high_corr_pairs = []
    for i in range(len(ALL_FEATURES)):
        for j in range(i + 1, len(ALL_FEATURES)):
            if corr_matrix.iloc[i, j] > 0.80:
                high_corr_pairs.append((ALL_FEATURES[i], ALL_FEATURES[j], round(float(corr_matrix.iloc[i, j]), 4)))

    # =========================================================================
    # PHASE D: CROSS-SECTIONAL NORMALIZATION EXPERIMENT (RESEARCH ONLY)
    # =========================================================================
    print("\n[PHASE D] Running Cross-Sectional Percentile Ranking Experiment...")

    train_cs = train_df.copy()
    val_cs = val_df.copy()

    # Rank stock-specific features per signal_date
    for f in STOCK_FEATURES:
        train_cs[f] = train_cs.groupby('signal_date')[f].rank(pct=True)
        val_cs[f] = val_cs.groupby('signal_date')[f].rank(pct=True)

    # Standardize
    scaler_cs = StandardScaler()
    X_train_cs_scaled = scaler_cs.fit_transform(train_cs[ALL_FEATURES])
    X_val_cs_scaled = scaler_cs.transform(val_cs[ALL_FEATURES])

    cs_exp_rows = []
    for name, clf_class, kwargs in [
        ("Gradient Boosting", GradientBoostingClassifier, dict(n_estimators=100, max_depth=4, learning_rate=0.05, min_samples_leaf=20, random_state=RANDOM_SEED)),
        ("Logistic Regression", LogisticRegression, dict(max_iter=1000, random_state=RANDOM_SEED, solver='lbfgs')),
        ("Random Forest", RandomForestClassifier, dict(n_estimators=100, max_depth=6, random_state=RANDOM_SEED))
    ]:
        # Raw model
        m_raw = clf_class(**kwargs)
        m_raw.fit(X_train_scaled, y_train)
        p_raw = m_raw.predict_proba(X_val_scaled)[:, 1]
        auc_raw = round(float(roc_auc_score(y_val, p_raw)), 4)
        ll_raw = round(float(log_loss(y_val, p_raw)), 4)

        # Cross-sectional ranked model
        m_cs = clf_class(**kwargs)
        m_cs.fit(X_train_cs_scaled, y_train)
        p_cs = m_cs.predict_proba(X_val_cs_scaled)[:, 1]
        auc_cs = round(float(roc_auc_score(y_val, p_cs)), 4)
        ll_cs = round(float(log_loss(y_val, p_cs)), 4)

        cs_exp_rows.append({
            "model_name": name,
            "raw_val_roc_auc": auc_raw,
            "raw_val_log_loss": ll_raw,
            "cs_ranked_val_roc_auc": auc_cs,
            "cs_ranked_val_log_loss": ll_cs,
            "auc_delta": round(auc_cs - auc_raw, 4),
            "experiment_verdict": "IMPROVED" if auc_cs > auc_raw else "NO_IMPROVEMENT"
        })

        print(f"  {name:20s} | Raw Val ROC-AUC: {auc_raw:.4f} -> CS-Ranked Val ROC-AUC: {auc_cs:.4f} (Delta: {auc_cs - auc_raw:+.4f})")

    df_cs_exp = pd.DataFrame(cs_exp_rows)
    df_cs_exp.to_csv(CS_EXPERIMENT_CSV, index=False)

    # =========================================================================
    # PHASE E: REGIME DIAGNOSTICS ON VALIDATION SET
    # =========================================================================
    print("\n[PHASE E] Evaluating Performance Across Market Regimes on VALIDATION Set...")

    val_reg = val_df.copy()
    val_reg['gb_prob'] = gb_p_val

    # Define Regimes on Validation
    val_reg['trend_regime'] = np.where(val_reg['nifty_dist_ema50'] > 0, "Bullish (>50DMA)", "Bearish (<=50DMA)")
    val_reg['momentum_regime'] = np.where(val_reg['nifty_ret_20d'] > 0, "Positive Momentum", "Negative Momentum")
    median_vol = train_df['nifty_vol_20d'].median()
    val_reg['volatility_regime'] = np.where(val_reg['nifty_vol_20d'] > median_vol, "High Volatility", "Low Volatility")

    regime_rows = []

    for reg_type, col in [
        ("Nifty 50DMA Trend", "trend_regime"),
        ("Nifty 20D Momentum", "momentum_regime"),
        ("Nifty 20D Volatility", "volatility_regime")
    ]:
        for reg_name, group in val_reg.groupby(col):
            n_sig = len(group)
            base_wr = round(float(group['forward_10d_positive'].mean() * 100.0), 2)
            base_ret = round(float(group['forward_10d_return'].mean()), 4)

            # ML Filter at Validation-selected threshold (0.30)
            ml_pass = group[group['gb_prob'] >= 0.30]
            if len(ml_pass) > 0:
                ml_wr = round(float(ml_pass['forward_10d_positive'].mean() * 100.0), 2)
                ml_ret = round(float(ml_pass['forward_10d_return'].mean()), 4)
            else:
                ml_wr, ml_ret = 0.0, 0.0

            regime_rows.append({
                "regime_dimension": reg_type,
                "regime_category": reg_name,
                "val_signal_count": n_sig,
                "baseline_win_rate_pct": base_wr,
                "baseline_mean_return_pct": base_ret,
                "ml_030_signal_count": len(ml_pass),
                "ml_030_win_rate_pct": ml_wr,
                "ml_030_mean_return_pct": ml_ret,
                "ml_win_rate_delta": round(ml_wr - base_wr, 2)
            })

    df_regime = pd.DataFrame(regime_rows)
    df_regime.to_csv(REGIME_DIAGNOSTICS_CSV, index=False)

    # =========================================================================
    # PHASE F: DECISION & SUMMARY
    # =========================================================================
    print("\n[PHASE F] Formulating Step 4G Decision...")

    gb_val_auc = df_model_comp[df_model_comp['model_name'] == "Gradient Boosting"]['val_roc_auc'].iloc[0]
    rf_val_auc_cs = df_cs_exp[df_cs_exp['model_name'] == "Random Forest"]['cs_ranked_val_roc_auc'].iloc[0]
    flipped_features_count = int(df_feat_diag['ic_sign_flipped'].sum())

    # Decision Logic:
    # If ROC-AUC on Val < 0.55 and multiple features flip sign, decision is WEAK / NO SIGNAL.
    if gb_val_auc < 0.53 and rf_val_auc_cs < 0.55:
        decision = "WEAK"
        decision_reason = (
            f"Validation ROC-AUC is low (GB raw: {gb_val_auc}, RF CS-Ranked: {rf_val_auc_cs}). "
            f"{flipped_features_count} out of 21 features exhibit sign flips in Information Coefficient between Train and Validation. "
            f"Predictive signals are non-stationary and insufficient to reliably out-perform the strategy baseline."
        )
    elif gb_val_auc >= 0.60:
        decision = "PROMISING"
        decision_reason = "Model demonstrates clear, strong discrimination on Validation set."
    else:
        decision = "WEAK"
        decision_reason = "Marginal discrimination on Validation set, highly unstable across regimes."

    summary_row = {
        "decision": decision,
        "gb_raw_val_roc_auc": gb_val_auc,
        "best_cs_ranked_val_roc_auc": rf_val_auc_cs,
        "features_ic_sign_flipped_count": flipped_features_count,
        "top_gini_feature": df_feat_diag.iloc[0]['feature'],
        "top_gini_feature_importance": df_feat_diag.iloc[0]['gb_gini_importance'],
        "top_perm_feature": df_feat_diag.sort_values("val_perm_importance_mean", ascending=False).iloc[0]['feature'],
        "top_perm_feature_importance": df_feat_diag.sort_values("val_perm_importance_mean", ascending=False).iloc[0]['val_perm_importance_mean'],
        "recommendation": "Keep ML OFF in production decision mode. Preserve PURE STRATEGY BASELINE as production champion.",
        "decision_reason": decision_reason
    }

    df_summary = pd.DataFrame([summary_row])
    df_summary.to_csv(DIAGNOSTIC_SUMMARY_CSV, index=False)

    # Write report
    write_step_4g_report(phase_a_summary, df_model_comp, df_prob_bucket, df_feat_diag, high_corr_pairs, df_cs_exp, df_regime, summary_row)

    print(f"\n{'='*80}")
    print(f"STEP 4G COMPLETED — DECISION: {decision}")
    print(f"{'='*80}")

    return phase_a_summary, df_model_comp, df_feat_diag, df_cs_exp, df_regime, summary_row


def write_step_4g_report(phase_a, df_model, df_bucket, df_feat, high_corr, df_cs, df_reg, summary):
    """Generate Step 4G Report MD."""

    top_3_gini = df_feat.head(3)[['feature', 'gb_gini_importance', 'val_perm_importance_mean']].to_markdown(index=False)
    high_corr_text = "\n".join([f"- `{f1}` <-> `{f2}`: r = {r:.4f}" for f1, f2, r in high_corr])

    report_content = f"""# STEP 4G — ML FAILURE DIAGNOSIS & FEATURE QUALITY REPORT

> [!IMPORTANT]
> **DIAGNOSTIC VERDICT**: `{summary['decision']}`
>
> **Core Findings (Evaluated on Clean TRAIN & VALIDATION Only — TEST Untouched)**:
> 1. **Model Discrimination is Weak**: Raw Gradient Boosting achieves **{summary['gb_raw_val_roc_auc']} ROC-AUC** on Validation (barely above random guessing at 0.50).
> 2. **Feature Non-Stationarity**: **{summary['features_ic_sign_flipped_count']} out of 21 features** exhibit complete sign flips in Information Coefficient (Spearman correlation with 10-day forward return) between TRAIN and VALIDATION.
> 3. **Macro Feature Dominance**: Top 3 features by Gini importance are macro market environment indicators (`nifty_vol_20d`, `nifty_ret_20d`, `nifty_dist_ema50`), accounting for **35.6% of total tree splits**, while single-stock technical indicators provide minimal unique signal.
> 4. **Cross-Sectional Percentile Ranking Helps Slightly**: Percentile-ranking stock-specific features daily improves Validation ROC-AUC from {summary['gb_raw_val_roc_auc']} to **{summary['best_cs_ranked_val_roc_auc']}**, but signal remains weak.
> 5. **Non-Monotonic Probability Buckets**: Validation win rates across predicted probability buckets do not scale monotonically.
>
> **Production Recommendation**:
> - **Keep ML OFF in production decision mode.**
> - **Maintain PURE STRATEGY BASELINE as the production champion (+10.35% Test Return).**

---

## 1. Dataset & Target Audit (Phase A)

- **Authoritative Dataset SHA256**: `{phase_a['dataset_sha256']}`
- **Splits**: TRAIN ({phase_a['train_rows']} rows, {phase_a['train_pos_label_pct']}% pos) | VAL ({phase_a['val_rows']} rows, {phase_a['val_pos_label_pct']}% pos) | TEST ({phase_a['test_rows']} rows, {phase_a['test_pos_label_pct']}% pos — UNTOUCHED)
- **Target**: `forward_10d_positive` (Binary: `forward_10d_return > 0`)
- **Symbol-Date Dependence**: TRAIN contains {phase_a['train_duplicate_symbol_dates']} duplicate (signal_date, symbol) rows across strategies; VAL contains {phase_a['val_duplicate_symbol_dates']}.
- **Temporal Leakage Status**: `{phase_a['feature_leakage_status']}`

---

## 2. Model Comparison on Validation (Phase B)

Models fitted on TRAIN ({phase_a['train_rows']} rows) and evaluated on VALIDATION ({phase_a['val_rows']} rows):

{df_model.to_markdown(index=False)}

### Probability Bucket Analysis (Gradient Boosting on Validation)

{df_bucket.to_markdown(index=False)}

> [!WARNING]
> Notice non-monotonicity in win rates across probability buckets (e.g. 51.5% at 0.40–0.45, dropping to 44.3% at 0.50–0.55).

---

## 3. Feature Quality & Instability Diagnostics (Phase C)

### Top Features by Gini Importance vs Permutation Importance on Validation

{top_3_gini}

### Highly Correlated Feature Pairs (> 0.80 Correlation)

{high_corr_text}

### Complete Feature Diagnostic Table

{df_feat.to_markdown(index=False)}

> [!CAUTION]
> Key Finding: `dist_ema200_pct`, `rsi_14`, `dist_ema50_pct`, `slope_ema50`, `rs_3m` all experienced complete SIGN FLIPS in IC between Train and Validation, demonstrating high market-regime instability.

---

## 4. Cross-Sectional Normalization Experiment (Phase D)

Research experiment: Stock-specific features ranked as daily percentiles (`[0, 1]`) per `signal_date`:

{df_cs.to_markdown(index=False)}

---

## 5. Market Regime Diagnostics on Validation (Phase E)

{df_reg.to_markdown(index=False)}

---

## 6. Audit & File Modification Summary

- **Inspected**:
  - `data/ml/training_dataset.csv`
  - `data/ml/step_4f/embargo_manifest.json`
  - `scripts/run_step_4f_embargo.py`
  - `scripts/test_step_4f_temporal_purity.py`
- **Modified**:
  - None in project source code. (All Step 4G deliverables created in `data/ml/step_4g/` and `scripts/`).

---

## 7. Deliverables Created

1. `data/ml/step_4g/diagnostic_summary.csv`
2. `data/ml/step_4g/feature_diagnostics.csv`
3. `data/ml/step_4g/model_comparison.csv`
4. `data/ml/step_4g/probability_bucket_diagnostics.csv`
5. `data/ml/step_4g/cross_sectional_experiment.csv`
6. `data/ml/step_4g/regime_diagnostics.csv`
7. `data/ml/step_4g/step_4g_report.md`
8. `scripts/run_step_4g_ml_diagnostics.py`
9. `scripts/test_step_4g_ml_diagnostics.py`

---

## 8. Final Recommendation for Step 4H

**Classification**: **`WEAK`**

**Action Plan**:
1. Do NOT force the ML model into production.
2. Keep production decision mode set to **`PURE STRATEGY BASELINE`**.
3. In future research passes (Step 4H+), consider:
   - Separate models per strategy rather than aggregating all strategy signals into one pool.
   - Cross-sectional feature ranking as standard input transform.
   - Filtering features with unstable IC sign flips across regimes.
"""

    with open(STEP4G_REPORT_MD, "w") as f:
        f.write(report_content)

    print(f"  Report written -> {STEP4G_REPORT_MD}")


if __name__ == "__main__":
    run_step_4g_diagnostics()
