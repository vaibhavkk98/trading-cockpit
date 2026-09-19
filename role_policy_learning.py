"""Systematic Engine V1E: prospective, observational policy-learning ledger.

This module is deliberately downstream of every trading decision.  It reads
immutable/current policy records, freezes the causal state that was available,
and links outcomes only after ROLE-D1 matures them.  Nothing here is imported by
the decision engines and every EOD call is safe to isolate as non-blocking.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from collections import Counter, defaultdict
from typing import Any, Iterable

from database import (
    AutoPaperAccount, AutoPaperDecision, AutoPaperManifest, AutoPaperOpportunityMetadata,
    AutoPaperOrder, AutoPaperPortfolioSnapshot, AutoPaperTrade, DynamicExposureDecisionSnapshot,
    DynamicExposureTelemetry, EdgeCaptureMatch, OpportunitySelectionCandidateSnapshot,
    PolicyComparisonSnapshot, PolicyDecisionEpisode, PolicyMatchGroup, PolicyMatureOutcomeLink,
    PortfolioRiskDecisionSnapshot,
    PortfolioRiskMatch, RecommendationLedger, RoleOutcomeHorizon, RoleOutcomeObservation,
    RolePolicyActivation, RolePolicyHealth, RolePolicyReviewGateSnapshot, SessionLocal, init_db,
)


METHODOLOGY_VERSION = "ROLE_POLICY_LEARNING_V1E"
COMPARISON_VERSION = "ROLE_POLICY_COMPARISON_V1"
ACTIVATION_ID = "ROLE_POLICY_LEARNING_V1E_PROSPECTIVE"
NORMALIZED_ACTIONS = {
    "ENTER", "DEFER", "REJECT", "HOLD", "EXIT_TIME", "EXIT_CATASTROPHE",
    "EXIT_THESIS", "EXPIRE",
}
REVIEW_GATE = {
    "mature_decision_episodes": 100,
    "direct_matched_comparisons": 60,
    "signal_dates": 40,
    "strategies": 3,
    "policy_families": 3,
    "calendar_days": 90,
}


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _parse(value: Any, default: Any = None) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value) if value else ({} if default is None else default)
    except (TypeError, ValueError):
        return {} if default is None else default


def _hash(value: Any) -> str:
    return hashlib.sha256(_json(value).encode()).hexdigest()


EPISODE_SCHEMA = {
    "version": METHODOLOGY_VERSION,
    "identity": ["source_decision_id", "methodology_hash"],
    "state_groups": ["opportunity_state", "portfolio_state", "market_state", "action_state"],
    "actions": sorted(NORMALIZED_ACTIONS),
    "counterfactual_classes": [
        "OBSERVED_COUNTERFACTUAL", "OUTCOME_ONLY_COUNTERFACTUAL", "UNSUPPORTED",
    ],
    "evidence_classes": [
        "DIRECT_MATCH", "PARTIAL_MATCH", "OUTCOME_COUNTERFACTUAL", "INSUFFICIENT", "NOT_MATURE",
    ],
    "authority": {"trading": False, "ranking": False, "sizing": False, "exits": False},
}
SCHEMA_HASH = _hash(EPISODE_SCHEMA)
METHODOLOGY_HASH = _hash({
    "version": METHODOLOGY_VERSION,
    "schema_hash": SCHEMA_HASH,
    "review_gate": REVIEW_GATE,
    "prospective_only": True,
    "observed_feasible_alternatives_only": True,
})


PRIMARY_POLICY = {
    "BASELINE_C3": ("BASELINE", "BASELINE"),
    "SHADOW_C0": ("BASELINE_SHADOW", "C0"),
    "SHADOW_D1": ("BASELINE_SHADOW", "D1_LEGACY"),
    "SHADOW_CATASTROPHE": ("BASELINE_SHADOW", "CATASTROPHE"),
    "SHADOW_ROLLING": ("EDGE_CAPTURE", "E0"),
    "SHADOW_EDGE_H20": ("EDGE_CAPTURE", "E1"),
    "SHADOW_EDGE_H20_CATASTROPHE": ("EDGE_CAPTURE", "E2"),
    "SHADOW_EDGE_H20_THESIS": ("EDGE_CAPTURE", "E3"),
    "SHADOW_RISK_BUDGET": ("PORTFOLIO_RISK", "B1"),
    "SHADOW_RISK_DIVERSIFIED": ("PORTFOLIO_RISK", "B2"),
    "SHADOW_SELECT_QUALITY": ("SELECTION", "C1"),
    "SHADOW_SELECT_RISK_ADJ": ("SELECTION", "C2"),
    "SHADOW_EXPOSURE_PORTFOLIO": ("EXPOSURE", "D1"),
    "SHADOW_EXPOSURE_COMBINED": ("EXPOSURE", "D2"),
}

# A control account can be the explicitly frozen comparator in a later phase.
MATCH_POLICY = {
    "EXIT": {
        "SHADOW_ROLLING": "E0", "SHADOW_EDGE_H20": "E1",
        "SHADOW_EDGE_H20_CATASTROPHE": "E2", "SHADOW_EDGE_H20_THESIS": "E3",
    },
    "RISK": {
        "SHADOW_ROLLING": "B0", "SHADOW_RISK_BUDGET": "B1",
        "SHADOW_RISK_DIVERSIFIED": "B2",
    },
    "SELECTION": {
        "SHADOW_RISK_BUDGET": "C0", "SHADOW_SELECT_QUALITY": "C1",
        "SHADOW_SELECT_RISK_ADJ": "C2",
    },
    "EXPOSURE": {
        "SHADOW_RISK_BUDGET": "D0", "SHADOW_EXPOSURE_PORTFOLIO": "D1",
        "SHADOW_EXPOSURE_COMBINED": "D2",
    },
}


def normalize_action(action: str, reason_code: str) -> str:
    action, reason = str(action or "").upper(), str(reason_code or "").upper()
    if action == "QUEUE":
        return "DEFER"
    if action == "EXIT":
        if "CATASTROPHE" in reason:
            return "EXIT_CATASTROPHE"
        if "THESIS" in reason:
            return "EXIT_THESIS"
        return "EXIT_TIME"
    mapped = {"ENTER": "ENTER", "REJECT": "REJECT", "HOLD": "HOLD", "EXPIRE": "EXPIRE"}.get(action)
    if not mapped:
        raise ValueError(f"Unsupported policy action: {action}")
    return mapped


def _first(mapping: Any, names: Iterable[str], default: Any = "NOT_AVAILABLE") -> Any:
    """Find a causal field in nested persisted payloads without changing its value."""
    if isinstance(mapping, dict):
        for name in names:
            if name in mapping and mapping[name] is not None:
                return mapping[name]
        for value in mapping.values():
            found = _first(value, names, default=None)
            if found is not None:
                return found
    elif isinstance(mapping, list):
        for value in mapping:
            found = _first(value, names, default=None)
            if found is not None:
                return found
    return default


def _aware(value: dt.datetime) -> dt.datetime:
    return value if value.tzinfo else value.replace(tzinfo=dt.timezone.utc)


def ensure_activation(market_date: dt.date, now: dt.datetime | None = None) -> dict[str, Any]:
    """Freeze the boundary now; decisions already stored before it are never backfilled."""
    init_db()
    timestamp = _aware(now or dt.datetime.now(dt.timezone.utc))
    with SessionLocal() as session:
        row = session.get(RolePolicyActivation, ACTIVATION_ID)
        if row:
            return {"created": False, "timestamp": row.activation_timestamp, "market_date": row.activation_market_date}
        payload = {
            "activation_rule": "SOURCE_DECISION_CREATED_STRICTLY_AFTER_ACTIVATION",
            "historical_backfill": False,
            "decision_authority": False,
            "schema_hash": SCHEMA_HASH,
        }
        row = RolePolicyActivation(
            activation_id=ACTIVATION_ID, status="ACTIVE", activation_timestamp=timestamp,
            activation_market_date=market_date, methodology_version=METHODOLOGY_VERSION,
            methodology_hash=METHODOLOGY_HASH, schema_hash=SCHEMA_HASH,
            provenance="NEXT_CAUSALLY_ELIGIBLE_EVENT_AFTER_DEPLOYMENT", payload=_json(payload),
            payload_hash=_hash(payload),
        )
        session.add(row); session.commit()
        return {"created": True, "timestamp": timestamp, "market_date": market_date}


def _recommendation(session, opportunity_id: str):
    return (session.query(RecommendationLedger)
            .filter(RecommendationLedger.opportunity_id == opportunity_id)
            .order_by(RecommendationLedger.created_at.desc()).first())


def _phase_snapshot(session, decision: AutoPaperDecision) -> tuple[dict[str, Any], str]:
    filters = {"account_id": decision.account_id, "opportunity_id": decision.opportunity_id,
               "market_date": decision.market_date}
    for model, label in (
        (DynamicExposureDecisionSnapshot, "DYNAMIC_EXPOSURE_DECISION"),
        (OpportunitySelectionCandidateSnapshot, "OPPORTUNITY_SELECTION_DECISION"),
        (PortfolioRiskDecisionSnapshot, "PORTFOLIO_RISK_DECISION"),
    ):
        row = session.query(model).filter_by(**filters).order_by(model.created_at.desc()).first()
        if row:
            return _parse(row.payload), label
    row = (session.query(AutoPaperPortfolioSnapshot)
           .filter_by(account_id=decision.account_id, market_date=decision.market_date)
           .order_by(AutoPaperPortfolioSnapshot.created_at.desc()).first())
    if row:
        payload = _parse(row.payload)
        payload.update({"nav": row.nav, "cash": row.cash, "open_positions": row.open_positions})
        return payload, "END_OF_CYCLE_CAUSAL_SNAPSHOT"
    return {}, "NOT_AVAILABLE"


def _market_snapshot(session, decision: AutoPaperDecision, phase_payload: dict[str, Any]) -> tuple[dict[str, Any], str]:
    candidate = phase_payload
    source = "DECISION_SNAPSHOT"
    if _first(candidate, ("benchmark_rv20", "rv20", "exposure_multiplier"), None) is None:
        row = (session.query(DynamicExposureTelemetry)
               .filter_by(market_date=decision.market_date)
               .order_by(DynamicExposureTelemetry.created_at.desc()).first())
        candidate = _parse(row.payload) if row else {}
        source = "DATE_LEVEL_DYNAMIC_EXPOSURE_TELEMETRY" if row else "NOT_AVAILABLE"
    return {
        "nifty500_trend_state": _first(candidate, ("trend_state", "nifty500_trend_state", "benchmark_trend")),
        "benchmark_rv20": _first(candidate, ("benchmark_rv20", "rv20", "market_rv20")),
        "benchmark_rv20_percentile": _first(candidate, ("benchmark_rv20_percentile", "rv20_percentile")),
        "phase_d_exposure_multiplier": _first(candidate, ("exposure_multiplier", "dynamic_exposure_multiplier")),
        "market_data_coverage": _first(candidate, ("market_data_coverage", "coverage")),
        "source": source,
        "as_of_date": decision.market_date.isoformat(),
    }, source


def _episode_payload(session, decision: AutoPaperDecision) -> tuple[dict[str, Any], dict[str, str]]:
    source = _parse(decision.payload)
    recommendation = _recommendation(session, decision.opportunity_id)
    metadata = session.get(AutoPaperOpportunityMetadata, decision.opportunity_id)
    phase_payload, portfolio_source = _phase_snapshot(session, decision)
    market_state, market_source = _market_snapshot(session, decision, phase_payload)
    account = session.get(AutoPaperAccount, decision.account_id)
    manifest = session.get(AutoPaperManifest, account.methodology_hash) if account else None
    policy_family, policy_code = PRIMARY_POLICY.get(decision.account_id, ("UNMAPPED", decision.account_id))
    vector = _parse(recommendation.vector_payload) if recommendation else {}
    market_context = _parse(recommendation.market_context_payload) if recommendation else {}
    signal_date = recommendation.signal_date if recommendation else dt.date.fromisoformat(
        str(source.get("signal_date") or decision.market_date)[:10]
    )
    opportunity_state = {
        "opportunity_id": decision.opportunity_id,
        "symbol": recommendation.symbol if recommendation else _first(source, ("symbol",)),
        "security_id": _first(source, ("security_id",), _first(vector, ("security_id",))),
        "signal_date": signal_date.isoformat(),
        "strategy": recommendation.strategy if recommendation else _first(source, ("strategy",)),
        "qualification_state": _first(source, ("qualification_state", "qualification_status"), "QUALIFIED_PROSPECTIVE"),
        "reference_price": recommendation.reference_price if recommendation else _first(source, ("reference_price", "entry_price")),
        "atr_pct": _first(vector, ("atr_pct", "atr_percent")),
        "rv_20d": _first(vector, ("realized_volatility_20d", "rv_20d", "rv20")),
        "sector": metadata.sector if metadata else _first(source, ("sector",)),
        "signal_cohort": _first(source, ("signal_cohort", "cohort")),
        "production_native_features": vector,
        "path_risk": _first(vector, ("path_risk", "path_risk_state")),
        "recommendation_snapshot_hash": recommendation.snapshot_hash if recommendation else "NOT_AVAILABLE",
        "snapshot_coverage": "COMPLETE" if recommendation else "MISSING_RECOMMENDATION_SNAPSHOT",
    }
    portfolio_state = {
        "account_id": decision.account_id,
        "nav": _first(phase_payload, ("nav", "portfolio_nav")),
        "cash": _first(phase_payload, ("cash", "available_cash")),
        "exposure": _first(phase_payload, ("exposure", "gross_exposure", "invested_pct")),
        "open_positions": _first(phase_payload, ("open_positions", "position_count")),
        "available_admissions": _first(phase_payload, ("available_admissions", "remaining_slots", "capacity")),
        "portfolio_heat": _first(phase_payload, ("portfolio_heat", "portfolio_heat_pct")),
        "remaining_heat": _first(phase_payload, ("remaining_heat", "remaining_heat_pct")),
        "drawdown": _first(phase_payload, ("drawdown", "drawdown_pct", "portfolio_drawdown_pct")),
        "sector_concentration": _first(phase_payload, ("sector_concentration", "largest_sector_share")),
        "date_concentration": _first(phase_payload, ("date_concentration", "largest_signal_date_share")),
        "correlation": _first(phase_payload, ("correlation", "weighted_correlation")),
        "position_age_distribution": _first(phase_payload, ("position_age_distribution", "age_distribution")),
        "snapshot_timing": portfolio_source,
        "as_of_date": decision.market_date.isoformat(),
    }
    order = (session.query(AutoPaperOrder).filter_by(
        account_id=decision.account_id, opportunity_id=decision.opportunity_id)
        .order_by(AutoPaperOrder.created_at.desc()).first())
    action = normalize_action(decision.action, decision.reason_code)
    action_state = {
        "action": action, "raw_action": decision.action, "reason_code": decision.reason_code,
        "rank": _first(source, ("rank", "selection_rank")),
        "requested_allocation": order.requested_capital if order else _first(source, ("requested_allocation", "requested_capital")),
        "actual_allocation": ((order.quantity or 0) * (order.fill_price or 0)) if order and order.fill_price else _first(source, ("actual_allocation",)),
        "risk_budget": _first(phase_payload, ("risk_budget", "risk_budget_inr", "target_risk")),
        "fill_status": order.status if order else "NOT_APPLICABLE",
        "fill_price": order.fill_price if order else None,
        "order_id": order.order_id if order else None,
        "decision_authority": False,
    }
    provenance = {
        "origin": "PROSPECTIVE", "source_decision_id": decision.decision_id,
        "source_decision_hash": decision.payload_hash, "portfolio_state_source": portfolio_source,
        "market_state_source": market_source, "recommendation_available": bool(recommendation),
        "future_or_outcome_fields_read": False,
    }
    identity = {
        "policy_family": policy_family, "policy_code": policy_code,
        "policy_version": manifest.version if manifest else (account.methodology_hash if account else "NOT_AVAILABLE"),
        "methodology_hash": account.methodology_hash if account else "NOT_AVAILABLE",
        "config_hash": manifest.config_hash if manifest else "NOT_AVAILABLE",
        "signal_date": signal_date,
    }
    payload = {
        "opportunity_state": opportunity_state, "portfolio_state": portfolio_state,
        "market_state": market_state, "action_state": action_state,
        "source_provenance": provenance,
    }
    return payload, identity


def _mature_10d(session, opportunity_id: str) -> dict[str, Any] | None:
    row = (session.query(RoleOutcomeHorizon)
           .join(RoleOutcomeObservation, RoleOutcomeObservation.id == RoleOutcomeHorizon.observation_id)
           .filter(RoleOutcomeObservation.opportunity_id == opportunity_id,
                   RoleOutcomeHorizon.horizon_sessions == 10).first())
    return _parse(row.payload) if row else None


def _source_match_count(session, family: str, opportunity_id: str, signal_date: dt.date) -> int:
    if family == "EXIT":
        return session.query(EdgeCaptureMatch).filter_by(opportunity_id=opportunity_id, signal_date=signal_date).count()
    if family == "RISK":
        return session.query(PortfolioRiskMatch).filter_by(opportunity_id=opportunity_id, signal_date=signal_date).count()
    if family == "SELECTION":
        return session.query(OpportunitySelectionCandidateSnapshot).filter_by(
            opportunity_id=opportunity_id, signal_date=signal_date).count()
    if family == "EXPOSURE":
        return session.query(DynamicExposureDecisionSnapshot).filter_by(
            opportunity_id=opportunity_id).count()
    return 0


def _reconcile_matches(session) -> dict[str, int]:
    episodes = session.query(PolicyDecisionEpisode).all()
    by_key: dict[tuple[str, str, dt.date], list[tuple[PolicyDecisionEpisode, str]]] = defaultdict(list)
    for episode in episodes:
        for family, accounts in MATCH_POLICY.items():
            code = accounts.get(episode.account_id)
            relevant_actions = ({"ENTER", "EXIT_TIME", "EXIT_CATASTROPHE", "EXIT_THESIS"}
                                if family == "EXIT" else {"ENTER", "DEFER", "REJECT", "EXPIRE"})
            if code and episode.normalized_action in relevant_actions:
                by_key[(family, episode.opportunity_id, episode.signal_date)].append((episode, code))
    counts = Counter()
    for (family, opportunity_id, signal_date), values in by_key.items():
        account_members: dict[str, dict[str, Any]] = {}
        for episode, code in values:
            member = account_members.setdefault(episode.account_id, {"policy_code": code, "episode_ids": [], "actions": []})
            member["episode_ids"].append(episode.episode_id)
            member["actions"].append(episode.normalized_action)
        mature = _mature_10d(session, opportunity_id)
        source_matches = _source_match_count(session, family, opportunity_id, signal_date)
        observed_policies = len(account_members)
        expected = len(MATCH_POLICY[family])
        if not mature:
            evidence, counterfactual, status = "NOT_MATURE", "UNSUPPORTED", "AWAITING_10D"
        elif observed_policies >= 2 and source_matches >= 2:
            evidence, counterfactual, status = "DIRECT_MATCH", "OBSERVED_COUNTERFACTUAL", "MATURE_COMPARABLE"
        elif observed_policies >= 2:
            evidence, counterfactual, status = "PARTIAL_MATCH", "OBSERVED_COUNTERFACTUAL", "SOURCE_MATCH_INCOMPLETE"
        elif observed_policies == 1:
            evidence, counterfactual, status = "OUTCOME_COUNTERFACTUAL", "OUTCOME_ONLY_COUNTERFACTUAL", "MATURE_OUTCOME_ONLY"
        else:
            evidence, counterfactual, status = "INSUFFICIENT", "UNSUPPORTED", "INSUFFICIENT"
        members = {key: account_members[key] for key in sorted(account_members)}
        consistency = {
            "observed_policy_count": observed_policies, "expected_policy_count": expected,
            "source_match_legs": source_matches, "same_opportunity": True,
            "same_signal_date": True, "material_divergence_checked": source_matches >= 2,
        }
        group_id = _hash([METHODOLOGY_HASH, family, opportunity_id, signal_date.isoformat()])
        body_hash = _hash({"members": members, "consistency": consistency, "evidence": evidence,
                           "counterfactual": counterfactual, "status": status})
        row = session.get(PolicyMatchGroup, group_id)
        if row:
            row.evidence_class, row.counterfactual_class, row.comparison_status = evidence, counterfactual, status
            row.member_payload, row.consistency_payload, row.payload_hash = _json(members), _json(consistency), body_hash
        else:
            session.add(PolicyMatchGroup(
                group_id=group_id, policy_family=family, opportunity_id=opportunity_id,
                signal_date=signal_date, evidence_class=evidence, counterfactual_class=counterfactual,
                comparison_status=status, member_payload=_json(members), consistency_payload=_json(consistency),
                payload_hash=body_hash,
            ))
        counts["groups"] += 1
        counts[evidence] += 1
        trades = session.query(AutoPaperTrade).filter_by(opportunity_id=opportunity_id).all()
        observed = []
        for trade in trades:
            code = MATCH_POLICY[family].get(trade.account_id)
            if code and trade.account_id in account_members:
                observed.append({
                    "account_id": trade.account_id, "policy_code": code,
                    "realized_return_pct": trade.realized_return_pct, "net_pnl": trade.net_pnl,
                    "holding_sessions": trade.holding_sessions, "entry_date": trade.entry_date.isoformat(),
                    "exit_date": trade.exit_date.isoformat(),
                })
        entered_accounts = {
            account for account, member in account_members.items() if "ENTER" in member["actions"]
        }
        traded_accounts = {item["account_id"] for item in observed}
        comparison_ready = (
            evidence == "DIRECT_MATCH" and observed_policies == expected and
            (not entered_accounts or entered_accounts.issubset(traded_accounts))
        )
        if not comparison_ready:
            continue
        best = max((x["realized_return_pct"] for x in observed), default=None)
        for item in observed:
            item["decision_regret_pct"] = item["realized_return_pct"] - best if best is not None else None
        comparison = {
            "family": family, "opportunity_id": opportunity_id,
            "canonical_10d_outcome": mature, "observed_feasible_alternatives": observed,
            "unsupported_actions": sorted(set(["ENTER_REDUCED", "ENTER_FULL", "REJECT", "DEFER", "HOLD", "EXIT"])
                                          - {a for m in members.values() for a in m["actions"]}),
            "decision_regret_definition": "chosen_observed_value_minus_best_observed_feasible_alternative",
            "descriptive_only": True, "decision_authority": False,
        }
        comparison_id = _hash([group_id, 10, COMPARISON_VERSION])
        payload_hash = _hash(comparison)
        existing = session.get(PolicyComparisonSnapshot, comparison_id)
        if existing and existing.payload_hash != payload_hash:
            # A comparison is created only once its source match is mature. Later
            # state must never rewrite the observed evidence.
            counts["comparison_conflicts"] += 1
        elif not existing:
            session.add(PolicyComparisonSnapshot(
                comparison_id=comparison_id, group_id=group_id, policy_family=family,
                horizon_sessions=10, comparison_version=COMPARISON_VERSION,
                evidence_class=evidence, payload=_json(comparison), payload_hash=payload_hash,
            ))
            counts["comparisons_created"] += 1
    return dict(counts)


def _link_mature_outcomes(session) -> dict[str, int]:
    counts = Counter()
    episodes = session.query(PolicyDecisionEpisode).all()
    for episode in episodes:
        horizons = (session.query(RoleOutcomeHorizon)
                    .join(RoleOutcomeObservation, RoleOutcomeObservation.id == RoleOutcomeHorizon.observation_id)
                    .filter(RoleOutcomeObservation.opportunity_id == episode.opportunity_id,
                            RoleOutcomeHorizon.horizon_sessions.in_((5, 10, 20))).all())
        for horizon in horizons:
            link_id = _hash([episode.episode_id, horizon.horizon_sessions])
            payload = _parse(horizon.payload)
            link_payload = {
                "episode_id": episode.episode_id, "opportunity_id": episode.opportunity_id,
                "horizon_sessions": horizon.horizon_sessions,
                "observation_date": horizon.observation_date.isoformat(), "outcome": payload,
                "source_outcome_hash": horizon.payload_hash,
            }
            payload_hash = _hash(link_payload)
            existing = session.get(PolicyMatureOutcomeLink, link_id)
            if existing:
                if existing.payload_hash != payload_hash:
                    counts["conflicts"] += 1
                else:
                    counts["existing"] += 1
                continue
            session.add(PolicyMatureOutcomeLink(
                link_id=link_id, episode_id=episode.episode_id, opportunity_id=episode.opportunity_id,
                horizon_sessions=horizon.horizon_sessions, observation_date=horizon.observation_date,
                payload=_json(link_payload), payload_hash=payload_hash,
            ))
            counts["created"] += 1
    return dict(counts)


def _review_gate_snapshot(session) -> dict[str, Any]:
    episodes = session.query(PolicyDecisionEpisode).all()
    mature_ids = {row[0] for row in session.query(PolicyMatureOutcomeLink.episode_id).filter_by(
        horizon_sessions=20).all()}
    mature = [episode for episode in episodes if episode.episode_id in mature_ids]
    direct = session.query(PolicyMatchGroup).filter_by(evidence_class="DIRECT_MATCH").count()
    dates = {episode.signal_date for episode in mature}
    strategies = {_parse(episode.opportunity_state).get("strategy") for episode in mature} - {None, "NOT_AVAILABLE"}
    families = {episode.policy_family for episode in mature}
    calendar_days = ((max((e.decision_date for e in episodes), default=None) -
                      min((e.decision_date for e in episodes), default=None)).days + 1) if episodes else 0
    values = {
        "mature_decision_episodes": len(mature), "direct_matched_comparisons": direct,
        "signal_dates": len(dates), "strategies": len(strategies),
        "policy_families": len(families), "calendar_days": calendar_days,
    }
    rows = [{"metric": key, "value": values[key], "required": required,
             "met": values[key] >= required} for key, required in REVIEW_GATE.items()]
    return {"values": values, "rows": rows, "passed": all(row["met"] for row in rows),
            "effect": "OFFLINE_POLICY_RESEARCH_ONLY", "automatic_promotion": False}


def ingest_policy_learning(market_date: dt.date, run_id: str,
                           completed_at: dt.datetime | None = None) -> dict[str, Any]:
    """Non-authoritative EOD observer. First call only freezes activation."""
    init_db()
    completed = _aware(completed_at or dt.datetime.now(dt.timezone.utc))
    activation = ensure_activation(market_date)
    result: dict[str, Any] = {
        "status": "HEALTHY", "active": True, "decision_authority": False,
        "activation_created": activation["created"], "episodes_created": 0,
        "idempotent_existing": 0, "duplicate_conflicts": 0, "failures": [],
    }
    with SessionLocal() as session:
        boundary = session.get(RolePolicyActivation, ACTIVATION_ID)
        if not boundary:
            raise RuntimeError("ROLE policy activation missing")
        decisions = (session.query(AutoPaperDecision)
                     .filter(AutoPaperDecision.created_at > boundary.activation_timestamp)
                     .order_by(AutoPaperDecision.created_at, AutoPaperDecision.decision_id).all())
        for decision in decisions:
            try:
                payload, identity = _episode_payload(session, decision)
                episode_id = _hash([decision.decision_id, METHODOLOGY_HASH])
                snapshot = {
                    "source_decision_id": decision.decision_id,
                    "decision_date": decision.market_date.isoformat(),
                    "account_id": decision.account_id,
                    "policy_family": identity["policy_family"], "policy_code": identity["policy_code"],
                    **payload,
                }
                snapshot_hash = _hash(snapshot)
                existing = session.get(PolicyDecisionEpisode, episode_id)
                if existing:
                    if existing.snapshot_hash != snapshot_hash:
                        result["duplicate_conflicts"] += 1
                        result["failures"].append(f"IMMUTABILITY_CONFLICT:{decision.decision_id}")
                    else:
                        result["idempotent_existing"] += 1
                    continue
                session.add(PolicyDecisionEpisode(
                    episode_id=episode_id, source_decision_id=decision.decision_id,
                    opportunity_id=decision.opportunity_id, signal_date=identity["signal_date"],
                    decision_date=decision.market_date, decision_timestamp=decision.decision_timestamp,
                    account_id=decision.account_id, policy_family=identity["policy_family"],
                    policy_code=identity["policy_code"], policy_version=identity["policy_version"],
                    normalized_action=payload["action_state"]["action"], reason_code=decision.reason_code,
                    methodology_hash=METHODOLOGY_HASH, config_hash=identity["config_hash"],
                    activation_provenance=boundary.provenance,
                    opportunity_state=_json(payload["opportunity_state"]),
                    portfolio_state=_json(payload["portfolio_state"]), market_state=_json(payload["market_state"]),
                    action_state=_json(payload["action_state"]), source_provenance=_json(payload["source_provenance"]),
                    snapshot_hash=snapshot_hash,
                ))
                result["episodes_created"] += 1
            except Exception as exc:  # one malformed observational row must not block the ledger
                result["failures"].append(f"{decision.decision_id}:{type(exc).__name__}:{str(exc)[:120]}")
        session.flush()
        outcome_links = _link_mature_outcomes(session)
        session.flush()
        match_counts = _reconcile_matches(session)
        session.flush()
        result.update({"match_groups": match_counts.get("groups", 0),
                       "direct_matches": match_counts.get("DIRECT_MATCH", 0),
                       "comparisons_created": match_counts.get("comparisons_created", 0),
                       "mature_outcome_links_created": outcome_links.get("created", 0)})
        policy_coverage = dict(Counter(x[0] for x in session.query(PolicyDecisionEpisode.policy_family).all()))
        result["policy_coverage"] = policy_coverage
        result["outcome_link_status"] = "LINKED_READ_ONLY"
        if (result["failures"] or result["duplicate_conflicts"] or
                match_counts.get("comparison_conflicts") or outcome_links.get("conflicts")):
            result["status"] = "DEGRADED"
        gate = _review_gate_snapshot(session)
        result["review_gate_passed"] = gate["passed"]
        if not session.get(RolePolicyReviewGateSnapshot, run_id):
            session.add(RolePolicyReviewGateSnapshot(
                run_id=run_id, market_date=market_date, gate_passed=gate["passed"],
                payload=_json(gate), payload_hash=_hash(gate),
            ))
        health_payload = dict(result)
        health_hash = _hash(health_payload)
        health = session.get(RolePolicyHealth, run_id)
        if not health:
            session.add(RolePolicyHealth(
                run_id=run_id, market_date=market_date, status=result["status"],
                episodes_created=result["episodes_created"], idempotent_existing=result["idempotent_existing"],
                match_groups=result["match_groups"], direct_matches=result["direct_matches"],
                duplicate_conflicts=result["duplicate_conflicts"], outcome_link_status=result["outcome_link_status"],
                policy_coverage_payload=_json(policy_coverage), failure_reasons_payload=_json(result["failures"]),
                payload=_json(health_payload), payload_hash=health_hash, completed_at=completed,
            ))
        session.commit()
    return result


def _episode_outcome_map(session) -> dict[str, dict[str, dict[str, Any]]]:
    output: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for link in session.query(PolicyMatureOutcomeLink).all():
        link_payload = _parse(link.payload)
        output[link.opportunity_id][f"{link.horizon_sessions}d"] = link_payload.get("outcome") or {}
    return dict(output)


def _phase_value_summaries(episodes: list[PolicyDecisionEpisode],
                           outcomes: dict[str, dict[str, dict[str, Any]]],
                           trades: list[AutoPaperTrade]) -> list[dict[str, Any]]:
    trade_map = {(x.account_id, x.opportunity_id): x for x in trades}
    summaries = []
    for family, account_codes in MATCH_POLICY.items():
        relevant = [e for e in episodes if e.account_id in account_codes]
        entered, not_entered, actual = [], [], []
        heat_returns, capital_days = [], 0.0
        interventions, avoided_downside, suppressed_winners = 0, [], []
        for episode in relevant:
            outcome = (outcomes.get(episode.opportunity_id) or {}).get("10d")
            action = episode.normalized_action
            if outcome and action == "ENTER":
                entered.append(float(outcome.get("close_return_pct") or 0.0))
            elif outcome and action in {"DEFER", "REJECT", "EXPIRE"}:
                value = float(outcome.get("close_return_pct") or 0.0)
                not_entered.append(value)
                (avoided_downside if value < 0 else suppressed_winners).append(value)
            if any(token in episode.reason_code.upper() for token in
                   ("HEAT", "CORRELATION", "SECTOR", "CONCENTRATION", "EXPOSURE")):
                interventions += 1
            trade = trade_map.get((episode.account_id, episode.opportunity_id))
            if trade and action == "ENTER":
                actual.append(float(trade.realized_return_pct))
                allocation = _first(_parse(episode.action_state), ("actual_allocation", "requested_allocation"), 0)
                allocation = float(allocation) if isinstance(allocation, (int, float)) else 0.0
                capital_days += allocation * trade.holding_sessions
                heat = _first(_parse(episode.portfolio_state), ("portfolio_heat",), None)
                if isinstance(heat, (int, float)) and heat:
                    heat_returns.append(float(trade.realized_return_pct) / float(heat))
        mean = lambda values: (sum(values) / len(values)) if values else None
        summaries.append({
            "family": family,
            "entered_mature_n": len(entered), "not_entered_mature_n": len(not_entered),
            "selected_vs_not_entered_lift_pct": (mean(entered) - mean(not_entered)) if entered and not_entered else None,
            "mean_realized_return_pct": mean(actual), "return_per_heat": mean(heat_returns),
            "capital_days": capital_days if actual else None, "risk_or_exposure_interventions": interventions,
            "avoided_downside_pct": -sum(avoided_downside) if avoided_downside else None,
            "suppressed_winner_opportunity_cost_pct": sum(suppressed_winners) if suppressed_winners else None,
            "random_benchmark_comparison": "NOT_AVAILABLE_UNTIL_MATCHED_MATURE_RANDOM_EVIDENCE" if family == "SELECTION" else "NOT_APPLICABLE",
            "attribution_class": "DESCRIPTIVE_ACCOUNTING",
        })
    return summaries


def _portfolio_attribution(episodes: list[PolicyDecisionEpisode], trades: list[AutoPaperTrade]) -> dict[str, list[dict[str, Any]]]:
    episode_lookup = {(e.account_id, e.opportunity_id): e for e in episodes if e.normalized_action == "ENTER"}
    buckets: dict[str, dict[str, float]] = {key: defaultdict(float) for key in
                                           ("security", "strategy", "signal_date", "sector", "policy_intervention")}
    counts: dict[str, Counter] = {key: Counter() for key in buckets}
    for trade in trades:
        episode = episode_lookup.get((trade.account_id, trade.opportunity_id))
        if not episode:
            continue
        opportunity = _parse(episode.opportunity_state)
        keys = {
            "security": opportunity.get("symbol") or trade.symbol,
            "strategy": opportunity.get("strategy") or "NOT_AVAILABLE",
            "signal_date": episode.signal_date.isoformat(),
            "sector": opportunity.get("sector") or "NOT_AVAILABLE",
            "policy_intervention": f"{episode.policy_family}:{episode.policy_code}",
        }
        for family, key in keys.items():
            buckets[family][str(key)] += float(trade.net_pnl)
            counts[family][str(key)] += 1
    return {family: [{"key": key, "net_pnl": value, "trades": counts[family][key],
                      "attribution_class": "DESCRIPTIVE_ACCOUNTING"}
                     for key, value in sorted(values.items())]
            for family, values in buckets.items()}


def load_policy_learning_state() -> dict[str, Any]:
    """Read-only persisted projection for Learning/health surfaces."""
    init_db()
    with SessionLocal() as session:
        activation = session.get(RolePolicyActivation, ACTIVATION_ID)
        episodes = session.query(PolicyDecisionEpisode).order_by(PolicyDecisionEpisode.created_at).all()
        groups = session.query(PolicyMatchGroup).all()
        comparisons = session.query(PolicyComparisonSnapshot).all()
        outcomes = _episode_outcome_map(session)
        health = session.query(RolePolicyHealth).order_by(RolePolicyHealth.created_at.desc()).first()
        mature = [e for e in episodes if "20d" in (outcomes.get(e.opportunity_id) or {})]
        maturity_counts = {f"{horizon}d": sum(
            f"{horizon}d" in (outcomes.get(e.opportunity_id) or {}) for e in episodes
        ) for horizon in (5, 10, 20)}
        evidence_mix = Counter(g.evidence_class for g in groups)
        family_coverage = Counter(e.policy_family for e in episodes)
        signal_dates = {e.signal_date for e in mature}
        strategies = {_parse(e.opportunity_state).get("strategy") for e in mature} - {None, "NOT_AVAILABLE"}
        policy_families = {e.policy_family for e in mature}
        calendar_days = ((max((e.decision_date for e in episodes), default=None) -
                          min((e.decision_date for e in episodes), default=None)).days + 1) if episodes else 0
        direct = sum(1 for g in groups if g.evidence_class == "DIRECT_MATCH")
        trades = session.query(AutoPaperTrade).all()
        gate_values = {
            "mature_decision_episodes": len(mature), "direct_matched_comparisons": direct,
            "signal_dates": len(signal_dates), "strategies": len(strategies),
            "policy_families": len(policy_families), "calendar_days": calendar_days,
        }
        gate_rows = [{"metric": key.replace("_", " ").title(), "value": gate_values[key],
                      "required": minimum, "met": gate_values[key] >= minimum}
                     for key, minimum in REVIEW_GATE.items()]
        phase_rows = []
        for family in ("EXIT", "RISK", "SELECTION", "EXPOSURE"):
            relevant = [g for g in groups if g.policy_family == family]
            phase_rows.append({
                "family": family, "match_groups": len(relevant),
                "direct": sum(g.evidence_class == "DIRECT_MATCH" for g in relevant),
                "partial": sum(g.evidence_class == "PARTIAL_MATCH" for g in relevant),
                "not_mature": sum(g.evidence_class == "NOT_MATURE" for g in relevant),
                "comparisons": sum(c.policy_family == family for c in comparisons),
            })
        regret_rows = []
        for comparison in comparisons:
            payload = _parse(comparison.payload)
            for observed in payload.get("observed_feasible_alternatives", []):
                if observed.get("decision_regret_pct") is not None:
                    regret_rows.append({
                        "family": comparison.policy_family, "opportunity_id": payload.get("opportunity_id"),
                        "policy": observed.get("policy_code"), "realized_return_pct": observed.get("realized_return_pct"),
                        "decision_regret_pct": observed.get("decision_regret_pct"),
                        "evidence": comparison.evidence_class,
                    })
        return {
            "methodology_version": METHODOLOGY_VERSION, "methodology_hash": METHODOLOGY_HASH,
            "schema_hash": SCHEMA_HASH, "decision_authority": False,
            "activation": ({"status": activation.status, "timestamp": activation.activation_timestamp.isoformat(),
                            "market_date": activation.activation_market_date.isoformat(),
                            "provenance": activation.provenance} if activation else None),
            "episode_count": len(episodes), "mature_episode_count": len(mature),
            "horizon_maturity_counts": maturity_counts,
            "match_group_count": len(groups), "direct_match_count": direct,
            "evidence_mix": dict(evidence_mix), "policy_family_coverage": dict(family_coverage),
            "phase_comparisons": phase_rows, "supported_regret": regret_rows,
            "phase_value_summaries": _phase_value_summaries(episodes, outcomes, trades),
            "portfolio_attribution": _portfolio_attribution(episodes, trades),
            "review_gate": {"passed": all(row["met"] for row in gate_rows), "rows": gate_rows,
                            "effect": "OFFLINE_POLICY_RESEARCH_ONLY"},
            "health": ({"status": health.status, "run_id": health.run_id,
                        "outcome_link_status": health.outcome_link_status,
                        "duplicate_conflicts": health.duplicate_conflicts,
                        "failures": _parse(health.failure_reasons_payload, [])} if health else {}),
            "training_contract": {
                "row_unit": "IMMUTABLE_POLICY_DECISION_EPISODE", "origin": "PROSPECTIVE_ONLY",
                "supported_actions": sorted(NORMALIZED_ACTIONS),
                "unsupported_counterfactuals": "EXPLICIT_UNSUPPORTED",
                "features": list(EPISODE_SCHEMA["state_groups"]), "label_status": "NOT_MATURE_UNTIL_ROLE_D1",
            },
        }


def load_episode_training_rows(*, mature_only: bool = False) -> list[dict[str, Any]]:
    """Machine-readable future research rows; never used by live policies."""
    init_db()
    with SessionLocal() as session:
        outcomes = _episode_outcome_map(session)
        rows = []
        for episode in session.query(PolicyDecisionEpisode).order_by(PolicyDecisionEpisode.created_at).all():
            outcome = outcomes.get(episode.opportunity_id) or {}
            fully_mature = "20d" in outcome
            if mature_only and not fully_mature:
                continue
            trade = session.query(AutoPaperTrade).filter_by(
                account_id=episode.account_id, opportunity_id=episode.opportunity_id).first()
            rows.append({
                "episode_id": episode.episode_id, "opportunity_id": episode.opportunity_id,
                "account_id": episode.account_id, "policy_family": episode.policy_family,
                "policy_code": episode.policy_code, "action": episode.normalized_action,
                "decision_timestamp": episode.decision_timestamp.isoformat(),
                "opportunity_state": _parse(episode.opportunity_state),
                "portfolio_state": _parse(episode.portfolio_state), "market_state": _parse(episode.market_state),
                "action_state": _parse(episode.action_state),
                "outcome_status": "MATURE" if fully_mature else "NOT_MATURE",
                "outcome_5d": outcome.get("5d"), "outcome_10d": outcome.get("10d"),
                "outcome_20d": outcome.get("20d"),
                "realized_trade": ({"return_pct": trade.realized_return_pct, "net_pnl": trade.net_pnl,
                                    "costs": _first(_parse(trade.payload), ("costs", "fees")),
                                    "holding_sessions": trade.holding_sessions,
                                    "exit_reason": _first(_parse(trade.payload), ("exit_reason",))} if trade else None),
                "decision_authority": False,
            })
        return rows
