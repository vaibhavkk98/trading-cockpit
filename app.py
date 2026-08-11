"""
NIFTY 500 QUANT TRADING COCKPIT (Frozen Phase F paper-trading support)

End-to-End Decision Support Application & Paper Trading Engine:
- Market Regime Monitor (Nifty 50 vs EMA50)
- Technical Strategy Screener & Signal Engine
- Frozen qualification, priority, and allocation rationale
- 7 Trend / 3 Volatility Strategy-Aware Allocation
- Deterministic Trade Rationale ("Why This Trade?")
- Interactive Stock Charts & Decision Cards
- Live Paper Portfolio Engine & SQLite Tracker
- Historical Research Performance Viewer
- Frozen nominal sizing and paper-trade lifecycle

Launch:
    PYTHONPATH=. streamlit run app.py
"""

import os
import sys
import datetime
import json
import pandas as pd
import numpy as np
import streamlit as st

PROJECT_ROOT = os.path.abspath(os.path.dirname(__file__))
sys.path.insert(0, PROJECT_ROOT)

from adapters import (
    MarketDataProvider,
    UniverseProvider,
    SignalEngine,
    PortfolioAllocationEngine,
    ExecutionAdapter,
    TREND_STRATEGIES,
    VOLATILITY_STRATEGIES
)
from event_intelligence import EventIntelligenceService
from market_risk_live import get_market_risk_context_for_ui
from live_decision_adapter import assemble_live_decisions, summarize_live_portfolio_risk
from operational_runtime import PRODUCT_VERSION, SCAN_FAILED, SCAN_NOT_RUN, SCAN_PARTIAL, SCAN_RUNNING, SCAN_SUCCESS, initial_scan_state, log_event, scan_freshness, utc_now
from ui_components import (
    allocation_display,
    apply_theme,
    display_value,
    format_currency,
    format_percent,
    format_price,
    format_volume,
    render_context_card,
    render_empty_state,
    render_market_risk_card,
    render_metric_card,
    render_section_header,
    status_badge,
)

# ------------------------------------------------------------------------------
# PAGE CONFIGURATION & PRESENTATION THEME
# ------------------------------------------------------------------------------
st.set_page_config(
    page_title="Nifty 500 Trading Cockpit MVP",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="collapsed"
)

apply_theme()

# ------------------------------------------------------------------------------
# INITIALIZE ADAPTERS & SESSION STATE
# ------------------------------------------------------------------------------
@st.cache_resource
def get_adapters():
    return {
        "market_data": MarketDataProvider(),
        "universe": UniverseProvider(),
        "signals": SignalEngine(),
        "portfolio_allocator": PortfolioAllocationEngine(),
        "execution": ExecutionAdapter()
    }

adapters = get_adapters()

# Session State Config Defaults
if "sizing_mode" not in st.session_state:
    st.session_state["sizing_mode"] = "EQUAL_WEIGHT"
if "exit_mode" not in st.session_state:
    st.session_state["exit_mode"] = "FIXED_10D"
if "enabled_strategies" not in st.session_state:
    st.session_state["enabled_strategies"] = list(TREND_STRATEGIES.union(VOLATILITY_STRATEGIES))
if "max_trend_slots" not in st.session_state:
    st.session_state["max_trend_slots"] = 7
if "max_vol_slots" not in st.session_state:
    st.session_state["max_vol_slots"] = 3
if "max_positions" not in st.session_state:
    st.session_state["max_positions"] = 10

# Phase F product policy is frozen. Session state may survive a Streamlit
# rerun, so a historical setting cannot change the active product policy.
st.session_state["sizing_mode"] = "EQUAL_WEIGHT"
st.session_state["exit_mode"] = "FIXED_10D"

# Session State Analysis Output
if "last_analysis_date" not in st.session_state:
    st.session_state["last_analysis_date"] = None
if "shortlist_df" not in st.session_state:
    st.session_state["shortlist_df"] = pd.DataFrame()
if "allocated_candidates" not in st.session_state:
    st.session_state["allocated_candidates"] = []
if "regime_info" not in st.session_state:
    st.session_state["regime_info"] = None
if "diag_info" not in st.session_state:
    st.session_state["diag_info"] = {}
if "scan_state" not in st.session_state:
    st.session_state["scan_state"] = initial_scan_state()

# Candidate dictionaries are session-persisted by Streamlit.  Invalidate any
# payload produced by the pre-Step-11.1 contract, which lacked canonical
# volume/EMA/date fields and could contain an invented 1.5x target ratio.
COCKPIT_CANDIDATE_CONTRACT_VERSION = 3
if st.session_state.get("candidate_contract_version") != COCKPIT_CANDIDATE_CONTRACT_VERSION:
    st.session_state["shortlist_df"] = pd.DataFrame()
    st.session_state["allocated_candidates"] = []
    st.session_state["regime_info"] = None
    st.session_state["diag_info"] = {}
    st.session_state["last_analysis_date"] = None
    st.session_state["candidate_contract_version"] = COCKPIT_CANDIDATE_CONTRACT_VERSION

# Compact application shell
st.markdown(
    f"""
    <div class="tc-header">
      <div><div class="tc-title">Trading Cockpit</div><div class="tc-subtitle">Paper-trading decision support · Manual execution only</div></div>
      <div class="tc-meta">{status_badge('Paper trading', 'neutral')} &nbsp; {status_badge('ML inactive', 'unavailable')}<br/>Latest completed EOD data</div>
    </div>
    """,
    unsafe_allow_html=True,
)

# ------------------------------------------------------------------------------
# TOP ACTION BAR (RUN TODAY'S ANALYSIS)
# ------------------------------------------------------------------------------
act_col1, act_col2, act_col3 = st.columns([2, 2, 4])

with act_col1:
    analysis_date = st.date_input("Analysis date", datetime.date.today())

with act_col2:
    st.write("")
    run_analysis_btn = st.button("Run today's analysis", type="primary", use_container_width=True)

with act_col3:
    st.caption(f"Frozen policy · ₹1L nominal ticket · Fixed 10-day lifecycle · {st.session_state['max_trend_slots']} trend / {st.session_state['max_vol_slots']} volatility slots")

# Handler for "Run Today's Analysis" (Does NOT execute paper trades automatically)
# A full live-universe scan is intentional user work.  Do not begin it merely
# because a fresh cockpit session has no saved candidates.
if run_analysis_btn:
    scan_state = initial_scan_state()
    scan_state.update({"status": SCAN_RUNNING, "scan_started_at": utc_now(), "analysis_date": str(analysis_date)})
    st.session_state["scan_state"] = scan_state
    log_event(PROJECT_ROOT, "SCAN_START", analysis_date=str(analysis_date))
    with st.spinner("Scanning the NIFTY 500 universe and applying frozen qualification and allocation rules…"):
      try:
        symbols = adapters["universe"].get_universe(date_str=str(analysis_date))
        scan_state["symbols_requested"] = len(symbols)
        regime_info = adapters["market_data"].get_index_regime(as_of_date=str(analysis_date))

        # Screen full universe as of date
        shortlist_df, diag_info = adapters["signals"].run_stage1_screening(
            symbols=symbols,
            max_scan=None, # Full universe scan
            as_of_date=str(analysis_date),
            return_diagnostics=True
        )

        open_positions = adapters["execution"].get_open_positions()

        allocator = PortfolioAllocationEngine(
            max_positions=st.session_state["max_positions"],
            max_trend=st.session_state["max_trend_slots"],
            max_vol=st.session_state["max_vol_slots"]
        )

        allocated_candidates = allocator.allocate_candidates(
            shortlist_df=shortlist_df,
            regime_info=regime_info,
            open_positions=open_positions,
            position_sizing_mode=st.session_state["sizing_mode"],
            exit_rule_mode=st.session_state["exit_mode"],
            enabled_strategies=st.session_state["enabled_strategies"]
        )

        # P3A is informational only: it is applied after qualification/allocation
        # and never feeds the allocator, score, or paper-trading execution path.
        event_cutoff = datetime.datetime.combine(analysis_date, datetime.time.max, tzinfo=datetime.timezone.utc)
        try:
            EventIntelligenceService().enrich_candidates(allocated_candidates, cutoff=event_cutoff)
        except Exception as event_exc:
            log_event(PROJECT_ROOT, "EVENT_CONTEXT_UNAVAILABLE", error=type(event_exc).__name__)

        symbols_succeeded = int(diag_info.get("valid_data_count", 0))
        symbols_failed = max(0, len(symbols) - symbols_succeeded)
        scan_state.update({
            "status": SCAN_PARTIAL if symbols_succeeded and symbols_failed else SCAN_SUCCESS,
            "scan_completed_at": utc_now(), "symbols_succeeded": symbols_succeeded,
            "symbols_failed": symbols_failed, "qualified_count": sum(c.get("is_qualified", False) for c in allocated_candidates),
            "allocated_count": sum(c.get("status") == "ALLOCATED" for c in allocated_candidates),
        })
        st.session_state["scan_state"] = scan_state
        log_event(PROJECT_ROOT, "SCAN_PARTIAL" if scan_state["status"] == SCAN_PARTIAL else "SCAN_COMPLETE", requested=len(symbols), succeeded=symbols_succeeded, failed=symbols_failed)
        st.session_state["last_analysis_date"] = str(analysis_date)
        st.session_state["shortlist_df"] = shortlist_df
        st.session_state["allocated_candidates"] = allocated_candidates
        st.session_state["regime_info"] = regime_info
        st.session_state["diag_info"] = diag_info
        adapters["execution"].save_portfolio_snapshot("ANALYSIS_COMPLETED")
      except Exception as exc:
        scan_state.update({"status": SCAN_FAILED, "scan_completed_at": utc_now(), "error_summary": type(exc).__name__})
        st.session_state["scan_state"] = scan_state
        log_event(PROJECT_ROOT, "SCAN_FAILED", error=type(exc).__name__)
        st.error("Analysis could not complete. Existing results, if any, were preserved.")

# ------------------------------------------------------------------------------
# 6 MAIN NAVIGATION TABS
# ------------------------------------------------------------------------------
tab_today, tab_signals, tab_portfolio, tab_details, tab_performance, tab_settings = st.tabs([
    "Today",
    "Signals",
    "Portfolio",
    "Trade Details",
    "Performance",
    "Settings"
])

regime_info = st.session_state.get("regime_info") or {"regime": "BULLISH", "status_text": "Bullish (Nifty +1.46% > 50 EMA)", "close": 24250.0, "ema50": 23900.0, "data_as_of": str(analysis_date)}
all_candidates = st.session_state.get("allocated_candidates") or []

# Differentiate Candidate Statuses cleanly
selected_candidates = [c for c in all_candidates if c.get('status') == 'ALLOCATED' or c.get('is_selected', False)]
qualified_unallocated = [c for c in all_candidates if c.get('status') == 'QUALIFIED — CAPITAL CAP' or (c.get('is_qualified', False) and not c.get('is_selected', False))]
rejected_candidates = [c for c in all_candidates if c.get('status', '').startswith('REJECTED')]

open_positions = adapters["execution"].get_open_positions()
live_qualified_decisions = assemble_live_decisions(all_candidates)
selected_candidates = [c for c in live_qualified_decisions if c.get("allocation_status") == "ALLOCATED"]
qualified_unallocated = [c for c in live_qualified_decisions if c.get("allocation_status") != "ALLOCATED"]

def render_market_risk_context():
    """C3 consumer-only rendering; has no connection to trading decisions."""
    # Informational only — does not alter qualification, allocation, sizing, or stops.
    context = get_market_risk_context_for_ui(PROJECT_ROOT)
    render_market_risk_card(context)
    with st.expander("Source coverage & methodology details", expanded=False):
        covered_groups = ", ".join(context.get("coverage_groups_achieved", [])) or "Not available"
        st.caption(f"Covered groups: {covered_groups}")
        for event in context.get("top_events", [])[:3]:
            st.markdown(f"**{display_value(event.get('headline_or_short_title'))}**")
            st.caption(f"{display_value(event.get('source_name'))} · {display_value(event.get('source_tier'))} · {display_value(event.get('status'))} · {display_value(event.get('expected_horizon'))}")
            if event.get('source_reference'):
                st.markdown(f"[Source]({event['source_reference']})")
        for source in context.get("source_diagnostics", []):
            st.caption(f"{display_value(source.get('source_name'), 'Source')}: {display_value(source.get('check_status'))}")

# Date Semantics Notice
actual_data_date = regime_info.get('data_as_of')
if str(analysis_date) == datetime.date.today().strftime("%Y-%m-%d"):
    date_note = "Today's analysis uses the latest completed EOD session."
else:
    date_note = "Historical analysis — no future data used."

# ==============================================================================
# TAB 1 — TODAY (EXECUTIVE TRADING COCKPIT)
# ==============================================================================
with tab_today:
    render_section_header("Today", "Decision command center", "Paper trading", "neutral")
    st.caption(f"Analysis date: {display_value(st.session_state.get('last_analysis_date') or analysis_date)} · Market data as of: {display_value(actual_data_date)} · {date_note}")

    # --------------------------------------------------------------------------
    # 1. PORTFOLIO SUMMARY CARDS
    # --------------------------------------------------------------------------
    tot_qualified_cnt = len(selected_candidates) + len(qualified_unallocated)
    tot_cap_req = sum(c.get("suggested_position_size", 100000.0) for c in selected_candidates)
    tot_cap_rem = max(0.0, 1000000.0 - tot_cap_req)
    defined_stop_risk = sum(
        c.get("executable_stop_risk_inr", 0.0)
        for c in selected_candidates
        if isinstance(c.get("executable_stop_risk_inr"), (int, float))
    )
    defined_stop_risk_available = any(
        isinstance(c.get("executable_stop_risk_inr"), (int, float))
        for c in selected_candidates
    )

    today_portfolio = adapters["execution"].get_portfolio_summary()
    live_risk_summary = summarize_live_portfolio_risk(open_positions)
    p1, p2, p3, p4, p5, p6 = st.columns(6)
    with p1:
        render_metric_card("Portfolio equity", format_currency(today_portfolio.get("total_portfolio_value_inr")), "Paper portfolio")
    with p2:
        render_metric_card("Cash", format_currency(today_portfolio.get("current_cash_inr")), "Available cash")
    with p3:
        render_metric_card("Open positions", f"{len(open_positions)} / 10", "Paper positions")
    with p4:
        render_metric_card("Reference heat", live_risk_summary["reference_heat_pct"], "Informational", "unavailable")
    with p5:
        render_metric_card("Qualified today", tot_qualified_cnt, "All qualified opportunities")
    with p6:
        render_metric_card("Allocated today", len(selected_candidates), f"{len(qualified_unallocated)} not allocated", "good" if selected_candidates else "neutral")

    market_col, scan_col = st.columns([2, 1])
    with market_col:
        render_market_risk_context()
    with scan_col:
        render_section_header("Scan status", "Current session")
        scan_state = st.session_state["scan_state"]
        freshness = scan_freshness(scan_state)
        state_label = {SCAN_NOT_RUN: "No scan yet", SCAN_RUNNING: "Scanning universe…", SCAN_SUCCESS: "Scan complete", SCAN_PARTIAL: "Scan partial", SCAN_FAILED: "Scan failed"}.get(scan_state.get("status"), "Not available")
        freshness_label = "Current" if freshness == "CURRENT" else "Stale" if freshness == "STALE" else "Not available"
        render_context_card("Analysis freshness", freshness_label, f"{state_label} · Completed: {display_value(scan_state.get('scan_completed_at'))}", "good" if freshness == "CURRENT" else "warn" if freshness == "STALE" else "unavailable")
        render_context_card("Scan coverage", f"{scan_state.get('symbols_succeeded', 0)} / {scan_state.get('symbols_requested', 0)} histories", f"Data unavailable: {scan_state.get('symbols_failed', 0)} · Qualified: {scan_state.get('qualified_count', 0)}", "neutral")
        render_context_card("Qualification rule", "Volume + price confirmation", "Volume Ratio 20 ≥ 2.0× and Close above EMA20.", "neutral", "Binding")
        render_context_card("Defined stop risk", format_currency(defined_stop_risk) if defined_stop_risk_available else "Not available", "Only for validated executable-stop strategies.", "neutral")

    # Sector Allocation Breakdown (Informational)
    if selected_candidates:
        sector_counts = {}
        for c in selected_candidates:
            sec = c.get("sector", "General")
            sector_counts[sec] = sector_counts.get(sec, 0) + c.get("suggested_position_size", 100000.0)
        sector_str = " • ".join([f"**{sec}**: ₹{amt:,.0f}" for sec, amt in sector_counts.items()])
        st.caption(f"Proposed sector allocation: {sector_str}")

    # Expandable Analysis Diagnostics Section
    with st.expander("🔍 Analysis Diagnostics & Selection Funnel", expanded=False):
        d_info = st.session_state.get("diag_info") or {}
        st.write(f"- **Analysis Date**: `{st.session_state.get('last_analysis_date', analysis_date)}`")
        st.write(f"- **Data As Of**: `{actual_data_date}` (*{date_note}*)")
        st.write(f"- **Universe Size**: `{d_info.get('universe_count', 500)} symbols`")
        st.write(f"- **Symbols Screened**: `{d_info.get('symbols_screened', 500)} symbols`")
        st.write(f"- **Valid Price Data**: `{d_info.get('valid_data_count', 0)} symbols`")
        st.write(f"- **Unique Signal Candidates (Stocks)**: `{d_info.get('unique_signal_candidates', len(all_candidates))}`")
        st.write(f"- **Total Qualified Setups (Vol 20D >= 2.0x & Close > EMA20)**: `{tot_qualified_cnt}`")
        st.write(f"- **Recommended Selected Positions (Allocated)**: `{len(selected_candidates)}` (Max 10 slots / ₹10L cap)")
        st.write(f"- **Qualified Candidates — Capital Cap (Unallocated)**: `{len(qualified_unallocated)}`")
        st.write(f"- **Rejected Candidates**: `{len(rejected_candidates)}` (Failed volume ratio < 2.0x, Close <= EMA20, or regime)")

    render_section_header("Today's opportunities", "All qualified opportunities, ordered by causal Volume Ratio 20", "Binding allocation", "neutral")
    if not live_qualified_decisions:
        if st.session_state.get("last_analysis_date"):
            render_empty_state("No qualified opportunities", "The latest scan completed without a qualified setup.")
        else:
            render_empty_state("No scan yet", "Run today's analysis to populate qualified opportunities.")
    else:
        opportunity_rows = []
        for candidate in live_qualified_decisions:
            allocation_label, _ = allocation_display(candidate.get("allocation_status"), candidate.get("allocation_reason_code"))
            opportunity_rows.append({
                "Priority": candidate.get("opportunity_priority_rank", "Not available"),
                "Symbol": candidate.get("symbol", "Not available"),
                "Strategy": candidate.get("strategy", "Not available"),
                "Allocation": allocation_label,
                "Volume Ratio": format_volume(candidate.get("volume_ratio_20")),
                "Reference Risk": format_percent(candidate.get("reference_risk_pct_equity")),
                "Heat Added": format_percent(candidate.get("candidate_reference_heat_add_pct")),
            })
        st.dataframe(pd.DataFrame(opportunity_rows), width="stretch", hide_index=True)
        st.caption("Use Trade Details to inspect an opportunity's setup, Trade Risk, portfolio impact, and descriptive historical context.")

    render_section_header("Allocation summary", "Existing portfolio constraints; no new allocation policy")
    allocation_counts = {"Allocated": 0, "Insufficient cash": 0, "Duplicate": 0, "Capacity": 0}
    for candidate in live_qualified_decisions:
        code = candidate.get("allocation_reason_code")
        if candidate.get("allocation_status") == "ALLOCATED":
            allocation_counts["Allocated"] += 1
        elif code == "DUPLICATE_POSITION":
            allocation_counts["Duplicate"] += 1
        elif code in {"CAPITAL_CAP", "INSUFFICIENT_CASH"}:
            allocation_counts["Insufficient cash" if code == "INSUFFICIENT_CASH" else "Capacity"] += 1
        else:
            allocation_counts["Capacity"] += 1
    a1, a2, a3, a4 = st.columns(4)
    for column, (label, count) in zip((a1, a2, a3, a4), allocation_counts.items()):
        with column:
            render_metric_card(label, count, "Qualified opportunities", "good" if label == "Allocated" and count else "neutral")

    if selected_candidates:
        render_section_header("Paper trade", "Explicit action only", "Manual", "neutral")
        db_health = adapters["execution"].database_diagnostics()
        if db_health.get("database_status") != "AVAILABLE":
            st.error("Paper-trade storage is unavailable. No paper trade can be recorded until database connectivity is restored.")
        trade_col, action_col = st.columns([3, 1])
        with trade_col:
            sel_sym = st.selectbox("Allocated opportunity", [candidate["symbol"] for candidate in selected_candidates], key="paper_trade_symbol")
        match_cand = next((candidate for candidate in selected_candidates if candidate["symbol"] == sel_sym), None)
        if match_cand:
            stop_text = format_price(match_cand.get("initial_executable_stop")) if match_cand.get("executable_stop_enabled") else "Not validated for this strategy"
            render_context_card("Paper-trade confirmation", f"{match_cand['symbol']} · {match_cand['strategy']}", f"Entry {format_price(match_cand.get('entry_price'))} · Quantity {display_value(match_cand.get('quantity'))} · Position value {format_currency(match_cand.get('suggested_position_size'))} · Reference risk {display_value(match_cand.get('risk_reference_value'))} · Executable stop {stop_text} · Target not available", "neutral", "Paper trade only")
        confirm_paper_trade = st.checkbox("I confirm this is a manual paper-trade record", key="confirm_paper_trade")
        with action_col:
            st.write("")
            exec_btn = st.button("Record paper trade", type="primary", width="stretch", disabled=not confirm_paper_trade or db_health.get("database_status") != "AVAILABLE")
        if exec_btn and sel_sym:
            if match_cand:
                submission_key = f"{match_cand['symbol']}:{match_cand.get('signal_date')}:{st.session_state['scan_state'].get('scan_completed_at')}"
                submitted = st.session_state.setdefault("paper_submission_keys", set())
                if submission_key in submitted:
                    st.warning("This opportunity was already recorded for the current scan.")
                else:
                    res = adapters["execution"].execute_paper_trade(match_cand)
                    if res.get("success"):
                        submitted.add(submission_key)
                        log_event(PROJECT_ROOT, "PAPER_TRADE_RECORDED", symbol=match_cand["symbol"], trade_id=res.get("trade_id"))
                        st.success(res["message"])
                        st.rerun()
                    else:
                        log_event(PROJECT_ROOT, "PAPER_TRADE_WRITE_FAILED", symbol=match_cand["symbol"])
                        st.error(res.get("message", "Paper-trade write failed."))

    # --------------------------------------------------------------------------
    # 2. QUALIFIED — NOT CURRENTLY ALLOCATED SECTION
    # --------------------------------------------------------------------------
    if qualified_unallocated:
        with st.expander(f"Qualified but not allocated ({len(qualified_unallocated)})", expanded=False):
            st.caption("These opportunities remain qualified. Existing cash, duplicate-position, or capacity constraints are binding.")
            rows = []
            for candidate in qualified_unallocated:
                allocation_label, _ = allocation_display(candidate.get("allocation_status"), candidate.get("allocation_reason_code"))
                rows.append({
                    "Priority": candidate.get("opportunity_priority_rank", "Not available"),
                    "Symbol": candidate.get("symbol", "Not available"),
                    "Strategy": candidate.get("strategy", "Not available"),
                    "Allocation": allocation_label,
                    "Reason": display_value(candidate.get("allocation_reason_text")),
                })
            st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)

    # --------------------------------------------------------------------------
    # 3. REJECTED CANDIDATES SECTION
    # --------------------------------------------------------------------------
    if rejected_candidates:
        with st.expander("Rejected-candidate diagnostics", expanded=False):
            st.caption("Methodology diagnostics only; qualified opportunities remain visible in Signals.")
            rejected_rows = []
            for candidate in rejected_candidates[:10]:
                rejected_rows.append({
                    "Symbol": candidate.get("symbol", "Not available"),
                    "Strategy": candidate.get("strategy", "Not available"),
                    "Reason": display_value(candidate.get("reason_text") or candidate.get("status")),
                })
            st.dataframe(pd.DataFrame(rejected_rows), width="stretch", hide_index=True)

# ==============================================================================
# TAB 2 — SIGNALS (ALL QUALIFYING SIGNALS)
# ==============================================================================
with tab_signals:
    render_section_header("Signals", "All qualified opportunities; allocation does not hide a setup", "All qualified", "neutral")

    f1, f2, f3, f4 = st.columns([2, 1.2, 1.2, 1.2])
    with f1:
        strat_filter = st.multiselect("Strategy", sorted(TREND_STRATEGIES.union(VOLATILITY_STRATEGIES)), default=sorted(TREND_STRATEGIES.union(VOLATILITY_STRATEGIES)))
    with f2:
        status_filter = st.selectbox("Allocation", ["All", "Allocated", "Not allocated"])
    with f3:
        symbol_filter = st.text_input("Symbol search", placeholder="e.g. RELIANCE")
    with f4:
        stop_filter = st.selectbox("Executable stop", ["All", "Available", "Not available"])

    if live_qualified_decisions:
        filtered_cands = live_qualified_decisions
        if strat_filter:
            filtered_cands = [c for c in filtered_cands if c.get('strategy') in strat_filter]
        if status_filter != "All":
            filtered_cands = [c for c in filtered_cands if (c.get('allocation_status') == 'ALLOCATED') == (status_filter == 'Allocated')]
        if symbol_filter:
            filtered_cands = [c for c in filtered_cands if symbol_filter.upper() in str(c.get('symbol', '')).upper()]
        if stop_filter != "All":
            filtered_cands = [c for c in filtered_cands if c.get('executable_stop_enabled') == (stop_filter == "Available")]

        st.caption(f"Showing {len(filtered_cands)} of {len(live_qualified_decisions)} qualified opportunities")
        signal_rows = []
        for candidate in filtered_cands:
            economics = candidate.get("trade_economics", {})
            allocation_label, _ = allocation_display(candidate.get("allocation_status"), candidate.get("allocation_reason_code"))
            economics_source = economics.get("display_sample_source", "INSUFFICIENT")
            economics_label = "Insufficient" if economics_source == "INSUFFICIENT" else f"{economics_source} · N={display_value(economics.get('sample_count'))}"
            signal_rows.append({
                "Symbol": candidate.get("symbol", "NOT_AVAILABLE"),
                "Strategy": candidate.get("strategy", "NOT_AVAILABLE"),
                "Priority": candidate.get("opportunity_priority_rank", "NOT_AVAILABLE"),
                "Allocation": allocation_label,
                "Volume Ratio": format_volume(candidate.get("volume_ratio_20")),
                "Entry": format_price(candidate.get("entry_price")),
                "Reference Risk": format_percent(candidate.get("reference_risk_pct_equity")),
                "Executable Stop": format_price(candidate.get("initial_executable_stop")) if candidate.get("executable_stop_enabled") else "—",
                "Heat Added": format_percent(candidate.get("candidate_reference_heat_add_pct")),
                "Economics": economics_label,
            })
        st.dataframe(pd.DataFrame(signal_rows), width="stretch", hide_index=True)
    else:
        render_empty_state("No qualified signals", "Run today's analysis to view qualified opportunities here.")

# ==============================================================================
# TAB 3 — PORTFOLIO (LIVE PAPER PORTFOLIO & POSITION ENGINE)
# ==============================================================================
with tab_portfolio:
    render_section_header("Portfolio", "Live paper-portfolio state", "Paper trading", "neutral")

    perf_summary = adapters["execution"].get_portfolio_summary()

    val_inr = perf_summary.get('total_portfolio_value_inr', 1000000.0)
    cash_inr = perf_summary.get('current_cash_inr', perf_summary.get('cash_inr', 1000000.0))
    inv_inr = perf_summary.get('invested_capital_inr', perf_summary.get('open_capital_deployed_inr', 0.0))
    pos_cnt = perf_summary.get('open_positions_count', 0)
    net_pnl = perf_summary.get('total_net_pnl_inr', 0.0)
    net_ret = perf_summary.get('total_net_return_pct', 0.0)

    live_portfolio_risk = summarize_live_portfolio_risk(open_positions)
    p1, p2, p3, p4, p5, p6 = st.columns(6)
    with p1:
        render_metric_card("Portfolio equity", format_currency(val_inr), "Paper portfolio")
    with p2:
        render_metric_card("Cash", format_currency(cash_inr), "Available")
    with p3:
        render_metric_card("Deployed", format_currency(inv_inr), "Open capital")
    with p4:
        render_metric_card("Open positions", f"{pos_cnt} / 10", "Maximum positions")
    with p5:
        render_metric_card("Reference heat", live_portfolio_risk["reference_heat_pct"], "Informational", "unavailable")
    with p6:
        render_metric_card("Executable stop heat", live_portfolio_risk["executable_stop_heat_pct"], f"Coverage: {live_portfolio_risk['positions_with_reference']} / {pos_cnt}", "unavailable")
    st.caption("Reference Heat is informational. Executable Stop Heat is supplementary. Neither is a maximum-loss estimate.")

    render_section_header("Open positions", "Risk fields are available only where the paper ledger carries the frozen lifecycle data")

    open_pos_list = adapters["execution"].get_open_positions()

    if not open_pos_list:
        render_empty_state("No open paper positions", "Run today's analysis and explicitly record a paper trade to begin tracking.")
    else:
        df_open_tbl = []
        for pos in open_pos_list:
            df_open_tbl.append({
                "Symbol": pos.get("symbol", ""),
                "Strategy": pos.get("strategy_used", ""),
                "Position value": format_currency(pos.get("position_value")),
                "Current price": format_price(pos.get("current_price")) if pos.get("price_status") == "AVAILABLE" else "Price not available",
                "P&L": format_currency(pos.get("unrealized_pnl_inr"), compact=False),
                "Reference risk": format_currency(pos.get("reference_risk_rupees"), compact=False) if pos.get("risk_reference_available") else "Not available",
                "Executable stop": format_price(pos.get("initial_executable_stop")) if pos.get("executable_stop_enabled") else "—",
                "Heat contribution": format_percent((pos.get("reference_risk_rupees") or 0) / 1_000_000 * 100) if pos.get("risk_reference_available") else "Not available",
            })
        st.dataframe(pd.DataFrame(df_open_tbl), width="stretch", hide_index=True)

        col_s1, col_s2 = st.columns([2, 4])
        with col_s1:
            if st.button("Sync live market prices"):
                res_sync = adapters["execution"].sync_live_prices()
                st.success(res_sync.get("message", "Synced live prices"))
                st.rerun()

        db_health = adapters["execution"].database_diagnostics()
        with st.expander("Manually close a paper trade", expanded=False):
            st.caption("Paper-trading record only. This does not place a broker order or infer an exit from a stop/target.")
            close_candidates = {f"#{pos['id']} · {pos['symbol']}": pos for pos in open_pos_list}
            close_label = st.selectbox("Open paper trade", list(close_candidates), key="manual_close_trade")
            close_position = close_candidates[close_label]
            default_exit = float(close_position.get("current_price") or close_position.get("entry_price") or 0.0)
            manual_exit_price = st.number_input("Manual exit price", min_value=0.0, value=default_exit, step=0.05, key="manual_exit_price")
            confirm_close = st.checkbox("I confirm this manual paper-trade close", key="confirm_manual_close")
            if st.button("Close paper trade", disabled=not confirm_close or db_health.get("database_status") != "AVAILABLE"):
                close_result = adapters["execution"].close_paper_trade(close_position["id"], manual_exit_price)
                if close_result.get("success"):
                    log_event(PROJECT_ROOT, "PAPER_TRADE_CLOSED", trade_id=close_position["id"])
                    st.success(close_result["message"])
                    st.rerun()
                else:
                    st.error(close_result.get("message", "Paper-trade close failed."))

# ==============================================================================
# TAB 4 — TRADE DETAILS (DECISION CARD & WHY THIS TRADE?)
# ==============================================================================
with tab_details:
    render_section_header("Trade details", "Selected-opportunity analysis")
    cand_symbols = [candidate["symbol"] for candidate in live_qualified_decisions] if live_qualified_decisions else ["No qualified candidate"]
    if st.session_state.get("detail_symbol") not in cand_symbols:
        st.session_state["detail_symbol"] = cand_symbols[0]
    selected_detail_sym = st.selectbox("Opportunity", cand_symbols, key="detail_symbol")
    match_detail = next((candidate for candidate in live_qualified_decisions if candidate.get("symbol") == selected_detail_sym), None)

    if not match_detail:
        match_detail = {
            "symbol": selected_detail_sym, "status": "UNAVAILABLE", "strategy": "NOT_AVAILABLE",
            "qualification_status": "NOT_AVAILABLE", "opportunity_priority_rank": "NOT_AVAILABLE",
            "allocation_status": "NOT_AVAILABLE", "allocation_reason_text": "NOT_AVAILABLE",
            "entry_price": None, "risk_reference_type": "NOT_AVAILABLE", "risk_reference_value": "NOT_AVAILABLE",
            "executable_stop_enabled": False, "reference_heat_before_pct": "NOT_AVAILABLE",
            "reference_heat_after_pct": "NOT_AVAILABLE", "correlation_availability": "NOT_AVAILABLE",
            "sector": "NOT_AVAILABLE", "sector_availability": "NOT_AVAILABLE",
            "trade_economics": {"display_sample_source": "INSUFFICIENT"},
        }
        render_empty_state("No analyzed opportunity", "Run today's analysis to populate this decision view.")

    allocation_label, allocation_tone = allocation_display(match_detail.get("allocation_status"), match_detail.get("allocation_reason_code"))
    st.markdown(f"### {display_value(match_detail.get('symbol'))}")
    st.markdown(f"{status_badge(display_value(match_detail.get('strategy')), 'neutral')} &nbsp; {status_badge(display_value(match_detail.get('qualification_status')), 'good' if match_detail.get('qualification_status') == 'QUALIFIED' else 'unavailable')} &nbsp; {status_badge(allocation_label, allocation_tone)}", unsafe_allow_html=True)
    st.caption(f"Priority #{display_value(match_detail.get('opportunity_priority_rank'))} by causal Volume Ratio 20 · {display_value(match_detail.get('allocation_reason_text'))}")

    render_section_header("Trade setup", "Binding qualification and allocation fields")
    setup_1, setup_2, setup_3 = st.columns(3)
    with setup_1:
        render_context_card("Entry", format_price(match_detail.get("entry_price")), f"Signal date: {display_value(match_detail.get('signal_date'))}")
    with setup_2:
        render_context_card("Volume ratio", format_volume(match_detail.get("volume_ratio_20")), "Causal opportunity-priority input")
    with setup_3:
        ema_value = match_detail.get("ema_20") or match_detail.get("ema20")
        render_context_card("EMA context", format_price(ema_value), "Supporting price-trend context")

    render_section_header("Trade risk", "Reference risk and executable stop are distinct")
    risk_1, risk_2 = st.columns(2)
    with risk_1:
        reference_detail = f"Value: {display_value(match_detail.get('risk_reference_value'))} · Risk: {format_percent(match_detail.get('reference_risk_pct_equity'))}"
        render_context_card("Reference risk", display_value(match_detail.get("risk_reference_type")), reference_detail, "neutral", "Reference")
    with risk_2:
        if match_detail.get("executable_stop_enabled"):
            stop_detail = f"Defined stop risk: {format_currency(match_detail.get('executable_stop_risk_inr'))} · {format_price(match_detail.get('executable_risk_per_share'))} / share"
            render_context_card("Executable stop", format_price(match_detail.get("initial_executable_stop")), stop_detail, "good", "Active")
        else:
            render_context_card("Executable stop", "Not validated for this strategy", "No executable stop is inferred from an unavailable contract.", "unavailable")
    if match_detail.get("gap_risk_possible"):
        st.caption("Gap and slippage can create realized losses larger than a defined stop risk.")

    render_section_header("Portfolio impact", "Informational context; no heat limit is active", "Informational", "neutral")
    impact_1, impact_2, impact_3 = st.columns(3)
    with impact_1:
        render_context_card("Reference heat", f"{format_percent(match_detail.get('reference_heat_before_pct'))} → {format_percent(match_detail.get('reference_heat_after_pct'))}", "Before → after candidate")
    with impact_2:
        render_context_card("Heat added", format_percent(match_detail.get("candidate_reference_heat_add_pct")), "Candidate contribution")
    with impact_3:
        render_context_card("Portfolio fit", display_value(match_detail.get("correlation_availability")), f"Sector: {display_value(match_detail.get('sector'))}")

    render_section_header("Trade economics", "Historical strategy context only", "Descriptive", "neutral")
    economics = match_detail.get("trade_economics", {})
    if not economics or economics.get("display_sample_source") == "INSUFFICIENT":
        render_context_card("Historical sample", "Insufficient clean validation/test sample", "No prediction, target, or trade score is shown.", "unavailable")
    else:
        historical = economics.get("historical_return_context", {})
        econ_1, econ_2, econ_3 = st.columns(3)
        with econ_1:
            render_context_card("Historical sample", f"{display_value(economics.get('display_sample_source'))} · N={display_value(economics.get('sample_count'))}", display_value(economics.get("sample_quality")))
        with econ_2:
            render_context_card("Median return", format_percent(historical.get("median_return")), f"Win rate: {format_percent(historical.get('win_rate'))}")
        with econ_3:
            render_context_card("Profit factor", display_value(historical.get("profit_factor")), f"P10 return: {format_percent(historical.get('p10_return'))}")
    st.caption("Target: Not available. No reward/risk ratio is displayed because the frozen contract has no target.")

    render_section_header("Other context", "Supporting information")
    other_1, other_2, other_3 = st.columns(3)
    with other_1:
        render_context_card("Entry timing", display_value(match_detail.get("entry_timing_availability")), "Research diagnostic only", "neutral", "Informational")
    with other_2:
        render_context_card("Event context", display_value(match_detail.get("event_context")), display_value(match_detail.get("event_summary")), "neutral", "Informational")
    with other_3:
        market_context = get_market_risk_context_for_ui(PROJECT_ROOT)
        render_context_card("Market risk", display_value(market_context.get("overall_level")).replace("_", " "), display_value(market_context.get("source_coverage_status")), "neutral", "Informational")

    render_section_header("Price trend", "Completed-session chart")
    chart_df = pd.DataFrame() if match_detail.get("status") == "UNAVAILABLE" else adapters["market_data"].get_symbol_chart_data(selected_detail_sym, as_of_date=str(analysis_date))
    if not chart_df.empty:
        st.line_chart(chart_df[["Close", "EMA_20", "EMA_50"]].tail(120), width="stretch")
    else:
        render_empty_state("Price chart unavailable", "Completed-session OHLCV data is not available for this view.")

# ==============================================================================
# TAB 5 — PERFORMANCE (HISTORICAL RESEARCH VIEWER)
# ==============================================================================
with tab_performance:
    render_section_header("Performance", "Historical research viewer; separate from the live paper portfolio", "Historical", "neutral")

    perf_file = os.path.join(PROJECT_ROOT, "data", "mvp", "performance_report.json")
    equity_file = os.path.join(PROJECT_ROOT, "data", "mvp", "equity_curve.csv")

    if os.path.exists(perf_file):
        with open(perf_file, "r") as f:
            perf_json = json.load(f)

        st.caption(f"Baseline: {perf_json.get('config_version', 'MVP v1.0')} · Allocation: {perf_json.get('allocation', '7T/3V')} · ML: {perf_json.get('ml_status', 'OFF')}")

        pf1, pf2 = st.columns(2)
        with pf1:
            render_section_header("Validation split", "In-sample historical research")
            vm = perf_json.get("validation", {})
            for label, value in (("Net return", format_percent(vm.get("net_return_pct"))), ("Daily Sharpe", display_value(vm.get("daily_sharpe_ratio"))), ("Max drawdown", format_percent(vm.get("max_drawdown_pct"))), ("Win rate / trades", f"{format_percent(vm.get('win_rate_pct'), 1)} · {display_value(vm.get('executed_trades'))}")):
                render_context_card(label, value)

        with pf2:
            render_section_header("Test split", "Out-of-sample descriptive research")
            tm = perf_json.get("test_descriptive", {})
            for label, value in (("Net return", format_percent(tm.get("net_return_pct"))), ("Daily Sharpe", display_value(tm.get("daily_sharpe_ratio"))), ("Max drawdown", format_percent(tm.get("max_drawdown_pct"))), ("Win rate / trades", f"{format_percent(tm.get('win_rate_pct'), 1)} · {display_value(tm.get('executed_trades'))}")):
                render_context_card(label, value)

        if os.path.exists(equity_file):
            render_section_header("Cumulative equity", "Historical research curve")
            df_eq = pd.read_csv(equity_file)
            st.line_chart(df_eq.set_index('date')['total_equity'], width="stretch")

    # Persistent paper results remain distinct from historical research above.
    render_section_header("Paper portfolio history", "Persisted portfolio snapshots; separate from historical research", "Paper trading", "neutral")
    snapshots = adapters["execution"].get_portfolio_snapshots()
    if snapshots:
        history = pd.DataFrame([{
            "timestamp": row.snapshot_timestamp, "equity": row.portfolio_equity,
            "realized_pnl": row.realized_pnl, "unrealized_pnl": row.unrealized_pnl,
            "open_positions": row.open_positions, "reason": row.snapshot_reason,
        } for row in reversed(snapshots)])
        if history["equity"].notna().any():
            st.line_chart(history.set_index("timestamp")["equity"], width="stretch")
        st.caption(f"{len(history)} persisted snapshot(s) · latest reason: {history.iloc[-1]['reason']}")
    else:
        render_empty_state("No persisted paper-portfolio snapshots", "Snapshots are saved after completed analysis, a paper-trade record, or portfolio refresh.")

# ==============================================================================
# TAB 6 — SETTINGS (CONFIGURABLE OPTIONS)
# ==============================================================================
with tab_settings:
    render_section_header("Settings", f"Frozen policy summary; no controls are editable · {PRODUCT_VERSION}", "Frozen", "neutral")
    policy_1, policy_2, policy_3 = st.columns(3)
    with policy_1:
        render_section_header("Trading policy")
        render_context_card("Nominal ticket", "₹1L", "Fixed nominal paper-trade ticket", "neutral", "Binding")
        render_context_card("Maximum positions", "10", "Existing cash and duplicate constraints remain binding", "neutral", "Binding")
        render_context_card("Execution", "Paper trading only", "Manual recording only; no automatic order placement", "neutral")
    with policy_2:
        render_section_header("Risk policy")
        render_context_card("Validated stops", "Donchian / RS / VCP", "Static validated executable-stop coverage only", "neutral")
        render_context_card("Risk-based sizing", "Inactive", "Nominal sizing remains frozen", "unavailable")
        render_context_card("Heat limit", "Inactive", "Heat is informational; no threshold is implied", "unavailable")
    with policy_3:
        render_section_header("Intelligence")
        render_context_card("Trade economics", "Descriptive only", "Historical strategy context", "neutral", "Informational")
        render_context_card("Market risk", "Informational only", "Forward-looking broad-market context", "neutral", "Informational")
        render_context_card("ML / sentiment", "Inactive", "Not used by the cockpit", "unavailable")
    render_section_header("Operations", "Cloud deployment diagnostics", "Operational", "neutral")
    db_health = adapters["execution"].database_diagnostics()
    op1, op2, op3 = st.columns(3)
    with op1:
        render_context_card("Product", PRODUCT_VERSION, "Paper-trading decision support", "neutral")
    with op2:
        render_context_card("Deployment", db_health.get("deployment_mode", "NOT_AVAILABLE"), "Operational mode", "neutral")
    with op3:
        available = db_health.get("database_status") == "AVAILABLE"
        render_context_card("Database", f"{db_health.get('database_backend', 'NOT_AVAILABLE')} · {db_health.get('database_status', 'NOT_AVAILABLE')}", "Connection status", "good" if available else "unavailable")
