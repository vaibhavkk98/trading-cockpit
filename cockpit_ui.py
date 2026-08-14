"""Focused HA-P2 product UI. Rendering is read-only unless a labeled action is submitted."""
from __future__ import annotations

import datetime as dt
from typing import Any, Callable
from urllib.parse import quote

import pandas as pd
import streamlit as st

import database
from historical_analogs_service import METHODOLOGY_HASH
from interaction_architecture import canonical_route_symbol, compact_allocation, navigation_query, ordered_decisions, short_strategy_name
from live_decision_adapter import summarize_live_portfolio_risk
from portfolio_analytics import get_portfolio_pnl
from ui_components import (
    display_value, format_currency, format_percent, format_price, format_signed_currency,
    render_context_card, render_empty_state, render_metric_card, render_page_header,
    render_section_header, status_badge,
)


PAGES = ["Dashboard", "Opportunities", "Portfolio", "Journal", "Settings"]
PAGE_KEYS = {"Dashboard": "today", "Opportunities": "signals", "Portfolio": "portfolio", "Journal": "journal", "Settings": "settings"}
STOCK_TABS = ["overview", "rally", "historical_analogs", "events", "trade"]


def stock_url(symbol: Any, tab: str = "overview") -> str:
    route = navigation_query("stock", symbol, tab)
    return f"?page=stock&symbol={quote(route['symbol'])}&tab={quote(route['tab'])}"


def _candidate_for_symbol(decisions, symbol):
    canonical = canonical_route_symbol(symbol)
    return next((row for row in decisions if canonical_route_symbol(row.get("symbol")) == canonical), None)


def _load_ha(candidate):
    if not candidate:
        return None
    opportunity_id = candidate.get("opportunity_id")
    signal_date = candidate.get("signal_date") or candidate.get("analysis_date")
    if not opportunity_id or not signal_date:
        return None
    return database.load_historical_analog_snapshot(opportunity_id, str(signal_date)[:10], METHODOLOGY_HASH)


def _ha_label(candidate):
    snapshot = _load_ha(candidate)
    if not snapshot:
        return "INSUFFICIENT"
    quality = snapshot.get("evidence_quality", "INSUFFICIENT")
    count = snapshot.get("analog_count", 0)
    return f"{count} analogs · {quality}" if count else "INSUFFICIENT"


def _link_table(frame: pd.DataFrame, *, link_column="Stock"):
    config = {link_column: st.column_config.LinkColumn(link_column, display_text=r".*symbol=([^&]+).*")}
    st.dataframe(frame, width="stretch", hide_index=True, column_config=config)


def _render_ticket(candidate, execution, hydrate_portfolio: Callable, *, key_prefix: str):
    if not candidate:
        render_empty_state("No qualified opportunity", "Run analysis before opening a paper-trade ticket.")
        return
    preview_cash = execution.get_portfolio_summary().get("current_cash_inr", 0.0)
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
                st.rerun()
            else:
                st.error(result.get("message", "Paper-trade write failed."))


def _render_sidebar(portfolio_summary, execution, hydrate_portfolio):
    route_page = st.session_state.get("navigation_page", "today")
    label = "Opportunities" if route_page == "stock" else next((name for name, key in PAGE_KEYS.items() if key == route_page), "Dashboard")
    if st.session_state.get("sidebar_workspace") != label:
        st.session_state["sidebar_workspace"] = label

    def change_page():
        target = PAGE_KEYS[st.session_state["sidebar_workspace"]]
        st.query_params.from_dict({"page": target})

    st.sidebar.markdown("### Trading Cockpit")
    selected = st.sidebar.radio("Workspace", PAGES, key="sidebar_workspace", on_change=change_page, label_visibility="collapsed")
    st.session_state["navigation_page"] = PAGE_KEYS[selected]
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
    st.sidebar.caption("Paper trading only · Explicit execution")
    return selected


def _render_dashboard(decisions, summary, execution, hydrate_portfolio):
    render_page_header("Dashboard", "Qualified opportunities and paper-portfolio state at a glance")
    c1, c2, c3, c4 = st.columns(4)
    with c1: render_metric_card("Portfolio equity", format_currency(summary.get("total_portfolio_value_inr")), "Paper NAV")
    with c2: render_metric_card("Cash", format_currency(summary.get("current_cash_inr")), "Available")
    with c3: render_metric_card("Open positions", summary.get("open_positions_count", 0), "Paper positions")
    with c4: render_metric_card("Qualified", len(decisions), "Allocator remains advisory")
    render_section_header("Manual paper trade", "Any qualified opportunity; no automatic execution")
    if decisions:
        labels = {f"{row.get('symbol')} · {short_strategy_name(row.get('strategy'))}": row for row in ordered_decisions(decisions)}
        chosen = st.selectbox("Qualified opportunity", list(labels), key="paper_trade_symbol")
        _render_ticket(labels[chosen], execution, hydrate_portfolio, key_prefix="dashboard")
    else:
        render_empty_state("No qualified opportunities", "Run today's analysis to populate the trading workspace.")


def _render_opportunities(decisions):
    render_page_header("Opportunities", "All qualified names; allocator selection is advisory")
    if not decisions:
        render_empty_state("No qualified opportunities", "Run today's analysis to populate this page.")
        return
    rows = []
    for row in ordered_decisions(decisions):
        symbol = canonical_route_symbol(row.get("symbol"))
        rows.append({
            "Stock": stock_url(symbol), "Symbol": symbol, "Strategy": short_strategy_name(row.get("strategy")),
            "Allocation": compact_allocation(row), "Entry": row.get("entry_price"),
            "Volume ratio": row.get("volume_ratio_20"), "Historical Analogs": stock_url(symbol, "historical_analogs"),
            "HA evidence": _ha_label(row),
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
    result = get_portfolio_pnl(period, custom_start=custom_start, custom_end=custom_end)
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
    snapshots = database.load_portfolio_snapshots(limit=365)
    period_start = dt.date.fromisoformat(result["period_start"]) if result.get("period_start") else None
    period_end = dt.date.fromisoformat(result["period_end"])
    usable = [row for row in reversed(snapshots) if row.portfolio_equity is not None
              and (period_start is None or row.snapshot_date >= period_start) and row.snapshot_date <= period_end]
    if len(usable) >= 2:
        chart = pd.DataFrame({"date": [row.snapshot_date for row in usable], "Portfolio equity": [row.portfolio_equity for row in usable]}).drop_duplicates("date", keep="last")
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
        st.success(f"Prices refreshed: {result.get('successful_marks', 0)} / {result.get('open_positions', 0)}")
        st.rerun()
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


def _portfolio_closed():
    trades = database.get_closed_trades()
    if not trades:
        render_empty_state("No closed trades", "Closed paper trades will appear here.")
        return
    rows = [{"Stock": stock_url(t.symbol), "Symbol": t.symbol, "Strategy": short_strategy_name(t.strategy_used),
             "Entry": t.entry_price, "Exit": t.exit_price, "Quantity": t.quantity, "Realized P&L": t.realized_pnl,
             "Closed": t.exit_date.date().isoformat() if t.exit_date else None} for t in trades]
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True, column_config={"Stock": st.column_config.LinkColumn("Stock", display_text="Open")})


def _portfolio_risk(positions):
    risk = summarize_live_portfolio_risk(positions)
    c1, c2 = st.columns(2)
    with c1: render_metric_card("Reference Heat", risk["reference_heat_pct"], f"Coverage {risk['positions_with_reference']} / {len(positions)}")
    with c2: render_metric_card("Executable Stop Heat", risk["executable_stop_heat_pct"], f"Coverage {risk['positions_with_executable_stop']} / {len(positions)}")
    st.caption("Reference Heat is informational. Executable Stop Heat is supplementary. Neither is a maximum-loss estimate.")


def _render_portfolio(summary, positions, execution, hydrate_portfolio):
    render_page_header("Portfolio", "Paper-ledger accounting, contribution and risk")
    tabs = ["Overview", "P&L", "Open Positions", "Closed Trades", "Risk"]
    selected = st.segmented_control("Portfolio view", tabs, default="Overview", key="portfolio_view")
    if selected == "Overview": _portfolio_overview(summary)
    elif selected == "P&L": _portfolio_pnl()
    elif selected == "Open Positions": _portfolio_positions(positions, execution, hydrate_portfolio)
    elif selected == "Closed Trades": _portfolio_closed()
    else: _portfolio_risk(positions)


def _render_ha(snapshot):
    if not snapshot:
        render_empty_state("Historical Analog evidence unavailable", "No immutable production snapshot exists for this opportunity yet.")
        return
    quality = snapshot.get("evidence_quality", "INSUFFICIENT")
    st.markdown(f"{status_badge(quality, 'good' if quality == 'HIGH' else 'warn' if quality in {'MEDIUM','LOW'} else 'unavailable')}", unsafe_allow_html=True)
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
    render_section_header("Analog cases", "Inspect the frozen K40 mapping")
    cases = []
    for row in snapshot.get("analogs", []):
        cases.append({"Rank": row["rank"], "Historical date": row["signal_date"], "Historical symbol": row["symbol"],
                      "Distance": row["distance"], "Prior 10D move": row.get("ret_10d"),
                      "Nifty 500 10D": row.get("nifty500_ret_10d"), "MFE 10D": row.get("mfe_10d"),
                      "MAE 10D": row.get("mae_10d"), "+5/-3 result": row.get("target_5_before_stop_3_20d")})
    st.dataframe(pd.DataFrame(cases), width="stretch", hide_index=True)


def _render_stock_detail(candidate, route, execution, hydrate_portfolio):
    symbol = route.get("symbol")
    render_page_header(symbol or "Stock Detail", "Qualified-opportunity intelligence and explicit paper execution")
    if not candidate:
        render_empty_state("Stock detail unavailable", "This symbol is not in the current qualified opportunity set.")
        return
    st.markdown(f"{status_badge(candidate.get('qualification_status', 'QUALIFIED'), 'good')} &nbsp; {status_badge(short_strategy_name(candidate.get('strategy')), 'neutral')} &nbsp; {status_badge(compact_allocation(candidate), 'neutral')}", unsafe_allow_html=True)
    labels = {"overview": "Overview", "rally": "Rally", "historical_analogs": "Historical Analogs", "events": "Events", "trade": "Trade"}
    current = route.get("tab") if route.get("tab") in STOCK_TABS else "overview"
    selected_label = st.segmented_control("Stock detail view", list(labels.values()), default=labels[current], key=f"stock_tab_{symbol}")
    selected = next(key for key, value in labels.items() if value == selected_label)
    if selected != current:
        st.query_params.from_dict(navigation_query("stock", symbol, selected))
        st.rerun()
    if selected == "overview":
        c1, c2, c3 = st.columns(3)
        with c1: render_context_card("Reference price", format_price(candidate.get("entry_price")), f"Signal date {display_value(candidate.get('signal_date'))}")
        with c2: render_context_card("Volume ratio", display_value(candidate.get("volume_ratio_20")), "Qualification context")
        with c3: render_context_card("Allocator", "Selected" if candidate.get("allocation_status") == "ALLOCATED" else "Not selected", "Advisory only")
        st.caption(display_value(candidate.get("allocation_reason_text"), "Qualified under the frozen technical contract."))
    elif selected == "rally":
        fields = [("5D return", "ret_5d"), ("10D return", "ret_10d"), ("20D return", "ret_20d"),
                  ("EMA20 extension", "distance_from_ema20_pct"), ("ATR extension", "distance_from_ema20_atr"),
                  ("Largest positive session", "largest_positive_daily_return_10d"), ("Positive sessions", "positive_sessions_10")]
        columns = st.columns(3)
        for index, (label, key) in enumerate(fields):
            value = candidate.get(key) or (candidate.get("ha_features") or {}).get(key)
            with columns[index % 3]: render_context_card(label, format_percent(value) if key != "positive_sessions_10" else display_value(value), "Descriptive only")
    elif selected == "historical_analogs": _render_ha(_load_ha(candidate))
    elif selected == "events": render_empty_state("Events", "Event Intelligence is not yet available.")
    else:
        snapshot = _load_ha(candidate)
        st.caption(f"Qualified · {'allocator selected' if candidate.get('allocation_status') == 'ALLOCATED' else 'allocator not selected'} · Prior 10D rally {format_percent((candidate.get('ha_features') or {}).get('ret_10d'))} · HA evidence {snapshot.get('evidence_quality') if snapshot else 'INSUFFICIENT'}")
        _render_ticket(candidate, execution, hydrate_portfolio, key_prefix=f"stock_{symbol}")


def render_cockpit(*, decisions, positions, portfolio_summary, adapters, hydrate_portfolio, route):
    """Render the focused product shell; no provider call occurs during routing."""
    selected = _render_sidebar(portfolio_summary, adapters["execution"], hydrate_portfolio)
    if route.get("page") == "stock":
        _render_stock_detail(_candidate_for_symbol(decisions, route.get("symbol")), route, adapters["execution"], hydrate_portfolio)
        return
    page = PAGE_KEYS[selected]
    if page == "today": _render_dashboard(decisions, portfolio_summary, adapters["execution"], hydrate_portfolio)
    elif page == "signals": _render_opportunities(decisions)
    elif page == "portfolio": _render_portfolio(portfolio_summary, positions, adapters["execution"], hydrate_portfolio)
    elif page == "journal":
        render_page_header("Journal", "Paper-trade review workspace")
        render_empty_state("Journal coming next", "Existing paper trades remain available under Portfolio.")
    else:
        render_page_header("Settings", "Portfolio controls and deployment status")
        health = adapters["execution"].database_diagnostics()
        render_context_card("Database", f"{health.get('database_backend')} · {health.get('database_status')}", "Persistent paper ledger")
