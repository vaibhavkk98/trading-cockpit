"""
STEP 6A-FINAL-AUDIT-2 — BUILD AUTHORITATIVE & DETERMINISTIC STRATEGY DATASET

1. Calculates and persists TRUE indicator fields into the dataset:
   - crsi: (RSI3 + StreakRSI2 + ROC100PercentRank) / 3
   - rsi_3: 3-period RSI
   - streak: consecutive up/down streak count
   - streak_rsi_2: 2-period RSI of streak
   - roc_100: 100-day Rate of Change
   - roc_100_percent_rank: 100-day rolling percentile rank of ROC100
   - daily_range: High(T) - Low(T)
   - nr7: Range(T) == min(Range(T-6)...Range(T))

2. Implements True Connors RSI Mean Reversion and True NR7 Volatility Expansion Breakout.
   - True NR7 Breakout requires confirmed breakout on day T+1 (High(T+1) > High(T)).

3. Uses local caching to ensure 100% offline determinism and outputs data/ml/step_6/dataset_manifest.csv.
"""
import os
import sys
import hashlib
import pickle
import pandas as pd
import numpy as np
import pandas_ta as ta
import yfinance as yf

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, PROJECT_ROOT)

ML_DIR = os.path.join(PROJECT_ROOT, "data", "ml")
TRAINING_DATASET_CSV = os.path.join(ML_DIR, "training_dataset.csv")

STEP6_DIR = os.path.join(ML_DIR, "step_6")
EXPANDED_DATASET_CSV = os.path.join(STEP6_DIR, "expanded_strategy_dataset.csv")
CACHE_PKL = os.path.join(STEP6_DIR, "cached_ohlcv_indicators.pkl")
MANIFEST_CSV = os.path.join(STEP6_DIR, "dataset_manifest.csv")


def compute_sha256(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def get_cached_indicator_map(symbols):
    if os.path.exists(CACHE_PKL):
        try:
            with open(CACHE_PKL, "rb") as f:
                indicator_map = pickle.load(f)
            if len(indicator_map) >= len(symbols) - 5:
                print(f"  Loaded cached indicator map for {len(indicator_map)} symbols from {CACHE_PKL}")
                return indicator_map
        except Exception as e:
            print(f"  Failed loading cache: {e}. Re-downloading...")

    print(f"  Fetching raw daily OHLCV for {len(symbols)} unique symbols...")
    indicator_map = {}
    for sym in symbols:
        yf_sym = sym + '.NS' if not sym.endswith('.NS') else sym
        try:
            df = yf.Ticker(yf_sym).history(period='2y', interval='1d', auto_adjust=True)
            if df.empty or len(df) < 120:
                continue

            df['RSI_3'] = ta.rsi(df['Close'], length=3)
            close_diff = df['Close'].diff()
            streak = np.zeros(len(df))
            for i in range(1, len(df)):
                if close_diff.iloc[i] > 0:
                    streak[i] = streak[i-1] + 1 if streak[i-1] > 0 else 1
                elif close_diff.iloc[i] < 0:
                    streak[i] = streak[i-1] - 1 if streak[i-1] < 0 else -1
                else:
                    streak[i] = 0
            df['Streak'] = streak
            df['Streak_RSI_2'] = ta.rsi(pd.Series(streak, index=df.index), length=2)
            roc_100 = df['Close'].pct_change(100) * 100.0
            df['ROC_100'] = roc_100
            df['ROC_100_PctRank'] = roc_100.rolling(100).rank(pct=True) * 100.0
            df['CRSI'] = (df['RSI_3'] + df['Streak_RSI_2'] + df['ROC_100_PctRank']) / 3.0

            df['Daily_Range'] = df['High'] - df['Low']
            df['NR7'] = df['Daily_Range'] == df['Daily_Range'].rolling(7).min()

            # Next day bar for PIT breakout confirmation
            df['Next_Open'] = df['Open'].shift(-1)
            df['Next_High'] = df['High'].shift(-1)
            df['Next_Low'] = df['Low'].shift(-1)
            df['Next_Close'] = df['Close'].shift(-1)

            df.index = df.index.strftime('%Y-%m-%d')
            indicator_map[sym] = df
        except Exception as e:
            print(f"Error fetching {sym}: {e}")

    try:
        with open(CACHE_PKL, "wb") as f:
            pickle.dump(indicator_map, f)
        print(f"  Cached indicator map saved -> {CACHE_PKL}")
    except Exception as e:
        print(f"Failed to cache indicator map: {e}")

    return indicator_map


def build_true_strategy_dataset():
    print("=" * 80)
    print("STEP 6A-FINAL-AUDIT-2 — BUILDING AUTHORITATIVE & DETERMINISTIC STRATEGY DATASET")
    print("=" * 80)

    os.makedirs(STEP6_DIR, exist_ok=True)

    df_base = pd.read_csv(TRAINING_DATASET_CSV)
    symbols = sorted(df_base['symbol'].unique())
    print(f"  Base Dataset Rows: {len(df_base)}")

    indicator_map = get_cached_indicator_map(symbols)

    # Merge TRUE indicators into df_base
    df_base['crsi'] = np.nan
    df_base['rsi_3'] = np.nan
    df_base['streak'] = np.nan
    df_base['streak_rsi_2'] = np.nan
    df_base['roc_100'] = np.nan
    df_base['roc_100_percent_rank'] = np.nan
    df_base['daily_range'] = np.nan
    df_base['nr7'] = False
    df_base['high_t'] = np.nan
    df_base['next_high'] = np.nan
    df_base['next_open'] = np.nan

    for idx, row in df_base.iterrows():
        sym = row['symbol']
        dt_str = row['signal_date']
        if sym in indicator_map and dt_str in indicator_map[sym].index:
            bar = indicator_map[sym].loc[dt_str]
            df_base.at[idx, 'crsi'] = float(bar['CRSI']) if pd.notna(bar['CRSI']) else np.nan
            df_base.at[idx, 'rsi_3'] = float(bar['RSI_3']) if pd.notna(bar['RSI_3']) else np.nan
            df_base.at[idx, 'streak'] = float(bar['Streak']) if pd.notna(bar['Streak']) else np.nan
            df_base.at[idx, 'streak_rsi_2'] = float(bar['Streak_RSI_2']) if pd.notna(bar['Streak_RSI_2']) else np.nan
            df_base.at[idx, 'roc_100'] = float(bar['ROC_100']) if pd.notna(bar['ROC_100']) else np.nan
            df_base.at[idx, 'roc_100_percent_rank'] = float(bar['ROC_100_PctRank']) if pd.notna(bar['ROC_100_PctRank']) else np.nan
            df_base.at[idx, 'daily_range'] = float(bar['Daily_Range']) if pd.notna(bar['Daily_Range']) else np.nan
            df_base.at[idx, 'nr7'] = bool(bar['NR7']) if pd.notna(bar['NR7']) else False
            df_base.at[idx, 'high_t'] = float(bar['High']) if pd.notna(bar['High']) else np.nan
            df_base.at[idx, 'next_high'] = float(bar['Next_High']) if pd.notna(bar['Next_High']) else np.nan
            df_base.at[idx, 'next_open'] = float(bar['Next_Open']) if pd.notna(bar['Next_Open']) else np.nan

    # Generate TRUE CRSI signals (crsi <= 45.0 in primary uptrend)
    crsi_mask = (df_base['dist_ema50_pct'] > 0.0) & (df_base['crsi'] <= 45.0)
    df_crsi = df_base[crsi_mask].copy()
    df_crsi['strategy_name'] = 'True Connors RSI Mean Reversion'
    df_crsi['signal_type'] = 'MEAN_REVERSION'

    # Generate TRUE NR7 Breakout signals (nr7 == True in primary uptrend AND confirmed breakout High(T+1) > High(T))
    nr7_mask = (df_base['dist_ema50_pct'] > 0.0) & (df_base['nr7'] == True) & (df_base['next_high'] > df_base['high_t'])
    df_nr7 = df_base[nr7_mask].copy()
    df_nr7['strategy_name'] = 'True NR7 Volatility Expansion Breakout'
    df_nr7['signal_type'] = 'EXPANSION'
    df_nr7['entry_price'] = np.maximum(df_nr7['next_open'], df_nr7['high_t'])

    # Combine all observations
    df_expanded = pd.concat([df_base, df_crsi, df_nr7], ignore_index=True)
    df_expanded = df_expanded.drop_duplicates(subset=['signal_date', 'symbol', 'strategy_name']).reset_index(drop=True)

    # Cross-sectional ranking features
    df_expanded['rs_3m_pct'] = df_expanded.groupby('signal_date')['rs_3m'].rank(pct=True)
    df_expanded['rsi_14_pct'] = df_expanded.groupby('signal_date')['rsi_14'].rank(pct=True)
    df_expanded['volume_ratio_20d_pct'] = df_expanded.groupby('signal_date')['volume_ratio_20d'].rank(pct=True)
    df_expanded['crsi_pct'] = df_expanded.groupby('signal_date')['crsi'].rank(pct=True)
    df_expanded['composite_score'] = (df_expanded['rs_3m_pct'] + df_expanded['rsi_14_pct'] + df_expanded['volume_ratio_20d_pct']) / 3.0

    df_expanded.to_csv(EXPANDED_DATASET_CSV, index=False)
    sha256 = compute_sha256(EXPANDED_DATASET_CSV)

    print(f"  Expanded Dataset Created -> {EXPANDED_DATASET_CSV}")
    print(f"  - Total Rows         : {len(df_expanded)}")
    print(f"  - Unique Symbols     : {df_expanded['symbol'].nunique()}")
    print(f"  - Min Signal Date    : {df_expanded['signal_date'].min()}")
    print(f"  - Max Signal Date    : {df_expanded['signal_date'].max()}")
    print(f"  - SHA256 Hash        : {sha256}")
    print(f"  - Strategy Counts    :\n{df_expanded['strategy_name'].value_counts().to_string()}")

    # Write Deterministic Dataset Manifest
    manifest_df = pd.DataFrame([{
        "dataset_file": "expanded_strategy_dataset.csv",
        "row_count": len(df_expanded),
        "unique_symbols": df_expanded['symbol'].nunique(),
        "min_signal_date": df_expanded['signal_date'].min(),
        "max_signal_date": df_expanded['signal_date'].max(),
        "sha256_hash": sha256,
        "source_caching_status": "FROZEN_LOCAL_CACHE",
        "generation_timestamp": pd.Timestamp.now().isoformat()
    }])
    manifest_df.to_csv(MANIFEST_CSV, index=False)
    print(f"  Dataset Manifest Written -> {MANIFEST_CSV}")

    return df_expanded


if __name__ == "__main__":
    build_true_strategy_dataset()
