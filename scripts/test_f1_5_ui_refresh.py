"""Focused F1.5 UI-refresh guardrails; presentation only, no methodology audit."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from live_decision_adapter import assemble_live_decisions  # noqa: E402
from ui_components import allocation_display, display_value, format_currency, format_percent, format_price, format_volume  # noqa: E402


def candidate(symbol, strategy, volume_ratio, status, atr_available=True):
    return {
        "symbol": symbol,
        "strategy": strategy,
        "signal_date": "2026-08-10",
        "entry_price": 100.0,
        "atr_20": 2.0,
        "atr_20_available": atr_available,
        "quantity": 1_000,
        "volume_ratio_20": volume_ratio,
        "is_qualified": True,
        "regime": "BULLISH",
        "status": status,
        "target_price": 140.0,
        "risk_reward_ratio": 2.0,
        "composite_score": 0.9,
    }


def run():
    decisions = assemble_live_decisions([
        candidate("DONCH", "Donchian Channel Breakout", 2.1, "ALLOCATED"),
        candidate("EMA", "EMA Pullback / Bounce", 3.0, "QUALIFIED — CAPITAL CAP"),
    ])
    assert [row["symbol"] for row in decisions] == ["EMA", "DONCH"]
    assert [row["opportunity_priority_rank"] for row in decisions] == [1, 2]
    assert decisions[1]["initial_executable_stop"] == 96.0
    assert decisions[0]["executable_stop_enabled"] is False
    assert decisions[1]["reference_risk_per_share"] == "NOT_AVAILABLE"
    assert decisions[1]["reference_heat_before_pct"] == "NOT_AVAILABLE"
    assert all("target_price" not in row and "risk_reward_ratio" not in row and "composite_score" not in row for row in decisions)

    assert allocation_display("ALLOCATED") == ("Allocated", "good")
    assert allocation_display("QUALIFIED_NOT_ALLOCATED_CAPITAL_CAP", "CAPITAL_CAP")[0] == "Capacity"
    assert display_value("NOT_AVAILABLE") == "Not available"
    assert display_value("NOT_APPLICABLE") == "—"
    assert display_value(float("nan")) == "Not available"
    assert format_currency(124_000) == "₹1.24L"
    assert format_price(1121.3) == "₹1,121.30"
    assert format_percent(1.24) == "1.24%"
    assert format_volume(2.36) == "2.36×"

    app_code = (ROOT / "app.py").read_text()
    component_code = (ROOT / "ui_components.py").read_text()
    assert "from ui_components import" in app_code
    assert "apply_theme()" in app_code
    assert "render_market_risk_card(context)" in app_code
    assert "render_metric_card(" in app_code
    assert "Record paper trade" in app_code
    assert "if run_analysis_btn:" in app_code
    assert "target_price" not in app_code
    assert "risk_reward_ratio" not in app_code
    assert "Composite Conviction" not in app_code
    assert "Average R:R Ratio" not in app_code
    assert "@lru_cache(maxsize=1)" in (ROOT / "trade_economics_context.py").read_text()
    assert "Does not alter qualification, allocation, sizing, or stops." in component_code
    print("F1.5 UI refresh tests: PASS")


if __name__ == "__main__":
    run()
