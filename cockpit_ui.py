"""Focused HA-P2 product UI. Rendering is read-only unless a labeled action is submitted."""
from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote

import pandas as pd
import streamlit as st

import database
from cockpit_cache import (
    load_closed_trade_rows, load_ha_snapshot, load_ha_summaries,
    load_market_context_bundle,
    load_open_positions, load_portfolio_pnl, load_portfolio_snapshots,
    load_portfolio_summary, load_role_evidence, load_role_learning_analytics,
)
from historical_analogs_service import HistoricalAnalogService, METHODOLOGY_HASH
from market_context import NOT_AVAILABLE, summarize_context
from interaction_architecture import canonical_route_symbol, compact_allocation, navigation_query, ordered_decisions, short_strategy_name
from live_decision_adapter import summarize_live_portfolio_risk
from performance_timing import timed
from role_learning_analytics import FAMILY_ANCHORS, INTERACTIONS, evidence_quality
from ui_components import (
    display_value, format_currency, format_percent, format_price, format_signed_currency,
    render_context_card, render_empty_state, render_metric_card, render_page_header,
    render_section_header, status_badge,
)


PAGES = ["Dashboard", "Opportunities", "Portfolio", "Stock Research", "Learning", "Settings"]
PAGE_KEYS = {"Dashboard": "today", "Opportunities": "signals", "Portfolio": "portfolio", "Stock Research": "research", "Learning": "learning", "Settings": "settings"}
STOCK_TABS = ["overview", "rally", "historical_analogs", "path_risk", "role_evidence", "events", "trade"]

PILLAR_HELP = {
    "Trend": {
        "measure": "Measures broad-market medium-term direction and drawdown using NIFTY 500 trend features.",
        "states": "SUPPORTIVE indicates positive broad-market trend; NEUTRAL is balanced; WEAKENING or DEFENSIVE indicates deteriorating direction or drawdown.",
    },
    "Breadth": {
        "measure": "Measures how broadly individual stocks participate in the market move. Broad participation is generally healthier than index strength driven by only a few stocks.",
        "states": "SUPPORTIVE means broad participation; NEUTRAL or MIXED means uneven participation; WEAK means relatively few stocks are participating positively.",
    },
    "Volatility": {
        "measure": "Measures market turbulence using India VIX and realized NIFTY 500 volatility. Higher volatility means greater price uncertainty, not necessarily falling prices.",
        "states": "LOW and NORMAL indicate contained variability; ELEVATED and HIGH indicate progressively greater price uncertainty without predicting direction.",
    },
    "Sector Participation": {
        "measure": "Measures whether strength or weakness is broad across sectors or concentrated in a small number of sectors.",
        "states": "BROAD indicates widespread participation; MIXED indicates concentration; WEAK indicates limited sector support.",
    },
    "Investor Participation": {
        "measure": "Measures FII/FPI and DII cash-market activity and institutional buying/selling pressure. It is descriptive, not a prediction.",
        "states": "SUPPORTIVE indicates net institutional buying; MIXED indicates offsetting flows; DEFENSIVE or HIGH SELLING PRESSURE indicates net selling pressure.",
    },
    "Cross-Asset": {
        "measure": "Measures external financial-market pressure using the available USDINR, Brent, US 10Y, DXY and S&P 500 inputs.",
        "states": "CALM and NORMAL indicate contained external pressure; ELEVATED and HIGH STRESS indicate progressively unusual external conditions.",
    },
    "External Risk": {
        "measure": "Highlights detected scheduled or unscheduled events that could materially affect Indian equities. LOW means no currently detected material event, not zero real-world risk.",
        "states": "LOW means no material event was detected by available sources; ELEVATED, HIGH and SEVERE reflect increasing detected materiality.",
    },
}


def stock_url(symbol: Any, tab: str = "overview") -> str:
    route = navigation_query("stock", symbol, tab)
    return f"?page=stock&symbol={quote(route['symbol'])}&tab={quote(route['tab'])}"


def _candidate_for_symbol(decisions, symbol):
    canonical = canonical_route_symbol(symbol)
    return next((row for row in decisions if canonical_route_symbol(row.get("symbol")) == canonical), None)


def _is_qualified(candidate):
    if not candidate:
        return False
    explicit = candidate.get("is_qualified")
    status = str(candidate.get("qualification_status") or "").upper()
    return explicit is True or (explicit is not False and status in {"QUALIFIED", "ALLOCATED", "PASS"})


def _load_ha(candidate):
    if not candidate:
        return None
    opportunity_id = candidate.get("opportunity_id")
    signal_date = candidate.get("signal_date") or candidate.get("analysis_date")
    if not opportunity_id or not signal_date:
        return None
    return load_ha_snapshot(str(opportunity_id), str(signal_date)[:10], METHODOLOGY_HASH)


def _load_ha_summary(candidate):
    if not candidate:
        return None
    opportunity_id = candidate.get("opportunity_id")
    signal_date = str(candidate.get("signal_date") or candidate.get("analysis_date") or "")[:10]
    if not opportunity_id or not signal_date:
        return None
    key = f"{opportunity_id}|{signal_date}"
    return load_ha_summaries(((str(opportunity_id), signal_date),), METHODOLOGY_HASH).get(key)


def _ha_label(candidate, summaries=None):
    opportunity_id = candidate.get("opportunity_id") if candidate else None
    signal_date = str(candidate.get("signal_date") or candidate.get("analysis_date") or "")[:10] if candidate else ""
    snapshot = (summaries or {}).get(f"{opportunity_id}|{signal_date}")
    if not snapshot:
        return "PENDING ANALYSIS REFRESH"
    quality = snapshot.get("evidence_quality", "INSUFFICIENT")
    count = snapshot.get("analog_count", 0)
    return f"{count} analogs · {quality}" if count else "INSUFFICIENT"


def _path_risk_state(candidate) -> str:
    state = ((candidate or {}).get("path_risk") or {}).get("state")
    return str(state or "NOT AVAILABLE").replace("_", " ")


def _link_table(frame: pd.DataFrame, *, link_column="Stock"):
    config = {link_column: st.column_config.LinkColumn(link_column, display_text=r".*symbol=([^&]+).*")}
    st.dataframe(frame, width="stretch", hide_index=True, column_config=config)


def _render_ticket(candidate, execution, hydrate_portfolio: Callable, *, key_prefix: str):
    if not candidate:
        render_empty_state("No qualified opportunity", "Run analysis before opening a paper-trade ticket.")
        return
    preview_cash = load_portfolio_summary(execution).get("current_cash_inr", 0.0)
    default_amount = min(100_000.0, max(0.0, float(preview_cash or 0.0)))
    amount = st.number_input("Investment amount (₹)", min_value=0.0, value=default_amount, step=1_000.0, key=f"{key_prefix}_amount")
    preview = execution.preview_paper_trade(candidate, amount)
    c1, c2, c3, c4 = st.columns(4)
    with c1: render_metric_card("Whole shares", preview.get("estimated_quantity"), "Integer quantity")
    with c2: render_metric_card("Deployed", format_currency(preview.get("executed_position_value_inr")), "At reference price")
    with c3: render_metric_card("Cash after", format_currency(preview.get("remaining_cash_inr")), "No leverage")
    with c4: render_metric_card("Allocator", "Selected" if preview.get("allocator_selected") else "Advisory only", "Does not control eligibility")
    with st.form(f"{key_prefix}_paper_trade_record_form"):
        confirmed = st.checkbox("I confirm this is a manual paper-trade record", key=f"{key_prefix}_confirm")
        submit = st.form_submit_button("Record paper trade", type="primary", disabled=not preview.get("valid"))
    if submit:
        if not confirmed:
            st.error("Confirm the manual paper-trade record before submitting.")
        else:
            result = execution.execute_paper_trade(candidate, amount)
            if result.get("success"):
                hydrate_portfolio(force=True)
                st.success(result["message"])
            else:
                st.error(result.get("message", "Paper-trade write failed."))


def _render_sidebar(route, execution_factory, portfolio_summary_loader, hydrate_portfolio):
    route_page = st.session_state.get("navigation_page", "today")
    label = "Stock Research" if route_page == "stock" else next((name for name, key in PAGE_KEYS.items() if key == route_page), "Dashboard")
    if st.session_state.get("sidebar_workspace") != label:
        st.session_state["sidebar_workspace"] = label

    def change_page():
        target = PAGE_KEYS[st.session_state["sidebar_workspace"]]
        st.query_params.from_dict({"page": target})

    st.sidebar.markdown("### Trading Cockpit")
    selected = st.sidebar.radio("Workspace", PAGES, key="sidebar_workspace", on_change=change_page, label_visibility="collapsed")
    st.session_state["navigation_page"] = PAGE_KEYS[selected]
    if selected in {"Dashboard", "Portfolio", "Settings"}:
        portfolio_summary = portfolio_summary_loader()
        execution = execution_factory()
        with st.sidebar.expander("Portfolio controls", expanded=False):
            capital = float(portfolio_summary.get("configured_portfolio_capital_inr", database.DEFAULT_PORTFOLIO_CAPITAL_INR))
            limit = portfolio_summary.get("configured_max_open_positions", database.DEFAULT_MAX_OPEN_POSITIONS)
            with st.form("portfolio_capital_configuration_form"):
                capital_input = st.number_input("Paper portfolio capital (₹)", min_value=1.0, value=capital, step=100_000.0)
                no_limit = st.checkbox("No position-count limit", value=limit is None)
                limit_input = st.number_input("Maximum open positions", min_value=1, value=int(limit or 10), step=1, disabled=no_limit)
                save = st.form_submit_button("Save portfolio controls")
            if save:
                result = execution.configure_portfolio(capital_input, None if no_limit else int(limit_input))
                if result.get("success"):
                    hydrate_portfolio(force=True)
                    st.success(result["message"])
                    st.rerun()
                else:
                    st.error(result.get("message"))
    else:
        st.sidebar.caption("Portfolio controls · Settings")
    st.sidebar.caption("Paper trading only · Explicit execution")
    return selected


@st.fragment
def _render_dashboard_ticket(decisions, execution_factory, hydrate_portfolio):
    if decisions:
        labels = {f"{row.get('symbol')} · {short_strategy_name(row.get('strategy'))}": row for row in ordered_decisions(decisions)}
        chosen = st.selectbox("Qualified opportunity", list(labels), key="paper_trade_symbol")
        _render_ticket(labels[chosen], execution_factory(), hydrate_portfolio, key_prefix="dashboard")
    else:
        render_empty_state("No qualified opportunities", "Run today's analysis to populate the trading workspace.")


def _pillar_models(bundle):
    structural = bundle.get("structural") or {}
    investor = bundle.get("investor_participation") or {}
    cross = bundle.get("cross_asset") or {}
    event = bundle.get("event_risk") or {}
    values = {
        "Trend": structural.get("trend") or {}, "Breadth": structural.get("breadth") or {},
        "Volatility": structural.get("volatility") or {}, "Sector Participation": structural.get("sector_participation") or {},
        "Investor Participation": investor, "Cross-Asset": cross, "External Risk": event,
    }
    subtitles = {"Trend": "Broad market direction", "Breadth": "Stock participation", "Volatility": "Market turbulence", "Sector Participation": "Sector participation", "Investor Participation": "Institutional flows", "Cross-Asset": "External market pressure", "External Risk": "Material event context"}
    timestamps = {"Trend": structural.get("as_of_timestamp"), "Breadth": structural.get("as_of_timestamp"), "Volatility": structural.get("as_of_timestamp"), "Sector Participation": structural.get("as_of_timestamp"), "Investor Participation": investor.get("as_of_timestamp"), "Cross-Asset": cross.get("as_of_timestamp"), "External Risk": event.get("as_of_timestamp")}
    provenance = {"Trend": structural.get("provenance"), "Breadth": structural.get("provenance"), "Volatility": structural.get("provenance"), "Sector Participation": structural.get("provenance"), "Investor Participation": investor.get("provenance"), "Cross-Asset": cross.get("provenance"), "External Risk": [d.get("source") for d in event.get("source_diagnostics", []) if d.get("status") == "SUCCESS"]}
    models = []
    for title, payload in values.items():
        state = payload.get("state") or NOT_AVAILABLE
        unavailable = state == NOT_AVAILABLE
        model = {"title": title, "state": "NOT AVAILABLE" if unavailable else str(state).replace("_", " "), "subtitle": "Awaiting first EOD refresh" if unavailable and title in {"Trend", "Breadth", "Volatility", "Sector Participation", "Investor Participation"} else "Awaiting first pre-open refresh" if unavailable else subtitles[title], "measure": PILLAR_HELP[title]["measure"], "states": PILLAR_HELP[title]["states"], "timestamp": timestamps[title], "provenance": provenance[title] or [], "why": "No persisted snapshot is available yet.", "coverage": None}
        if not unavailable:
            if title == "Trend":
                model["why"] = f"NIFTY 500 returned {format_percent(payload.get('nifty500_ret_20d_pct'))} over 20 sessions, sits {format_percent(payload.get('ema50_extension_pct'))} from EMA50, and is {format_percent(payload.get('drawdown_60d_pct'))} below its 60-session high."
            elif title == "Breadth":
                model["why"] = f"{format_percent(payload.get('above_ema20_pct'))} of measured stocks are above EMA20 and {format_percent(payload.get('advance_participation_pct'))} advanced in the latest session."
                model["coverage"] = f"{payload.get('coverage')} stocks measured" if payload.get("coverage") is not None else None
            elif title == "Volatility":
                model["why"] = f"India VIX is {display_value(payload.get('india_vix_level'))} ({display_value(payload.get('india_vix_percentile'))}th percentile) and realized volatility is {format_percent(payload.get('nifty500_realized_vol_20d_ann_pct'))}. Higher variability does not indicate market direction."
            elif title == "Sector Participation":
                model["why"] = f"{format_percent(payload.get('above_ema20_pct'))} of measured sector indices are above EMA20 and {format_percent(payload.get('positive_10d_pct'))} have positive 10-day returns."
                model["coverage"] = f"{payload.get('coverage')} sector indices measured" if payload.get("coverage") is not None else None
            elif title == "Investor Participation":
                model["why"] = f"FII/FPI net flow is {format_signed_currency(payload.get('fii_net_today_cr'))} crore and DII net flow is {format_signed_currency(payload.get('dii_net_today_cr'))} crore for the latest observation."
                model["coverage"] = str(payload.get("coverage") or "") or None
            elif title == "Cross-Asset":
                available = [row for row in (payload.get("series") or {}).values() if row.get("status") == "AVAILABLE"]
                strongest = sorted(available, key=lambda row: row.get("stress_percentile") if isinstance(row.get("stress_percentile"), (int, float)) else -1, reverse=True)[:3]
                drivers = ", ".join(f"{row.get('label')} ({display_value(row.get('stress_percentile'))}th stress percentile)" for row in strongest)
                model["why"] = f"External conditions are classified {str(state).lower()} from the available recent distributions. Leading contributors: {drivers}. This indicates pressure, not an equity-return forecast."
                model["coverage"] = f"{len(available)} / {len((payload.get('series') or {})) or 5} feeds available · minimum required for state calculation: {payload.get('required_core_inputs', 3)}"
            else:
                active = [row for row in event.get("events", []) if not row.get("scheduled") and row.get("status") == "ACTIVE"]
                model["why"] = f"Detected material event: {active[0].get('headline')}" if active else "No currently detected event crossed the configured materiality threshold. LOW does not mean zero real-world risk."
                model["coverage"] = f"{event.get('active_material_events', 0)} material events · {event.get('scheduled_events', 0)} scheduled events"
        models.append(model)
    return models


def _render_pillar(model):
    title_column, info_column = st.columns([0.84, 0.16], vertical_alignment="center")
    with title_column:
        st.markdown(f"**{model['title']}**")
    with info_column:
        with st.popover("ⓘ", key=f"market_context_info_{model['title']}", help=f"About {model['title']}"):
            st.markdown("**What it measures**")
            st.write(model["measure"])
            st.markdown("**How to interpret it**")
            st.write(model["states"])
            st.markdown("**Why today?**")
            st.write(model["why"])
            if model.get("coverage"):
                st.caption(model["coverage"])
            if model.get("timestamp"):
                st.caption(f"Latest data: {model['timestamp']}")
            if model.get("provenance"):
                sources = [item.get("source") or item.get("url") if isinstance(item, dict) else str(item) for item in model["provenance"]]
                st.caption(f"Source: {', '.join(filter(None, sources))}")
    render_context_card(None, model["state"], model["subtitle"], "unavailable" if model["state"] == "NOT AVAILABLE" else "neutral", "Advisory")


def _render_market_context():
    bundle = load_market_context_bundle()
    pillars = _pillar_models(bundle)
    first = st.columns(4)
    for column, model in zip(first, pillars[:4]):
        with column: _render_pillar(model)
    second = st.columns(3)
    for column, model in zip(second, pillars[4:]):
        with column: _render_pillar(model)
    st.caption(summarize_context(bundle))
    with st.expander("Raw values, timestamps & provenance", expanded=False):
        if not any(bundle.values()):
            render_empty_state("Market Context not yet refreshed", "The next EOD and pre-open refreshes will persist available modules. Missing inputs remain NOT_AVAILABLE.")
            return
        for label, key in (("Structural EOD", "structural"), ("Investor Participation", "investor_participation"), ("Cross-Asset", "cross_asset"), ("External / Event Risk", "event_risk")):
            payload = bundle.get(key)
            st.markdown(f"**{label}**")
            if payload:
                st.json(payload, expanded=False)
            else:
                st.caption(NOT_AVAILABLE)


@st.fragment
def _render_stock_ticket(candidate, execution_factory, hydrate_portfolio, symbol):
    _render_ticket(candidate, execution_factory(), hydrate_portfolio, key_prefix=f"stock_{symbol}")


def _render_dashboard(decisions, summary, execution_factory, hydrate_portfolio):
    render_page_header("Dashboard", "Today’s qualified set and paper-portfolio state")
    c1, c2, c3, c4 = st.columns(4)
    with c1: render_metric_card("Portfolio equity", format_currency(summary.get("total_portfolio_value_inr")), "Paper NAV")
    with c2: render_metric_card("Cash", format_currency(summary.get("current_cash_inr")), "Available")
    with c3: render_metric_card("Open positions", summary.get("open_positions_count", 0), "Paper positions")
    with c4: render_metric_card("Qualified", len(decisions), "Allocator remains advisory")
    render_section_header("Decision workspace", "A compact view of the current opportunity set")
    left, right = st.columns([1.55, 1])
    with left:
        pulse = [{"Symbol": canonical_route_symbol(row.get("symbol")), "Strategy": short_strategy_name(row.get("strategy")),
                  "Allocation": compact_allocation(row), "Path Risk": _path_risk_state(row),
                  "Volume": row.get("volume_ratio_20")}
                 for row in ordered_decisions(decisions)[:6]]
        if pulse:
            st.dataframe(pd.DataFrame(pulse), width="stretch", hide_index=True,
                         column_config={"Volume": st.column_config.NumberColumn(format="%.2fx")})
        else:
            render_empty_state("No qualified opportunities", "Run analysis to populate the decision workspace.")
    with right:
        render_context_card("Execution", "Manual only", "Scanning and navigation never create trades", "neutral", "Controlled")
        render_context_card("Allocator", "Advisory", "All qualified names remain tradeable within portfolio constraints")
    with st.expander("Market Context · Advisory", expanded=False):
        st.caption("Descriptive context only · no composite score · never changes qualification, allocation, sizing, or execution")
        _render_market_context()
    with st.expander("Manual paper trade", expanded=False):
        st.caption("Any qualified opportunity · explicit confirmation · no automatic execution")
        _render_dashboard_ticket(decisions, execution_factory, hydrate_portfolio)


def _render_opportunities(decisions):
    render_page_header("Opportunities", "All qualified names; allocator selection is advisory")
    if not decisions:
        render_empty_state("No qualified opportunities", "Run today's analysis to populate this page.")
        return
    identities = tuple(sorted(
        (str(row.get("opportunity_id")), str(row.get("signal_date") or row.get("analysis_date"))[:10])
        for row in decisions if row.get("opportunity_id") and (row.get("signal_date") or row.get("analysis_date"))
    ))
    summaries = load_ha_summaries(identities, METHODOLOGY_HASH) if identities else {}
    rows = []
    for row in ordered_decisions(decisions):
        symbol = canonical_route_symbol(row.get("symbol"))
        rows.append({
            "Stock": stock_url(symbol), "Symbol": symbol, "Strategy": short_strategy_name(row.get("strategy")),
            "Allocation": compact_allocation(row), "Entry": row.get("entry_price"),
            "Volume ratio": row.get("volume_ratio_20"), "Path Risk": _path_risk_state(row),
            "Historical Analogs": stock_url(symbol, "historical_analogs"),
            "HA evidence": _ha_label(row, summaries),
        })
    frame = pd.DataFrame(rows)
    st.dataframe(frame, width="stretch", hide_index=True, column_config={
        "Stock": st.column_config.LinkColumn("Stock detail", display_text="Open"),
        "Historical Analogs": st.column_config.LinkColumn("Historical Analogs", display_text="Open HA"),
        "Entry": st.column_config.NumberColumn(format="₹%.2f"),
        "Volume ratio": st.column_config.NumberColumn(format="%.2fx"),
    })


def _portfolio_overview(summary):
    values = [
        ("Portfolio Equity", summary.get("total_portfolio_value_inr")), ("Total P&L", summary.get("total_net_pnl_inr")),
        ("Realized P&L", summary.get("total_realized_pnl_inr") or summary.get("total_net_pnl_inr")),
        ("Unrealized P&L", summary.get("unrealized_pnl_inr")), ("Cash", summary.get("current_cash_inr")),
        ("Open Positions", summary.get("open_positions_count", 0)),
    ]
    columns = st.columns(3)
    for index, (label, value) in enumerate(values):
        with columns[index % 3]:
            render_metric_card(label, value if label == "Open Positions" else format_signed_currency(value) if "P&L" in label else format_currency(value), "Paper ledger")
    st.caption(f"Deployed capital: {format_currency(summary.get('open_capital_deployed_inr'))}")


def _portfolio_pnl():
    choices = ["Lifetime", "YTD", "1Y", "6M", "3M", "1M", "Custom"]
    period = st.selectbox("P&L period", choices, index=0, key="portfolio_pnl_period")
    custom_start = custom_end = None
    if period == "Custom":
        a, b = st.columns(2)
        with a: custom_start = st.date_input("Start date", dt.date.today().replace(month=1, day=1))
        with b: custom_end = st.date_input("End date", dt.date.today())
    result = load_portfolio_pnl(period, custom_start=custom_start, custom_end=custom_end)
    c1, c2, c3, c4 = st.columns(4)
    with c1: render_metric_card("Total P&L", format_signed_currency(result.get("total_pnl")), "Selected period")
    with c2: render_metric_card("Realized", format_signed_currency(result.get("realized_pnl")), "Closed-trade recognition")
    with c3: render_metric_card("Unrealized / mark", format_signed_currency(result.get("unrealized_or_mark_contribution")), "Boundary-mark contribution")
    with c4: render_metric_card("Return", format_percent(result.get("return_pct")), result.get("return_denominator", "Not available"))
    if result.get("status") != "AVAILABLE":
        st.warning("Period attribution is NOT_AVAILABLE because one or more required historical boundary marks are missing.")
        for missing in result.get("coverage", {}).get("missing_boundaries", []):
            st.caption(f"{missing['symbol']}: {', '.join(missing['missing'])}")
    render_section_header("P&L trend", "Shown only with adequate persisted dated portfolio history")
    snapshots = load_portfolio_snapshots(limit=365)
    period_start = dt.date.fromisoformat(result["period_start"]) if result.get("period_start") else None
    period_end = dt.date.fromisoformat(result["period_end"])
    usable = [row for row in reversed(snapshots) if row["portfolio_equity"] is not None
              and (period_start is None or dt.date.fromisoformat(row["snapshot_date"]) >= period_start)
              and dt.date.fromisoformat(row["snapshot_date"]) <= period_end]
    if len(usable) >= 2:
        with timed("chart.portfolio_pnl", source_rows=len(usable)):
            chart = pd.DataFrame({"date": [row["snapshot_date"] for row in usable], "Portfolio equity": [row["portfolio_equity"] for row in usable]}).drop_duplicates("date", keep="last")
            if len(chart) > 400:
                stride = max(1, len(chart) // 398)
                chart = pd.concat([chart.iloc[[0]], chart.iloc[1:-1:stride], chart.iloc[[-1]]]).drop_duplicates("date")
        if len(chart) >= 2: st.line_chart(chart.set_index("date"), width="stretch")
        else: render_empty_state("Trend unavailable", "At least two dated portfolio marks are required.")
    else:
        render_empty_state("Trend unavailable", "At least two dated portfolio marks are required; no prices are backfilled.")
    render_section_header("Stock contribution", "Rows reconcile to portfolio totals when coverage is complete")
    rows = []
    for row in result.get("stock_contributions", []):
        rows.append({"Stock": stock_url(row["symbol"]), "Symbol": row["symbol"], "Realized": row["realized_pnl"],
                     "Unrealized / mark": row["unrealized_or_mark_contribution"], "Total contribution": row["total_pnl"]})
    if rows:
        frame = pd.DataFrame(rows)
        frame["_sort"] = frame["Total contribution"].abs().fillna(-1)
        frame = frame.sort_values(["_sort", "Symbol"], ascending=[False, True]).drop(columns="_sort")
        st.dataframe(frame, width="stretch", hide_index=True, column_config={"Stock": st.column_config.LinkColumn("Stock", display_text="Open")})
    else: render_empty_state("No contribution rows", "No trades fall within this period.")


def _portfolio_positions(positions, execution, hydrate_portfolio):
    if st.button("Refresh Prices", key="refresh_portfolio_prices", disabled=not positions):
        result = execution.refresh_portfolio_positions()
        hydrate_portfolio(force=True, positions=result.get("positions", []))
        positions = result.get("positions", [])
        st.success(f"Prices refreshed: {result.get('successful_marks', 0)} / {result.get('open_positions', 0)}")
    if not positions:
        render_empty_state("No open positions", "Explicitly record a qualified paper trade to begin tracking.")
        return
    rows = []
    for row in positions:
        current = row.get("current_price")
        rows.append({"Stock": stock_url(row["symbol"]), "Symbol": row["symbol"], "Strategy": short_strategy_name(row.get("strategy_used")),
                     "Entry price": row.get("entry_price"), "Current price": current, "P&L ₹": row.get("unrealized_pnl_inr"),
                     "P&L %": row.get("unrealized_pnl_pct"), "Entry value": row.get("position_value"),
                     "Current value": current * row.get("quantity", 0) if current is not None else None,
                     "Executable stop": row.get("initial_executable_stop") if row.get("executable_stop_enabled") else None})
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True, column_config={"Stock": st.column_config.LinkColumn("Stock", display_text="Open")})
    with st.expander("Close a paper position", expanded=False):
        choices = {f"{row.get('symbol')} · trade #{row.get('id')}": row for row in positions}
        label = st.selectbox("Position", list(choices), key="manual_close_position")
        position = choices[label]
        reference = max(0.01, float(position.get("current_price") or position.get("entry_price") or 0.01))
        with st.form("manual_paper_close_form"):
            exit_price = st.number_input("Manual exit price (₹)", min_value=0.01, value=reference, step=0.05)
            confirmed = st.checkbox("I confirm this manual paper-position close")
            close = st.form_submit_button("Close paper position")
        if close:
            if not confirmed:
                st.error("Confirm the manual paper-position close before submitting.")
            else:
                result = execution.close_paper_trade(int(position["id"]), float(exit_price))
                if result.get("success"):
                    hydrate_portfolio(force=True)
                    st.success(result["message"])
                    st.rerun()
                else:
                    st.error(result.get("message"))


def _portfolio_closed():
    trades = load_closed_trade_rows(limit=250)
    if not trades:
        render_empty_state("No closed trades", "Closed paper trades will appear here.")
        return
    rows = [{"Stock": stock_url(t["symbol"]), "Symbol": t["symbol"], "Strategy": short_strategy_name(t["strategy_used"]),
             "Entry": t["entry_price"], "Exit": t["exit_price"], "Quantity": t["quantity"], "Realized P&L": t["realized_pnl"],
             "Closed": t["exit_date"]} for t in trades]
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True, column_config={"Stock": st.column_config.LinkColumn("Stock", display_text="Open")})


def _portfolio_risk(positions):
    risk = summarize_live_portfolio_risk(positions)
    c1, c2 = st.columns(2)
    with c1: render_metric_card("Reference Heat", risk["reference_heat_pct"], f"Coverage {risk['positions_with_reference']} / {len(positions)}")
    with c2: render_metric_card("Executable Stop Heat", risk["executable_stop_heat_pct"], f"Coverage {risk['positions_with_executable_stop']} / {len(positions)}")
    st.caption("Reference Heat is informational. Executable Stop Heat is supplementary. Neither is a maximum-loss estimate.")


@st.fragment
def _render_portfolio(execution_factory, portfolio_summary_loader, positions_loader, hydrate_portfolio):
    render_page_header("Portfolio", "Paper-ledger accounting, contribution and risk")
    tabs = ["Performance", "Positions & Risk"]
    if st.session_state.get("portfolio_view") not in {None, *tabs}:
        st.session_state["portfolio_view"] = "Performance"
    selected = st.segmented_control("Portfolio view", tabs, default="Performance", key="portfolio_view")
    with timed("portfolio.selected_view", view=selected):
        if selected == "Performance":
            _portfolio_overview(portfolio_summary_loader())
            render_section_header("Performance history", "Period P&L and contribution")
            _portfolio_pnl()
        else:
            positions = positions_loader()
            render_section_header("Open positions", "Current paper positions and marks")
            _portfolio_positions(positions, execution_factory(), hydrate_portfolio)
            render_section_header("Closed positions", "Completed paper trades")
            _portfolio_closed()
            render_section_header("Portfolio risk", "Measured stop-risk coverage")
            _portfolio_risk(positions)


@st.fragment
def _render_ha_cases(candidate, provided_snapshot=None):
    render_section_header("Analog cases", "Frozen K40 mapping loads only when requested")
    show_cases = st.toggle("Inspect the 40 closest analog cases", value=False, key=f"ha_cases_{candidate.get('opportunity_id')}")
    if not show_cases:
        st.caption("Case rows are deferred to keep the summary interaction fast.")
        return
    full_snapshot = provided_snapshot or _load_ha(candidate)
    cases = []
    for row in (full_snapshot or {}).get("analogs", []):
        cases.append({"Rank": row["rank"], "Historical date": row["signal_date"], "Historical symbol": row["symbol"],
                      "Distance": row["distance"], "Prior 10D move": row.get("ret_10d"),
                      "Nifty 500 10D": row.get("nifty500_ret_10d"), "MFE 10D": row.get("mfe_10d"),
                      "MAE 10D": row.get("mae_10d"), "+5/-3 result": row.get("target_5_before_stop_3_20d")})
    st.dataframe(pd.DataFrame(cases), width="stretch", hide_index=True)


def _render_ha(candidate, snapshot, *, generic_state=False):
    if not snapshot:
        if generic_state:
            render_empty_state("Generic-state analogs unavailable", "Adequate completed-session stock, Nifty 500 and India VIX history is required.")
        else:
            render_empty_state("Historical Analog snapshot pending", "This persisted opportunity predates live HA enrichment. Run analysis once to create its immutable snapshot.")
        return
    quality = snapshot.get("evidence_quality", "INSUFFICIENT")
    st.markdown(f"{status_badge(quality, 'good' if quality == 'HIGH' else 'warn' if quality in {'MEDIUM','LOW'} else 'unavailable')}", unsafe_allow_html=True)
    if generic_state:
        st.caption("Generic completed-session state · research only · does not confer qualification or trade eligibility")
    st.caption(f"{snapshot.get('analog_count', 0)} analogs across {snapshot.get('unique_security_count', 0)} securities · {snapshot.get('earliest_analog_date')}–{snapshot.get('latest_analog_date')} · Historical evidence, not a forecast")
    attractive, downside = snapshot.get("outcome_attractiveness", {}), snapshot.get("downside_evidence", {})
    c1, c2, c3, c4 = st.columns(4)
    with c1: render_metric_card("Median 10D upside potential", format_percent(attractive.get("median_mfe_10d")), "Historical MFE")
    with c2: render_metric_card("Median 20D upside potential", format_percent(attractive.get("median_mfe_20d")), "Historical MFE")
    with c3: render_metric_card("Median adverse move", format_percent(downside.get("median_mae_10d")), "Historical 10D MAE")
    with c4: render_metric_card("+5% before -3%", format_percent((attractive.get("plus_5_before_minus_3_rate") or 0) * 100), "Historical success rate")
    render_section_header("Evidence diversity", "Concentration is disclosed without changing retrieval")
    d1, d2, d3, d4 = st.columns(4)
    with d1: render_context_card("Analog count", snapshot.get("analog_count"))
    with d2: render_context_card("Unique securities", snapshot.get("unique_security_count"))
    with d3: render_context_card("Maximum year share", format_percent((snapshot.get("maximum_year_share") or 0) * 100))
    with d4: render_context_card("Maximum date share", format_percent((snapshot.get("maximum_date_share") or 0) * 100))
    if (snapshot.get("maximum_year_share") or 0) >= .40:
        st.info("Historical evidence is concentrated in a smaller number of market periods.")
    _render_ha_cases(candidate, snapshot if generic_state else None)


def _render_trade_economics(candidate):
    economics = candidate.get("trade_economics") or {}
    render_section_header("Trade economics", "Historical strategy context only")
    if not economics or economics.get("display_sample_source") == "INSUFFICIENT":
        render_context_card("Historical sample", "Insufficient", "No forecast or target is inferred.")
        return
    historical = economics.get("historical_return_context") or {}
    columns = st.columns(3)
    with columns[0]: render_context_card("Historical sample", f"{display_value(economics.get('display_sample_source'))} · N={display_value(economics.get('sample_count'))}", display_value(economics.get("sample_quality")))
    with columns[1]: render_context_card("Median return", format_percent(historical.get("median_return")), f"Win rate {format_percent(historical.get('win_rate'))}")
    with columns[2]: render_context_card("Profit factor", display_value(historical.get("profit_factor")), f"P10 {format_percent(historical.get('p10_return'))}")


def _render_events(candidate):
    context = candidate.get("event_context", "NOT_AVAILABLE")
    render_context_card("Event context", display_value(context).replace("_", " "), display_value(candidate.get("event_summary")))
    event = candidate.get("top_event")
    if event:
        st.markdown(f"**{display_value(event.get('headline'))}**")
        st.caption(" · ".join(filter(None, [display_value(event.get("source")), display_value(event.get("materiality")), display_value(event.get("expected_horizon"))])))
        if event.get("source_url_or_id") not in {None, "NOT_AVAILABLE"}:
            st.markdown(f"[Authoritative/source evidence]({event['source_url_or_id']})")
    else:
        st.caption("No persisted material company event was available at the analysis cutoff.")
    st.caption("Informational only; this does not alter qualification, allocation, sizing, or stops.")


@st.cache_data(ttl=3600, show_spinner=False)
def _stock_catalog():
    """Current Nifty 500 company/symbol options for Streamlit's searchable selector."""
    path = Path(__file__).resolve().parent / "data/universe/nifty500_constituents.csv"
    frame = pd.read_csv(path, usecols=["symbol", "company_name"])
    frame["symbol"] = frame["symbol"].map(canonical_route_symbol)
    frame = frame.dropna(subset=["symbol"]).drop_duplicates("symbol").sort_values(["company_name", "symbol"])
    return tuple((str(row.symbol), str(row.company_name)) for row in frame.itertuples(index=False))


@st.cache_data(ttl=900, show_spinner=False)
def _load_stock_research(symbol: str, as_of_date: str | None = None):
    """Explicit, cached lookup for a non-opportunity stock; no portfolio writes."""
    from screener import fetch_ha_market_histories, fetch_stock_data
    stock = fetch_stock_data(symbol, period="2y", as_of_date=as_of_date)
    if stock is None or stock.empty:
        return None
    signal_date = stock.index[-1].strftime("%Y-%m-%d")
    nifty500, vix = fetch_ha_market_histories(period="2y", as_of_date=signal_date)
    features, percentiles = {}, {}
    if nifty500 is not None and vix is not None:
        try:
            state = HistoricalAnalogService.build_causal_query_state(stock, nifty500, vix, signal_date)
            features, percentiles = state["ha_features"], state["ha_stock_percentiles"]
        except Exception:
            features, percentiles = {}, {}
    close = pd.to_numeric(stock["Close"], errors="coerce")
    chart = pd.DataFrame({"Close": close, "EMA 20": close.ewm(span=20, adjust=False).mean(), "EMA 50": close.ewm(span=50, adjust=False).mean()}).tail(120)
    return {"symbol": canonical_route_symbol(symbol), "signal_date": signal_date, "entry_price": float(close.iloc[-1]),
            "ha_features": features, "ha_stock_percentiles": percentiles,
            "chart": {name: {str(index): float(value) for index, value in series.dropna().items()} for name, series in chart.items()}}


@st.cache_data(ttl=900, show_spinner=False)
def _load_generic_ha(symbol: str, signal_date: str, features: dict, percentiles: dict):
    if not features or not percentiles:
        return None
    state = {
        "opportunity_id": f"GENERIC:{symbol}:{signal_date}", "symbol": symbol,
        "signal_date": signal_date, "qualification_status": "RESEARCH_ONLY", "is_qualified": False,
        "ha_features": features, "ha_stock_percentiles": percentiles,
    }
    try:
        return HistoricalAnalogService().evaluate_generic_state(state)
    except Exception:
        return None


def _generic_candidate(research):
    if not research:
        return None
    return {
        **research,
        "opportunity_id": f"GENERIC:{research['symbol']}:{research['signal_date']}",
        "qualification_status": "RESEARCH_ONLY", "is_qualified": False,
        "strategy": None, "allocation_status": "RESEARCH_ONLY",
        "allocation_reason_text": "Research context only; this stock is not a current qualified opportunity.",
        "event_context": "NOT_AVAILABLE",
    }


def _render_stock_research(decisions, execution_factory, hydrate_portfolio):
    render_page_header("Stock Research", "Search the Nifty 500 by company or symbol; research access does not change trade eligibility")
    catalog = list(_stock_catalog())
    known = {symbol for symbol, _ in catalog}
    for row in decisions:
        symbol = canonical_route_symbol(row.get("symbol"))
        if symbol and symbol not in known:
            catalog.append((symbol, symbol))
            known.add(symbol)
    labels = {f"{company} · {symbol}": symbol for symbol, company in catalog}
    current = st.session_state.get("research_symbol")
    current_label = next((label for label, symbol in labels.items() if symbol == current), None)
    selected_label = st.selectbox(
        "Company or NSE symbol", options=list(labels), index=list(labels).index(current_label) if current_label else None,
        placeholder="Type a company name or ticker", key="research_stock_selector",
    )
    if selected_label:
        st.session_state["research_symbol"] = labels[selected_label]
    chosen = st.session_state.get("research_symbol")
    if not chosen:
        st.caption("Start typing a company name or NSE ticker to search the current Nifty 500 universe.")
        return
    qualified = _candidate_for_symbol(decisions, chosen)
    if qualified:
        st.success("This stock is a current qualified opportunity.")
        candidate = qualified
    else:
        with st.spinner(f"Loading completed-session data for {chosen}…"):
            research = _load_stock_research(chosen)
        candidate = _generic_candidate(research)
    if not candidate:
        render_empty_state("Stock data unavailable", "No adequate completed-session NSE history was returned for this symbol.")
        return
    _render_stock_detail(
        candidate, {"page": "stock", "symbol": chosen, "tab": "overview"},
        execution_factory, hydrate_portfolio, show_header=False, sync_query=False,
    )


def _render_role_evidence(candidate):
    """Read-only ROLE-R2 presentation; loaded only when its tab is selected."""
    render_section_header("ROLE Evidence", "Your own recommendation-outcome history · Advisory only")
    opportunity_id = str(candidate.get("opportunity_id") or "")
    signal_date = str(candidate.get("signal_date") or candidate.get("analysis_date") or "")[:10]
    report = load_role_evidence(opportunity_id, signal_date) if opportunity_id and signal_date else None
    if not report:
        render_empty_state("Recommendation evidence unavailable", "No immutable recommendation snapshot exists for this opportunity.")
        return

    quality = str(report.get("evidence_quality") or "INSUFFICIENT")
    baseline = report.get("system_baseline") or {}
    baseline_n = int(baseline.get("mature_sample_size") or 0)
    comparable_n = int(report.get("effective_comparable_sample_size") or 0)
    origins = report.get("prospective_vs_backfilled_evidence") or {}
    horizon = int(report.get("primary_horizon_sessions") or 10)
    c1, c2, c3, c4 = st.columns(4)
    with c1: render_context_card("Evidence state", report.get("status") or quality, f"Primary horizon · {horizon}D")
    with c2: render_context_card("Comparable recommendations", f"N={comparable_n}", "Effective mature comparable sample")
    with c3: render_context_card("System baseline", _role_metric(baseline.get("plus_5_before_minus_3_success_pct")), f"+5% before -3% · N={baseline_n}")
    with c4: render_context_card("Evidence origin", f"{int(origins.get('PROSPECTIVE') or 0)} / {int(origins.get('BACKFILL') or 0)}", "Prospective / backfilled")

    if quality == "INSUFFICIENT":
        st.warning("Current evidence is too limited for a calibrated recommendation view.")
        for reason in report.get("insufficiency_reasons") or []:
            st.caption(f"Reason: {reason}")
    else:
        render_section_header("Supported estimates", f"Shrunk toward the system baseline · N={comparable_n} · {quality}")
        values = [
            ("Estimated +5% before -3%", "estimated_plus_5_before_minus_3_success_pct"),
            ("Estimated MFE", "estimated_mfe_pct"),
            ("Estimated MAE", "estimated_mae_pct"),
            ("Estimated close return", "estimated_close_return_pct"),
        ]
        columns = st.columns(4)
        for index, (label, key) in enumerate(values):
            with columns[index]: render_context_card(label, _role_metric(report.get(key)), f"{horizon}D · N={comparable_n} · {quality}")
        estimate = report.get("estimated_plus_5_before_minus_3_success_pct")
        baseline_success = baseline.get("plus_5_before_minus_3_success_pct")
        if isinstance(estimate, (int, float)) and isinstance(baseline_success, (int, float)):
            st.caption(f"Baseline comparison: {estimate - baseline_success:+.2f} percentage points · System baseline N={baseline_n}")
        for label, key in (("Positive learned drivers", "positive_learned_drivers"), ("Negative learned drivers", "negative_learned_drivers")):
            drivers = report.get(key) or []
            if drivers:
                st.markdown(f"**{label}**")
                for driver in drivers:
                    st.caption(f"{driver.get('driver')} · {driver.get('state')} · N={int(driver.get('sample_size') or 0)} · {float(driver.get('success_difference_vs_baseline_pp') or 0):+.2f} pp vs baseline")

    unsupported = report.get("unavailable_or_unsupported_drivers") or []
    if unsupported:
        with st.expander("Drivers without enough evidence", expanded=False):
            for driver in unsupported:
                sample = f" · N={int(driver['sample_size'])}" if driver.get("sample_size") is not None else ""
                st.caption(f"{driver.get('driver')} · {driver.get('state')} · Not enough evidence · {driver.get('reason')}{sample}")

    with st.expander("How ROLE evidence works", expanded=False):
        st.markdown(
            "ROLE learns from outcomes of this cockpit's own immutable recommendations. "
            "Historical Analogs instead compares the current market state with a broader historical research pool.\n\n"
            "Evidence states progress from **INSUFFICIENT** to **EARLY**, **DEVELOPING**, and **STRONG** "
            "only as sample size and data coverage improve. Estimates are suppressed whenever the baseline "
            "or comparable drivers fail the existing evidence gates.\n\n"
            "**Advisory only:** ROLE never changes qualification, ranking, allocation, sizing, execution, or exits."
        )
    st.caption(f"Methodology: {report.get('methodology_version', 'NOT_AVAILABLE')} · {report.get('methodology_hash', 'NOT_AVAILABLE')}")


def _render_path_risk(candidate):
    payload = candidate.get("path_risk") or {}
    state = _path_risk_state(candidate)
    st.info("Path Risk measures downside-path adversity, not expected direction. HIGH risk can still have substantial upside potential.")
    c1, c2, c3, c4 = st.columns(4)
    with c1: render_context_card("10D Path Risk", state, "Advisory only", "unavailable" if state == "NOT AVAILABLE" else "neutral")
    with c2: render_context_card("Adverse barrier", format_percent(payload.get("adverse_barrier_probability") * 100) if isinstance(payload.get("adverse_barrier_probability"), (int, float)) else "Not available", "Probability of reaching -3%")
    with c3: render_context_card("Predicted adversity", format_percent(-payload.get("predicted_adverse_magnitude_pct")) if isinstance(payload.get("predicted_adverse_magnitude_pct"), (int, float)) else "Not available", "Expected MAE / adverse magnitude context")
    with c4: render_context_card("Feature coverage", format_percent(payload.get("feature_coverage_pct")), "Recommendation-time inputs")
    if state == "NOT AVAILABLE":
        missing = payload.get("missing_features") or []
        reason = payload.get("reason") or (f"Missing required state: {', '.join(missing)}" if missing else "No persisted Path Risk snapshot is available for this opportunity.")
        st.warning(reason)
    else:
        context = payload.get("baseline_context") or {}
        bucket = context.get("state_bucket") or {}
        st.caption(
            f"Historical PR-R1 context only · Overall -3% reach rate {format_percent(context.get('overall_minus_3_reached_pct'))} "
            f"across N={context.get('research_sample_n', 'Not available')} untouched-test opportunities. "
            f"The {state} bucket's historical median 10D MAE was {format_percent(bucket.get('median_mae_10d'))}; these are research averages, not an individual forecast."
        )
        drivers = payload.get("why_today") or []
        render_section_header("Why today?", "Largest direct contributions from the frozen dual-head model")
        for driver in drivers:
            label = str(driver.get("feature") or "feature").replace("_", " ")
            barrier = "higher" if driver.get("barrier_direction") == "HIGHER" else "lower"
            magnitude = "higher" if driver.get("magnitude_direction") == "HIGHER" else "lower"
            st.caption(f"{label.title()} · barrier contribution {barrier} · magnitude contribution {magnitude}")
    with st.expander("Methodology & provenance", expanded=False):
        st.caption(f"Methodology: {payload.get('methodology_version', 'NOT AVAILABLE')} · {payload.get('methodology_hash', 'NOT AVAILABLE')}")
        st.caption(f"Artifact SHA-256: {payload.get('artifact_sha256', 'NOT AVAILABLE')}")
        st.caption(f"As of: {payload.get('as_of_timestamp', 'NOT AVAILABLE')} · Source: {payload.get('provenance', 'NOT AVAILABLE')}")
        st.markdown("Frozen inference only. No sklearn fitting, provider calls, qualification changes, ranking changes, or execution effects occur during navigation.")


def _render_stock_detail(candidate, route, execution_factory, hydrate_portfolio, *, show_header=True, sync_query=True):
    symbol = route.get("symbol")
    qualified = _is_qualified(candidate)
    if show_header:
        render_page_header(
            symbol or "Stock Detail",
            "Qualified-opportunity intelligence and explicit paper execution" if qualified else "Completed-session stock research · execution disabled",
        )
    if not candidate:
        render_empty_state("Stock detail unavailable", "This symbol is not in the current qualified opportunity set.")
        return
    badges = [status_badge("QUALIFIED", "good")] if qualified else [status_badge("RESEARCH ONLY", "neutral")]
    if qualified:
        badges.extend([status_badge(short_strategy_name(candidate.get("strategy")), "neutral"), status_badge(compact_allocation(candidate), "neutral")])
    st.markdown(" &nbsp; ".join(badges), unsafe_allow_html=True)
    labels = {"overview": "Overview", "rally": "Rally", "historical_analogs": "Historical Analogs"}
    if qualified:
        labels["path_risk"] = "Path Risk"
        labels["role_evidence"] = "ROLE Evidence"
    labels.update({"events": "Events", "trade": "Trade"})
    query_tab = st.query_params.get("tab")
    tab_key = f"stock_tab_{symbol}"
    route_key = f"{tab_key}_route"
    if sync_query:
        current = query_tab if query_tab in STOCK_TABS else route.get("tab") if route.get("tab") in STOCK_TABS else "overview"
    else:
        current = st.session_state.get(route_key, "overview")
    if current not in labels:
        current = "overview"
    if st.session_state.get(route_key) != current:
        st.session_state[tab_key] = labels[current]
        st.session_state[route_key] = current

    def change_stock_tab():
        selected_key = next(key for key, value in labels.items() if value == st.session_state[tab_key])
        st.session_state[route_key] = selected_key
        if sync_query:
            st.query_params.from_dict(navigation_query("stock", symbol, selected_key))

    selected_label = st.segmented_control(
        "Stock detail view", list(labels.values()), default=None, key=tab_key,
        on_change=change_stock_tab,
    )
    selected = next(key for key, value in labels.items() if value == selected_label)
    if selected == "overview":
        c1, c2, c3 = st.columns(3)
        features = candidate.get("ha_features") or {}
        with c1: render_context_card("Reference price" if qualified else "Latest close", format_price(candidate.get("entry_price")), f"{'Signal date' if qualified else 'As of'} {display_value(candidate.get('signal_date'))}")
        with c2: render_context_card("Volume ratio", display_value(candidate.get("volume_ratio_20") or features.get("volume_ratio_20")), "Qualification context" if qualified else "Completed-session OHLCV")
        with c3: render_context_card("Allocator" if qualified else "Decision eligibility", "Selected" if candidate.get("allocation_status") == "ALLOCATED" else "Not selected" if qualified else "Research only", "Advisory only" if qualified else "Paper execution requires a qualified opportunity")
        st.caption(display_value(candidate.get("allocation_reason_text"), "Qualified under the frozen technical contract."))
        _render_trade_economics(candidate)
        with st.expander("Price trend", expanded=False):
            chart_key = f"stock_chart_{symbol}"
            if st.button("Load completed-session chart", key=f"load_{chart_key}"):
                st.session_state[chart_key] = True
            if st.session_state.get(chart_key):
                research = _load_stock_research(symbol)
                chart_payload = (research or {}).get("chart") or {}
                if chart_payload:
                    chart = pd.DataFrame({name: pd.Series(values, dtype=float) for name, values in chart_payload.items()})
                    st.line_chart(chart, width="stretch")
                else:
                    render_empty_state("Price chart unavailable", "Completed-session OHLCV is unavailable for this symbol.")
            else:
                st.caption("Loaded only when requested.")
    elif selected == "rally":
        fields = [("5D return", "ret_5d"), ("10D return", "ret_10d"), ("20D return", "ret_20d"),
                  ("EMA20 extension", "distance_from_ema20_pct"), ("ATR extension", "distance_from_ema20_atr"),
                  ("Largest positive session", "largest_positive_daily_return_10d"), ("Positive sessions", "positive_sessions_10")]
        columns = st.columns(3)
        for index, (label, key) in enumerate(fields):
            value = candidate.get(key) or (candidate.get("ha_features") or {}).get(key)
            with columns[index % 3]: render_context_card(label, format_percent(value) if key != "positive_sessions_10" else display_value(value), "Descriptive only")
    elif selected == "historical_analogs":
        if qualified:
            _render_ha(candidate, _load_ha_summary(candidate))
        else:
            with st.spinner("Retrieving generic-state historical analogs…"):
                generic_snapshot = _load_generic_ha(
                    symbol, candidate.get("signal_date"), candidate.get("ha_features") or {},
                    candidate.get("ha_stock_percentiles") or {},
                )
            _render_ha(candidate, generic_snapshot, generic_state=True)
    elif selected == "path_risk": _render_path_risk(candidate)
    elif selected == "role_evidence": _render_role_evidence(candidate)
    elif selected == "events": _render_events(candidate)
    else:
        if not qualified:
            render_empty_state("Research only", "Paper execution is available only when this stock becomes a current qualified opportunity.")
            return
        snapshot = _load_ha_summary(candidate)
        st.caption(f"Qualified · {'allocator selected' if candidate.get('allocation_status') == 'ALLOCATED' else 'allocator not selected'} · Prior 10D rally {format_percent((candidate.get('ha_features') or {}).get('ret_10d'))} · HA evidence {snapshot.get('evidence_quality') if snapshot else 'INSUFFICIENT'}")
        _render_stock_ticket(candidate, execution_factory, hydrate_portfolio, symbol)


def _role_horizon_summary(analytics: dict[str, Any], horizon: int) -> dict[str, Any]:
    baseline = analytics.get("baseline") or {}
    if horizon == 10:
        return baseline
    summary = dict((baseline.get("secondary_context") or {}).get(f"{horizon}d") or {})
    total = int(analytics.get("recommendation_count") or 0)
    sample = int(summary.get("mature_sample_size") or 0)
    coverage = 100.0 * sample / total if total else 0.0
    summary["evidence_quality"] = evidence_quality(sample, coverage)
    summary["data_coverage"] = {"outcome_coverage_pct": coverage}
    return summary


def _role_metric(value: Any) -> str:
    return format_percent(value) if isinstance(value, (int, float)) else "Not available"


def _role_cohort_rows(cohorts: list[dict[str, Any]], baseline_quality: str) -> list[dict[str, Any]]:
    rows = []
    for cohort in cohorts:
        quality = str(cohort.get("evidence_quality") or "INSUFFICIENT")
        coverage = (cohort.get("data_coverage") or {}).get("evidence_coverage_pct")
        difference = cohort.get("difference_vs_system_baseline") or {}
        comparable = quality != "INSUFFICIENT" and baseline_quality != "INSUFFICIENT"
        success_difference = difference.get("plus_5_before_minus_3_success_pct_difference")
        return_difference = difference.get("median_close_return_pct_difference")
        comparison = "Insufficient evidence"
        if comparable and isinstance(success_difference, (int, float)) and isinstance(return_difference, (int, float)):
            comparison = f"Success {success_difference:+.1f} pp · Return {return_difference:+.2f} pp"
        rows.append({
            "Cohort": cohort.get("cohort"), "N": int(cohort.get("mature_sample_size") or 0),
            "Coverage": _role_metric(coverage), "Evidence": quality,
            "+5 before -3": _role_metric(cohort.get("plus_5_before_minus_3_success_pct")),
            "Median MFE": _role_metric(cohort.get("median_mfe_pct")),
            "Median MAE": _role_metric(cohort.get("median_mae_pct")),
            "Median close return": _role_metric(cohort.get("median_close_return_pct")),
            "Vs baseline": comparison,
        })
    return rows


def _render_role_cohorts(title: str, subtitle: str, cohorts: list[dict[str, Any]], baseline_quality: str):
    with st.expander(title, expanded=title == "Strategy"):
        st.caption(subtitle)
        rows = _role_cohort_rows(cohorts, baseline_quality)
        if rows:
            st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
        else:
            st.info("INSUFFICIENT evidence · No 10D observations currently have the required persisted state.")


def _render_learning():
    """Read-only ROLE-R1 consumer; never observes outcomes or calls providers."""
    render_page_header("Learning", "Explainable recommendation-outcome research · Advisory only")
    analytics = load_role_learning_analytics()
    total = int(analytics.get("recommendation_count") or 0)
    maturity = analytics.get("maturity_coverage") or {}
    baseline = analytics.get("baseline") or {}
    baseline_quality = str(baseline.get("evidence_quality") or "INSUFFICIENT")
    origins = (baseline.get("data_coverage") or {}).get("origin_10d_counts") or {}

    st.caption(
        f"{total} recommendations · {int(maturity.get('10d') or 0)} mature at 10D · "
        f"Prospective 10D: {int(origins.get('PROSPECTIVE') or 0)} · Evidence: {baseline_quality}"
    )
    c1, c2, c3, c4 = st.columns(4)
    with c1: render_metric_card("Recommendations", total, "Immutable ledger snapshots")
    with c2: render_metric_card("Mature outcomes", f"{int(maturity.get('5d') or 0)} / {int(maturity.get('10d') or 0)} / {int(maturity.get('20d') or 0)}", "5D / 10D / 20D")
    with c3: render_metric_card("10D origin", f"{int(origins.get('PROSPECTIVE') or 0)} / {int(origins.get('BACKFILL') or 0)}", "Prospective / backfilled")
    with c4: render_metric_card("Primary evidence", baseline_quality, "Primary horizon · 10D")

    render_section_header("System baseline", "Recommendation outcomes at the selected matured horizon")
    selected = st.segmented_control("Outcome horizon", ["5D", "10D", "20D"], default="10D", key="role_horizon") or "10D"
    horizon = int(selected[:-1])
    summary = _role_horizon_summary(analytics, horizon)
    sample = int(summary.get("mature_sample_size") or 0)
    quality = str(summary.get("evidence_quality") or "INSUFFICIENT")
    evidence_note = f"N={sample} · Evidence {quality}"
    m1, m2, m3, m4 = st.columns(4)
    with m1: render_metric_card("+5% before -3%", _role_metric(summary.get("plus_5_before_minus_3_success_pct")), evidence_note)
    with m2: render_metric_card("Median MFE", _role_metric(summary.get("median_mfe_pct")), evidence_note)
    with m3: render_metric_card("Median MAE", _role_metric(summary.get("median_mae_pct")), evidence_note)
    with m4: render_metric_card("Median close return", _role_metric(summary.get("median_close_return_pct")), evidence_note)

    render_section_header("What ROLE is learning", "Frozen 10D family anchors; insufficient evidence never produces a comparison")
    _render_role_cohorts("Strategy", "Outcomes grouped by the strategy recorded at recommendation time.", analytics.get("strategy_cohorts") or [], baseline_quality)
    family_labels = {
        "price_state": "Price State", "participation": "Participation", "relative_demand": "Relative Demand",
        "volatility_state": "Volatility State", "price_response": "Price Response", "positioning": "Positioning",
        "liquidity": "Liquidity", "environment": "Environment",
    }
    family_data = analytics.get("family_cohorts") or {}
    for family, label in family_labels.items():
        payload = family_data.get(family) or {}
        anchor = payload.get("anchor_field") or FAMILY_ANCHORS[family]
        _render_role_cohorts(label, f"Predefined anchor: {str(anchor).replace('_', ' ')}.", payload.get("cohorts") or [], baseline_quality)

    render_section_header("Predefined interactions", "Only the four frozen ROLE-R1 interactions are evaluated")
    interaction_labels = {
        "relative_demand_x_participation": "Relative Demand × Participation",
        "price_response_x_participation": "Price Response × Participation",
        "strategy_x_breadth": "Strategy × Breadth",
        "volatility_x_strategy": "Volatility × Strategy",
    }
    interaction_data = analytics.get("interaction_cohorts") or {}
    for key in INTERACTIONS:
        _render_role_cohorts(interaction_labels[key], "Predefined interaction · no automatic feature mining.", (interaction_data.get(key) or {}).get("cohorts") or [], baseline_quality)

    with st.expander("How to read ROLE evidence", expanded=False):
        st.markdown(
            "ROLE observes realized outcomes of immutable recommendations; it does not create signals. "
            "**INSUFFICIENT** means N<10 or coverage<50%; **EARLY** means N<30 or coverage<70%; "
            "**DEVELOPING** means N<100 or coverage<90%; **STRONG** requires N≥100 and coverage≥90%. "
            "Backfilled rows reconstruct causal history where possible; prospective rows were captured live. "
            "Small samples are exploratory and not actionable. ROLE is research/advisory only and cannot alter qualification, ranking, sizing, allocation, execution, or exits."
        )


def render_cockpit(*, route, decisions_loader, execution_factory,
                   portfolio_summary_loader, positions_loader, hydrate_portfolio):
    """Route first, then invoke only the selected page's data dependencies."""
    selected = _render_sidebar(route, execution_factory, portfolio_summary_loader, hydrate_portfolio)
    if route.get("page") == "stock":
        decisions = decisions_loader()
        candidate = _candidate_for_symbol(decisions, route.get("symbol"))
        if not candidate:
            candidate = _generic_candidate(_load_stock_research(route.get("symbol")))
        with timed("page.stock_detail", tab=route.get("tab")):
            _render_stock_detail(candidate, route, execution_factory, hydrate_portfolio)
        return
    page = PAGE_KEYS[selected]
    if page == "today":
        _render_dashboard(decisions_loader(), portfolio_summary_loader(), execution_factory, hydrate_portfolio)
    elif page == "signals":
        decisions = decisions_loader()
        with timed("page.opportunities", rows=len(decisions)): _render_opportunities(decisions)
    elif page == "portfolio":
        with timed("page.portfolio"): _render_portfolio(execution_factory, portfolio_summary_loader, positions_loader, hydrate_portfolio)
    elif page == "research":
        _render_stock_research(decisions_loader(), execution_factory, hydrate_portfolio)
    elif page == "learning":
        with timed("page.learning"): _render_learning()
    else:
        render_page_header("Settings", "Portfolio controls and deployment status")
        health = execution_factory().database_diagnostics()
        render_context_card("Database", f"{health.get('database_backend')} · {health.get('database_status')}", "Persistent paper ledger")
