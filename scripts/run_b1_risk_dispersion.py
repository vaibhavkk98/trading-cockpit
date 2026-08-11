"""B1 baseline diagnostic: frozen Phase A risk dispersion and portfolio heat."""
from pathlib import Path
import sys,pickle
import numpy as np
import pandas as pd
ROOT=Path(__file__).resolve().parents[1]; R=ROOT/'data/research';sys.path.insert(0,str(ROOT))
from scripts.run_a3_phase_a_freeze import run_replay
def q(s,x): return s.quantile(x) if len(s) else np.nan
def stats(s,prefix):
 return {f'{prefix}_{k}':v for k,v in {'mean':s.mean(),'median':s.median(),'p10':q(s,.1),'p25':q(s,.25),'p75':q(s,.75),'p90':q(s,.9),'p95':q(s,.95),'max':s.max()}.items()}
def run():
 risk=pd.read_csv(R/'a3_phase_b_risk_readiness.csv'); sel=pd.read_csv(R/'a2_frozen_strategy_stop_selection.csv')
 opp=pd.read_csv(ROOT/'data/foundation/canonical_opportunity_ledger.csv'); opp=opp[opp.is_unique_opportunity].copy(); opp.signal_date=opp.signal_date.astype(str).str[:10]
 opp['opportunity_id']=['OPP_{:04d}'.format(i) for i in range(len(opp))]
 c=pd.read_csv(R/'a1b_strategy_risk_contracts.csv');key=c[['signal_id','strategy','symbol','signal_date']].drop_duplicates(['strategy','symbol','signal_date']);opp=opp.merge(key,left_on=['strategy_name','symbol','signal_date'],right_on=['strategy','symbol','signal_date'],how='left').rename(columns={'signal_id':'a3_signal_id'})
 with open(ROOT/'data/ml/step_6/cached_ohlcv_indicators.pkl','rb') as f:cache=pickle.load(f)
 allrows=[]
 from scripts.run_a2_bounded_stop_research import simulate_signal
 for _,x in c.iterrows():
  for v in ('NO_STOP','PRIMARY','ATR_STOP_2X'):allrows.append(simulate_signal(x,v,cache))
 _,trades,states=run_replay(opp,pd.DataFrame(allrows).set_index(['signal_id','variant']),sel,cache)
 d=trades.merge(risk.drop_duplicates('opportunity_id')[['opportunity_id','primary_reference_value','primary_reference_available','risk_reference_distance','risk_reference_distance_pct','risk_reference_distance_atr','risk_per_share_reference','initial_executable_stop','executable_stop_enabled','executable_stop_distance','executable_stop_distance_pct','risk_per_share_executable','gap_risk_possible']],on='opportunity_id',how='left')
 d['position_value']=d.qty*d.entry;d['reference_risk_rupees']=d.qty*d.risk_per_share_reference;d['reference_risk_pct_position']=d.reference_risk_rupees/d.position_value*100;d['reference_risk_status']=np.where(d.primary_reference_available & d.reference_risk_rupees.gt(0),'AVAILABLE','RISK_REFERENCE_NOT_AVAILABLE');d['executable_risk_rupees']=d.qty*d.risk_per_share_executable;d['executable_risk_pct_position']=d.executable_risk_rupees/d.position_value*100
 # states are causal post-exit/pre-next-date book values under the corrected lifecycle.
 heat=[]; contrib=[]
 for s in states.itertuples(index=False):
  active=d[(d.entry_date<=s.date)&(d.exit_date>s.date)]; rr=active.reference_risk_rupees.clip(lower=0).fillna(0);er=active.executable_risk_rupees.clip(lower=0).fillna(0); eq=s.equity
  heat.append({'date':s.date,'portfolio_equity':eq,'cash':'NOT_AVAILABLE','deployed_capital':active.position_value.sum(),'open_positions':len(active),'reference_heat_rupees':rr.sum(),'reference_heat_pct':rr.sum()/eq*100 if eq else np.nan,'executable_stop_heat_rupees':er.sum(),'executable_stop_heat_pct':er.sum()/eq*100 if eq else np.nan,'number_positions_with_reference':active.reference_risk_rupees.gt(0).sum(),'number_positions_without_reference':active.reference_risk_rupees.isna().sum(),'number_positions_with_executable_stop':active.executable_stop_enabled.sum()})
  if rr.sum()>0:
   x=active.assign(reference_risk_contribution_pct=rr/rr.sum()*100)
   contrib.extend(x[['date' if False else 'opportunity_id','strategy','reference_risk_rupees','reference_risk_contribution_pct']].to_dict('records'))
 h=pd.DataFrame(heat);h.to_csv(R/'b1_portfolio_heat_timeseries.csv',index=False);pd.DataFrame(contrib).to_csv(R/'b1_portfolio_heat_contributions.csv',index=False)
 eqmap=h.set_index('date').portfolio_equity;d['reference_risk_pct_equity_at_entry']=d.reference_risk_rupees/d.entry_date.map(eqmap)*100;d['executable_risk_pct_equity_at_entry']=d.executable_risk_rupees/d.entry_date.map(eqmap)*100;d.to_csv(R/'b1_trade_risk_dispersion.csv',index=False)
 summary=[]
 for st,g in d.groupby('strategy'):
  x=g[g.reference_risk_status.eq('AVAILABLE')];e=g[g.executable_stop_enabled]
  summary.append({'strategy':st,'allocated_trades':len(g),'reference_coverage_pct':100*len(x)/len(g),**stats(x.reference_risk_rupees,'reference_risk_inr'),**stats(x.reference_risk_pct_position,'reference_pct_position'),**stats(x.reference_risk_pct_equity_at_entry,'reference_pct_equity'),'executable_trade_count':len(e),**stats(e.executable_risk_rupees,'executable_risk_inr'),**stats(e.executable_risk_pct_position,'executable_pct_position'),**stats(e.executable_risk_pct_equity_at_entry,'executable_pct_equity')})
 ss=pd.DataFrame(summary);ss.to_csv(R/'b1_strategy_risk_summary.csv',index=False)
 allref=d[d.reference_risk_status.eq('AVAILABLE')]; ex=d[d.executable_stop_enabled]; bands=pd.cut(allref.reference_risk_pct_position,[-np.inf,1,3,5,10,15,20,np.inf],labels=['<1','1-3','3-5','5-10','10-15','15-20','>20']).value_counts().sort_index()
 hc=pd.DataFrame(contrib); largest=hc.groupby('opportunity_id').reference_risk_contribution_pct.max() if len(hc) else pd.Series(dtype=float)
 report=['# B1 Risk Dispersion and Portfolio Heat Baseline','',f'Corrected frozen A2 source: **{len(d)} allocated trades**, not the bugged 112-trade replay.','', '## Overall reference-risk dispersion','',pd.DataFrame([stats(allref.reference_risk_rupees,"INR"),stats(allref.reference_risk_pct_position,"pct_position"),stats(allref.reference_risk_pct_equity_at_entry,"pct_equity")]).to_markdown(index=False),'','## Strategy comparison','',ss.to_markdown(index=False),'','## Reference risk % of position bands','',bands.to_frame('trade_count').to_markdown(),'','## Heat distribution','',pd.DataFrame([stats(h.reference_heat_pct,'reference_heat_pct'),stats(h.executable_stop_heat_pct,'executable_heat_pct')]).to_markdown(index=False),'',f'Largest-position heat contribution: median {largest.median():.2f}%, P90 {q(largest,.9):.2f}%, max {largest.max():.2f}%.','',f'Equal ₹1L tickets do not imply equal risk: reference-risk ₹ vs nominal position-value correlation is {allref.position_value.corr(allref.reference_risk_rupees):.3f}, while reference risk varies materially by strategy and stop width.','',f'NR7 reference coverage is {ss.loc[ss.strategy.str.contains("NR7"),"reference_coverage_pct"].iloc[0]:.2f}%; unavailable/invalid references remain unfilled.','', '## B1 readiness','', 'B2_RISK_SIZING_RESEARCH = PARTIAL_GO. Coverage is useful and dispersion/heat is material, but NR7 remains partial and reference risk—not executable-stop risk—is the broad measure for non-stop strategies. No sizing percentage or heat limit was selected.']
 (R/'b1_risk_dispersion_report.md').write_text('\n'.join(report)+'\n');print(ss[['strategy','allocated_trades','reference_coverage_pct','reference_risk_inr_median','reference_risk_inr_p90']].to_string(index=False));print(h.reference_heat_pct.describe(percentiles=[.75,.9,.95]))
if __name__=='__main__':run()
