import os
import yfinance as yf
import pandas as pd
import numpy as np
import pandas_ta as ta
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass, field


# ==============================================================================
# CONFIGURATION OBJECTS (CHANGE 6)
# ==============================================================================

@dataclass
class TransactionCostConfig:
    """
    Configurable Indian Equities Transaction Cost & Slippage Parameters.
    NOTE: These parameters are sensible placeholder defaults and require explicit verification against official broker/exchange rate cards.
    """
    brokerage_pct: float = 0.0003          # 0.03% or capped per order (placeholder)
    brokerage_cap_per_order: float = 20.0  # ₹20 max per order flat fee (placeholder)
    stt_pct: float = 0.0010                # 0.1% STT on delivery sell side (placeholder)
    exchange_charge_pct: float = 0.0000345 # NSE Transaction Charges (placeholder)
    gst_pct: float = 0.18                  # 18% GST on Brokerage + Exchange Charges (placeholder)
    stamp_duty_pct: float = 0.00015        # 0.015% Stamp Duty on Buy side (placeholder)
    entry_slippage_pct: float = 0.0010     # 0.10% Entry Slippage (worse buy price)
    exit_slippage_pct: float = 0.0010      # 0.10% Exit Slippage (worse sell price)


@dataclass
class BacktestConfig:
    """
    Centralized Backtest Simulation Configuration.
    """
    initial_capital: float = 1_000_000.0
    max_risk_per_trade: float = 0.02
    max_position_size_pct: float = 0.20
    atr_multiplier: float = 2.0
    target_rr: float = 2.5
    stop_loss_pct: float = 0.04
    max_holding_days: int = 20
    min_conviction_score: int = 6
    ambiguous_bar_policy: str = "CONSERVATIVE"  # Options: 'CONSERVATIVE', 'OPTIMISTIC', 'SKIP'
    costs: TransactionCostConfig = field(default_factory=TransactionCostConfig)


# ==============================================================================
# TRANSACTION COST & SLIPPAGE MODEL ENGINE (CHANGE 2 & CHANGE 3)
# ==============================================================================

def calculate_trade_execution_costs(
    observed_entry_px: float,
    observed_exit_px: float,
    quantity: int,
    costs_config: TransactionCostConfig
) -> Dict[str, float]:
    """
    Calculates execution slippage, entry/exit transaction costs, gross P&L, and net P&L.
    
    Long Position Slippage Logic:
    - Executed Entry Price = Observed Entry Price * (1 + entry_slippage_pct) [Worse Buy]
    - Executed Exit Price  = Observed Exit Price * (1 - exit_slippage_pct)  [Worse Sell]
    """
    # 1. Apply Slippage to Observed Prices
    executed_entry_price = observed_entry_px * (1.0 + costs_config.entry_slippage_pct)
    executed_exit_price = observed_exit_px * (1.0 - costs_config.exit_slippage_pct)

    buy_val_gross = observed_entry_px * quantity
    buy_val_exec = executed_entry_price * quantity

    sell_val_gross = observed_exit_px * quantity
    sell_val_exec = executed_exit_price * quantity

    # Slippage Cost (INR)
    entry_slippage_cost = buy_val_exec - buy_val_gross
    exit_slippage_cost = sell_val_gross - sell_val_exec
    total_slippage_cost = entry_slippage_cost + exit_slippage_cost

    # 2. Buy Side Transaction Costs
    entry_brokerage = min(costs_config.brokerage_cap_per_order, buy_val_exec * costs_config.brokerage_pct)
    entry_stamp_duty = buy_val_exec * costs_config.stamp_duty_pct
    entry_exchange_fee = buy_val_exec * costs_config.exchange_charge_pct
    entry_gst = (entry_brokerage + entry_exchange_fee) * costs_config.gst_pct

    entry_total_costs = entry_brokerage + entry_stamp_duty + entry_exchange_fee + entry_gst

    # 3. Sell Side Transaction Costs
    exit_brokerage = min(costs_config.brokerage_cap_per_order, sell_val_exec * costs_config.brokerage_pct)
    exit_stt = sell_val_exec * costs_config.stt_pct
    exit_exchange_fee = sell_val_exec * costs_config.exchange_charge_pct
    exit_gst = (exit_brokerage + exit_exchange_fee) * costs_config.gst_pct

    exit_total_costs = exit_brokerage + exit_stt + exit_exchange_fee + exit_gst

    total_transaction_costs = entry_total_costs + exit_total_costs

    # 4. Gross vs Net PnL Calculations
    gross_pnl = round(sell_val_gross - buy_val_gross, 2)
    net_pnl = round(sell_val_exec - buy_val_exec - total_transaction_costs, 2)

    gross_return_pct = round(((observed_exit_px - observed_entry_px) / observed_entry_px) * 100.0, 2) if observed_entry_px > 0 else 0.0
    net_return_pct = round((net_pnl / buy_val_exec) * 100.0, 2) if buy_val_exec > 0 else 0.0

    return {
        "executed_entry_price": round(executed_entry_price, 2),
        "executed_exit_price": round(executed_exit_price, 2),
        "entry_slippage_cost": round(entry_slippage_cost, 2),
        "exit_slippage_cost": round(exit_slippage_cost, 2),
        "total_slippage_cost": round(total_slippage_cost, 2),
        "entry_transaction_costs": round(entry_total_costs, 2),
        "exit_transaction_costs": round(exit_total_costs, 2),
        "total_transaction_costs": round(total_transaction_costs, 2),
        "gross_pnl": gross_pnl,
        "net_pnl": net_pnl,
        "gross_return_pct": gross_return_pct,
        "net_return_pct": net_return_pct
    }


# ==============================================================================
# TECHNICAL INDICATORS CALCULATOR
# ==============================================================================

def calculate_backtest_indicators(df: pd.DataFrame, nifty_df: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    """
    Compute multi-strategy technical indicators on historical price bars for backtesting.
    """
    df = df.copy()

    df['EMA_20'] = ta.ema(df['Close'], length=20)
    df['EMA_50'] = ta.ema(df['Close'], length=50)
    df['EMA_200'] = ta.ema(df['Close'], length=200)
    df['RSI_14'] = ta.rsi(df['Close'], length=14)
    df['ATR_20'] = ta.atr(df['High'], df['Low'], df['Close'], length=20)
    df['ATR_60'] = ta.atr(df['High'], df['Low'], df['Close'], length=60)

    df['Donchian_20'] = df['High'].shift(1).rolling(20).max()
    df['Donchian_50'] = df['High'].shift(1).rolling(50).max()

    df['Turnover'] = df['Close'] * df['Volume']
    df['Turnover_20D'] = df['Turnover'].rolling(20).mean()

    # Relative Strength (RS) vs Nifty 50
    if nifty_df is not None and not nifty_df.empty:
        common_idx = df.index.intersection(nifty_df.index)
        if len(common_idx) > 60:
            df_reindexed = df.loc[common_idx]
            nifty_reindexed = nifty_df.loc[common_idx]

            stock_ret_3m = df_reindexed['Close'].pct_change(63) * 100.0
            nifty_ret_3m = nifty_reindexed['Close'].pct_change(63) * 100.0
            df['RS_3M'] = (stock_ret_3m - nifty_ret_3m).reindex(df.index).fillna(0.0)
        else:
            df['RS_3M'] = df['Close'].pct_change(63) * 100.0
    else:
        df['RS_3M'] = df['Close'].pct_change(63) * 100.0

    return df


# ==============================================================================
# MAIN BACKTEST ENGINE (CHANGE 1, 4, 5, 7, 8)
# ==============================================================================

from universe_engine import get_universe_as_of, get_universe_metadata


def run_historical_backtest(
    symbols: Optional[List[str]] = None,
    period: str = "1y",
    as_of_date: Optional[str] = None,
    mode: str = "research",
    config: Optional[BacktestConfig] = None,
    # Backward-compatible parameter overrides:
    initial_capital: float = 1_000_000.0,
    max_risk_per_trade: float = 0.02,
    holding_days: int = 20,
    target_pct: float = 0.10,
    stop_loss_pct: float = 0.04,
    w_tech: float = 0.50,
    w_fund: float = 0.30,
    w_sent: float = 0.20
) -> Dict[str, Any]:
    """
    Realistic Multi-Strategy Historical Backtesting Engine with Point-in-Time Universe Integration:
    - Point-in-Time Research Universe fetching via universe_engine.py
    - T+1 Next-Bar Open Price Entry (Eliminates Same-Day Closing Look-Ahead Bias)
    - Separated Transaction Costs & Slippage Calculations
    - Configurable Ambiguous Bar Execution Policy (CONSERVATIVE, OPTIMISTIC, SKIP)
    - Detailed Trade Audit Trail Log
    """
    if config is None:
        config = BacktestConfig(
            initial_capital=initial_capital,
            max_risk_per_trade=max_risk_per_trade,
            stop_loss_pct=stop_loss_pct,
            max_holding_days=holding_days
        )

    # Dynamic Point-in-Time Universe Fetching
    target_as_of = as_of_date if as_of_date else "2026-08-10"
    univ_meta = get_universe_metadata(target_as_of)

    if not symbols:
        raw_univ = get_universe_as_of(target_as_of, mode=mode)
        symbols = [s + ".NS" if not s.endswith(".NS") else s for s in raw_univ]

    print(f"[BACKTEST UNIVERSE INTEGRATION]")
    print(f"  - Target As-Of Date   : {target_as_of}")
    print(f"  - Universe Size       : {len(symbols)} Stocks")
    print(f"  - Evidence Status     : {univ_meta['evidence_status']}")
    print(f"  - Recon Method        : {univ_meta['reconstruction_method']}")
    print(f"  - Survivorship Risk   : {univ_meta['survivorship_bias_risk']}")

    # Normalize Ensemble Weights
    total_w = w_tech + w_fund + w_sent
    if total_w > 0:
        w_tech_norm = w_tech / total_w
        w_fund_norm = w_fund / total_w
        w_sent_norm = w_sent / total_w
    else:
        w_tech_norm, w_fund_norm, w_sent_norm = 0.5, 0.3, 0.2

    # Fetch Nifty 50 Benchmark
    try:
        nifty_ticker = yf.Ticker("^NSEI")
        nifty_df = nifty_ticker.history(period=period, interval="1d", auto_adjust=True)
    except Exception as e:
        print(f"Backtest Nifty fetch error: {e}")
        nifty_df = None

    all_trades = []
    symbol_dfs = {}

    for sym in symbols:
        try:
            ticker = yf.Ticker(sym)
            df = ticker.history(period=period, interval="1d", auto_adjust=True)
            if df.empty or len(df) < 100:
                continue

            df = calculate_backtest_indicators(df, nifty_df)
            symbol_dfs[sym] = df
        except Exception as e:
            print(f"Error fetching backtest data for {sym}: {e}")

    if not symbol_dfs:
        res_empty = _empty_backtest_result(config)
        res_empty["universe_metadata"] = univ_meta
        return res_empty

    # Simulate Trade Generation Across Symbols
    for sym, df in symbol_dfs.items():
        i = 60
        while i < len(df) - 2:  # Need at least i+1 for T+1 Open entry
            # Signal Date (T Close)
            signal_bar = df.iloc[i]
            signal_date_str = df.index[i].strftime("%Y-%m-%d")
            c = signal_bar['Close']
            ema20 = signal_bar['EMA_20']
            ema50 = signal_bar['EMA_50']
            ema200 = signal_bar['EMA_200']
            rsi = signal_bar['RSI_14']
            donch20 = signal_bar['Donchian_20']

            atr20 = signal_bar['ATR_20']
            atr60 = signal_bar['ATR_60']
            vcp_ratio = atr20 / atr60 if (pd.notna(atr60) and atr60 > 0) else 1.0
            rs_3m = signal_bar.get('RS_3M', 0.0)

            is_uptrend = bool(pd.notna(ema50) and pd.notna(ema200) and c > ema50 > ema200)

            # Signal Detection
            strategy = None
            tech_score_norm = 0.75

            if is_uptrend:
                if pd.notna(donch20) and c >= donch20 * 0.995:
                    strategy = "Donchian Channel Breakout"
                    tech_score_norm = 0.90
                elif vcp_ratio <= 1.05 and rsi >= 50:
                    strategy = "VCP Volatility Contraction Breakout"
                    tech_score_norm = 0.88
                elif pd.notna(ema20) and abs(c - ema20) / ema20 <= 0.015:
                    strategy = "EMA Pullback / Bounce"
                    tech_score_norm = 0.82
                elif rs_3m > 5.0 and rsi >= 60:
                    strategy = "RS Momentum Breakout"
                    tech_score_norm = 0.85

            if strategy and is_uptrend:
                s_tech = tech_score_norm * 10.0
                s_fund = 7.5  # Baseline fundamental score
                s_sent = 7.0  # Baseline sentiment score
                r_penalty = 0.0

                weighted_score = (w_tech_norm * s_tech) + (w_fund_norm * s_fund) + (w_sent_norm * s_sent) - r_penalty
                conviction_final = max(1, min(10, int(round(weighted_score))))

                if conviction_final >= config.min_conviction_score:
                    # --- CHANGE 1: NEXT-BAR ENTRY (T+1 OPEN) ---
                    entry_bar_idx = i + 1
                    if entry_bar_idx >= len(df):
                        i += 1
                        continue

                    entry_bar = df.iloc[entry_bar_idx]
                    signal_px = float(entry_bar['Open'])

                    # Handle missing or invalid T+1 Open price safely
                    if pd.isna(signal_px) or signal_px <= 0:
                        i += 1
                        continue

                    entry_date_str = df.index[entry_bar_idx].strftime("%Y-%m-%d")

                    # Executed entry price after entry slippage
                    executed_entry_px = round(signal_px * (1.0 + config.costs.entry_slippage_pct), 2)

                    # Calculate Stop Loss and Target based on Signal Date (T) ATR
                    sl_dist = max(signal_px * config.stop_loss_pct, config.atr_multiplier * atr20 if pd.notna(atr20) else signal_px * 0.03)
                    stop_price = round(signal_px - sl_dist, 2)
                    target_price = round(signal_px + (sl_dist * config.target_rr), 2)
                    rr_ratio = round(config.target_rr, 2)

                    # --- FIX 1: POSITION SIZING USES EXECUTED ENTRY PRICE ---
                    risk_amt = config.initial_capital * config.max_risk_per_trade
                    actual_risk_per_share = max(executed_entry_px - stop_price, executed_entry_px * 0.005)
                    quantity = max(1, int(risk_amt // actual_risk_per_share))

                    # Cap trade size using executed entry price
                    max_val = config.initial_capital * config.max_position_size_pct
                    if quantity * executed_entry_px > max_val:
                        quantity = max(1, int(max_val // executed_entry_px))

                    # Exit Simulation Loop
                    observed_exit_price = None
                    exit_reason = None
                    exit_date_str = None
                    hold_len = 0

                    for j in range(entry_bar_idx, min(entry_bar_idx + config.max_holding_days, len(df))):
                        sub_bar = df.iloc[j]
                        hold_len += 1
                        hi = float(sub_bar['High'])
                        lo = float(sub_bar['Low'])

                        hit_target = bool(hi >= target_price)
                        hit_stop = bool(lo <= stop_price)

                        # --- CHANGE 4: SAME-DAY STOP/TARGET AMBIGUITY ---
                        if hit_target and hit_stop:
                            if config.ambiguous_bar_policy == "CONSERVATIVE":
                                observed_exit_price = stop_price
                                exit_reason = "Stop Loss Hit (Ambiguous Bar - Conservative)"
                                exit_date_str = df.index[j].strftime("%Y-%m-%d")
                                break
                            elif config.ambiguous_bar_policy == "OPTIMISTIC":
                                observed_exit_price = target_price
                                exit_reason = "Target Hit (Ambiguous Bar - Optimistic)"
                                exit_date_str = df.index[j].strftime("%Y-%m-%d")
                                break
                            elif config.ambiguous_bar_policy == "SKIP":
                                observed_exit_price = float(sub_bar['Close'])
                                exit_reason = "Ambiguous Bar - Skipped"
                                exit_date_str = df.index[j].strftime("%Y-%m-%d")
                                break
                        elif hit_target:
                            observed_exit_price = target_price
                            exit_reason = "Target Hit"
                            exit_date_str = df.index[j].strftime("%Y-%m-%d")
                            break
                        elif hit_stop:
                            observed_exit_price = stop_price
                            exit_reason = "Stop Loss Hit"
                            exit_date_str = df.index[j].strftime("%Y-%m-%d")
                            break

                    if observed_exit_price is None:
                        last_idx = min(entry_bar_idx + config.max_holding_days - 1, len(df) - 1)
                        observed_exit_price = float(df.iloc[last_idx]['Close'])
                        exit_reason = "Time Exit"
                        exit_date_str = df.index[last_idx].strftime("%Y-%m-%d")

                    # --- CHANGE 2 & 3: TRANSACTION COSTS & SLIPPAGE ---
                    cost_res = calculate_trade_execution_costs(
                        observed_entry_px=signal_px,
                        observed_exit_px=observed_exit_price,
                        quantity=quantity,
                        costs_config=config.costs
                    )

                    # --- CHANGE 5: TRADE AUDIT TRAIL RECORD ---
                    trade_record = {
                        "symbol": sym,
                        "strategy": strategy,
                        "signal_date": signal_date_str,
                        "entry_date": entry_date_str,
                        "signal_price": round(signal_px, 2),
                        "entry_price": cost_res['executed_entry_price'],
                        "stop_price": stop_price,
                        "target_price": target_price,
                        "exit_date": exit_date_str,
                        "exit_price": cost_res['executed_exit_price'],
                        "observed_exit_price": round(observed_exit_price, 2),
                        "exit_reason": exit_reason,
                        "quantity": quantity,
                        "gross_pnl": cost_res['gross_pnl'],
                        "transaction_costs": cost_res['total_transaction_costs'],
                        "slippage_cost": cost_res['total_slippage_cost'],
                        "net_pnl": cost_res['net_pnl'],
                        "holding_period": hold_len,
                        "gross_return_pct": cost_res['gross_return_pct'],
                        "return_pct": cost_res['net_return_pct'],
                        "win": cost_res['net_pnl'] > 0,
                        "ambiguous_skipped": (exit_reason == "Ambiguous Bar - Skipped"),
                        "risk_reward_ratio": rr_ratio,
                        "conviction_score": conviction_final
                    }

                    all_trades.append(trade_record)
                    i += max(hold_len, 5)
                else:
                    i += 1
            else:
                i += 1

    trades_df = pd.DataFrame(all_trades)
    if trades_df.empty:
        return _empty_backtest_result(config)

    # Filter out skipped ambiguous trades for win/loss metrics if policy is SKIP
    valid_trades_df = trades_df[trades_df['ambiguous_skipped'] == False].copy() if 'ambiguous_skipped' in trades_df else trades_df.copy()
    if valid_trades_df.empty:
        return _empty_backtest_result(config)

    valid_trades_df.sort_values(by="entry_date", inplace=True)
    valid_trades_df.reset_index(drop=True, inplace=True)

    # --- CHANGE 7: EXPANDED PERFORMANCE METRICS ---
    total_trades = len(valid_trades_df)
    winning_trades_df = valid_trades_df[valid_trades_df['win'] == True]
    losing_trades_df = valid_trades_df[valid_trades_df['win'] == False]

    winning_trades = len(winning_trades_df)
    losing_trades = len(losing_trades_df)
    win_rate_pct = round((winning_trades / total_trades) * 100.0, 1) if total_trades > 0 else 0.0

    avg_win_inr = round(winning_trades_df['net_pnl'].mean(), 2) if not winning_trades_df.empty else 0.0
    avg_loss_inr = round(abs(losing_trades_df['net_pnl'].mean()), 2) if not losing_trades_df.empty else 0.0

    avg_win_pct = round(winning_trades_df['return_pct'].mean(), 2) if not winning_trades_df.empty else 0.0
    avg_loss_pct = round(abs(losing_trades_df['return_pct'].mean()), 2) if not losing_trades_df.empty else 0.0

    gross_profits = winning_trades_df['net_pnl'].sum() if not winning_trades_df.empty else 0.0
    gross_losses = abs(losing_trades_df['net_pnl'].sum()) if not losing_trades_df.empty else 0.0
    profit_factor = round(gross_profits / gross_losses, 2) if gross_losses > 0 else (5.0 if gross_profits > 0 else 0.0)

    # Expectancy per trade (INR) = (Win Rate * Avg Win) - (Loss Rate * Avg Loss)
    win_prob = winning_trades / total_trades if total_trades > 0 else 0.0
    expectancy_inr = round((win_prob * avg_win_inr) - ((1.0 - win_prob) * avg_loss_inr), 2)

    # Equity Curve & Returns
    cumulative_pnl = valid_trades_df['net_pnl'].cumsum()
    valid_trades_df['equity'] = config.initial_capital + cumulative_pnl

    gross_cumulative_pnl = valid_trades_df['gross_pnl'].cumsum()
    valid_trades_df['gross_equity'] = config.initial_capital + gross_cumulative_pnl

    net_return_pct = round(((valid_trades_df['equity'].iloc[-1] - config.initial_capital) / config.initial_capital) * 100.0, 2)
    gross_return_pct = round(((valid_trades_df['gross_equity'].iloc[-1] - config.initial_capital) / config.initial_capital) * 100.0, 2)

    # Holding Period Metrics
    avg_holding_period = round(valid_trades_df['holding_period'].mean(), 1) if total_trades > 0 else 0.0
    median_holding_period = round(float(valid_trades_df['holding_period'].median()), 1) if total_trades > 0 else 0.0

    # Max Drawdown & Sharpe Ratio
    dates = pd.to_datetime(valid_trades_df['entry_date'])
    equity_series = pd.Series(valid_trades_df['equity'].values, index=dates)
    daily_equity = equity_series.groupby(equity_series.index).last().resample('D').ffill().fillna(config.initial_capital)

    daily_returns = daily_equity.pct_change().dropna()
    if len(daily_returns) > 5 and daily_returns.std() > 0:
        ann_std = daily_returns.std() * np.sqrt(252)
        ann_mean = daily_returns.mean() * 252
        sharpe_ratio = round(ann_mean / ann_std, 2)
    else:
        sharpe_ratio = "N/A"

    rolling_max = daily_equity.cummax()
    drawdown = (daily_equity - rolling_max) / rolling_max * 100.0
    max_drawdown_pct = round(abs(drawdown.min()), 2) if not drawdown.empty else 0.0

    largest_win_inr = round(valid_trades_df['net_pnl'].max(), 2) if total_trades > 0 else 0.0
    largest_loss_inr = round(valid_trades_df['net_pnl'].min(), 2) if total_trades > 0 else 0.0

    total_transaction_costs = round(valid_trades_df['transaction_costs'].sum(), 2)
    total_slippage_cost = round(valid_trades_df['slippage_cost'].sum(), 2)

    # --- CHANGE 8: STRATEGY BREAKDOWN TABLE ---
    strategy_rows = []
    for strat_name, grp in valid_trades_df.groupby('strategy'):
        s_total = len(grp)
        s_wins = len(grp[grp['win'] == True])
        s_losses = s_total - s_wins
        s_win_rate = round((s_wins / s_total) * 100.0, 1) if s_total > 0 else 0.0
        s_avg_win = round(grp[grp['win'] == True]['return_pct'].mean(), 2) if s_wins > 0 else 0.0
        s_avg_loss = round(abs(grp[grp['win'] == False]['return_pct'].mean()), 2) if s_losses > 0 else 0.0
        
        s_gross_p = grp[grp['net_pnl'] > 0]['net_pnl'].sum()
        s_gross_l = abs(grp[grp['net_pnl'] < 0]['net_pnl'].sum())
        s_pf = round(s_gross_p / s_gross_l, 2) if s_gross_l > 0 else (5.0 if s_gross_p > 0 else 0.0)
        
        s_prob = s_wins / s_total if s_total > 0 else 0.0
        s_expectancy = round((s_prob * (grp[grp['win'] == True]['net_pnl'].mean() or 0.0)) - ((1 - s_prob) * abs(grp[grp['win'] == False]['net_pnl'].mean() or 0.0)), 2)
        s_net_return = round(grp['net_pnl'].sum(), 2)
        s_avg_hold = round(grp['holding_period'].mean(), 1)

        # Max drawdown per strategy
        s_dates = pd.to_datetime(grp['entry_date'])
        s_eq = pd.Series(config.initial_capital + grp['net_pnl'].cumsum().values, index=s_dates)
        s_daily = s_eq.groupby(s_eq.index).last().resample('D').ffill().fillna(config.initial_capital)
        s_dd = abs(((s_daily - s_daily.cummax()) / s_daily.cummax() * 100.0).min())

        strategy_rows.append({
            "strategy": strat_name,
            "total_trades": s_total,
            "win_rate_pct": s_win_rate,
            "avg_win_pct": s_avg_win,
            "avg_loss_pct": s_avg_loss,
            "profit_factor": s_pf,
            "expectancy_inr": s_expectancy,
            "net_return_inr": s_net_return,
            "max_drawdown_pct": round(s_dd, 2),
            "avg_holding_period": s_avg_hold
        })

    strategy_breakdown_df = pd.DataFrame(strategy_rows)
    equity_curve_df = pd.DataFrame({"Equity_INR": daily_equity})

    return {
        "total_trades": total_trades,
        "winning_trades": winning_trades,
        "losing_trades": losing_trades,
        "win_rate_pct": win_rate_pct,
        "avg_win_inr": avg_win_inr,
        "avg_loss_inr": avg_loss_inr,
        "avg_win_pct": avg_win_pct,
        "avg_loss_pct": avg_loss_pct,
        "profit_factor": profit_factor,
        "expectancy_inr": expectancy_inr,
        "gross_return_pct": gross_return_pct,
        "net_return_pct": net_return_pct,
        "total_return_pct": net_return_pct,  # Backward compatibility
        "sharpe_ratio": sharpe_ratio,
        "max_drawdown_pct": max_drawdown_pct,
        "avg_holding_period": avg_holding_period,
        "median_holding_period": median_holding_period,
        "largest_win_inr": largest_win_inr,
        "largest_loss_inr": largest_loss_inr,
        "total_transaction_costs": total_transaction_costs,
        "total_slippage_cost": total_slippage_cost,
        "ambiguous_bar_policy": config.ambiguous_bar_policy,
        "strategy_breakdown": strategy_breakdown_df,
        "equity_curve": equity_curve_df,
        "trades_df": valid_trades_df,
        "universe_metadata": univ_meta
    }


def _empty_backtest_result(config: BacktestConfig) -> Dict[str, Any]:
    return {
        "total_trades": 0,
        "winning_trades": 0,
        "losing_trades": 0,
        "win_rate_pct": 0.0,
        "avg_win_inr": 0.0,
        "avg_loss_inr": 0.0,
        "avg_win_pct": 0.0,
        "avg_loss_pct": 0.0,
        "profit_factor": 0.0,
        "expectancy_inr": 0.0,
        "gross_return_pct": 0.0,
        "net_return_pct": 0.0,
        "total_return_pct": 0.0,
        "sharpe_ratio": "N/A",
        "max_drawdown_pct": 0.0,
        "avg_holding_period": 0.0,
        "median_holding_period": 0.0,
        "largest_win_inr": 0.0,
        "largest_loss_inr": 0.0,
        "total_transaction_costs": 0.0,
        "total_slippage_cost": 0.0,
        "ambiguous_bar_policy": config.ambiguous_bar_policy,
        "strategy_breakdown": pd.DataFrame(),
        "equity_curve": pd.DataFrame(),
        "trades_df": pd.DataFrame()
    }


if __name__ == "__main__":
    print("=" * 80)
    print("REALISTIC NIFTY 500 BACKTEST ENGINE (T+1 ENTRY & COST MODEL)")
    print("=" * 80)

    test_symbols = [
        "TCS.NS", "INFY.NS", "PERSISTENT.NS", "TECHM.NS", "ICICIBANK.NS",
        "SBIN.NS", "TATAMOTORS.NS", "DIXON.NS", "RELIANCE.NS", "SUNPHARMA.NS"
    ]

    res = run_historical_backtest(symbols=test_symbols, period="1y")

    print(f"Ambiguous Bar Policy   : {res['ambiguous_bar_policy']}")
    print(f"Total Simulated Trades : {res['total_trades']}")
    print(f"Win Rate (%)           : {res['win_rate_pct']}% ({res['winning_trades']}W / {res['losing_trades']}L)")
    print(f"Gross Return (%)       : {res['gross_return_pct']}%")
    print(f"Net Return (%)         : {res['net_return_pct']}%")
    print(f"Profit Factor          : {res['profit_factor']}")
    print(f"Sharpe Ratio           : {res['sharpe_ratio']}")
    print(f"Max Drawdown (%)       : {res['max_drawdown_pct']}%")
    print(f"Expectancy Per Trade   : ₹{res['expectancy_inr']}")
    print(f"Total Transaction Costs: ₹{res['total_transaction_costs']}")
    print(f"Total Slippage Cost    : ₹{res['total_slippage_cost']}")

    print("\nStrategy Breakdown Table:")
    print(res['strategy_breakdown'])
    print("=" * 80)
