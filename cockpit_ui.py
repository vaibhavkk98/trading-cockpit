"""Focused HA-P2 product UI. Rendering is read-only unless a labeled action is submitted."""
from __future__ import annotations

import datetime as dt
from typing import Any, Callable
from urllib.parse import quote

import pandas as pd
import streamlit as st

import database
from cockpit_cache import (
    load_closed_trade_rows, load_ha_snapshot, load_ha_summaries,
    load_open_positions, load_portfolio_pnl, load_portfolio_snapshots,
    load_portfolio_summary,
)
from historical_analogs_service import HistoricalAnalogService, METHODOLOGY_HASH
from interaction_architecture import canonical_route_symbol, compact_allocation, navigation_query, ordered_decisions, short_strategy_name
from live_decision_adapter import summarize_live_portfolio_risk
from performance_timing import timed
from ui_components import (
    display_value, format_currency, format_percent, format_price, format_signed_currency,
    render_context_card, render_empty_state, render_metric_card, render_page_header,
    render_section_header, status_badge,
)


PAGES = ["Dashboard", "Opportunities", "Portfolio", "Stock Research", "Settings"]
PAGE_KEYS = {"Dashboard": "today", "Opportunities": "signals", "Portfolio": "portfolio", "Stock Research": "research", "Settings": "settings"}
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
                  "Allocation": compact_allocation(row), "Volume": row.get("volume_ratio_20")}
                 for row in ordered_decisions(decisions)[:6]]
        if pulse:
            st.dataframe(pd.DataFrame(pulse), width="stretch", hide_index=True,
                         column_config={"Volume": st.column_config.NumberColumn(format="%.2fx")})
        else:
            render_empty_state("No qualified opportunities", "Run analysis to populate the decision workspace.")
    with right:
        render_context_card("Execution", "Manual only", "Scanning and navigation never create trades", "neutral", "Controlled")
        render_context_card("Allocator", "Advisory", "All qualified names remain tradeable within portfolio constraints")
    with st.expander("Market risk · Experimental", expanded=False):
        render_empty_state(
            "India-impact source policy pending",
            "The broad global feed is intentionally withheld. An India-source-only, impact-materiality methodology requires separate research before production use.",
        )
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
            "Volume ratio": row.get("volume_ratio_20"), "Historical Analogs": stock_url(symbol, "historical_analogs"),
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
def _render_ha_cases(candidate):
    render_section_header("Analog cases", "Frozen K40 mapping loads only when requested")
    show_cases = st.toggle("Inspect the 40 closest analog cases", value=False, key=f"ha_cases_{candidate.get('opportunity_id')}")
    if not show_cases:
        st.caption("Case rows are deferred to keep the summary interaction fast.")
        return
    full_snapshot = _load_ha(candidate)
    cases = []
    for row in (full_snapshot or {}).get("analogs", []):
        cases.append({"Rank": row["rank"], "Historical date": row["signal_date"], "Historical symbol": row["symbol"],
                      "Distance": row["distance"], "Prior 10D move": row.get("ret_10d"),
                      "Nifty 500 10D": row.get("nifty500_ret_10d"), "MFE 10D": row.get("mfe_10d"),
                      "MAE 10D": row.get("mae_10d"), "+5/-3 result": row.get("target_5_before_stop_3_20d")})
    st.dataframe(pd.DataFrame(cases), width="stretch", hide_index=True)


def _render_ha(candidate, snapshot):
    if not snapshot:
        render_empty_state("Historical Analog snapshot pending", "This persisted opportunity predates live HA enrichment. Run analysis once to create its immutable snapshot.")
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
    _render_ha_cases(candidate)


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


@st.cache_data(ttl=900, show_spinner=False)
def _load_stock_research(symbol: str):
    """Explicit, cached lookup for a non-opportunity stock; no portfolio writes."""
    from screener import fetch_ha_market_histories, fetch_stock_data
    stock = fetch_stock_data(symbol, period="2y")
    if stock is None or stock.empty:
        return None
    signal_date = stock.index[-1].strftime("%Y-%m-%d")
    nifty500, vix = fetch_ha_market_histories(period="2y", as_of_date=signal_date)
    features = {}
    if nifty500 is not None and vix is not None:
        try:
            features = HistoricalAnalogService.build_causal_query_state(stock, nifty500, vix, signal_date)["ha_features"]
        except Exception:
            features = {}
    close = pd.to_numeric(stock["Close"], errors="coerce")
    chart = pd.DataFrame({"Close": close, "EMA 20": close.ewm(span=20, adjust=False).mean(), "EMA 50": close.ewm(span=50, adjust=False).mean()}).tail(120)
    return {"symbol": canonical_route_symbol(symbol), "signal_date": signal_date, "entry_price": float(close.iloc[-1]), "ha_features": features,
            "chart": {name: {str(index): float(value) for index, value in series.dropna().items()} for name, series in chart.items()}}


def _render_stock_research(decisions):
    render_page_header("Stock Research", "Search any NSE stock; qualified opportunities retain the full decision workflow")
    active = sorted({canonical_route_symbol(row.get("symbol")) for row in decisions if canonical_route_symbol(row.get("symbol"))})
    with st.form("stock_research_form"):
        symbol = st.text_input("NSE symbol", value=st.session_state.get("research_symbol", ""), placeholder="e.g. RELIANCE")
        submitted = st.form_submit_button("Search stock", type="primary")
    if submitted:
        canonical = canonical_route_symbol(symbol)
        if canonical:
            st.session_state["research_symbol"] = canonical
        else:
            st.error("Enter a valid NSE symbol.")
    chosen = st.session_state.get("research_symbol")
    if not chosen:
        st.caption("Current qualified symbols: " + (", ".join(active) if active else "none"))
        return
    qualified = _candidate_for_symbol(decisions, chosen)
    if qualified:
        st.success("This stock is a current qualified opportunity.")
        st.link_button("Open full opportunity intelligence", stock_url(chosen))
        research = {"symbol": chosen, "signal_date": qualified.get("signal_date"), "entry_price": qualified.get("entry_price"), "ha_features": qualified.get("ha_features") or {}, "chart": {}}
    else:
        with st.spinner(f"Loading completed-session data for {chosen}…"):
            research = _load_stock_research(chosen)
    if not research:
        render_empty_state("Stock data unavailable", "No adequate completed-session NSE history was returned for this symbol.")
        return
    c1, c2 = st.columns(2)
    with c1: render_context_card("Latest close", format_price(research.get("entry_price")), f"As of {research.get('signal_date')}")
    with c2: render_context_card("Decision eligibility", "Qualified" if qualified else "Research only", "Paper execution remains restricted to qualified opportunities")
    features = research.get("ha_features") or {}
    fields = [("5D return", "ret_5d"), ("10D return", "ret_10d"), ("20D return", "ret_20d"), ("EMA20 extension", "distance_from_ema20_pct"), ("ATR extension", "distance_from_ema20_atr")]
    columns = st.columns(3)
    for index, (label, key) in enumerate(fields):
        with columns[index % 3]: render_context_card(label, format_percent(features.get(key)), "Completed-session OHLCV")
    chart_payload = research.get("chart") or {}
    if chart_payload:
        chart = pd.DataFrame({name: pd.Series(values, dtype=float) for name, values in chart_payload.items()})
        st.line_chart(chart, width="stretch")


def _render_stock_detail(candidate, route, execution_factory, hydrate_portfolio):
    symbol = route.get("symbol")
    render_page_header(symbol or "Stock Detail", "Qualified-opportunity intelligence and explicit paper execution")
    if not candidate:
        render_empty_state("Stock detail unavailable", "This symbol is not in the current qualified opportunity set.")
        return
    st.markdown(f"{status_badge(candidate.get('qualification_status', 'QUALIFIED'), 'good')} &nbsp; {status_badge(short_strategy_name(candidate.get('strategy')), 'neutral')} &nbsp; {status_badge(compact_allocation(candidate), 'neutral')}", unsafe_allow_html=True)
    labels = {"overview": "Overview", "rally": "Rally", "historical_analogs": "Historical Analogs", "events": "Events", "trade": "Trade"}
    query_tab = st.query_params.get("tab")
    current = query_tab if query_tab in STOCK_TABS else route.get("tab") if route.get("tab") in STOCK_TABS else "overview"
    tab_key = f"stock_tab_{symbol}"
    route_key = f"{tab_key}_route"
    if st.session_state.get(route_key) != current:
        st.session_state[tab_key] = labels[current]
        st.session_state[route_key] = current

    def change_stock_tab():
        selected_key = next(key for key, value in labels.items() if value == st.session_state[tab_key])
        st.session_state[route_key] = selected_key
        st.query_params.from_dict(navigation_query("stock", symbol, selected_key))

    selected_label = st.segmented_control(
        "Stock detail view", list(labels.values()), default=None, key=tab_key,
        on_change=change_stock_tab,
    )
    selected = next(key for key, value in labels.items() if value == selected_label)
    if selected == "overview":
        c1, c2, c3 = st.columns(3)
        with c1: render_context_card("Reference price", format_price(candidate.get("entry_price")), f"Signal date {display_value(candidate.get('signal_date'))}")
        with c2: render_context_card("Volume ratio", display_value(candidate.get("volume_ratio_20")), "Qualification context")
        with c3: render_context_card("Allocator", "Selected" if candidate.get("allocation_status") == "ALLOCATED" else "Not selected", "Advisory only")
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
    elif selected == "historical_analogs": _render_ha(candidate, _load_ha_summary(candidate))
    elif selected == "events": _render_events(candidate)
    else:
        snapshot = _load_ha_summary(candidate)
        st.caption(f"Qualified · {'allocator selected' if candidate.get('allocation_status') == 'ALLOCATED' else 'allocator not selected'} · Prior 10D rally {format_percent((candidate.get('ha_features') or {}).get('ret_10d'))} · HA evidence {snapshot.get('evidence_quality') if snapshot else 'INSUFFICIENT'}")
        _render_stock_ticket(candidate, execution_factory, hydrate_portfolio, symbol)


def render_cockpit(*, route, decisions_loader, execution_factory,
                   portfolio_summary_loader, positions_loader, hydrate_portfolio):
    """Route first, then invoke only the selected page's data dependencies."""
    selected = _render_sidebar(route, execution_factory, portfolio_summary_loader, hydrate_portfolio)
    if route.get("page") == "stock":
        decisions = decisions_loader()
        with timed("page.stock_detail", tab=route.get("tab")):
            _render_stock_detail(_candidate_for_symbol(decisions, route.get("symbol")), route, execution_factory, hydrate_portfolio)
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
        _render_stock_research(decisions_loader())
    else:
        render_page_header("Settings", "Portfolio controls and deployment status")
        health = execution_factory().database_diagnostics()
        render_context_card("Database", f"{health.get('database_backend')} · {health.get('database_status')}", "Persistent paper ledger")
