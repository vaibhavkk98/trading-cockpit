"""P2A research-only causal meta-ranking of P1-qualified signals."""
import os, sys, json
import numpy as np, pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler, OrdinalEncoder
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score, average_precision_score, brier_score_loss, log_loss
from sklearn.inspection import permutation_importance
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from scripts.run_step_4f_embargo import apply_embargo
ROOT=os.path.abspath(os.path.join(os.path.dirname(__file__),'..')); DATA=os.path.join(ROOT,'data/research/p1_qualified_signals.csv'); OUT=os.path.join(ROOT,'data/research')
TARGET='hit_3_before_minus_2'; CAT=['strategy_name']; NUM=['nifty_dist_ema50','return_1d','return_3d','return_5d','return_10d','return_20d','up_days_5','up_days_10','consecutive_up_days','distance_from_ema20_pct','distance_from_ema20_atr','distance_from_ema50_pct','distance_from_ema50_atr','volume_ratio_20','volume_acceleration_5_vs_20','true_range_atr20','atr5_atr20','close_location_in_day_range','upper_wick_pct_of_range','body_pct_of_range','distance_from_strategy_trigger_pct','bars_since_prior_strategy_signal','price_change_since_prior_strategy_signal']
LEAKAGE=['symbol','signal_date','forward_return_3d','forward_return_5d','forward_return_10d','mfe_10d','mae_10d','hit_3_before_minus_2','hit_5_before_minus_3','entry_price','close']
def outcome(x):
 r=x.forward_return_10d; pos=r[r>0].sum(); neg=abs(r[r<0].sum())
 return {'N':len(x),'mean_return':round(r.mean(),2),'median_return':round(r.median(),2),'win_rate':round((r>0).mean()*100,1),'profit_factor':round(pos/neg,2) if neg else np.nan,'mfe':round(x.mfe_10d.mean(),2),'mae':round(x.mae_10d.mean(),2),'hit_3_before_minus_2':round(x[TARGET].mean()*100,1),'hit_5_before_minus_3':round(x.hit_5_before_minus_3.mean()*100,1)}
def metrics(y,p): return {'roc_auc':round(roc_auc_score(y,p),3),'pr_auc':round(average_precision_score(y,p),3),'brier':round(brier_score_loss(y,p),3),'log_loss':round(log_loss(y,p),3)}
def lr(): return Pipeline([('prep',ColumnTransformer([('cat',OneHotEncoder(handle_unknown='ignore'),CAT),('num',Pipeline([('imp',SimpleImputer(strategy='median')),('scale',StandardScaler())]),NUM)])),('model',LogisticRegression(max_iter=2000,C=.5))])
def hgb(): return Pipeline([('prep',ColumnTransformer([('cat',Pipeline([('imp',SimpleImputer(strategy='most_frequent')),('enc',OrdinalEncoder(handle_unknown='use_encoded_value',unknown_value=-1))]),CAT),('num',SimpleImputer(strategy='median'),NUM)])),('model',HistGradientBoostingClassifier(max_iter=100,max_leaf_nodes=8,l2_regularization=2,random_state=7))])
def ranked(frame,p):
 q=pd.Series(p,index=frame.index); cuts={'top 10%':q>=q.quantile(.9),'top 20%':q>=q.quantile(.8),'top 30%':q>=q.quantile(.7),'middle':(q>q.quantile(.3))&(q<q.quantile(.7)),'bottom 30%':q<=q.quantile(.3)}
 return pd.DataFrame([{'bucket':k,**outcome(frame.loc[v])} for k,v in cuts.items()])
def run():
 df=pd.read_csv(DATA); sp=apply_embargo(df,10); train,val,test=sp['train'].copy(),sp['val'].copy(),sp['test'].copy()
 train,val,test=(x.dropna(subset=[TARGET]).copy() for x in (train,val,test))
 for frame in (train,val,test): frame[NUM]=frame[NUM].replace([np.inf,-np.inf],np.nan).clip(-100,100)
 assert set(NUM+CAT).isdisjoint(LEAKAGE)
 models={'Logistic Regression':lr(),'HistGradientBoosting':hgb()}; valrows=[]; fitted={}
 for n,m in models.items():
  m.fit(train[CAT+NUM],train[TARGET]); p=m.predict_proba(val[CAT+NUM])[:,1]; fitted[n]=m; valrows.append({'model':n,'N':len(val),**metrics(val[TARGET],p),**{f'top20_{k}':v for k,v in outcome(val.loc[p>=np.quantile(p,.8)]).items()}})
 vr=pd.DataFrame(valrows); chosen=vr.sort_values(['top20_hit_3_before_minus_2','roc_auc'],ascending=False).iloc[0]['model']; model=fitted[chosen]
 # model selection frozen before this single test prediction
 pt=model.predict_proba(test[CAT+NUM])[:,1]; pv=model.predict_proba(val[CAT+NUM])[:,1]
 test_rank=ranked(test,pt); baseline_score=test.sort_values('volume_ratio_20',ascending=False).head(max(1,int(np.ceil(.2*len(test))))); ml20=test.loc[pt>=np.quantile(pt,.8)]
 comp=pd.DataFrame([{'group':'ML top 20%',**outcome(ml20)},{'group':'Existing causal volume-ratio top 20%',**outcome(baseline_score)},{'group':'All qualified',**outcome(test)}])
 strat=[]
 for s,g in test.assign(prob=pt).groupby('strategy_name'):
  top=g[g.prob>=g.prob.quantile(.8)]; strat.append({'strategy':s,'N':len(g),'actual_success':round(g[TARGET].mean()*100,1),'mean_probability':round(g.prob.mean(),3),'mean_return':round(g.forward_return_10d.mean(),2),'top_vs_rest_return':round(top.forward_return_10d.mean()-(g.drop(top.index).forward_return_10d.mean() if len(g)>len(top) else 0),2),'status':'' if len(g)>=8 else 'INSUFFICIENT SAMPLE'})
 cal=pd.DataFrame({'pred':pt,'actual':test[TARGET]}).assign(bucket=lambda x:pd.cut(x.pred,[0,.2,.4,.6,.8,1],include_lowest=True)).groupby('bucket',observed=False).agg(N=('actual','size'),mean_pred=('pred','mean'),actual_success=('actual','mean')).reset_index(); cal.actual_success=(cal.actual_success*100).round(1); cal.mean_pred=cal.mean_pred.round(3)
 # interpretation without fitting on TEST
 if chosen=='Logistic Regression':
  names=model.named_steps['prep'].get_feature_names_out(); coef=model.named_steps['model'].coef_[0]; interp=pd.DataFrame({'feature':names,'importance':coef}).assign(abs=lambda x:x.importance.abs()).sort_values('abs',ascending=False).head(12).drop(columns='abs')
 else:
  pi=permutation_importance(model,val[CAT+NUM],val[TARGET],n_repeats=10,random_state=7,scoring='roc_auc'); interp=pd.DataFrame({'feature':CAT+NUM,'importance':pi.importances_mean}).sort_values('importance',ascending=False).head(12)
 vr.to_csv(os.path.join(OUT,'p2a_validation_comparison.csv'),index=False); test_rank.to_csv(os.path.join(OUT,'p2a_test_ranking_buckets.csv'),index=False); comp.to_csv(os.path.join(OUT,'p2a_test_baseline_comparison.csv'),index=False); pd.DataFrame(strat).to_csv(os.path.join(OUT,'p2a_strategy_robustness.csv'),index=False)
 report=['# P2A — ML Meta-Ranking Research','', '## A–D. Objective, data, target and safeguards','',f'- {len(df)} qualified strategy signals; train {len(train)}, validation {len(val)}, embargoed untouched test {len(test)}. Unique test symbol/date opportunities: {test[["symbol","signal_date"]].drop_duplicates().shape[0]}.','- Target: +3% before -2% within the existing 10-session horizon. Forward outcomes are labels only.','- Whitelist: strategy plus causal technical/regime/price-volume/P1 fields listed in script. Blacklist: raw symbol/date, all forward returns, MFE/MAE, targets, allocation/results and P3. Preprocessing is fit on TRAIN only.','','## E–G. Models, validation and frozen selection','',vr.to_markdown(index=False),'',f'Frozen selection: **{chosen}**, chosen on validation top-20% success then ROC-AUC; TEST was not used.','','## H–J. Untouched test and baseline','',f'Test metrics: {metrics(test[TARGET],pt)}.','',test_rank.to_markdown(index=False),'',comp.to_markdown(index=False),'','## K. Strategy robustness','',pd.DataFrame(strat).to_markdown(index=False),'','## L. Feature interpretation','',interp.to_markdown(index=False),'','## M. Probability calibration','',cal.to_markdown(index=False),'','Probabilities are research ranking outputs; sparse calibration buckets mean they are not production absolute probabilities.','','## N. Shadow portfolio','', 'NOT RUN: the P1 signal contract is not a drop-in frozen portfolio execution contract; forcing a simulation would change semantics.','','## O–P. Limitations and recommendation','', 'Small embargoed test, strategy-level cells and only one validation selection make this unsuitable for live ranking unless the out-of-sample top group clearly dominates the causal baseline. No live/UI/allocation change was made.']
 verdict='PARTIAL GO' if comp.iloc[0].mean_return>comp.iloc[1].mean_return and comp.iloc[0].hit_3_before_minus_2>comp.iloc[1].hit_3_before_minus_2 else 'NO-GO'; report+=['',f'# {verdict}']
 open(os.path.join(OUT,'p2a_ml_meta_ranking_report.md'),'w').write('\n'.join(report)+'\n'); print({'chosen':chosen,'test':metrics(test[TARGET],pt),'verdict':verdict})
if __name__=='__main__':run()
