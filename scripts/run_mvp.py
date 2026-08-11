"""
STEP 8 — TRADING SYSTEM MVP RUNNER

End-to-end MVP pipeline integrating all validated components:
- 6 strategy implementations from the expanded dataset
- Champion allocation: 7 Trend / 3 Volatility (frozen Step 7C.3)
- Validated single-portfolio simulator with causal NR7 execution
- Indian market transaction cost model
- Market regime filter (Nifty 50 vs EMA50)
- ML: OFF | Sentiment: DISABLED

Usage:
    PYTHONPATH=. ./venv/bin/python scripts/run_mvp.py
"""
import os
import sys
import json
import hashlib
import pickle
import datetime
import pandas as pd
import numpy as np

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, PROJECT_ROOT)


# ─────── Config ───────
def load_mvp_config():
    try:
        import yaml
        config_path = os.path.join(PROJECT_ROOT, "config", "mvp_config.yaml")
        with open(config_path, "r") as f:
            return yaml.safe_load(f)
    except ImportError:
        # Fallback: parse YAML manually for simple flat configs
        config_path = os.path.join(PROJECT_ROOT, "config", "mvp_config.yaml")
        import json as _json
        # Use a simple YAML loader
        try:
            from yaml import safe_load
            with open(config_path, "r") as f:
                return safe_load(f)
        except Exception:
            raise ImportError("PyYAML is required. Install with: pip install pyyaml")


def compute_sha256(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


# ─────── Causal NR7 Dataset Builder ───────
def build_causal_nr7_dataset(df_exp, cache_map):
    """
    Builds causal Model A NR7 dataset.
    At T close: NR7 setup identified, stop-buy placed at High(T).
    At T+1: Gap fill (Open >= High(T) -> entry at Open) or
            Intraday fill (High(T+1) >= High(T) -> entry at High(T)).
    """
    nr7_setups = df_exp[(df_exp['nr7'] == True) & (df_exp['dist_ema50_pct'] > 0.0)].copy()

    model_a_rows = []
    for idx, row in nr7_setups.iterrows():
        sym = row['symbol']
        dt = row['signal_date']
        if sym in cache_map and dt in cache_map[sym].index:
            df_bar = cache_map[sym]
            i = df_bar.index.get_loc(dt)
            high_t = float(df_bar.iloc[i]['High'])

            if i + 1 < len(df_bar):
                bar_t1 = df_bar.iloc[i + 1]
                open_t1 = float(bar_t1['Open'])
                high_t1 = float(bar_t1['High'])
                if high_t1 >= high_t:
                    is_gap = open_t1 >= high_t
                    entry_px = open_t1 if is_gap else high_t
                    r = row.to_dict()
                    r['strategy_name'] = 'True NR7 Volatility Expansion Breakout'
                    r['entry_price'] = entry_px
                    if i + 10 < len(df_bar):
                        close_t10 = float(df_bar.iloc[i + 10]['Close'])
                        r['forward_10d_return'] = ((close_t10 - entry_px) / entry_px) * 100.0
                    model_a_rows.append(r)

    df_other = df_exp[df_exp['strategy_name'] != 'True NR7 Volatility Expansion Breakout'].copy()
    df_nr7_causal = pd.DataFrame(model_a_rows)
    df_all_causal = pd.concat([df_other, df_nr7_causal], ignore_index=True)
    return df_all_causal


# ─────── Main MVP Pipeline ───────
def run_mvp():
    start_time = datetime.datetime.now()
    print("=" * 80)
    print("STEP 8 — TRADING SYSTEM MVP")
    print("=" * 80)
    print(f"Started: {start_time.isoformat()}")
    print()

    # 1. Load Config
    print("[1/10] Loading MVP configuration...")
    config = load_mvp_config()
    assert config['ml']['enabled'] == False, "SAFETY: ML must be OFF"
    assert config['sentiment']['enabled'] == False, "SAFETY: Sentiment must be DISABLED"
    assert config['regime']['enabled'] == True, "SAFETY: Regime filter must be ON"
    print(f"       ML: {config['ml']['status']}")
    print(f"       Sentiment: {config['sentiment']['status']}")
    print(f"       Regime Filter: ENABLED")
    print(f"       Allocation: {config['portfolio']['max_trend_positions']} Trend / {config['portfolio']['max_volatility_positions']} Volatility")
    print(f"       Initial Capital: ₹{config['backtest']['initial_capital']:,.0f}")
    print()

    # 2. Load Data
    print("[2/10] Loading expanded strategy dataset...")
    dataset_path = os.path.join(PROJECT_ROOT, config['backtest']['data_source'])
    cache_path = os.path.join(PROJECT_ROOT, config['backtest']['ohlcv_cache'])

    df_exp = pd.read_csv(dataset_path)
    dataset_sha = compute_sha256(dataset_path)
    print(f"       Dataset: {len(df_exp)} observations")
    print(f"       SHA256: {dataset_sha[:16]}...")

    with open(cache_path, "rb") as f:
        cache_map = pickle.load(f)
    print(f"       OHLCV Cache: {len(cache_map)} symbols")
    print()

    # 3. Build Causal NR7 Dataset
    print("[3/10] Building causal NR7 dataset (Model A stop-buy execution)...")
    df_all_causal = build_causal_nr7_dataset(df_exp, cache_map)
    nr7_count = len(df_all_causal[df_all_causal['strategy_name'] == 'True NR7 Volatility Expansion Breakout'])
    other_count = len(df_all_causal[df_all_causal['strategy_name'] != 'True NR7 Volatility Expansion Breakout'])
    print(f"       NR7 causal setups: {nr7_count}")
    print(f"       Other strategy signals: {other_count}")
    print(f"       Total causal dataset: {len(df_all_causal)}")
    print()

    # 4. Apply Embargo Splits
    print("[4/10] Applying embargo splits...")
    from scripts.run_step_4f_embargo import apply_embargo
    emb = apply_embargo(df_all_causal, 10)
    val_df = emb['val'].copy()
    test_df = emb['test'].copy()
    val_date_min = str(val_df['signal_date'].min())[:10]
    val_date_max = str(val_df['signal_date'].max())[:10]
    test_date_min = str(test_df['signal_date'].min())[:10]
    test_date_max = str(test_df['signal_date'].max())[:10]
    print(f"       Validation: {len(val_df)} signals ({val_date_min} to {val_date_max})")
    print(f"       Test: {len(test_df)} signals ({test_date_min} to {test_date_max})")
    print()

    # 5. Run Champion Simulator
    print("[5/10] Running champion simulator (7 Trend / 3 Volatility)...")
    from scripts.run_step_7c3_global_baseline import simulate_single_portfolio_global

    initial_capital = float(config['backtest']['initial_capital'])
    pos_size = float(config['portfolio']['position_sizing']['per_position_capital'])
    max_positions = config['portfolio']['max_positions']
    max_trend = config['portfolio']['max_trend_positions']
    max_vol = config['portfolio']['max_volatility_positions']
    cost_mult = config['execution']['cost_multiplier']

    print("       Running VALIDATION split...")
    res_val = simulate_single_portfolio_global(
        val_df, cache_map,
        is_bucket_model=True, max_trend=max_trend, max_vol=max_vol,
        total_max=max_positions, initial_capital=initial_capital,
        pos_size=pos_size, cost_mult=cost_mult, regime_filter=True
    )
    print(f"       VALIDATION: {res_val['net_portfolio_return_pct']:+.2f}% | Sharpe {res_val['daily_sharpe_ratio']:.2f} | DD {res_val['max_drawdown_pct']:.2f}% | {res_val['executed_positions']} trades")

    print("       Running TEST split (descriptive only)...")
    res_test = simulate_single_portfolio_global(
        test_df, cache_map,
        is_bucket_model=True, max_trend=max_trend, max_vol=max_vol,
        total_max=max_positions, initial_capital=initial_capital,
        pos_size=pos_size, cost_mult=cost_mult, regime_filter=True
    )
    print(f"       TEST:       {res_test['net_portfolio_return_pct']:+.2f}% | Sharpe {res_test['daily_sharpe_ratio']:.2f} | DD {res_test['max_drawdown_pct']:.2f}% | {res_test['executed_positions']} trades")
    print()

    # 6. Create Output Directory
    output_dir = os.path.join(PROJECT_ROOT, config['output']['base_dir'])
    os.makedirs(output_dir, exist_ok=True)

    # 7. Save Outputs
    print("[6/10] Saving MVP outputs...")

    # Trade Ledger
    trade_ledger_path = os.path.join(output_dir, config['output']['trade_ledger'])
    df_trades_val = res_val['trade_log'].copy()
    df_trades_val['split'] = 'VALIDATION'
    df_trades_test = res_test['trade_log'].copy()
    df_trades_test['split'] = 'TEST'
    df_all_trades = pd.concat([df_trades_val, df_trades_test], ignore_index=True)
    df_all_trades.to_csv(trade_ledger_path, index=False)
    print(f"       Trade Ledger: {trade_ledger_path} ({len(df_all_trades)} trades)")

    # Equity Curve
    equity_curve_path = os.path.join(output_dir, config['output']['equity_curve'])
    df_eq_val = res_val['df_daily'].copy()
    df_eq_val['split'] = 'VALIDATION'
    df_eq_test = res_test['df_daily'].copy()
    df_eq_test['split'] = 'TEST'
    df_all_equity = pd.concat([df_eq_val, df_eq_test], ignore_index=True)
    df_all_equity.to_csv(equity_curve_path, index=False)
    print(f"       Equity Curve: {equity_curve_path} ({len(df_all_equity)} daily records)")

    # Daily Returns
    daily_returns_path = os.path.join(output_dir, config['output']['daily_returns'])
    daily_ret_rows = []
    for split_name, df_daily in [('VALIDATION', res_val['df_daily']), ('TEST', res_test['df_daily'])]:
        rets = df_daily['total_equity'].pct_change().dropna()
        for i, ret_val_pct in enumerate(rets.values):
            daily_ret_rows.append({
                'split': split_name,
                'date': df_daily.iloc[i + 1]['date'],
                'daily_return_pct': round(ret_val_pct * 100.0, 6),
                'cumulative_equity': df_daily.iloc[i + 1]['total_equity']
            })
    df_daily_returns = pd.DataFrame(daily_ret_rows)
    df_daily_returns.to_csv(daily_returns_path, index=False)
    print(f"       Daily Returns: {daily_returns_path} ({len(df_daily_returns)} days)")

    # Signals Log
    signals_log_path = os.path.join(output_dir, config['output']['signals_log'])
    sig_cols = ['signal_date', 'symbol', 'strategy_name', 'composite_score', 'forward_10d_return']
    sig_val = val_df[sig_cols].copy()
    sig_val['split'] = 'VALIDATION'
    sig_test = test_df[sig_cols].copy()
    sig_test['split'] = 'TEST'
    df_signals = pd.concat([sig_val, sig_test], ignore_index=True)
    df_signals.to_csv(signals_log_path, index=False)
    print(f"       Signals Log: {signals_log_path} ({len(df_signals)} signals)")

    # Performance Report
    perf_report_path = os.path.join(output_dir, config['output']['performance_report'])
    perf_report = {
        'generated_at': datetime.datetime.now().isoformat(),
        'config_version': 'MVP v1.0',
        'dataset_sha256': dataset_sha,
        'initial_capital': initial_capital,
        'allocation': f"{max_trend} Trend / {max_vol} Volatility",
        'ml_status': 'OFF',
        'sentiment_status': 'DISABLED',
        'regime_filter': 'ON',
        'validation': {
            'period': f"{val_date_min} to {val_date_max}",
            'net_return_pct': res_val['net_portfolio_return_pct'],
            'daily_sharpe_ratio': res_val['daily_sharpe_ratio'],
            'max_drawdown_pct': res_val['max_drawdown_pct'],
            'win_rate_pct': res_val['win_rate_pct'],
            'profit_factor': res_val['profit_factor'],
            'executed_trades': res_val['executed_positions'],
            'mean_trade_return_pct': res_val['mean_trade_return_pct'],
            'median_trade_return_pct': res_val['median_trade_return_pct'],
            'nr7_trades': res_val['nr7_trades'],
            'avg_open_positions': res_val['avg_open_positions']
        },
        'test_descriptive': {
            'period': f"{test_date_min} to {test_date_max}",
            'net_return_pct': res_test['net_portfolio_return_pct'],
            'daily_sharpe_ratio': res_test['daily_sharpe_ratio'],
            'max_drawdown_pct': res_test['max_drawdown_pct'],
            'win_rate_pct': res_test['win_rate_pct'],
            'profit_factor': res_test['profit_factor'],
            'executed_trades': res_test['executed_positions'],
            'mean_trade_return_pct': res_test['mean_trade_return_pct'],
            'median_trade_return_pct': res_test['median_trade_return_pct'],
            'nr7_trades': res_test['nr7_trades'],
            'avg_open_positions': res_test['avg_open_positions']
        }
    }
    with open(perf_report_path, 'w') as f:
        json.dump(perf_report, f, indent=2)
    print(f"       Performance Report: {perf_report_path}")
    print()

    # 8. Accounting Reconciliation
    print("[7/10] Running accounting reconciliation...")
    for split_name, res in [('VALIDATION', res_val), ('TEST', res_test)]:
        df_daily = res['df_daily']
        final_equity = df_daily['total_equity'].iloc[-1]
        final_cash = df_daily['cash'].iloc[-1]
        final_mtm = df_daily['mtm_pos_val'].iloc[-1]
        recon_gap = abs(final_equity - (final_cash + final_mtm))
        recon_ok = "PASS" if recon_gap < 1.0 else "WARN"
        print(f"       {split_name}:")
        print(f"         Final Equity: Rs {final_equity:,.2f}")
        print(f"         Cash + MTM:   Rs {final_cash + final_mtm:,.2f}")
        print(f"         Recon Gap:    Rs {recon_gap:,.2f} [{recon_ok}]")
    print()

    # 9. Safety Checks
    print("[8/10] Running safety checks...")
    checks_passed = 0
    checks_total = 0

    # Check 1: ML is OFF
    checks_total += 1
    if config['ml']['enabled'] == False:
        print("       [PASS] ML is OFF")
        checks_passed += 1
    else:
        print("       [FAIL] ML is ON — VIOLATION")

    # Check 2: Sentiment is DISABLED
    checks_total += 1
    if config['sentiment']['enabled'] == False:
        print("       [PASS] Sentiment is DISABLED")
        checks_passed += 1
    else:
        print("       [FAIL] Sentiment is ENABLED — VIOLATION")

    # Check 3: Regime filter is ON
    checks_total += 1
    if config['regime']['enabled'] == True:
        print("       [PASS] Regime filter is ON")
        checks_passed += 1
    else:
        print("       [FAIL] Regime filter is OFF — VIOLATION")

    # Check 4: Position limits respected
    for split_name, res in [('VALIDATION', res_val), ('TEST', res_test)]:
        checks_total += 1
        max_open = res['df_daily']['open_positions_cnt'].max()
        if max_open <= max_positions:
            print(f"       [PASS] {split_name}: Max open positions = {max_open} (limit {max_positions})")
            checks_passed += 1
        else:
            print(f"       [FAIL] {split_name}: Max open positions = {max_open} EXCEEDS limit {max_positions}")

    # Check 5: Outputs exist
    checks_total += 1
    output_files = [
        trade_ledger_path, equity_curve_path, daily_returns_path,
        perf_report_path, signals_log_path
    ]
    all_exist = all(os.path.exists(f) for f in output_files)
    if all_exist:
        print(f"       [PASS] All {len(output_files)} output files generated")
        checks_passed += 1
    else:
        missing = [f for f in output_files if not os.path.exists(f)]
        print(f"       [FAIL] Missing outputs: {missing}")

    # Check 6: Trade count > 0
    checks_total += 1
    if res_val['executed_positions'] > 0:
        print(f"       [PASS] Validation produced {res_val['executed_positions']} trades")
        checks_passed += 1
    else:
        print("       [FAIL] Validation produced 0 trades")

    print(f"       Safety: {checks_passed}/{checks_total} checks passed")
    print()

    # 10. Generate Readiness Report
    print("[9/10] Generating MVP readiness report...")
    report_path = os.path.join(output_dir, config['output']['readiness_report'])
    report_md = f"""# TRADING SYSTEM MVP — READINESS REPORT

Generated: {datetime.datetime.now().isoformat()}

## Executive Summary

| Parameter | Value |
|---|---|
| **Config Version** | MVP v1.0 |
| **Initial Capital** | Rs {initial_capital:,.0f} |
| **Allocation** | {max_trend} Trend / {max_vol} Volatility |
| **Max Positions** | {max_positions} |
| **Position Size** | Rs {pos_size:,.0f} |
| **Holding Period** | 10 days |
| **ML Status** | OFF |
| **Sentiment Status** | DISABLED |
| **Regime Filter** | ON (Nifty 50 vs EMA50) |
| **Dataset SHA256** | `{dataset_sha[:16]}...` |

## Performance Results

### VALIDATION (In-Sample Development)

| Metric | Value |
|---|---|
| **Net Return** | {res_val['net_portfolio_return_pct']:+.2f}% |
| **Daily Sharpe Ratio** | {res_val['daily_sharpe_ratio']:.2f} |
| **Max Drawdown** | {res_val['max_drawdown_pct']:.2f}% |
| **Win Rate** | {res_val['win_rate_pct']:.1f}% |
| **Profit Factor** | {res_val['profit_factor']:.2f} |
| **Executed Trades** | {res_val['executed_positions']} |
| **Mean Trade Return** | {res_val['mean_trade_return_pct']:+.2f}% |
| **Median Trade Return** | {res_val['median_trade_return_pct']:+.2f}% |
| **NR7 Trades** | {res_val['nr7_trades']} |
| **Avg Open Positions** | {res_val['avg_open_positions']:.2f} |

### TEST (Descriptive Out-of-Sample)

| Metric | Value |
|---|---|
| **Net Return** | {res_test['net_portfolio_return_pct']:+.2f}% |
| **Daily Sharpe Ratio** | {res_test['daily_sharpe_ratio']:.2f} |
| **Max Drawdown** | {res_test['max_drawdown_pct']:.2f}% |
| **Win Rate** | {res_test['win_rate_pct']:.1f}% |
| **Profit Factor** | {res_test['profit_factor']:.2f} |
| **Executed Trades** | {res_test['executed_positions']} |
| **Mean Trade Return** | {res_test['mean_trade_return_pct']:+.2f}% |
| **Median Trade Return** | {res_test['median_trade_return_pct']:+.2f}% |
| **NR7 Trades** | {res_test['nr7_trades']} |
| **Avg Open Positions** | {res_test['avg_open_positions']:.2f} |

## Strategy Breakdown

"""
    # Add per-strategy breakdown
    for split_name, res in [('Validation', res_val), ('Test (Descriptive)', res_test)]:
        df_t = res['trade_log']
        if len(df_t) > 0:
            report_md += f"### {split_name} Trades by Strategy\n\n"
            report_md += "| Strategy | Trades | Win Rate | Mean Return | Total P&L |\n"
            report_md += "|---|---|---|---|---|\n"
            for strat in sorted(df_t['strategy_name'].unique()):
                s_trades = df_t[df_t['strategy_name'] == strat]
                s_cnt = len(s_trades)
                s_wr = (s_trades['net_pnl'] > 0).mean() * 100.0
                s_mr = s_trades['net_return_pct'].mean()
                s_pnl = s_trades['net_pnl'].sum()
                report_md += f"| {strat} | {s_cnt} | {s_wr:.1f}% | {s_mr:+.2f}% | Rs {s_pnl:,.0f} |\n"
            report_md += "\n"

    report_md += f"""## Safety Checks

- ML: **OFF** [PASS]
- Sentiment: **DISABLED** [PASS]
- Regime Filter: **ON** [PASS]
- Safety Checks: **{checks_passed}/{checks_total} PASSED** {'[ALL PASS]' if checks_passed == checks_total else '[WARNING]'}

## Output Files

| File | Path |
|---|---|
| Trade Ledger | `{config['output']['base_dir']}/{config['output']['trade_ledger']}` |
| Equity Curve | `{config['output']['base_dir']}/{config['output']['equity_curve']}` |
| Daily Returns | `{config['output']['base_dir']}/{config['output']['daily_returns']}` |
| Performance Report | `{config['output']['base_dir']}/{config['output']['performance_report']}` |
| Signals Log | `{config['output']['base_dir']}/{config['output']['signals_log']}` |
| Readiness Report | `{config['output']['base_dir']}/{config['output']['readiness_report']}` |

## Commands

```bash
# Run MVP backtest
PYTHONPATH=. ./venv/bin/python scripts/run_mvp.py

# Launch dashboard
PYTHONPATH=. streamlit run app.py

# Run MVP tests
PYTHONPATH=. ./venv/bin/python scripts/test_step_8_mvp.py
```

## Deferred Enhancements (Post-MVP)

- Dynamic Risk & Position Sizing
- Advanced exit strategies (trailing stops, partial exits)
- Sentiment integration (requires real-time news data feeds)
- ML reintroduction (requires embargo fix + revalidation)
- Broker integration (Zerodha/Upstox API)
- Live trading mode
- Cloud deployment
- Further strategy research
"""

    with open(report_path, 'w') as f:
        f.write(report_md)
    print(f"       Readiness Report: {report_path}")
    print()

    # Final Summary
    elapsed = (datetime.datetime.now() - start_time).total_seconds()
    print("[10/10] MVP PIPELINE COMPLETE")
    print("=" * 80)
    print(f"  Elapsed:     {elapsed:.1f}s")
    print(f"  Config:      config/mvp_config.yaml")
    print(f"  Output Dir:  {output_dir}")
    print(f"  Trades:      {res_val['executed_positions']} (val) + {res_test['executed_positions']} (test) = {res_val['executed_positions'] + res_test['executed_positions']} total")
    print(f"  VALIDATION:  {res_val['net_portfolio_return_pct']:+.2f}% | Sharpe {res_val['daily_sharpe_ratio']:.2f} | DD {res_val['max_drawdown_pct']:.2f}%")
    print(f"  TEST:        {res_test['net_portfolio_return_pct']:+.2f}% | Sharpe {res_test['daily_sharpe_ratio']:.2f} | DD {res_test['max_drawdown_pct']:.2f}%")
    print(f"  Safety:      {checks_passed}/{checks_total} checks passed")
    print(f"  Dashboard:   PYTHONPATH=. streamlit run app.py")
    print("=" * 80)

    return {
        'config': config,
        'validation': res_val,
        'test': res_test,
        'safety_checks_passed': checks_passed,
        'safety_checks_total': checks_total,
        'output_dir': output_dir
    }


if __name__ == "__main__":
    run_mvp()
