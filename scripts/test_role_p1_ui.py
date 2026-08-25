#!/usr/bin/env python3
"""Focused qualified-opportunity ROLE evidence presentation and cache checks."""

import inspect
import os
from pathlib import Path
import tempfile
import time


ROOT = Path(__file__).resolve().parents[1]
temporary = tempfile.TemporaryDirectory()
os.environ.pop("DATABASE_URL", None)
os.environ["TRADING_COCKPIT_DB_PATH"] = str(Path(temporary.name) / "role-p1.sqlite")

import cockpit_cache  # noqa: E402
import cockpit_ui  # noqa: E402
import database  # noqa: E402
import latent_state_vector as lsv  # noqa: E402
from role_outcome_engine import OUTCOME_METHOD_HASH, ROLE_OUTCOME_VERSION  # noqa: E402
from sqlalchemy import event  # noqa: E402
from streamlit.testing.v1 import AppTest  # noqa: E402


def seed_recommendation(opportunity_id, symbol, *, observed=False):
    vector = lsv.empty_vector("2026-01-02")
    vector["participation"]["volume_ratio_20d"] = 2.0
    database.persist_recommendation_snapshot({
        "opportunity_id": opportunity_id, "signal_date": "2026-01-02",
        "signal_timestamp": "2026-01-02T11:00:00+00:00", "symbol": symbol,
        "strategy": "VCP", "reference_price": 100.0, "lsv_v1": vector,
        "historical_analog": {}, "market_context": {},
        "methodologies": {"decision_contract": "HISTORICAL_CANONICAL_PAYLOAD_BACKFILL" if observed else "LIVE"},
        "source_timestamps": {}, "missingness": vector["missingness"], "provenance": [],
    })
    if observed:
        observation = database.persist_role_observation_state({
            "opportunity_id": opportunity_id, "lsv_methodology_hash": lsv.LSV_METHOD_HASH,
            "signal_date": "2026-01-02", "reference_price": 100.0,
            "outcome_contract_version": ROLE_OUTCOME_VERSION, "outcome_methodology_hash": OUTCOME_METHOD_HASH,
            "lifecycle_state": "PARTIAL", "sessions_observed": 10,
            "last_observation_date": "2026-01-16", "source": {}, "completeness": {}, "missingness": [],
        })
        database.persist_role_outcome_horizon(observation["observation_id"], 10, "2026-01-16", {
            "close_return_pct": 2.0, "mfe_pct": 6.0, "mae_pct": -2.0,
            "plus_5_before_minus_3": {"value": True},
        })


def run():
    for index in range(5):
        seed_recommendation(f"ROLE-P1:H{index}", f"H{index}", observed=True)
    seed_recommendation("ROLE-P1:TARGET", "TARGET")
    cockpit_cache.load_role_evidence.clear()
    statements = []

    def record_statement(_connection, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement)

    event.listen(database.engine, "before_cursor_execute", record_statement)
    try:
        learning_rows = database.load_role_learning_rows(OUTCOME_METHOD_HASH)
    finally:
        event.remove(database.engine, "before_cursor_execute", record_statement)
    assert len(learning_rows) == 6
    assert len([statement for statement in statements if "FROM role_outcome_horizons" in statement]) == 1

    candidate = {
        "opportunity_id": "ROLE-P1:TARGET", "symbol": "TARGET", "signal_date": "2026-01-02",
        "qualification_status": "QUALIFIED", "is_qualified": True,
        "strategy": "VCP", "entry_price": 100.0, "allocation_status": "QUALIFIED",
    }
    app = AppTest.from_file(str(ROOT / "app.py")).run(timeout=30)
    app.session_state["live_decisions"] = [candidate]
    app.session_state["qualified_candidates"] = [candidate]
    app.query_params["page"] = "stock"
    app.query_params["symbol"] = "TARGET"
    app.query_params["tab"] = "role_evidence"
    app.run(timeout=30)
    passed = 1  # All recommendation horizons load in one bounded query.

    assert not app.exception and app.segmented_control[0].value == "ROLE Evidence"
    assert "ROLE Evidence" in app.segmented_control[0].options
    passed += 1  # Qualified recommendation has a durable dedicated ROLE tab.

    markdown = "\n".join(item.value for item in app.markdown)
    captions = "\n".join(item.value for item in app.caption)
    warnings = "\n".join(item.value for item in app.warning)
    assert "INSUFFICIENT EVIDENCE" in markdown
    assert "Current evidence is too limited" in warnings
    assert "SYSTEM_BASELINE_EVIDENCE_IS_INSUFFICIENT" in captions
    assert "NO_SUPPORTED_COMPARABLE_DRIVERS" in captions
    passed += 1  # Insufficient state and exact R2 reasons remain explicit.

    assert "Estimated +5% before -3%" not in markdown
    assert "Estimated MFE" not in markdown and "Estimated MAE" not in markdown
    passed += 1  # Unsupported estimates never appear.

    assert "N=0" in markdown and "N=5" in markdown and "Primary horizon · 10D" in markdown
    assert "0 / 5" in markdown and "Prospective / backfilled" in markdown
    assert "ROLE_R2_EMPIRICAL_BAYES_V1" in captions
    passed += 1  # N, horizon, evidence provenance, and methodology are visible.

    assert "Historical Analogs instead compares" in markdown
    assert "Advisory only" in markdown and "Not enough evidence" in captions
    passed += 1  # Explainability and unsupported drivers stay explicit.

    before = (database.recommendation_ledger_coverage()["rows"], database.role_outcome_lifecycle_counts(OUTCOME_METHOD_HASH))
    original_loader = cockpit_cache.load_role_r2_report
    cockpit_cache.load_role_r2_report = lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("warm ROLE cache missed"))
    try:
        started = time.perf_counter()
        app.run(timeout=30)
        warm_ms = (time.perf_counter() - started) * 1000
    finally:
        cockpit_cache.load_role_r2_report = original_loader
    after = (database.recommendation_ledger_coverage()["rows"], database.role_outcome_lifecycle_counts(OUTCOME_METHOD_HASH))
    assert not app.exception and before == after and warm_ms < 2_000
    passed += 1  # Cached repeat navigation does no read-through, writes, or observation.

    source = inspect.getsource(cockpit_ui._render_role_evidence)
    assert all(forbidden not in source for forbidden in ("fetch_", "persist_", "observe_", "execute_paper_trade"))
    passed += 1  # ROLE rendering cannot call a provider or trading/observation writer.

    report = cockpit_cache.load_role_evidence(candidate["opportunity_id"], candidate["signal_date"])
    positive = {"driver": "participation", "state": "HIGH", "sample_size": 12, "success_difference_vs_baseline_pp": 4.5}
    negative = {"driver": "strategy", "state": "VCP", "sample_size": 12, "success_difference_vs_baseline_pp": -2.0}
    supported = {
        **report, "status": "EARLY", "evidence_quality": "EARLY", "effective_comparable_sample_size": 12,
        "estimated_plus_5_before_minus_3_success_pct": 62.0,
        "estimated_mfe_pct": 7.0, "estimated_mae_pct": -2.2,
        "estimated_close_return_pct": 3.0,
        "positive_learned_drivers": [positive], "negative_learned_drivers": [negative],
        "insufficiency_reasons": [],
    }
    original_ui_loader = cockpit_ui.load_role_evidence
    cockpit_ui.load_role_evidence = lambda *_args, **_kwargs: supported
    try:
        app.run(timeout=30)
        supported_markdown = "\n".join(item.value for item in app.markdown)
        supported_captions = "\n".join(item.value for item in app.caption)
    finally:
        cockpit_ui.load_role_evidence = original_ui_loader
    assert "Estimated +5% before -3%" in supported_markdown and "Estimated MFE" in supported_markdown
    assert "Positive learned drivers" in supported_markdown and "Negative learned drivers" in supported_markdown
    assert "participation · HIGH · N=12 · +4.50 pp" in supported_captions
    assert "strategy · VCP · N=12 · -2.00 pp" in supported_captions
    passed += 1  # Supported estimates and drivers map exclusively to R2 output.

    research_candidate = {**candidate, "is_qualified": False, "qualification_status": "RESEARCH_ONLY"}
    app.session_state["live_decisions"] = [research_candidate]
    app.session_state["qualified_candidates"] = []
    app.query_params["tab"] = "overview"
    app.run(timeout=30)
    assert not app.exception and "ROLE Evidence" not in app.segmented_control[0].options
    passed += 1  # Research-only stocks do not gain recommendation evidence.

    assert passed == 10
    print(f"ROLE-P1 opportunity evidence UI tests: PASS ({passed}/10 scenarios) · warm render {warm_ms:.2f} ms")


if __name__ == "__main__":
    try:
        run()
    finally:
        temporary.cleanup()
