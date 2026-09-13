"""Prospective PB-R2/PB-R3 shadow capture with zero decision authority."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Iterable, Mapping, Protocol

import database
from latent_state_vector import LSV_METHOD_HASH, NOT_AVAILABLE
from pb_r2_frozen_inference import FrozenPB2Inference, MANIFEST_PATH as PB_R2_MANIFEST_PATH

ROOT=Path(__file__).resolve().parent
MANIFEST_PATH=ROOT/"data/production/predictability/pb_p1_manifest.json"
EXPECTED_SCHEMA="PB_P1_MANIFEST_V1"
EXPECTED_PB_R2_HASH="cd82dc5e6a354445506306a1a98c54de8f7287b248e89bf3f6044342d68b8e27"
EXPECTED_PB_R3_HASH="7534d1bc782c12e03bf726dcb45a5cd104693c8263c6446f80785fe6638eaf9c"
HORIZONS=(5,10,20)
SUPPORTED=("mfe","mae","forward_volatility","success_5_before_3","adverse_first")


class FrozenArtifactUnavailableError(RuntimeError):pass


class FrozenInference(Protocol):
    def predict(self, decision: Mapping[str, Any]) -> Mapping[str, Any]:...


def load_manifest(path: Path = MANIFEST_PATH) -> dict[str, Any]:
    payload=json.loads(path.read_text())
    if payload.get("schema_version")!=EXPECTED_SCHEMA:raise ValueError("PB-P1 manifest schema mismatch")
    if payload.get("pb_r2_methodology_hash")!=EXPECTED_PB_R2_HASH:raise ValueError("PB-R2 methodology hash mismatch")
    if payload.get("pb_r3_methodology_hash")!=EXPECTED_PB_R3_HASH:raise ValueError("PB-R3 methodology hash mismatch")
    if payload.get("selected_methodology")!="A_MFE_ADVERSITY_RATIO":raise ValueError("PB-R3 methodology mismatch")
    if payload.get("decision_authority") is not False:raise ValueError("PB-P1 manifest grants decision authority")
    return payload


def load_frozen_inference(manifest: Mapping[str, Any]):
    artifact=manifest.get("inference_artifact") or {};relative=artifact.get("path");expected=artifact.get("sha256")
    if not relative or expected in (None,"",NOT_AVAILABLE):raise FrozenArtifactUnavailableError("PB-R2 frozen inference artifact is unavailable")
    path=ROOT/str(relative)
    if not path.exists():raise FrozenArtifactUnavailableError("PB-R2 frozen inference artifact is missing")
    if hashlib.sha256(path.read_bytes()).hexdigest()!=expected:raise ValueError("PB-R2 frozen inference artifact hash mismatch")
    inference=FrozenPB2Inference.from_manifest(PB_R2_MANIFEST_PATH)
    if inference.metadata["artifact_sha256"]!=expected:raise ValueError("PB-P1/PB-R2 artifact identity mismatch")
    return inference


def asymmetry_value_bucket(mfe_10d: Any, mae_10d: Any, signal_year: int, manifest: Mapping[str, Any]) -> tuple[float|str,str]:
    try:mfe=float(mfe_10d);mae=float(mae_10d);floor=float((manifest.get("annual_mae_floors") or {})[str(signal_year)])
    except (TypeError,ValueError,KeyError):return NOT_AVAILABLE,NOT_AVAILABLE
    if not all(math.isfinite(x) for x in (mfe,mae,floor)) or floor<=0:return NOT_AVAILABLE,NOT_AVAILABLE
    value=mfe/max(abs(mae),floor);bounds=manifest["bucket_boundaries"]
    bucket="UNFAVORABLE" if value<float(bounds["unfavorable_lt"]) else ("FAVORABLE" if value>=float(bounds["favorable_gte"]) else "MIXED")
    return float(value),bucket


def _prediction(outputs: Mapping[str,Any],target: str,horizon: int):
    value=(outputs.get(target) or {}).get(str(horizon),NOT_AVAILABLE)
    try:value=float(value)
    except (TypeError,ValueError):return NOT_AVAILABLE
    return value if math.isfinite(value) else NOT_AVAILABLE


def _snapshot(decision: Mapping[str,Any],outputs: Mapping[str,Any],timestamp: dt.datetime,manifest: Mapping[str,Any],qualification_methodology: str)->dict[str,Any]:
    signal_date=dt.date.fromisoformat(str(decision.get("signal_date") or decision.get("data_as_of"))[:10]);predictions={target:{str(h):_prediction(outputs,target,h) for h in HORIZONS} for target in SUPPORTED}
    value,bucket=asymmetry_value_bucket(predictions["mfe"]["10"],predictions["mae"]["10"],signal_date.year,manifest)
    missing=[f"{target}.{h}d" for target,values in predictions.items() for h,value_at_h in values.items() if value_at_h==NOT_AVAILABLE]
    path_risk=decision.get("path_risk") if isinstance(decision.get("path_risk"),Mapping) else {}
    predictions["path_risk_state"]=path_risk.get("state") or NOT_AVAILABLE
    status="AVAILABLE" if value!=NOT_AVAILABLE and not missing else "NOT_AVAILABLE"
    vector=decision.get("lsv_v1") if isinstance(decision.get("lsv_v1"),Mapping) else {}
    available=sum(_prediction(outputs,target,h)!=NOT_AVAILABLE for target in SUPPORTED for h in HORIZONS)
    return {"opportunity_id":str(decision.get("opportunity_id") or ""),"signal_date":signal_date.isoformat(),"prediction_timestamp":timestamp,"symbol":decision.get("symbol"),"security_id":decision.get("canonical_security_id"),"strategy":decision.get("strategy"),"reference_price":decision.get("entry_price"),"qualification_methodology":qualification_methodology,"lsv_methodology_hash":str(vector.get("methodology_hash") or LSV_METHOD_HASH),"pb_r2_methodology_hash":manifest["pb_r2_methodology_hash"],"pb_r3_methodology_hash":manifest["pb_r3_methodology_hash"],"origin":"PROSPECTIVE","inference_status":status,"predictions":predictions,"asymmetry_value":None if value==NOT_AVAILABLE else value,"asymmetry_bucket":bucket,"evidence":outputs.get("evidence") or {"mfe":"CALIBRATED_LOW_STRENGTH","mae":"WELL_CALIBRATED","forward_volatility":"WELL_CALIBRATED","barriers":"CALIBRATED_LOW_STRENGTH"},"feature_coverage":{"missing":missing,"available":available,"required":len(SUPPORTED)*len(HORIZONS)},"source_timestamps":{"completed_session":signal_date.isoformat(),"prediction_timestamp":timestamp.isoformat()},"provenance":[{"source":"PB-R2 frozen inference artifact","artifact_sha256":manifest["inference_artifact"]["sha256"]},{"source":"PB-R3 frozen methodology","methodology_hash":manifest["pb_r3_methodology_hash"]}]}


def capture_prospective_shadows(decisions: Iterable[Mapping[str,Any]],prediction_timestamp: Any,run_id: str,qualification_methodology: str,inference: FrozenInference|None=None,manifest: Mapping[str,Any]|None=None,persist_health: bool=True)->dict[str,Any]:
    decisions=list(decisions);manifest=dict(manifest or load_manifest());timestamp=prediction_timestamp if isinstance(prediction_timestamp,dt.datetime) else dt.datetime.fromisoformat(str(prediction_timestamp).replace("Z","+00:00"));timestamp=timestamp if timestamp.tzinfo else timestamp.replace(tzinfo=dt.timezone.utc)
    health={"run_id":str(run_id),"status":"HEALTHY","opportunities_examined":len(decisions),"qualified_opportunities":len(decisions),"snapshots_created":0,"idempotent_existing":0,"unavailable_snapshots":0,"failures":0,"failure_reasons":[],"latest_successful_prediction_date":None,"completed_at":dt.datetime.now(dt.timezone.utc).isoformat(),"pb_r3_methodology_hash":manifest.get("pb_r3_methodology_hash") or NOT_AVAILABLE}
    if manifest.get("activation_status")!="ACTIVE" or not manifest.get("activation_date"):
        health.update(status="NOT_ACTIVATED",failure_reasons=[str(manifest.get("blocker") or "PB-P1 is not activated")])
        if persist_health:database.persist_pb_shadow_pipeline_health(health)
        return health
    activation=dt.date.fromisoformat(str(manifest["activation_date"])[:10])
    try:inference=inference or load_frozen_inference(manifest)
    except Exception as exc:
        health.update(status="DEGRADED",failures=1,failure_reasons=[f"{type(exc).__name__}: {str(exc)[:160]}"])
        if persist_health:database.persist_pb_shadow_pipeline_health(health)
        return health
    for decision in decisions:
        try:
            signal_date=dt.date.fromisoformat(str(decision.get("signal_date") or decision.get("data_as_of"))[:10])
            if signal_date<activation:raise ValueError("historical opportunity cannot be marked PROSPECTIVE")
            snapshot=_snapshot(decision,inference.predict(decision),timestamp,manifest,qualification_methodology)
            result=database.persist_pb_asymmetry_shadow(snapshot);health["snapshots_created"]+=int(result["saved"]);health["idempotent_existing"]+=int(not result["saved"]);health["unavailable_snapshots"]+=int(snapshot["inference_status"]!="AVAILABLE")
            if snapshot["inference_status"]=="AVAILABLE":health["latest_successful_prediction_date"]=signal_date.isoformat()
        except Exception as exc:
            health["failures"]+=1;health["failure_reasons"].append(f"{type(exc).__name__}: {str(exc)[:160]}")
    if health["failures"]:health["status"]="DEGRADED"
    elif health["unavailable_snapshots"]:health["status"]="PARTIAL"
    if persist_health:database.persist_pb_shadow_pipeline_health(health)
    return health


def prospective_validation_summary()->dict[str,Any]:
    rows=database.load_pb_shadow_research_rows();bucket_counts={};matured={str(h):0 for h in HORIZONS};metrics={}
    for row in rows:
        bucket_counts[row["asymmetry_bucket"]]=bucket_counts.get(row["asymmetry_bucket"],0)+1
        for h in HORIZONS:
            payload=(row.get("horizons") or {}).get(str(h))
            if payload:
                matured[str(h)]+=1
                if h==10:
                    metrics.setdefault(row["asymmetry_bucket"],[]).append(payload)
    by_bucket={}
    for bucket,items in metrics.items():
        success=[bool((x.get("plus_5_before_minus_3") or {}).get("value")) for x in items];mfe=[x.get("mfe_pct") for x in items];mae=[x.get("mae_pct") for x in items]
        valid=[(float(a),float(b)) for a,b in zip(mfe,mae) if a is not None and b is not None]
        by_bucket[bucket]={"n":len(items),"success_rate":sum(success)/len(success) if success else None,"median_mfe":_median(mfe),"median_mae":_median(mae),"median_reward_adversity":_median([a/max(abs(b),.01) for a,b in valid])}
    return {"prospective_n":len(rows),"bucket_counts":bucket_counts,"matured_counts":matured,"outcomes_10d_by_bucket":by_bucket,"strategy_distribution":_counts(row.get("strategy") for row in rows),"path_risk_distribution":_counts((row.get("predictions") or {}).get("path_risk_state",NOT_AVAILABLE) for row in rows)}


def _median(values):
    clean=sorted(float(x) for x in values if x is not None and math.isfinite(float(x)));n=len(clean)
    return None if not n else (clean[n//2] if n%2 else (clean[n//2-1]+clean[n//2])/2)


def _counts(values):
    result={}
    for value in values:result[str(value)]=result.get(str(value),0)+1
    return result
