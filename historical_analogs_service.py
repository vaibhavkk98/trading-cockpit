"""Production-only, advisory historical-analog retrieval.

The service consumes a causally prepared current feature state.  It reads only
the frozen TRAIN outcome pool: validation and final-test outcomes are never
needed to answer a live query.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping, Optional

import numpy as np
import pandas as pd
import pyarrow.parquet as pq


METHODOLOGY_ID = "C_HYBRID__B_GROUP_BALANCED__K40__MARKET_CONTEXT_V1"
METHODOLOGY_HASH = "c886a21dac9e14a3e6dadfa167b83f5066a8b63b1367b6e426d8da59c967407f"
K = 40
TRAIN_CUTOFF = pd.Timestamp("2023-01-01")
MIN_PAIRWISE_COVERAGE = 0.50
OUTCOMES = (
    "mfe_10d", "mfe_20d", "mae_10d", "mae_20d",
    "target_5_before_stop_3_20d", "time_to_mfe_10d", "time_to_mfe_20d",
)


class HistoricalAnalogContractError(ValueError):
    """Raised when a query cannot satisfy the frozen production contract."""


class HistoricalAnalogService:
    def __init__(self, artifact_root: Optional[Path] = None, snapshot_writer=None):
        root = Path(__file__).resolve().parent
        production_root = root / "data/production/historical_analogs"
        self.artifact_root = Path(artifact_root or (production_root if production_root.exists() else root / "data/research/historical_analogs/ha_v1_final_expanded"))
        self.methodology_path = self.artifact_root / "frozen_methodology.json"
        self.production_pool_path = self.artifact_root / "ha_k40_train_pool.parquet"
        self.states_path = self.artifact_root / "expanded_opportunity_states.parquet"
        self.outcomes_path = self.artifact_root / "expanded_forward_outcomes.parquet"
        raw = self.methodology_path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != METHODOLOGY_HASH:
            raise HistoricalAnalogContractError("Frozen historical-analog methodology hash mismatch.")
        self.contract = json.loads(raw)
        if self.contract.get("candidate_id") != METHODOLOGY_ID or int(self.contract.get("k", 0)) != K:
            raise HistoricalAnalogContractError("Frozen historical-analog candidate contract mismatch.")
        self.features = tuple(self.contract["feature_contract"])
        if len(self.features) != 23 or self.contract.get("sector_candidate_eligible") is not False:
            raise HistoricalAnalogContractError("Expected the frozen 23-feature Base+Market contract without sector context.")
        self.groups = self.contract["group_contract"]
        self.blends = self.contract["preprocessing"]["frozen_base_stock_percentile_blend"]
        self.median = self.contract["preprocessing"]["robust_median"]
        self.scale = self.contract["preprocessing"]["robust_scale"]
        self.snapshot_writer = snapshot_writer
        self._history: Optional[pd.DataFrame] = None
        self._matrix: Optional[np.ndarray] = None

    @staticmethod
    def _qualified(opportunity: Mapping[str, Any]) -> bool:
        explicit = opportunity.get("is_qualified")
        if explicit is False:
            return False
        status = str(opportunity.get("qualification_status") or "").upper()
        return explicit is True or status in {"QUALIFIED", "ALLOCATED", "PASS"}

    @staticmethod
    def build_causal_query_state(stock_bars: pd.DataFrame, nifty500_bars: pd.DataFrame,
                                 india_vix_bars: pd.DataFrame, signal_date: Any) -> dict[str, dict[str, float]]:
        """Reconstruct the frozen query fields from histories ending at signal T."""
        cutoff = pd.Timestamp(signal_date).normalize()

        def normalized(frame: pd.DataFrame) -> pd.DataFrame:
            result = frame.copy()
            result.index = pd.to_datetime(result.index).tz_localize(None).normalize()
            result = result[result.index <= cutoff].sort_index(kind="mergesort")
            result.columns = [str(column).lower() for column in result.columns]
            return result

        stock, nifty, vix = normalized(stock_bars), normalized(nifty500_bars), normalized(india_vix_bars)
        if len(stock) < 140 or len(nifty) < 21 or len(vix) < 6:
            raise HistoricalAnalogContractError("Insufficient causal OHLCV history for the frozen HA query state.")
        required = {"open", "high", "low", "close", "volume"}
        if not required.issubset(stock.columns) or "close" not in nifty or "close" not in vix:
            raise HistoricalAnalogContractError("HA query histories require stock OHLCV and benchmark close columns.")
        opn, high, low = (pd.to_numeric(stock[name], errors="coerce") for name in ("open", "high", "low"))
        close, volume = (pd.to_numeric(stock[name], errors="coerce") for name in ("close", "volume"))
        daily = close.pct_change(fill_method=None) * 100.0
        previous = close.shift(1)
        true_range = pd.concat([(high - low).abs(), (high - previous).abs(), (low - previous).abs()], axis=1).max(axis=1, skipna=False)
        prior_volume20 = volume.shift(1).rolling(20, min_periods=20).mean()
        ema20 = close.ewm(span=20, adjust=False).mean()
        atr20 = true_range.rolling(20, min_periods=20).mean()
        positive = daily.gt(0).where(daily.notna()).astype(float)
        fields = pd.DataFrame(index=stock.index)
        fields["atr_pct"] = atr20 / close * 100.0
        fields["realized_volatility_20d_ann_pct"] = daily.rolling(20, min_periods=20).std(ddof=1) * np.sqrt(252.0)
        fields["typical_abs_daily_return_20d"] = daily.abs().rolling(20, min_periods=20).median()
        fields["volume_variability_20d"] = volume.rolling(20, min_periods=20).std(ddof=1) / volume.rolling(20, min_periods=20).mean().replace(0, np.nan)
        gap = (opn / previous - 1.0) * 100.0
        fields["gap_frequency_20d"] = gap.abs().ge(1.0).where(gap.notna()).astype(float).rolling(20, min_periods=20).mean()
        fields["momentum_persistence_20d"] = positive.rolling(20, min_periods=20).mean()
        fields["volume_ratio_20"] = volume / prior_volume20.replace(0, np.nan)
        fields["distance_from_ema20_pct"] = (close / ema20 - 1.0) * 100.0
        fields["distance_from_ema20_atr"] = (close - ema20) / atr20.replace(0, np.nan)
        fields["distance_from_prior_20d_high_pct"] = (close / high.shift(1).rolling(20, min_periods=20).max() - 1.0) * 100.0
        fields["ret_10d"] = close.pct_change(10, fill_method=None) * 100.0
        fields["largest_positive_daily_return_10d"] = daily.rolling(10, min_periods=10).max()
        fields["positive_sessions_10"] = positive.rolling(10, min_periods=10).sum()
        fields["volume_acceleration_5_vs_20"] = volume.shift(1).rolling(5, min_periods=5).mean() / prior_volume20.replace(0, np.nan)
        stock_ret20 = close.pct_change(20, fill_method=None) * 100.0

        nifty_close = pd.to_numeric(nifty["close"], errors="coerce")
        nifty_daily = nifty_close.pct_change(fill_method=None) * 100.0
        market = {
            "nifty500_ret_5d": nifty_close.pct_change(5, fill_method=None).iloc[-1] * 100.0,
            "nifty500_ret_10d": nifty_close.pct_change(10, fill_method=None).iloc[-1] * 100.0,
            "nifty500_ret_20d": nifty_close.pct_change(20, fill_method=None).iloc[-1] * 100.0,
            "nifty500_distance_from_ema20_pct": (nifty_close.iloc[-1] / nifty_close.ewm(span=20, adjust=False).mean().iloc[-1] - 1.0) * 100.0,
            "nifty500_realized_volatility_20d_ann_pct": nifty_daily.rolling(20, min_periods=20).std(ddof=1).iloc[-1] * np.sqrt(252.0),
            "india_vix_level": pd.to_numeric(vix["close"], errors="coerce").iloc[-1],
            "india_vix_change_5d_pct": pd.to_numeric(vix["close"], errors="coerce").pct_change(5, fill_method=None).iloc[-1] * 100.0,
        }
        current = {name: float(fields[name].iloc[-1]) for name in fields}
        current.update({name: float(value) for name, value in market.items()})
        current["stock_minus_nifty500_ret_10d"] = current["ret_10d"] - current["nifty500_ret_10d"]
        current["stock_minus_nifty500_ret_20d"] = float(stock_ret20.iloc[-1]) - current["nifty500_ret_20d"]
        percentiles = {}
        for feature, output in {
            "distance_from_ema20_atr": "distance_from_ema20_atr_stock_pctile_252",
            "distance_from_ema20_pct": "distance_from_ema20_pct_stock_pctile_252",
            "largest_positive_daily_return_10d": "largest_positive_daily_return_10d_stock_pctile_252",
            "positive_sessions_10": "positive_sessions_10_stock_pctile_252",
            "ret_10d": "ret_10d_stock_pctile_252",
            "volume_acceleration_5_vs_20": "volume_acceleration_5_vs_20_stock_pctile_252",
        }.items():
            prior = pd.to_numeric(fields[feature].iloc[max(0, len(fields) - 253):-1], errors="coerce").dropna().to_numpy()
            value = current[feature]
            if len(prior) < 120 or not np.isfinite(value):
                raise HistoricalAnalogContractError(f"Insufficient trailing percentile history for {feature}.")
            percentiles[output] = float(100.0 * ((prior < value).sum() + 0.5 * (prior == value).sum()) / len(prior))
        if not all(np.isfinite(value) for value in current.values()):
            raise HistoricalAnalogContractError("Causal HA query state contains unavailable feature values.")
        return {"ha_features": current, "ha_stock_percentiles": percentiles}

    def _load_train_pool(self) -> tuple[pd.DataFrame, np.ndarray]:
        if self._history is not None and self._matrix is not None:
            return self._history, self._matrix
        state_columns = [
            "opportunity_id", "canonical_security_id", "symbol", "signal_date", "label_end_date",
            *self.features, *self.blends.values(),
        ]
        label_columns = ["opportunity_id", "signal_date", "outcome_availability", *OUTCOMES]
        cutoff = TRAIN_CUTOFF.to_pydatetime()
        if self.production_pool_path.exists():
            history = pq.read_table(self.production_pool_path, filters=[("signal_date", "<", cutoff)]).to_pandas()
        else:
            # The filters are a safety boundary, not merely an in-memory convenience.
            states = pq.read_table(self.states_path, columns=state_columns, filters=[("signal_date", "<", cutoff)]).to_pandas()
            labels = pq.read_table(self.outcomes_path, columns=label_columns, filters=[("signal_date", "<", cutoff)]).to_pandas()
            history = states.merge(labels.drop(columns="signal_date"), on="opportunity_id", how="inner", validate="one_to_one")
        history["signal_date"] = pd.to_datetime(history["signal_date"]).dt.normalize()
        history["label_end_date"] = pd.to_datetime(history["label_end_date"]).dt.normalize()
        history = history[
            history["outcome_availability"].eq("AVAILABLE")
            & history["label_end_date"].notna()
            & history["signal_date"].lt(TRAIN_CUTOFF)
        ].sort_values(["signal_date", "symbol", "opportunity_id"], kind="mergesort").reset_index(drop=True)
        self._history = history
        self._matrix = self._transform_frame(history)
        return self._history, self._matrix

    def _transform_frame(self, frame: pd.DataFrame) -> np.ndarray:
        columns = []
        for feature in self.features:
            raw = pd.to_numeric(frame[feature], errors="coerce").replace([np.inf, -np.inf], np.nan)
            global_value = ((raw - float(self.median[feature])) / float(self.scale[feature])).clip(-5, 5)
            if feature in self.blends:
                stock = (pd.to_numeric(frame[self.blends[feature]], errors="coerce") - 50.0) / 25.0
                global_value = pd.concat([global_value, stock], axis=1).mean(axis=1, skipna=True)
            columns.append(global_value.to_numpy(dtype=float))
        return np.column_stack(columns)

    def _query_vector(self, opportunity: Mapping[str, Any]) -> np.ndarray:
        features = opportunity.get("ha_features")
        percentiles = opportunity.get("ha_stock_percentiles")
        if not isinstance(features, Mapping) and all(opportunity.get(name) is not None for name in ("ha_stock_bars", "ha_nifty500_bars", "ha_india_vix_bars")):
            prepared = self.build_causal_query_state(
                opportunity["ha_stock_bars"], opportunity["ha_nifty500_bars"], opportunity["ha_india_vix_bars"],
                opportunity.get("signal_date") or opportunity.get("analysis_date"),
            )
            features, percentiles = prepared["ha_features"], prepared["ha_stock_percentiles"]
        if not isinstance(features, Mapping) or not isinstance(percentiles, Mapping):
            raise HistoricalAnalogContractError(
                "Qualified opportunity requires causal ha_features and ha_stock_percentiles; current scanner payload is not yet HA-ready."
            )
        row: dict[str, list[Any]] = {}
        missing = []
        for feature in self.features:
            value = features.get(feature)
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(float(value)):
                missing.append(feature)
            row[feature] = [value]
        for feature, percentile_name in self.blends.items():
            value = percentiles.get(percentile_name, percentiles.get(feature))
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(float(value)):
                missing.append(percentile_name)
            row[percentile_name] = [value]
        if missing:
            raise HistoricalAnalogContractError("Missing causal HA query fields: " + ", ".join(sorted(set(missing))))
        return self._transform_frame(pd.DataFrame(row))[0]

    def _distances(self, query: np.ndarray, candidates: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        feature_index = {feature: index for index, feature in enumerate(self.features)}
        valid_all = np.isfinite(candidates) & np.isfinite(query)[None, :]
        coverage = valid_all.sum(axis=1) / len(self.features)
        group_values = []
        group_valid = np.ones(len(candidates), dtype=bool)
        for group_features in self.groups.values():
            indices = [feature_index[name] for name in group_features]
            valid = valid_all[:, indices]
            count = valid.sum(axis=1)
            group_valid &= count > 0
            differences = np.abs(candidates[:, indices] - query[indices])
            group_values.append(np.divide(np.where(valid, differences, 0.0).sum(axis=1), count,
                                          out=np.full(len(candidates), np.nan), where=count > 0))
        distance = np.nanmean(np.column_stack(group_values), axis=1)
        distance[(coverage < MIN_PAIRWISE_COVERAGE) | ~group_valid] = np.inf
        return distance, coverage

    @staticmethod
    def _evidence_quality(count: int, unique: int, max_year_share: float, max_date_share: float, coverage: float) -> str:
        if count < 20 or coverage < 0.90:
            return "INSUFFICIENT"
        if unique < 20 or max_year_share > 0.60 or max_date_share > 0.15:
            return "LOW"
        if unique < 30 or max_year_share > 0.40 or max_date_share > 0.10:
            return "MEDIUM"
        return "HIGH"

    def evaluate(self, opportunity: Mapping[str, Any], *, persist: bool = True) -> dict[str, Any]:
        if not self._qualified(opportunity):
            raise HistoricalAnalogContractError("Historical analogs are available only for qualified opportunities.")
        query_date = pd.Timestamp(opportunity.get("signal_date") or opportunity.get("analysis_date")).normalize()
        if pd.isna(query_date):
            raise HistoricalAnalogContractError("Qualified opportunity requires a causal signal_date.")
        opportunity_id = str(opportunity.get("opportunity_id") or "").strip()
        symbol = str(opportunity.get("symbol") or "").strip().upper()
        if not opportunity_id or not symbol:
            raise HistoricalAnalogContractError("Qualified opportunity requires opportunity_id and symbol.")
        query = self._query_vector(opportunity)
        history, matrix = self._load_train_pool()
        eligible_mask = history["signal_date"].lt(query_date) & history["label_end_date"].lt(query_date)
        eligible = history.loc[eligible_mask].copy()
        eligible_matrix = matrix[np.flatnonzero(eligible_mask.to_numpy())]
        distance, coverage = self._distances(query, eligible_matrix)
        ids = eligible["opportunity_id"].astype(str).to_numpy()
        dates = eligible["signal_date"].to_numpy(dtype="datetime64[ns]")
        order = np.lexsort((ids, dates, distance))
        chosen = order[np.isfinite(distance[order])][:K]
        analogs = eligible.iloc[chosen].copy()
        analogs["distance"] = distance[chosen]
        analogs["feature_coverage"] = coverage[chosen]
        analogs["rank"] = np.arange(1, len(analogs) + 1)
        years = analogs["signal_date"].dt.year.value_counts(normalize=True)
        date_shares = analogs["signal_date"].value_counts(normalize=True)
        max_year_share = float(years.max()) if len(years) else 0.0
        max_date_share = float(date_shares.max()) if len(date_shares) else 0.0
        median_coverage = float(analogs["feature_coverage"].median()) if len(analogs) else 0.0
        unique = int(analogs["canonical_security_id"].nunique()) if len(analogs) else 0

        def median(name: str):
            values = pd.to_numeric(analogs[name], errors="coerce")
            return float(values.median()) if values.notna().any() else None

        success = pd.to_numeric(analogs["target_5_before_stop_3_20d"], errors="coerce")
        result: dict[str, Any] = {
            "opportunity_id": opportunity_id, "signal_date": query_date.date().isoformat(), "symbol": symbol,
            "methodology_id": METHODOLOGY_ID, "methodology_version": str(self.contract["version"]),
            "methodology_hash": METHODOLOGY_HASH,
            "analog_count": int(len(analogs)), "unique_security_count": unique,
            "earliest_analog_date": analogs["signal_date"].min().date().isoformat() if len(analogs) else None,
            "latest_analog_date": analogs["signal_date"].max().date().isoformat() if len(analogs) else None,
            "maximum_year_share": max_year_share, "maximum_date_share": max_date_share,
            "outcome_attractiveness": {
                "median_mfe_10d": median("mfe_10d"), "median_mfe_20d": median("mfe_20d"),
                "plus_5_before_minus_3_rate": float(success.mean()) if success.notna().any() else None,
                "median_time_to_mfe_10d": median("time_to_mfe_10d"),
                "median_time_to_mfe_20d": median("time_to_mfe_20d"),
            },
            "downside_evidence": {"median_mae_10d": median("mae_10d"), "median_mae_20d": median("mae_20d")},
            "evidence_quality": self._evidence_quality(len(analogs), unique, max_year_share, max_date_share, median_coverage),
            "evidence": {
                "median_distance": float(analogs["distance"].median()) if len(analogs) else None,
                "median_feature_coverage": median_coverage, "historical_pool": "TRAIN_ONLY_PRE_2023",
                "analog_date_span_days": int((analogs["signal_date"].max() - analogs["signal_date"].min()).days) if len(analogs) else 0,
                "retrieval_k": K, "validation_outcomes_accessed": False, "final_test_outcomes_accessed": False,
            },
            "analogs": [],
        }
        for _, row in analogs.iterrows():
            result["analogs"].append({
                "rank": int(row["rank"]), "opportunity_id": str(row["opportunity_id"]),
                "symbol": str(row["symbol"]), "signal_date": row["signal_date"].date().isoformat(),
                "label_end_date": row["label_end_date"].date().isoformat(), "distance": float(row["distance"]),
                "feature_coverage": float(row["feature_coverage"]),
                "ret_10d": float(row["ret_10d"]) if pd.notna(row["ret_10d"]) else None,
                "nifty500_ret_10d": float(row["nifty500_ret_10d"]) if pd.notna(row["nifty500_ret_10d"]) else None,
                **{name: (float(row[name]) if pd.notna(row[name]) else None) for name in OUTCOMES},
            })
        if persist:
            writer = self.snapshot_writer
            if writer is None:
                from database import persist_historical_analog_snapshot
                writer = persist_historical_analog_snapshot
            result["persistence"] = writer(result)
        return result
