"""One bounded, offline PB feature reconstruction audit; never activates PB."""
from pathlib import Path
import ast
import hashlib
import json
import sys
import time
from collections import Counter

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from pb_canonical_features import build_batch, PRICE_CONTRACT, UNIVERSE_CONTRACT

OUT = ROOT/'data/production/predictability/pb_p1c'
RESEARCH = ROOT/'data/research/predictability/pb_r1'
LAYERS = ROOT/'data/ha_d1/layers'
SOURCE = ROOT/'predictability_boundary_research.py'
STATUS = 'NO-GO — EXACT PB PARITY NOT ACHIEVABLE; PB-R2B/R3B PRODUCTION-NATIVE RETRAINING REQUIRED'


def write(name,value):
    (OUT/name).write_text(json.dumps(value,indent=2,default=str,allow_nan=False)+'\n')


def contract():
    tree = ast.parse(SOURCE.read_text()); families = None; formulas={}; preamble=[]
    for node in tree.body:
        if isinstance(node,ast.AnnAssign) and isinstance(node.target,ast.Name) and node.target.id=='FEATURE_FAMILIES':
            families=ast.literal_eval(node.value)
        if isinstance(node,ast.FunctionDef) and node.name in ('_security_state_and_outcomes','_benchmark_state_and_outcomes','build_causal_dataset'):
            for statement in ast.walk(node):
                if isinstance(statement,ast.Assign):
                    for t in statement.targets:
                        if isinstance(t,ast.Subscript) and isinstance(t.slice,ast.Constant) and isinstance(t.slice.value,str):
                            formulas[t.slice.value]=ast.unparse(statement.value)
            if node.name=='_security_state_and_outcomes':
                preamble=[ast.unparse(x) for x in node.body[:15]]
    prior=json.loads((ROOT/'data/production/predictability/pb_p1b_feature_mapping_manifest.json').read_text())
    old={r['frozen_pb_r2_field']:r for r in prior['mapping']}; rows=[]
    windows={'drawdown_20d':(20,20),'volume_ratio':(21,15),'turnover_ratio':(21,15),
        'volume_persistence_5d':(25,5),'up_down_volume_ratio_20d':(21,15), 'atr_pct':(15,14),
        'range_expansion_ratio':(22,15),'realized_vol_ratio_5v20':(21,20),
        'traded_value_ratio':(21,15),'log_amihud_impact_20d':(21,15)}
    rank={'rs_percentile_20d','traded_value_percentile','market_breadth_ema20'}
    tv={'turnover_ratio','log_traded_value','traded_value_ratio','log_amihud_impact_20d'}
    for family,features in families.items():
        for name in features:
            if any(r['feature']==name for r in rows):continue
            formula=formulas.get(name); lookback,minimum=windows.get(name,(1,1))
            if name.startswith('return_'):
                n=int(name.split('_')[1][:-1]); formula=f'(close / close.shift({n}) - 1) * 100';lookback,minimum=n+1,n+1
            if name.startswith('realized_vol_') and name.endswith('d'):
                n=int(name.split('_')[2][:-1]);lookback,minimum=n+1,n
            if name.startswith('distance_high_'):
                n=int(name.split('_')[2][:-1]);lookback=minimum=n
            if name.startswith('ema') or name=='market_trend_ema20':lookback='full source history; EWM seed is first observation'
            if name.startswith('excess_'):lookback,minimum=61,61
            if name in ('market_return_20d','market_realized_vol_20d'):lookback,minimum=21,21
            if name=='india_vix':formula='same-date India VIX close; left join; no forward fill'
            if name in rank:
                status='NOT_RECONSTRUCTABLE';reason='Live scanner lacks HA_D1 EQ/ISIN eligibility and full deterministic nonqualified sample; qualified-only/full-fetched-universe ranks are different.'
            elif name in tv:
                status='NOT_AVAILABLE';reason='Exchange TOTTRDVAL/TtlTrfVal available in historical archive only; live EOD supplies OHLCV, not exchange traded value. Close*volume is not equivalent.'
            elif name=='india_vix':
                status='NOT_AVAILABLE';reason='VIX exists in scanner transient Yahoo history and rounded Market Context, but exact NSE benchmark archive close is not retained in PB EOD inputs.'
            elif name.startswith('market_'):
                status='RECONSTRUCTABLE_EXACT';reason='Formula can be rebuilt from NIFTY500 history; exact archive/provider source and EWM seed must still be verified before activation.'
            else:
                status='NOT_RECONSTRUCTABLE';reason='Historical split/bonus-only forward-chain prices and raw volume differ from live Yahoo auto_adjust=True histories including dividend/back adjustments; exact historical seed/action chain is absent live.'
            rows.append({'feature':name,'family':family,'exact_formula':formula,
                'raw_inputs':(['exchange traded_value_inr','raw volume','forward-chain OHLC'] if name in tv else
                    ['complete PB date population','per-security source state'] if name in rank else
                    ['Nifty 500 close','India VIX close'] if family=='ENVIRONMENT' else ['forward-chain adjusted OHLC','raw exchange volume','Nifty 500 close when relative']),
                'lookback':lookback,'minimum_observations':minimum,'current_session_included':True,
                'row_inclusion':'PB requires source position >=60; no missing bar compression',
                'price_semantics':PRICE_CONTRACT+'; raw volume/traded value never adjusted',
                'benchmark':'Nifty 500; excess_5d/10d use scaled 20D benchmark return',
                'universe':UNIVERSE_CONTRACT,'rank_semantics':'pandas average ties, pct=True *100; breadth unweighted >0 mean; compute before filtering qualified',
                'clipping':'none except log_traded_value clips traded value below zero before log1p',
                'normalization':'percentage *100; volatility annualized sqrt(252); no feature winsorization; frozen inference owns train imputation/scaling',
                'missingness':'pandas rolling min_periods; zero safe-div denominator => NaN; no ffill; vr.gt(1) treats NaN as false; preserve mask',
                'causal_cutoff':'completed session T inclusive; never outcome columns',
                'source':'predictability_boundary_research.py:_security_state_and_outcomes/_benchmark_state_and_outcomes/build_causal_dataset',
                'dtype':'float64','absolute_tolerance':1e-10,'mapping_status':status,
                'current_production_source':old[name]['production_field'],
                'current_production_semantics':old[name]['provenance'],
                'mismatch':reason,'proposed_reconstruction':'pb_canonical_features.build_batch using certified original raw contracts only',
                'raw_inputs_available_live':False if status!='RECONSTRUCTABLE_EXACT' else 'PARTIAL: benchmark series exists; seed/provider equality unproven'})
    assert len(rows)==36 and all(x['exact_formula'] for x in rows)
    return {'schema_version':'PB_FEATURE_CONTRACT_V1','decision_authority':False,
        'source_sha256':hashlib.sha256(SOURCE.read_bytes()).hexdigest(),'formula_preamble':preamble,
        'eligibility_rules':json.loads((ROOT/'config/ha_d1_liquid_universe.json').read_text()),
        'sample':'all date-qualified + lowest SHA256(date|canonical_security_id) min(N_nonqualified,max(75,3*N_qualified)); rows then require >=61 source observations',
        'features':rows}


def retraining_spec(c):
    features=[]
    for r in c['features']:
        remove=r['feature']=='india_vix'
        features.append({'original':r['feature'],'disposition':'removed' if remove else 'replaced by new explicit production-native feature',
            'new_name':None if remove else 'native_'+r['feature'],
            'formula':None if remove else r['exact_formula'], 'lookback':r['lookback'],
            'minimum_observations':r['minimum_observations'],
            'source_override':'Use raw NSE EQ OHLCV; traded-value inputs explicitly replaced by raw close*raw volume; no adjustment; no inference from old LSV payloads.',
            'cross_section_override':'All same-date current-contract qualified securities, unique canonical ID, evaluated before allocation; average ties pct*100; breadth >0 mean.',
            'missingness':r['missingness']})
    return {'schema_version':'PB_R2B_R3B_PRODUCTION_NATIVE_SPEC_V1','status':'IMPLEMENTATION_READY_NO_RETRAINING_EXECUTED',
        'new_methodology_required':True,'reuse_old_coefficients_or_thresholds':False,'decision_authority':False,
        'formula_binding':{'reference_contract':'pb_feature_contract_v1.json','variable_preamble':c['formula_preamble'],
            'override':'Bind adjusted_open/high/low/close to raw OHLC; bind traded_value_inr to raw close*volume. Resolve expressions in original-name order, then rename native_. Cross-section uses native qualified set.',
            'native_symbol_mapping':'Use canonical_security_id and dated symbol_history.parquet, not symbol string alone'},
        'features':features,'feature_count':35,'minimum_source_history':120,
        'raw_data_contract':{'historical':'data/ha_d1/layers/01_raw_exchange_observations/*.parquet: raw NSE EQ OHLCV; join canonical identity from data/ha_d1/layers/02_security_identity_history using dated ISIN mapping',
            'live':'Add immutable daily NSE bhavcopy EQ OHLCV snapshot ingestion using existing ha_data_foundation parser. Yahoo auto-adjusted bars are not admissible; fail closed when authoritative raw bar is absent.',
            'identity':'ISIN canonical security ID, dated NSE symbol; exclude unresolved IDs',
            'adjustment':'NONE; raw split-day jumps remain explicit; quarantine any security for 20 observed sessions after abs(raw close return)>=40%; apply identically historical/live',
            'history_seed':'First archived observation, persisted state from then onward; do not truncate/reseed EWM at live activation',
            'universe':'Reconstruct current frozen qualification on these exact raw bars for each T. No future membership. Rank over this entire qualified set before allocation; do not mix existing adjusted-bar qualified identities.',
            'benchmark':'NSE Nifty 500 completed-session close, same archived/live source; no substitution; missing benchmark features NaN',
            'timestamp':'as_of_session T and source publication/ingestion time <= decision timestamp; freeze payload and SHA; T+1 never read by feature code',
            'volume':'raw shares, zeros preserved','traded_value_proxy':'raw close * raw shares explicitly named proxy, not exchange turnover',
            'india_vix':'removed; do not replace with realized volatility'},
        'targets':{'horizon':10,'reference':'raw close at T; next 10 NSE sessions; complete bars required',
            'mfe':'100*(max(H[T+1:T+10])/C[T]-1)', 'mae':'100*(min(L[T+1:T+10])/C[T]-1)',
            'forward_volatility':'std(simple close returns T+1..T+10,ddof=1)*sqrt(252)*100',
            'success_5_before_3':'first high>=1.05*C[T] strictly before first low<=0.97*C[T]; same-session tie fails',
            'adverse_first':'-3 reached and +5 not reached earlier; tie adverse; neither reached=>false',
            'corporate_actions':'Exclude outcome windows crossing known split/bonus ex-dates; use outcomes only for label eligibility, never feature universe. Persist exclusions.',
            'market_adjusted_direction':'removed'},
        'split_protocol':{'base':'2016-2019; labels must end before 2020-01-01','calibration':'2020-2021; labels end before 2022-01-01',
            'validation':'2022-2023; label-end purge at next boundary',
            'historical_evaluation':'2024-2026 already examined by PB/Path Risk; disclosed reused evaluation, not globally untouched; annual base before Y-2, calibration Y-2..Y-1, labels end strictly before Y',
            'embargo':'20 trading sessions before each partition boundary in addition to label-end purge; no random split',
            'future_test':'Freeze before new prospective collection; minimum 12 months and 1000 matured qualified recommendations, >=3 strategies and >=100 sessions; no authority until separate review',
            'autopaper':'Do not open/replay AutoPaper holdout or evaluate its policies'},
        'model_selection':{'seed':20260901,'regression':'Ridge alpha=10, training medians, standard scaling, missing indicators; raw or log1p magnitude, max2 per target',
            'classification':'L2 logistic C=1 liblinear, same preprocessing; constant prevalence baseline if single class',
            'calibration':'none vs affine for regressions; none vs Platt for classification on calibration partition only',
            'selection':'validation MAE (regression), Brier (classification), ties choose simpler; freeze once; no threshold search after evaluation',
            'numerical':'finite deterministic inference artifact; no runtime fit; tolerance1e-10'},
        'evaluation':{'regression':['MAE','RMSE','Spearman','calibration slope/intercept','train-defined quartile monotonicity','empirical interval coverage'],
            'classification':['Brier vs calibration prevalence','ROC-AUC','PR-AUC','ECE 10 equal-count bins','calibration slope/intercept'],
            'stability':['annual folds','strategy','year','symbol/date concentration','missingness groups'],
            'baselines':['calibration median/prevalence','ATR-only Ridge/logistic','original frozen PB-R2 archived performance on common identities where possible; report different population/source explicitly'],
            'promotion':'research GO only if beats unconditional MAE/Brier in >=2/3 annual folds with same-direction bucket differences in >=3 strategies; decision authority requires separately authorized prospective gate'},
        'pb_r3b':{'formula':'new predicted_MFE/max(abs(new predicted_MAE),new causal_floor)',
            'floor':'10th percentile abs(MAE prediction) in calibration predictions, lower bound1e-6; frozen before validation',
            'buckets':'calibration ratio tertiles; LOW/MIDDLE/HIGH; freeze per annual artifact, never reuse PB-R3 thresholds',
            'validation':'report realized MFE/MAE and success/adverse by bucket, N, missingness, fold/strategy stability; no gate/score/ranking'},
        'implementation_files':['pb_native_features.py','scripts/train_pb_r2b.py','pb_r2b_frozen_inference.py','scripts/test_pb_r2b.py'],
        'required_tests':['raw source historical/live parity >=5000 rows','missing masks','same-date full qualified rank','future perturbation','corporate action quarantine','label barriers/ties/neither','purged splits','serialization and artifact hashes','no UI fit/provider calls','unchanged baseline fingerprint'],
        'delivery':['new feature manifest and hashes','new calibration artifacts','new PB-R3B boundaries','comparison/coverage report','disabled shadow integration until acceptance']}


def main():
    OUT.mkdir(parents=True,exist_ok=True); c=contract();write('pb_feature_contract_v1.json',c)
    write('production_mapping_manifest.json',{'counts':dict(Counter(r['mapping_status'] for r in c['features'])),'mapping':c['features']})
    write('pb_r2b_production_native_spec.json',retraining_spec(c))
    features=[r['feature'] for r in c['features']]
    data=pd.read_parquet(RESEARCH/'causal_feature_dataset.parquet')
    data.trade_date=pd.to_datetime(data.trade_date)
    # Full sampled cross-sections on four fixed ordinal dates in every year;
    # no target/outcome file is opened or used for selection.
    dates=[]
    for year,g in data.groupby(data.trade_date.dt.year):
        ds=sorted(g.trade_date.unique());dates.extend(ds[int((len(ds)-1)*q)] for q in (.1,.35,.65,.9))
    ref=data[data.trade_date.isin(dates)].copy()
    pop=ref[['decision_id','canonical_security_id','trade_date','population','primary_strategy']].copy()
    columns=['trade_date','canonical_security_id','adjusted_open','adjusted_high','adjusted_low','adjusted_close','volume','traded_value_inr']
    start=time.perf_counter()
    bars=pd.concat([pd.read_parquet(p,columns=columns) for p in sorted((LAYERS/'04_research_adjusted_ohlcv').glob('research_adjusted_eq_*.parquet'))],ignore_index=True)
    bench=pd.read_parquet(LAYERS/'06_benchmark_series/benchmarks.parquet')
    load_seconds=time.perf_counter()-start;start=time.perf_counter()
    result=build_batch(bars,pop,bench,max(dates),price_contract=PRICE_CONTRACT,universe_contract=UNIVERSE_CONTRACT)
    seconds=time.perf_counter()-start
    a=ref.set_index('decision_id').sort_index();b=result.set_index('decision_id').reindex(a.index)
    rows=[]
    for name in features:
        x=pd.to_numeric(a[name],errors='coerce'); y=pd.to_numeric(b[name],errors='coerce')
        finite=np.isfinite(x)&np.isfinite(y); diff=(x[finite]-y[finite]).abs(); masks=int((x.isna()!=y.isna()).sum())
        mismatch=int((diff>1e-10).sum()); worst=diff.idxmax() if len(diff) else None
        rows.append({'feature':name,'n_compared':int(finite.sum()),'n_both_missing':int((x.isna()&y.isna()).sum()),
            'mismatched_missingness':masks,'match_pct':float((diff<=1e-10).mean()*100) if len(diff) else None,
            'mean_absolute_error':float(diff.mean()) if len(diff) else None,'max_absolute_error':float(diff.max()) if len(diff) else None,
            'max_relative_error':float((diff/np.maximum(x[finite].abs(),1e-300)).max()) if len(diff) else None,
            'worst_example':str(worst),'mismatches':mismatch,'historical_formula_status':'PASS' if not masks and not mismatch else 'FAIL',
            'live_contract_status':next(r['mapping_status'] for r in c['features'] if r['feature']==name)})
    write('parity_report.json',{'status':STATUS,'rows':len(ref),'dates':len(dates),'years':sorted(data.trade_date.dt.year.unique().tolist()),
        'strategies':sorted(ref.primary_strategy.dropna().unique().tolist()),'historical_formula_passes':sum(r['historical_formula_status']=='PASS' for r in rows),
        'features':rows,'pb_r2_inference':'NOT_RUN: live feature source gate fails','pb_r3':'NOT_RUN: prerequisite live gate fails',
        'interpretation':'Historical archival formula parity is not evidence of live source equivalence. No production shadows activated.'})
    write('performance_report.json',{'archive_load_seconds':load_seconds,'batch_seconds':seconds,'rows':len(result),'securities':int(pop.canonical_security_id.nunique()),
        'database_queries':0,'provider_calls':0,'ui_imported':False,'live_eod_runtime':'NOT_CERTIFIED: exact raw source unavailable'})
    write('causal_provenance_report.json',{'max_source_session':str(max(dates)),'outcome_files_opened':False,'autopaper_holdout_opened':False,
        'archive_last_session':str(bars.trade_date.max()),'benchmark_last_session':str(bench.trade_date.max()),
        'prices':'causal forward split/bonus chain; available historical only',
        'live_blockers':['Missing live exchange traded value','Missing full historical-liquid NSE eligibility/sample','Yahoo adjustment includes different actions; EWM history seed differs','Exact NSE India VIX values absent from PB live payload'],
        'production_evidence':['screener.py: auto_adjust=True and runtime history exports','eod_pipeline.py: runtime stock/Nifty500 only','ha_data_foundation.py: forward split/bonus and eligibility','latent_state_vector.py: payload approximations'],
        'source_sha256':c['source_sha256'],'decision_authority':False})
    print(json.dumps({'rows':len(ref),'historical_formula_passes':sum(r['historical_formula_status']=='PASS' for r in rows),'mapping':dict(Counter(r['mapping_status'] for r in c['features'])),'batch_seconds':seconds,'verdict':STATUS}))


if __name__=='__main__':main()
