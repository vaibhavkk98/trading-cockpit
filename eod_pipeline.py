"""Headless canonical EOD orchestration for the frozen Trading Cockpit policy."""

import datetime as dt
from typing import Any, Dict, Optional

from adapters import ExecutionAdapter, MarketDataProvider, PortfolioAllocationEngine, SignalEngine, UniverseProvider
from event_intelligence import EventIntelligenceService
from live_decision_adapter import assemble_live_decisions
from database import persist_analysis_run
from operational_runtime import SCAN_PARTIAL, SCAN_SUCCESS


DECISION_CONTRACT_VERSION = "TRADING_COCKPIT_V1_1_EOD"


def expected_indian_market_date(now: Optional[dt.datetime] = None) -> dt.date:
    """The scheduled workflow runs after close; bar presence remains the holiday authority."""
    now = now or dt.datetime.now(dt.timezone(dt.timedelta(hours=5, minutes=30)))
    return now.date()


def has_completed_market_bar(regime_info: Dict[str, Any], expected_date: dt.date) -> bool:
    return str(regime_info.get("data_as_of") or "") == expected_date.isoformat()


def execute_eod_pipeline(analysis_date: Optional[dt.date] = None, source: str = "AUTOMATED_EOD", dependencies: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Run the existing production screen/allocation once, then persist its exact decision payload."""
    analysis_date = analysis_date or expected_indian_market_date()
    if isinstance(analysis_date, str):
        analysis_date = dt.date.fromisoformat(analysis_date)
    deps = dependencies or {}
    market = deps.get("market_data") or MarketDataProvider()
    universe = deps.get("universe") or UniverseProvider()
    signals = deps.get("signals") or SignalEngine()
    execution = deps.get("execution") or ExecutionAdapter()
    allocator = deps.get("allocator") or PortfolioAllocationEngine()
    event_service = deps.get("event_service") or EventIntelligenceService()
    started = dt.datetime.now(dt.timezone.utc)
    run_id = f"EOD-{analysis_date.isoformat()}"
    regime = market.get_index_regime(as_of_date=analysis_date.isoformat())
    if not has_completed_market_bar(regime, analysis_date):
        return {"status": "NO_COMPLETED_MARKET_BAR", "analysis_date": analysis_date.isoformat(), "run_id": run_id,
                "persisted": False, "reason": "Expected completed daily market bar is absent."}
    try:
        symbols = universe.get_universe(date_str=analysis_date.isoformat())
        shortlist, diagnostics = signals.run_stage1_screening(symbols=symbols, max_scan=None, as_of_date=analysis_date.isoformat(), return_diagnostics=True)
        candidates = allocator.allocate_candidates(shortlist_df=shortlist, regime_info=regime, open_positions=execution.get_open_positions(),
                                                   position_sizing_mode="EQUAL_WEIGHT", exit_rule_mode="FIXED_10D")
        try:
            cutoff = dt.datetime.combine(analysis_date, dt.time.max, tzinfo=dt.timezone.utc)
            event_service.enrich_candidates(candidates, cutoff=cutoff)
        except Exception:
            pass  # Informational context is deliberately not a pipeline failure.
        decisions = assemble_live_decisions(candidates)
        succeeded = int(diagnostics.get("valid_data_count", 0)); requested = len(symbols); failed = max(0, requested - succeeded)
        status = SCAN_PARTIAL if succeeded and failed else SCAN_SUCCESS
        completed = dt.datetime.now(dt.timezone.utc)
        run = {"run_id": run_id, "analysis_date": analysis_date, "started_at": started, "completed_at": completed,
               "status": status, "symbols_requested": requested, "symbols_succeeded": succeeded, "symbols_failed": failed,
               "qualified_count": len(decisions), "allocated_count": sum(item.get("allocation_status") == "ALLOCATED" for item in decisions),
               "provider_summary": diagnostics, "decision_contract_version": DECISION_CONTRACT_VERSION, "source": source}
        persisted = persist_analysis_run(run, decisions)
        mark_result = execution.refresh_portfolio_positions(source_run_id=run_id)
        snapshot_reason = "AUTOMATED_EOD" if source == "AUTOMATED_EOD" else "ANALYSIS_COMPLETED"
        execution.save_portfolio_snapshot(snapshot_reason)
        return {**run, "persisted": True, "decisions": decisions, "regime_info": regime, "diagnostics": diagnostics,
                "mark_count": len([p for p in mark_result.get("positions", []) if p.get("current_price") is not None]), "snapshot_reason": snapshot_reason}
    except Exception as exc:
        return {"status": "FAILED", "analysis_date": analysis_date.isoformat(), "run_id": run_id, "persisted": False,
                "error_summary": type(exc).__name__}
