"""Focused Phase F1 checks for the live consumer adapter and cockpit guardrails."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from live_decision_adapter import (  # noqa: E402
    EXECUTABLE_STOP_STRATEGIES,
    assemble_live_decisions,
    summarize_live_portfolio_risk,
)


def candidate(symbol, strategy, volume_ratio, status, atr_available=True):
    return {
        "symbol": symbol,
        "strategy": strategy,
        "strategy_category": "Trend",
        "signal_date": "2026-08-10",
        "entry_price": 100.0,
        "atr_20": 2.0,
        "atr_20_available": atr_available,
        "quantity": 1_000,
        "volume_ratio_20": volume_ratio,
        "is_qualified": True,
        "regime": "BULLISH",
        "status": status,
        # Deliberately present legacy fields: the live adapter must suppress them.
        "target_price": 140.0,
        "risk_reward_ratio": 2.0,
        "composite_score": 0.9,
    }


def run():
    decisions = assemble_live_decisions([
        candidate("DONCH", "Donchian Channel Breakout", 2.1, "ALLOCATED"),
        candidate("EMA", "EMA Pullback / Bounce", 3.0, "QUALIFIED — CAPITAL CAP"),
        candidate("VCP", "VCP Volatility Contraction Breakout", 2.5, "ALLOCATED", atr_available=False),
    ])
    by_symbol = {row["symbol"]: row for row in decisions}

    assert [row["symbol"] for row in decisions] == ["EMA", "VCP", "DONCH"]
    assert [row["opportunity_priority_rank"] for row in decisions] == [1, 2, 3]
    assert by_symbol["DONCH"]["initial_executable_stop"] == 96.0
    assert by_symbol["DONCH"]["executable_stop_risk_inr"] == 4_000.0
    assert by_symbol["EMA"]["executable_stop_enabled"] is False
    assert by_symbol["VCP"]["executable_stop_enabled"] is False
    assert by_symbol["DONCH"]["risk_reference_available"] is False
    assert by_symbol["DONCH"]["risk_reference_value"] == "NOT_AVAILABLE"
    assert by_symbol["EMA"]["allocation_reason_code"] == "CAPITAL_CAP"
    assert by_symbol["DONCH"]["target_status"] == "NOT_AVAILABLE"
    for row in decisions:
        assert "target_price" not in row
        assert "risk_reward_ratio" not in row
        assert "composite_score" not in row
        assert row["reference_heat_before_pct"] == "NOT_AVAILABLE"
        assert row["correlation_availability"] == "NOT_AVAILABLE"

    assert EXECUTABLE_STOP_STRATEGIES == {
        "Donchian Channel Breakout",
        "RS Momentum Breakout",
        "VCP Volatility Contraction Breakout",
    }
    summary = summarize_live_portfolio_risk([{"symbol": "LEGACY"}])
    assert summary["reference_heat_pct"] == "NOT_AVAILABLE"
    assert summary["executable_stop_heat_pct"] == "NOT_AVAILABLE"
    assert summary["positions_without_reference"] == 1

    app_code = (ROOT / "app.py").read_text()
    assert "assemble_live_decisions(all_candidates)" in app_code
    assert "render_market_risk_context()" in app_code
    assert "Target: Not available" in app_code
    assert "Average R:R Ratio" not in app_code
    assert "Composite Conviction" not in app_code
    assert "st.session_state[\"sizing_mode\"] = \"EQUAL_WEIGHT\"" in app_code
    assert "st.session_state[\"exit_mode\"] = \"FIXED_10D\"" in app_code
    assert 'if run_analysis_btn:' in app_code
    assert '"qualification_status": "NOT_AVAILABLE"' in app_code
    economics_code = (ROOT / "trade_economics_context.py").read_text()
    assert "@lru_cache(maxsize=1)" in economics_code
    print("F1 final cockpit integration tests: PASS")


if __name__ == "__main__":
    run()
