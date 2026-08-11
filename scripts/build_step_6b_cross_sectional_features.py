"""
STEP 6B — CROSS-SECTIONAL FEATURE ENGINEERING PIPELINE

Generates point-in-time cross-sectional percentile ranked features for continuous SIGNAL_FEATURE columns.
Operates strictly per signal_date. Never leaks future information.

Outputs:
- data/ml/step_6/cross_sectional_feature_manifest.csv
- data/ml/step_6/cross_sectional_sample_size_audit.csv
"""
import os
import sys
import pandas as pd
import numpy as np

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, PROJECT_ROOT)

ML_DIR = os.path.join(PROJECT_ROOT, "data", "ml")
STEP6_DIR = os.path.join(ML_DIR, "step_6")
EXPANDED_DATASET_CSV = os.path.join(STEP6_DIR, "expanded_strategy_dataset.csv")

SAMPLE_SIZE_AUDIT_CSV = os.path.join(STEP6_DIR, "cross_sectional_sample_size_audit.csv")
FEATURE_MANIFEST_CSV = os.path.join(STEP6_DIR, "cross_sectional_feature_manifest.csv")


def run_cross_sectional_feature_engineering():
    print("=" * 80)
    print("STEP 6B — CROSS-SECTIONAL FEATURE ENGINEERING & SAMPLE SIZE AUDIT")
    print("=" * 80)

    os.makedirs(STEP6_DIR, exist_ok=True)
    df = pd.read_csv(EXPANDED_DATASET_CSV)
    print(f"  Loaded Authoritative Dataset: {len(df)} rows across {df['signal_date'].nunique()} unique dates.")

    # Phase 1: Sample Size Audit
    date_stats = []
    for dt, group in df.groupby('signal_date'):
        total_sig = len(group)
        uniq_sym = group['symbol'].nunique()
        non_null_rsi = group['rsi_14'].notna().sum()
        non_null_crsi = group['crsi'].notna().sum()
        non_null_rs = group['rs_3m'].notna().sum()

        date_stats.append({
            "signal_date": dt,
            "total_signals": total_sig,
            "unique_symbols": uniq_sym,
            "non_null_rsi_14": non_null_rsi,
            "non_null_crsi": non_null_crsi,
            "non_null_rs_3m": non_null_rs
        })

    df_sample = pd.DataFrame(date_stats)
    df_sample.to_csv(SAMPLE_SIZE_AUDIT_CSV, index=False)
    print(f"  Sample Size Audit Saved -> {SAMPLE_SIZE_AUDIT_CSV}")

    sym_counts = df_sample['unique_symbols']
    print(f"  - Total Signal Dates        : {len(df_sample)}")
    print(f"  - Median Unique Symbols/Date: {sym_counts.median()}")
    print(f"  - 25th Percentile           : {sym_counts.quantile(0.25)}")
    print(f"  - 10th Percentile           : {sym_counts.quantile(0.10)}")
    print(f"  - Dates >= 20 Symbols       : {(sym_counts >= 20).mean() * 100:.1f}%")

    # Phase 2: Feature Engineering & Manifest
    candidate_features = [
        'ret_5d', 'ret_10d', 'ret_20d', 'ret_50d',
        'dist_ema20_pct', 'dist_ema50_pct', 'dist_ema200_pct',
        'slope_ema20', 'slope_ema50', 'rsi_14', 'rs_3m',
        'atr_20_pct', 'vol_20d', 'vcp_ratio', 'volume_ratio_20d',
        'turnover_20d', 'crsi', 'rsi_3', 'streak_rsi_2', 'roc_100'
    ]

    manifest_rows = []
    for col in candidate_features:
        cs_col = f"{col}_cs_rank"
        df[cs_col] = df.groupby('signal_date')[col].rank(pct=True)

        manifest_rows.append({
            "raw_feature_name": col,
            "cross_sectional_feature_name": cs_col,
            "ranking_group": "signal_date",
            "ranking_method": "pct_rank_descending",
            "pit_safety_status": "PIT_SAFE_DATE_T_CLOSE",
            "non_null_pct": round(df[cs_col].notna().mean() * 100.0, 2)
        })

    df_manifest = pd.DataFrame(manifest_rows)
    df_manifest.to_csv(FEATURE_MANIFEST_CSV, index=False)
    print(f"  Feature Manifest Saved -> {FEATURE_MANIFEST_CSV}")

    # Re-save dataset with computed CS rank columns
    df.to_csv(EXPANDED_DATASET_CSV, index=False)
    print(f"  Updated Dataset with CS Ranks -> {EXPANDED_DATASET_CSV}")

    return df_sample, df_manifest


if __name__ == "__main__":
    run_cross_sectional_feature_engineering()
