"""
TRADING MVP ADAPTERS & MODULAR LAYER

Defines clean interface boundaries between:
- Market Data Provider
- Universe Provider
- Signal Engine
- Portfolio & Allocation Engine
- Risk / Position Sizing Engine
- Execution Adapter (Paper Trading / Zerodha Placeholder)
"""

import os
import datetime
import pickle
import pandas as pd
import numpy as np
from typing import List, Dict, Any, Optional, Tuple

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

from universe_engine import get_universe_as_of, get_universe_metadata
from screener import run_stage1_screener, fetch_stock_data, calculate_indicators, DEFAULT_NIFTY_SYMBOLS
from database import (
    add_paper_trade,
    get_database_diagnostics,
    get_open_trades_with_live_data,
    get_open_trades_persisted,
    get_closed_trades,
    load_portfolio_snapshots,
    save_portfolio_snapshot,
    sync_paper_trades,
    update_trade_status,
    get_portfolio_performance_summary
)

TREND_STRATEGIES = {'Donchian Channel Breakout', 'EMA Pullback / Bounce', 'RS Momentum Breakout', 'VCP Volatility Contraction Breakout'}
VOLATILITY_STRATEGIES = {'True NR7 Volatility Expansion Breakout', 'True Connors RSI Mean Reversion'}

# ------------------------------------------------------------------------------
# 1. MARKET DATA PROVIDER
# ------------------------------------------------------------------------------
class MarketDataProvider:
    def __init__(self, cache_path: Optional[str] = None):
        self.cache_path = cache_path or os.path.join(PROJECT_ROOT, "data", "ml", "step_6", "cached_ohlcv_indicators.pkl")
        self.cache_map = None
        if os.path.exists(self.cache_path):
            try:
                with open(self.cache_path, "rb") as f:
                    self.cache_map = pickle.load(f)
            except Exception:
                self.cache_map = None

    def get_index_regime(self, as_of_date: Optional[str] = None) -> Dict[str, Any]:
        """
        Fetches current Nifty 50 market regime status (Nifty vs EMA50) as of `as_of_date`.
        """
        try:
            import yfinance as yf
            ticker = yf.Ticker("^NSEI")
            df = ticker.history(period="2y", interval="1d", auto_adjust=True)
            if df.empty:
                raise ValueError("Insufficient index data")

            if as_of_date:
                df = df[df.index.strftime('%Y-%m-%d') <= as_of_date]

            if len(df) < 50:
                raise ValueError("Insufficient index bars as of date")

            close_px = float(df['Close'].iloc[-1])
            ema50 = float(df['Close'].ewm(span=50, adjust=False).mean().iloc[-1])
            dist_ema50 = (close_px - ema50) / ema50 * 100.0
            regime = "BULLISH" if close_px > ema50 else "BEARISH"
            actual_date = df.index[-1].strftime('%Y-%m-%d')
            return {
                "close": round(close_px, 2),
                "ema50": round(ema50, 2),
                "nifty_dist_ema50": round(dist_ema50, 2),
                "regime": regime,
                "data_as_of": actual_date,
                "status_text": f"Bullish (Nifty +{dist_ema50:.2f}% > 50 EMA)" if regime == "BULLISH" else f"Cautious (Nifty {dist_ema50:.2f}% <= 50 EMA)"
            }
        except Exception:
            return {
                # A market-data outage must not manufacture a bullish regime.
                # Allocation rejects an unavailable regime rather than treating
                # placeholder index values as an investable signal.
                "close": None,
                "ema50": None,
                "nifty_dist_ema50": None,
                "regime": "UNAVAILABLE",
                "data_as_of": None,
                "status_text": "Market regime unavailable — no allocation"
            }

    def get_symbol_chart_data(self, symbol: str, as_of_date: Optional[str] = None) -> pd.DataFrame:
        """
        Returns OHLCV + EMA indicators DataFrame for chart rendering.
        """
        clean_sym = symbol.replace(".NS", "")
        if self.cache_map and clean_sym in self.cache_map:
            df_cached = self.cache_map[clean_sym].copy()
            if as_of_date:
                df_cached = df_cached[df_cached.index.strftime('%Y-%m-%d') <= as_of_date]
            return df_cached
        try:
            df = fetch_stock_data(clean_sym, period="1y", as_of_date=as_of_date)
            if df is not None and not df.empty:
                df['EMA_20'] = df['Close'].ewm(span=20, adjust=False).mean()
                df['EMA_50'] = df['Close'].ewm(span=50, adjust=False).mean()
                return df
        except Exception:
            pass
        return pd.DataFrame()


# ------------------------------------------------------------------------------
# 2. UNIVERSE PROVIDER
# ------------------------------------------------------------------------------
class UniverseProvider:
    def get_universe(self, date_str: Optional[str] = None, max_symbols: Optional[int] = None) -> List[str]:
        if date_str is None:
            date_str = datetime.date.today().strftime("%Y-%m-%d")
        try:
            symbols = get_universe_as_of(date_str, mode="research")
            clean_syms = [s if s.endswith(".NS") else f"{s}.NS" for s in symbols]
            if max_symbols:
                return clean_syms[:max_symbols]
            return clean_syms
        except Exception:
            clean_default = [s if s.endswith(".NS") else f"{s}.NS" for s in DEFAULT_NIFTY_SYMBOLS]
            if max_symbols:
                return clean_default[:max_symbols]
            return clean_default

    def get_metadata(self, date_str: Optional[str] = None) -> Dict[str, Any]:
        if date_str is None:
            date_str = datetime.date.today().strftime("%Y-%m-%d")
        return get_universe_metadata(date_str)


# ------------------------------------------------------------------------------
# 3. SIGNAL ENGINE
# ------------------------------------------------------------------------------
class SignalEngine:
    def run_stage1_screening(
        self,
        symbols: List[str],
        max_scan: Optional[int] = None,
        as_of_date: Optional[str] = None,
        return_diagnostics: bool = False
    ) -> Any:
        # Full universe screening by default when max_scan is None
        if max_scan is None or max_scan >= len(symbols):
            scan_symbols = symbols
        else:
            scan_symbols = symbols[:max_scan]

        return run_stage1_screener(
            symbols=scan_symbols,
            verbose=False,
            as_of_date=as_of_date,
            return_diagnostics=return_diagnostics
        )


# ------------------------------------------------------------------------------
# 4. PORTFOLIO ALLOCATION ENGINE (7 TREND / 3 VOLATILITY BUCKET ALLOCATOR)
# ------------------------------------------------------------------------------
class PortfolioAllocationEngine:
    def __init__(self, max_positions: int = 10, max_trend: int = 7, max_vol: int = 3, initial_capital: float = 1000000.0):
        self.max_positions = max_positions
        self.max_trend = max_trend
        self.max_vol = max_vol
        self.initial_capital = initial_capital

# Sector Metadata Helper Map for Nifty Constituents
SECTOR_MAP = {
    "RELIANCE": "Energy / Oil & Gas", "TCS": "IT Services", "INFY": "IT Services", "HDFCBANK": "Banking & Financials",
    "ICICIBANK": "Banking & Financials", "BHARTIARTL": "Telecom", "SBIN": "Banking & Financials", "ITC": "FMCG",
    "LTIM": "IT Services", "LT": "Infrastructure", "HINDUNILVR": "FMCG", "AXISBANK": "Banking & Financials",
    "KOTAKBANK": "Banking & Financials", "M&M": "Automobile", "TATAMOTORS": "Automobile", "MARUTI": "Automobile",
    "SUNPHARMA": "Pharmaceuticals", "NTPC": "Power", "ONGC": "Energy / Oil & Gas", "POWERGRID": "Power",
    "TATASTEEL": "Metals & Mining", "JSWSTEEL": "Metals & Mining", "ADANIENT": "Conglomerate", "ADANIPORTS": "Ports & Logistics",
    "COALINDIA": "Mining", "BAJFINANCE": "Financial Services", "BAJAJFINSV": "Financial Services", "ASIANPAINT": "Consumer Durables",
    "TITAN": "Consumer Durables", "ULTRACEMCO": "Cement", "GRASIM": "Materials", "HCLTECH": "IT Services",
    "WIPRO": "IT Services", "TECHM": "IT Services", "NESTLEIND": "FMCG", "BRITANNIA": "FMCG",
    "CIPLA": "Pharmaceuticals", "DRREDDY": "Pharmaceuticals", "APOLLOHOSP": "Healthcare", "EICHERMOT": "Automobile",
    "HEROMOTOCO": "Automobile", "BPCL": "Energy / Oil & Gas", "IOC": "Energy / Oil & Gas", "BEL": "Defense / Capital Goods",
    "HAL": "Defense / Aerospace", "TRENT": "Retail", "ZOMATO": "Consumer Tech", "JIOFIN": "Financial Services"
}


# ------------------------------------------------------------------------------
# 4. PORTFOLIO ALLOCATION ENGINE (7 TREND / 3 VOLATILITY BUCKET ALLOCATOR)
# ------------------------------------------------------------------------------
class PortfolioAllocationEngine:
    def __init__(self, max_positions: int = 10, max_trend: int = 7, max_vol: int = 3, initial_capital: float = 1000000.0):
        self.max_positions = max_positions
        self.max_trend = max_trend
        self.max_vol = max_vol
        self.initial_capital = initial_capital

    def allocate_candidates(
        self,
        shortlist_df: pd.DataFrame,
        regime_info: Dict[str, Any],
        open_positions: List[Dict[str, Any]],
        position_sizing_mode: str = "EQUAL_WEIGHT",
        exit_rule_mode: str = "FIXED_10D",
        enabled_strategies: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        """
        Allocate portfolio slots dynamically based on strategy category (Trend vs Volatility).
        Ensures max 7 Trend and max 3 Volatility positions are filled in order of Composite Score.
        Differentiates between QUALIFIED (Volume 20D >= 2.0x & Close > EMA20) and ALLOCATED.
        Calculates exact per-trade risk/reward metrics and sector exposure.
        """
        open_syms = set(p['symbol'] for p in open_positions)
        curr_trend_cnt = sum(1 for p in open_positions if p.get('strategy_used', '') in TREND_STRATEGIES)
        curr_vol_cnt = sum(1 for p in open_positions if p.get('strategy_used', '') in VOLATILITY_STRATEGIES)

        avail_total_slots = max(0, self.max_positions - len(open_positions))
        avail_trend_slots = max(0, self.max_trend - curr_trend_cnt)
        avail_vol_slots = max(0, self.max_vol - curr_vol_cnt)

        results = []
        if shortlist_df.empty:
            return results

        # Process each signal deterministically
        for idx, row in shortlist_df.iterrows():
            sym = str(row["Symbol"])
            clean_sym = sym.replace(".NS", "")
            sector_name = SECTOR_MAP.get(clean_sym, "Capital Goods & Industrials")
            strat = row.get("Setup_Type", "Donchian Channel Breakout")

            if strat == "Donchian_Breakout":
                strat = "Donchian Channel Breakout"
            elif strat == "EMA_Bounce":
                strat = "EMA Pullback / Bounce"
            elif strat == "VCP_Contraction":
                strat = "VCP Volatility Contraction Breakout"
            elif strat == "RS_Momentum":
                strat = "RS Momentum Breakout"

            if enabled_strategies and strat not in enabled_strategies:
                continue

            strat_cat = "Volatility" if strat in VOLATILITY_STRATEGIES else "Trend"

            # Strict extraction without fake defaults
            close_raw = row.get("Close")
            if close_raw is None or pd.isna(close_raw) or float(close_raw) <= 0:
                continue
            close_px = float(close_raw)

            ema20_raw = row.get("EMA_20")
            if ema20_raw is None or pd.isna(ema20_raw) or float(ema20_raw) <= 0:
                ema20_px = None
            else:
                ema20_px = float(ema20_raw)

            vol_ratio_raw = row.get("Volume_Ratio_20")
            if vol_ratio_raw is None or pd.isna(vol_ratio_raw):
                vol_ratio = None
            else:
                vol_ratio = float(vol_ratio_raw)

            curr_vol_raw = row.get("Current_Volume")
            curr_vol = float(curr_vol_raw) if curr_vol_raw is not None and pd.notna(curr_vol_raw) else None
            vol_20d_avg_raw = row.get("Volume_20D_Avg")
            vol_20d_avg = float(vol_20d_avg_raw) if vol_20d_avg_raw is not None and pd.notna(vol_20d_avg_raw) else None

            vol_confirmed = bool(vol_ratio is not None and vol_ratio >= 2.0 and bool(row.get("Volume_Confirmed", False)))
            price_confirmed = bool(close_px is not None and ema20_px is not None and close_px > ema20_px and bool(row.get("Price_Confirmed", False)))
            vol_price_confirmed = bool(vol_confirmed and price_confirmed)

            # Runtime Assertions - Protect Against Invalid Data Contamination
            if vol_price_confirmed:
                assert vol_ratio is not None and vol_ratio >= 2.0, f"DATA CONTRACT ERROR: Qualified candidate {clean_sym} has invalid volume_ratio_20={vol_ratio}"
                assert vol_confirmed is True, f"DATA CONTRACT ERROR: Qualified candidate {clean_sym} has volume_confirmed=False"
                assert ema20_px is not None and ema20_px > 0, f"DATA CONTRACT ERROR: Qualified candidate {clean_sym} has invalid ema20={ema20_px}"
                assert close_px > ema20_px, f"DATA CONTRACT ERROR: Qualified candidate {clean_sym} has close ({close_px}) <= ema20 ({ema20_px})"
                assert price_confirmed is True, f"DATA CONTRACT ERROR: Qualified candidate {clean_sym} has price_confirmed=False"

            rs_score = float(row.get("RS_Score", 0.0)) if pd.notna(row.get("RS_Score")) else 0.0
            conviction = int(row.get("Strategy_Rank", 7))
            composite_score = round(min(0.99, max(0.35, 0.40 + (rs_score / 20.0) + (conviction / 30.0))), 2)

            is_selected = False
            rejection_reasons = []
            selection_reasons = []

            # Step 11 Qualification Checklist
            if vol_confirmed and vol_ratio is not None:
                selection_reasons.append(f"✓ Volume Confirmed ({vol_ratio:.2f}x >= 2.0x 20D Avg)")
            else:
                rejection_reasons.append(f"✗ Failed Volume Confirmation ({'N/A' if vol_ratio is None else f'{vol_ratio:.2f}x'} < 2.0x 20D Avg)")

            if price_confirmed and ema20_px is not None:
                selection_reasons.append(f"✓ Price Confirmed (Close ₹{close_px:,.2f} > 20 EMA ₹{ema20_px:,.2f})")
            else:
                rejection_reasons.append(f"✗ Failed Price Confirmation (Close ₹{close_px:,.2f} <= 20 EMA {'N/A' if ema20_px is None else f'₹{ema20_px:,.2f}'})")

            ema50_raw = row.get("EMA_50")
            if ema50_raw is not None and pd.notna(ema50_raw) and close_px > float(ema50_raw):
                selection_reasons.append("✓ Price above 50 EMA (Uptrend confirmed)")

            if rs_score > 0:
                selection_reasons.append(f"✓ Relative Strength positive (+{rs_score:.1f}% vs Nifty)")

            selection_reasons.append(f"✓ {strat} setup confirmed")
            selection_reasons.append(f"✓ Composite Score {composite_score:.2f} (Rank #{idx+1})")

            # Determine Qualification & Allocation Status
            if not vol_price_confirmed:
                if not vol_confirmed:
                    status_code = "REJECTED — VOLUME CONFIRMATION"
                    status_reason = f"Rejection: Volume ratio {'N/A' if vol_ratio is None else f'{vol_ratio:.2f}x'} below 2.0x threshold"
                else:
                    status_code = "REJECTED — PRICE CONFIRMATION"
                    status_reason = f"Rejection: Close ₹{close_px:,.2f} <= 20 EMA {'N/A' if ema20_px is None else f'₹{ema20_px:,.2f}'}"
            elif regime_info.get('regime', 'BULLISH') != "BULLISH":
                status_code = "REJECTED — REGIME"
                status_reason = "Rejection: Market regime filter failed (Nifty <= 50 EMA)"
            elif sym in open_syms or f"{sym}.NS" in open_syms:
                status_code = "REJECTED — DUPLICATE"
                status_reason = "Rejection: Duplicate active position in portfolio"
            else:
                # Setup is QUALIFIED. Now check Portfolio Allocation capacity.
                if strat_cat == "Trend" and avail_trend_slots <= 0:
                    status_code = "QUALIFIED — CAPITAL CAP"
                    status_reason = f"Qualified — Not allocated (Trend capacity max {self.max_trend} slots full)"
                elif strat_cat == "Volatility" and avail_vol_slots <= 0:
                    status_code = "QUALIFIED — CAPITAL CAP"
                    status_reason = f"Qualified — Not allocated (Volatility capacity max {self.max_vol} slots full)"
                elif len([r for r in results if r['is_selected']]) >= avail_total_slots:
                    status_code = "QUALIFIED — CAPITAL CAP"
                    status_reason = f"Qualified — Not allocated (Total portfolio max {self.max_positions} slots full)"
                else:
                    # ALLOCATED!
                    is_selected = True
                    status_code = "ALLOCATED"
                    status_reason = f"Allocated: #{len([r for r in results if r['is_selected']])+1} {strat_cat} position"
                    selection_reasons.append(f"✓ Fits {strat_cat} allocation bucket")
                    selection_reasons.append("✓ Portfolio slot available")
                    if strat_cat == "Trend":
                        avail_trend_slots -= 1
                    else:
                        avail_vol_slots -= 1

            # Position Sizing
            atr_raw = row.get("ATR_20")
            atr_available = atr_raw is not None and pd.notna(atr_raw) and float(atr_raw) > 0
            # Retain this legacy allocator fallback for its existing internal
            # calculations. Consumers must consult atr_20_available before
            # treating ATR as an executable-stop input.
            atr_val = float(atr_raw) if atr_available else close_px * 0.03
            sl_px = float(row.get("ATR_Stop_Loss", close_px - (2.0 * atr_val)))

            if position_sizing_mode == "VOLATILITY_ADJUSTED":
                target_risk_amt = self.initial_capital * 0.015
                stop_dist = 2.0 * atr_val
                qty = max(1, int(target_risk_amt / stop_dist)) if stop_dist > 0 else int(100000.0 / close_px)
                pos_size_inr = round(qty * close_px, 2)
                sizing_label = f"₹{pos_size_inr:,.0f} ({qty} shares @ 1.5% Risk / 2x ATR)"
            else:
                pos_size_inr = 100000.0
                qty = max(1, int(pos_size_inr / close_px))
                sizing_label = f"₹100,000 ({qty} shares @ Equal Weight)"

            # Risk / Reward Handling (Section 11: Do not force target if unconfigured)
            has_explicit_target = "Target_Price" in row and pd.notna(row["Target_Price"])
            if has_explicit_target:
                tp_px = float(row["Target_Price"])
                risk_per_share = round(max(0.01, close_px - sl_px), 2)
                reward_per_share = round(max(0.01, tp_px - close_px), 2)
                rr_ratio = round(reward_per_share / risk_per_share, 2) if risk_per_share > 0 else 0.0
                total_pos_risk = round(risk_per_share * qty, 2)
                total_pos_reward = round(reward_per_share * qty, 2)
                if rr_ratio >= 2.0:
                    quality_label = "⭐ Excellent R:R"
                elif rr_ratio >= 1.5:
                    quality_label = "👍 Good R:R"
                elif rr_ratio >= 1.0:
                    quality_label = "👌 Acceptable R:R"
                else:
                    quality_label = "⚠️ Weak R:R"
            else:
                tp_px = None
                risk_per_share = round(max(0.01, close_px - sl_px), 2)
                reward_per_share = None
                rr_ratio = None
                total_pos_risk = round(risk_per_share * qty, 2)
                total_pos_reward = None
                quality_label = "Target: Not configured"

            # Exit Rule Label
            if exit_rule_mode == "ATR_TRAILING":
                exit_label = f"2.5x ATR Trailing Stop (Max 10 days)"
            elif exit_rule_mode == "TIME_DECAY":
                exit_label = f"Day 3 Time-Decay Exit (Max 10 days)"
            else:
                exit_label = f"10-Day Fixed Holding Period"

            # The candidate's date is the date of the OHLCV bar used to compute
            # its close, EMA20 and volume ratio—not the index-regime date and not
            # the wall-clock analysis date.
            row_data_as_of = row.get("Data_As_Of")
            if row_data_as_of is None or pd.isna(row_data_as_of):
                row_data_as_of = None
            else:
                row_data_as_of = str(row_data_as_of)[:10]

            results.append({
                "rank": idx + 1,
                "symbol": clean_sym,
                "sector": sector_name,
                "strategy": strat,
                "strategy_category": strat_cat,
                "signal_date": row_data_as_of,
                "data_as_of": row_data_as_of,
                "composite_score": composite_score,
                "signal_strength": "HIGH" if composite_score >= 0.70 else "MEDIUM",
                "close": round(close_px, 2),
                "entry_price": round(close_px, 2),
                "stop_loss": round(sl_px, 2),
                "target_price": round(tp_px, 2) if tp_px is not None else None,
                "ema20": round(ema20_px, 2) if ema20_px is not None else None,
                "ema_20": round(ema20_px, 2) if ema20_px is not None else None,
                "current_volume": curr_vol,
                "volume_20d_avg": vol_20d_avg,
                "volume_ratio_20": round(vol_ratio, 2) if vol_ratio is not None else None,
                "volume_confirmed": vol_confirmed,
                "price_confirmed": price_confirmed,
                "volume_price_confirmed": vol_price_confirmed,
                "atr_20": round(atr_val, 2),
                "atr_20_available": atr_available,
                "risk_per_share": risk_per_share,
                "reward_per_share": reward_per_share,
                "risk_reward_ratio": rr_ratio,
                "total_position_risk": total_pos_risk,
                "total_position_reward": total_pos_reward,
                "trade_quality_label": quality_label,
                "suggested_position_size": pos_size_inr,
                "position_size_label": sizing_label,
                "quantity": qty,
                "expected_holding_period": exit_label,
                "regime": regime_info.get('regime', 'BULLISH'),
                "is_selected": is_selected,
                "is_qualified": vol_price_confirmed,
                "status": status_code,
                "selection_reasons": selection_reasons,
                "rejection_reasons": rejection_reasons,
                "reason_text": status_reason
            })

        return results


# ------------------------------------------------------------------------------
# 5. EXECUTION ADAPTER (Paper Trading / Broker Placeholder)
# ------------------------------------------------------------------------------
class ExecutionAdapter:
    def get_broker_status(self) -> Dict[str, Any]:
        return {
            "broker_name": "Zerodha Kite Connect API",
            "is_connected": False,
            "status_text": "NOT CONNECTED — Manual Confirmation / Paper Mode Active 🛑",
            "mode": "RESEARCH / PAPER TRADING"
        }

    def execute_paper_trade(self, candidate: Dict[str, Any]) -> Dict[str, Any]:
        try:
            reference_available = candidate.get("risk_reference_available") is True
            executable_enabled = candidate.get("executable_stop_enabled") is True
            entry = candidate["entry_price"]
            quantity = candidate.get("quantity", max(1, int(100000.0 / entry)))
            reference_per_share = candidate.get("reference_risk_per_share") if reference_available else None
            executable_per_share = candidate.get("executable_risk_per_share") if executable_enabled else None
            metadata = {
                "risk_contract_version": "F2_V1",
                "allocation_status": candidate.get("allocation_status"),
                "opportunity_reference": candidate.get("opportunity_id") or f"{candidate.get('symbol')}:{candidate.get('signal_date')}",
                "risk_reference_type": candidate.get("risk_reference_type"),
                "risk_reference_value": candidate.get("risk_reference_value") if reference_available and isinstance(candidate.get("risk_reference_value"), (int, float)) else None,
                "risk_reference_available": reference_available,
                "reference_risk_per_share": reference_per_share if isinstance(reference_per_share, (int, float)) else None,
                "reference_risk_rupees": (reference_per_share * quantity) if isinstance(reference_per_share, (int, float)) else None,
                "executable_stop_enabled": executable_enabled,
                "initial_executable_stop": candidate.get("initial_executable_stop") if executable_enabled else None,
                "executable_risk_per_share": executable_per_share if isinstance(executable_per_share, (int, float)) else None,
                "executable_risk_rupees": candidate.get("executable_stop_risk_inr") if executable_enabled and isinstance(candidate.get("executable_stop_risk_inr"), (int, float)) else None,
                "gap_risk_possible": candidate.get("gap_risk_possible") is True,
                "target_status": "NOT_AVAILABLE",
            }
            trade = add_paper_trade(
                symbol=candidate["symbol"],
                strategy_used=candidate["strategy"],
                entry_price=entry,
                quantity=quantity,
                stop_loss=candidate.get("initial_executable_stop") if executable_enabled else None,
                target=None,
                sector=candidate.get("sector", "General"),
                risk_metadata=metadata,
            )
            self.save_portfolio_snapshot("PAPER_TRADE_RECORDED")
            return {
                "success": True,
                "trade_id": trade.id,
                "message": f"Successfully recorded paper trade #{trade.id} for {candidate['symbol']} ({candidate['strategy']})"
            }
        except Exception:
            return {"success": False, "message": "Paper-trade storage is unavailable. No trade was recorded."}

    def get_open_positions(self) -> List[Dict[str, Any]]:
        # Navigation reads the durable ledger only. Provider marks are explicit.
        return get_open_trades_persisted()

    def refresh_open_positions(self, source_run_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """The only position-read path allowed to request current market prices."""
        return get_open_trades_with_live_data(source_run_id=source_run_id)

    def get_closed_positions(self) -> List[Dict[str, Any]]:
        return get_closed_trades()

    def sync_live_prices(self) -> Dict[str, Any]:
        result = sync_paper_trades()
        self.save_portfolio_snapshot("PORTFOLIO_REFRESH")
        return result

    def refresh_portfolio_positions(self, source_run_id: Optional[str] = None) -> Dict[str, Any]:
        """Explicit user action: sync frozen legacy lifecycle, then fetch fresh marks."""
        sync_result = self.sync_live_prices()
        return {**sync_result, "positions": self.refresh_open_positions(source_run_id=source_run_id)}

    def close_paper_trade(self, trade_id: int, exit_price: float) -> Dict[str, Any]:
        """Persist a user-confirmed manual paper close; no exit is inferred."""
        try:
            if not isinstance(exit_price, (int, float)) or exit_price <= 0:
                return {"success": False, "message": "A positive manual exit price is required."}
            trade = update_trade_status(trade_id, "CLOSED_MANUAL", float(exit_price))
            if not trade:
                return {"success": False, "message": "Paper trade was not found or could not be closed."}
            self.save_portfolio_snapshot("PAPER_TRADE_CLOSED")
            return {"success": True, "trade_id": trade.id, "message": f"Paper trade #{trade.id} closed manually."}
        except Exception:
            return {"success": False, "message": "Paper-trade storage is unavailable. No close was recorded."}

    def database_diagnostics(self) -> Dict[str, str]:
        return get_database_diagnostics()

    def save_portfolio_snapshot(self, reason: str) -> Dict[str, Any]:
        """Persist existing portfolio semantics only after a meaningful action."""
        try:
            from live_decision_adapter import summarize_live_portfolio_risk
            summary = self.get_portfolio_summary()
            positions = self.get_open_positions()
            risk = summarize_live_portfolio_risk(positions)
            unrealized = sum(p.get("unrealized_pnl_inr") or 0.0 for p in positions)
            return save_portfolio_snapshot({
                "portfolio_equity": summary.get("total_portfolio_value_inr"),
                "cash": summary.get("current_cash_inr"),
                "deployed_capital": summary.get("open_capital_deployed_inr"),
                "realized_pnl": summary.get("total_realized_pnl_inr"),
                "unrealized_pnl": unrealized,
                "open_positions": summary.get("open_positions_count"),
                "reference_heat_pct": risk.get("reference_heat_pct_value"),
                "reference_heat_coverage_count": risk.get("positions_with_reference"),
                "reference_heat_missing_count": risk.get("positions_without_reference"),
                "executable_stop_heat_pct": risk.get("executable_stop_heat_pct_value"),
                "executable_stop_coverage_count": risk.get("positions_with_executable_stop"),
                "price_coverage_count": summary.get("price_coverage_count"),
                "price_coverage_total": summary.get("price_coverage_total"),
            }, reason)
        except Exception:
            return {"saved": False, "snapshot_id": None}

    def get_portfolio_snapshots(self) -> List[Any]:
        return load_portfolio_snapshots()

    def get_portfolio_summary(self) -> Dict[str, Any]:
        raw = get_portfolio_performance_summary()
        initial_cap = 1000000.0
        open_capital = float(raw.get("open_capital_deployed", 0.0))
        realized_pnl = float(raw.get("total_realized_pnl", 0.0))
        persisted_positions = self.get_open_positions()
        valid_marks = [position for position in persisted_positions if isinstance(position.get("current_price"), (int, float))]
        unrealized_pnl = round(sum(position.get("unrealized_pnl_inr") or 0.0 for position in valid_marks), 2)
        cash = round(initial_cap - open_capital + realized_pnl, 2)
        total_val = round(cash + open_capital + unrealized_pnl, 2)
        net_ret = round((total_val - initial_cap) / initial_cap * 100.0, 2)

        return {
            "total_portfolio_value_inr": total_val,
            "current_cash_inr": cash,
            "cash_inr": cash,
            "invested_capital_inr": open_capital,
            "open_capital_deployed_inr": open_capital,
            "open_positions_count": raw.get("open_trades_count", 0),
            "closed_positions_count": raw.get("closed_trades_count", 0),
            "total_net_pnl_inr": round(realized_pnl, 2),
            "total_net_return_pct": net_ret,
            "unrealized_pnl_inr": unrealized_pnl if valid_marks else None,
            "price_coverage_count": len(valid_marks),
            "price_coverage_total": len(persisted_positions),
            "win_rate_pct": raw.get("win_rate_pct", 0.0),
            "raw_summary": raw
        }
