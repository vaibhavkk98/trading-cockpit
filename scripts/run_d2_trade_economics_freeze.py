from pathlib import Path
import json
import pandas as pd
ROOT=Path(__file__).resolve().parents[1];R=ROOT/'data/research'
def pick(g):
 test=g[g.split.eq('TEST')];val=g[g.split.eq('VALIDATION')]
 if len(test) and test.iloc[0].sample_quality!='LOW_SAMPLE_SIZE':return test.iloc[0],'TEST'
 if len(val) and val.iloc[0].sample_quality!='LOW_SAMPLE_SIZE':return val.iloc[0],'VALIDATION'
 return None,'INSUFFICIENT'
def run():
 d=pd.read_csv(R/'d1_strategy_economics.csv');items=[]
 for strategy,g in d.groupby('strategy'):
  row,source=pick(g)
  if row is None:
   items.append({'strategy':strategy,'display_sample_source':'INSUFFICIENT','sample_count':0,'sample_quality':'LOW_SAMPLE_SIZE','historical_return_context':'INSUFFICIENT_SAMPLE','payoff_context':'INSUFFICIENT_SAMPLE','excursion_context':'INSUFFICIENT_SAMPLE','current_risk_context':'ATTACH_CURRENT_PHASE_A_B_RISK_FIELDS','target_context':{'status':'NOT_AVAILABLE'},'descriptive_only':True,'predictive':False,'historical_path_metric_label':'Historical frequency of +3% before -2%','product_copy':'Historical strategy context — descriptive only. Insufficient validation/test sample.'});continue
  def fields(names):return {k:None if pd.isna(row[n]) else float(row[n]) for k,n in names.items()}
  items.append({'strategy':strategy,'display_sample_source':source,'sample_count':int(row.N),'sample_quality':row.sample_quality,'historical_return_context':fields({'mean_return':'mean_return_pct','median_return':'median_return_pct','win_rate':'win_rate_pct','profit_factor':'profit_factor','p10_return':'p10_return_pct','worst_return':'worst_return_pct'}),'payoff_context':fields({'average_winner':'average_winner_pct','average_loser':'average_loser_pct','payoff_ratio':'average_winner_to_loser'}),'excursion_context':fields({'median_mfe':'median_mfe_pct','p90_mfe':'p90_mfe_pct','median_mae':'median_mae_magnitude_pct','p90_mae':'p90_mae_magnitude_pct','plus3_before_minus2_frequency':'historical_plus3_before_minus2_pct'}),'current_risk_context':'ATTACH_CURRENT_PHASE_A_B_RISK_FIELDS','target_context':{'status':'NOT_AVAILABLE'},'descriptive_only':True,'predictive':False,'historical_path_metric_label':'Historical frequency of +3% before -2%','product_copy':f'Historical strategy context — descriptive only. {source} sample: N={int(row.N)}, {row.sample_quality}.'})
 contract={'contract_version':'D2_TRADE_ECONOMICS_V1','layer_name':'TRADE_ECONOMICS_CONTEXT','descriptive_only':True,'predictive':False,'display_rule':'Use TEST only if sample quality is MODERATE_SAMPLE or ROBUSTER_SAMPLE; else VALIDATION only if quality is adequate; otherwise INSUFFICIENT. Never pool splits or silently promote TRAIN.','target_status':'NOT_AVAILABLE','prohibitions':['predicted return','probability of success','target price','alpha/edge score','ranking/allocation/sizing input'],'risk_merge_rule':'Attach current risk reference and executable stop separately; never derive R:R without target.'}
 (R/'d2_trade_economics_product_contract.json').write_text(json.dumps(contract,indent=2)+'\n');(R/'d2_trade_economics_display_payload.json').write_text(json.dumps(items,indent=2)+'\n')
 report=['# D2 Trade Economics Product Freeze','', 'Trade Economics is historical strategy context, descriptive only—not a prediction, probability, target, score, or allocation input.','', '## Product display selection','',pd.DataFrame([{'strategy':x['strategy'],'display_sample_source':x['display_sample_source'],'N':x['sample_count'],'quality':x['sample_quality']} for x in items]).to_markdown(index=False),'','Target status is `NOT_AVAILABLE` for all strategies. The +3% before -2% field is labeled historical path metric and always carries its displayed sample source. Phase A/B risk fields attach separately at current-opportunity rendering time.','', 'PHASE D = COMPLETE.']
 (R/'d2_trade_economics_freeze_report.md').write_text('\n'.join(report)+'\n');print(pd.DataFrame([{'strategy':x['strategy'],'source':x['display_sample_source'],'N':x['sample_count'],'quality':x['sample_quality']} for x in items]).to_string(index=False))
if __name__=='__main__':run()
