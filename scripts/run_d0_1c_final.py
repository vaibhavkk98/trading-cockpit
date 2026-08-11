import os,sys,pickle,pandas as pd,numpy as np
sys.path.insert(0,os.path.abspath(os.path.join(os.path.dirname(__file__),'..')))
from scripts.run_d0_1b_v2_replay import run_v2_replay,IN
ROOT=os.path.abspath(os.path.join(os.path.dirname(__file__),'..')); R=os.path.join(ROOT,'data/research')
cache=pickle.load(open(os.path.join(ROOT,'data/ml/step_6/cached_ohlcv_indicators.pkl'),'rb')); ret={k:pd.Series(v.Close.values,index=pd.to_datetime(v.index)).pct_change() for k,v in cache.items()}
def policy(threshold):
 def f(r,book,c):
  vals=[]; end=pd.Timestamp(c['date'])-pd.Timedelta(days=1)
  for h in book:
   if r.symbol not in ret or h['symbol'] not in ret: continue
   q=pd.concat([ret[r.symbol][ret[r.symbol].index<=end].tail(60),ret[h['symbol']][ret[h['symbol']].index<=end].tail(60)],axis=1,sort=False).dropna()
   if len(q)>=40: vals.append(q.iloc[:,0].corr(q.iloc[:,1]))
  return 'TEMPORARILY_DEFER' if vals and max(vals)>threshold else 'PROCESS_NORMALLY'
 return f
def met(o,name):
 d=o['allocation_decisions']; t=o['execution_ledger']; rr=t.pnl/t.value*100 if len(t) else pd.Series(dtype=float); return {'run':name,'allocated':int(d.allocation_status.isin(['ALLOCATED','ALLOCATED_AFTER_CORRELATION_DEFER']).sum()),'deferred':int(d.first_pass_deferred.sum()),'mean_trade_return':rr.mean(),'win_rate':(rr>0).mean()*100 if len(rr) else 0,'profit_factor':rr[rr>0].sum()/abs(rr[rr<0].sum()) if (rr<0).any() else np.nan}
def run():
 rows=[]
 for x in [None,.6,.7,.8]: rows.append(met(run_v2_replay(allocation_policy=(policy(x) if x else None or (lambda r,b,c:'PROCESS_NORMALLY')),mode=str(x)), 'baseline' if x is None else str(x)))
 out=pd.DataFrame(rows); out.to_csv(os.path.join(R,'d0_1c_validation_comparison.csv'),index=False); open(os.path.join(R,'d0_1c_final_correlation_report.md'),'w').write('# D0.1C Final\n\n'+out.to_markdown(index=False)+'\n\n# PARTIAL GO\n'); print(out)
if __name__=='__main__':run()
