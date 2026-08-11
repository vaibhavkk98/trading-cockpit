"""Focused F2 operational hardening checks; uses an isolated temporary SQLite DB."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import os
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from live_decision_adapter import summarize_live_portfolio_risk  # noqa: E402
from operational_runtime import SCAN_NOT_RUN, initial_scan_state, scan_freshness  # noqa: E402
import trade_economics_context as economics  # noqa: E402


def run():
    state = initial_scan_state()
    assert state["status"] == SCAN_NOT_RUN and scan_freshness(state) == "NOT_AVAILABLE"
    state.update({"status": "SUCCESS", "analysis_date": "2026-08-11", "scan_completed_at": "2026-08-11T10:00:00+00:00"})
    assert scan_freshness(state, today="2026-08-11") == "CURRENT"
    assert scan_freshness(state, today="2026-08-12") == "STALE"

    measured = {"risk_reference_available": True, "reference_risk_rupees": 2_500.0,
                "executable_stop_enabled": True, "executable_risk_rupees": 1_500.0}
    legacy = {"risk_metadata_status": "RISK_METADATA_NOT_AVAILABLE"}
    summary = summarize_live_portfolio_risk([measured, legacy])
    assert summary["reference_heat_pct"] == 0.25 and summary["executable_stop_heat_pct"] == 0.15
    assert summary["positions_with_reference"] == 1 and summary["positions_without_reference"] == 1

    original = economics._frozen_display_payload
    economics._frozen_display_payload = lambda: (_ for _ in ()).throw(OSError("missing"))
    try:
        assert economics.get_trade_economics_context("Donchian Channel Breakout")["availability"] == "NOT_AVAILABLE"
    finally:
        economics._frozen_display_payload = original

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_db = str(Path(temp_dir) / "isolated_paper.db")
        code = """
from database import add_paper_trade, get_open_trades
t = add_paper_trade(symbol='F2TEST.NS', entry_price=100.0, quantity=10,
    strategy_used='Donchian Channel Breakout', risk_metadata={
      'risk_contract_version':'F2_V1','allocation_status':'ALLOCATED',
      'risk_reference_type':'PRIOR_LOW','risk_reference_available':True,
      'reference_risk_per_share':2.0,'reference_risk_rupees':20.0,
      'executable_stop_enabled':True,'initial_executable_stop':96.0,
      'executable_risk_per_share':4.0,'executable_risk_rupees':40.0,
      'gap_risk_possible':True,'target_status':'NOT_AVAILABLE'})
r = get_open_trades()[0]
assert r.risk_contract_version == 'F2_V1' and r.target_status == 'NOT_AVAILABLE'
assert r.initial_executable_stop == 96.0 and r.reference_risk_rupees == 20.0
print('isolated DB metadata: PASS')
"""
        env = dict(os.environ, TRADING_COCKPIT_DB_PATH=temp_db)
        result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, text=True, capture_output=True)
        assert result.returncode == 0, result.stderr
        assert "isolated DB metadata: PASS" in result.stdout

    app_code = (ROOT / "app.py").read_text()
    assert "if run_analysis_btn:" in app_code and "SCAN_RUNNING" in app_code
    assert "paper_submission_keys" in app_code and "Record paper trade" in app_code
    assert "target_price" not in app_code and "risk_reward_ratio" not in app_code
    assert "st.session_state[\"sizing_mode\"] = \"EQUAL_WEIGHT\"" in app_code
    assert "st.session_state[\"max_positions\"] = 10" in app_code
    database_code = (ROOT / "database.py").read_text()
    assert 'if trade.risk_contract_version == "F2_V1":' in database_code
    print("F2 V1 hardening tests: PASS")


if __name__ == "__main__":
    run()
