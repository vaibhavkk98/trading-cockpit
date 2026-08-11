import os
import pandas as pd
import datetime

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
ML_DIR = os.path.join(PROJECT_ROOT, "data", "ml")
DATASET_CSV = os.path.join(ML_DIR, "training_dataset.csv")
MANIFEST_CSV = os.path.join(ML_DIR, "authoritative_dataset_manifest.csv")


def create_authoritative_manifest():
    if not os.path.exists(DATASET_CSV):
        raise FileNotFoundError(f"Missing dataset at {DATASET_CSV}")

    df = pd.read_csv(DATASET_CSV)
    df['signal_dt'] = pd.to_datetime(df['signal_date'])
    df_sorted = df.sort_values(by="signal_dt").reset_index(drop=True)

    n_tot = len(df_sorted)
    tr_df = df_sorted[df_sorted['signal_date'] < "2025-10-15"]
    va_df = df_sorted[(df_sorted['signal_date'] >= "2025-10-15") & (df_sorted['signal_date'] < "2026-02-18")]
    te_df = df_sorted[df_sorted['signal_date'] >= "2026-02-18"]

    manifest_data = [{
        "dataset_path": "data/ml/training_dataset.csv",
        "dataset_row_count": n_tot,
        "unique_security_count": df['symbol'].nunique(),
        "strategy_count": df['strategy_name'].nunique(),
        "feature_count": 21,
        "start_date": df_sorted['signal_date'].min(),
        "end_date": df_sorted['signal_date'].max(),
        "train_start": tr_df['signal_date'].min(),
        "train_end": tr_df['signal_date'].max(),
        "validation_start": va_df['signal_date'].min(),
        "validation_end": va_df['signal_date'].max(),
        "test_start": te_df['signal_date'].min(),
        "test_end": te_df['signal_date'].max(),
        "dataset_generation_timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "random_seed": 42,
        "universe_selection_date": "2024-04-01",
        "universe_evidence_status": "EVENT_RECONSTRUCTED",
        "survivorship_bias_classification": "MEDIUM"
    }]

    df_manifest = pd.DataFrame(manifest_data)
    df_manifest.to_csv(MANIFEST_CSV, index=False)
    print(f"Authoritative Dataset Manifest created -> {MANIFEST_CSV}")


if __name__ == "__main__":
    create_authoritative_manifest()
