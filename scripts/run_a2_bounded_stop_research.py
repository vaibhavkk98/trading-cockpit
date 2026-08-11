"""A2: bounded stop research.  Research-only; does not modify V2 or A1B."""
from pathlib import Path
import pickle
import sys
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RESEARCH = ROOT / "data/research"
CONTRACT = RESEARCH / "a1b_strategy_risk_contracts.csv"
OPPORTUNITIES = ROOT / "data/foundation/canonical_opportunity_ledger.csv"
CACHE = ROOT / "data/ml/step_6/cached_ohlcv_indicators.pkl"
sys.path.insert(0, str(ROOT))
from scripts.run_step_4f_embargo import apply_embargo

VARIANTS = ("NO_STOP", "PRIMARY", "ATR_STOP_2X")

def num(x): return pd.to_numeric(pd.Series([x]), errors="coerce").iloc[0]
def pct(x): return x * 100 if pd.notna(x) else np.nan
def q(x, n): return x.quantile(n) if len(x) else np.nan

def load():
    c = pd.read_csv(CONTRACT)
    with CACHE.open("rb") as f: cache = pickle.load(f)
    return c, cache

def simulate_signal(row, variant, cache):
    entry, stop = num(row.entry_reference_price), np.nan
    available = variant == "NO_STOP"
    if variant == "PRIMARY": available, stop = bool(row.primary_reference_available), num(row.primary_reference_value)
    if variant == "ATR_STOP_2X": available, stop = bool(row.atr_benchmark_available), num(row.atr_benchmark_value)
    base = {"signal_id":row.signal_id, "strategy":row.strategy, "symbol":row.symbol, "entry_date":row.signal_date,
            "entry_price":entry, "variant":variant, "initial_stop":stop if available and variant != "NO_STOP" else "NOT_AVAILABLE",
            "same_bar_sequence_ambiguous":bool(row.same_bar_sequence_ambiguous), "stop_available":available}
    frame=cache.get(row.symbol); date=str(row.signal_date)[:10]
    if frame is None or date not in frame.index or pd.isna(entry):
        return {**base, "stop_hit":False,"stop_event_type":"NOT_AVAILABLE","exit_reason":"NOT_AVAILABLE"}
    ix=frame.index.get_loc(date); future=frame.iloc[ix+1:ix+11]
    if len(future)<10: return {**base, "stop_hit":False,"stop_event_type":"NOT_AVAILABLE","exit_reason":"NOT_AVAILABLE"}
    exitbar=future.iloc[-1]; hit=None; event="NOT_HIT"
    if variant != "NO_STOP" and available:
        for offset, (_, bar) in enumerate(future.iterrows(), 1):
            if bar.Open <= stop: hit=(offset, bar, bar.Open, "GAP_THROUGH"); break
            if bar.Low <= stop: hit=(offset, bar, stop, "INTRADAY_TOUCH"); break
    if hit:
        offset, bar, exitp, event=hit; exitdate=str(bar.name)[:10]; reason="STOP"
    else:
        offset, exitp, exitdate, reason = 10, exitbar.Close, str(exitbar.name)[:10], "TIME_EXIT"
    ret=(exitp/entry-1)*100; no_stop=(exitbar.Close/entry-1)*100
    after=future.iloc[offset:] if hit else future.iloc[0:0]
    recovery=(after.High.max()/entry-1)*100 if len(after) else np.nan
    return {**base,"stop_distance":entry-stop if available and variant!="NO_STOP" else np.nan,
            "stop_distance_pct":pct((entry-stop)/entry) if available and variant!="NO_STOP" else np.nan,
            "stop_distance_atr":(entry-stop)/num(row.atr20) if available and variant!="NO_STOP" and num(row.atr20)>0 else np.nan,
            "risk_per_share":entry-stop if available and variant!="NO_STOP" else np.nan,
            "stop_hit":bool(hit),"stop_hit_date":exitdate if hit else "NOT_HIT","sessions_to_stop":offset if hit else np.nan,
            "stop_event_type":event,"exit_date":exitdate,"exit_price":exitp,"exit_reason":reason,
            "return_with_stop":ret,"return_no_stop_10d":no_stop,
            "recovered_above_entry_before_day10":bool(hit and after.High.max()>entry),
            "would_finish_profitable_day10":bool(hit and exitbar.Close>entry),"post_stop_mfe_pct":recovery,
            "post_stop_return_to_day10_pct":no_stop if hit else np.nan}

def summarize(diag, split):
    out=[]
    for (strategy, variant), g in diag[diag.split.eq(split)].groupby(["strategy","variant"]):
        valid=g[g.stop_available & g.return_with_stop.notna()]; hits=valid[valid.stop_hit]
        r=valid.return_with_stop
        gains=r[r>0].sum(); losses=-r[r<0].sum()
        out.append({"split":split,"strategy":strategy,"variant":variant,"signals":len(g),"effective_trades":len(valid),
          "coverage_pct":100*len(valid)/len(g),"mean_return_pct":r.mean(),"median_return_pct":r.median(),"win_rate_pct":100*(r>0).mean(),
          "profit_factor":gains/losses if losses else np.nan,"worst_trade_pct":r.min(),"p10_return_pct":q(r,.1),"p05_return_pct":q(r,.05),
          "worst_5_avg_pct":r.nsmallest(min(5,len(r))).mean(),"return_std_pct":r.std(),
          "median_stop_distance_pct":valid.stop_distance_pct.median(),"p10_stop_distance_pct":q(valid.stop_distance_pct.dropna(),.1),"p25_stop_distance_pct":q(valid.stop_distance_pct.dropna(),.25),"p75_stop_distance_pct":q(valid.stop_distance_pct.dropna(),.75),"p90_stop_distance_pct":q(valid.stop_distance_pct.dropna(),.9),"median_stop_distance_atr":valid.stop_distance_atr.median(),
          "stop_hit_count":len(hits),"stop_hit_pct":100*len(hits)/len(valid) if len(valid) else np.nan,"median_sessions_to_stop":hits.sessions_to_stop.median(),"gap_through_count":(hits.stop_event_type=="GAP_THROUGH").sum(),"same_bar_ambiguity_count":valid.same_bar_sequence_ambiguous.sum(),
          "mean_stop_loss_pct":hits.return_with_stop.mean(),"median_stop_loss_pct":hits.return_with_stop.median(),"worst_stop_loss_pct":hits.return_with_stop.min(),
          "recovery_above_entry_pct":100*hits.recovered_above_entry_before_day10.mean() if len(hits) else np.nan,"would_finish_profitable_pct":100*hits.would_finish_profitable_day10.mean() if len(hits) else np.nan,"median_post_stop_recovery_pct":hits.post_stop_return_to_day10_pct.median()})
    return pd.DataFrame(out)

def choose(validation, all_strategies):
    selections=[]
    for strategy in sorted(all_strategies):
        group=validation[validation.strategy.eq(strategy)]
        if group.empty:
            selections.append({"strategy":strategy,"selected_contract":"NO_STOP_CONTRACT","selected_reference_type":"NO_STOP","selected_stop_definition":"NO_STOP","validation_rationale":"No embargo-clean validation observations; no executable stop selected.","selection_frozen":True,"contract_version":"A2_V1"})
            continue
        base=group[group.variant.eq("NO_STOP")].iloc[0]
        primary=group[group.variant.eq("PRIMARY")].iloc[0]; atr=group[group.variant.eq("ATR_STOP_2X")].iloc[0]
        if strategy=="True Connors RSI Mean Reversion": chosen="RISK_REFERENCE_ONLY"; selected="SETUP_BAR_LOW"; why="A1B risk-reference-only classification and high entry-bar ambiguity retained."
        else:
            candidates=[x for x in (primary,atr) if x.coverage_pct>=80 and x.p10_return_pct>base.p10_return_pct and x.worst_trade_pct>base.worst_trade_pct]
            if not candidates: chosen="NO_STOP_CONTRACT"; selected="NO_STOP"; why="Neither frozen stop improved both validation left-tail diagnostics."
            else:
                best=max(candidates,key=lambda x:(x.p10_return_pct,x.worst_trade_pct))
                chosen="PRIMARY_EXECUTABLE_STOP" if best.variant=="PRIMARY" else "ATR_EXECUTABLE_STOP"; selected=best.variant; why="Validation-only left-tail improvement with adequate causal coverage."
        selections.append({"strategy":strategy,"selected_contract":chosen,"selected_reference_type":selected,"selected_stop_definition":selected,"validation_rationale":why,"selection_frozen":True,"contract_version":"A2_V1"})
    return pd.DataFrame(selections)

def portfolio(unique, diagnostics, selection, cache):
    # The same causal daily loop as V2: exits first, then ordered candidates.
    policy=dict(zip(selection.strategy,selection.selected_reference_type)); lookup=diagnostics.set_index(["signal_id","variant"])
    days=sorted({str(x)[:10] for f in cache.values() for x in f.index}); groups={k:g for k,g in unique.groupby("signal_date")}; cash=1_000_000.; active=[]; trades=[]; equity=[]
    for day in days:
        still=[]
        for p in active:
            f=cache[p["symbol"]]; bar=f.loc[day] if day in f.index else None; p["age"]+=1; exitp=None; reason=None; event=""
            if bar is not None and p["stop"] is not None and p["age"]>0:
                if bar.Open<=p["stop"]: exitp,reason,event=bar.Open,"STOP","GAP_THROUGH"
                elif bar.Low<=p["stop"]: exitp,reason,event=p["stop"],"STOP","INTRADAY_TOUCH"
            if exitp is None and p["age"]>=10: exitp,reason,event=bar.Close,"TIME_EXIT",""
            if exitp is None: still.append(p); continue
            pnl=p["qty"]*(exitp-p["entry"]); cash+=p["cost"]+pnl; trades.append({**p,"exit_date":day,"exit_price":exitp,"exit_reason":reason,"stop_event_type":event,"return_pct":(exitp/p["entry"]-1)*100,"pnl":pnl})
        active=still
        g=groups.get(day)
        if g is None: g=pd.DataFrame(columns=unique.columns)
        else: g=g.sort_values(["opportunity_priority","symbol"],ascending=[False,True])
        for _, r in g.iterrows():
            if len(active)>=10 or cash<100000 or r.symbol in [p["symbol"] for p in active]: continue
            sid=r.get("a2_signal_id"); variant=policy.get(r.strategy_name,"NO_STOP"); stop=None
            if variant!="NO_STOP":
                try: d=lookup.loc[(sid,variant)]; stop=num(d.initial_stop) if bool(d.stop_available) else None
                except KeyError: continue
            qty=max(1,int(100000/r.entry_price)); cost=qty*r.entry_price; cash-=cost; active.append({"trade_id":f"A2_{len(trades)+len(active)}","signal_id":sid,"symbol":r.symbol,"strategy":r.strategy_name,"entry":r.entry_price,"qty":qty,"cost":cost,"stop":stop,"age":0})
        equity.append({"date":day,"equity":cash+sum(p["cost"] for p in active),"open_positions":len(active)})
    return pd.DataFrame(trades),pd.DataFrame(equity)

def portfolio_metrics(name,trades,equity):
    r=trades.return_pct if len(trades) else pd.Series(dtype=float); daily=equity.equity.pct_change().dropna(); peak=equity.equity.cummax()
    return {"portfolio":name,"starting_capital":1_000_000,"ending_capital":equity.equity.iloc[-1],"total_return_pct":(equity.equity.iloc[-1]/1_000_000-1)*100,"sharpe":daily.mean()/daily.std()*np.sqrt(252) if daily.std() else np.nan,"max_drawdown_pct":((equity.equity/peak-1)*100).min(),"trade_count":len(trades),"win_rate_pct":100*(r>0).mean(),"profit_factor":r[r>0].sum()/-r[r<0].sum() if (r<0).any() else np.nan,"mean_trade_return_pct":r.mean(),"median_trade_return_pct":r.median(),"average_open_positions":equity.open_positions.mean(),"capital_utilization_pct":100*equity.open_positions.mean()/10,"average_holding_period":trades.age.mean() if len(trades) else np.nan,"stop_exits":(trades.exit_reason=="STOP").sum() if len(trades) else 0,"gap_through_exits":(trades.stop_event_type=="GAP_THROUGH").sum() if len(trades) else 0,"time_exits":(trades.exit_reason=="TIME_EXIT").sum() if len(trades) else 0,"worst_single_trade_pct":r.min(),"p05_trade_return_pct":q(r,.05),"p10_trade_return_pct":q(r,.1)}

def run():
    contracts, cache=load(); split=apply_embargo(contracts.rename(columns={"strategy":"strategy_name"}),10)
    allrows=[]
    for label, frame in [("VALIDATION",split["val"]),("TEST",split["test"])]:
        frame=frame.rename(columns={"strategy_name":"strategy"})
        for _,row in frame.iterrows():
            for variant in VARIANTS: allrows.append({**simulate_signal(row,variant,cache),"split":label})
    diag=pd.DataFrame(allrows); diag.to_csv(RESEARCH/"a2_signal_stop_diagnostics.csv",index=False)
    comparison=pd.concat([summarize(diag,"VALIDATION"),summarize(diag,"TEST")]); comparison.to_csv(RESEARCH/"a2_strategy_validation_comparison.csv",index=False)
    selection=choose(comparison[comparison.split.eq("VALIDATION")], contracts.strategy.unique()); selection.to_csv(RESEARCH/"a2_frozen_strategy_stop_selection.csv",index=False)
    test= comparison[comparison.split.eq("TEST")].merge(selection[["strategy","selected_contract","selected_reference_type"]],on="strategy")
    test.to_csv(RESEARCH/"a2_strategy_test_results.csv",index=False)
    opp=pd.read_csv(OPPORTUNITIES); unique=opp[opp.is_unique_opportunity].copy(); unique.signal_date=unique.signal_date.astype(str).str[:10]
    # exact strategy/date/symbol join; representative rows are unique by construction.
    key=contracts[["signal_id","strategy","symbol","signal_date"]].drop_duplicates(["strategy","symbol","signal_date"])
    unique=unique.merge(key,left_on=["strategy_name","symbol","signal_date"],right_on=["strategy","symbol","signal_date"],how="left").rename(columns={"signal_id":"a2_signal_id"})
    base_sel=selection.assign(selected_reference_type="NO_STOP")
    bt,be=portfolio(unique,diag,base_sel,cache); ft,fe=portfolio(unique,diag,selection,cache)
    port=pd.DataFrame([portfolio_metrics("V2_NO_STOP_BASELINE",bt,be),portfolio_metrics("A2_FROZEN_CONTRACT",ft,fe)]); port.to_csv(RESEARCH/"a2_portfolio_comparison.csv",index=False)
    verdict=[]
    for _,s in selection.iterrows():
        execv="GO_EXECUTABLE_STOP" if "EXECUTABLE" in s.selected_contract else ("PARTIAL_GO_INFORMATIONAL_ONLY" if s.selected_contract=="RISK_REFERENCE_ONLY" else "NO_GO_EXECUTABLE_STOP")
        ref="RISK_REFERENCE_GO" if s.strategy not in ["True NR7 Volatility Expansion Breakout"] else "RISK_REFERENCE_PARTIAL"
        verdict.append({"strategy":s.strategy,"execution_verdict":execv,"risk_reference_verdict":ref,"final_contract":s.selected_contract})
    vd=pd.DataFrame(verdict)
    report=["# A2 Bounded Executable Stop Research","","No variants beyond frozen primary and 2×ATR20 were evaluated; TEST did not change validation-frozen selections.","","## Validation comparison","",comparison[comparison.split.eq("VALIDATION")].to_markdown(index=False),"","## Frozen selection","",selection.to_markdown(index=False),"","## Untouched test","",test.to_markdown(index=False),"","## Final Phase A verdicts","",vd.to_markdown(index=False),"","## Portfolio","",port.to_markdown(index=False),"","## Limitations","","Defined stop risk is not maximum possible loss: gap-through exits use the Open. EMA/VCP remain proxies; Connors remains risk-reference-only. Same-entry-bar ambiguous touches were not attributed as stop hits. Phase B is ready for risk-per-share/heat work using the frozen contracts, not risk-based sizing in A2."]
    (RESEARCH/"a2_strategy_risk_report.md").write_text("\n".join(report)+"\n")
    print(selection.to_string(index=False)); print(port.to_string(index=False))

if __name__=="__main__": run()
