"""Headless canonical EOD orchestration for the frozen Trading Cockpit policy."""

import datetime as dt
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

from adapters import ExecutionAdapter, MarketDataProvider, PortfolioAllocationEngine, SignalEngine, UniverseProvider
from event_intelligence import EventIntelligenceService
from live_decision_adapter import assemble_live_decisions
from database import persist_analysis_run
from operational_runtime import SCAN_PARTIAL, SCAN_SUCCESS


DECISION_CONTRACT_VERSION = "TRADING_COCKPIT_V1_1_EOD"
INDIAN_MARKET_TIMEZONE = ZoneInfo("Asia/Kolkata")
EOD_READINESS_TIME = dt.time(16, 7)
EXPECTED_SESSION_LOOKBACK_DAYS = 10


def resolve_expected_completed_market_date(
    now: Optional[dt.datetime] = None,
    completed_bar_lookup=None,
    readiness_time: dt.time = EOD_READINESS_TIME,
    max_lookback_days: int = EXPECTED_SESSION_LOOKBACK_DAYS,
) -> Optional[dt.date]:
    """Resolve the latest eligible IST session, with provider bars as holiday authority."""
    local_now = now or dt.datetime.now(INDIAN_MARKET_TIMEZONE)
    if local_now.tzinfo is None:
        local_now = local_now.replace(tzinfo=INDIAN_MARKET_TIMEZONE)
    else:
        local_now = local_now.astimezone(INDIAN_MARKET_TIMEZONE)
    candidate = local_now.date() if local_now.time().replace(tzinfo=None) >= readiness_time else local_now.date() - dt.timedelta(days=1)
    for offset in range(max_lookback_days + 1):
        session_date = candidate - dt.timedelta(days=offset)
        if session_date.weekday() >= 5:
            continue
        if completed_bar_lookup is None or completed_bar_lookup(session_date):
            return session_date
    return None


def expected_indian_market_date(now: Optional[dt.datetime] = None) -> dt.date:
    """Compatibility alias for UI defaults; provider validation occurs in the pipeline."""
    resolved = resolve_expected_completed_market_date(now=now)
    if resolved is None:  # Bounded search always finds a weekday without a provider callback.
        raise RuntimeError("Unable to resolve an eligible Indian market date.")
    return resolved


def normalize_market_date(value: Any) -> Optional[dt.date]:
    """Normalize provider timestamps to an Asia/Kolkata trading date."""
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        localized = value.replace(tzinfo=INDIAN_MARKET_TIMEZONE) if value.tzinfo is None else value.astimezone(INDIAN_MARKET_TIMEZONE)
        return localized.date()
    if isinstance(value, dt.date):
        return value
    text = str(value).strip()
    try:
        parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
        localized = parsed.replace(tzinfo=INDIAN_MARKET_TIMEZONE) if parsed.tzinfo is None else parsed.astimezone(INDIAN_MARKET_TIMEZONE)
        return localized.date()
    except ValueError:
        try:
            return dt.date.fromisoformat(text[:10])
        except ValueError:
            return None


def has_completed_market_bar(regime_info: Dict[str, Any], expected_date: dt.date) -> bool:
    return normalize_market_date(regime_info.get("data_as_of")) == expected_date


def execute_eod_pipeline(analysis_date: Optional[dt.date] = None, source: str = "AUTOMATED_EOD", dependencies: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Run the existing production screen/allocation once, then persist its exact decision payload."""
    deps = dependencies or {}
    market = deps.get("market_data") or MarketDataProvider()
    universe = deps.get("universe") or UniverseProvider()
    signals = deps.get("signals") or SignalEngine()
    execution = deps.get("execution") or ExecutionAdapter()
    allocator = deps.get("allocator") or PortfolioAllocationEngine()
    event_service = deps.get("event_service") or EventIntelligenceService()
    regime_cache: Dict[dt.date, Dict[str, Any]] = {}
    market_date_diagnostics = []
    if analysis_date is None:
        def completed_bar_lookup(candidate_date: dt.date) -> bool:
            regime_cache[candidate_date] = market.get_index_regime(as_of_date=candidate_date.isoformat())
            completed = has_completed_market_bar(regime_cache[candidate_date], candidate_date)
            provider_date = normalize_market_date(regime_cache[candidate_date].get("data_as_of"))
            market_date_diagnostics.append({
                "candidate": candidate_date.isoformat(),
                "completed_bar_available": completed,
                "provider_latest_bar_date": provider_date.isoformat() if provider_date else None,
            })
            return completed

        analysis_date = resolve_expected_completed_market_date(
            now=deps.get("now"), completed_bar_lookup=completed_bar_lookup
        )
        if analysis_date is None:
            fallback_date = expected_indian_market_date(now=deps.get("now"))
            return {"status": "NO_COMPLETED_MARKET_BAR", "analysis_date": fallback_date.isoformat(),
                    "run_id": f"EOD-{fallback_date.isoformat()}", "persisted": False,
                    "reason": "No completed daily market bar was found in the bounded session search.",
                    "market_date_diagnostics": market_date_diagnostics}
    if isinstance(analysis_date, str):
        analysis_date = dt.date.fromisoformat(analysis_date)
    started = dt.datetime.now(dt.timezone.utc)
    run_id = f"EOD-{analysis_date.isoformat()}"
    regime = regime_cache.get(analysis_date) or market.get_index_regime(as_of_date=analysis_date.isoformat())
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
        # Freeze the canonical identity before any downstream evidence is
        # computed. Persistence uses this same identity for the opportunity row.
        for decision in decisions:
            decision.setdefault(
                "opportunity_id",
                f"{analysis_date.isoformat()}:{decision.get('symbol')}:{decision.get('strategy')}",
            )
        # Advisory-only HA snapshots are captured at signal time when the
        # scanner supplied a complete causal state. Failure cannot affect the
        # qualified set, allocator, persistence, or paper-trade eligibility.
        diagnostics["ha_snapshot_saved_count"] = 0
        diagnostics["ha_snapshot_failure_count"] = 0
        diagnostics["ha_snapshot_skipped_count"] = 0
        try:
            from historical_analogs_service import HistoricalAnalogService
            analog_service = HistoricalAnalogService()
            for decision in decisions:
                if decision.get("ha_features") and decision.get("ha_stock_percentiles"):
                    try:
                        analog_service.evaluate(decision, persist=True)
                        diagnostics["ha_snapshot_saved_count"] += 1
                    except Exception:
                        diagnostics["ha_snapshot_failure_count"] += 1
                else:
                    diagnostics["ha_snapshot_skipped_count"] += 1
        except Exception:
            diagnostics["ha_snapshot_failure_count"] += sum(
                1 for decision in decisions if decision.get("ha_features") and decision.get("ha_stock_percentiles")
            )
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
                "market_date_diagnostics": market_date_diagnostics,
                "mark_count": mark_result.get("successful_marks", 0), "mark_refresh": {key: mark_result.get(key) for key in (
                    "open_positions", "unique_symbols", "provider_calls", "successful_marks", "failed_marks", "elapsed_seconds")},
                "snapshot_reason": snapshot_reason}
    except Exception as exc:
        return {"status": "FAILED", "analysis_date": analysis_date.isoformat(), "run_id": run_id, "persisted": False,
                "error_summary": type(exc).__name__}
