"""Offline feature/source-contract tests; no model fitting or production writes."""
import ast
import hashlib
import json
from pathlib import Path
import sys
import unittest

import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from pb_canonical_features import security_features,benchmark_features,build_batch,PRICE_CONTRACT,UNIVERSE_CONTRACT
from scripts.run_pb_p1c_contract_audit import contract,retraining_spec,OUT


def bars(n=100):
    c=100+np.arange(n)*.2+np.sin(np.arange(n))
    return pd.DataFrame({'trade_date':pd.bdate_range('2020-01-01',periods=n),'canonical_security_id':'A',
        'adjusted_close':c,'adjusted_open':c-.3,'adjusted_high':c+1,'adjusted_low':c-1,
        'volume':1000+np.arange(n)*3,'traded_value_inr':(1000+np.arange(n)*3)*(c-.1)})


class ContractTests(unittest.TestCase):
    def test_manifest_36_complete(self):
        c=contract();self.assertEqual(len(c['features']),36)
        for row in c['features']:
            for key in ('exact_formula','raw_inputs','lookback','minimum_observations','missingness','causal_cutoff','source','dtype','absolute_tolerance'):
                self.assertIsNotNone(row[key])

    def test_research_oracle_including_insufficient_history(self):
        # Extract only the authoritative feature prefix. Never execute its
        # forward outcome loop, imports, fitting or data access.
        tree=ast.parse((ROOT/'predictability_boundary_research.py').read_text())
        fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_security_state_and_outcomes')
        prefix=[]
        for node in fn.body:
            if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='positions' for t in node.targets):break
            prefix.append(node)
        fn.body=prefix+[ast.Return(value=ast.Name(id='state',ctx=ast.Load()))]
        namespace={'pd':pd,'np':np,'_safe_div':lambda a,b:a.div(b.where(b.ne(0)))}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[fn],type_ignores=[])),'oracle','exec'),namespace)
        for count in (10,61,100):
            raw=bars(count);a=namespace[fn.name](raw,None);b=security_features(raw,raw.trade_date.max())
            pd.testing.assert_frame_equal(a.drop(columns='feature_source_max_date'),b.drop(columns='history_position'),atol=1e-10,rtol=0)

    def test_future_bars_do_not_change_features(self):
        raw=bars();cut=raw.trade_date.iloc[75];a=security_features(raw,cut)
        raw.loc[raw.trade_date>cut,'adjusted_close']=1e12
        pd.testing.assert_frame_equal(a,security_features(raw,cut))

    def test_zero_denominators_missing_and_extremes(self):
        raw=bars();raw['volume']=0;raw['traded_value_inr']=0
        raw['adjusted_high']=raw.adjusted_close;raw['adjusted_low']=raw.adjusted_close
        s=security_features(raw,raw.trade_date.max())
        self.assertTrue(s.volume_ratio.isna().all());self.assertTrue(s.close_location_value.isna().all())
        self.assertTrue(s.log_amihud_impact_20d.isna().all())

    def test_population_rank_before_requested_selection(self):
        raw=bars();other=raw.copy();other.canonical_security_id='B';other.adjusted_close*=1.2
        date=raw.trade_date.max();p=pd.DataFrame({'canonical_security_id':['A','B'],'trade_date':[date,date]})
        benchmark=pd.DataFrame({'trade_date':raw.trade_date,'index_name':'Nifty 500','close':raw.adjusted_close})
        d=build_batch(pd.concat([raw,other]),p,benchmark,date,price_contract=PRICE_CONTRACT,universe_contract=UNIVERSE_CONTRACT)
        self.assertEqual(len(d),2);self.assertTrue(d.india_vix.isna().all())
        self.assertEqual(d.traded_value_percentile.tolist(),[75.,75.])
        self.assertEqual(d.market_breadth_ema20.nunique(),1)

    def test_future_benchmark_vix(self):
        raw=bars();b=pd.DataFrame({'trade_date':raw.trade_date,'index_name':'Nifty 500','close':raw.adjusted_close})
        v=b.copy();v.index_name='India VIX';v.close=15.;b=pd.concat([b,v])
        cut=raw.trade_date.iloc[70];a=benchmark_features(b,cut);b.loc[b.trade_date>cut,'close']=1e12
        pd.testing.assert_frame_equal(a,benchmark_features(b,cut));self.assertEqual(a.india_vix.iloc[-1],15.)

    def test_source_contract_rejected(self):
        with self.assertRaisesRegex(ValueError,'CONTRACT_NOT_PROVEN'):
            build_batch(None,None,None,'2026-01-01',price_contract='YAHOO_AUTO_ADJUST',universe_contract=UNIVERSE_CONTRACT)

    def test_historical_parity_report(self):
        r=json.loads((OUT/'parity_report.json').read_text())
        self.assertGreaterEqual(r['rows'],5000);self.assertEqual(r['historical_formula_passes'],36)
        self.assertTrue(all(x['mismatched_missingness']==0 for x in r['features']))

    def test_fail_closed_and_retraining_complete(self):
        spec=retraining_spec(contract());self.assertEqual(spec['feature_count'],35)
        self.assertFalse(spec['decision_authority']);self.assertFalse(spec['reuse_old_coefficients_or_thresholds'])
        for key in ('targets','split_protocol','model_selection','evaluation','pb_r3b','required_tests'):self.assertTrue(spec[key])
        m=json.loads((ROOT/'data/production/predictability/pb_p1_manifest.json').read_text())
        self.assertFalse(m['decision_authority']);self.assertNotEqual(m.get('activation_status'),'ACTIVE')

    def test_frozen_identity_no_runtime_import(self):
        p=ROOT/'data/production/predictability/pb_r2_frozen_inference_v1.joblib'
        self.assertEqual(hashlib.sha256(p.read_bytes()).hexdigest(),'5d06576fb89ea3d9d6619d07dbf6b08fc5a07e88ad4456fb623b5d7cf53ea0fd')
        for path in ('app.py','eod_pipeline.py','cockpit_ui.py','autopaper_prospective.py'):
            self.assertNotIn('pb_canonical_features',(ROOT/path).read_text())


if __name__=='__main__':unittest.main()
