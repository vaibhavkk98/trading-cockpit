"""Pure V1.1 interaction helpers; no provider, database, or financial logic."""
from copy import deepcopy
from typing import Any, Dict, Iterable, List, Optional


NAVIGATION_PAGES = {"today", "signals", "portfolio", "journal", "research", "stock", "learning", "performance", "settings"}
STOCK_TABS = {"overview", "rally", "historical_analogs", "role_evidence", "events", "trade"}


def canonical_route_symbol(symbol: Any) -> Optional[str]:
    value = str(symbol or "").strip().upper()
    if value.startswith("NSE:"):
        value = value[4:]
    if value.endswith(".NS"):
        value = value[:-3]
    return value if value and value.replace("-", "").replace("&", "").replace("_", "").isalnum() else None


def navigation_state(query_params: Any, current: Optional[Dict[str, Any]] = None) -> Dict[str, Optional[str]]:
    """Resolve a durable, read-only deep link without executing application work."""
    current = current or {}

    def scalar(name: str):
        value = query_params.get(name) if hasattr(query_params, "get") else None
        return value[-1] if isinstance(value, (list, tuple)) and value else value

    page = str(scalar("page") or current.get("navigation_page") or "today").lower()
    page = page if page in NAVIGATION_PAGES else "today"
    symbol = canonical_route_symbol(scalar("symbol") or current.get("navigation_symbol"))
    tab = str(scalar("tab") or current.get("navigation_tab") or "overview").lower()
    tab = tab if tab in STOCK_TABS else "overview"
    if page != "stock":
        symbol, tab = None, "overview"
    elif symbol is None:
        page, tab = "today", "overview"
    return {"page": page, "symbol": symbol, "tab": tab}


def hydrate_navigation_state(session_state: Dict[str, Any], query_params: Any) -> Dict[str, Optional[str]]:
    route = navigation_state(query_params, session_state)
    session_state["navigation_page"] = route["page"]
    session_state["navigation_symbol"] = route["symbol"]
    session_state["navigation_tab"] = route["tab"]
    return route


def navigation_query(page: str, symbol: Any = None, tab: str = "overview") -> Dict[str, str]:
    """Build canonical URL parameters; contains no trade or confirmation state."""
    route = navigation_state({"page": page, "symbol": symbol, "tab": tab})
    result = {"page": str(route["page"])}
    if route["page"] == "stock":
        result.update({"symbol": str(route["symbol"]), "tab": str(route["tab"])})
    return result


STRATEGY_LABELS = {
    "Donchian Channel Breakout": "Donchian",
    "EMA Pullback / Bounce": "EMA Pullback",
    "RS Momentum Breakout": "RS Momentum",
    "VCP Volatility Contraction Breakout": "VCP",
    "True Connors RSI Mean Reversion": "Connors RSI",
    "Connors RSI Mean Reversion": "Connors RSI",
    "True NR7 Volatility Expansion Breakout": "NR7",
}

ALLOCATION_LABELS = {
    "ALLOCATED": "ALLOCATED",
    "DUPLICATE_POSITION": "DUPLICATE",
    "INSUFFICIENT_CASH": "CASH",
    "CAPITAL_CAP": "CAPACITY",
}


def short_strategy_name(strategy: Any) -> str:
    return STRATEGY_LABELS.get(strategy, str(strategy or "Not available"))


def compact_allocation(candidate: Dict[str, Any]) -> str:
    if candidate.get("allocation_status") == "ALLOCATED":
        return ALLOCATION_LABELS["ALLOCATED"]
    return ALLOCATION_LABELS.get(candidate.get("allocation_reason_code"), "NOT ALLOCATED")


def priority_value(candidate: Dict[str, Any]) -> int:
    value = candidate.get("opportunity_priority_rank")
    return value if isinstance(value, int) else 10**9


def ordered_decisions(candidates: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Strict priority order; allocation is never a sort key."""
    return sorted(candidates, key=lambda candidate: (priority_value(candidate), str(candidate.get("symbol", ""))))


def current_scan_identity(scan_state: Dict[str, Any]) -> str:
    return f"{scan_state.get('analysis_date') or ''}:{scan_state.get('scan_completed_at') or ''}"


def candidate_identity(candidate: Dict[str, Any], scan_state: Dict[str, Any]) -> str:
    return str(candidate.get("opportunity_id") or f"{current_scan_identity(scan_state)}:{candidate.get('symbol') or ''}")


def reconcile_selection(session_state: Dict[str, Any], candidates: Iterable[Dict[str, Any]], scan_state: Dict[str, Any]) -> Optional[str]:
    valid = {candidate_identity(candidate, scan_state) for candidate in candidates}
    selected = session_state.get("selected_opportunity_id")
    if selected not in valid:
        session_state["selected_opportunity_id"] = None
    return session_state.get("selected_opportunity_id")


def select_candidate(candidates: Iterable[Dict[str, Any]], selected_id: Optional[str], scan_state: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    for candidate in candidates:
        if candidate_identity(candidate, scan_state) == selected_id:
            return candidate
    return None


def has_meaningful_comparative_coverage(candidates: Iterable[Dict[str, Any]], field: str) -> bool:
    items = list(candidates)
    return bool(items) and sum(isinstance(candidate.get(field), (int, float)) for candidate in items) * 2 >= len(items)


def default_signal_rows(candidates: Iterable[Dict[str, Any]], formatters: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Build the compact comparison surface without mutating decision payloads."""
    ordered = ordered_decisions(candidates)
    include_reference = has_meaningful_comparative_coverage(ordered, "reference_risk_pct_equity")
    include_heat = has_meaningful_comparative_coverage(ordered, "candidate_reference_heat_add_pct")
    rows = []
    for candidate in ordered:
        row = {
            "Priority": candidate.get("opportunity_priority_rank", "Not available"),
            "Symbol": candidate.get("symbol", "Not available"),
            "Strategy": short_strategy_name(candidate.get("strategy")),
            "Allocation": compact_allocation(candidate),
            "Volume Ratio": formatters["volume"](candidate.get("volume_ratio_20")),
            "Entry": formatters["price"](candidate.get("entry_price")),
            "Stop": formatters["price"](candidate.get("initial_executable_stop")) if candidate.get("executable_stop_enabled") else "—",
        }
        if include_reference:
            row["Reference Risk"] = formatters["percent"](candidate.get("reference_risk_pct_equity"))
        if include_heat:
            row["Heat Added"] = formatters["percent"](candidate.get("candidate_reference_heat_add_pct"))
        rows.append(row)
    return rows


def portfolio_position_rows(positions: Iterable[Dict[str, Any]], formatters: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Build the persisted Portfolio comparison surface without provider work."""
    rows = []
    for position in positions:
        pnl = position.get("unrealized_pnl_inr")
        pnl_display = (
            f"{formatters['currency'](pnl, compact=False)} · {formatters['percent'](position.get('unrealized_pnl_pct'))}"
            if isinstance(pnl, (int, float)) else "Not available"
        )
        rows.append({
            "Symbol": position.get("symbol", ""),
            "Strategy": short_strategy_name(position.get("strategy_used")),
            "Entry price": formatters["price"](position.get("entry_price")),
            "Entry value": formatters["currency"](position.get("position_value")),
            "Current price": formatters["price"](position.get("current_price")) if position.get("price_status") == "AVAILABLE" else "Price not available",
            "P&L": pnl_display,
            "Price as of": formatters["display"](position.get("marked_at") or position.get("mark_date")),
            "Executable stop": formatters["price"](position.get("initial_executable_stop")) if position.get("executable_stop_enabled") else "—",
        })
    return rows


def initial_workspace_state() -> Dict[str, Any]:
    return {
        "scan_summary": {}, "qualified_candidates": [], "live_decisions": [],
        "selected_opportunity_id": None, "portfolio_loaded": False,
        "portfolio_positions": [], "portfolio_summary": {}, "portfolio_snapshots": [], "last_price_refresh_at": None,
        "navigation_page": "today", "navigation_symbol": None, "navigation_tab": "overview",
    }
