import os
import pandas as pd
import numpy as np
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "."))


@dataclass
class Position:
    position_id: str
    symbol: str
    strategy_names: List[str]
    ml_probability: float
    entry_date: str
    entry_price: float
    position_size: float
    quantity: int
    days_held: int = 0


@dataclass
class PortfolioState:
    initial_capital: float = 1000000.0
    position_size: float = 100000.0
    max_positions: int = 10
    cash: float = 1000000.0
    active_positions: List[Position] = field(default_factory=list)
    portfolio_value: float = 1000000.0


class PortfolioEngine:
    def __init__(
        self,
        initial_capital: float = 1000000.0,
        position_size: float = 100000.0,
        max_positions: int = 10,
        max_holding_days: int = 10,
        slot_policy: str = "hold_to_expiry",  # "hold_to_expiry" or "replace_if_superior"
        replacement_margin: float = 0.10,    # min score difference to trigger replacement
        cost_multiplier: float = 1.0
    ):
        self.initial_capital = initial_capital
        self.position_size = position_size
        self.max_positions = max_positions
        self.max_holding_days = max_holding_days
        self.slot_policy = slot_policy
        self.replacement_margin = replacement_margin

        # Transaction cost & slippage configuration
        self.brok_pct = 0.0003 * cost_multiplier
        self.stt_pct = 0.0010 * cost_multiplier
        self.slip_pct = 0.0010 * cost_multiplier

        self.reset()

    def reset(self):
        self.cash = self.initial_capital
        self.active_positions: List[Position] = []
        self.portfolio_ledger: List[Dict[str, Any]] = []
        self.executed_trades: List[Dict[str, Any]] = []
        self.daily_equities: List[Dict[str, Any]] = []
        self.position_counter = 0

    def compute_transaction_cost(self, gross_val: float, is_exit: bool = False) -> float:
        brok = min(20.0, gross_val * self.brok_pct)
        stt = (gross_val * self.stt_pct) if is_exit else 0.0
        gst = brok * 0.18
        slippage = gross_val * self.slip_pct
        return brok + stt + gst + slippage

    def consolidate_same_day_signals(self, df_day_signals: pd.DataFrame) -> pd.DataFrame:
        """Consolidates same-day signals on the same symbol to enforce 1 security = max 1 position."""
        if df_day_signals.empty:
            return df_day_signals

        consolidated = []
        for symbol, grp in df_day_signals.groupby("symbol"):
            sort_cols = [c for c in ["ml_probability", "rsi_14"] if c in grp.columns]
            if sort_cols:
                asc = [False if c == "ml_probability" else True for c in sort_cols]
                best_sig = grp.sort_values(by=sort_cols, ascending=asc).iloc[0].copy()
            else:
                best_sig = grp.iloc[0].copy()
            strat_list = sorted(list(grp["strategy_name"].unique()))
            best_sig["all_strategies"] = ",".join(strat_list)
            best_sig["strategy_count"] = len(strat_list)
            consolidated.append(best_sig)

        return pd.DataFrame(consolidated)

    def process_day(
        self,
        current_date: str,
        df_day_signals: pd.DataFrame,
        policy_mode: str = "ML_RANKING",  # "BASELINE", "ML_THRESHOLD", "ML_RANKING"
        min_probability_threshold: float = 0.52
    ) -> Dict[str, Any]:
        """Processes portfolio decisions for a single trading date."""

        # 1. Update and exit expired positions
        new_active = []
        for pos in self.active_positions:
            pos.days_held += 1
            if pos.days_held >= self.max_holding_days:
                # Exit position at T+10
                ret_pct = pos.row_data.get("forward_10d_return", 0.0)
                gross_val = pos.position_size

                cost_exit = self.compute_transaction_cost(gross_val, is_exit=True)
                cost_entry = pos.entry_cost

                net_pnl = (gross_val * (ret_pct / 100.0)) - (cost_entry + cost_exit)
                self.cash += pos.position_size + net_pnl

                self.executed_trades.append({
                    "position_id": pos.position_id,
                    "symbol": pos.symbol,
                    "strategy_names": ",".join(pos.strategy_names),
                    "entry_date": pos.entry_date,
                    "exit_date": current_date,
                    "entry_price": pos.entry_price,
                    "ml_probability": pos.ml_probability,
                    "net_pnl": round(net_pnl, 2),
                    "net_return_pct": round((net_pnl / pos.position_size) * 100.0, 2),
                    "exit_reason": "EXPIRED_T10"
                })

                self.portfolio_ledger.append({
                    "decision_date": current_date,
                    "symbol": pos.symbol,
                    "strategy_name": ",".join(pos.strategy_names),
                    "ml_probability": pos.ml_probability,
                    "decision": "EXIT",
                    "decision_reason": "EXPIRED",
                    "entry_date": pos.entry_date,
                    "entry_price": pos.entry_price,
                    "exit_date": current_date,
                    "exit_price": pos.entry_price * (1.0 + ret_pct / 100.0),
                    "position_size": pos.position_size,
                    "gross_exposure": len(new_active) * self.position_size,
                    "cash_before": self.cash - (pos.position_size + net_pnl),
                    "cash_after": self.cash,
                    "portfolio_value": self.cash + (len(new_active) * self.position_size),
                    "position_id": pos.position_id,
                    "replacement_flag": False
                })
            else:
                new_active.append(pos)
        self.active_positions = new_active

        # 2. Consolidate same-day multi-strategy signals
        df_consolidated = self.consolidate_same_day_signals(df_day_signals)

        # 3. Filter & Rank candidate signals
        if policy_mode == "BASELINE":
            candidates = df_consolidated.copy()
            # Sort deterministically by strategy_name ASC, symbol ASC
            if not candidates.empty:
                candidates = candidates.sort_values(by=["symbol"], ascending=[True])
        elif policy_mode == "ML_THRESHOLD":
            candidates = df_consolidated[df_consolidated["ml_probability"] >= min_probability_threshold].copy()
            if not candidates.empty:
                candidates = candidates.sort_values(by=["ml_probability", "symbol"], ascending=[False, True])
        elif policy_mode == "ML_RANKING":
            candidates = df_consolidated.copy()
            if not candidates.empty:
                candidates = candidates.sort_values(by=["ml_probability", "symbol"], ascending=[False, True])
        else:
            raise ValueError(f"Unknown policy mode: {policy_mode}")

        # Record rejected signals due to threshold
        if policy_mode == "ML_THRESHOLD" and not df_consolidated.empty:
            rejected_thresh = df_consolidated[df_consolidated["ml_probability"] < min_probability_threshold]
            for _, row in rejected_thresh.iterrows():
                self.portfolio_ledger.append({
                    "decision_date": current_date,
                    "symbol": row["symbol"],
                    "strategy_name": row.get("all_strategies", row["strategy_name"]),
                    "ml_probability": row["ml_probability"],
                    "decision": "REJECTED",
                    "decision_reason": "REJECTED_ML_THRESHOLD",
                    "entry_date": row["entry_date"],
                    "entry_price": row["entry_price"],
                    "exit_date": None,
                    "exit_price": None,
                    "position_size": self.position_size,
                    "gross_exposure": len(self.active_positions) * self.position_size,
                    "cash_before": self.cash,
                    "cash_after": self.cash,
                    "portfolio_value": self.cash + (len(self.active_positions) * self.position_size),
                    "position_id": None,
                    "replacement_flag": False
                })

        # 4. Fill Portfolio Slots / Perform Slot Replacement
        existing_active_symbols = set(p.symbol for p in self.active_positions)

        if not candidates.empty:
            for _, row in candidates.iterrows():
                symbol = row["symbol"]
                prob = row.get("ml_probability", 0.50)

                # Prevent duplicate position in same active security
                if symbol in existing_active_symbols:
                    self.portfolio_ledger.append({
                        "decision_date": current_date,
                        "symbol": symbol,
                        "strategy_name": row.get("all_strategies", row["strategy_name"]),
                        "ml_probability": prob,
                        "decision": "REJECTED",
                        "decision_reason": "REJECTED_DUPLICATE_POSITION",
                        "entry_date": row["entry_date"],
                        "entry_price": row["entry_price"],
                        "exit_date": None,
                        "exit_price": None,
                        "position_size": self.position_size,
                        "gross_exposure": len(self.active_positions) * self.position_size,
                        "cash_before": self.cash,
                        "cash_after": self.cash,
                        "portfolio_value": self.cash + (len(self.active_positions) * self.position_size),
                        "position_id": None,
                        "replacement_flag": False
                    })
                    continue

                # Case A: Open slot available
                if len(self.active_positions) < self.max_positions and self.cash >= self.position_size:
                    self.position_counter += 1
                    pos_id = f"POS_{self.position_counter:04d}"
                    qty = max(1, int(self.position_size / row["entry_price"]))
                    cost_entry = self.compute_transaction_cost(self.position_size, is_exit=False)

                    pos = Position(
                        position_id=pos_id,
                        symbol=symbol,
                        strategy_names=row.get("all_strategies", row["strategy_name"]).split(","),
                        ml_probability=prob,
                        entry_date=row["entry_date"],
                        entry_price=row["entry_price"],
                        position_size=self.position_size,
                        quantity=qty
                    )
                    pos.row_data = row.to_dict()
                    pos.entry_cost = cost_entry

                    self.cash -= self.position_size
                    self.active_positions.append(pos)
                    existing_active_symbols.add(symbol)

                    self.portfolio_ledger.append({
                        "decision_date": current_date,
                        "symbol": symbol,
                        "strategy_name": row.get("all_strategies", row["strategy_name"]),
                        "ml_probability": prob,
                        "decision": "EXECUTED",
                        "decision_reason": "EXECUTED",
                        "entry_date": row["entry_date"],
                        "entry_price": row["entry_price"],
                        "exit_date": None,
                        "exit_price": None,
                        "position_size": self.position_size,
                        "gross_exposure": len(self.active_positions) * self.position_size,
                        "cash_before": self.cash + self.position_size,
                        "cash_after": self.cash,
                        "portfolio_value": self.cash + (len(self.active_positions) * self.position_size),
                        "position_id": pos_id,
                        "replacement_flag": False
                    })

                # Case B: Slots full, check slot replacement policy
                elif self.slot_policy == "replace_if_superior" and len(self.active_positions) >= self.max_positions:
                    # Find lowest ML probability active position
                    worst_pos_idx = min(range(len(self.active_positions)), key=lambda i: self.active_positions[i].ml_probability)
                    worst_pos = self.active_positions[worst_pos_idx]

                    old_ret_pct = worst_pos.row_data.get("forward_10d_return", 0.0)
                    old_gross_val = worst_pos.position_size
                    old_cost_exit = self.compute_transaction_cost(old_gross_val, is_exit=True)
                    old_net_pnl = (old_gross_val * (old_ret_pct / 100.0)) - (worst_pos.entry_cost + old_cost_exit)
                    net_cash_returned = worst_pos.position_size + old_net_pnl

                    if prob >= (worst_pos.ml_probability + self.replacement_margin) and (self.cash + net_cash_returned >= self.position_size):

                        self.executed_trades.append({
                            "position_id": worst_pos.position_id,
                            "symbol": worst_pos.symbol,
                            "strategy_names": ",".join(worst_pos.strategy_names),
                            "entry_date": worst_pos.entry_date,
                            "exit_date": current_date,
                            "entry_price": worst_pos.entry_price,
                            "ml_probability": worst_pos.ml_probability,
                            "net_pnl": round(old_net_pnl, 2),
                            "net_return_pct": round((old_net_pnl / worst_pos.position_size) * 100.0, 2),
                            "exit_reason": "REPLACED_BY_SUPERIOR_SIGNAL"
                        })

                        self.portfolio_ledger.append({
                            "decision_date": current_date,
                            "symbol": worst_pos.symbol,
                            "strategy_name": ",".join(worst_pos.strategy_names),
                            "ml_probability": worst_pos.ml_probability,
                            "decision": "EXIT",
                            "decision_reason": "REPLACED_LOWER_SCORE_POSITION",
                            "entry_date": worst_pos.entry_date,
                            "entry_price": worst_pos.entry_price,
                            "exit_date": current_date,
                            "exit_price": worst_pos.entry_price * (1.0 + old_ret_pct / 100.0),
                            "position_size": worst_pos.position_size,
                            "gross_exposure": (len(self.active_positions) - 1) * self.position_size,
                            "cash_before": self.cash,
                            "cash_after": self.cash + worst_pos.position_size + old_net_pnl,
                            "portfolio_value": self.cash + (len(self.active_positions) * self.position_size) + old_net_pnl,
                            "position_id": worst_pos.position_id,
                            "replacement_flag": True
                        })

                        existing_active_symbols.remove(worst_pos.symbol)
                        self.cash += worst_pos.position_size + old_net_pnl
                        self.active_positions.pop(worst_pos_idx)

                        # Enter new position
                        self.position_counter += 1
                        new_pos_id = f"POS_{self.position_counter:04d}"
                        qty = max(1, int(self.position_size / row["entry_price"]))
                        cost_entry = self.compute_transaction_cost(self.position_size, is_exit=False)

                        new_pos = Position(
                            position_id=new_pos_id,
                            symbol=symbol,
                            strategy_names=row.get("all_strategies", row["strategy_name"]).split(","),
                            ml_probability=prob,
                            entry_date=row["entry_date"],
                            entry_price=row["entry_price"],
                            position_size=self.position_size,
                            quantity=qty
                        )
                        new_pos.row_data = row.to_dict()
                        new_pos.entry_cost = cost_entry

                        self.cash -= self.position_size
                        self.active_positions.append(new_pos)
                        existing_active_symbols.add(symbol)

                        self.portfolio_ledger.append({
                            "decision_date": current_date,
                            "symbol": symbol,
                            "strategy_name": row.get("all_strategies", row["strategy_name"]),
                            "ml_probability": prob,
                            "decision": "EXECUTED",
                            "decision_reason": "EXECUTED_VIA_REPLACEMENT",
                            "entry_date": row["entry_date"],
                            "entry_price": row["entry_price"],
                            "exit_date": None,
                            "exit_price": None,
                            "position_size": self.position_size,
                            "gross_exposure": len(self.active_positions) * self.position_size,
                            "cash_before": self.cash + self.position_size,
                            "cash_after": self.cash,
                            "portfolio_value": self.cash + (len(self.active_positions) * self.position_size),
                            "position_id": new_pos_id,
                            "replacement_flag": True
                        })
                    else:
                        # Reject due to capacity
                        self.portfolio_ledger.append({
                            "decision_date": current_date,
                            "symbol": symbol,
                            "strategy_name": row.get("all_strategies", row["strategy_name"]),
                            "ml_probability": prob,
                            "decision": "REJECTED",
                            "decision_reason": "REJECTED_CAPACITY",
                            "entry_date": row["entry_date"],
                            "entry_price": row["entry_price"],
                            "exit_date": None,
                            "exit_price": None,
                            "position_size": self.position_size,
                            "gross_exposure": len(self.active_positions) * self.position_size,
                            "cash_before": self.cash,
                            "cash_after": self.cash,
                            "portfolio_value": self.cash + (len(self.active_positions) * self.position_size),
                            "position_id": None,
                            "replacement_flag": False
                        })

                else:
                    # Reject due to capacity
                    self.portfolio_ledger.append({
                        "decision_date": current_date,
                        "symbol": symbol,
                        "strategy_name": row.get("all_strategies", row["strategy_name"]),
                        "ml_probability": prob,
                        "decision": "REJECTED",
                        "decision_reason": "REJECTED_CAPACITY",
                        "entry_date": row["entry_date"],
                        "entry_price": row["entry_price"],
                        "exit_date": None,
                        "exit_price": None,
                        "position_size": self.position_size,
                        "gross_exposure": len(self.active_positions) * self.position_size,
                        "cash_before": self.cash,
                        "cash_after": self.cash,
                        "portfolio_value": self.cash + (len(self.active_positions) * self.position_size),
                        "position_id": None,
                        "replacement_flag": False
                    })

        # Track daily equity state
        n_act = len(self.active_positions)
        port_val = self.cash + (n_act * self.position_size)
        util_pct = (n_act / self.max_positions) * 100.0

        self.daily_equities.append({
            "date": current_date,
            "cash": self.cash,
            "active_positions": n_act,
            "gross_exposure": n_act * self.position_size,
            "portfolio_value": port_val,
            "capital_utilization_pct": util_pct
        })

        return {
            "date": current_date,
            "cash": self.cash,
            "active_positions_count": n_act,
            "portfolio_value": port_val
        }

    def get_summary_performance(self) -> Dict[str, Any]:
        if not self.daily_equities:
            return {}

        df_eq = pd.DataFrame(self.daily_equities)
        fin_cap = df_eq["portfolio_value"].iloc[-1]
        cum_ret = round(((fin_cap - self.initial_capital) / self.initial_capital) * 100.0, 2)
        n_exec = len(self.executed_trades)

        if n_exec > 0:
            rets = np.array([t["net_return_pct"] for t in self.executed_trades])
            wins = np.sum(rets > 0)
            wr = round((wins / n_exec) * 100.0, 1)
            avg_r = round(float(np.mean(rets)), 2)
            pos_g = np.sum(rets[rets > 0])
            neg_l = abs(np.sum(rets[rets < 0]))
            pf = round(pos_g / neg_l, 2) if neg_l > 0 else 5.0
            std_r = np.std(rets)
            sharpe = round((np.mean(rets) * np.sqrt(252)) / (std_r * np.sqrt(10)), 2) if std_r > 0 else 0.0
        else:
            wr, avg_r, pf, sharpe = 0.0, 0.0, 0.0, 0.0

        eq_arr = df_eq["portfolio_value"].values
        roll_max = np.maximum.accumulate(eq_arr)
        dd = (eq_arr - roll_max) / roll_max * 100.0
        mdd = round(float(abs(np.min(dd))), 2)

        df_ledg = pd.DataFrame(self.portfolio_ledger)
        n_rejected_ml = len(df_ledg[df_ledg["decision_reason"] == "REJECTED_ML_THRESHOLD"]) if not df_ledg.empty else 0
        n_rejected_cap = len(df_ledg[df_ledg["decision_reason"] == "REJECTED_CAPACITY"]) if not df_ledg.empty else 0
        n_replacements = len(df_ledg[df_ledg["decision_reason"] == "REPLACED_LOWER_SCORE_POSITION"]) if not df_ledg.empty else 0

        avg_util = round(float(df_eq["capital_utilization_pct"].mean()), 1)
        max_util = round(float(df_eq["capital_utilization_pct"].max()), 1)

        pct_10_filled = round(float(np.mean(df_eq["active_positions"] == 10) * 100.0), 1)
        pct_idle = round(float(np.mean(df_eq["active_positions"] < 10) * 100.0), 1)

        return {
            "initial_capital": self.initial_capital,
            "final_capital": round(fin_cap, 2),
            "net_portfolio_return_pct": cum_ret,
            "executed_positions": n_exec,
            "rejected_ml_threshold": n_rejected_ml,
            "rejected_capacity": n_rejected_cap,
            "replacements_executed": n_replacements,
            "win_rate_pct": wr,
            "avg_position_return_pct": avg_r,
            "profit_factor": pf,
            "sharpe_ratio": sharpe,
            "max_drawdown_pct": mdd,
            "max_concurrent_positions": df_eq["active_positions"].max(),
            "avg_concurrent_positions": round(float(df_eq["active_positions"].mean()), 1),
            "avg_capital_utilization_pct": avg_util,
            "max_capital_utilization_pct": max_util,
            "pct_days_10_slots_filled": pct_10_filled,
            "pct_days_idle_capital": pct_idle,
            "total_costs_paid_inr": round(sum(t.get("net_pnl", 0) for t in self.executed_trades), 2) # cost metric
        }
