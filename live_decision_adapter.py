"""Live consumer adapter for frozen Phase A–E display semantics only."""

from collections import defaultdict

from trade_economics_context import get_trade_economics_context


EXECUTABLE_STOP_STRATEGIES = {
    "Donchian Channel Breakout",
    "RS Momentum Breakout",
    "VCP Volatility Contraction Breakout",
}
REFERENCE_TYPES = {
    "Donchian Channel Breakout": "PRIOR_20_COMPLETED_SESSION_LOW",
    "EMA Pullback / Bounce": "PRIOR_5_COMPLETED_SESSION_LOW_PROXY",
    "RS Momentum Breakout": "PRIOR_20_COMPLETED_SESSION_LOW",
    "VCP Volatility Contraction Breakout": "CAUSAL_PRIOR_RANGE_LOW_PROXY",
    "True Connors RSI Mean Reversion": "SETUP_BAR_LOW",
    "True NR7 Volatility Expansion Breakout": "NR7_SETUP_BAR_LOW",
}
LEGACY_DISPLAY_FIELDS = {
    "target_price",
    "reward_per_share",
    "risk_reward_ratio",
    "total_position_reward",
    "trade_quality_label",
    "composite_score",
    "signal_strength",
}


def _allocation(candidate):
    """Translate the existing allocator state; never make an allocation decision."""
    status = str(candidate.get("status", ""))
    if status == "ALLOCATED":
        return "ALLOCATED", "ALLOCATED", "Allocated under the existing frozen portfolio rules."
    if "DUPLICATE" in status:
        return (
            "QUALIFIED_NOT_ALLOCATED_DUPLICATE_POSITION",
            "DUPLICATE_POSITION",
            "Qualified — Not Allocated: Position Already Open.",
        )
    if "CAPITAL CAP" in status:
        return (
            "QUALIFIED_NOT_ALLOCATED_CAPITAL_CAP",
            "CAPITAL_CAP",
            "Qualified — Not Allocated: Capital / Position Capacity.",
        )
    return (
        "QUALIFIED_NOT_ALLOCATED_OTHER_FROZEN_CONSTRAINT",
        "OTHER_FROZEN_CONSTRAINT",
        candidate.get("status_reason")
        or candidate.get("reason_text", "Qualified — Not Allocated under an existing frozen constraint."),
    )


def assemble_live_decisions(candidates):
    """Decorate qualified live candidates; never decide qualification/allocation.

    This payload is intentionally distinct from the causal historical E2 payload.
    Missing causal inputs stay unavailable instead of being imputed for display.
    """
    qualifying = [
        dict(candidate)
        for candidate in candidates
        if candidate.get("is_qualified") and candidate.get("regime", "BULLISH") == "BULLISH"
    ]
    groups = defaultdict(list)
    for candidate in qualifying:
        groups[candidate.get("signal_date") or candidate.get("data_as_of") or "CURRENT"].append(candidate)

    decisions = []
    for _, group in groups.items():
        ordered = sorted(
            group,
            key=lambda candidate: (
                candidate.get("volume_ratio_20") is not None,
                candidate.get("volume_ratio_20") or -1,
            ),
            reverse=True,
        )
        for priority_rank, candidate in enumerate(ordered, 1):
            for field in LEGACY_DISPLAY_FIELDS:
                candidate.pop(field, None)

            strategy = candidate.get("strategy", "NOT_AVAILABLE")
            entry = candidate.get("entry_price")
            atr = candidate.get("atr_20")
            executable_stop_enabled = (
                strategy in EXECUTABLE_STOP_STRATEGIES
                and candidate.get("atr_20_available") is True
                and isinstance(entry, (int, float))
                and isinstance(atr, (int, float))
                and atr > 0
            )
            stop = entry - 2 * atr if executable_stop_enabled else None
            quantity = candidate.get("quantity")
            if not isinstance(quantity, int) or quantity <= 0:
                quantity = max(1, int(100000 / entry)) if executable_stop_enabled else None
            stop_risk_inr = round((entry - stop) * quantity, 2) if executable_stop_enabled else "NOT_AVAILABLE"
            allocation, allocation_code, allocation_reason = _allocation(candidate)
            economics = get_trade_economics_context(
                strategy,
                {
                    "risk_reference": "NOT_AVAILABLE",
                    "executable_stop": round(stop, 2) if executable_stop_enabled else "NO_EXECUTABLE_STOP",
                    "reference_risk_per_share": "NOT_AVAILABLE",
                    "executable_stop_risk_per_share": round(entry - stop, 2)
                    if executable_stop_enabled
                    else "NOT_AVAILABLE",
                },
            )
            candidate.update(
                {
                    "qualification_status": "QUALIFIED",
                    "opportunity_priority_rank": priority_rank,
                    "priority_basis": "VOLUME_RATIO_20_DESC",
                    "allocation_status": allocation,
                    "allocation_reason_code": allocation_code,
                    "allocation_reason_text": allocation_reason,
                    "risk_reference_type": REFERENCE_TYPES.get(strategy, "NOT_AVAILABLE"),
                    "risk_reference_available": False,
                    "risk_reference_value": "NOT_AVAILABLE",
                    "reference_risk_per_share": "NOT_AVAILABLE",
                    "reference_risk_pct_equity": "NOT_AVAILABLE",
                    "executable_stop_enabled": executable_stop_enabled,
                    "initial_executable_stop": round(stop, 2) if executable_stop_enabled else "NOT_AVAILABLE",
                    "executable_risk_per_share": round(entry - stop, 2)
                    if executable_stop_enabled
                    else "NOT_AVAILABLE",
                    "executable_stop_risk_inr": stop_risk_inr,
                    "executable_risk_pct_equity": round(stop_risk_inr / 1_000_000 * 100, 3)
                    if executable_stop_enabled
                    else "NOT_AVAILABLE",
                    "gap_risk_possible": executable_stop_enabled,
                    "reference_heat_before_pct": "NOT_AVAILABLE",
                    "candidate_reference_heat_add_pct": "NOT_AVAILABLE",
                    "reference_heat_after_pct": "NOT_AVAILABLE",
                    "correlation_availability": "NOT_AVAILABLE",
                    "candidate_to_book_max_correlation": "NOT_AVAILABLE",
                    "sector_availability": "AVAILABLE" if candidate.get("sector") else "NOT_AVAILABLE",
                    "entry_timing_availability": "NOT_AVAILABLE",
                    "entry_timing_status": "NOT_AVAILABLE",
                    "event_context_status": candidate.get("event_context", "NOT_AVAILABLE") or "NOT_AVAILABLE",
                    "trade_economics": economics,
                    "target_status": "NOT_AVAILABLE",
                    "descriptive_only": True,
                    "informational_only_fields": [
                        "portfolio heat",
                        "correlation",
                        "sector",
                        "entry timing",
                        "event context",
                        "market risk",
                        "trade economics",
                    ],
                }
            )
            decisions.append(candidate)
    return decisions


def summarize_live_portfolio_risk(open_positions):
    """Report only measured paper-position risk; legacy rows remain uncovered."""
    reference_rows = [
        position for position in open_positions
        if position.get("risk_reference_available") and isinstance(position.get("reference_risk_rupees"), (int, float))
    ]
    executable_rows = [
        position for position in open_positions
        if position.get("executable_stop_enabled") and isinstance(position.get("executable_risk_rupees"), (int, float))
    ]
    reference_heat = round(sum(position["reference_risk_rupees"] for position in reference_rows) / 1_000_000 * 100, 3) if reference_rows else None
    executable_heat = round(sum(position["executable_risk_rupees"] for position in executable_rows) / 1_000_000 * 100, 3) if executable_rows else None
    return {
        "reference_heat_pct": reference_heat if reference_heat is not None else "NOT_AVAILABLE",
        "reference_heat_pct_value": reference_heat,
        "executable_stop_heat_pct": executable_heat if executable_heat is not None else "NOT_AVAILABLE",
        "executable_stop_heat_pct_value": executable_heat,
        "positions_with_reference": len(reference_rows),
        "positions_without_reference": len(open_positions) - len(reference_rows),
        "positions_with_executable_stop": len(executable_rows),
        "semantics": "Reference Heat is broad context; Executable Stop Heat is supplementary; neither is maximum possible loss.",
    }
