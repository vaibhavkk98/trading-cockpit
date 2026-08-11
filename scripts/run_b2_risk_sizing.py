"""B2 single-rule reference-risk sizing research; no live integration."""
from pathlib import Path
import json,pickle,sys
import numpy as np
import pandas as pd
ROOT=Path(__file__).resolve().parents[1];R=ROOT/'data/research';sys.path.insert(0,str(ROOT))
from scripts.run_a2_bounded_stop_research import simulate_signal
TARGET=float(pd.read_csv(R/'b1_trade_risk_dispersion.csv').reference_risk_pct_equity_at_entry.median())/100
def q(s,x):return s.quantile(x) if len(s) else np.nan
def replay(opp,lookup,sel,risk_map,risk_sizing=False):
 policy=dict(zip(sel.strategy,sel.selected_reference_type));cache=pickle.load(open(ROOT/'data/ml/step_6/cached_ohlcv_indicators.pkl','rb'));days=sorted({str(x)[:10] for f in cache.values() for x in f.index});groups={k:g for k,g in opp.groupby('signal_date')};cash=1e6;active=[];trades=[];dec=[];states=[]
 for day in days:
  keep=[]
  for p in active:
   p['age']+=1;bar=cache[p['symbol']].loc[day] if day in cache[p['symbol']].index else None;ep=reason=event=None
   if p['stop'] is not None and bar is not None:
    if bar.Open<=p['stop']:ep,reason,event=bar.Open,'STOP','GAP_THROUGH'
    elif bar.Low<=p['stop']:ep,reason,event=p['stop'],'STOP','INTRADAY_TOUCH'
   if ep is None and p['age']>=10:ep,reason,event=bar.Close,'TIME_EXIT',''
   if ep is None:keep.append(p);continue
   pnl=p['qty']*(ep-p['entry']);cash+=p['cost']+pnl;trades.append({**p,'exit_date':day,'exit_price':ep,'exit_reason':reason,'stop_event_type':event,'return_pct':(ep/p['entry']-1)*100,'pnl':pnl})
  active=keep;g=groups.get(day)
  if g is not None:
   for seq,(_,r) in enumerate(g.sort_values(['opportunity_priority','symbol'],ascending=[False,True]).iterrows(),1):
    baseq=max(1,int(100000/r.entry_price));ref=risk_map.get(r.opportunity_id,{}).get('risk_per_share_reference',np.nan);fallback=not(pd.notna(ref) and ref>0);target=TARGET*(cash+sum(x['cost'] for x in active));qty=baseq;sr='NOMINAL_BASELINE'
    if risk_sizing and not fallback:
     qty=min(baseq,int(np.floor(target/ref)));sr='REFERENCE_RISK_CAP_AT_B1_MEDIAN'
    elif risk_sizing:sr='SIZING_FALLBACK_NO_REFERENCE_RISK'
    pre={'opportunity_id':r.opportunity_id,'symbol':r.symbol,'strategy':r.strategy_name,'decision_date':day,'priority_rank_date_sequence':seq,'portfolio_equity_before_entry':cash+sum(x['cost'] for x in active),'entry_price':r.entry_price,'reference_risk_per_share':ref,'baseline_quantity':baseq,'baseline_position_value':baseq*r.entry_price,'risk_target_rupees':target,'risk_sized_quantity':qty,'risk_sized_position_value':qty*r.entry_price,'size_multiplier_vs_baseline':qty/baseq,'sizing_status':sr,'fallback_flag':fallback,'cash_before':cash,'open_positions_before':len(active)}
    variant=policy.get(r.strategy_name,'NO_STOP');stop=None
    if variant!='NO_STOP':
     try:z=lookup.loc[(r.a3_signal_id,variant)];stop=float(z.initial_stop) if bool(z.stop_available) else None
     except KeyError:stop=None
    if qty<1:status,why='NOT_ALLOCATED','RISK_SIZE_BELOW_ONE_SHARE'
    elif r.symbol in [x['symbol'] for x in active]:status,why='NOT_ALLOCATED','DUPLICATE_POSITION'
    elif len(active)>=10:status,why='NOT_ALLOCATED','CAPITAL_CAP'
    elif (not risk_sizing and cash<100000) or (risk_sizing and cash<qty*r.entry_price):status,why='NOT_ALLOCATED','CASH_CAP'
    else:
     status,why='ALLOCATED','NORMAL_ALLOCATION';cost=qty*r.entry_price;cash-=cost;active.append({**pre,'trade_id':f'B2_{len(trades)+len(active)}','entry_date':day,'entry':r.entry_price,'qty':qty,'cost':cost,'stop':stop,'age':0})
    dec.append({**pre,'status':status,'reason':why,'candidate_contract':variant,'stop_available':stop is not None})
  states.append({'date':day,'equity':cash+sum(x['cost'] for x in active),'cash':cash,'deployed_capital':sum(x['cost'] for x in active),'open_positions':len(active)})
 return pd.DataFrame(dec),pd.DataFrame(trades),pd.DataFrame(states)
def metrics(name,t,s):
 r=t.return_pct;peak=s.equity.cummax();dr=s.equity.pct_change().dropna();return {'variant':name,'starting_capital':1e6,'ending_capital':s.equity.iloc[-1],'total_return_pct':(s.equity.iloc[-1]/1e6-1)*100,'sharpe':dr.mean()/dr.std()*np.sqrt(252) if dr.std() else np.nan,'max_drawdown_pct':((s.equity/peak-1)*100).min(),'trade_count':len(t),'win_rate_pct':100*(r>0).mean(),'profit_factor':r[r>0].sum()/-r[r<0].sum(),'mean_trade_return_pct':r.mean(),'median_trade_return_pct':r.median(),'average_holding_period':t.age.mean(),'worst_trade_inr':t.pnl.min(),'worst_trade_pct':r.min(),'p05_trade_return_pct':q(r,.05),'p10_trade_return_pct':q(r,.1),'average_deployed_capital':s.deployed_capital.mean(),'median_deployed_capital':s.deployed_capital.median(),'average_cash':s.cash.mean(),'average_open_positions':s.open_positions.mean(),'sessions_at_10_positions_pct':100*(s.open_positions==10).mean(),'capital_utilization_pct':100*s.deployed_capital.mean()/1e6}
def run():
 c=pd.read_csv(R/'a1b_strategy_risk_contracts.csv');sel=pd.read_csv(R/'a2_frozen_strategy_stop_selection.csv');risk=pd.read_csv(R/'a3_phase_b_risk_readiness.csv').drop_duplicates('opportunity_id').set_index('opportunity_id').to_dict('index')
 opp=pd.read_csv(ROOT/'data/foundation/canonical_opportunity_ledger.csv');opp=opp[opp.is_unique_opportunity].copy();opp.signal_date=opp.signal_date.astype(str).str[:10]
 opp['opportunity_id']=['OPP_{:04d}'.format(i) for i in range(len(opp))]
 key=c[['signal_id','strategy','symbol','signal_date']].drop_duplicates(['strategy','symbol','signal_date']);opp=opp.merge(key,left_on=['strategy_name','symbol','signal_date'],right_on=['strategy','symbol','signal_date'],how='left').rename(columns={'signal_id':'a3_signal_id'})
 cache=pickle.load(open(ROOT/'data/ml/step_6/cached_ohlcv_indicators.pkl','rb'));rows=[]
 for _,x in c.iterrows():
  for v in ('NO_STOP','PRIMARY','ATR_STOP_2X'):rows.append(simulate_signal(x,v,cache))
 lookup=pd.DataFrame(rows).set_index(['signal_id','variant']);bd,bt,bs=replay(opp,lookup,sel,risk);rd,rt,rs=replay(opp,lookup,sel,risk,True)
 diag=rd.merge(pd.DataFrame(risk).T.reset_index().rename(columns={'index':'opportunity_id'})[['opportunity_id','risk_per_share_reference']],on='opportunity_id',how='left');diag['baseline_reference_risk_rupees']=diag.baseline_quantity*diag.risk_per_share_reference;diag['risk_sized_reference_risk_rupees']=diag.risk_sized_quantity*diag.risk_per_share_reference;diag.to_csv(R/'b2_trade_sizing_diagnostics.csv',index=False)
 pm=pd.DataFrame([metrics('NOMINAL_100K_BASELINE',bt,bs),metrics('REFERENCE_RISK_CAP_AT_B1_MEDIAN',rt,rs)])
 for label,t,s in [('NOMINAL_100K_BASELINE',bt,bs),('REFERENCE_RISK_CAP_AT_B1_MEDIAN',rt,rs)]:
  heat=[]
  for z in s.itertuples(index=False):
   a=t[(t.entry_date<=z.date)&(t.exit_date>z.date)]; ref=(a.qty*a.reference_risk_per_share).clip(lower=0).fillna(0).sum(); exe=(a.qty*(a.entry-a.stop)).where(a.stop.notna(),0).clip(lower=0).sum()
   heat.append((ref/z.equity*100 if z.equity else np.nan,exe/z.equity*100 if z.equity else np.nan))
  hs=pd.DataFrame(heat,columns=['reference_heat_pct','executable_heat_pct'])
  for col,out in [('reference_heat_pct','reference_heat'),('executable_heat_pct','executable_heat')]:
   row=pm.variant.eq(label);pm.loc[row,[out+'_median',out+'_p75',out+'_p90',out+'_p95',out+'_max']]=[hs[col].quantile(x) for x in [.5,.75,.9,.95]]+[hs[col].max()]
 pm.to_csv(R/'b2_portfolio_comparison.csv',index=False)
 # split reporting is descriptive only; contract was predeclared from B1 before either split.
 compare=[]
 for split,lo,hi in [('VALIDATION','2025-10-15','2026-02-18'),('TEST','2026-02-18','9999-12-31')]:
  for name,t in [('BASELINE',bt),('RISK_SIZED',rt)]:
   x=t[(t.entry_date>=lo)&(t.entry_date<hi)];compare.append({'split':split,'variant':name,'trades':len(x),'mean_return_pct':x.return_pct.mean(),'p10_return_pct':q(x.return_pct,.1),'worst_return_pct':x.return_pct.min(),'total_pnl':x.pnl.sum()})
 comp=pd.DataFrame(compare);comp[comp.split.eq('VALIDATION')].to_csv(R/'b2_validation_comparison.csv',index=False);comp[comp.split.eq('TEST')].to_csv(R/'b2_test_comparison.csv',index=False)
 rec=bd[['opportunity_id','status','reason']].rename(columns={'status':'baseline_status','reason':'baseline_reason'}).merge(rd[['opportunity_id','status','reason']].rename(columns={'status':'risk_sized_status','reason':'risk_sized_reason'}),on='opportunity_id');rec['difference_reason']=np.where(rec.baseline_status.eq(rec.risk_sized_status),'NO_DIFFERENCE','SIZING_CHANGED_CASH_OR_BOOK_STATE');rec.to_csv(R/'b2_allocation_reconciliation.csv',index=False)
 frozen={'contract':'REFERENCE_RISK_CAP_AT_B1_MEDIAN','target_risk_pct':TARGET*100,'nominal_position_cap':100000,'whole_shares_only':True,'missing_reference_fallback':'NOMINAL_100K','selection_frozen':True};(R/'b2_frozen_sizing_contract.json').write_text(json.dumps(frozen,indent=2)+'\n')
 report=['# B2 Bounded Risk-Based Position Sizing','',f'Predeclared target: B1 exact median reference risk at entry = **{TARGET*100:.10f}%**.','', '## Portfolio comparison','',pm.to_markdown(index=False),'','## Validation/Test (descriptive; target unchanged)','',comp.to_markdown(index=False),'','## Allocation reconciliation','',rec.difference_reason.value_counts().to_frame('count').to_markdown(),'','RISK_SIZING = PARTIAL_GO: upper-tail exposure is capped without leverage, but capital utilization/economics must be weighed; no heat limit or second parameter was tested. Defined risk remains distinct from maximum gap loss.'];(R/'b2_risk_sizing_report.md').write_text('\n'.join(report)+'\n');print(pm.to_string(index=False));print(comp.to_string(index=False))
if __name__=='__main__':run()
