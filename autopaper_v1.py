"""Deterministic paper-only daily-bar simulation. No production entrypoint imports this module.

Signals are observed at close, orders are fixed then and resolve on later sessions.
Decision engines receive current state only. Outcome tracking is a separate pass.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
import math
from typing import Any

import numpy as np
import pandas as pd

VERSION = "AutoPaper_V1"


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()


def number(row, key, default):
    try:
        value = float(row.get(key, default))
        return value if math.isfinite(value) else default
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class Configuration:
    capital: float = 1_000_000
    max_positions: int = 10
    max_stock_weight: float = .10
    max_sector_weight: float = 1.0  # PIT sectors unavailable: explicitly unconstrained in V1.
    max_strategy_weight: float = .60
    reserve: float = .10
    minimum_traded_value: float = 20_000_000
    participation_limit: float = .01
    expiry: int = 2
    max_hold: int = 10
    stop_pct: float = .03
    target_pct: float = .05
    trail_activation: float = .03
    trail_distance: float = .02
    slippage: float = .0005
    brokerage_rate: float = .0015
    brokerage_cap: float = 20
    sell_stt: float = .001
    paper_only: bool = True
    enabled: bool = False


@dataclass(frozen=True)
class Policy:
    name: str
    priority: str = "P0"
    exit: str = "E0"
    replacement_hurdle: float | None = None


@dataclass(frozen=True)
class PortfolioControl:
    name: str = "C0"
    drawdown_throttle: bool = False
    volatility_sizing: bool = False
    caution_drawdown: float = -.10
    defensive_drawdown: float = -.18
    caution_recover: float = -.05
    defensive_recover: float = -.12
    caution_slots: int = 7
    defensive_slots: int = 3
    caution_size: float = .70
    defensive_size: float = .30
    target_atr_pct: float = 4.0
    minimum_vol_multiplier: float = .50


@dataclass(frozen=True)
class ClusterPortfolioControl(PortfolioControl):
    """V2D-only extensions; PortfolioControl serialization remains unchanged."""
    cluster_mode: str = "D1_CAP"
    date_commitment_cap: float = .20
    date_scale_trigger: float = .30
    date_scaled_target: float = .20


POLICIES = (
    Policy("B1_P0_E0"),
    Policy("P3_E0", "P3"),
    Policy("P3_E1", "P3", "E1"),
    Policy("P3_E1_REPLACE_1", "P3", "E1", 1.0),
)
B0 = Policy("B0_VOLUME_FIXED10", "VOLUME", "TIME")


class DecisionJournal:
    def __init__(self, methodology_hash):
        self.methodology_hash = methodology_hash
        self.rows = []
        self.orders = set()

    def record(self, date, action, opportunity, reason, cash, book, **extra):
        self.rows.append({"sequence": len(self.rows), "timestamp": str(date), "action": action,
                          "opportunity_id": opportunity, "reason": reason, "cash": cash,
                          "portfolio_snapshot": {k: {"quantity": p["quantity"], "mark": p["mark"],
                          "entry": p["entry"], "age": p["age"]} for k, p in sorted(book.items())},
                          "methodology_hash": self.methodology_hash, **extra})

    def order(self, order):
        if order["order_id"] in self.orders:
            return False
        self.orders.add(order["order_id"])
        return True


class OpportunityQueue:
    def __init__(self):
        self.rows = {}
        self.seen = set()

    def ingest(self, rows, session):
        for row in rows:
            oid = row["decision_id"]
            if oid not in self.seen:
                self.rows[oid] = {**row, "signal_session": session}
                self.seen.add(oid)

    def expire(self, session, expiry):
        # Last valid fill is signal session + expiry; no new order on that close.
        expired = [v for v in self.rows.values() if session - v["signal_session"] >= expiry]
        for row in expired:
            self.rows.pop(row["decision_id"])
        return expired


class PriorityEngine:
    @staticmethod
    def ordered(rows, book, policy):
        counts = {}
        for p in book.values():
            counts[p["strategy"]] = counts.get(p["strategy"], 0) + 1
        def number(row, name, missing):
            value = float(row.get(name, missing))
            return value if math.isfinite(value) else missing
        def key(row):
            freshness = (-row["signal_session"], row["decision_id"])
            if policy.priority.startswith("RANDOM_"):
                # Seed is part of the policy identity. Hash ordering is stable
                # across processes and uses no outcome or future observation.
                seed = policy.priority.removeprefix("RANDOM_")
                return (digest([seed, row["trade_date"], row["decision_id"]]),)
            if policy.priority == "B1":
                return (-number(row, "volume_ratio", 0),
                        -number(row, "rs_percentile_20d", 0), *freshness)
            if policy.priority == "B2":
                return (-int(number(row, "close_location_value", -math.inf) >= .70),
                        -number(row, "rs_percentile_20d", 0),
                        number(row, "atr_pct", math.inf), *freshness)
            if policy.priority == "VOLUME":
                return (-float(row["volume_ratio"]), *freshness)
            if policy.priority == "P3":
                return (counts.get(row["primary_strategy"], 0), *freshness)
            return freshness
        return sorted(rows, key=key)


class PortfolioRiskEngine:
    @staticmethod
    def nav(cash, book):
        return cash + sum(p["quantity"] * p["mark"] for p in book.values())

    @staticmethod
    def budget(row, cash, book, cfg):
        nav = PortfolioRiskEngine.nav(cash, book)
        sid = row["canonical_security_id"]
        if sid in book:
            return 0., "DUPLICATE_OPEN_POSITION"
        if len(book) >= cfg.max_positions:
            return 0., "NO_CAPITAL"
        traded = float(row.get("traded_value", float("nan")))
        if not math.isfinite(traded) or traded < cfg.minimum_traded_value:
            return 0., "RISK_LIMIT"
        strategy_value = sum(p["quantity"] * p["mark"] for p in book.values() if p["strategy"] == row["primary_strategy"])
        sector = row.get("sector", "UNKNOWN")
        sector_value = sum(p["quantity"] * p["mark"] for p in book.values() if p.get("sector", "UNKNOWN") == sector)
        concentration = min(nav * cfg.max_strategy_weight - strategy_value, nav * cfg.max_sector_weight - sector_value)
        budget = min(nav * cfg.max_stock_weight, cash - nav * cfg.reserve, concentration, traded * cfg.participation_limit)
        if budget <= 0:
            return 0., "PORTFOLIO_CONCENTRATION" if concentration <= 0 else "NO_CAPITAL"
        return float(budget), "ELIGIBLE"


class PortfolioControlEngine:
    def __init__(self, control, capital):
        self.control = control or PortfolioControl()
        self.state = "NORMAL"
        self.peak_nav = capital

    def observe(self, nav):
        self.peak_nav = max(self.peak_nav, nav)
        drawdown = nav / self.peak_nav - 1
        old = self.state
        if self.control.drawdown_throttle:
            tolerance = 1e-12
            if self.state == "NORMAL" and drawdown <= self.control.caution_drawdown + tolerance:
                self.state = "CAUTION"
            if drawdown <= self.control.defensive_drawdown + tolerance:
                self.state = "DEFENSIVE"
            elif self.state == "DEFENSIVE" and drawdown >= self.control.defensive_recover - tolerance:
                self.state = "CAUTION"
            if self.state == "CAUTION" and drawdown >= self.control.caution_recover - tolerance:
                self.state = "NORMAL"
        return {"state": self.state, "previous_state": old, "drawdown": drawdown,
                "transition": old != self.state}

    def adjust(self, row, budget, positions, nav=None, cluster_commitment=0., cluster_scale=1.):
        multiplier = 1.0
        slots = math.inf
        if self.state == "CAUTION":
            slots, multiplier = self.control.caution_slots, self.control.caution_size
        elif self.state == "DEFENSIVE":
            slots, multiplier = self.control.defensive_slots, self.control.defensive_size
        if len(positions) >= slots:
            return 0., "PORTFOLIO_THROTTLE_SUPPRESSED", multiplier
        if self.control.volatility_sizing:
            atr = number(row, "atr_pct", math.inf)
            vol_multiplier = max(self.control.minimum_vol_multiplier,
                                 min(1., self.control.target_atr_pct / atr)) if atr > 0 else 0.
            multiplier *= vol_multiplier
        if getattr(self.control, "cluster_mode", None) == "D2_SCALE":
            multiplier *= cluster_scale
        adjusted = budget * multiplier
        if getattr(self.control, "cluster_mode", None) == "D1_CAP":
            room = max(0., nav * self.control.date_commitment_cap - cluster_commitment)
            adjusted = min(adjusted, room)
        return adjusted, "PORTFOLIO_CONTROL_DOWNSIZED" if adjusted < budget else "ELIGIBLE", multiplier

    def cluster_scales(self, rows, cash, book, cfg):
        if getattr(self.control, "cluster_mode", None) != "D2_SCALE":
            return {}
        nav = PortfolioRiskEngine.nav(cash, book)
        committed = {}
        for position in book.values():
            date = position.get("signal_date")
            committed[date] = committed.get(date, 0.) + position.get(
                "entry_cost", position["quantity"] * position["mark"])
        intended = dict(committed)
        for row in rows:
            budget, _ = PortfolioRiskEngine.budget(row, cash, book, cfg)
            if budget <= 0:
                continue
            atr = number(row, "atr_pct", math.inf)
            vol = max(self.control.minimum_vol_multiplier,
                      min(1., self.control.target_atr_pct / atr)) if atr > 0 else 0.
            date = row["trade_date"]
            intended[date] = intended.get(date, 0.) + budget * vol
        return {date: self.control.date_scaled_target * nav / amount
                for date, amount in intended.items()
                if amount > self.control.date_scale_trigger * nav}


class ExecutionSimulator:
    def __init__(self, cfg):
        if not cfg.paper_only:
            raise ValueError("PAPER_ONLY_GUARD")
        self.cfg = cfg

    @staticmethod
    def executable(bar):
        if bar is None or len(bar) < 6:
            return False
        o, h, l, c, v, traded = bar[:6]
        return all(math.isfinite(x) and x > 0 for x in (o, h, l, c, v, traded)) and h > l and l <= min(o, c) <= max(o, c) <= h

    def fee(self, value, sell=False):
        return min(self.cfg.brokerage_cap, value * self.cfg.brokerage_rate) + (value * self.cfg.sell_stt if sell else 0)

    def buy(self, bar, budget):
        if not self.executable(bar):
            return None
        price = bar[0] * (1 + self.cfg.slippage)
        # Participation budget is fixed from signal-session traded value, never
        # resized using the not-yet-known execution session's aggregate volume.
        quantity = int(budget / price)
        while quantity > 0 and quantity * price + self.fee(quantity * price) > budget:
            quantity -= 1
        if quantity <= 0:
            return None
        value = quantity * price
        return {"quantity": quantity, "price": price, "fee": self.fee(value), "cost": value + self.fee(value),
                "slippage_cost": quantity * (price - bar[0])}

    def sell(self, reference, quantity):
        price = reference * (1 - self.cfg.slippage)
        value = price * quantity
        return {"price": price, "fee": self.fee(value, True), "proceeds": value - self.fee(value, True),
                "slippage_cost": quantity * (reference - price)}


class PositionStateEngine:
    @staticmethod
    def update(position, bar, index):
        position["age"] = index - position["entry_session"] + 1
        position["mark"] = bar[3]
        position["peak"] = max(position["peak"], bar[1])
        position["trough"] = min(position["trough"], bar[2])
        position["pnl_pct"] = (bar[3] / position["entry"] - 1) * 100
        position["mfe_pct"] = (position["peak"] / position["entry"] - 1) * 100
        position["mae_pct"] = (position["trough"] / position["entry"] - 1) * 100


class ExitEngine:
    @staticmethod
    def barrier(position, bar, policy, cfg):
        if policy.exit == "TIME":
            return None
        if policy.exit == "A1":
            stop = position["entry"] * .90
            if bar[0] <= stop:
                return bar[0], "GAP_CATASTROPHE_STOP"
            return (stop, "CATASTROPHE_STOP") if bar[2] <= stop else None
        if policy.exit == "A2":
            if position["age"] <= 3:
                return None
            stop = position["entry"] * .94
            if bar[0] <= stop:
                return bar[0], "GAP_DELAYED_STOP"
            return (stop, "DELAYED_STOP") if bar[2] <= stop else None
        if policy.exit == "A3":
            stop = position.get("protection_stop")
            if stop is None:
                return None
            if bar[0] <= stop:
                return bar[0], "GAP_WINNER_PROTECTION"
            return (stop, "WINNER_PROTECTION") if bar[2] <= stop else None
        stop = position["stop"]  # Includes trailing level fixed at previous close only.
        target = position["entry"] * (1 + cfg.target_pct)
        if bar[0] <= stop:
            return bar[0], "GAP_STOP"
        if policy.exit == "E0" and bar[0] >= target:
            return bar[0], "GAP_TARGET"
        if bar[2] <= stop:
            return stop, "STOP"  # Conservative adverse first when both barriers touch.
        if policy.exit == "E0" and bar[1] >= target:
            return target, "TARGET"
        return None

    @staticmethod
    def after_close(position, policy, cfg):
        if policy.exit == "A3" and position["peak"] >= position["entry"] * 1.08:
            position["protection_stop"] = max(position.get("protection_stop", 0), position["peak"] * .96)
        if policy.exit == "E1" and position["peak"] >= position["entry"] * (1 + cfg.trail_activation):
            position["stop"] = max(position["stop"], position["peak"] * (1 - cfg.trail_distance))
        return "TIME_EXIT" if position["age"] >= cfg.max_hold else "HOLD"


class ReplacementEngine:
    @staticmethod
    def evaluate(candidate, book, session, cfg, policy):
        if policy.replacement_hurdle is None or not book:
            return None
        # Percentage-point opportunity-room proxy, explicitly not an expected-return model.
        candidate_value = 5.0 - 2.0 * (session - candidate["signal_session"]) / max(cfg.expiry, 1)
        values = []
        for sid, p in book.items():
            remaining = max(0., 5. - p.get("pnl_pct", 0.))
            giveback = max(0., p.get("mfe_pct", 0.) - p.get("pnl_pct", 0.))
            protection = max(0., (p["stop"] / p["entry"] - 1) * 100)
            hold_value = remaining + protection - 2. * p["age"] / cfg.max_hold - .25 * giveback
            values.append((hold_value, sid))
        hold_value, sid = min(values)
        friction = 100 * (2 * cfg.slippage + cfg.sell_stt) + 2 * cfg.brokerage_cap / max(cfg.capital * cfg.max_stock_weight, 1) * 100
        gain = candidate_value - hold_value
        return {"sid": sid, "candidate_value": candidate_value, "hold_value": hold_value,
                "gain": gain, "hurdle": policy.replacement_hurdle + friction} if gain > policy.replacement_hurdle + friction else None


class PaperReplay:
    def __init__(self, cfg=Configuration()):
        self.cfg = cfg
        self.execution = ExecutionSimulator(cfg)

    def run(self, signals, sessions, bars, policy, end_new_entries=None, portfolio_control=None):
        cfg = self.cfg
        control = portfolio_control or PortfolioControl()
        methodology_payload = {"version": VERSION, "config": asdict(cfg), "policy": asdict(policy)}
        if portfolio_control is not None:
            methodology_payload["portfolio_control"] = asdict(control)
        methodology = digest(methodology_payload)
        journal = DecisionJournal(methodology)
        controller = PortfolioControlEngine(control, cfg.capital)
        queue = OpportunityQueue()
        groups = {}
        for row in signals:
            groups.setdefault(row["trade_date"], []).append(row)
        cash = cfg.capital
        book, pending, trades, equity, fills = {}, [], [], [], []
        missing_marks = set()
        completed_sells = set()
        final_reason = {}
        decision_reasons = {}
        control_events = []
        control_suppressed, control_downsized = set(), set()
        def log(date, action, oid, reason, **kw):
            journal.record(date, action, oid, reason, cash, book, **kw)
            if action in ("QUEUE", "REJECT", "ENTER"):
                final_reason[oid] = reason
                if reason not in ("STALE", "ORDER_SUBMITTED_NEXT_OPEN", "ENTERED"):
                    decision_reasons.setdefault(oid, reason)
        def close(sid, date, reference, reason, order_id, replacement=None):
            nonlocal cash
            p = book.pop(sid)
            fill = self.execution.sell(reference, p["quantity"])
            cash += fill["proceeds"]
            # Guaranteed observed excursion only; daily bars do not reveal all
            # prices before an intrabar exit. Never use the exit bar's later high.
            p["mfe_pct"] = (max(p["peak"], fill["price"]) / p["entry"] - 1) * 100
            p["mae_pct"] = (min(p["trough"], fill["price"]) / p["entry"] - 1) * 100
            p["excursion_semantics"] = "OBSERVED_PRE_EXIT_LOWER_BOUND_NOT_FULL_EXIT_BAR"
            trade = {**p, "exit_date": date, "exit": fill["price"], "exit_fee": fill["fee"],
                     "exit_slippage": fill["slippage_cost"], "net_pnl": fill["proceeds"] - p["entry_cost"],
                     "net_return_pct": (fill["proceeds"] / p["entry_cost"] - 1) * 100,
                     "exit_reason": reason, "exit_order_id": order_id, "replacement_candidate": replacement}
            trades.append(trade)
            completed_sells.add(order_id)
            fills.append({"order_id": order_id, "fill_id": order_id + ":FILL", "date": date, "side": "SELL", "quantity": p["quantity"], **fill})
            log(date + "TSESSION", "EXIT", p["decision_id"], reason, order_id=order_id, fill_id=order_id + ":FILL", price=fill["price"], cash_delta=fill["proceeds"])
        for index, date in enumerate(sessions):
            day = bars.get(date, {})
            # Resolve previously submitted sells before previously submitted buys.
            old_orders, pending = pending, []
            for order in sorted(old_orders, key=lambda o: (o["side"] != "SELL", o["sequence"])):
                sid = order["sid"]
                bar = day.get(sid)
                if order["side"] == "SELL":
                    if sid not in book:
                        continue
                    if not self.execution.executable(bar):
                        pending.append(order)
                        continue
                    close(sid, date, bar[0], order["reason"], order["order_id"], order.get("replacement_candidate"))
                else:
                    row = order["candidate"]
                    if order.get("depends_on") and order["depends_on"] not in completed_sells:
                        log(date, "QUEUE", row["decision_id"], "REPLACEMENT_EXIT_UNFILLED")
                        continue
                    if index - row["signal_session"] > cfg.expiry:
                        log(date, "REJECT", row["decision_id"], "STALE", order_id=order["order_id"])
                        continue
                    if any(p["age"] and sid0 not in day for sid0, p in book.items()):
                        log(date, "REJECT", row["decision_id"], "INCOMPLETE_PORTFOLIO_MARKS")
                        continue
                    budget, reason = PortfolioRiskEngine.budget(row, cash, book, cfg)
                    fill = self.execution.buy(bar, min(order["budget"], budget)) if budget > 0 else None
                    if fill is None:
                        log(date, "QUEUE", row["decision_id"], reason if budget <= 0 else "INCOMPLETE_EXECUTION_DATA")
                        continue
                    cash -= fill["cost"]
                    book[sid] = {"decision_id": row["decision_id"], "signal_date": row["trade_date"], "sid": sid,
                                 "symbol": row["symbol"], "strategy": row["primary_strategy"], "sector": row.get("sector", "UNKNOWN"),
                                 "entry_date": date, "entry_session": index, "entry": fill["price"],
                                 "quantity": fill["quantity"], "entry_cost": fill["cost"], "entry_fee": fill["fee"],
                                 "entry_slippage": fill["slippage_cost"], "entry_order_id": order["order_id"], "mark": fill["price"],
                                 "peak": fill["price"], "trough": fill["price"], "age": 0,
                                 "stop": fill["price"] * (1 - cfg.stop_pct), "path_risk": "NOT_AVAILABLE", "market_context": "NOT_AVAILABLE"}
                    fills.append({"order_id": order["order_id"], "fill_id": order["order_id"] + ":FILL", "date": date, "side": "BUY", **fill})
                    queue.rows.pop(row["decision_id"], None)
                    log(date + "TOPEN", "ENTER", row["decision_id"], "ENTERED", order_id=order["order_id"], fill_id=order["order_id"] + ":FILL", cash_delta=-fill["cost"])
            for sid, p in list(book.items()):
                bar = day.get(sid)
                p["age"] = index - p["entry_session"] + 1
                if not self.execution.executable(bar):
                    missing_marks.add((date, sid))
                    continue
                p["age"] = index - p["entry_session"] + 1
                exit_hit = ExitEngine.barrier(p, bar, policy, cfg)
                if exit_hit:
                    # Intrabar order is pre-existing; do not infer excursion ordering within exit bar.
                    close(sid, date, exit_hit[0], exit_hit[1], p["entry_order_id"] + ":BARRIER:" + date)
                    continue
                PositionStateEngine.update(p, bar, index)
                action = ExitEngine.after_close(p, policy, cfg)
                if action == "TIME_EXIT" and not any(o["side"] == "SELL" and o["sid"] == sid for o in pending):
                    order = {"side": "SELL", "sid": sid, "reason": "TIME_EXIT", "sequence": len(journal.rows), "order_id": p["entry_order_id"] + ":TIME"}
                    if journal.order(order):
                        pending.append(order)
                    log(date + "TCLOSE", "HOLD", p["decision_id"], "TIME_EXIT_SUBMITTED_NEXT_OPEN", order_id=order["order_id"])
                else:
                    log(date + "TCLOSE", "HOLD", p["decision_id"], action)
            queue.ingest(groups.get(date, []), index)
            for row in queue.expire(index, cfg.expiry):
                log(date + "TCLOSE", "REJECT", row["decision_id"], "STALE")
            control_state = controller.observe(PortfolioRiskEngine.nav(cash, book))
            gross_exposure = sum(p["quantity"] * p["mark"] for p in book.values())
            control_events.append({"date": date, **control_state, "gross_exposure": gross_exposure,
                                   "positions": len(book), "entries_suppressed": 0, "entries_downsized": 0})
            if end_new_entries is None or date <= end_new_entries:
                virtual = {sid: dict(p) for sid, p in book.items()}
                virtual_cash = cash
                replacement_used = False
                ordered_rows = PriorityEngine.ordered(queue.rows.values(), book, policy)
                cluster_scales = controller.cluster_scales(ordered_rows, cash, book, cfg)
                for row in ordered_rows:
                    oid, sid = row["decision_id"], row["canonical_security_id"]
                    dependency = None
                    if any(p.get("candidate", {}).get("decision_id") == oid for p in pending):
                        continue
                    budget, reason = PortfolioRiskEngine.budget(row, virtual_cash, virtual, cfg)
                    if budget > 0:
                        virtual_nav = PortfolioRiskEngine.nav(virtual_cash, virtual)
                        committed = sum(p.get("entry_cost", p["quantity"] * p["mark"])
                                        for p in virtual.values()
                                        if p.get("signal_date") == row["trade_date"])
                        budget, control_reason, multiplier = controller.adjust(
                            row, budget, virtual, virtual_nav, committed,
                            cluster_scales.get(row["trade_date"], 1.))
                        if control_reason == "PORTFOLIO_THROTTLE_SUPPRESSED":
                            control_events[-1]["entries_suppressed"] += 1
                            control_suppressed.add(oid)
                            reason = control_reason
                        elif control_reason == "PORTFOLIO_CONTROL_DOWNSIZED":
                            control_events[-1]["entries_downsized"] += 1
                            control_downsized.add(oid)
                            reason = control_reason
                    if any(s not in day or not self.execution.executable(day[s]) for s in book):
                        budget, reason = 0, "INCOMPLETE_PORTFOLIO_MARKS"
                    if budget <= 0 and len(virtual) >= cfg.max_positions and not replacement_used:
                        replacement = ReplacementEngine.evaluate(row, book, index, cfg, policy)
                        if replacement and not any(o["side"] == "SELL" for o in pending):
                            victim = replacement["sid"]
                            released = virtual.pop(victim)
                            virtual_cash += released["quantity"] * released["mark"] * (1 - cfg.slippage - cfg.sell_stt) - cfg.brokerage_cap
                            budget, reason = PortfolioRiskEngine.budget(row, virtual_cash, virtual, cfg)
                            if budget > 0:
                                order = {"side": "SELL", "sid": victim, "reason": "REPLACE", "sequence": len(journal.rows),
                                         "order_id": digest([methodology, date, victim, oid, "REPLACE"]), "replacement_candidate": oid}
                                if journal.order(order):
                                    pending.append(order)
                                dependency = order["order_id"]
                                log(date + "TCLOSE", "REPLACE", oid, "REPLACEMENT_HURDLE_PASSED", **replacement)
                                replacement_used = True
                            else:
                                virtual[victim] = released
                                virtual_cash -= released["quantity"] * released["mark"] * (1 - cfg.slippage - cfg.sell_stt) - cfg.brokerage_cap
                        else:
                            reason = "REPLACEMENT_HURDLE_NOT_MET" if policy.replacement_hurdle is not None else "LOWER_PRIORITY"
                    if budget <= 0:
                        log(date + "TCLOSE", "QUEUE", oid, reason)
                        continue
                    order = {"side": "BUY", "sid": sid, "candidate": row, "budget": budget, "sequence": len(journal.rows),
                             "order_id": digest([methodology, date, oid, "BUY"]), "depends_on": dependency}
                    if journal.order(order):
                        pending.append(order)
                    log(date + "TCLOSE", "QUEUE", oid, "ORDER_SUBMITTED_NEXT_OPEN", order_id=order["order_id"], budget=budget, priority=policy.priority)
                    virtual_cash -= budget
                    virtual[sid] = {"quantity": 1, "mark": budget, "entry_cost": budget,
                                    "signal_date": row["trade_date"],
                                    "strategy": row["primary_strategy"], "sector": row.get("sector", "UNKNOWN")}
            if cash < -1e-7:
                raise AssertionError("NEGATIVE_CASH")
            equity_row = {"date": date, "nav": PortfolioRiskEngine.nav(cash, book), "cash": cash, "positions": len(book)}
            if portfolio_control is not None:
                equity_row.update(control_state=controller.state,
                                  gross_exposure=sum(p["quantity"] * p["mark"] for p in book.values()))
            equity.append(equity_row)
        result = {"policy": asdict(policy), "methodology_hash": methodology, "trades": trades, "equity": equity,
                "journal": journal.rows, "fills": fills, "open_positions": list(book.values()), "queue": list(queue.rows.values()),
                "missing_marks": len(missing_marks), "final_reason": final_reason, "decision_reasons": decision_reasons,
                "health": {"status": "INCOMPLETE_DATA" if missing_marks else "COMPLETED", "live_enabled": False},
                "journal_hash": digest(journal.rows)}
        if portfolio_control is not None:
            result.update(portfolio_control=asdict(control), control_events=control_events,
                          control_suppressed=sorted(control_suppressed), control_downsized=sorted(control_downsized))
        return result


class CounterfactualTracker:
    """Post-replay only: uses future bars for evaluation, never an order decision."""
    @staticmethod
    def observe(signals, sessions, bars, result, cfg):
        index = {d: i for i, d in enumerate(sessions)}
        entered = {t["decision_id"] for t in result["trades"] + result["open_positions"]}
        rows = []
        for s in signals:
            start = index[s["trade_date"]] + 1
            sid = s["canonical_security_id"]
            for horizon in (10, 20):
                window = [bars.get(d, {}).get(sid) for d in sessions[start:start + horizon]]
                row = {"decision_id": s["decision_id"], "horizon": horizon, "entered": s["decision_id"] in entered,
                       "reason": result["decision_reasons"].get(s["decision_id"], result["final_reason"].get(s["decision_id"], "NO_CAPITAL")),
                       "terminal_reason": result["final_reason"].get(s["decision_id"]), "status": "NOT_AVAILABLE"}
                if len(window) == horizon and all(ExecutionSimulator.executable(b) for b in window):
                    reference = window[0][0] * (1 + cfg.slippage)
                    up = next((i for i, b in enumerate(window) if b[1] >= reference * 1.05), 999)
                    down = next((i for i, b in enumerate(window) if b[2] <= reference * .97), 999)
                    row.update(status="AVAILABLE", mfe_pct=(max(b[1] for b in window) / reference - 1) * 100,
                               mae_pct=(min(b[2] for b in window) / reference - 1) * 100,
                               close_return_pct=(window[-1][3] / reference - 1) * 100, success=float(up < down))
                rows.append(row)
        return rows


class PolicyEvaluator:
    @staticmethod
    def metrics(result, cfg):
        e = pd.DataFrame(result["equity"])
        t = pd.DataFrame(result["trades"])
        nav = pd.concat([pd.Series([cfg.capital]), e.nav], ignore_index=True)
        returns = nav.pct_change().dropna()
        mean, std = returns.mean(), returns.std(ddof=1)
        annual = (nav.iloc[-1] / cfg.capital) ** (252 / max(len(e), 1)) - 1
        dd = (nav / nav.cummax() - 1).min()
        downside = np.sqrt(np.mean(np.minimum(returns, 0) ** 2))
        pnl = t.net_pnl if len(t) else pd.Series(dtype=float)
        gross_profit, gross_loss = pnl[pnl > 0].sum(), -pnl[pnl < 0].sum()
        m = {"return_pct": float((nav.iloc[-1] / cfg.capital - 1) * 100), "annual_return_pct": float(annual * 100),
             "volatility_pct": float(std * np.sqrt(252) * 100), "sharpe": float(mean / std * np.sqrt(252)) if std > 0 else 0.,
             "sortino": float(mean / downside * np.sqrt(252)) if downside > 0 else None,
             "max_drawdown_pct": float(dd * 100), "calmar": float(annual / abs(dd)) if dd else None,
             "profit_factor": float(gross_profit / gross_loss) if gross_loss > 0 else None,
             "closed_trades": len(t), "hit_rate": float((pnl > 0).mean()) if len(t) else None,
             "cash_utilization": float((1 - e.cash / e.nav).mean()), "open_positions_at_end": len(result["open_positions"]),
             "missing_marks": result["missing_marks"], "journal_hash": result["journal_hash"]}
        if len(t):
            costs = t.entry_fee + t.exit_fee + t.entry_slippage + t.exit_slippage
            m.update(mean_trade_return=float(t.net_return_pct.mean()), median_trade_return=float(t.net_return_pct.median()),
                     median_observed_mfe=float(t.mfe_pct.median()), median_observed_mae=float(t.mae_pct.median()),
                     mean_giveback_pct=float((t.mfe_pct - t.net_return_pct).clip(lower=0).mean()),
                     capture_ratio=float(t.net_return_pct.sum() / t.mfe_pct.sum()) if t.mfe_pct.sum() > 0 else None,
                     average_hold=float(t.age.mean()), total_cost=float(costs.sum()),
                     turnover=float(((t.entry * t.quantity).sum() + (t.exit * t.quantity).sum()) / e.nav.mean()),
                     stop_frequency=float(t.exit_reason.str.contains("STOP").mean()), time_exit_frequency=float(t.exit_reason.eq("TIME_EXIT").mean()),
                     replacements=int(t.exit_reason.eq("REPLACE").sum()),
                     top_symbol_profit_share=float(t.assign(positive=t.net_pnl.clip(lower=0)).groupby("sid").positive.sum().max() / max(gross_profit, 1)),
                     top_date_profit_share=float(t.assign(positive=t.net_pnl.clip(lower=0)).groupby("signal_date").positive.sum().max() / max(gross_profit, 1)),
                     top_strategy_profit_share=float(t.assign(positive=t.net_pnl.clip(lower=0)).groupby("strategy").positive.sum().max() / max(gross_profit, 1)))
        return m


class ResearchStore:
    """Atomic, idempotent isolated replay storage; never uses the live paper ledger."""
    def __init__(self, path):
        import sqlite3
        self.db = sqlite3.connect(path)
        self.db.execute("CREATE TABLE IF NOT EXISTS runs (run_id TEXT PRIMARY KEY, content_hash TEXT NOT NULL, payload TEXT NOT NULL)")

    def persist(self, run_id, result, paper_only=True, enabled=False):
        if not paper_only:
            raise ValueError("PAPER_ONLY_GUARD")
        if enabled:
            raise ValueError("PROSPECTIVE_ACTIVATION_NOT_AUTHORIZED_BY_HISTORICAL_RESULT")
        payload = json.dumps(result, sort_keys=True, default=str)
        identity = digest(result)
        with self.db:
            old = self.db.execute("SELECT content_hash FROM runs WHERE run_id=?", (run_id,)).fetchone()
            if old:
                if old[0] != identity:
                    raise ValueError("IMMUTABLE_RUN_CONFLICT")
                return False
            self.db.execute("INSERT INTO runs VALUES (?,?,?)", (run_id, identity, payload))
        return True

    def close(self):
        self.db.close()
