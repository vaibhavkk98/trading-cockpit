#!/usr/bin/env python3
"""Focused Systematic Engine V1E causal-ledger acceptance tests."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
DB = Path(tempfile.gettempdir()) / "systematic_engine_v1e_test.db"
if DB.exists():
    DB.unlink()
os.environ["TRADING_COCKPIT_DB_PATH"] = str(DB)

import database
import role_policy_learning as role

passed = 0


def check(value):
    global passed
    assert value
    passed += 1


def dump(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def digest(value):
    return hashlib.sha256(dump(value).encode()).hexdigest()


def seed_account(session, account_id, version):
    method = digest([account_id, version])
    config = {"account": account_id, "paper_only": True, "decision_authority": False}
    session.add(database.AutoPaperManifest(
        methodology_hash=method, version=version,
        activation_timestamp=dt.datetime(2026, 9, 19, 8, tzinfo=dt.timezone.utc),
        activation_market_date=dt.date(2026, 9, 19), config_payload=dump(config),
        config_hash=digest(config), code_identity="TEST"))
    session.add(database.AutoPaperAccount(
        account_id=account_id, methodology_hash=method, initial_capital=1_000_000,
        cash=900_000, last_market_date=dt.date(2026, 9, 21), status="ACTIVE", state_version=1))


def seed_recommendation(session, opportunity_id, signal_date, symbol="TEST"):
    vector = {"atr_pct": 3.2, "realized_volatility_20d": 22.1, "path_risk_state": "NORMAL"}
    session.add(database.RecommendationLedger(
        opportunity_id=opportunity_id, signal_date=signal_date,
        signal_timestamp=dt.datetime.combine(signal_date, dt.time(16), tzinfo=dt.timezone.utc),
        symbol=symbol, strategy="VCP", reference_price=100., allocator_status="SELECTED",
        opportunity_rank=1, lsv_contract_version="LSV_V1", lsv_methodology_hash=digest("LSV"),
        vector_payload=dump(vector), historical_analog_payload=dump({}),
        market_context_payload=dump({"trend": "POSITIVE"}), methodology_payload=dump({}),
        source_timestamps=dump({"ohlcv": signal_date.isoformat()}), missingness=dump([]),
        provenance=dump({"origin": "PROSPECTIVE"}), snapshot_hash=digest(vector)))


def seed_outcome(session, opportunity_id, signal_date, result=4.0):
    observation = database.RoleOutcomeObservation(
        opportunity_id=opportunity_id, lsv_methodology_hash=digest("LSV"), signal_date=signal_date,
        reference_price=100., outcome_contract_version="ROLE_D1", outcome_methodology_hash=digest("ROLE_D1"),
        lifecycle_state="PARTIAL", sessions_observed=10, last_observation_date=signal_date + dt.timedelta(days=14),
        source_payload=dump({"origin": "PROSPECTIVE"}), completeness=dump({"10": True}), missingness=dump([]))
    session.add(observation); session.flush()
    payload = {"close_return_pct": result, "mfe_pct": 7., "mae_pct": -2.,
               "maximum_gain_pct": 7., "maximum_drawdown_pct": -2.,
               "plus_5_before_minus_3": {"value": True, "first_hit": "+5"}}
    for horizon, days in ((5, 7), (10, 14), (20, 28)):
        horizon_payload = {**payload, "horizon_sessions": horizon}
        session.add(database.RoleOutcomeHorizon(
            observation_id=observation.id, horizon_sessions=horizon,
            observation_date=signal_date + dt.timedelta(days=days), payload=dump(horizon_payload),
            payload_hash=digest(horizon_payload)))


def seed_decision(session, account_id, opportunity_id, market_date, action, reason, created_at):
    decision_id = digest([account_id, opportunity_id, market_date, action, reason])
    payload = {"symbol": "TEST", "signal_date": market_date.isoformat(), "strategy": "VCP",
               "qualification_status": "QUALIFIED", "reference_price": 100.}
    session.add(database.AutoPaperDecision(
        decision_id=decision_id, account_id=account_id, opportunity_id=opportunity_id,
        decision_timestamp=created_at, market_date=market_date, action=action, reason_code=reason,
        payload=dump(payload), payload_hash=digest(payload), created_at=created_at))
    return decision_id


def main():
    database.init_db()
    market_date = dt.date(2026, 9, 21)
    activation_time = dt.datetime(2026, 9, 19, 12, tzinfo=dt.timezone.utc)
    activation = role.ensure_activation(dt.date(2026, 9, 19), activation_time)
    check(activation["created"])
    check(role.ensure_activation(dt.date(2026, 9, 20), activation_time + dt.timedelta(days=1))["created"] is False)
    check(role.normalize_action("QUEUE", "CAPACITY") == "DEFER")
    check(role.normalize_action("EXIT", "H10_TIME_EXIT") == "EXIT_TIME")
    check(role.normalize_action("EXIT", "CATASTROPHE_EXIT") == "EXIT_CATASTROPHE")
    check(role.normalize_action("EXIT", "THESIS_FAILURE") == "EXIT_THESIS")

    opportunity = "2026-09-21:TEST:VCP"
    old_opportunity = "2026-09-18:OLD:VCP"
    accounts = {"SHADOW_ROLLING": "B0", "SHADOW_RISK_BUDGET": "B1", "SHADOW_RISK_DIVERSIFIED": "B2"}
    session = database.SessionLocal()
    try:
        for account, code in accounts.items():
            seed_account(session, account, f"TEST_{code}")
        seed_recommendation(session, opportunity, market_date)
        seed_recommendation(session, old_opportunity, dt.date(2026, 9, 18), "OLD")
        seed_outcome(session, opportunity, market_date)
        seed_decision(session, "SHADOW_ROLLING", old_opportunity, dt.date(2026, 9, 18), "ENTER", "FILLED",
                      activation_time - dt.timedelta(minutes=1))
        actions = {"SHADOW_ROLLING": ("ENTER", "FILLED"),
                   "SHADOW_RISK_BUDGET": ("ENTER", "FILLED"),
                   "SHADOW_RISK_DIVERSIFIED": ("REJECT", "HEAT_LIMIT")}
        for index, (account, (action, reason)) in enumerate(actions.items(), 1):
            seed_decision(session, account, opportunity, market_date, action, reason,
                          activation_time + dt.timedelta(minutes=index))
            session.add(database.PortfolioRiskMatch(
                leg_id=digest([account, opportunity]), match_id=digest(opportunity),
                opportunity_id=opportunity, signal_date=market_date, account_id=account,
                policy=accounts[account], considered=True, entered=action == "ENTER",
                allocation=100_000 if action == "ENTER" else None,
                entry_date=market_date if action == "ENTER" else None,
                entry_price=100. if action == "ENTER" else None,
                exit_date=market_date + dt.timedelta(days=14) if action == "ENTER" else None,
                realized_return_pct=5. if account == "SHADOW_ROLLING" else (3. if action == "ENTER" else None),
                payload=dump({"causal": True}), payload_hash=digest([account, "match"])))
            if action == "ENTER":
                session.add(database.AutoPaperTrade(
                    trade_id=digest([account, "trade"]), account_id=account, opportunity_id=opportunity,
                    symbol="TEST", entry_date=market_date, exit_date=market_date + dt.timedelta(days=14),
                    net_pnl=5000 if account == "SHADOW_ROLLING" else 3000,
                    realized_return_pct=5. if account == "SHADOW_ROLLING" else 3., holding_sessions=10,
                    payload=dump({"exit_reason": "H10_TIME_EXIT"}), payload_hash=digest([account, "trade-payload"])))
        session.commit()
    finally:
        session.close()

    started = time.perf_counter()
    result = role.ingest_policy_learning(market_date, "ROLE-EOD-1")
    ingestion_ms = (time.perf_counter() - started) * 1000
    check(result["status"] == "HEALTHY")
    check(result["episodes_created"] == 3)
    check(result["direct_matches"] >= 1)
    check(ingestion_ms < 2_000)
    session = database.SessionLocal()
    try:
        episodes = session.query(database.PolicyDecisionEpisode).all()
        check(len(episodes) == 3)
        check(not any(e.opportunity_id == old_opportunity for e in episodes))
        check(all(json.loads(e.source_provenance)["origin"] == "PROSPECTIVE" for e in episodes))
        check(all(json.loads(e.action_state)["decision_authority"] is False for e in episodes))
        hashes_before = {e.episode_id: e.snapshot_hash for e in episodes}
        risk_group = session.query(database.PolicyMatchGroup).filter_by(
            policy_family="RISK", opportunity_id=opportunity).one()
        check(risk_group.evidence_class == "DIRECT_MATCH")
        check(risk_group.counterfactual_class == "OBSERVED_COUNTERFACTUAL")
        comparison = session.query(database.PolicyComparisonSnapshot).filter_by(
            policy_family="RISK").one()
        compared = json.loads(comparison.payload)["observed_feasible_alternatives"]
        check(sorted(row["decision_regret_pct"] for row in compared) == [-2.0, 0.0])
    finally:
        session.close()

    retry = role.ingest_policy_learning(market_date, "ROLE-EOD-2")
    check(retry["episodes_created"] == 0 and retry["idempotent_existing"] == 3)
    session = database.SessionLocal()
    try:
        check({e.episode_id: e.snapshot_hash for e in session.query(database.PolicyDecisionEpisode).all()} == hashes_before)
        check(session.query(database.PolicyComparisonSnapshot).count() == 1)
        check(session.query(database.PolicyMatureOutcomeLink).count() == 9)
        check(session.query(database.RolePolicyReviewGateSnapshot).count() == 2)
    finally:
        session.close()

    state = role.load_policy_learning_state()
    check(state["episode_count"] == 3 and state["mature_episode_count"] == 3)
    check(state["direct_match_count"] >= 1)
    check(state["review_gate"]["passed"] is False)
    check(state["decision_authority"] is False)
    rows = role.load_episode_training_rows(mature_only=True)
    check(len(rows) == 3 and all(row["outcome_status"] == "MATURE" for row in rows))
    check(all(row["decision_authority"] is False for row in rows))
    check(role.SCHEMA_HASH == digest(role.EPISODE_SCHEMA))

    from streamlit.testing.v1 import AppTest
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30)
    app.query_params["page"] = "learning"; app.run(timeout=30)
    check(not app.exception)
    check(any("ROLE Policy Learning" in str(item.value) for item in app.markdown))
    check(any("Prospective policy evidence" in str(item.value) for item in app.markdown))

    print(f"Systematic Engine V1E focused tests: {passed} passed · ingestion {ingestion_ms:.2f} ms")


if __name__ == "__main__":
    main()
