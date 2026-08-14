import yfinance as yf
import pandas as pd
import numpy as np
import pandas_ta as ta
import datetime
from typing import List, Dict, Any, Optional, Tuple

import universe_engine
from provider_symbols import yahoo_nse_symbol

# Dynamic Nifty 500 Symbol List from Universe Engine
DEFAULT_NIFTY_SYMBOLS = universe_engine.get_current_universe()


def fetch_nifty50_benchmark(period: str = "1y", as_of_date: Optional[str] = None) -> Tuple[Optional[pd.DataFrame], Dict[str, float]]:
    """
    Fetch Nifty 50 index (^NSEI) daily data and return calculated performance (% return)
    over 1 Month (21 days), 3 Months (63 days), and 6 Months (126 days).
    """
    try:
        nifty_ticker = yf.Ticker("^NSEI")
        df = nifty_ticker.history(period=period, interval="1d", auto_adjust=True)
        if df.empty:
            return None, {"1m": 0.0, "3m": 0.0, "6m": 0.0}

        if as_of_date:
            df = df[df.index.strftime('%Y-%m-%d') <= as_of_date]

        if len(df) < 130:
            return None, {"1m": 0.0, "3m": 0.0, "6m": 0.0}

        close = df['Close']
        latest_c = close.iloc[-1]
        c_21d = close.iloc[-22] if len(close) >= 22 else close.iloc[0]
        c_63d = close.iloc[-64] if len(close) >= 64 else close.iloc[0]
        c_126d = close.iloc[-127] if len(close) >= 127 else close.iloc[0]

        returns = {
            "1m": float((latest_c - c_21d) / c_21d * 100.0),
            "3m": float((latest_c - c_63d) / c_63d * 100.0),
            "6m": float((latest_c - c_126d) / c_126d * 100.0)
        }
        return df, returns
    except Exception as e:
        print(f"Error fetching Nifty 50 benchmark data: {e}")
        return None, {"1m": 0.0, "3m": 0.0, "6m": 0.0}


def fetch_ha_market_histories(period: str = "2y", as_of_date: Optional[str] = None) -> Tuple[Optional[pd.DataFrame], Optional[pd.DataFrame]]:
    """Fetch advisory Nifty 500/VIX histories once per scan; failures never affect qualification."""
    try:
        nifty500 = yf.Ticker("^CRSLDX").history(period=period, interval="1d", auto_adjust=True)
        india_vix = yf.Ticker("^INDIAVIX").history(period=period, interval="1d", auto_adjust=True)
        if as_of_date:
            nifty500 = nifty500[nifty500.index.strftime("%Y-%m-%d") <= as_of_date]
            india_vix = india_vix[india_vix.index.strftime("%Y-%m-%d") <= as_of_date]
        return (nifty500 if not nifty500.empty else None, india_vix if not india_vix.empty else None)
    except Exception:
        return None, None


def fetch_stock_data(symbol: str, period: str = "2y", as_of_date: Optional[str] = None) -> Optional[pd.DataFrame]:
    """
    Fetch historical EOD daily price data for a given ticker symbol using yfinance.
    Ensures .NS ticker suffix is appended for NSE securities.
    Slices historical bars as of `as_of_date` when provided.
    """
    try:
        clean_sym = yahoo_nse_symbol(symbol)
        ticker = yf.Ticker(clean_sym)
        df = ticker.history(period=period, interval="1d", auto_adjust=True)
        if df.empty:
            return None

        if as_of_date:
            df = df[df.index.strftime('%Y-%m-%d') <= as_of_date]

        if len(df) < 200:  # Need at least 200 bars for 200 EMA
            return None
        return df
    except Exception as e:
        print(f"Error fetching data for {symbol}: {e}")
        return None


def fetch_bulk_stock_data(symbols: List[str], period: str = "2y") -> Dict[str, pd.DataFrame]:
    """
    Bulk download historical EOD daily price data for a list of ticker symbols using yfinance multi-threading.
    Returns a dictionary mapping clean_symbol (with .NS) to its OHLCV DataFrame.
    """
    clean_map = {s: yahoo_nse_symbol(s) for s in symbols}
    clean_syms = list(set(clean_map.values()))

    try:
        data_df = yf.download(clean_syms, period=period, interval="1d", group_by="ticker", threads=True, progress=False, auto_adjust=True)
        result = {}
        for clean_sym in clean_syms:
            try:
                if len(clean_syms) == 1:
                    df = data_df.copy()
                else:
                    if hasattr(data_df.columns, 'levels') and clean_sym in data_df.columns.levels[0]:
                        df = data_df[clean_sym].dropna(how="all").copy()
                    else:
                        continue
                if not df.empty and len(df) >= 50:
                    result[clean_sym] = df
            except Exception:
                pass
        return result
    except Exception as e:
        print(f"Bulk download failed: {e}")
        return {}


def calculate_indicators(df: pd.DataFrame, nifty_returns: Dict[str, float]) -> pd.DataFrame:
    """
    Calculate Technical Indicators:
    - 20, 50, and 200 EMAs
    - 14-day RSI
    - 20-day ATR & 60-day ATR (Volatility Contraction Ratio)
    - Volume Dry-up Ratio (Down-day volume / Up-day volume)
    - EMA Bounce / Retest (20 EMA & 50 EMA retest with volume)
    - Donchian Channel Highs (20-day and 50-day highs)
    - Relative Strength (RS) vs Nifty 50 (1M, 3M, 6M)
    - 20-day Average Daily Turnover
    """
    df = df.copy()

    # Exponential Moving Averages
    df['EMA_20'] = ta.ema(df['Close'], length=20)
    df['EMA_50'] = ta.ema(df['Close'], length=50)
    df['EMA_200'] = ta.ema(df['Close'], length=200)

    # 14-day RSI
    df['RSI_14'] = ta.rsi(df['Close'], length=14)

    # ATRs for Volatility Contraction Pattern (VCP)
    df['ATR_20'] = ta.atr(df['High'], df['Low'], df['Close'], length=20)
    df['ATR_60'] = ta.atr(df['High'], df['Low'], df['Close'], length=60)

    # 20-day Average Daily Turnover (Close * Volume)
    df['Turnover'] = df['Close'] * df['Volume']
    # 20-day Average Daily Turnover & 20-day Shifted Volume Average (Prior 20 sessions)
    df['Turnover'] = df['Close'] * df['Volume']
    df['Turnover_20D_Avg'] = df['Turnover'].rolling(window=20).mean()
    df['Volume_20D_Avg'] = df['Volume'].shift(1).rolling(window=20).mean()

    # Volume 20D Ratio (Current Volume / Prior 20-Session Mean)
    vol_20d_avg_shifted = df['Volume_20D_Avg']
    df['Volume_Ratio_20'] = np.where((vol_20d_avg_shifted.notna()) & (vol_20d_avg_shifted > 0), df['Volume'] / vol_20d_avg_shifted, np.nan)

    # --- Donchian Channel Breakout Calculation (20-day and 50-day Highs) ---
    df['Donchian_High_20'] = df['High'].shift(1).rolling(window=20).max()
    df['Donchian_High_50'] = df['High'].shift(1).rolling(window=50).max()

    latest_c = df['Close'].iloc[-1]
    donch_20 = df['Donchian_High_20'].iloc[-1]
    donch_50 = df['Donchian_High_50'].iloc[-1]

    donchian_20_breakout = bool(pd.notna(donch_20) and latest_c >= donch_20)
    donchian_50_breakout = bool(pd.notna(donch_50) and latest_c >= donch_50)

    # --- VCP Volatility Contraction & Volume Dry-up Ratio ---
    latest_atr20 = df['ATR_20'].iloc[-1]
    latest_atr60 = df['ATR_60'].iloc[-1]
    vcp_ratio = round(latest_atr20 / latest_atr60, 2) if (pd.notna(latest_atr60) and latest_atr60 > 0) else 1.0
    volatility_contracting = bool(vcp_ratio <= 1.05)

    recent_20 = df.tail(20)
    up_days = recent_20[recent_20['Close'] > recent_20['Open']]
    down_days = recent_20[recent_20['Close'] <= recent_20['Open']]
    avg_up_vol = up_days['Volume'].mean() if not up_days.empty else 1.0
    avg_down_vol = down_days['Volume'].mean() if not down_days.empty else 0.0

    vol_dryup_ratio = round(avg_down_vol / avg_up_vol, 2) if avg_up_vol > 0 else 1.0
    volume_dryup = bool(vol_dryup_ratio < 1.0)
    vcp_active = bool(volatility_contracting and volume_dryup)

    # --- EMA Bounce / Retest Detection ---
    latest_ema20 = df['EMA_20'].iloc[-1]
    latest_ema50 = df['EMA_50'].iloc[-1]
    latest_vol = df['Volume'].iloc[-1]
    vol_20d_avg = df['Volume_20D_Avg'].iloc[-1]

    vol_above_avg = bool(pd.notna(vol_20d_avg) and latest_vol >= 1.1 * vol_20d_avg)
    near_ema20 = bool(pd.notna(latest_ema20) and abs(latest_c - latest_ema20) / latest_ema20 <= 0.015)
    near_ema50 = bool(pd.notna(latest_ema50) and abs(latest_c - latest_ema50) / latest_ema50 <= 0.020)

    ema20_bounce = bool(near_ema20 and vol_above_avg)
    ema50_bounce = bool(near_ema50 and vol_above_avg)

    # Assign scalar technical metrics to latest bar
    df['VCP_Ratio'] = vcp_ratio
    df['Volume_DryUp_Ratio'] = vol_dryup_ratio
    df['Volume_DryUp'] = volume_dryup
    df['VCP_Active'] = vcp_active

    df['EMA20_Bounce'] = ema20_bounce
    df['EMA50_Bounce'] = ema50_bounce
    df['Donchian_20_Breakout'] = donchian_20_breakout
    df['Donchian_50_Breakout'] = donchian_50_breakout

    # --- Relative Strength (RS) vs Nifty 50 Benchmark ---
    close_series = df['Close']
    c_21d = close_series.iloc[-22] if len(close_series) >= 22 else close_series.iloc[0]
    c_63d = close_series.iloc[-64] if len(close_series) >= 64 else close_series.iloc[0]
    c_126d = close_series.iloc[-127] if len(close_series) >= 127 else close_series.iloc[0]

    s_perf_1m = float((latest_c - c_21d) / c_21d * 100.0)
    s_perf_3m = float((latest_c - c_63d) / c_63d * 100.0)
    s_perf_6m = float((latest_c - c_126d) / c_126d * 100.0)

    rs_1m = round(s_perf_1m - nifty_returns.get("1m", 0.0), 2)
    rs_3m = round(s_perf_3m - nifty_returns.get("3m", 0.0), 2)
    rs_6m = round(s_perf_6m - nifty_returns.get("6m", 0.0), 2)
    rs_composite = round(0.5 * rs_3m + 0.3 * rs_1m + 0.2 * rs_6m, 2)

    df['RS_1M'] = rs_1m
    df['RS_3M'] = rs_3m
    df['RS_6M'] = rs_6m
    df['RS_Score'] = rs_composite

    return df


def evaluate_swing_criteria(latest_row: pd.Series) -> Dict[str, Any]:
    """
    Evaluate multi-strategy swing trading criteria:
    1. Uptrend: Price > 50 EMA > 200 EMA
    2. Relative Strength vs Nifty 50: RS_3M > 0 or RS_Score > 0
    3. Multi-Strategy Active: VCP Contraction OR Donchian Breakout OR EMA Bounce OR RS Momentum
    4. Liquidity: 20-day Average Daily Turnover >= ₹2 Crore
    5. Step 11 Volume & Price Confirmation: Volume_Ratio_20 >= 2.0x AND Close > EMA20
    """
    close = latest_row['Close']
    ema20 = latest_row['EMA_20']
    ema50 = latest_row['EMA_50']
    ema200 = latest_row['EMA_200']
    rsi14 = latest_row['RSI_14']
    turnover = latest_row['Turnover_20D_Avg']

    vcp_ratio = latest_row['VCP_Ratio']
    vol_dryup_ratio = latest_row['Volume_DryUp_Ratio']
    volume_dryup = latest_row['Volume_DryUp']
    vcp_active = latest_row['VCP_Active']

    ema20_bounce = latest_row.get('EMA20_Bounce', False)
    ema50_bounce = latest_row.get('EMA50_Bounce', False)
    donchian_20_bk = latest_row.get('Donchian_20_Breakout', False)
    donchian_50_bk = latest_row.get('Donchian_50_Breakout', False)

    rs_1m = latest_row['RS_1M']
    rs_3m = latest_row['RS_3M']
    rs_6m = latest_row['RS_6M']
    rs_score = latest_row['RS_Score']

    # Strategy signals
    uptrend_pass = bool(pd.notna(close) and pd.notna(ema50) and pd.notna(ema200) and (close > ema50 > ema200))
    rs_pass = bool(pd.notna(rs_3m) and (rs_3m > 0 or rs_score > 0))
    vcp_pass = bool(vcp_active or (pd.notna(vcp_ratio) and vcp_ratio <= 1.05))
    breakout_pass = bool(donchian_20_bk or donchian_50_bk)
    bounce_pass = bool(near_ema20 and vol_above_avg) if 'near_ema20' in locals() else bool(ema20_bounce or ema50_bounce)
    rs_momentum_pass = bool(rs_pass and pd.notna(rsi14) and rsi14 >= 60)
    liquidity_pass = bool(pd.notna(turnover) and (turnover >= 20_000_000)) # 2 Crore

    # Step 11 Volume + Price Confirmation
    curr_vol = float(latest_row['Volume']) if pd.notna(latest_row['Volume']) else 0.0
    vol_20d_avg = float(latest_row['Volume_20D_Avg']) if (pd.notna(latest_row['Volume_20D_Avg']) and latest_row['Volume_20D_Avg'] > 0) else 0.0
    
    if vol_20d_avg > 0 and curr_vol >= 0:
        vol_ratio_20 = round(curr_vol / vol_20d_avg, 2)
        volume_confirmed = bool(vol_ratio_20 >= 2.0)
    else:
        vol_ratio_20 = None
        volume_confirmed = False

    if pd.notna(close) and pd.notna(ema20) and float(ema20) > 0:
        price_confirmed = bool(close > ema20)
    else:
        price_confirmed = False

    volume_price_confirmed = bool(volume_confirmed and price_confirmed)

    # Determine primary setup type
    setup_type = "Donchian Channel Breakout"
    if breakout_pass:
        setup_type = "Donchian Channel Breakout"
    elif vcp_pass:
        setup_type = "VCP Volatility Contraction Breakout"
    elif bounce_pass:
        setup_type = "EMA Pullback / Bounce"
    elif rs_momentum_pass:
        setup_type = "RS Momentum Breakout"

    # Candidate passes technical criteria
    strategy_pass = bool(vcp_pass or breakout_pass or bounce_pass or rs_momentum_pass)
    technical_pass = uptrend_pass and rs_pass and liquidity_pass and strategy_pass

    # Overall pass includes Step 11 confirmation
    overall_pass = technical_pass and volume_price_confirmed

    return {
        "Close": round(close, 2) if pd.notna(close) else None,
        "EMA_20": round(ema20, 2) if pd.notna(ema20) else None,
        "EMA_50": round(ema50, 2) if pd.notna(ema50) else None,
        "EMA_200": round(ema200, 2) if pd.notna(ema200) else None,
        "RSI_14": round(rsi14, 2) if pd.notna(rsi14) else None,
        "ATR_20": round(latest_row['ATR_20'], 2) if pd.notna(latest_row['ATR_20']) else None,
        "ATR_60": round(latest_row['ATR_60'], 2) if pd.notna(latest_row['ATR_60']) else None,
        "Volume_20D_Avg": vol_20d_avg,
        "Current_Volume": curr_vol,
        "Volume_Ratio_20": vol_ratio_20,
        "Volume_Confirmed": volume_confirmed,
        "Price_Confirmed": price_confirmed,
        "Volume_Price_Confirmed": volume_price_confirmed,
        "VCP_Ratio": vcp_ratio,
        "Volume_DryUp_Ratio": vol_dryup_ratio,
        "Volume_DryUp": volume_dryup,
        "VCP_Active": vcp_active,
        "EMA20_Bounce": ema20_bounce,
        "EMA50_Bounce": ema50_bounce,
        "Donchian_20_Breakout": donchian_20_bk,
        "Donchian_50_Breakout": donchian_50_bk,
        "Donchian_High_20": round(latest_row['Donchian_High_20'], 2) if pd.notna(latest_row['Donchian_High_20']) else None,
        "Donchian_High_50": round(latest_row['Donchian_High_50'], 2) if pd.notna(latest_row['Donchian_High_50']) else None,
        "RS_1M": rs_1m,
        "RS_3M": rs_3m,
        "RS_6M": rs_6m,
        "RS_Score": rs_score,
        "Turnover_Cr": round(turnover / 10_000_000, 2) if pd.notna(turnover) else 0.0,
        "Uptrend": uptrend_pass,
        "RS_Pass": rs_pass,
        "VCP_Pass": vcp_pass,
        "Breakout_Pass": breakout_pass,
        "Bounce_Pass": bounce_pass,
        "RS_Momentum_Pass": rs_momentum_pass,
        "Setup_Type": setup_type,
        "Liquidity": liquidity_pass,
        "Technical_Pass": technical_pass,
        "Passed": overall_pass
    }


def run_screener(
    symbols: Optional[List[str]] = None,
    verbose: bool = False,
    as_of_date: Optional[str] = None,
    return_diagnostics: bool = False
) -> Any:
    """
    Scan a list of symbols and return shortlisted candidates matching technical criteria.
    Ensures all symbol names are properly formatted with .NS and sliced up to as_of_date.
    Uses fast bulk downloading for multi-ticker universes.
    """
    if symbols is None:
        symbols = DEFAULT_NIFTY_SYMBOLS

    results = []
    diagnostics = {
        "universe_count": len(symbols),
        "symbols_screened": len(symbols),
        "valid_data_count": 0,
        "donchian_triggers": 0,
        "vcp_triggers": 0,
        "ema_bounce_triggers": 0,
        "rs_momentum_triggers": 0,
        "connors_rsi_triggers": 0,
        "nr7_triggers": 0,
        "total_strategy_triggers": 0,
        "unique_signal_candidates": 0,
        "rejection_reasons_summary": {}
    }

    _, nifty_returns = fetch_nifty50_benchmark(period="1y", as_of_date=as_of_date)
    ha_nifty500, ha_vix = fetch_ha_market_histories(period="2y", as_of_date=as_of_date)

    # Fast bulk fetch for multi-ticker universes
    stock_data_map = fetch_bulk_stock_data(symbols, period="2y") if len(symbols) > 5 else {}

    for idx, raw_symbol in enumerate(symbols, 1):
        clean_sym = yahoo_nse_symbol(raw_symbol)
        df = stock_data_map.get(clean_sym)
        
        if df is None or df.empty:
            df = fetch_stock_data(clean_sym, period="2y", as_of_date=as_of_date)

        if df is None or df.empty:
            diagnostics["rejection_reasons_summary"]["Insufficient Price History (<200 bars)"] = diagnostics["rejection_reasons_summary"].get("Insufficient Price History (<200 bars)", 0) + 1
            continue

        if as_of_date:
            df = df[df.index.strftime('%Y-%m-%d') <= as_of_date]

        if len(df) < 200:
            diagnostics["rejection_reasons_summary"]["Insufficient Price History (<200 bars)"] = diagnostics["rejection_reasons_summary"].get("Insufficient Price History (<200 bars)", 0) + 1
            continue

        diagnostics["valid_data_count"] += 1
        df_calc = calculate_indicators(df, nifty_returns)
        latest = df_calc.iloc[-1]
        eval_res = evaluate_swing_criteria(latest)

        # Count individual strategy triggers
        triggers_count = 0
        if eval_res.get("Breakout_Pass"):
            diagnostics["donchian_triggers"] += 1
            triggers_count += 1
        if eval_res.get("VCP_Pass"):
            diagnostics["vcp_triggers"] += 1
            triggers_count += 1
        if eval_res.get("Bounce_Pass"):
            diagnostics["ema_bounce_triggers"] += 1
            triggers_count += 1
        if eval_res.get("RS_Momentum_Pass"):
            diagnostics["rs_momentum_triggers"] += 1
            triggers_count += 1

        diagnostics["total_strategy_triggers"] += triggers_count

        if eval_res["Technical_Pass"]:
            diagnostics["unique_signal_candidates"] += 1
            result_row = {
                "Symbol": clean_sym,
                # Every price/volume field below is derived from this exact final
                # completed EOD bar.  Keep the date with the row so downstream
                # consumers never substitute the market-index date for it.
                "Data_As_Of": df_calc.index[-1].strftime('%Y-%m-%d'),
                "Close": eval_res["Close"],
                "EMA_20": eval_res["EMA_20"],
                "EMA_50": eval_res["EMA_50"],
                "EMA_200": eval_res["EMA_200"],
                "RSI_14": eval_res["RSI_14"],
                "ATR_20": eval_res["ATR_20"],
                "ATR_60": eval_res["ATR_60"],
                # Missing financial data must remain unavailable.  In particular,
                # 1.0 is not a neutral substitute for a volume ratio.
                "Volume_20D_Avg": eval_res.get("Volume_20D_Avg"),
                "Current_Volume": eval_res.get("Current_Volume"),
                "Volume_Ratio_20": eval_res.get("Volume_Ratio_20"),
                "Volume_Confirmed": eval_res.get("Volume_Confirmed", False),
                "Price_Confirmed": eval_res.get("Price_Confirmed", False),
                "Volume_Price_Confirmed": eval_res.get("Volume_Price_Confirmed", False),
                "VCP_Ratio": eval_res["VCP_Ratio"],
                "Volume_DryUp": eval_res["Volume_DryUp"],
                "VCP_Active": eval_res["VCP_Active"],
                "RS_1M": eval_res["RS_1M"],
                "RS_3M": eval_res["RS_3M"],
                "RS_6M": eval_res["RS_6M"],
                "RS_Score": eval_res["RS_Score"],
                "Setup_Type": eval_res["Setup_Type"],
                "Avg_Turnover_Cr": eval_res["Turnover_Cr"],
                "Technical_Pass": eval_res["Technical_Pass"],
                "Passed": eval_res["Passed"]
            }
            if ha_nifty500 is not None and ha_vix is not None:
                try:
                    from historical_analogs_service import HistoricalAnalogService
                    result_row.update(HistoricalAnalogService.build_causal_query_state(
                        df, ha_nifty500, ha_vix, result_row["Data_As_Of"]
                    ))
                except Exception:
                    pass
            results.append(result_row)
        else:
            if not eval_res.get("Uptrend"):
                reason = "Failed Uptrend (Price <= 50 EMA or 50 EMA <= 200 EMA)"
            elif not eval_res.get("RS_Pass"):
                reason = "Failed Relative Strength (RS <= 0 vs Nifty)"
            elif not eval_res.get("Liquidity"):
                reason = "Failed Liquidity (Turnover < ₹2 Cr)"
            else:
                reason = "Failed Technical Breakout / Pattern Trigger"
            diagnostics["rejection_reasons_summary"][reason] = diagnostics["rejection_reasons_summary"].get(reason, 0) + 1

    results_df = pd.DataFrame(results)
    if return_diagnostics:
        return results_df, diagnostics
    return results_df

# Alias for stage 1 screener
run_stage1_screener = run_screener
