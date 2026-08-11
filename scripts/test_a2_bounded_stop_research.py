"""Focused A2 invariants; frozen Step 8/10C are run separately as regressions."""
from pathlib import Path
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]; R=ROOT/"data/research"
def check(x,m):
    if not x: raise AssertionError(m)
def run():
    contract=pd.read_csv(R/"a1b_strategy_risk_contracts.csv")
    diag=pd.read_csv(R/"a2_signal_stop_diagnostics.csv")
    selection=pd.read_csv(R/"a2_frozen_strategy_stop_selection.csv")
    port=pd.read_csv(R/"a2_portfolio_comparison.csv")
    check(len(contract)==1009 and len(diag)>0,"A1B population / diagnostics missing")
    check(set(diag.variant)=={"NO_STOP","PRIMARY","ATR_STOP_2X"},"new stop variant")
    check((diag[diag.variant.eq("ATR_STOP_2X")].initial_stop.notna()).any(),"ATR unavailable")
    check(selection.selection_frozen.astype(str).str.lower().eq("true").all() and len(selection)==6,"selection not frozen")
    check(set(selection.selected_contract)<= {"PRIMARY_EXECUTABLE_STOP","ATR_EXECUTABLE_STOP","RISK_REFERENCE_ONLY","NO_STOP_CONTRACT"},"invalid contract")
    stopped=diag[diag.stop_hit]
    check((stopped.exit_reason=="STOP").all(),"stop exit semantics")
    check((stopped.sessions_to_stop>=1).all(),"same-entry-bar attribution")
    gap=stopped[stopped.stop_event_type.eq("GAP_THROUGH")]; touch=stopped[stopped.stop_event_type.eq("INTRADAY_TOUCH")]
    check((pd.to_numeric(gap.exit_price) <= pd.to_numeric(gap.initial_stop)).all(),"gap must use open")
    check((pd.to_numeric(touch.exit_price) == pd.to_numeric(touch.initial_stop)).all(),"touch must use stop")
    check((port.trade_count>=0).all() and (port.starting_capital==1_000_000).all(),"portfolio contract")
    check((port.capital_utilization_pct<=100).all(),"max positions violated")
    check((port.ending_capital>=0).all(),"negative cash/equity")
    print("A2 focused stop-research tests: PASS")
if __name__=="__main__": run()
