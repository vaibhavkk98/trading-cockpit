"""Immutable Recommendation Ledger assembly for LSV-V1."""

from __future__ import annotations

import copy
import datetime as dt
from typing import Any, Iterable, Mapping

import database
from latent_state_vector import (
    LSV_METHOD_HASH, LSV_VERSION, NOT_AVAILABLE, assign_cross_sectional_rs_percentiles,
    empty_vector, reconstruct_from_decision_payload,
)


ENVIRONMENT_KEYS = {
    "trend": ("structural", "trend"),
    "breadth": ("structural", "breadth"),
    "volatility": ("structural", "volatility"),
    "sector_participation": ("structural", "sector_participation"),
    "investor_participation": ("investor_participation", None),
    "cross_asset": ("cross_asset", None),
    "external_event_risk": ("event_risk", None),
}


def _timestamp(value: Any) -> dt.datetime:
    if isinstance(value, dt.datetime):
        return value if value.tzinfo else value.replace(tzinfo=dt.timezone.utc)
    if value:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)
    return dt.datetime.now(dt.timezone.utc)


def _environment(bundle: Mapping[str, Any]) -> tuple[dict[str, str], list[str]]:
    states = {}; missing = []
    for output, (section, child) in ENVIRONMENT_KEYS.items():
        payload = bundle.get(section) if isinstance(bundle, Mapping) else None
        value = (payload.get(child) if child and isinstance(payload, Mapping) else payload) or {}
        state = value.get("state") if isinstance(value, Mapping) else None
        states[output] = str(state or NOT_AVAILABLE)
        if states[output] == NOT_AVAILABLE:
            missing.append(f"environment.{output}")
    return states, missing


def _context_metadata(bundle: Mapping[str, Any]) -> tuple[dict[str, Any], list[Any]]:
    timestamps = {}; provenance = []
    for key, payload in bundle.items():
        if not isinstance(payload, Mapping):
            timestamps[key] = NOT_AVAILABLE
            continue
        timestamps[key] = payload.get("as_of_timestamp") or payload.get("observation_date") or NOT_AVAILABLE
        source = payload.get("provenance") or payload.get("source_diagnostics") or []
        provenance.append({"family": "environment", "pillar": key, "source": source})
    return timestamps, provenance


def capture_qualified_recommendations(
    decisions: Iterable[dict[str, Any]], recommendation_timestamp: Any,
    decision_contract_version: str, *, assign_percentiles: bool = True,
) -> dict[str, int]:
    """Freeze every qualified decision without changing decision semantics."""
    decisions = list(decisions)
    if assign_percentiles:
        assign_cross_sectional_rs_percentiles(decisions)
    saved = idempotent = failed = 0
    cutoff = _timestamp(recommendation_timestamp)
    try:
        from historical_analogs_service import METHODOLOGY_HASH as HA_HASH
    except Exception:
        HA_HASH = NOT_AVAILABLE
    for decision in decisions:
        try:
            signal_date = str(decision.get("signal_date") or decision.get("data_as_of"))[:10]
            vector = copy.deepcopy(decision.get("lsv_v1") or empty_vector(signal_date))
            vector.setdefault("contract_version", LSV_VERSION)
            vector.setdefault("methodology_hash", LSV_METHOD_HASH)
            bundle = database.load_market_context_bundle_as_of(signal_date, cutoff)
            environment, environment_missing = _environment(bundle)
            vector["environment"] = environment
            vector["missingness"] = sorted(set((vector.get("missingness") or []) + environment_missing))
            context_timestamps, context_provenance = _context_metadata(bundle)
            opportunity_id = str(decision.get("opportunity_id") or "")
            ha = database.load_historical_analog_snapshot(opportunity_id, signal_date, HA_HASH) if HA_HASH != NOT_AVAILABLE else None
            ha_summary = ha or {"evidence_quality": NOT_AVAILABLE, "methodology_hash": HA_HASH}
            snapshot = {
                "opportunity_id": opportunity_id, "signal_date": signal_date,
                "signal_timestamp": cutoff, "symbol": decision.get("symbol"),
                "strategy": decision.get("strategy"),
                "reference_price": decision.get("entry_price"),
                "allocator_status": decision.get("allocation_status"),
                "opportunity_rank": decision.get("opportunity_priority_rank"),
                "lsv_v1": vector, "historical_analog": ha_summary,
                "market_context": environment,
                "methodologies": {
                    "lsv": LSV_METHOD_HASH, "decision_contract": decision_contract_version,
                    "historical_analogs": HA_HASH,
                    "market_context": {key: (value or {}).get("methodology_version", NOT_AVAILABLE) if isinstance(value, Mapping) else NOT_AVAILABLE for key, value in bundle.items()},
                },
                "source_timestamps": {"price_state": vector.get("source_timestamp") or NOT_AVAILABLE, **context_timestamps},
                "missingness": vector["missingness"],
                "provenance": (vector.get("provenance") or []) + context_provenance,
            }
            result = database.persist_recommendation_snapshot(snapshot)
            saved += int(result["saved"]); idempotent += int(not result["saved"])
        except Exception:
            failed += 1
    return {"saved": saved, "idempotent": idempotent, "failed": failed, "total": len(decisions)}


def backfill_canonical_history() -> dict[str, Any]:
    """Backfill only fields already frozen in canonical opportunity payloads."""
    rows = database.load_canonical_opportunity_history()
    reconstructed = []
    for row in rows:
        signal_date = str(row.get("signal_date") or row.get("data_as_of"))[:10]
        copy_row = dict(row)
        copy_row["lsv_v1"] = row.get("lsv_v1") or reconstruct_from_decision_payload(row, signal_date)
        reconstructed.append(copy_row)
    assign_cross_sectional_rs_percentiles(reconstructed)
    result = {"saved": 0, "idempotent": 0, "failed": 0, "total": 0}
    for row in reconstructed:
        timestamp = row.get("canonical_created_at") or f"{str(row.get('signal_date'))[:10]}T16:07:00+05:30"
        item = capture_qualified_recommendations(
            [row], timestamp, "HISTORICAL_CANONICAL_PAYLOAD_BACKFILL", assign_percentiles=False
        )
        for key in result:
            result[key] += item[key]
    result["source_rows"] = len(rows)
    result["coverage_mode"] = "CANONICAL_PAYLOAD_ONLY"
    result["ledger_coverage"] = database.recommendation_ledger_coverage()
    return result
