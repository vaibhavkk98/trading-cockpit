#!/usr/bin/env python3
"""Single bounded PB-R2B/PB-R3B production-native retraining run."""
from __future__ import annotations

import hashlib
import json
import math
import sys
import time
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from scipy.special import expit, logit
from scipy.stats import spearmanr
from sklearn.linear_model import LinearRegression, LogisticRegression, Ridge
from sklearn.metrics import average_precision_score, brier_score_loss, mean_absolute_error, mean_squared_error, roc_auc_score
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pb_native_features import FEATURES, HORIZONS, VERSION as BUILDER_VERSION, build_dataset, load_historical_inputs
from pb_r2b_frozen_inference import FrozenPBR2BInference, SCHEMA, sha256_file


SPEC_PATH = ROOT/"data/production/predictability/pb_p1c/pb_r2b_production_native_spec.json"
OUT = ROOT/"data/research/predictability/pb_r2b"
PROD = ROOT/"data/production/predictability/pb_r2b"
SEED = 20260901
TARGETS = {"mfe": "regression", "mae": "regression", "forward_volatility": "regression", "success_5_before_3": "classification", "adverse_first": "classification"}
TRANSFORMS = ("raw", "signed_log1p")
BASELINE_FINGERPRINT = "b93e8c2dd1a99fba712f89a38ba3a5689595891a47375e5c4f20a31d567d3640"
GO_TEXT = "GO — PRODUCTION-NATIVE PB-R2B/R3B FROZEN; PROSPECTIVE SHADOW ACTIVE"
NO_GO_TEXT = "NO-GO — PRODUCTION-NATIVE PB DOES NOT RETAIN SUFFICIENT PREDICTIVE VALUE"


def canonical_json(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str).encode()


def hash_payload(value) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    def clean(item):
        if isinstance(item, dict):
            return {str(key): clean(val) for key, val in item.items()}
        if isinstance(item, (list, tuple)):
            return [clean(val) for val in item]
        if isinstance(item, (float, np.floating)) and not math.isfinite(float(item)):
            return None
        if isinstance(item, np.generic):
            return item.item()
        return item
    path.write_text(json.dumps(clean(value), indent=2, sort_keys=True, default=str, allow_nan=False)+"\n")


def validate_spec(spec: dict) -> list[dict]:
    if spec.get("schema_version") != "PB_R2B_R3B_PRODUCTION_NATIVE_SPEC_V1":
        raise ValueError("unexpected PB-R2B spec")
    retained = [item for item in spec["features"] if item["disposition"] != "removed"]
    if len(retained) != 35 or tuple(item["new_name"] for item in retained) != FEATURES:
        raise ValueError("PB-R2B feature contract/order mismatch")
    if spec["decision_authority"] is not False or spec["reuse_old_coefficients_or_thresholds"] is not False:
        raise ValueError("unsafe PB-R2B spec flags")
    return retained


def feature_manifest(spec: dict, retained: list[dict]) -> dict:
    source = spec["raw_data_contract"]
    rows = []
    for item in retained:
        rows.append({
            **item,
            "raw_production_source": source["historical"],
            "timestamp_cutoff": source["timestamp"],
            "adjustment_semantics": source["adjustment"],
            "benchmark": source["benchmark"] if "market_" in item["new_name"] or "excess_" in item["new_name"] else None,
            "cross_sectional_universe": source["universe"] if item["new_name"] in {"native_rs_percentile_20d", "native_traded_value_percentile", "native_market_breadth_ema20"} else None,
            "historical_reconstruction_function": "pb_native_features.build_dataset",
            "live_production_function": "pb_native_features.build_live_eod_snapshot",
            "canonical_security_function": "pb_native_features.security_state",
        })
    return {"schema_version": "PB_R2B_FEATURE_MANIFEST_V1", "builder_version": BUILDER_VERSION, "feature_count": len(rows), "features": rows}


def materialize() -> tuple[pd.DataFrame, dict]:
    cache = OUT/"production_native_dataset.parquet"
    metadata_path = OUT/"dataset_metadata.json"
    if cache.exists() and metadata_path.exists():
        data = pd.read_parquet(cache)
        meta = json.loads(metadata_path.read_text())
        if meta.get("builder_version") == BUILDER_VERSION and meta.get("feature_count") == len(FEATURES):
            data.signal_date = pd.to_datetime(data.signal_date)
            return data, meta
    start = time.perf_counter()
    bars, eligibility, benchmarks, actions = load_historical_inputs(ROOT)
    data = build_dataset(bars, eligibility, benchmarks, actions)
    data.to_parquet(cache, index=False)
    meta = {
        "builder_version": BUILDER_VERSION, "feature_count": len(FEATURES), "rows": len(data),
        "securities": int(data.canonical_security_id.nunique()), "sessions": int(data.signal_date.nunique()),
        "date_min": str(data.signal_date.min().date()), "date_max": str(data.signal_date.max().date()),
        "build_seconds": time.perf_counter()-start, "source_contract": "immutable NSE EQ bhavcopy raw OHLCV",
        "positive_price_gate_all": bool(data.positive_price_gate_pass.all()),
        "auto_paper_holdout_opened": False,
    }
    write_json(metadata_path, meta)
    return data, meta


def _transform(values: np.ndarray, kind: str) -> np.ndarray:
    return values if kind == "raw" else np.sign(values)*np.log1p(np.abs(values))


def fit_component(frame: pd.DataFrame, target: str, task: str, transform: str) -> dict:
    values = frame[list(FEATURES)].apply(pd.to_numeric, errors="coerce").replace([np.inf, -np.inf], np.nan).to_numpy(float)
    values = _transform(values, transform)
    # Frozen PB research discipline: all limits are fitted on past/base rows.
    lower = np.nanquantile(values, .01, axis=0)
    upper = np.nanquantile(values, .99, axis=0)
    lower[~np.isfinite(lower)] = 0.0
    upper[~np.isfinite(upper)] = lower[~np.isfinite(upper)]
    values = np.clip(values, lower, upper)
    median = np.nanmedian(values, axis=0)
    median[~np.isfinite(median)] = 0.0
    missing = ~np.isfinite(values)
    indicator_columns = np.flatnonzero(missing.any(axis=0))
    values = np.where(missing, median, values)
    scaler = StandardScaler().fit(values)
    x = scaler.transform(values)
    if len(indicator_columns):
        x = np.column_stack([x, missing[:, indicator_columns].astype(float)])
    y = pd.to_numeric(frame[target], errors="coerce").to_numpy(float)
    if task == "regression":
        # SAG is the numerically stable optimizer for the frozen Ridge
        # objective on this macOS Accelerate build; cholesky/lsqr invoke a
        # broken BLAS matmul despite finite, bounded inputs.
        model = Ridge(alpha=10.0, solver="sag", random_state=SEED, tol=1e-6, max_iter=10_000).fit(x, y)
    else:
        model = LogisticRegression(C=1.0, solver="liblinear", random_state=SEED, max_iter=2000).fit(x, y.astype(int))
    return {
        "task": task, "transform": transform,
        "preprocessing": {"winsor_lower": lower.tolist(), "winsor_upper": upper.tolist(), "median": median.tolist(), "mean": scaler.mean_.tolist(), "scale": scaler.scale_.tolist(), "missing_indicator_columns": indicator_columns.tolist()},
        "intercept": float(np.ravel(model.intercept_)[0]), "coefficients": np.ravel(model.coef_).astype(float).tolist(),
    }


def raw_predict(component: dict, frame: pd.DataFrame) -> np.ndarray:
    values = frame[list(FEATURES)].apply(pd.to_numeric, errors="coerce").replace([np.inf, -np.inf], np.nan).to_numpy(float)
    values = _transform(values, component["transform"])
    prep = component["preprocessing"]
    values = np.clip(values, np.asarray(prep["winsor_lower"]), np.asarray(prep["winsor_upper"]))
    missing = ~np.isfinite(values)
    x = np.where(missing, np.asarray(prep["median"]), values)
    x = (x-np.asarray(prep["mean"]))/np.asarray(prep["scale"])
    columns = np.asarray(prep["missing_indicator_columns"], int)
    if len(columns):
        x = np.column_stack([x, missing[:, columns].astype(float)])
    score = component["intercept"]+np.einsum("ij,j->i", x, np.asarray(component["coefficients"]), optimize=False)
    return expit(score) if component["task"] == "classification" else score


def fit_calibrator(task: str, kind: str, raw: np.ndarray, actual: np.ndarray) -> dict:
    if kind == "none":
        return {"kind": "none"}
    if task == "regression":
        model = LinearRegression().fit(raw.reshape(-1, 1), actual)
        return {"kind": "affine", "intercept": float(model.intercept_), "slope": float(model.coef_[0])}
    x = logit(np.clip(raw, 1e-8, 1-1e-8)).reshape(-1, 1)
    model = LogisticRegression(C=1e6, solver="liblinear", random_state=SEED, max_iter=2000).fit(x, actual.astype(int))
    return {"kind": "platt", "intercept": float(model.intercept_[0]), "slope": float(model.coef_[0, 0])}


def calibrate(component: dict, raw: np.ndarray) -> np.ndarray:
    cal = component["calibrator"]
    if cal["kind"] == "none":
        return np.clip(raw, 1e-8, 1-1e-8) if component["task"] == "classification" else raw
    if cal["kind"] == "affine":
        return cal["intercept"]+cal["slope"]*raw
    return expit(cal["intercept"]+cal["slope"]*logit(np.clip(raw, 1e-8, 1-1e-8)))


def metric(actual: np.ndarray, prediction: np.ndarray, task: str, baseline: float) -> dict:
    if task == "regression":
        mae = mean_absolute_error(actual, prediction)
        base = mean_absolute_error(actual, np.full(len(actual), baseline))
        return {"mae": float(mae), "baseline_mae": float(base), "improvement_pct": float((base-mae)/base*100),
                "rmse": float(mean_squared_error(actual, prediction)**.5), "spearman": float(spearmanr(actual, prediction).statistic)}
    brier = brier_score_loss(actual, prediction)
    base = brier_score_loss(actual, np.full(len(actual), baseline))
    return {"brier": float(brier), "baseline_brier": float(base), "improvement_pct": float((base-brier)/base*100),
            "roc_auc": float(roc_auc_score(actual, prediction)), "pr_auc": float(average_precision_score(actual, prediction))}


def partition(data: pd.DataFrame, horizon: int, target: str, start: str, end: str | None) -> pd.DataFrame:
    date = pd.to_datetime(data.signal_date)
    label_end = pd.to_datetime(data[f"label_end_date_{horizon}d"])
    mask = date.ge(pd.Timestamp(start)) & data[target].notna()
    if end:
        boundary = pd.Timestamp(end)
        mask &= date.lt(boundary) & label_end.lt(boundary)
    return data.loc[mask].copy()


def pre_boundary(data: pd.DataFrame, horizon: int, target: str, boundary: str) -> pd.DataFrame:
    limit = pd.Timestamp(boundary)
    return data[data[target].notna() & pd.to_datetime(data.signal_date).lt(limit) & pd.to_datetime(data[f"label_end_date_{horizon}d"]).lt(limit)].copy()


def select_choices(data: pd.DataFrame) -> tuple[dict, pd.DataFrame]:
    choices, rows = {}, []
    for horizon in HORIZONS:
        for name, task in TARGETS.items():
            target = f"{name}_{horizon}d"
            base = pre_boundary(data, horizon, target, "2020-01-01")
            cal = partition(data, horizon, target, "2020-01-01", "2022-01-01")
            val = partition(data, horizon, target, "2022-01-01", "2024-01-01")
            if min(len(base), len(cal), len(val)) < 100:
                raise RuntimeError(f"insufficient chronological rows for {target}")
            baseline = float(cal[target].mean() if task == "classification" else cal[target].median())
            candidates = []
            for transform in TRANSFORMS:
                fitted = fit_component(base, target, task, transform)
                raw_cal = raw_predict(fitted, cal)
                for kind in (("none", "platt") if task == "classification" else ("none", "affine")):
                    component = {**fitted, "calibrator": fit_calibrator(task, kind, raw_cal, cal[target].to_numpy(float))}
                    prediction = calibrate(component, raw_predict(component, val))
                    stats = metric(val[target].to_numpy(float), prediction, task, baseline)
                    score = stats["brier" if task == "classification" else "mae"]
                    candidates.append((score, transform != "raw", kind != "none", transform, kind, stats))
                    rows.append({"horizon": horizon, "target": name, "transform": transform, "calibrator": kind, "base_n": len(base), "calibration_n": len(cal), "validation_n": len(val), **stats})
            winner = sorted(candidates, key=lambda x: (x[0], x[1], x[2]))[0]
            choices[f"{horizon}/{name}"] = {"transform": winner[3], "calibrator": winner[4], "validation_metrics": winner[5]}
    return choices, pd.DataFrame(rows)


def fit_selected(base: pd.DataFrame, cal: pd.DataFrame, target: str, task: str, choice: dict) -> dict:
    component = fit_component(base, target, task, choice["transform"])
    raw = raw_predict(component, cal)
    component["calibrator"] = fit_calibrator(task, choice["calibrator"], raw, cal[target].to_numpy(float))
    return component


def locked_evaluation(data: pd.DataFrame, choices: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    predictions, metrics = [], []
    # This is the sole read/evaluation of the disclosed reused 2024-2026 period.
    for year in (2024, 2025, 2026):
        eval_start, eval_end = f"{year}-01-01", f"{year+1}-01-01"
        cal_start = f"{year-2}-01-01"
        for horizon in HORIZONS:
            for name, task in TARGETS.items():
                target = f"{name}_{horizon}d"
                base = pre_boundary(data, horizon, target, cal_start)
                cal = partition(data, horizon, target, cal_start, eval_start)
                evaluation = partition(data, horizon, target, eval_start, eval_end)
                if not len(evaluation):
                    continue
                component = fit_selected(base, cal, target, task, choices[f"{horizon}/{name}"])
                pred = calibrate(component, raw_predict(component, evaluation))
                baseline = float(cal[target].mean() if task == "classification" else cal[target].median())
                stats = metric(evaluation[target].to_numpy(float), pred, task, baseline)
                metrics.append({"year": year, "horizon": horizon, "target": name, "n": len(evaluation), **stats})
                predictions.append(pd.DataFrame({"opportunity_id": evaluation.opportunity_id, "signal_date": evaluation.signal_date,
                    "symbol": evaluation.symbol, "primary_strategy": evaluation.primary_strategy, "year": year,
                    "horizon": horizon, "target": name, "actual": evaluation[target].to_numpy(float), "prediction": pred}))
    return pd.concat(predictions, ignore_index=True), pd.DataFrame(metrics)


def aggregate_metrics(predictions: pd.DataFrame, annual: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for (horizon, target), group in predictions.groupby(["horizon", "target"]):
        task = TARGETS[target]
        annual_rows = annual[(annual.horizon == horizon) & (annual.target == target)]
        baseline = float(group.actual.mean() if task == "classification" else group.actual.median())
        stats = metric(group.actual.to_numpy(float), group.prediction.to_numpy(float), task, baseline)
        fold_wins = int((annual_rows.improvement_pct > 0).sum())
        if task == "regression":
            ic = stats["spearman"]
            evidence = "WELL_CALIBRATED" if fold_wins >= 2 and stats["improvement_pct"] >= 5 and ic >= .15 else (
                "CALIBRATED_LOW_STRENGTH" if fold_wins >= 2 and stats["improvement_pct"] > 0 and ic > 0 else (
                    "ORDINAL_ONLY" if ic >= .05 else "UNUSABLE"))
        else:
            auc = stats["roc_auc"]
            evidence = "WELL_CALIBRATED" if fold_wins >= 2 and stats["improvement_pct"] >= 3 and auc >= .57 else (
                "CALIBRATED_LOW_STRENGTH" if fold_wins >= 2 and stats["improvement_pct"] > 0 and auc > .5 else (
                    "ORDINAL_ONLY" if auc >= .53 else "UNUSABLE"))
        rows.append({"horizon": horizon, "target": target, "n": len(group), "annual_fold_wins": fold_wins, "evidence": evidence, **stats})
    return pd.DataFrame(rows)


def stability(predictions: pd.DataFrame, dimension: str) -> pd.DataFrame:
    rows = []
    if dimension == "strategy":
        iterator = predictions.groupby(["horizon", "target", "primary_strategy"])
    else:
        iterator = predictions.groupby(["horizon", "target", "year"])
    for keys, group in iterator:
        h, target, state = keys
        if len(group) < 30:
            continue
        task = TARGETS[target]
        baseline = float(group.actual.mean() if task == "classification" else group.actual.median())
        rows.append({"horizon": h, "target": target, dimension: state, "n": len(group), **metric(group.actual.to_numpy(float), group.prediction.to_numpy(float), task, baseline)})
    return pd.DataFrame(rows)


def r3b_report(predictions: pd.DataFrame) -> tuple[dict, pd.DataFrame, pd.DataFrame]:
    ten = predictions[predictions.horizon.eq(10)].pivot(index=["opportunity_id", "signal_date", "symbol", "primary_strategy", "year"], columns="target", values=["actual", "prediction"]).reset_index()
    ten.columns = [x[0] if x[1] == "" else f"{x[0]}_{x[1]}" for x in ten.columns]
    calibration = ten[ten.year.isin([2024, 2025])].copy()
    floor = max(float(calibration.prediction_mae.abs().quantile(.10)), 1e-6)
    calibration["ratio"] = calibration.prediction_mfe/calibration.prediction_mae.abs().clip(lower=floor)
    boundaries = [float(x) for x in calibration.ratio.quantile([1/3, 2/3]).to_list()]
    evaluation = ten.copy()
    evaluation["asymmetry"] = evaluation.prediction_mfe/evaluation.prediction_mae.abs().clip(lower=floor)
    evaluation["state"] = pd.cut(evaluation.asymmetry, [-np.inf, boundaries[0], boundaries[1], np.inf], labels=["UNFAVORABLE", "MIXED", "FAVORABLE"], right=False).astype(str)
    evaluation["realized_ratio"] = evaluation.actual_mfe/evaluation.actual_mae.abs().clip(lower=.01)
    buckets = evaluation.groupby("state", observed=True).agg(n=("opportunity_id", "size"), success_rate=("actual_success_5_before_3", "mean"), adverse_rate=("actual_adverse_first", "mean"), mean_mfe=("actual_mfe", "mean"), median_mfe=("actual_mfe", "median"), mean_mae=("actual_mae", "mean"), median_mae=("actual_mae", "median"), realized_reward_adversity=("realized_ratio", "median")).reset_index()
    strategy = evaluation.groupby(["primary_strategy", "state"], observed=True).agg(n=("opportunity_id", "size"), success_rate=("actual_success_5_before_3", "mean"), realized_reward_adversity=("realized_ratio", "median")).reset_index()
    indexed = buckets.set_index("state")
    ordered = all(name in indexed.index for name in ("UNFAVORABLE", "FAVORABLE")) and indexed.loc["FAVORABLE", "success_rate"] > indexed.loc["UNFAVORABLE", "success_rate"] and indexed.loc["FAVORABLE", "realized_reward_adversity"] > indexed.loc["UNFAVORABLE", "realized_reward_adversity"]
    strategy_pivot = strategy.pivot(index="primary_strategy", columns="state", values="success_rate")
    stable_strategies = int(((strategy_pivot.get("FAVORABLE") > strategy_pivot.get("UNFAVORABLE"))).fillna(False).sum()) if not strategy_pivot.empty else 0
    config = {"formula": "predicted_MFE_10D/max(abs(predicted_MAE_10D),causal_floor)", "causal_floor": floor, "boundaries": boundaries, "ordered": bool(ordered), "stable_strategies": stable_strategies}
    return config, buckets, strategy


def freeze_artifact(data: pd.DataFrame, choices: dict, aggregate: pd.DataFrame, r3: dict, manifest_hash: str) -> tuple[dict, Path]:
    components = {}
    for horizon in HORIZONS:
        components[str(horizon)] = {}
        for name, task in TARGETS.items():
            evidence = aggregate[(aggregate.horizon == horizon) & (aggregate.target == name)].iloc[0].evidence
            if evidence == "UNUSABLE":
                continue
            target = f"{name}_{horizon}d"
            base = pre_boundary(data, horizon, target, "2024-01-01")
            cal = partition(data, horizon, target, "2024-01-01", "2026-01-01")
            component = fit_selected(base, cal, target, task, choices[f"{horizon}/{name}"])
            component.update(target=name, horizon=horizon, evidence=evidence, base_n=len(base), calibration_n=len(cal))
            components[str(horizon)][name] = component
    method_identity = {"schema": SCHEMA, "feature_manifest_hash": manifest_hash, "choices": choices, "evidence": aggregate[["horizon", "target", "evidence"]].to_dict("records"), "model_contract": "Ridge(alpha=10,SAG numerical solver)|Logistic(C=1,liblinear); train 1/99 winsor+median+standard scale+missing indicators; calibration-only affine/Platt"}
    methodology_hash = hash_payload(method_identity)
    pb_r3_methodology_hash = hash_payload({"pb_r2b_methodology_hash": methodology_hash, **r3})
    artifact = {"schema_version": SCHEMA, "features": list(FEATURES), "feature_manifest_hash": manifest_hash,
                "methodology_hash": methodology_hash, "components": components,
                "pb_r3b": {**r3, "methodology_hash": pb_r3_methodology_hash}, "decision_authority": False}
    path = PROD/"pb_r2b_frozen_inference_v1.joblib"
    path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(artifact, path, compress=3)
    return artifact, path


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    spec = json.loads(SPEC_PATH.read_text())
    retained = validate_spec(spec)
    manifest = feature_manifest(spec, retained)
    write_json(OUT/"production_native_feature_manifest.json", manifest)
    manifest_hash = hash_payload(manifest)
    data, dataset_meta = materialize()
    choices, grid = select_choices(data)
    write_json(OUT/"frozen_model_selection.json", choices)
    grid.to_csv(OUT/"validation_model_grid.csv", index=False)
    predictions, annual = locked_evaluation(data, choices)
    predictions.to_parquet(OUT/"locked_chronological_predictions.parquet", index=False)
    annual.to_csv(OUT/"annual_locked_metrics.csv", index=False)
    aggregate = aggregate_metrics(predictions, annual)
    aggregate.to_csv(OUT/"locked_evaluation_summary.csv", index=False)
    strategy = stability(predictions, "strategy")
    strategy.to_csv(OUT/"strategy_stability.csv", index=False)
    r3, r3_buckets, r3_strategy = r3b_report(predictions)
    r3_buckets.to_csv(OUT/"pb_r3b_bucket_results.csv", index=False)
    r3_strategy.to_csv(OUT/"pb_r3b_strategy_stability.csv", index=False)
    write_json(OUT/"pb_r3b_configuration.json", r3)

    ten = aggregate[aggregate.horizon.eq(10)].set_index("target")
    core_supported = all(ten.loc[name, "evidence"] in {"WELL_CALIBRATED", "CALIBRATED_LOW_STRENGTH"} for name in ("forward_volatility", "mae", "mfe"))
    gate = bool(core_supported and r3["ordered"] and r3["stable_strategies"] >= 3)
    verdict = GO_TEXT if gate else NO_GO_TEXT
    original = pd.read_csv(ROOT/"data/research/predictability/pb_r2/calibration_summary.csv")
    original = original[original.horizon.eq(10)][["target", "quality", "spearman", "mae_improvement_pct", "auc", "brier", "baseline_brier"]]
    comparison = ten.reset_index().merge(original, on="target", how="left", suffixes=("_r2b", "_original"))
    comparison.to_csv(OUT/"original_pb_r2_comparison.csv", index=False)

    activation = {"status": "INACTIVE", "decision_authority": False, "activation_date": None, "prospective_only": True, "verdict": verdict}
    hashes = {}
    if gate:
        artifact, artifact_path = freeze_artifact(data, choices, aggregate, r3, manifest_hash)
        artifact_sha = sha256_file(artifact_path)
        reference = data[data.signal_date.ge(pd.Timestamp("2026-01-01"))].head(128)
        production_manifest = {
            "schema_version": "PB_R2B_PRODUCTION_MANIFEST_V1", "activation_status": "ACTIVE",
            "activation_date": "2026-09-21", "prospective_origin": "NEXT_CAUSALLY_ELIGIBLE_FINALIZED_COHORT",
            "decision_authority": False, "pb_r2b_methodology_hash": artifact["methodology_hash"],
            "pb_r3b_methodology_hash": artifact["pb_r3b"]["methodology_hash"],
            "feature_manifest_hash": manifest_hash,
            "artifact_path": str(artifact_path.relative_to(ROOT)), "artifact_sha256": artifact_sha,
            "supported_outputs": {str(h): sorted(artifact["components"][str(h)]) for h in HORIZONS},
            "label": "Prospective PB shadow — no trading authority.",
        }
        write_json(PROD/"pb_r2b_manifest.json", production_manifest)
        engine = FrozenPBR2BInference.from_manifest(PROD/"pb_r2b_manifest.json")
        first = engine.predict_frame(reference)
        second = engine.predict_frame(reference)
        if canonical_json(first) != canonical_json(second):
            raise RuntimeError("frozen PB-R2B inference is not deterministic")
        write_json(PROD/"reference_reproduction.json", {"rows": len(reference), "tolerance": 1e-10, "exact_json_reproduction": True, "outputs": first})
        activation = {"status": "ACTIVE", "decision_authority": False, "activation_date": production_manifest["activation_date"], "prospective_only": True, "historical_prospective_backfill": False, "verdict": verdict}
        hashes = {"pb_r2b_methodology_hash": artifact["methodology_hash"], "pb_r3b_methodology_hash": artifact["pb_r3b"]["methodology_hash"], "artifact_sha256": artifact_sha, "feature_manifest_hash": manifest_hash}
    write_json(OUT/"prospective_activation.json", activation)
    result = {"verdict": verdict, "production_native_contract": manifest["schema_version"], "dataset": dataset_meta,
              "chronological_splits": spec["split_protocol"], "selected_models": choices,
              "pb_r2b_10d": ten.reset_index().to_dict("records"), "pb_r3b": r3,
              "activation": activation, "hashes": hashes, "baseline_fingerprint_expected": BASELINE_FINGERPRINT,
              "old_artifacts_mutated": False, "autopaper_holdout_opened": False}
    write_json(OUT/"pb_r2b_results.json", result)
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
