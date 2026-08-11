import os
import json
import asyncio
import pandas as pd
import pandas_ta as ta
import numpy as np
from typing import List, Dict, Any, Optional, Tuple
from pydantic import BaseModel, Field

import universe_engine

from screener import fetch_nifty50_benchmark
from fundamentals_agent import fetch_screener_fundamentals, analyze_fundamentals_async, FundamentalAnalysisResult
from sentiment_agent import fetch_google_news_rss, analyze_sentiment_async, SentimentAnalysisResult

# Check if google-genai SDK is available
try:
    from google import genai
    from google.genai import types
    GENAI_AVAILABLE = True
except ImportError:
    GENAI_AVAILABLE = False


# Sector Mapping Dictionary for Nifty Stocks
SECTOR_MAP = {
    "TCS.NS": "IT", "INFY.NS": "IT", "PERSISTENT.NS": "IT", "HCLTECH.NS": "IT", "WIPRO.NS": "IT",
    "TECHM.NS": "IT", "COFORGE.NS": "IT", "LTTS.NS": "IT", "MPHASIS.NS": "IT", "TATAELXSI.NS": "IT", "OFSS.NS": "IT",
    "HDFCBANK.NS": "Banking & Financials", "ICICIBANK.NS": "Banking & Financials", "AXISBANK.NS": "Banking & Financials",
    "SBIN.NS": "Banking & Financials", "KOTAKBANK.NS": "Banking & Financials", "BAJFINANCE.NS": "Banking & Financials",
    "CHOLAFIN.NS": "Banking & Financials", "SHRIRAMFIN.NS": "Banking & Financials", "MUTHOOTFIN.NS": "Banking & Financials",
    "JIOFIN.NS": "Banking & Financials", "PFC.NS": "Banking & Financials", "REC.NS": "Banking & Financials", "INDUSINDBK.NS": "Banking & Financials",
    "TATAMOTORS.NS": "Automobiles", "M&M.NS": "Automobiles", "MARUTI.NS": "Automobiles", "HEROMOTOCO.NS": "Automobiles",
    "EICHERMOT.NS": "Automobiles", "BAJAJ-AUTO.NS": "Automobiles", "BHARATFORG.NS": "Automobiles", "BALKRISIND.NS": "Automobiles", "APOLLOTYRE.NS": "Automobiles", "TIINDIA.NS": "Automobiles",
    "LT.NS": "Industrials & Defense", "HAL.NS": "Industrials & Defense", "BEL.NS": "Industrials & Defense",
    "SIEMENS.NS": "Industrials & Defense", "ABB.NS": "Industrials & Defense", "POLYCAB.NS": "Industrials & Defense",
    "DIXON.NS": "Industrials & Defense", "CUMMINSIND.NS": "Industrials & Defense", "CGPOWER.NS": "Industrials & Defense", "MAZDOCK.NS": "Industrials & Defense", "RVNL.NS": "Industrials & Defense", "BHEL.NS": "Industrials & Defense",
    "RELIANCE.NS": "Energy & Power", "NTPC.NS": "Energy & Power", "POWERGRID.NS": "Energy & Power",
    "COALINDIA.NS": "Energy & Power", "ONGC.NS": "Energy & Power", "TATAPOWER.NS": "Energy & Power", "BPCL.NS": "Energy & Power", "IOC.NS": "Energy & Power", "GAIL.NS": "Energy & Power", "ADANIENT.NS": "Energy & Power", "ADANIPORTS.NS": "Energy & Power",
    "SUNPHARMA.NS": "Pharma & Healthcare", "CIPLA.NS": "Pharma & Healthcare", "DRREDDY.NS": "Pharma & Healthcare",
    "DIVISLAB.NS": "Pharma & Healthcare", "APOLLOHOSP.NS": "Pharma & Healthcare", "MANKIND.NS": "Pharma & Healthcare", "MAXHEALTH.NS": "Pharma & Healthcare", "LALPATHLAB.NS": "Pharma & Healthcare",
    "HINDUNILVR.NS": "FMCG & Retail", "ITC.NS": "FMCG & Retail", "NESTLEIND.NS": "FMCG & Retail",
    "TITAN.NS": "FMCG & Retail", "TRENT.NS": "FMCG & Retail", "VBL.NS": "FMCG & Retail", "BRITANNIA.NS": "FMCG & Retail", "DABUR.NS": "FMCG & Retail", "GODREJCP.NS": "FMCG & Retail", "PIDILITIND.NS": "FMCG & Retail",
    "JSWSTEEL.NS": "Metals & Mining", "TATASTEEL.NS": "Metals & Mining", "HINDALCO.NS": "Metals & Mining", "VEDL.NS": "Metals & Mining", "JINDALSTEL.NS": "Metals & Mining", "NMDC.NS": "Metals & Mining",
    "ULTRACEMCO.NS": "Realty & Construction", "GRASIM.NS": "Realty & Construction", "DLF.NS": "Realty & Construction", "GODREJPROP.NS": "Realty & Construction", "AMBUJACEM.NS": "Realty & Construction", "OBEROIRAL.NS": "Realty & Construction", "LODHA.NS": "Realty & Construction",
    "BHARTIARTL.NS": "Telecom & Chemicals", "SRF.NS": "Telecom & Chemicals", "DEEPAKNTR.NS": "Telecom & Chemicals", "NAVINFLUOR.NS": "Telecom & Chemicals", "PIIND.NS": "Telecom & Chemicals", "TATACOMM.NS": "Telecom & Chemicals"
}


# --- MODEL HELPER WITH MODEL FALLBACK ---

async def async_generate_content_with_fallback(
    client: Any,
    contents: str,
    system_instruction: str,
    response_schema: Optional[Any],
    primary_model: str,
    fallback_model: str,
    temperature: float = 0.2
) -> Optional[str]:
    """
    Call Gemini API with primary_model (e.g. gemini-3.5-flash or gemini-3.1-pro-preview),
    falling back to fallback_model (e.g. gemini-2.5-flash or gemini-2.5-pro) if an endpoint error occurs.
    """
    models_to_try = [primary_model, fallback_model]
    for model_name in models_to_try:
        try:
            config_kwargs = {
                "system_instruction": system_instruction,
                "temperature": temperature
            }
            if response_schema is not None:
                config_kwargs["response_mime_type"] = "application/json"
                config_kwargs["response_schema"] = response_schema

            response = await client.aio.models.generate_content(
                model=model_name,
                contents=contents,
                config=types.GenerateContentConfig(**config_kwargs)
            )
            if response.text:
                return response.text
        except Exception as e:
            print(f"Gemini API model '{model_name}' unavailable or failed: {e}. Trying fallback...")
    return None


class TechnicalAnalysisResult(BaseModel):
    symbol: str = Field(description="NSE Ticker Symbol with .NS extension")
    strategy_type: str = Field(description="Identified technical strategy: 'VCP Volatility Contraction Breakout', 'EMA Pullback / Bounce', 'RS Momentum Breakout', or 'Donchian Channel Breakout'")
    tech_conviction_normalized: float = Field(description="Normalized technical conviction score strictly between 0.0 (weakest) and 1.0 (strongest)")
    entry_range: str = Field(description="Recommended entry price range in INR (e.g. '1420.00 - 1430.00')")
    stop_loss_level: float = Field(description="Pivot or ATR-based Stop Loss price level in INR")
    target_rr: float = Field(default=2.6, description="Target Risk-Reward ratio relative to entry (minimum 2.5)")
    technical_reasoning: str = Field(description="Detailed multi-strategy technical justification")


class TradeDecision(BaseModel):
    symbol: str = Field(description="NSE Ticker Symbol with .NS extension")
    sector: str = Field(default="General", description="Industry Sector Classification")
    setup_type: str = Field(description="Identified swing setup (e.g. VCP Breakout, EMA Pullback, RS Momentum, Donchian Breakout)")
    entry_range: str = Field(description="Recommended entry price range in INR (e.g. '1420.00 - 1430.00')")
    stop_loss: float = Field(description="Dynamic 2.0x ATR or pivot-based Stop Loss price in INR")
    target: float = Field(description="Target price giving minimum 1:2.5 Risk-Reward ratio")
    tech_conviction_normalized: float = Field(default=0.85, description="Normalized technical conviction score between 0.0 and 1.0")
    conviction_score: int = Field(description="Overall Multi-Agent Conviction score from 1 (lowest) to 10 (highest)")
    fundamental_score: int = Field(default=7, description="Fundamental Health Score from 1 (poor) to 10 (exceptional)")
    sentiment_score: float = Field(default=0.0, description="News Sentiment Score between -1.0 and +1.0")
    sentiment_classification: str = Field(default="Neutral", description="News sentiment classification (Bullish/Neutral/Bearish)")
    market_regime: str = Field(default="Bullish", description="Overall Market Regime (Bullish: Nifty > 50 EMA, Cautious: Nifty < 50 EMA)")
    priority_rank: int = Field(default=1, description="Portfolio Manager Priority Rank (1 = Top Priority)")
    market_narrative: List[str] = Field(default_factory=list, description="Bulleted summary of key market news narrative and catalysts")
    red_flags: List[str] = Field(default_factory=list, description="Identified fundamental, sentiment, or risk red flags")
    position_size: int = Field(description="Calculated quantity of shares based on risk limits, sector caps, and market regime")
    reasoning: str = Field(description="Comprehensive multi-agent decision thesis combining technicals, fundamentals, sentiment, and risk rules")


# --- AGENT SYSTEM PROMPTS ---

TECHNICAL_ANALYST_SYSTEM_PROMPT = """
You are a Senior Quantitative & Technical Analyst Agent specializing in Nifty 500 Multi-Strategy Swing Trading.
Your task is to dynamically analyze technical indicators across 4 core swing strategies:
1. Volatility Contraction Pattern (VCP): Checks ATR20/ATR60 ratio <= 1.05 and volume dry-up ratio < 1.0 on pullbacks.
2. EMA Bounce / Pullback: Price retesting 20 EMA or 50 EMA with volume expansion.
3. Relative Strength (RS) Momentum: RS score vs Nifty 50 > 0 with RSI >= 60 in a strong uptrend (Price > 50 EMA > 200 EMA).
4. Donchian Channel Breakout: Price breaking out above 20-day or 50-day highs on strong volume.

Analyze the candidate's metrics and output:
- strategy_type: The single primary or dominant strategy setup identified.
- tech_conviction_normalized: A normalized score strictly between 0.0 (weakest) and 1.0 (strongest).
- entry_range: Tight entry zone in INR.
- stop_loss_level: Technical pivot or ATR-based stop loss level.
- target_rr: Risk-Reward ratio (>= 2.5).
- technical_reasoning: High-conviction technical justification.
"""

PORTFOLIO_MANAGER_SYSTEM_PROMPT = """
You are the Chief Investment Officer & Portfolio Manager Agent.
Your objective is to aggregate findings from the Technical Analyst, Fundamentals Agent, Sentiment Agent, and Risk Manager:
1. Synthesize all agent inputs into a cohesive investment thesis.
2. Ensure position size adheres strictly to Risk Manager gatekeeper rules.
3. Assign a Priority Rank (1 = highest conviction trade) and an Overall Multi-Agent Conviction Score (1-10).
4. Return a structured TradeDecision output matching the schema.
"""


# --- AGENT 1: MARKET DATA AGENT ---

def get_market_regime() -> Dict[str, Any]:
    """
    Market Data Agent helper: Checks Nifty 50 index (^NSEI) close relative to its 50-day EMA.
    Returns market regime status (Bullish vs Cautious).
    """
    try:
        nifty_df, returns = fetch_nifty50_benchmark(period="1y")
        if nifty_df is not None and not nifty_df.empty and len(nifty_df) >= 50:
            nifty_df['EMA_50'] = ta.ema(nifty_df['Close'], length=50)
            latest_c = nifty_df['Close'].iloc[-1]
            latest_ema50 = nifty_df['EMA_50'].iloc[-1]

            is_below_50ema = bool(latest_c < latest_ema50)
            regime_str = "Cautious (Nifty < 50 EMA)" if is_below_50ema else "Bullish (Nifty > 50 EMA)"

            return {
                "below_50ema": is_below_50ema,
                "regime_str": regime_str,
                "nifty_close": round(latest_c, 2),
                "nifty_ema50": round(latest_ema50, 2),
                "returns": returns
            }
    except Exception as e:
        print(f"Market Data Agent regime check error: {e}")

    return {
        "below_50ema": False,
        "regime_str": "Bullish (Nifty > 50 EMA)",
        "nifty_close": 0.0,
        "nifty_ema50": 0.0,
        "returns": {"1m": 0.0, "3m": 0.0, "6m": 0.0}
    }


# --- AGENT 2: TECHNICAL ANALYST AGENT (MULTI-STRATEGY) ---

async def run_technical_analyst_agent_async(
    symbol: str,
    row: Dict[str, Any],
    api_key: Optional[str] = None
) -> TechnicalAnalysisResult:
    """
    Technical Analyst Agent: Dynamically evaluates multi-strategy indicators (VCP, EMA Bounce, RS Momentum, Donchian Breakout)
    and outputs a normalized technical conviction score (0.0 to 1.0) and detected strategy type.
    Uses 'gemini-3.5-flash' (with fallback to 'gemini-2.5-flash').
    """
    close = float(row.get('Close', 0.0))
    ema20 = float(row.get('EMA_20', close * 0.99))
    ema50 = float(row.get('EMA_50', close * 0.98))
    ema200 = float(row.get('EMA_200', close * 0.95))
    rsi = float(row.get('RSI_14', 50.0))

    vcp_ratio = float(row.get('VCP_Ratio', 1.0))
    vol_dryup_ratio = float(row.get('Volume_DryUp_Ratio', 1.0))
    vcp_active = bool(row.get('VCP_Active', False))

    ema20_bounce = bool(row.get('EMA20_Bounce', False))
    ema50_bounce = bool(row.get('EMA50_Bounce', False))
    donchian_20_bk = bool(row.get('Donchian_20_Breakout', False))
    donchian_50_bk = bool(row.get('Donchian_50_Breakout', False))

    rs_1m = float(row.get('RS_1M', 0.0))
    rs_3m = float(row.get('RS_3M', 0.0))
    rs_6m = float(row.get('RS_6M', 0.0))
    rs_score = float(row.get('RS_Score', 0.0))

    # Algorithmic multi-strategy rule detection for fallback
    if donchian_50_bk or donchian_20_bk:
        rule_strategy = "Donchian Channel Breakout"
        rule_score = 0.92 if donchian_50_bk else 0.85
    elif vcp_active and vcp_ratio <= 0.95:
        rule_strategy = "VCP Volatility Contraction Breakout"
        rule_score = 0.90
    elif ema20_bounce or ema50_bounce:
        rule_strategy = "EMA Pullback / Bounce"
        rule_score = 0.85
    elif rs_score > 10.0 and rsi >= 60:
        rule_strategy = "RS Momentum Breakout"
        rule_score = 0.88
    elif close <= ema50 * 1.02:
        rule_strategy = "EMA Pullback / Bounce"
        rule_score = 0.78
    else:
        rule_strategy = "VCP Volatility Contraction Breakout"
        rule_score = 0.75

    entry_low = round(close * 0.995, 2)
    entry_high = round(close * 1.005, 2)
    entry_range_str = f"{entry_low:.2f} - {entry_high:.2f}"
    stop_loss_val = round(close * 0.965, 2)

    rule_fallback = TechnicalAnalysisResult(
        symbol=symbol,
        strategy_type=rule_strategy,
        tech_conviction_normalized=rule_score,
        entry_range=entry_range_str,
        stop_loss_level=stop_loss_val,
        target_rr=2.6,
        technical_reasoning=f"{symbol} exhibits strong multi-strategy alignment ({rule_strategy}) with RS +{rs_3m:.1f}% vs Nifty 50."
    )

    if not api_key:
        api_key = os.environ.get("GEMINI_API_KEY")

    if not api_key or not GENAI_AVAILABLE:
        return rule_fallback

    try:
        client = genai.Client(api_key=api_key)
        prompt = f"""
Evaluate the following technical data for {symbol} across 4 swing strategies (VCP, EMA Bounce, RS Momentum, Donchian Breakout):
- Close: ₹{close} | 20 EMA: ₹{ema20} | 50 EMA: ₹{ema50} | 200 EMA: ₹{ema200}
- 14 RSI: {rsi}
- VCP Contraction Ratio (ATR20/ATR60): {vcp_ratio} | Volume Dry-up Ratio: {vol_dryup_ratio} (VCP Active: {vcp_active})
- Donchian 20-Day Breakout: {donchian_20_bk} | Donchian 50-Day Breakout: {donchian_50_bk}
- EMA 20 Bounce: {ema20_bounce} | EMA 50 Bounce: {ema50_bounce}
- Relative Strength vs Nifty 50 (1M/3M/6M): {rs_1m}% / {rs_3m}% / {rs_6m}% (Composite RS: {rs_score})

Output strategy_type, normalized tech_conviction_normalized (0.0 to 1.0), entry_range, stop_loss_level, target_rr, and reasoning.
"""
        json_str = await async_generate_content_with_fallback(
            client=client,
            contents=prompt,
            system_instruction=TECHNICAL_ANALYST_SYSTEM_PROMPT,
            response_schema=TechnicalAnalysisResult,
            primary_model='gemini-3.5-flash',
            fallback_model='gemini-2.5-flash'
        )

        if json_str:
            res_dict = json.loads(json_str)
            return TechnicalAnalysisResult(**res_dict)
        else:
            return rule_fallback
    except Exception as e:
        print(f"Technical Analyst Agent LLM error for {symbol}: {e}")
        return rule_fallback


# --- AGENT 5: RISK MANAGER AGENT (GATEKEEPER & POSITION SIZER) ---

def run_risk_manager_gatekeeper(
    symbol: str,
    row_data: Dict[str, Any],
    fund_res: FundamentalAnalysisResult,
    sent_res: SentimentAnalysisResult,
    tech_res: TechnicalAnalysisResult,
    market_regime: Dict[str, Any],
    sector_exposure_capital: Dict[str, float],
    portfolio_capital: float = 1_000_000.0,
    max_risk_pct: float = 0.02
) -> Tuple[bool, List[str], float, int, str]:
    """
    Risk Manager Agent enforcing strict risk controls:
    1. Rejects stocks with negative news sentiment (< -0.3).
    2. Rejects stocks with low fundamental health scores (< 5/10).
    3. Calculates ATR-based dynamic stop loss (2.0x 20-day ATR below entry).
    4. Adjusts risk per trade based on Market Regime (if Nifty < 50 EMA, reduce risk from 2% to 1%).
    5. Enforces max 20% sector exposure across approved portfolio trades.

    Returns: (approved: bool, rejection_reasons: List[str], dynamic_stop_loss: float, position_size: int, sector: str)
    """
    reasons = []
    sector = universe_engine.get_sector(symbol) or SECTOR_MAP.get(symbol, "Unknown")

    # Rule 1: Reject negative sentiment (< -0.3)
    if sent_res.sentiment_score < -0.3:
        reasons.append(f"REJECTED by Risk Manager: Bearish news sentiment ({sent_res.sentiment_score:.2f} < -0.3)")

    # Rule 2: Reject low fundamental score (< 5/10)
    if fund_res.fundamental_score < 5:
        reasons.append(f"REJECTED by Risk Manager: Low Fundamental Health Score ({fund_res.fundamental_score}/10 < 5)")

    if reasons:
        return False, reasons, 0.0, 0, sector

    close = float(row_data.get('Close', 0.0))
    atr20 = float(row_data.get('ATR_20', close * 0.03)) if row_data.get('ATR_20') else close * 0.03

    # Rule 3: Dynamic 2.0x ATR Stop Loss
    atr_stop_loss = round(close - (2.0 * atr20), 2)
    min_stop_loss = round(close * 0.97, 2)
    dynamic_stop_loss = min(atr_stop_loss, min_stop_loss, tech_res.stop_loss_level)

    # Rule 4: Market Regime Risk Adjustment
    effective_risk_pct = max_risk_pct
    if market_regime.get('below_50ema', False):
        effective_risk_pct = max_risk_pct * 0.5  # Scale down risk to 1% in cautious regime
        reasons.append("Cautious Market Regime: Risk per trade scaled down by 50%")

    risk_amount = portfolio_capital * effective_risk_pct
    risk_per_share = max(close - dynamic_stop_loss, close * 0.005)

    quantity = int(risk_amount // risk_per_share)

    # Cap single trade value to 20% of portfolio
    max_trade_val = portfolio_capital * 0.20
    if quantity * close > max_trade_val:
        quantity = int(max_trade_val // close)

    # Rule 5: Sector Risk Cap (Max 20% portfolio capital per sector)
    current_sector_allocated = sector_exposure_capital.get(sector, 0.0)
    max_sector_allowed = portfolio_capital * 0.20

    trade_val = quantity * close
    if current_sector_allocated + trade_val > max_sector_allowed:
        remaining_sector_budget = max_sector_allowed - current_sector_allocated
        if remaining_sector_budget <= 0:
            reasons.append(f"REJECTED by Risk Manager: Sector exposure limit (20%) reached for {sector}")
            return False, reasons, dynamic_stop_loss, 0, sector
        else:
            quantity = int(remaining_sector_budget // close)
            reasons.append(f"Sector Cap Applied: Position reduced to fit {sector} 20% limit")

    quantity = max(quantity, 1)

    return True, reasons, dynamic_stop_loss, quantity, sector


# --- AGENTS ORCHESTRATOR & EXECUTOR ---

async def evaluate_single_candidate_multiagent(
    row: Dict[str, Any],
    market_regime: Dict[str, Any],
    sector_exposure_capital: Dict[str, float],
    portfolio_capital: float,
    max_risk_pct: float,
    w_tech: float = 0.50,
    w_fund: float = 0.25,
    w_sent: float = 0.25,
    api_key: Optional[str] = None
) -> Optional[TradeDecision]:
    """
    Orchestrates the 6-agent pipeline for a single stock candidate:
    1. Market Data Agent: Row data & Nifty regime.
    2. Technical Analyst Agent: Multi-strategy evaluation (VCP, EMA Bounce, RS, Donchian).
    3. Fundamentals Agent: Scrapes Screener.in and evaluates health score.
    4. Sentiment Agent: Scrapes Google News RSS and evaluates narrative.
    5. Risk Manager Agent: Gatekeeper check, dynamic 2.0x ATR SL, sector cap.
    6. Portfolio Manager Agent: Aggregates outputs into final TradeDecision.
    """
    symbol = row['Symbol']
    loop = asyncio.get_running_loop()

    # Parallel Execution of Technical, Fundamentals & Sentiment Subagents
    fund_raw_task = loop.run_in_executor(None, fetch_screener_fundamentals, symbol)
    news_task = loop.run_in_executor(None, fetch_google_news_rss, symbol)

    raw_fund_data, news_headlines = await asyncio.gather(fund_raw_task, news_task)

    tech_res_task = run_technical_analyst_agent_async(symbol, row, api_key=api_key)
    fund_res_task = analyze_fundamentals_async(symbol, data=raw_fund_data, api_key=api_key)
    sent_res_task = analyze_sentiment_async(symbol, headlines=news_headlines, api_key=api_key)

    tech_res, fund_res, sent_res = await asyncio.gather(tech_res_task, fund_res_task, sent_res_task)

    # Risk Manager Gatekeeper Check
    approved, risk_notes, dynamic_sl, quantity, sector = run_risk_manager_gatekeeper(
        symbol=symbol,
        row_data=row,
        fund_res=fund_res,
        sent_res=sent_res,
        tech_res=tech_res,
        market_regime=market_regime,
        sector_exposure_capital=sector_exposure_capital,
        portfolio_capital=portfolio_capital,
        max_risk_pct=max_risk_pct
    )

    if not approved:
        print(f"Risk Manager rejected {symbol}: {risk_notes}")
        return None

    # Track sector capital allocation for subsequent stocks
    close = float(row.get('Close', 0.0))
    sector_exposure_capital[sector] = sector_exposure_capital.get(sector, 0.0) + (quantity * close)

    setup_type = tech_res.strategy_type
    entry_range = tech_res.entry_range

    entry_mid = close
    try:
        parts = entry_range.split('-')
        entry_mid = (float(parts[0].strip()) + float(parts[1].strip())) / 2.0
    except Exception:
        entry_mid = close

    risk_per_share = entry_mid - dynamic_sl
    if risk_per_share <= 0:
        risk_per_share = entry_mid * 0.03
        dynamic_sl = round(entry_mid - risk_per_share, 2)

    reward = risk_per_share * tech_res.target_rr
    target = round(entry_mid + reward, 2)

    # Normalize weights
    total_w = w_tech + w_fund + w_sent
    if total_w > 0:
        w_tech_norm = w_tech / total_w
        w_fund_norm = w_fund / total_w
        w_sent_norm = w_sent / total_w
    else:
        w_tech_norm, w_fund_norm, w_sent_norm = 0.50, 0.25, 0.25

    # --- WEIGHTED ENSEMBLE DECISION ENGINE FORMULA ---
    # Final Score = (w_tech * S_tech) + (w_fund * S_fund) + (w_sent * S_sent) - (w_risk * R_penalty)
    s_tech = tech_res.tech_conviction_normalized * 10.0
    s_fund = float(fund_res.fundamental_score)
    s_sent = ((sent_res.sentiment_score + 1.0) / 2.0) * 10.0

    r_penalty = 2.0 if (market_regime.get('below_50ema', False) or len(fund_res.red_flags) > 0) else 0.0
    w_risk = 1.0

    final_score_raw = (w_tech_norm * s_tech) + (w_fund_norm * s_fund) + (w_sent_norm * s_sent) - (w_risk * r_penalty)
    composite_conviction_score = max(1, min(10, int(round(final_score_raw))))

    combined_red_flags = list(fund_res.red_flags)
    for note in risk_notes:
        if "Cautious" in note or "Cap" in note:
            combined_red_flags.append(note)

    # Call Gemini for Portfolio Manager synthesis (gemini-3.1-pro-preview / gemini-2.5-pro)
    reasoning = None
    if api_key and GENAI_AVAILABLE:
        try:
            client = genai.Client(api_key=api_key)
            pm_prompt = f"""
Portfolio Manager Synthesis for {symbol}:
- Sector: {sector}
- Weighted Ensemble Score: {composite_conviction_score}/10 (w_tech={w_tech_norm:.2f}, w_fund={w_fund_norm:.2f}, w_sent={w_sent_norm:.2f})
- Technical Strategy: {setup_type} (Normalized Tech Conviction: {tech_res.tech_conviction_normalized:.2f}, Close ₹{close}, SL ₹{dynamic_sl}, Target ₹{target})
- Fundamental Health: {fund_res.fundamental_score}/10 (ROCE {fund_res.roce_pct}%, D/E {fund_res.debt_to_equity})
- News Sentiment: {sent_res.sentiment_classification} ({sent_res.sentiment_score:+.2f})
- Market Narrative: {sent_res.market_narrative}
- Market Regime: {market_regime['regime_str']}
- Risk Manager Position: Approved {quantity} shares within 20% sector cap.

Write a 2-sentence high-conviction institutional trade thesis.
"""
            reasoning = await async_generate_content_with_fallback(
                client=client,
                contents=pm_prompt,
                system_instruction=PORTFOLIO_MANAGER_SYSTEM_PROMPT,
                response_schema=None,
                primary_model='gemini-3.1-pro-preview',
                fallback_model='gemini-2.5-pro'
            )
            if reasoning:
                reasoning = reasoning.strip()
        except Exception as e:
            print(f"Portfolio Manager LLM call error for {symbol}: {e}")

    if not reasoning:
        rs_3m = float(row.get('RS_3M', 0.0))
        reasoning = (
            f"{symbol} ({sector}) approved by Risk Manager for {quantity} shares with dynamic 2.0x ATR SL ₹{dynamic_sl:.2f} ({tech_res.target_rr:.1f} RR). "
            f"Weighted Ensemble Score: {composite_conviction_score}/10 (Tech: {tech_res.tech_conviction_normalized:.2f}, Fund: {fund_res.fundamental_score}/10, News: {sent_res.sentiment_score:+.2f})."
        )

    return TradeDecision(
        symbol=symbol,
        sector=sector,
        setup_type=setup_type,
        entry_range=entry_range,
        stop_loss=dynamic_sl,
        target=target,
        tech_conviction_normalized=tech_res.tech_conviction_normalized,
        conviction_score=composite_conviction_score,
        fundamental_score=fund_res.fundamental_score,
        sentiment_score=sent_res.sentiment_score,
        sentiment_classification=sent_res.sentiment_classification,
        market_regime=market_regime['regime_str'],
        priority_rank=1,  # Assigned by Portfolio Manager ranking
        market_narrative=sent_res.market_narrative,
        red_flags=combined_red_flags,
        position_size=quantity,
        reasoning=reasoning
    )


async def analyze_stock_with_antigravity(
    row: Dict[str, Any],
    market_regime: Dict[str, Any],
    sector_exposure_capital: Dict[str, float],
    portfolio_capital: float = 1_000_000.0,
    max_risk_pct: float = 0.02,
    w_tech: float = 0.50,
    w_fund: float = 0.25,
    w_sent: float = 0.25,
    api_key: Optional[str] = None
) -> Optional[TradeDecision]:
    """
    Analyzes a single stock candidate using the multi-agent engine with custom ensemble weights.
    """
    return await evaluate_single_candidate_multiagent(
        row=row,
        market_regime=market_regime,
        sector_exposure_capital=sector_exposure_capital,
        portfolio_capital=portfolio_capital,
        max_risk_pct=max_risk_pct,
        w_tech=w_tech,
        w_fund=w_fund,
        w_sent=w_sent,
        api_key=api_key
    )


async def run_agent_engine_async(
    candidates_df: pd.DataFrame,
    portfolio_capital: float = 1_000_000.0,
    max_risk_pct: float = 0.02,
    w_tech: float = 0.50,
    w_fund: float = 0.25,
    w_sent: float = 0.25
) -> List[TradeDecision]:
    """
    Main 6-Agent Orchestrator Pipeline:
    1. Market Data Agent: Checks Nifty market regime & candidate prices.
    2. Runs Technical Analyst, Fundamentals, and Sentiment Agents concurrently per candidate.
    3. Calculates Weighted Ensemble Decision Score.
    4. Passes results through Risk Manager Agent gatekeeper (negative sentiment, low fundamentals, dynamic ATR SL, sector caps).
    5. Portfolio Manager Agent ranks approved decisions by conviction score and assigns priority ranks.
    """
    if candidates_df.empty:
        return []

    api_key = os.getenv("GEMINI_API_KEY")

    # Step 1: Market Data Agent -> Check overall Market Regime
    market_regime = get_market_regime()
    print(f"Market Data Agent -> Regime: {market_regime['regime_str']}")

    sector_exposure_capital: Dict[str, float] = {}

    # Step 2: Run Multi-Agent evaluations for all candidates
    tasks = [
        evaluate_single_candidate_multiagent(
            row.to_dict(),
            market_regime=market_regime,
            sector_exposure_capital=sector_exposure_capital,
            portfolio_capital=portfolio_capital,
            max_risk_pct=max_risk_pct,
            w_tech=w_tech,
            w_fund=w_fund,
            w_sent=w_sent,
            api_key=api_key
        )
        for _, row in candidates_df.iterrows()
    ]

    results = await asyncio.gather(*tasks)
    approved_decisions: List[TradeDecision] = [res for res in results if res is not None]

    # Step 3: Portfolio Manager Agent -> Sort and assign Priority Ranks
    approved_decisions.sort(key=lambda d: d.conviction_score, reverse=True)
    for rank, decision in enumerate(approved_decisions, start=1):
        decision.priority_rank = rank

    return approved_decisions


def run_agent_engine(
    candidates_df: pd.DataFrame,
    portfolio_capital: float = 1_000_000.0,
    max_risk_pct: float = 0.02,
    w_tech: float = 0.50,
    w_fund: float = 0.25,
    w_sent: float = 0.25
) -> str:
    """
    Synchronous wrapper for agent engine returning formatted JSON string output.
    """
    decisions = asyncio.run(run_agent_engine_async(
        candidates_df, portfolio_capital, max_risk_pct, w_tech, w_fund, w_sent
    ))
    json_list = [d.model_dump() for d in decisions]
    return json.dumps(json_list, indent=2)


if __name__ == "__main__":
    print("=" * 80)
    print("NIFTY 500 MULTI-STRATEGY 6-AGENT INSTITUTIONAL ENGINE SCAN TEST")
    print("=" * 80)

    from screener import run_screener

    shortlist_df = run_screener(verbose=False).head(5)
    if not shortlist_df.empty:
        print(f"Running Multi-Strategy 6-Agent Engine for {len(shortlist_df)} shortlisted candidates...")
        decisions = asyncio.run(run_agent_engine_async(shortlist_df))
        print(f"\nFinal Portfolio Manager Approved Trades ({len(decisions)}):")
        for d in decisions:
            print(f"\nRank #{d.priority_rank} | {d.symbol} ({d.sector})")
            print(f"  Regime: {d.market_regime}")
            print(f"  Multi-Strategy Setup: {d.setup_type} (Normalized Tech Conviction: {d.tech_conviction_normalized:.2f})")
            print(f"  Overall Conviction: {d.conviction_score}/10 | Fund Health: {d.fundamental_score}/10 | News: {d.sentiment_classification} ({d.sentiment_score:+.2f})")
            print(f"  Entry: {d.entry_range} | Dynamic ATR SL: ₹{d.stop_loss:.2f} | Target: ₹{d.target:.2f}")
            print(f"  Position Size: {d.position_size} Shares")
            print(f"  Red Flags: {d.red_flags}")
            print(f"  Reasoning: {d.reasoning}")
    print("=" * 80)
