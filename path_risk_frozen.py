"""Warning-free inference for the frozen PR-R1 dual-head methodology.

The artifact contains train-fitted preprocessing, coefficients, rank
references, and state boundaries.  This module never fits a model.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd
from scipy.interpolate import BSpline
from scipy.special import expit


EXPECTED_METHODOLOGY_HASH = "9f7e58c82de0b3084fbe0e13ad64a3912773469051b9ed773022acbd46c04f79"
EXPECTED_ARTIFACT_FILE_SHA256 = "91129e08a3e4849f1de24d7886049e7208b53bc128ce3a44eaf3bef12334e6e1"
ARTIFACT_SCHEMA_VERSION = "PR_R1_FROZEN_INFERENCE_V1"
STATE_ORDER = ("LOW", "NORMAL", "ELEVATED", "HIGH")


def _canonical_bytes(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def artifact_digest(payload: Mapping[str, Any]) -> str:
    content = dict(payload)
    content.pop("artifact_sha256", None)
    return hashlib.sha256(_canonical_bytes(content)).hexdigest()


def load_frozen_artifact(path: str | Path) -> dict[str, Any]:
    raw = Path(path).read_bytes()
    if hashlib.sha256(raw).hexdigest() != EXPECTED_ARTIFACT_FILE_SHA256:
        raise ValueError("Path Risk artifact file hash mismatch")
    payload = json.loads(raw)
    if payload.get("schema_version") != ARTIFACT_SCHEMA_VERSION:
        raise ValueError("Unsupported Path Risk artifact schema")
    if payload.get("methodology_hash") != EXPECTED_METHODOLOGY_HASH:
        raise ValueError("Path Risk methodology hash mismatch")
    if payload.get("artifact_sha256") != artifact_digest(payload):
        raise ValueError("Path Risk artifact integrity check failed")
    return payload


def _numeric_input(frame: pd.DataFrame, feature_names: list[str]) -> np.ndarray:
    missing = [name for name in feature_names if name not in frame]
    if missing:
        raise ValueError(f"Missing Path Risk features: {', '.join(missing)}")
    values = frame.loc[:, feature_names].apply(pd.to_numeric, errors="coerce").to_numpy(dtype=float)
    if np.isinf(values).any():
        raise ValueError("Infinite Path Risk feature value")
    return values


def _impute_clip(values: np.ndarray, preprocessing: Mapping[str, Any]) -> np.ndarray:
    medians = np.asarray(preprocessing["imputer_medians"], dtype=float)
    lower = np.asarray(preprocessing["winsor_lower"], dtype=float)
    upper = np.asarray(preprocessing["winsor_upper"], dtype=float)
    result = np.where(np.isnan(values), medians, values)
    result = np.clip(result, lower, upper)
    if not np.isfinite(result).all():
        raise ValueError("Non-finite Path Risk value after preprocessing")
    return result


def _one_hot(frame: pd.DataFrame, categories: list[str]) -> np.ndarray:
    if "primary_strategy" not in frame:
        raise ValueError("Missing Path Risk feature: primary_strategy")
    values = frame["primary_strategy"].astype(str).to_numpy()
    return np.column_stack([values == category for category in categories]).astype(float)


def _logistic_matrix(frame: pd.DataFrame, artifact: Mapping[str, Any]) -> np.ndarray:
    names = list(artifact["numeric_features"])
    prep = artifact["logistic_preprocessing"]
    values = _impute_clip(_numeric_input(frame, names), prep)
    mean = np.asarray(prep["scale_mean"], dtype=float)
    scale = np.asarray(prep["scale_scale"], dtype=float)
    numeric = (values - mean) / scale
    return np.hstack((numeric, _one_hot(frame, list(artifact["strategy_categories"]))))


def _spline_matrix(frame: pd.DataFrame, artifact: Mapping[str, Any]) -> np.ndarray:
    names = list(artifact["numeric_features"])
    prep = artifact["regression_preprocessing"]
    values = _impute_clip(_numeric_input(frame, names), prep)
    center = np.asarray(prep["robust_center"], dtype=float)
    scale = np.asarray(prep["robust_scale"], dtype=float)
    values = (values - center) / scale
    pieces = []
    for column, specification in enumerate(prep["splines"]):
        spline = BSpline(
            np.asarray(specification["knots"], dtype=float),
            np.asarray(specification["coefficients"], dtype=float),
            int(specification["degree"]),
            extrapolate=False,
        )
        # Frozen SplineTransformer(include_bias=False) drops the final basis.
        pieces.append(spline(values[:, column])[:, :-1])
    numeric = np.hstack(pieces)
    return np.hstack((numeric, _one_hot(frame, list(artifact["strategy_categories"]))))


def _safe_linear_response(matrix: np.ndarray, coefficients: np.ndarray, intercept: float) -> np.ndarray:
    """Avoid the affected Accelerate matrix-multiply path."""
    if matrix.shape[1] != coefficients.shape[0]:
        raise ValueError("Path Risk artifact coefficient shape mismatch")
    response = float(intercept) + np.sum(matrix * coefficients[np.newaxis, :], axis=1)
    if not np.isfinite(response).all():
        raise ValueError("Non-finite Path Risk linear response")
    return response


def _rank_against(reference: np.ndarray, values: np.ndarray) -> np.ndarray:
    return np.searchsorted(reference, values, side="right") / len(reference)


class FrozenPathRiskModel:
    """Read-only frozen PR-R1 inference contract."""

    def __init__(self, artifact: Mapping[str, Any]):
        self.artifact = dict(artifact)
        if self.artifact.get("methodology_hash") != EXPECTED_METHODOLOGY_HASH:
            raise ValueError("Path Risk methodology hash mismatch")

    @classmethod
    def from_path(cls, path: str | Path) -> "FrozenPathRiskModel":
        return cls(load_frozen_artifact(path))

    def predict(self, frame: pd.DataFrame) -> dict[str, np.ndarray]:
        logistic = self.artifact["logistic_model"]
        regression = self.artifact["regression_model"]
        probability = expit(_safe_linear_response(
            _logistic_matrix(frame, self.artifact),
            np.asarray(logistic["coefficients"], dtype=float),
            float(logistic["intercept"]),
        ))
        adverse_magnitude = np.maximum(_safe_linear_response(
            _spline_matrix(frame, self.artifact),
            np.asarray(regression["coefficients"], dtype=float),
            float(regression["intercept"]),
        ), 0.0)
        contract = self.artifact["risk_contract"]
        probability_rank = _rank_against(np.asarray(contract["probability_reference"], dtype=float), probability)
        magnitude_rank = _rank_against(np.asarray(contract["magnitude_reference"], dtype=float), adverse_magnitude)
        score = (probability_rank + magnitude_rank) / 2.0
        low, middle, high = (float(value) for value in contract["state_cutoffs"])
        states = np.select(
            [score <= low, score <= middle, score <= high],
            STATE_ORDER[:3],
            default=STATE_ORDER[3],
        )
        return {
            "adverse_barrier_probability": probability,
            "predicted_adverse_magnitude": adverse_magnitude,
            "risk_percentile_score": score,
            "state": states,
        }

    def explain(self, frame: pd.DataFrame, limit: int = 3) -> list[list[dict[str, Any]]]:
        """Return direct frozen-model contributions; no surrogate explanation."""
        names = list(self.artifact["numeric_features"])
        categories = list(self.artifact["strategy_categories"])
        logistic_matrix = _logistic_matrix(frame, self.artifact)
        spline_matrix = _spline_matrix(frame, self.artifact)
        logistic_coef = np.asarray(self.artifact["logistic_model"]["coefficients"], dtype=float)
        regression_coef = np.asarray(self.artifact["regression_model"]["coefficients"], dtype=float)
        logistic_parts = logistic_matrix * logistic_coef[np.newaxis, :]
        numeric_regression = spline_matrix[:, :len(names) * 4].reshape(len(frame), len(names), 4)
        numeric_regression_coef = regression_coef[:len(names) * 4].reshape(len(names), 4)
        regression_parts = np.sum(numeric_regression * numeric_regression_coef[np.newaxis, :, :], axis=2)
        strategy_logistic = logistic_parts[:, len(names):]
        strategy_regression = spline_matrix[:, len(names) * 4:] * regression_coef[len(names) * 4:][np.newaxis, :]
        rows = []
        for index in range(len(frame)):
            candidates = []
            for feature_index, name in enumerate(names):
                barrier = float(logistic_parts[index, feature_index])
                magnitude = float(regression_parts[index, feature_index])
                candidates.append({
                    "feature": name, "barrier_contribution": barrier,
                    "magnitude_contribution": magnitude,
                    "barrier_direction": "HIGHER" if barrier > 0 else "LOWER",
                    "magnitude_direction": "HIGHER" if magnitude > 0 else "LOWER",
                    "importance": abs(barrier) + abs(magnitude),
                })
            for category_index, category in enumerate(categories):
                if strategy_logistic[index, category_index] == 0 and strategy_regression[index, category_index] == 0:
                    continue
                barrier = float(strategy_logistic[index, category_index])
                magnitude = float(strategy_regression[index, category_index])
                candidates.append({
                    "feature": f"strategy:{category}", "barrier_contribution": barrier,
                    "magnitude_contribution": magnitude,
                    "barrier_direction": "HIGHER" if barrier > 0 else "LOWER",
                    "magnitude_direction": "HIGHER" if magnitude > 0 else "LOWER",
                    "importance": abs(barrier) + abs(magnitude),
                })
            rows.append(sorted(candidates, key=lambda item: item["importance"], reverse=True)[:limit])
        return rows
