"""P4 research-only transparent diagnostics/shadow selection; never used live."""
import os,sys,pandas as pd,numpy as np
sys.path.insert(0,os.path.abspath(os.path.join(os.path.dirname(__file__),'..')))
from scripts.run_step_4f_embargo import apply_embargo
from adapters import SECTOR_MAP
ROOT=os.path.abspath(os.path.join(os.path.dirname(__file__),'..')); DATA=os.path.join(ROOT,'data/research/p1_qualified_signals.csv'); OUT=os.path.join(ROOT,'data/research')
def sector(s): return SECTOR_MAP.get(s.replace('.NS',''),'NOT_AVAILABLE')
def select(frame,cap=None):
 out=[]
 for date,g in frame.sort_values(['signal_date','volume_ratio_20'],ascending=[True,False]).groupby('signal_date'):
  used={}; chosen=[]
  for _,r in g.iterrows():
   sec=sector(r.symbol); status='ALLOCATED'
   if len(chosen)>=10: status='QUALIFIED — CAPITAL CAP'
   elif cap and used.get(sec,0)>=cap: status='QUALIFIED — SECTOR CONCENTRATION'
   if status=='ALLOCATED': chosen.append(r.name); used[sec]=used.get(sec,0)+1
   out.append({**r.to_dict(),'candidate_sector':sec,'opportunity_priority_rank':int(g.index.get_loc(r.name))+1,'portfolio_fit':'sector cap '+str(cap) if cap else 'baseline','allocation_priority_rank':len(chosen) if status=='ALLOCATED' else None,'status':status,'primary_reason':status,'sector_before':used.get(sec,0)-(1 if status=='ALLOCATED' else 0),'sector_after':used.get(sec,0),'correlation':'NOT_AVAILABLE','portfolio_heat_before':'NOT_AVAILABLE','portfolio_heat_after':'NOT_AVAILABLE','capital_impact_inr':100000 if status=='ALLOCATED' else 0})
 return pd.DataFrame(out)
def metric(x,name):
 a=x[x.status=='ALLOCATED']; r=a.forward_return_10d; return {'split':name,'approach':('Diversification A: max 2 sector/day' if 'SECTOR' in x.primary_reason.values else 'Baseline: volume ratio priority'),'qualified':len(x),'allocated':len(a),'unallocated':len(x)-len(a),'mean_10d_return':round(r.mean(),2),'win_rate':round((r>0).mean()*100,1),'profit_factor':round(r[r>0].sum()/abs(r[r<0].sum()),2) if (r<0).any() else np.nan,'max_sector_concentration':round(a.candidate_sector.value_counts(normalize=True).max()*100,1) if len(a) else 0}
def run():
 d=pd.read_csv(DATA); s=apply_embargo(d,10); val,test=s['val'],s['test']; rows=[]; diags=[]
 for n,f in [('VALIDATION',val),('TEST',test)]:
  b=select(f); a=select(f,2); rows += [metric(b,n),metric(a,n)]; diags += [b,a]
 pd.DataFrame(rows).to_csv(os.path.join(OUT,'p4_shadow_comparison.csv'),index=False); diag=pd.concat(diags); diag.to_csv(os.path.join(OUT,'p4_candidate_diagnostics.csv'),index=False)
 lines=['# P4.1 + P4.2 — Portfolio Intelligence Diagnostics + Shadow Allocation','','## A. Execution assumptions','', 'Research shadow only: P1 qualified signals, causal date order, ₹10L/₹1L nominal/max-10 constraints. It uses existing forward 10D labels solely for evaluation; it is not the frozen execution simulator because P1 lacks complete entry/stop/execution fields.','','## B–F. Definitions','', 'Opportunity priority = causal volume_ratio_20 descending within signal date. Portfolio fit = sector exposure. Baseline applies capital cap only. Control A freezes validation-defined max two same-sector selections per date. Control B correlation-aware: NOT RUN (P1 contract lacks causal return windows). Control C heat-aware: NOT RUN (no genuine historical stops).','','## G–J. Validation and untouched test','',pd.DataFrame(rows).to_markdown(index=False),'','## K. Non-allocation reasons','',diag.primary_reason.value_counts().rename_axis('reason').reset_index(name='count').to_markdown(index=False),'','## L. Candidate examples','',diag[['symbol','signal_date','opportunity_priority_rank','portfolio_fit','allocation_priority_rank','status','primary_reason','candidate_sector','sector_before','sector_after','capital_impact_inr']].head(5).to_markdown(index=False),'','## M–N. Limitations and P4.3','', 'No causal correlation histories, historical sector master, or stop-risk coverage were available in the P1 contract. Do not alter live allocation. P4.3 should first build a point-in-time candidate/execution ledger with sector and return-window evidence.','','# PARTIAL GO']
 open(os.path.join(OUT,'p4_portfolio_intelligence_report.md'),'w').write('\n'.join(lines)+'\n'); print(pd.DataFrame(rows))
if __name__=='__main__':run()
