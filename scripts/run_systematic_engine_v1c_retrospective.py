#!/usr/bin/env python3
"""Run the frozen V1C retrospective once on authorized AutoPaper development data."""
from __future__ import annotations
import hashlib, json, sys
from pathlib import Path
from collections import defaultdict
from statistics import mean
import pandas as pd
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT))
import systematic_engine_v1c_retrospective as r
import opportunity_selection_engine as selection

SOURCE = ROOT / "data/research/predictability/pb_r1/causal_feature_dataset.parquet"
BAR_DIR = ROOT / "data/ha_d1/layers/04_research_adjusted_ohlcv"
CALENDAR = ROOT / "data/ha_d1/layers/06_benchmark_series/benchmarks.parquet"
SPLIT = ROOT / "data/research/autopaper_v1/split_manifest.json"
OUT = ROOT / "data/research/systematic_engine/v1c_retrospective"

def write(name, value):
    path = OUT / name
    if isinstance(value, pd.DataFrame): value.to_csv(path, index=False)
    else: path.write_text(json.dumps(value, indent=2, sort_keys=True, default=str, allow_nan=False) + "\n")

def random_summary(rows):
    keys = ["net_return_pct", "gross_return_pct", "max_drawdown_pct", "sharpe", "sortino", "turnover", "direct_costs",
            "average_exposure", "cash_utilization", "completed_trades", "average_holding_period", "capacity_blocks", "expired_queued_opportunities"]
    result = {}
    for key in keys:
        values = [float(row[key]) for row in rows if row.get(key) is not None]
        result[key] = {"mean": sum(values)/len(values), "median": float(pd.Series(values).median()),
            "p25": float(pd.Series(values).quantile(.25)), "p75": float(pd.Series(values).quantile(.75)),
            "min": min(values), "max": max(values)}
    return result

def main():
    OUT.mkdir(parents=True, exist_ok=True)
    split = json.loads(SPLIT.read_text()); end = pd.Timestamp(split["development_last_signal"])
    assert end < r.HOLDOUT_START and not (ROOT / "data/research/autopaper_v1/holdout_opened.json").exists()
    cols = ["decision_id","opportunity_id","trade_date","canonical_security_id","symbol","population","primary_strategy",
        "feature_source_max_date","excess_20d","volume_ratio","close_location_value","ema20_extension","atr_pct","realized_vol_20d"]
    data = pd.read_parquet(SOURCE, columns=cols, filters=[("population","==","QUALIFIED"),("trade_date","<=",end)])
    # Reattach signal-session liquidity from the frozen development event artifacts.
    liquidity = pd.concat([pd.read_parquet(path, columns=["decision_id","traded_value","sector"])
                           for path in sorted((ROOT/"data/research/autopaper_v1").glob("development_events_fold*.parquet"))])
    # Early development context is authorized but was not persisted as fold events; derive traded value from bars below.
    data = data.merge(liquidity.drop_duplicates("decision_id"), on="decision_id", how="left")
    start = pd.Timestamp(data.trade_date.min()); bar_end = pd.Timestamp(split["development_end"])
    sids = set(data.canonical_security_id); parts=[]
    bar_cols=["trade_date","canonical_security_id","adjusted_open","adjusted_high","adjusted_low","adjusted_close","volume","traded_value_inr"]
    for path in sorted(BAR_DIR.glob("research_adjusted_eq_*.parquet")):
        year=int(path.stem.rsplit("_",1)[1])
        if start.year <= year <= bar_end.year:
            frame=pd.read_parquet(path,columns=bar_cols,filters=[("trade_date",">=",start),("trade_date","<=",bar_end)])
            parts.append(frame[frame.canonical_security_id.isin(sids)])
    raw=pd.concat(parts,ignore_index=True); signal_liq=raw[["trade_date","canonical_security_id","traded_value_inr"]]
    missing=data.traded_value.isna()
    if missing.any():
        lookup=signal_liq.rename(columns={"traded_value_inr":"derived_traded_value"})
        data=data.merge(lookup,on=["trade_date","canonical_security_id"],how="left")
        data.loc[data.traded_value.isna(),"traded_value"]=data.loc[data.traded_value.isna(),"derived_traded_value"]
    signals=r.prepare_signals(data)
    bars={}
    for date,sid,o,h,l,c,v,tv in raw.itertuples(index=False,name=None):
        bars.setdefault(str(pd.Timestamp(date).date()),{})[sid]=tuple(float(x) for x in (o,h,l,c,v,tv))
    calendar=pd.read_parquet(CALENDAR,columns=["index_name","trade_date"])
    sessions=[str(x.date()) for x in pd.DatetimeIndex(pd.to_datetime(calendar.loc[calendar.index_name.eq("Nifty 500"),"trade_date"]).unique()).sort_values()
              if start <= x <= bar_end]
    folds=json.loads((ROOT/"data/research/autopaper_v1/preregistration.json").read_text())["folds"]
    ranges={"FULL":{"start":str(start.date()),"last_signal":str(end.date()),"end":split["development_end"]},
            **{f"FOLD_{x['fold']}":x for x in folds}}
    all_results=[]; full={}
    for label, window in ranges.items():
        lo, last, hi=window["start"],window["last_signal"],window["end"]
        ss=[x for x in signals if lo <= x["trade_date"] <= last]; cal=[x for x in sessions if lo <= x <= hi]
        for policy in ("C0","C1","C2"):
            result=r.replay(ss,cal,bars,policy); diag=r.event_diagnostics(result,"quality_score" if policy=="C1" else "risk_adjusted_quality" if policy=="C2" else "p0_score")
            all_results.append({"period":label,"policy":policy,**result["metrics"],"result_hash":result["result_hash"],"diagnostic":diag})
            if label=="FULL": full[policy]=result
        for seed in selection.RANDOM_SEEDS:
            result=r.replay(ss,cal,bars,"R0",seed); all_results.append({"period":label,"policy":f"R0_{seed}",**result["metrics"],"result_hash":result["result_hash"]})
    table=pd.DataFrame([{k:v for k,v in row.items() if k!="diagnostic"} for row in all_results]); write("portfolio_results.csv",table)
    full_random=[row for row in all_results if row["period"]=="FULL" and row["policy"].startswith("R0_")]
    rnd=random_summary(full_random); write("random_distribution.json",rnd)
    diagnostics={p:r.event_diagnostics(full[p],"quality_score" if p=="C1" else "risk_adjusted_quality" if p=="C2" else "p0_score") for p in ("C0","C1","C2")}
    event_rows=[]
    for policy in ("C0","C1","C2"):
        for event in full[policy]["events"]:
            if not event["constrained"]: continue
            for row in event["candidates"]:
                outcome=row.get("outcome") or {}
                event_rows.append({"label":r.LABEL,"policy":policy,"event_id":event["event_id"],"event_date":event["date"],
                    "opportunity_id":row["opportunity_id"],"selected":row["selected"],"rank":row["rank"],
                    "quality_score":row.get("quality_score"),"risk_adjusted_quality":row.get("risk_adjusted_quality"),
                    "strategy":row["strategy"],"sector":row["sector"],"signal_date":row["signal_date"],
                    **{key:row["features"].get(key) for key in selection.FEATURE_MANIFEST["features"]},
                    **{key:outcome.get(key) for key in ("h10_net_return_pct","mfe_pct","mae_pct","plus_5_before_minus_3","realized_efficiency")}})
    event_frame=pd.DataFrame(event_rows); event_frame.to_parquet(OUT/"candidate_events.parquet",index=False)
    stability=[]
    for policy, group in event_frame.dropna(subset=["h10_net_return_pct"]).groupby("policy"):
        for dimension in ("year","strategy","sector"):
            values=(group.assign(year=group.signal_date.str[:4]) if dimension=="year" else group)
            for key,part in values.groupby(dimension):
                selected=part[part.selected].h10_net_return_pct; rejected=part[~part.selected].h10_net_return_pct
                stability.append({"policy":policy,"dimension":dimension,"group":key,"n":len(part),"selected_n":len(selected),
                    "selected_mean_h10":float(selected.mean()) if len(selected) else None,
                    "rejected_mean_h10":float(rejected.mean()) if len(rejected) else None,
                    "selection_lift_h10":float(selected.mean()-rejected.mean()) if len(selected) and len(rejected) else None})
    write("stability_analysis.csv",pd.DataFrame(stability))
    # Distinguish single-best regret from the shared engine's same-sized regret.
    regret=[]
    for policy in ("C0","C1","C2"):
        group=event_frame[(event_frame.policy==policy)&event_frame.h10_net_return_pct.notna()]
        single=[]
        for _,part in group.groupby("event_id"):
            chosen=part[part.selected].h10_net_return_pct; rejected=part[~part.selected].h10_net_return_pct
            if len(chosen) and len(rejected): single.append(float(chosen.mean()-rejected.max()))
        regret.append({"policy":policy,"selected_minus_single_best_rejected":mean(single) if single else None,
            "selected_minus_best_same_sized_rejected":diagnostics[policy]["selection_regret"]["selected_minus_best_rejected"],
            "selected_minus_average_rejected":diagnostics[policy]["selection_regret"]["selected_minus_average_rejected"]})
    write("selection_regret.csv",pd.DataFrame(regret))
    # Frozen feature diagnostics on C1 constrained-event observations only.
    feature_rows=[row for event in full["C1"]["events"] if event["constrained"] for row in event["candidates"] if row.get("outcome")]
    fdiag=[]
    for feature in selection.FEATURE_MANIFEST["features"]:
        for target in ("h10_net_return_pct","mfe_pct","mae_pct","plus_5_before_minus_3"):
            pairs=[(r.finite(row["features"].get(feature)),r.finite(row["outcome"].get(target))) for row in feature_rows]
            pairs=[x for x in pairs if None not in x]
            fdiag.append({"feature":feature,"target":target,"n":len(pairs),"spearman":float(pd.Series([x for x,_ in pairs]).corr(pd.Series([y for _,y in pairs]),method="spearman")) if len(pairs)>=3 else None})
    write("feature_diagnostics.csv",pd.DataFrame(fdiag)); write("selection_diagnostics.json",diagnostics)
    control=full["C0"]["metrics"]; classes={p:r.classify(full[p]["metrics"],control,rnd,diagnostics[p]) for p in ("C1","C2")}
    # Opportunity-cost sets use fully mature common outcomes.
    selected={p:{oid for event in full[p]["events"] for oid in event["selected"]} for p in ("C0","C1","C2")}
    outcomes={row["opportunity_id"]:row["outcome"] for event in full["C1"]["events"] for row in event["candidates"] if row.get("outcome")}
    opportunity_cost=[]
    for p in ("C1","C2"):
        for label,ids in (("UNIQUE_SELECTED",selected[p]-selected["C0"]),("MISSED_C0",selected["C0"]-selected[p])):
            vals=[outcomes[x]["h10_net_return_pct"] for x in ids if x in outcomes]
            opportunity_cost.append({"policy":p,"set":label,"n":len(vals),"mean_h10_return_pct":mean(vals) if vals else None})
    write("opportunity_cost.csv",pd.DataFrame(opportunity_cost))
    concentration={p:{key:value for key,value in full[p]["metrics"].items() if key.startswith("top_")} for p in ("C0","C1","C2")}
    write("concentration_analysis.json",concentration)
    percentile_position={}
    random_returns=[row["net_return_pct"] for row in full_random]
    for policy in ("C0","C1","C2"):
        value=full[policy]["metrics"]["net_return_pct"]
        percentile_position[policy]=sum(x<=value for x in random_returns)/len(random_returns)*100
    write("candidate_event_metadata.json",{"rows":len(event_frame),"events":int(event_frame.event_id.nunique()),
        "policies":sorted(event_frame.policy.unique()),"label":r.LABEL,"dataset_hash":r.digest(event_rows)})
    manifest=r.frozen_contract(); write("frozen_methodology_manifest.json",manifest)
    write("authorized_range.json",{"signal_start":str(start.date()),"signal_end":str(end.date()),"outcome_end":split["development_end"],
        "holdout_start":"2024-02-16","holdout_opened":False,"opportunities":len(signals),"securities":len({x['canonical_security_id'] for x in signals}),
        "signal_dates":len({x['trade_date'] for x in signals}),"split_hash":split["split_hash"]})
    summary={"version":r.VERSION,"label":r.LABEL,"authorized_range":{"start":str(start.date()),"last_signal":str(end.date()),"end":split["development_end"]},
        "population":{"opportunities":len(signals),"securities":len({x['canonical_security_id'] for x in signals}),"signal_dates":len({x['trade_date'] for x in signals})},
        "portfolio":{p:full[p]["metrics"] for p in ("C0","C1","C2")},"random_distribution":rnd,
        "random_return_percentile":percentile_position,"diagnostics":diagnostics,"classification":classes,
        "c2_incremental":{"net_return_difference_pct":full["C2"]["metrics"]["net_return_pct"]-full["C1"]["metrics"]["net_return_pct"],
            "drawdown_difference_pct":full["C2"]["metrics"]["max_drawdown_pct"]-full["C1"]["metrics"]["max_drawdown_pct"],
            "h10_selection_lift_difference":diagnostics["C2"]["selection_lift"]["h10_net_return_pct"]-diagnostics["C1"]["selection_lift"]["h10_net_return_pct"],
            "mae_lift_difference":diagnostics["C2"]["selection_lift"]["mae_pct"]-diagnostics["C1"]["selection_lift"]["mae_pct"]},
        "holdout_opened":False,"production_changed":False,
        "baseline_fingerprint":r.BASELINE_FINGERPRINT}
    summary["result_hash"]=r.digest(summary); write("results.json",summary)
    report=["# Systematic Engine V1C-R — Frozen retrospective diagnostic","",f"Authorized signals: {start.date()} to {end.date()}; outcomes through {split['development_end']}.",
        "The AutoPaper holdout beginning 2024-02-16 remained sealed.","",f"Opportunities: {len(signals):,}; securities: {summary['population']['securities']:,}; dates: {summary['population']['signal_dates']:,}.",""]
    for p in ("C0","C1","C2"): report.append(f"- {p}: net {full[p]['metrics']['net_return_pct']:.2f}%, drawdown {full[p]['metrics']['max_drawdown_pct']:.2f}%, Sharpe {full[p]['metrics']['sharpe']:.2f}.")
    report += ["",f"- C1 classification: **{classes['C1']}**",f"- C2 classification: **{classes['C2']}**","","No production or prospective record changed."]
    (OUT/"final_report.md").write_text("\n".join(report)+"\n")
    hashes={path.name:hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(OUT.iterdir()) if path.is_file() and path.name!="result_hashes.json"}
    write("result_hashes.json",hashes); print(json.dumps({"classification":classes,"result_hash":summary["result_hash"],"out":str(OUT)},indent=2))

if __name__=="__main__": main()
