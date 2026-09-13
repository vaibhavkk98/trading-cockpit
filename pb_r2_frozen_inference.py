"""Load-only PB-R2 inference from an integrity-checked frozen artifact."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping

import joblib
import numpy as np
import pandas as pd
from scipy.special import expit, logit


ROOT=Path(__file__).resolve().parent
MANIFEST_PATH=ROOT/"data/production/predictability/pb_r2_inference_manifest_v1.json"
ARTIFACT_SCHEMA="PB_R2_FROZEN_INFERENCE_V1"
MANIFEST_SCHEMA="PB_R2_INFERENCE_MANIFEST_V1"
EXPECTED_CONFIG_HASH="cd82dc5e6a354445506306a1a98c54de8f7287b248e89bf3f6044342d68b8e27"
EXPECTED_PB_R3_HASH="7534d1bc782c12e03bf726dcb45a5cd104693c8263c6446f80785fe6638eaf9c"
OUTPUTS=("mfe","mae","forward_volatility","success_5_before_3","adverse_first")
HORIZONS=(5,10,20)
STATES=np.asarray(("LOW","NORMAL","ELEVATED","HIGH"))
NOT_AVAILABLE="NOT_AVAILABLE"


def sha256_file(path: Path)->str:return hashlib.sha256(path.read_bytes()).hexdigest()


def load_manifest(path: Path=MANIFEST_PATH)->dict[str,Any]:
    payload=json.loads(path.read_text())
    if payload.get("schema_version")!=MANIFEST_SCHEMA:raise ValueError("PB-R2 inference manifest schema mismatch")
    if payload.get("pb_r2_config_hash")!=EXPECTED_CONFIG_HASH:raise ValueError("PB-R2 configuration hash mismatch")
    if tuple(payload.get("outputs") or ())!=OUTPUTS:raise ValueError("PB-R2 output contract mismatch")
    if tuple(payload.get("horizons") or ())!=HORIZONS:raise ValueError("PB-R2 horizon contract mismatch")
    return payload


def _feature_schema_hash(features:list[str])->str:
    return hashlib.sha256(json.dumps(features,separators=(",",":"),ensure_ascii=True).encode()).hexdigest()


def _jsonable(value:Any)->Any:
    if isinstance(value,np.ndarray):return value.tolist()
    if isinstance(value,np.generic):return value.item()
    if isinstance(value,Mapping):return {str(k):_jsonable(v) for k,v in value.items()}
    if isinstance(value,(list,tuple)):return [_jsonable(v) for v in value]
    return value


def component_identity_hash(component:Mapping[str,Any])->str:
    """Hash stable component metadata; the bundle SHA covers serialized tree state."""
    model=component["model"]
    model_identity={"kind":model["kind"]}
    if model["kind"]=="LINEAR_RIDGE":model_identity.update(intercept=model["intercept"],coefficients=model["coefficients"])
    else:model_identity["parameters"]=model["estimator"].get_params(deep=True)
    identity={key:component[key] for key in ("target","horizon","task","choice","transform","preprocessing","calibrator","ordinal_boundaries","empirical_ranges","evidence","base_rows","calibration_rows")}
    identity["model"]=model_identity
    encoded=json.dumps(_jsonable(identity),sort_keys=True,separators=(",",":"),allow_nan=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def load_frozen_artifact(manifest_path: Path=MANIFEST_PATH)->tuple[dict[str,Any],dict[str,Any]]:
    manifest=load_manifest(manifest_path);artifact_path=ROOT/manifest["artifact_path"]
    if not artifact_path.exists():raise FileNotFoundError("PB-R2 frozen artifact missing")
    if sha256_file(artifact_path)!=manifest["artifact_sha256"]:raise ValueError("PB-R2 frozen artifact hash mismatch")
    artifact=joblib.load(artifact_path)
    if artifact.get("schema_version")!=ARTIFACT_SCHEMA:raise ValueError("PB-R2 artifact schema mismatch")
    if artifact.get("pb_r2_config_hash")!=EXPECTED_CONFIG_HASH:raise ValueError("PB-R2 artifact methodology mismatch")
    if artifact.get("pb_r3_methodology_hash")!=EXPECTED_PB_R3_HASH:raise ValueError("PB-R3 methodology mismatch")
    if artifact.get("feature_schema_hash")!=manifest["feature_schema_hash"]:raise ValueError("PB-R2 feature schema identity mismatch")
    if _feature_schema_hash(list(artifact.get("features") or []))!=manifest["feature_schema_hash"]:raise ValueError("PB-R2 feature order/hash mismatch")
    if artifact.get("component_hashes")!=manifest.get("component_hashes"):raise ValueError("PB-R2 component manifest mismatch")
    for year,horizons in artifact.get("pipelines",{}).items():
        for horizon,targets in horizons.items():
            for target,component in targets.items():
                key=f"{year}/{horizon}/{target}"
                if component_identity_hash(component)!=manifest["component_hashes"].get(key):raise ValueError(f"PB-R2 component hash mismatch: {key}")
    return artifact,manifest


def _model_prediction(component:Mapping[str,Any],values:np.ndarray)->np.ndarray:
    model=component["model"]
    if model["kind"]=="LINEAR_RIDGE":
        prep=component["preprocessing"];missing=np.isnan(values);median=np.asarray(prep["median"],float)
        x=np.where(missing,median,values);x=(x-np.asarray(prep["mean"],float))/np.asarray(prep["scale"],float)
        has_missing=np.asarray(prep["has_missing"],bool)
        if has_missing.any():x=np.column_stack([x,missing[:,has_missing].astype(float)])
        return float(model["intercept"])+np.einsum("ij,j->i",x,np.asarray(model["coefficients"],float),optimize=False)
    estimator=model["estimator"]
    return estimator.predict_proba(values)[:,1]


def _calibrate(component:Mapping[str,Any],raw:np.ndarray)->np.ndarray:
    spec=component["calibrator"];kind=spec["kind"]
    if kind=="none":return np.clip(raw,1e-6,1-1e-6) if component["task"]=="classification" else raw
    if component["task"]=="classification":return expit(float(spec["intercept"])+float(spec["coefficient"])*logit(np.clip(raw,1e-5,1-1e-5)))
    return float(spec["intercept"])+float(spec["slope"])*raw


def _ranges(component:Mapping[str,Any],prediction:np.ndarray)->tuple[np.ndarray,np.ndarray]:
    table=component["empirical_ranges"];maximum=np.asarray(table["prediction_max"],float);bucket=np.clip(np.searchsorted(maximum,prediction,side="left"),0,len(maximum)-1)
    return np.asarray(table["lower"],float)[bucket],np.asarray(table["upper"],float)[bucket]


class FrozenPB2Inference:
    """Inference-only adapter. This class has no training or network path."""
    def __init__(self,artifact:Mapping[str,Any],manifest:Mapping[str,Any]):self.artifact=dict(artifact);self.manifest=dict(manifest)
    @classmethod
    def from_manifest(cls,path:Path=MANIFEST_PATH)->"FrozenPB2Inference":
        artifact,manifest=load_frozen_artifact(path);return cls(artifact,manifest)
    @property
    def metadata(self)->dict[str,Any]:
        return {key:self.manifest[key] for key in ("artifact_sha256","pb_r2_config_hash","feature_schema_hash","training_cutoffs","created_at","versions")}
    def predict_frame(self,frame:pd.DataFrame,as_of_year:int)->list[dict[str,Any]]:
        features=list(self.artifact["features"]);missing_columns=[name for name in features if name not in frame]
        if missing_columns:return [{"status":NOT_AVAILABLE,"reason":"MISSING_FEATURE_COLUMNS","missing_features":missing_columns} for _ in range(len(frame))]
        values=frame.loc[:,features].apply(pd.to_numeric,errors="coerce").replace([np.inf,-np.inf],np.nan).to_numpy(float)
        finite_coverage=np.isfinite(values).sum(axis=1);result=[{"status":"AVAILABLE","predictions":{},"coverage":{"available":int(finite_coverage[i]),"required":len(features),"missing_features":[features[j] for j in np.flatnonzero(~np.isfinite(values[i]))]},"provenance":self.metadata} for i in range(len(frame))]
        if str(as_of_year) not in self.artifact["pipelines"]:
            return [{"status":NOT_AVAILABLE,"reason":"UNFROZEN_AS_OF_YEAR","as_of_year":int(as_of_year)} for _ in range(len(frame))]
        for index in np.flatnonzero(finite_coverage==0):result[index]={"status":NOT_AVAILABLE,"reason":"NO_FINITE_FEATURES","missing_features":features}
        for horizon in HORIZONS:
            for target in OUTPUTS:
                component=self.artifact["pipelines"][str(as_of_year)][str(horizon)][target]
                raw=_model_prediction(component,values);prediction=_calibrate(component,raw)
                oriented=-prediction if target=="mae" else prediction;state=STATES[np.digitize(oriented,np.asarray(component["ordinal_boundaries"],float))];lower,upper=_ranges(component,prediction)
                for i,item in enumerate(result):
                    if item["status"]!= "AVAILABLE":continue
                    item["predictions"].setdefault(target,{})[str(horizon)]={"value":float(prediction[i]),"ordinal_state":str(state[i]),"empirical_range":{"lower":float(lower[i]),"upper":float(upper[i])},"evidence":component["evidence"]}
        return result
    def predict_one(self,features:Mapping[str,Any],as_of_year:int)->dict[str,Any]:
        return self.predict_frame(pd.DataFrame([dict(features)]),as_of_year)[0]
    def predict(self,snapshot:Mapping[str,Any])->dict[str,Any]:
        """PB-P1 callable contract for one already-constructed causal feature snapshot."""
        features=snapshot.get("features") if isinstance(snapshot.get("features"),Mapping) else snapshot
        year=snapshot.get("as_of_year")
        if year is None:
            date_value=snapshot.get("signal_date") or snapshot.get("trade_date") or snapshot.get("as_of_date")
            try:year=int(str(date_value)[:4])
            except (TypeError,ValueError):return {"status":NOT_AVAILABLE,"reason":"MISSING_AS_OF_YEAR"}
        return self.predict_one(features,int(year))

    def asymmetry_10d(self,prediction:Mapping[str,Any],as_of_year:int)->dict[str,Any]:
        """Apply the frozen PB-R3 ratio and annual denominator floor."""
        if prediction.get("status")!="AVAILABLE":return {"status":NOT_AVAILABLE,"reason":"PB_R2_NOT_AVAILABLE"}
        try:
            mfe=float(prediction["predictions"]["mfe"]["10"]["value"])
            mae=float(prediction["predictions"]["mae"]["10"]["value"])
            floor=float(self.artifact["pb_r3"]["annual_mae_floors"][str(as_of_year)])
        except (KeyError,TypeError,ValueError):return {"status":NOT_AVAILABLE,"reason":"PB_R3_INPUT_NOT_AVAILABLE"}
        value=mfe/max(abs(mae),floor)
        boundaries=self.artifact["pb_r3"]["bucket_boundaries"]
        bucket="UNFAVORABLE" if value<float(boundaries["unfavorable_lt"]) else ("FAVORABLE" if value>=float(boundaries["favorable_gte"]) else "MIXED")
        return {"status":"AVAILABLE","value":float(value),"bucket":bucket,"methodology_hash":EXPECTED_PB_R3_HASH,"denominator_floor":floor}


def validate_finite_output(payload:Mapping[str,Any])->bool:
    if payload.get("status")!="AVAILABLE":return False
    return all(math.isfinite(float(payload["predictions"][target][str(h)]["value"])) for target in OUTPUTS for h in HORIZONS)
