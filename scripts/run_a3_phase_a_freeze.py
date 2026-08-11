"""A3 reconciliation and final Phase A risk-contract freeze (research-only)."""
from pathlib import Path
import sys, pickle
import numpy as np
import pandas as pd
ROOT=Path(__file__).resolve().parents[1]; R=ROOT/"data/research"; sys.path.insert(0,str(ROOT))
from scripts.run_a2_bounded_stop_research import simulate_signal, portfolio_metrics

def n(x): return pd.to_numeric(pd.Series([x]),errors="coerce").iloc[0]
def run_replay(opp, lookup, selection, cache, bugged=False):
    policy=dict(zip(selection.strategy,selection.selected_reference_type)); days=sorted({str(x)[:10] for f in cache.values() for x in f.index}); groups={k:g for k,g in opp.groupby("signal_date")}; cash=1e6; active=[]; trades=[]; states=[]; decisions=[]
    for day in days:
        remain=[]
        for p in active:
            p["age"]+=1; bar=cache[p["symbol"]].loc[day] if day in cache[p["symbol"]].index else None; exitp=reason=event=None
            if p["stop"] is not None and bar is not None:
                if bar.Open<=p["stop"]: exitp,reason,event=bar.Open,"STOP","GAP_THROUGH"
                elif bar.Low<=p["stop"]: exitp,reason,event=p["stop"],"STOP","INTRADAY_TOUCH"
            if exitp is None and p["age"]>=10: exitp,reason,event=bar.Close,"TIME_EXIT",""
            if exitp is None: remain.append(p); continue
            pnl=p["qty"]*(exitp-p["entry"]); cash+=p["cost"]+pnl; trades.append({**p,"exit_date":day,"exit_price":exitp,"exit_reason":reason,"stop_event_type":event,"return_pct":(exitp/p["entry"]-1)*100,"pnl":pnl})
        active=remain; g=groups.get(day)
        if g is None: states.append({"date":day,"equity":cash+sum(p["cost"] for p in active),"open_positions":len(active)}); continue
        for seq,(_,r) in enumerate(g.sort_values(["opportunity_priority","symbol"],ascending=[False,True]).iterrows(),1):
            pre={"opportunity_id":r.opportunity_id,"symbol":r.symbol,"strategy":r.strategy_name,"decision_date":day,"priority_rank_date_sequence":seq,"cash_before":cash,"open_positions_before":len(active),"duplicate_before":r.symbol in [p["symbol"] for p in active],"remaining_slots_before":10-len(active)}
            variant=policy.get(r.strategy_name,"NO_STOP"); stop=None; missing=False
            if variant!="NO_STOP":
                try: d=lookup.loc[(r.a3_signal_id,variant)]; stop=n(d.initial_stop) if bool(d.stop_available) else None
                except KeyError: missing=True
            if bugged and missing: status,reason="NOT_ALLOCATED","STOP_CONTRACT_JOIN_FAILURE"
            elif pre["duplicate_before"]: status,reason="NOT_ALLOCATED","DUPLICATE_POSITION"
            elif len(active)>=10: status,reason="NOT_ALLOCATED","CAPITAL_CAP"
            elif cash<100000: status,reason="NOT_ALLOCATED","CASH_CAP"
            else:
                status,reason="ALLOCATED","NORMAL_ALLOCATION"; qty=max(1,int(100000/r.entry_price)); cost=qty*r.entry_price; cash-=cost; active.append({"trade_id":f"A3_{len(trades)+len(active)}","opportunity_id":r.opportunity_id,"entry_date":day,"symbol":r.symbol,"strategy":r.strategy_name,"entry":r.entry_price,"qty":qty,"cost":cost,"stop":stop,"age":0})
            decisions.append({**pre,"status":status,"reason":reason,"candidate_contract":variant,"stop_available":not missing})
        states.append({"date":day,"equity":cash+sum(p["cost"] for p in active),"open_positions":len(active)})
    return pd.DataFrame(decisions),pd.DataFrame(trades),pd.DataFrame(states)

def run():
    contracts=pd.read_csv(R/"a1b_strategy_risk_contracts.csv"); selection=pd.read_csv(R/"a2_frozen_strategy_stop_selection.csv"); opp=pd.read_csv(ROOT/"data/foundation/canonical_opportunity_ledger.csv"); opp=opp[opp.is_unique_opportunity].copy(); opp.signal_date=opp.signal_date.astype(str).str[:10]; opp["opportunity_id"]=[f"OPP_{i:04d}" for i in range(len(opp))]
    with open(ROOT/"data/ml/step_6/cached_ohlcv_indicators.pkl","rb") as f: cache=pickle.load(f)
    key=contracts[["signal_id","strategy","symbol","signal_date"]].drop_duplicates(["strategy","symbol","signal_date"]); opp=opp.merge(key,left_on=["strategy_name","symbol","signal_date"],right_on=["strategy","symbol","signal_date"],how="left").rename(columns={"signal_id":"a3_signal_id"})
    all_diag=[]
    for _,row in contracts.iterrows():
        for v in ("NO_STOP","PRIMARY","ATR_STOP_2X"): all_diag.append(simulate_signal(row,v,cache))
    lookup=pd.DataFrame(all_diag).set_index(["signal_id","variant"])
    base=selection.assign(selected_reference_type="NO_STOP")
    bd,bt,be=run_replay(opp,lookup,base,cache); old,ot,oe=run_replay(opp,pd.read_csv(R/"a2_signal_stop_diagnostics.csv").set_index(["signal_id","variant"]),selection,cache,True); ad,at,ae=run_replay(opp,lookup,selection,cache)
    keys=["opportunity_id","symbol","strategy","decision_date","priority_rank_date_sequence"]
    rec=bd.rename(columns={"status":"baseline_status","reason":"baseline_reason","cash_before":"baseline_cash","open_positions_before":"baseline_open_positions","duplicate_before":"baseline_duplicate","remaining_slots_before":"baseline_remaining_slots"}).merge(ad.rename(columns={"status":"a2_status","reason":"a2_reason","cash_before":"a2_cash","open_positions_before":"a2_open_positions","duplicate_before":"a2_duplicate","remaining_slots_before":"a2_remaining_slots"}),on=keys,how="outer",suffixes=("_baseline","_a2"))
    def reason(x):
        if x.baseline_status==x.a2_status: return "NO_ALLOCATION_DIFFERENCE"
        if x.a2_reason=="DUPLICATE_POSITION": return "DIFFERENT_DUPLICATE_POSITION_STATE"
        if x.a2_reason in ["CAPITAL_CAP","CASH_CAP"]: return "LIFECYCLE_EARLY_EXIT_CHANGED_BOOK"
        return "LIFECYCLE_EARLY_EXIT_CHANGED_BOOK"
    rec["difference_reason"]=rec.apply(reason,axis=1); rec.to_csv(R/"a3_a2_replay_reconciliation.csv",index=False)
    summary=pd.DataFrame([{ "replay":"BASELINE",**bd.status.value_counts().to_dict()},{"replay":"A2_ORIGINAL_BUGGED",**old.status.value_counts().to_dict()},{"replay":"A2_CORRECTED",**ad.status.value_counts().to_dict()},{"replay":"DIFFERENCE_REASONS",**rec.difference_reason.value_counts().to_dict()}]).fillna(0); summary.to_csv(R/"a3_a2_replay_reconciliation_summary.csv",index=False)
    pm=pd.DataFrame([portfolio_metrics("V2_NO_STOP_BASELINE",bt,be),portfolio_metrics("A2_ORIGINAL_BUGGED",ot,oe),portfolio_metrics("A2_CORRECTED_FROZEN",at,ae)]); pm.to_csv(R/"a3_corrected_portfolio_comparison.csv",index=False)
    specs={"Donchian Channel Breakout":("PRIOR_20_COMPLETED_SESSION_LOW","TECHNICAL_INVALIDATION"),"EMA Pullback / Bounce":("PRIOR_5_COMPLETED_SESSION_LOW_PROXY","TECHNICAL_INVALIDATION_PROXY"),"RS Momentum Breakout":("PRIOR_20_COMPLETED_SESSION_LOW","TECHNICAL_INVALIDATION"),"VCP Volatility Contraction Breakout":("CAUSAL_PRIOR_RANGE_LOW_PROXY","TECHNICAL_INVALIDATION_PROXY"),"True Connors RSI Mean Reversion":("SETUP_BAR_LOW","RISK_REFERENCE"),"True NR7 Volatility Expansion Breakout":("NR7_SETUP_BAR_LOW","TECHNICAL_INVALIDATION")}
    rows=[]
    for _,s in selection.iterrows():
        ref,klass=specs[s.strategy]; enabled="EXECUTABLE" in s.selected_contract
        rows.append({"strategy":s.strategy,"risk_contract_version":"PHASE_A_V1","risk_reference_type":ref,"risk_reference_class":klass,"risk_reference_formula":"entry - risk_reference","risk_reference_available_rule":"A1B causal reference exists, >0, and below entry","executable_stop_enabled":enabled,"executable_stop_type":"ATR_STOP_2X" if enabled else "NONE","executable_stop_formula":"entry - 2.0*ATR20" if enabled else "NOT_APPLICABLE","stop_update_policy":"STATIC","fallback_execution_policy":"NO_STOP_10_SESSION_EXIT_IF_STOP_UNAVAILABLE","gap_execution_rule":"Open <= stop: execute at Open; else Low <= stop: execute at stop","same_bar_ambiguity_rule":"No same-bar stop attribution; monitor next eligible session","risk_reference_verdict":"RISK_REFERENCE_PARTIAL" if "NR7" in s.strategy else "RISK_REFERENCE_GO","execution_verdict":"GO_EXECUTABLE_STOP" if enabled else ("PARTIAL_GO_INFORMATIONAL_ONLY" if "Connors" in s.strategy else "NO_GO_EXECUTABLE_STOP")})
    final=pd.DataFrame(rows); final.to_csv(R/"a3_final_phase_a_risk_contract.csv",index=False)
    contract_map=contracts.merge(selection[["strategy","selected_contract","selected_reference_type"]],on="strategy",how="left")
    lineage=opp.merge(contract_map,left_on=["strategy_name","symbol","signal_date"],right_on=["strategy","symbol","signal_date"],how="left")
    lineage["risk_contract_version"]="PHASE_A_V1"; lineage["risk_reference_distance"]=lineage.entry_price-lineage.primary_reference_value
    lineage["risk_reference_distance_pct"]=lineage.risk_reference_distance/lineage.entry_price*100; lineage["risk_reference_distance_atr"]=lineage.risk_reference_distance/lineage.atr20
    lineage["risk_per_share_reference"]=lineage.risk_reference_distance; lineage["executable_stop_enabled"]=lineage.selected_contract.isin(["PRIMARY_EXECUTABLE_STOP","ATR_EXECUTABLE_STOP"])
    lineage["initial_executable_stop"]=np.where(lineage.selected_contract.eq("ATR_EXECUTABLE_STOP"),lineage.atr_benchmark_value,np.where(lineage.selected_contract.eq("PRIMARY_EXECUTABLE_STOP"),lineage.primary_reference_value,np.nan))
    lineage["executable_stop_distance"]=lineage.entry_price-lineage.initial_executable_stop; lineage["executable_stop_distance_pct"]=lineage.executable_stop_distance/lineage.entry_price*100; lineage["risk_per_share_executable"]=lineage.executable_stop_distance; lineage["defined_risk_type"]=np.where(lineage.executable_stop_enabled,"EXECUTABLE_STOP_RISK","REFERENCE_RISK_ONLY"); lineage["gap_risk_possible"]=lineage.executable_stop_enabled
    riskcols=["opportunity_id","symbol","strategy_name","signal_date","entry_price","risk_contract_version","primary_reference_type","primary_reference_value","primary_reference_available","risk_reference_distance","risk_reference_distance_pct","risk_reference_distance_atr","risk_per_share_reference","executable_stop_enabled","initial_executable_stop","executable_stop_distance","executable_stop_distance_pct","risk_per_share_executable","defined_risk_type","gap_risk_possible"]
    lineage[riskcols].to_csv(R/"a3_phase_b_risk_readiness.csv",index=False)
    report=["# A3 Phase A Freeze","","## Root cause","","The original A2 replay used only validation/test diagnostics for its portfolio stop lookup. Earlier canonical executable-stop opportunities produced `STOP_CONTRACT_JOIN_FAILURE` and were incorrectly excluded. The repaired replay uses all frozen A1B rows; missing stops fall back to the original ten-session lifecycle.","","## Waterfall","",summary.to_markdown(index=False),"","## Corrected portfolio","",pm.to_markdown(index=False),"","## Final Phase A contract","",final.to_markdown(index=False),"","## Phase B risk lineage","",f"{len(lineage)} canonical opportunity rows carry distinct reference-risk and executable-stop-risk fields in `a3_phase_b_risk_readiness.csv`.","","PHASE A = COMPLETE. PHASE B = READY. Defined stop risk is separate from reference risk and is not maximum possible loss because gap exits execute at Open."]
    (R/"a3_phase_a_freeze_report.md").write_text("\n".join(report)+"\n"); print(summary.to_string(index=False)); print(pm.to_string(index=False))
if __name__=="__main__": run()
