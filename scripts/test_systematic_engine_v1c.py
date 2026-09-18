#!/usr/bin/env python3
"""Focused Systematic Engine V1C acceptance tests."""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
import sys
import tempfile

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
DB = Path(tempfile.gettempdir()) / "systematic_engine_v1c_test.db"
if DB.exists(): DB.unlink()
os.environ["TRADING_COCKPIT_DB_PATH"] = str(DB)
os.environ["AUTOPAPER_PROSPECTIVE_ENABLED"] = "1"
os.environ["AUTOPAPER_KILL_SWITCH"] = "0"

import database
import autopaper_prospective as engine
import opportunity_selection_engine as selection
from autopaper_ui_service import load_autopaper_ui_state

passed = 0


def check(value):
    global passed
    assert value
    passed += 1


def candidate(symbol, date, rank, atr=4.):
    return {"opportunity_id": f"{date}:{symbol}:VCP", "symbol": f"{symbol}.NS",
        "signal_date": str(date), "strategy": "VCP", "entry_price": 100., "atr_20": atr,
        "current_volume": 2_000_000, "sector": "Industrials", "qualification_status": "QUALIFIED",
        "opportunity_priority_rank": rank}


def histories(symbols, end="2026-09-18"):
    dates = pd.bdate_range(end=end, periods=45); result = {}
    for index, symbol in enumerate(symbols):
        close = 90 + np.linspace(0, 10 + index * 3, len(dates))
        volume = np.full(len(dates), 1_000_000.); volume[-1] = 1_500_000 + index * 300_000
        result[f"{symbol}.NS"] = pd.DataFrame({"Open": close - .2, "High": close + 1 + index * .1,
            "Low": close - 1, "Close": close + index * .1, "Volume": volume}, index=dates)
    benchmark = np.linspace(95, 100, len(dates))
    result["NIFTY500"] = pd.DataFrame({"Open": benchmark, "High": benchmark + 1,
        "Low": benchmark - 1, "Close": benchmark, "Volume": 1_000_000.}, index=dates)
    return result


def main():
    manifest = json.loads((ROOT / "config/selection_feature_manifest_v1.json").read_text())
    check(manifest == selection.FEATURE_MANIFEST)
    check(len(manifest["features"]) == 4 and selection.FEATURE_MANIFEST_HASH == selection._digest(manifest))
    check(engine.METHODOLOGY_HASH == "3ed64bac9138d36cc1c582c78803215e0142cd12bf8c9db3c50bfc05e3f43d79")
    check(set(engine.OPPORTUNITY_SELECTION_ACCOUNT_CONFIGS) == {"SHADOW_SELECT_QUALITY", "SHADOW_SELECT_RISK_ADJ"})
    check(all(not config["decision_authority"] for config in engine.OPPORTUNITY_SELECTION_CONFIGS.values()))
    check(all(config["downstream_risk_policy"] == "B1" for config in engine.OPPORTUNITY_SELECTION_CONFIGS.values()))

    date = dt.date(2026, 9, 18); data = histories(["A", "B", "C"])
    row = engine._candidate(candidate("A", date, 1), date)
    before = selection.extract_candidate_features(row, data)
    future = {key: value.copy() for key, value in data.items()}
    future["A.NS"].loc[pd.Timestamp("2026-09-21")] = [1, 1000, 1, 1000, 99_000_000]
    after = selection.extract_candidate_features(row, future)
    check(before == after)
    check(before["feature_manifest_hash"] == selection.FEATURE_MANIFEST_HASH)
    check(before["risk_proxy_pct"] >= before["atr_pct"] and before["rv20_valid_returns"] == 20)

    raw = [
        {"opportunity_id": "A", "signal_date": "2026-09-18", "selection_inputs": {"features": {
            "relative_demand_20d_pct": 1., "volume_ratio_20d": 1., "close_location_value": 1., "ema20_extension_pct": 1.}, "risk_proxy_pct": 2., "eligible": True}},
        {"opportunity_id": "B", "signal_date": "2026-09-18", "selection_inputs": {"features": {
            "relative_demand_20d_pct": 2., "volume_ratio_20d": 2., "close_location_value": 2., "ema20_extension_pct": 2.}, "risk_proxy_pct": 8., "eligible": True}},
        {"opportunity_id": "C", "signal_date": "2026-09-17", "selection_inputs": {"features": {
            "relative_demand_20d_pct": 3., "volume_ratio_20d": 3., "close_location_value": "NOT_AVAILABLE", "ema20_extension_pct": "NOT_AVAILABLE"}, "risk_proxy_pct": 3., "eligible": True}},
    ]
    c1 = selection.score_candidate_set(raw, "C1")
    check([row["opportunity_id"] for row in c1] == ["B", "A", "C"])
    check(c1[-1]["quality_score"] == "NOT_AVAILABLE" and c1[-1]["quality_feature_coverage"] == 2)
    c2 = selection.score_candidate_set(raw, "C2")
    expected = .5 * next(x for x in c2 if x["opportunity_id"] == "A")["quality_score"] + .5 * (
        1 - next(x for x in c2 if x["opportunity_id"] == "A")["risk_percentile"])
    check(next(x for x in c2 if x["opportunity_id"] == "A")["risk_adjusted_quality"] == expected)
    check(selection.score_candidate_set(raw, "C2") == c2)
    ineligible = {"opportunity_id": "X", "signal_date": "2026-09-18", "ranking_eligible": False,
        "selection_inputs": {"features": {name: 1_000. for name in manifest["features"]},
            "risk_proxy_pct": .01, "eligible": True}}
    with_ineligible = selection.score_candidate_set([*raw, ineligible], "C1")
    check([row["quality_score"] for row in with_ineligible if row["opportunity_id"] in {"A", "B", "C"}] ==
          [row["quality_score"] for row in c1])
    check(next(row for row in with_ineligible if row["opportunity_id"] == "X")["selection_rank"] is None)
    event_id = selection.selection_event_id(date, ["A", "B", "C"])
    random_a = selection.random_orderings(event_id, ["A", "B", "C"])
    check(random_a == selection.random_orderings(event_id, ["C", "A", "B"]) and len(random_a) == 20)
    check(all(sorted(ordering) == ["A", "B", "C"] for ordering in random_a.values()))

    attribution = selection.selection_attribution([
        {"admitted": True, "quality_score": .9, "outcome": {"h10_net_return_pct": 5., "mfe_pct": 7., "mae_pct": -1., "plus_5_before_minus_3": 1., "realized_efficiency": 5.}},
        {"admitted": False, "quality_score": .5, "outcome": {"h10_net_return_pct": 1., "mfe_pct": 3., "mae_pct": -2., "plus_5_before_minus_3": 0., "realized_efficiency": .5}},
        {"admitted": False, "quality_score": .1},
    ], "quality_score")
    check(attribution["mature_comparisons"] == 2)
    check(attribution["selection_lift"]["h10_net_return_pct"] == 4.)
    check(attribution["selection_regret"]["selected_minus_best_rejected"] == 4.)
    check(selection.selection_attribution([
        {"admitted": True, "quality_score": .9, "outcome": {"close_return_pct": 2., "plus_5_before_minus_3": "TARGET_FIRST"}},
        {"admitted": False, "quality_score": .2, "outcome": {"close_return_pct": -1., "plus_5_before_minus_3": "STOP_FIRST"}},
    ], "quality_score")["selection_lift"]["plus_5_before_minus_3"] == 1.)

    database.init_db(); engine._manifest_and_accounts()
    phase_b_session = database.SessionLocal()
    try:
        phase_b_payload = {"status": "ACTIVE", "activation_signal_date": date.isoformat()}
        phase_b_session.add(database.PortfolioRiskActivation(
            activation_id="PORTFOLIO_RISK_V1B_ACTIVATION", status="ACTIVE",
            activation_mode="SAME_COHORT", activation_timestamp=dt.datetime(2026, 9, 18, 11, tzinfo=dt.timezone.utc),
            activation_signal_date=date, after_market_date=None, provenance="TEST",
            payload=engine._json(phase_b_payload), payload_hash=engine._hash(phase_b_payload)))
        phase_b_session.commit()
    finally:
        phase_b_session.close()
    activation = engine.ensure_opportunity_selection_activation(date, dt.datetime(2026, 9, 18, 12, tzinfo=dt.timezone.utc))
    check(activation["status"] == "ACTIVE" and activation["activation_mode"] == "SAME_B1_COHORT")
    engine.ensure_opportunity_selection_accounts(dt.datetime(2026, 9, 18, 12, tzinfo=dt.timezone.utc), date)
    decisions = [candidate(symbol, date, index + 1, 2 + index * 2) for index, symbol in enumerate(("A", "B", "C"))]
    before_core = engine._hash([(row.account_id, row.cash, row.state_version) for row in
        database.SessionLocal().query(database.AutoPaperAccount).filter(
            database.AutoPaperAccount.account_id.in_(tuple(engine.ACCOUNTS))).all()])
    for account_id in engine.OPPORTUNITY_SELECTION_ACCOUNT_CONFIGS:
        result = engine._process_account(account_id, decisions, data, date,
            dt.datetime(2026, 9, 18, 12, tzinfo=dt.timezone.utc))
        check(result["admission_used"] == 2 and result["constrained_selection_event"])
    session = database.SessionLocal()
    try:
        snapshots = session.query(database.OpportunitySelectionCandidateSnapshot).all()
        check(len(snapshots) == 6 and all(row.constrained for row in snapshots))
        check(sum(row.admitted for row in snapshots) == 4)
        check(session.query(database.OpportunitySelectionRandomOrdering).count() == 60)
        check(all(json.loads(row.payload)["decision_authority"] is False for row in snapshots))
        check(all(session.query(database.AutoPaperOrder).filter_by(account_id=account, side="BUY", status="PENDING").count() == 2
                  for account in engine.OPPORTUNITY_SELECTION_ACCOUNT_CONFIGS))
        after_core = engine._hash([(row.account_id, row.cash, row.state_version) for row in
            session.query(database.AutoPaperAccount).filter(
                database.AutoPaperAccount.account_id.in_(tuple(engine.ACCOUNTS))).all()])
        check(before_core == after_core)
    finally:
        session.close()
    retry = engine._process_account("SHADOW_SELECT_QUALITY", decisions, data, date,
        dt.datetime(2026, 9, 18, 12, tzinfo=dt.timezone.utc))
    check(retry["idempotent"] is True)
    ui = load_autopaper_ui_state(tuple(row["opportunity_id"] for row in decisions))
    check(ui["opportunity_selection"]["activation"]["status"] == "ACTIVE")
    check(set(ui["opportunity_selection"]["accounts"]) == set(engine.OPPORTUNITY_SELECTION_ACCOUNT_CONFIGS))
    check(ui["opportunity_selection"]["random_seed_count"] == 20)
    check(ui["opportunity_selection"]["completed_matched_events"] == 0)
    check(ui["opportunity_selection"]["review_gate"]["completed_matched_events"]["target"] == 60)
    from streamlit.testing.v1 import AppTest
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30)
    app.query_params["page"] = "portfolio"; app.run(timeout=30)
    app.session_state["portfolio_view"] = "Shadows"; app.run(timeout=30)
    check(not app.exception)
    check(any("Opportunity Selection Engine" in str(item.value) for item in app.markdown))
    learning = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30)
    learning.query_params["page"] = "learning"; learning.run(timeout=30)
    check(not learning.exception)
    check(any("Selection evidence" in str(item.label) for item in learning.expander))
    print(f"Systematic Engine V1C focused tests: {passed} passed")


if __name__ == "__main__":
    main()
