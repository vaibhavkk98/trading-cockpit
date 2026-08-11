"""Focused V1.1 interaction/state checks; no universe, price, or DB writes."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from interaction_architecture import (  # noqa: E402
    candidate_identity,
    compact_allocation,
    current_scan_identity,
    default_signal_rows,
    initial_workspace_state,
    ordered_decisions,
    reconcile_selection,
    short_strategy_name,
)
import trade_economics_context as economics  # noqa: E402


def volume(value): return f"{value:.2f}×" if isinstance(value, (int, float)) else "Not available"
def price(value): return f"₹{value:.2f}" if isinstance(value, (int, float)) else "Not available"
def percent(value): return f"{value:.2f}%" if isinstance(value, (int, float)) else "Not available"


def candidate(priority, symbol, strategy, allocation="ALLOCATED", reason=None):
    return {
        "opportunity_priority_rank": priority, "symbol": symbol, "strategy": strategy,
        "allocation_status": allocation, "allocation_reason_code": reason,
        "volume_ratio_20": 2.4, "entry_price": 100.0, "executable_stop_enabled": False,
        "reference_risk_pct_equity": "NOT_AVAILABLE", "candidate_reference_heat_add_pct": "NOT_AVAILABLE",
    }


def run() -> None:
    state = initial_workspace_state()
    assert state["live_decisions"] == [] and state["portfolio_loaded"] is False
    data = [candidate(3, "CCC", "True NR7 Volatility Expansion Breakout", "QUALIFIED", "CAPITAL_CAP"),
            candidate(1, "AAA", "Donchian Channel Breakout"),
            candidate(2, "BBB", "EMA Pullback / Bounce", "QUALIFIED", "INSUFFICIENT_CASH")]
    ordered = ordered_decisions(data)
    assert [row["opportunity_priority_rank"] for row in ordered] == [1, 2, 3]
    assert compact_allocation(ordered[1]) == "CASH" and compact_allocation(ordered[2]) == "CAPACITY"
    assert short_strategy_name("Donchian Channel Breakout") == "Donchian"
    assert short_strategy_name("True NR7 Volatility Expansion Breakout") == "NR7"
    rows = default_signal_rows(data, {"volume": volume, "price": price, "percent": percent})
    assert [row["Priority"] for row in rows] == [1, 2, 3]
    assert "Reference Risk" not in rows[0] and "Heat Added" not in rows[0]
    assert rows[0]["Strategy"] == "Donchian" and rows[2]["Allocation"] == "CAPACITY"

    scan = {"analysis_date": "2026-08-11", "scan_completed_at": "2026-08-11T10:00:00+00:00"}
    state = {"selected_opportunity_id": candidate_identity(ordered[0], scan)}
    assert reconcile_selection(state, ordered, scan) == state["selected_opportunity_id"]
    next_scan = {"analysis_date": "2026-08-12", "scan_completed_at": "2026-08-12T10:00:00+00:00"}
    assert reconcile_selection(state, ordered, next_scan) is None
    assert current_scan_identity(scan) != current_scan_identity(next_scan)

    economics._frozen_display_payload.cache_clear()
    economics.get_trade_economics_context("Donchian Channel Breakout")
    economics.get_trade_economics_context("Donchian Channel Breakout")
    assert economics._frozen_display_payload.cache_info().hits >= 1

    app = (ROOT / "app.py").read_text()
    database = (ROOT / "database.py").read_text()
    market = (ROOT / "market_risk_live.py").read_text()
    assert "if run_analysis_btn:" in app
    assert "@st.fragment\ndef render_signals_workbench" in app
    assert "refresh_portfolio_positions" in app and "Refresh prices" in app
    assert "st.form(\"paper_trade_record_form\"" in app and "st.form(\"manual_paper_close_form\"" in app
    assert "get_open_trades_persisted" in database and "PRICE_NOT_REFRESHED" in database
    assert "@lru_cache(maxsize=8)" in market and "never fetches" in market
    for forbidden in ("target_price", "risk_reward_ratio", "composite_score"):
        assert forbidden not in app
    print("V1.1 interaction architecture tests: PASS")


if __name__ == "__main__":
    run()
