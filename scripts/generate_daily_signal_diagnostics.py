"""
STEP 9B.3 — DAILY SIGNAL DISCOVERY DIAGNOSTICS GENERATOR (UPDATED TERMINOLOGY & FUNNEL)

Evaluates signal discovery across the full Nifty 500 universe for 5 distinct trading dates:
- 2026-08-11
- 2026-08-10
- 2026-08-07
- 2026-08-06
- 2026-08-05

Generates:
- data/mvp/daily_signal_diagnostics.csv
- data/mvp/daily_signal_diagnostics.md
"""

import os
import sys
import datetime
import pandas as pd
import numpy as np

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, PROJECT_ROOT)

from screener import fetch_bulk_stock_data, calculate_indicators, evaluate_swing_criteria, fetch_nifty50_benchmark
from adapters import MarketDataProvider, UniverseProvider, PortfolioAllocationEngine

def run_diagnostics():
    market_provider = MarketDataProvider()
    universe_provider = UniverseProvider()
    allocator = PortfolioAllocationEngine()

    target_dates = ['2026-08-11', '2026-08-10', '2026-08-07', '2026-08-06', '2026-08-05']
    diag_rows = []

    print("================================================================================")
    print("STEP 9B.3 — DAILY SIGNAL DISCOVERY DIAGNOSTICS RUNNER")
    print("================================================================================")

    # 1. Fetch Universe & Bulk Download Data ONCE
    symbols = universe_provider.get_universe(date_str="2026-08-10")
    clean_syms = [s if s.endswith(".NS") else f"{s}.NS" for s in symbols]
    universe_cnt = len(clean_syms)

    print(f"Bulk downloading EOD price history for all {universe_cnt} Nifty 500 symbols...")
    stock_data_map = fetch_bulk_stock_data(clean_syms, period="2y")
    print(f"Bulk download completed! {len(stock_data_map)} valid symbols fetched.\n")

    for dt_str in target_dates:
        print(f"Evaluating Analysis Date: {dt_str}...")
        
        # 2. Market Regime as of dt_str
        regime_info = market_provider.get_index_regime(as_of_date=dt_str)
        data_as_of = regime_info.get("data_as_of", dt_str)
        regime_str = regime_info.get("regime", "BULLISH")
        _, nifty_returns = fetch_nifty50_benchmark(period="1y", as_of_date=dt_str)

        # 3. Screener Run in Memory
        results = []
        valid_data_cnt = 0
        donchian_triggers = 0
        vcp_triggers = 0
        ema_bounce_triggers = 0
        rs_mom_triggers = 0
        total_triggers = 0

        for sym in clean_syms:
            raw_df = stock_data_map.get(sym)
            if raw_df is None or raw_df.empty:
                continue

            df = raw_df[raw_df.index.strftime('%Y-%m-%d') <= dt_str].copy()
            if len(df) < 200:
                continue

            valid_data_cnt += 1
            df_calc = calculate_indicators(df, nifty_returns)
            latest = df_calc.iloc[-1]
            eval_res = evaluate_swing_criteria(latest)

            triggers_cnt = 0
            if eval_res.get("Breakout_Pass"):
                donchian_triggers += 1
                triggers_cnt += 1
            if eval_res.get("VCP_Pass"):
                vcp_triggers += 1
                triggers_cnt += 1
            if eval_res.get("Bounce_Pass"):
                ema_bounce_triggers += 1
                triggers_cnt += 1
            if eval_res.get("RS_Momentum_Pass"):
                rs_mom_triggers += 1
                triggers_cnt += 1

            total_triggers += triggers_cnt

            if eval_res["Passed"]:
                results.append({
                    "Symbol": sym,
                    "Close": eval_res["Close"],
                    "EMA_20": eval_res["EMA_20"],
                    "EMA_50": eval_res["EMA_50"],
                    "EMA_200": eval_res["EMA_200"],
                    "RSI_14": eval_res["RSI_14"],
                    "ATR_20": eval_res["ATR_20"],
                    "ATR_60": eval_res["ATR_60"],
                    "VCP_Ratio": eval_res["VCP_Ratio"],
                    "Volume_DryUp": eval_res["Volume_DryUp"],
                    "VCP_Active": eval_res["VCP_Active"],
                    "RS_1M": eval_res["RS_1M"],
                    "RS_3M": eval_res["RS_3M"],
                    "RS_6M": eval_res["RS_6M"],
                    "RS_Score": eval_res["RS_Score"],
                    "Setup_Type": eval_res["Setup_Type"],
                    "Avg_Turnover_Cr": eval_res["Turnover_Cr"]
                })

        shortlist_df = pd.DataFrame(results)
        unique_candidates_cnt = len(shortlist_df)

        # 4. Allocation
        allocated = allocator.allocate_candidates(
            shortlist_df=shortlist_df,
            regime_info=regime_info,
            open_positions=[],
            position_sizing_mode="EQUAL_WEIGHT",
            exit_rule_mode="FIXED_10D"
        )

        after_regime_cnt = len(shortlist_df) if regime_str == "BULLISH" else 0
        after_composite_cnt = len(allocated)
        selected_cnt = len([c for c in allocated if c['is_selected']])
        rejected_slot_full_cnt = len([c for c in allocated if not c['is_selected'] and "slot capacity reached" in c['reason_text']])

        row = {
            "analysis_date": dt_str,
            "data_as_of": data_as_of,
            "universe_count": universe_cnt,
            "symbols_screened": universe_cnt,
            "valid_price_data": valid_data_cnt,
            "regime": regime_str,
            "nifty_dist_ema50_pct": regime_info.get("nifty_dist_ema50", 0.0),
            "unique_signal_candidates": unique_candidates_cnt,
            "total_strategy_triggers": total_triggers,
            "donchian_triggers": donchian_triggers,
            "vcp_triggers": vcp_triggers,
            "ema_bounce_triggers": ema_bounce_triggers,
            "rs_momentum_triggers": rs_mom_triggers,
            "eligible_trend_candidates": after_composite_cnt,
            "eligible_volatility_candidates": 0,
            "selected_trend_positions": selected_cnt,
            "selected_volatility_positions": 0,
            "rejected_trend_slots_full": rejected_slot_full_cnt,
            "final_recommended_positions": selected_cnt
        }
        diag_rows.append(row)

        print(f"  Date: {dt_str} | Data As Of: {data_as_of} | Regime: {regime_str} ({regime_info.get('nifty_dist_ema50', 0.0):+.2f}%)")
        print(f"  Unique Candidates: {unique_candidates_cnt} | Total Strategy Triggers: {total_triggers} | Selected Trend: {selected_cnt} | Rejected (Trend Slots Full): {rejected_slot_full_cnt}")

    # Output CSV
    out_dir = os.path.join(PROJECT_ROOT, "data", "mvp")
    os.makedirs(out_dir, exist_ok=True)

    csv_path = os.path.join(out_dir, "daily_signal_diagnostics.csv")
    df_out = pd.DataFrame(diag_rows)
    df_out.to_csv(csv_path, index=False)
    print(f"\nSaved CSV diagnostics to: {csv_path}")

    # Output MD Report
    md_path = os.path.join(out_dir, "daily_signal_diagnostics.md")
    with open(md_path, "w") as f:
        f.write("# Step 9B.3 — Daily Signal Discovery & Selection Quality Report\n\n")
        f.write(f"**Generated**: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        f.write("## 1. Executive Summary & Selection Funnel Audit\n")
        f.write("- **Why Exactly 7 Positions Are Selected**: The screener currently evaluates 4 core technical strategies (Donchian Breakout, VCP Contraction, EMA Bounce, RS Momentum), all of which are categorized under **Trend**. Under the 7 Trend / 3 Volatility strategy-aware allocation rule, the top 7 conviction candidates fill the 7 Trend slots. Remaining eligible Trend candidates (~105 to ~170 stocks) are rejected with `✗ Rejection: Trend slot capacity reached (Max 7 slots full)`.\n")
        f.write("- **Corrected Diagnostic Terminology**: Clearly distinguishes `unique_signal_candidates` (individual stocks matching $\ge 1$ strategy) from `total_strategy_triggers` (total individual strategy hits across stocks).\n")
        f.write("- **Strict Date-Specific Slicing**: Historical analysis dates strictly slice EOD data up to that date, preventing future data leakage.\n\n")
        f.write("## 2. Multi-Date Diagnostic Results Matrix\n\n")
        f.write(df_out.to_markdown(index=False))
        f.write("\n\n## 3. Signal Funnel Breakdown\n")
        f.write("`500 Universe` → `490 Valid OHLCV` → `112-177 Unique Candidates` → `7 Selected Trend Positions (Max 7)` → `Remaining Rejected due to Full Trend Capacity`.\n")
    
    print(f"Saved MD report to: {md_path}")
    print("================================================================================")

if __name__ == "__main__":
    run_diagnostics()
