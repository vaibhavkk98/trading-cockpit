"""Research-only frozen V1C retrospective diagnostic.

The module never imports the production database.  It replays already-qualified
development opportunities with the deployed C0/C1/C2 ordering contracts and
the frozen R0 seeds.  Outputs are diagnostic artifacts, never prospective rows.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from dataclasses import dataclass
from statistics import mean, median
from typing import Any, Iterable, Mapping

import numpy as np
import pandas as pd

import opportunity_selection_engine as selection
from autopaper_v1 import Configuration, ExecutionSimulator
from portfolio_risk_engine import NORMAL_HEAT_LIMIT, TARGET_POSITION_RISK

VERSION = "SYSTEMATIC_ENGINE_V1C_RETROSPECTIVE_DIAGNOSTIC_V1"
LABEL = "RETROSPECTIVE_DIAGNOSTIC"
HOLDOUT_START = pd.Timestamp("2024-02-16")
BASELINE_FINGERPRINT = "b93e8c2dd1a99fba712f89a38ba3a5689595891a47375e5c4f20a31d567d3640"


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def finite(value: Any) -> float | None:
    try: value = float(value)
    except (TypeError, ValueError): return None
    return value if math.isfinite(value) else None


def frozen_contract() -> dict[str, Any]:
    contract = {"version": VERSION, "label": LABEL,
        "feature_manifest_hash": selection.FEATURE_MANIFEST_HASH,
        "c1_methodology_hash": selection.C1_METHODOLOGY_HASH,
        "c1_config_hash": selection.C1_CONFIG_HASH,
        "c2_methodology_hash": selection.C2_METHODOLOGY_HASH,
        "c2_config_hash": selection.C2_CONFIG_HASH,
        "random_manifest_hash": selection.RANDOM_MANIFEST_HASH,
        "random_seeds": list(selection.RANDOM_SEEDS),
        "portfolio": {"capital": 1_000_000, "rolling": "2/1/0", "risk": "B1",
            "hold": "H10", "execution": "T+1 next executable open", "replacement": False,
            "ordinary_stop": None, "target": None},
        "authority": "DIAGNOSTIC_ONLY_NO_PRODUCTION_CHANGE"}
    contract["contract_hash"] = digest(contract)
    return contract


def prepare_signals(frame: pd.DataFrame) -> list[dict[str, Any]]:
    """Map authoritative causal research fields to exact deployed semantics."""
    data = frame.copy()
    data["trade_date"] = pd.to_datetime(data.trade_date)
    if data.trade_date.max() >= HOLDOUT_START:
        raise RuntimeError("AUTOPAPER_HOLDOUT_SEALED")
    if (pd.to_datetime(data.feature_source_max_date) > data.trade_date).any():
        raise RuntimeError("FEATURE_LEAKAGE")
    rows = []
    for row in data.sort_values(["trade_date", "decision_id"]).to_dict("records"):
        rv_annual = finite(row.get("realized_vol_20d"))
        rv_daily = rv_annual / math.sqrt(252) if rv_annual is not None else selection.NOT_AVAILABLE
        atr = finite(row.get("atr_pct"))
        risk = max(atr, rv_daily) if atr is not None and rv_daily != selection.NOT_AVAILABLE else atr
        eligible = atr is not None and atr > 0
        rows.append({"decision_id": str(row["decision_id"]), "opportunity_id": str(row["opportunity_id"]),
            "trade_date": str(pd.Timestamp(row["trade_date"]).date()),
            "signal_date": str(pd.Timestamp(row["trade_date"]).date()),
            "canonical_security_id": str(row["canonical_security_id"]), "symbol": str(row["symbol"]),
            "primary_strategy": str(row["primary_strategy"]), "sector": str(row.get("sector") or "UNKNOWN"),
            "traded_value": float(row["traded_value"]), "atr_pct": atr,
            "selection_inputs": {"features": {
                "relative_demand_20d_pct": row.get("excess_20d", selection.NOT_AVAILABLE),
                "volume_ratio_20d": row.get("volume_ratio", selection.NOT_AVAILABLE),
                "close_location_value": row.get("close_location_value", selection.NOT_AVAILABLE),
                "ema20_extension_pct": row.get("ema20_extension", selection.NOT_AVAILABLE)},
                "atr_pct": atr if atr is not None else selection.NOT_AVAILABLE,
                "rv20_daily_pct": rv_daily, "risk_proxy_pct": risk if eligible else selection.NOT_AVAILABLE,
                "risk_proxy_coverage": "ATR_AND_RV20" if eligible and rv_daily != selection.NOT_AVAILABLE else "ATR_ONLY",
                "eligible": eligible, "source_timestamp": str(pd.Timestamp(row["trade_date"]).date()),
                "feature_manifest_hash": selection.FEATURE_MANIFEST_HASH}})
    return rows


def rolling_budget(open_positions: int) -> int:
    return 2 if open_positions < 5 else 1 if open_positions < 9 else 0


def candidate_outcome(row: Mapping[str, Any], sessions: list[str], bars: Mapping[str, Mapping[str, tuple]], cfg: Configuration) -> dict[str, Any] | None:
    index = {date: i for i, date in enumerate(sessions)}
    start = index.get(str(row["trade_date"]))
    if start is None: return None
    window = [bars.get(date, {}).get(row["canonical_security_id"]) for date in sessions[start + 1:start + 11]]
    x = ExecutionSimulator(cfg)
    if len(window) != 10 or not all(x.executable(bar) for bar in window): return None
    reference = window[0][0] * (1 + cfg.slippage)
    up = next((i for i, bar in enumerate(window) if bar[1] >= reference * 1.05), 999)
    down = next((i for i, bar in enumerate(window) if bar[2] <= reference * .97), 999)
    close_return = (window[-1][3] / reference - 1) * 100
    mae = (min(bar[2] for bar in window) / reference - 1) * 100
    return {"h10_net_return_pct": close_return, "close_return_pct": close_return,
        "mfe_pct": (max(bar[1] for bar in window) / reference - 1) * 100, "mae_pct": mae,
        "plus_5_before_minus_3": float(up < down),
        "realized_efficiency": close_return / max(abs(mae), .25)}


def _order(candidates: list[dict[str, Any]], policy: str, date: str, seed: int | None) -> list[dict[str, Any]]:
    if policy == "C0":
        return sorted(candidates, key=lambda row: (-int(row["signal_session"]), row["decision_id"]))
    if policy in {"C1", "C2"}:
        return selection.score_candidate_set(candidates, policy)
    event_id = selection.selection_event_id(date, [row["decision_id"] for row in candidates])
    rank = {oid: i for i, oid in enumerate(selection.random_orderings(event_id,
        [row["decision_id"] for row in candidates])[int(seed)], 1)}
    return sorted(candidates, key=lambda row: rank[row["decision_id"]])


def _portfolio_metrics(equity: list[dict[str, Any]], trades: list[dict[str, Any]], events: list[dict[str, Any]], cfg: Configuration) -> dict[str, Any]:
    e = pd.DataFrame(equity); t = pd.DataFrame(trades)
    nav = pd.concat([pd.Series([cfg.capital]), e.nav], ignore_index=True)
    returns = nav.pct_change().dropna(); std = returns.std(ddof=1); downside = np.sqrt(np.mean(np.minimum(returns, 0) ** 2))
    costs = float(t.cost.sum()) if len(t) else 0.
    gross = float((nav.iloc[-1] + costs) / cfg.capital - 1) * 100
    positive = t.assign(positive=t.net_pnl.clip(lower=0)) if len(t) else t
    gross_profit = float(positive.positive.sum()) if len(t) else 0.
    def share(column):
        return float(positive.groupby(column).positive.sum().max() / max(gross_profit, 1)) if len(t) else None
    return {"net_return_pct": float((nav.iloc[-1] / cfg.capital - 1) * 100), "gross_return_pct": gross,
        "max_drawdown_pct": float((nav / nav.cummax() - 1).min() * 100),
        "sharpe": float(returns.mean() / std * np.sqrt(252)) if std and std > 0 else 0.,
        "sortino": float(returns.mean() / downside * np.sqrt(252)) if downside and downside > 0 else None,
        "turnover": float(t.notional.sum() * 2 / e.nav.mean()) if len(t) else 0., "direct_costs": costs,
        "average_exposure": float(e.exposure.mean()), "cash_utilization": float((1 - e.cash / e.nav).mean()),
        "completed_trades": int(len(t)), "average_holding_period": float(t.holding_sessions.mean()) if len(t) else None,
        "capacity_blocks": int(sum(event["eligible_candidates"] > event["available_admissions"] for event in events)),
        "expired_queued_opportunities": int(sum(event.get("expired", 0) for event in events)),
        "top_1_signal_date_profit_share": share("signal_date"),
        "top_5_signal_date_profit_share": (float(positive.groupby("signal_date").positive.sum().nlargest(5).sum() / max(gross_profit, 1)) if len(t) else None),
        "top_10_signal_date_profit_share": (float(positive.groupby("signal_date").positive.sum().nlargest(10).sum() / max(gross_profit, 1)) if len(t) else None),
        "top_strategy_profit_share": share("strategy"), "top_sector_profit_share": share("sector")}


def replay(signals: list[dict[str, Any]], sessions: list[str], bars: Mapping[str, Mapping[str, tuple]],
           policy: str, seed: int | None = None, cfg: Configuration = Configuration()) -> dict[str, Any]:
    """Full path-dependent H10/T+1/B1/rolling replay for one frozen ordering."""
    if policy == "R0" and seed not in selection.RANDOM_SEEDS: raise ValueError("UNFROZEN_RANDOM_SEED")
    by_date = defaultdict(list)
    for row in signals: by_date[row["trade_date"]].append(dict(row))
    x = ExecutionSimulator(cfg); queue: dict[str, dict[str, Any]] = {}; seen = set()
    cash = cfg.capital; book = {}; pending_buys = []; pending_sells = []; trades = []; equity = []; events = []
    outcomes = {row["decision_id"]: candidate_outcome(row, sessions, bars, cfg) for row in signals}
    for session_index, date in enumerate(sessions):
        day = bars.get(date, {})
        # Frozen next-open exits first.
        keep_sells = []
        for order in pending_sells:
            position = book.get(order["sid"]); bar = day.get(order["sid"])
            if position is None: continue
            if not x.executable(bar): keep_sells.append(order); continue
            fill = x.sell(bar[0], position["quantity"]); cash += fill["proceeds"]
            cost = position["entry_fee"] + position["entry_slippage"] + fill["fee"] + fill["slippage_cost"]
            trades.append({"decision_id": position["decision_id"], "signal_date": position["signal_date"],
                "strategy": position["strategy"], "sector": position["sector"], "sid": order["sid"],
                "net_pnl": fill["proceeds"] - position["entry_cost"], "notional": position["entry_notional"],
                "cost": cost, "holding_sessions": position["age"]})
            del book[order["sid"]]
        pending_sells = keep_sells
        keep_buys = []
        for order in pending_buys:
            row = order["candidate"]; bar = day.get(row["canonical_security_id"])
            if session_index - row["signal_session"] > cfg.expiry: continue
            fill = x.buy(bar, min(order["budget"], cash)) if x.executable(bar) else None
            if fill is None: keep_buys.append(order); continue
            cash -= fill["cost"]; sid = row["canonical_security_id"]
            book[sid] = {"decision_id": row["decision_id"], "signal_date": row["trade_date"],
                "strategy": row["primary_strategy"], "sector": row["sector"], "quantity": fill["quantity"],
                "entry": fill["price"], "entry_cost": fill["cost"], "entry_notional": fill["quantity"] * fill["price"],
                "entry_fee": fill["fee"], "entry_slippage": fill["slippage_cost"], "mark": fill["price"],
                "age": 0, "entry_session": session_index, "risk_proxy_pct": row["selection_inputs"]["risk_proxy_pct"]}
            queue.pop(row["decision_id"], None)
        pending_buys = keep_buys
        for sid, position in list(book.items()):
            bar = day.get(sid); position["age"] = session_index - position["entry_session"] + 1
            if x.executable(bar): position["mark"] = bar[3]
            if position["age"] >= cfg.max_hold and not any(order["sid"] == sid for order in pending_sells):
                pending_sells.append({"sid": sid})
        for row in by_date.get(date, []):
            if row["decision_id"] not in seen:
                row["signal_session"] = session_index; queue[row["decision_id"]] = row; seen.add(row["decision_id"])
        expired = [oid for oid, row in queue.items() if session_index - row["signal_session"] >= cfg.expiry]
        for oid in expired: queue.pop(oid)
        start_positions = len(book); available = rolling_budget(start_positions)
        open_sids = set(book); pending_ids = {order["candidate"]["decision_id"] for order in pending_buys}; candidates = []
        for row in queue.values():
            inputs = row["selection_inputs"]
            row["ranking_eligible"] = bool(row["canonical_security_id"] not in open_sids and
                row["decision_id"] not in pending_ids and x.executable(day.get(row["canonical_security_id"])) and
                finite(row.get("traded_value")) is not None and row["traded_value"] >= cfg.minimum_traded_value and
                inputs.get("eligible") is True)
            if row["ranking_eligible"]: candidates.append(row)
        ordered = _order(candidates, policy, date, seed)
        nav = cash + sum(position["quantity"] * position["mark"] for position in book.values())
        virtual_cash = cash; virtual_book = {sid: dict(position) for sid, position in book.items()}
        admitted = []; decisions = []; used = 0; risk_block = False
        for row in ordered:
            risk_fraction = float(row["selection_inputs"]["risk_proxy_pct"]) / 100
            heat = sum(position["quantity"] * position["mark"] * float(position["risk_proxy_pct"]) / 100
                       for position in virtual_book.values())
            strategy_value = sum(position["quantity"] * position["mark"] for position in virtual_book.values()
                                 if position["strategy"] == row["primary_strategy"])
            ordinary = min(nav * cfg.max_stock_weight, virtual_cash - nav * cfg.reserve,
                nav * cfg.max_strategy_weight - strategy_value, row["traded_value"] * cfg.participation_limit)
            budget = min(ordinary, nav * TARGET_POSITION_RISK / risk_fraction,
                         max(0., nav * NORMAL_HEAT_LIMIT - heat) / risk_fraction) if risk_fraction > 0 else 0.
            if used >= available: budget = 0.; reason = "ROLLING_DAILY_ADMISSION_LIMIT"
            elif budget < day[row["canonical_security_id"]][3]:
                reason = "B1_RISK_BUDGET"; budget = 0.; risk_block = True
            else: reason = "ADMIT"
            if budget > 0:
                pending_buys.append({"candidate": row, "budget": budget}); admitted.append(row["decision_id"]); used += 1
                virtual_cash -= budget; virtual_book[row["canonical_security_id"]] = {
                    "quantity": 1, "mark": budget, "risk_proxy_pct": row["selection_inputs"]["risk_proxy_pct"],
                    "strategy": row["primary_strategy"]}
            decisions.append({"opportunity_id": row["decision_id"], "selected": budget > 0, "reason": reason,
                "admitted": budget > 0, "rank": len(decisions) + 1, "p0_score": -(len(decisions) + 1),
                "quality_score": row.get("quality_score"),
                "risk_adjusted_quality": row.get("risk_adjusted_quality"), "features": row["selection_inputs"]["features"],
                "outcome": outcomes.get(row["decision_id"]), "strategy": row["primary_strategy"],
                "sector": row["sector"], "signal_date": row["trade_date"]})
        constrained = len(candidates) > available or risk_block
        if candidates:
            events.append({"event_id": selection.selection_event_id(date, [row["decision_id"] for row in candidates]),
                "date": date, "policy": policy, "seed": seed, "eligible_candidates": len(candidates),
                "available_admissions": available, "risk_constrained": risk_block, "constrained": constrained,
                "selected": admitted, "candidates": decisions, "expired": len(expired)})
        exposure = sum(position["quantity"] * position["mark"] for position in book.values())
        equity.append({"date": date, "nav": cash + exposure, "cash": cash, "exposure": exposure})
    return {"version": VERSION, "label": LABEL, "policy": policy, "seed": seed,
        "metrics": _portfolio_metrics(equity, trades, events, cfg), "events": events, "trades": trades,
        "equity": equity, "result_hash": digest({"policy": policy, "seed": seed, "trades": trades, "equity": equity, "events": events})}


def event_diagnostics(result: Mapping[str, Any], score_field: str) -> dict[str, Any]:
    rows = [row for event in result["events"] if event["constrained"] for row in event["candidates"]
            if isinstance(row.get("outcome"), Mapping)]
    events = defaultdict(list)
    for event in result["events"]:
        if event["constrained"]:
            events[event["event_id"]].extend(row for row in event["candidates"] if isinstance(row.get("outcome"), Mapping))
    analytics = [selection.selection_attribution(values, score_field) for values in events.values()]
    metrics = ("h10_net_return_pct", "mfe_pct", "mae_pct", "plus_5_before_minus_3", "realized_efficiency")
    lift = {key: mean([float(a["selection_lift"][key]) for a in analytics
        if finite(a["selection_lift"].get(key)) is not None]) if any(finite(a["selection_lift"].get(key)) is not None for a in analytics) else None for key in metrics}
    regret = {key: mean([float(a["selection_regret"][key]) for a in analytics
        if finite(a["selection_regret"].get(key)) is not None]) if any(finite(a["selection_regret"].get(key)) is not None for a in analytics) else None
        for key in ("selected_minus_best_rejected", "selected_minus_average_rejected")}
    def rank_ic(outcome_key):
        vals = []
        for values in events.values():
            pair = [(finite(row.get(score_field)), finite((row.get("outcome") or {}).get(outcome_key))) for row in values]
            pair = [(x, y) for x, y in pair if x is not None and y is not None]
            if len(pair) >= 3:
                value = pd.Series([x for x, _ in pair]).corr(pd.Series([y for _, y in pair]), method="spearman")
                if pd.notna(value): vals.append(float(value))
        return mean(vals) if vals else None
    ordered = sorted((row for row in rows if finite(row.get(score_field)) is not None),
                     key=lambda row: float(row[score_field]), reverse=True)
    groups = 4 if len(ordered) >= 8 else 2 if len(ordered) >= 4 else 0; buckets = []
    for index, subset in enumerate(np.array_split(ordered, groups), 1) if groups else []:
        buckets.append({"bucket": index, "n": len(subset), **{key: mean([float(row["outcome"][key]) for row in subset]) for key in metrics}})
    return {"constrained_events": len(events), "mature_candidates": len(rows), "selection_lift": lift,
        "selection_regret": regret, "rank_ic": {key: rank_ic(key) for key in ("h10_net_return_pct", "mfe_pct", "mae_pct")},
        "rank_buckets": buckets}


def classify(metrics: Mapping[str, Any], control: Mapping[str, Any], random_summary: Mapping[str, Any], diagnostic: Mapping[str, Any]) -> str:
    ret = metrics["net_return_pct"]; lift = diagnostic["selection_lift"].get("h10_net_return_pct")
    success_lift = diagnostic["selection_lift"].get("plus_5_before_minus_3")
    ic = diagnostic["rank_ic"].get("h10_net_return_pct")
    if ret > control["net_return_pct"] and ret > random_summary["net_return_pct"]["p75"] and (lift or 0) > 0 and (ic or 0) > 0:
        return "CLEAR HISTORICAL SELECTION VALUE"
    # Portfolio outperformance alone can be path luck.  If both direct
    # selection outcomes fail the intended direction, the ranker has not shown
    # historical selection value even when its one realized portfolio did well.
    if (lift or 0) <= 0 and (success_lift or 0) <= 0:
        return "NO HISTORICAL SELECTION VALUE"
    if ret > control["net_return_pct"] or ret > random_summary["net_return_pct"]["median"] or (lift or 0) > 0 or (ic or 0) > 0:
        return "WEAK / MIXED SELECTION VALUE"
    return "NO HISTORICAL SELECTION VALUE"
