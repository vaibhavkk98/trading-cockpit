"""Integrity-checked, inference-only PB-R2B/PB-R3B runtime."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

import joblib
import numpy as np
import pandas as pd
from scipy.special import expit, logit


ROOT = Path(__file__).resolve().parent
MANIFEST_PATH = ROOT/"data/production/predictability/pb_r2b/pb_r2b_manifest.json"
SCHEMA = "PB_R2B_FROZEN_INFERENCE_V1"
NOT_AVAILABLE = "NOT_AVAILABLE"


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_bundle(manifest_path: Path = MANIFEST_PATH) -> tuple[dict[str, Any], dict[str, Any]]:
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("schema_version") != "PB_R2B_PRODUCTION_MANIFEST_V1":
        raise ValueError("PB-R2B manifest schema mismatch")
    artifact_path = ROOT/manifest["artifact_path"]
    if sha256_file(artifact_path) != manifest["artifact_sha256"]:
        raise ValueError("PB-R2B artifact hash mismatch")
    artifact = joblib.load(artifact_path)
    if artifact.get("schema_version") != SCHEMA:
        raise ValueError("PB-R2B artifact schema mismatch")
    if artifact.get("methodology_hash") != manifest.get("pb_r2b_methodology_hash"):
        raise ValueError("PB-R2B methodology mismatch")
    if artifact.get("pb_r3b", {}).get("methodology_hash") != manifest.get("pb_r3b_methodology_hash"):
        raise ValueError("PB-R3B methodology mismatch")
    return artifact, manifest


def _transform(values: np.ndarray, kind: str) -> np.ndarray:
    if kind == "raw":
        return values
    if kind == "signed_log1p":
        return np.sign(values)*np.log1p(np.abs(values))
    raise ValueError(f"unknown PB transform: {kind}")


def _raw_prediction(component: Mapping[str, Any], values: np.ndarray) -> np.ndarray:
    values = _transform(values, str(component["transform"]))
    prep = component["preprocessing"]
    values = np.clip(values, np.asarray(prep["winsor_lower"], float), np.asarray(prep["winsor_upper"], float))
    missing = ~np.isfinite(values)
    median = np.asarray(prep["median"], float)
    x = np.where(missing, median, values)
    x = (x-np.asarray(prep["mean"], float))/np.asarray(prep["scale"], float)
    indicators = np.asarray(prep["missing_indicator_columns"], int)
    if len(indicators):
        x = np.column_stack([x, missing[:, indicators].astype(float)])
    score = float(component["intercept"])+np.einsum("ij,j->i", x, np.asarray(component["coefficients"], float), optimize=False)
    return expit(score) if component["task"] == "classification" else score


def _calibrate(component: Mapping[str, Any], raw: np.ndarray) -> np.ndarray:
    cal = component["calibrator"]
    if cal["kind"] == "none":
        return np.clip(raw, 1e-8, 1-1e-8) if component["task"] == "classification" else raw
    if cal["kind"] == "affine":
        return float(cal["intercept"])+float(cal["slope"])*raw
    if cal["kind"] == "platt":
        return expit(float(cal["intercept"])+float(cal["slope"])*logit(np.clip(raw, 1e-8, 1-1e-8)))
    raise ValueError("unknown PB calibrator")


class FrozenPBR2BInference:
    def __init__(self, artifact: Mapping[str, Any], manifest: Mapping[str, Any]):
        self.artifact, self.manifest = dict(artifact), dict(manifest)

    @classmethod
    def from_manifest(cls, path: Path = MANIFEST_PATH) -> "FrozenPBR2BInference":
        return cls(*load_bundle(path))

    def predict_frame(self, frame: pd.DataFrame) -> list[dict[str, Any]]:
        features = list(self.artifact["features"])
        absent = [name for name in features if name not in frame]
        if absent:
            return [{"status": NOT_AVAILABLE, "reason": "MISSING_FEATURE_COLUMNS", "missing_features": absent} for _ in range(len(frame))]
        values = frame[features].apply(pd.to_numeric, errors="coerce").replace([np.inf, -np.inf], np.nan).to_numpy(float)
        available = np.isfinite(values).sum(axis=1)
        results = [{
            "status": "AVAILABLE" if available[i] else NOT_AVAILABLE,
            "reason": None if available[i] else "NO_FINITE_FEATURES",
            "predictions": {},
            "feature_coverage": {"available": int(available[i]), "required": len(features), "ratio": float(available[i]/len(features))},
            "methodology_hash": self.artifact["methodology_hash"],
            "artifact_sha256": self.manifest["artifact_sha256"],
            "decision_authority": False,
        } for i in range(len(frame))]
        for horizon, targets in self.artifact["components"].items():
            for target, component in targets.items():
                prediction = _calibrate(component, _raw_prediction(component, values))
                for idx, result in enumerate(results):
                    if result["status"] != "AVAILABLE":
                        continue
                    result["predictions"].setdefault(target, {})[horizon] = {
                        "value": float(prediction[idx]), "evidence": component["evidence"]
                    }
        for idx, result in enumerate(results):
            if result["status"] != "AVAILABLE":
                continue
            try:
                mfe = result["predictions"]["mfe"]["10"]["value"]
                mae = result["predictions"]["mae"]["10"]["value"]
                r3 = self.artifact["pb_r3b"]
                ratio = float(mfe/max(abs(mae), float(r3["causal_floor"])))
                low, high = map(float, r3["boundaries"])
                state = "UNFAVORABLE" if ratio < low else ("FAVORABLE" if ratio >= high else "MIXED")
                result["pb_r3b"] = {"value": ratio, "state": state, "methodology_hash": r3["methodology_hash"]}
            except (KeyError, TypeError, ValueError):
                result["pb_r3b"] = {"status": NOT_AVAILABLE, "reason": "UNSUPPORTED_R2B_PREREQUISITE"}
        return results

    def predict_one(self, features: Mapping[str, Any]) -> dict[str, Any]:
        return self.predict_frame(pd.DataFrame([dict(features)]))[0]
