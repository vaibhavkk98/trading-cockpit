"""D1 descriptive strategy economics under frozen Phase A exits; no scoring."""
from pathlib import Path
import json,pickle,sys
import numpy as np
import pandas as pd
ROOT=Path(__file__).resolve().parents[1];R=ROOT/'data/research';sys.path.insert(0,str(ROOT))
from scripts.run_a2_bounded_stop_research import simulate_signal
from scripts.run_step_4f_embargo import apply_embargo
def q(x,n):return x.quantile(n) if len(x) else np.nan
def quality(n):return 'ROBUSTER_SAMPLE' if n>=100 else 'MODERATE_SAMPLE' if n>=30 else 'LOW_SAMPLE_SIZE'
def metrics(g):
 r=g.frozen_return_pct.dropna();win=r[r>0];loss=r[r<0];mfe=g.mfe_10d.dropna();mae=-g.mae_10d.dropna()
 return {'N':len(r),'sample_quality':quality(len(r)),'mean_return_pct':r.mean(),'median_return_pct':r.median(),'win_rate_pct':100*(r>0).mean(),'profit_factor':win.sum()/-loss.sum() if len(loss) else np.nan,'average_winner_pct':win.mean(),'median_winner_pct':win.median(),'average_loser_pct':loss.mean(),'median_loser_pct':loss.median(),'p10_return_pct':q(r,.1),'p25_return_pct':q(r,.25),'p75_return_pct':q(r,.75),'p90_return_pct':q(r,.9),'worst_return_pct':r.min(),'return_std_pct':r.std(),'average_winner_to_loser':win.mean()/abs(loss.mean()) if len(loss) else np.nan,'median_winner_to_loser':win.median()/abs(loss.median()) if len(loss) else np.nan,'median_mfe_pct':mfe.median(),'p75_mfe_pct':q(mfe,.75),'p90_mfe_pct':q(mfe,.9),'median_mae_magnitude_pct':mae.median(),'p75_mae_magnitude_pct':q(mae,.75),'p90_mae_magnitude_pct':q(mae,.9),'historical_plus3_before_minus2_pct':100*g.hit_3_before_minus_2.mean()}
def run():
 c=pd.read_csv(R/'a1b_strategy_risk_contracts.csv');p=pd.read_csv(R/'p1_qualified_signals.csv');s=pd.read_csv(R/'a2_frozen_strategy_stop_selection.csv');assert len(c)==len(p)==1009
 with open(ROOT/'data/ml/step_6/cached_ohlcv_indicators.pkl','rb') as f:cache=pickle.load(f)
 selected=dict(zip(s.strategy,s.selected_reference_type));out=[]
 for _,row in c.iterrows():
  variant=selected[row.strategy];variant='NO_STOP' if variant in ['NO_STOP','SETUP_BAR_LOW'] else variant
  z=simulate_signal(row,variant,cache);out.append(z)
 d=pd.DataFrame(out);d['signal_date']=c.signal_date.values;d['mfe_10d']=p.mfe_10d.values;d['mae_10d']=p.mae_10d.values;d['hit_3_before_minus_2']=p.hit_3_before_minus_2.values;d['frozen_return_pct']=d.return_with_stop;d['target_status']='NOT_AVAILABLE';d['execution_contract']=d.variant.map({'ATR_STOP_2X':'STATIC_2X_ATR_STOP_OR_10_SESSION','NO_STOP':'TEN_SESSION_EXIT_ONLY'})
 split=apply_embargo(d,10);frames={'TRAIN':split['train'],'VALIDATION':split['val'],'TEST':split['test']};rows=[]
 for name,frame in frames.items():
  for strategy,g in frame.groupby('strategy'):rows.append({'split':name,'strategy':strategy,'return_definition':'Frozen Phase A execution return: static 2xATR stop-or-10-session where enabled; otherwise 10-session exit. Existing research engine convention has no separate transaction-cost field.','target_status':'NOT_AVAILABLE',**metrics(g)})
 result=pd.DataFrame(rows);result.to_csv(R/'d1_strategy_economics.csv',index=False)
 buckets=pd.cut(d.frozen_return_pct,[-np.inf,-5,-2,0,2,5,np.inf],labels=['<= -5%','-5% to -2%','-2% to 0%','0% to +2%','+2% to +5%','> +5%'])
 contexts=[]
 for st,g in d.groupby('strategy'):
  test=result[(result.strategy==st)&(result.split=='TEST')];val=result[(result.strategy==st)&(result.split=='VALIDATION')]
  contexts.append({'strategy':st,'historical_population':'1,009 qualified P1 strategy signals','validation_sample_quality':val.sample_quality.iloc[0] if len(val) else 'LOW_SAMPLE_SIZE','test_sample_quality':test.sample_quality.iloc[0] if len(test) else 'LOW_SAMPLE_SIZE','target_status':'NOT_AVAILABLE','historical_outcome_buckets_pct':{str(k):round(v*100,2) for k,v in buckets[g.index].value_counts(normalize=True).items()},'production_note':'Descriptive historical cohort context only; not a prediction, probability, score, target or ranking input.'})
 (R/'d1_expected_edge_context.json').write_text(json.dumps(contexts,indent=2)+'\n')
 report=['# D1 Trade Economics / Expected Edge Context','', 'Historical descriptive cohort context only. It does not predict a current trade, set a target, change ranking, allocation, sizing, or stops.','', '## Strategy economics by temporal split','',result.to_markdown(index=False),'','## Outcome buckets','',pd.DataFrame([{'bucket':str(k),'trade_pct':v*100} for k,v in buckets.value_counts(normalize=True).sort_index().items()]).to_markdown(index=False),'','Target status is `NOT_AVAILABLE` for every strategy: MFE is historical excursion context, not a generated target. Validation and untouched TEST remain separate; small groups are explicitly labeled `LOW_SAMPLE_SIZE`.']
 (R/'d1_trade_economics_report.md').write_text('\n'.join(report)+'\n');print(result[['split','strategy','N','mean_return_pct','sample_quality']].to_string(index=False))
if __name__=='__main__':run()
