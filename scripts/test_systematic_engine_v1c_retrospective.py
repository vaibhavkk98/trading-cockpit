#!/usr/bin/env python3
from __future__ import annotations
import sys
from pathlib import Path
import pandas as pd
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import opportunity_selection_engine as s
import systematic_engine_v1c_retrospective as r

passed=0
def check(x):
 global passed; assert x; passed+=1

def main():
 c=r.frozen_contract();check(c["feature_manifest_hash"]=="4131cb801cce743146e26b0dae66f08ab93b53fc7a57952868f6b8448f6bb4c1")
 check(c["c1_methodology_hash"]=="8cf389689f461eb0769ecc64311929f61a9402aaca91191fac9738ab57ff8497")
 check(c["c2_methodology_hash"]=="f087fb4cbd127a41f13739ad8cbee6aa28adcdf454c4ea30dce0ea9c0e19da38")
 check(c["random_manifest_hash"]=="2f05b0c8b888f6fe8dbbdf59c0b4f589d09be5fac78d3382ed076765b7292a22" and len(c["random_seeds"])==20)
 raw=[{"opportunity_id":"A","decision_id":"A","signal_date":"2020-01-01","signal_session":1,"selection_inputs":{"features":{x:1 for x in s.FEATURE_MANIFEST["features"]},"risk_proxy_pct":2,"eligible":True}},
      {"opportunity_id":"B","decision_id":"B","signal_date":"2020-01-01","signal_session":1,"selection_inputs":{"features":{x:2 for x in s.FEATURE_MANIFEST["features"]},"risk_proxy_pct":8,"eligible":True}}]
 check([x["decision_id"] for x in r._order(raw,"C1","2020-01-01",None)]==["B","A"])
 check(r._order(raw,"C2","2020-01-01",None)==r._order(raw,"C2","2020-01-01",None))
 check(r._order(raw,"R0","2020-01-01",s.RANDOM_SEEDS[0])==r._order(raw,"R0","2020-01-01",s.RANDOM_SEEDS[0]))
 check(r.rolling_budget(0)==2 and r.rolling_budget(5)==1 and r.rolling_budget(9)==0)
 frame=pd.DataFrame([{"decision_id":"A","opportunity_id":"A","trade_date":"2024-02-16","canonical_security_id":"X","symbol":"X","primary_strategy":"VCP","sector":"UNKNOWN","traded_value":1e8,"feature_source_max_date":"2024-02-16","excess_20d":1,"volume_ratio":2,"close_location_value":.8,"ema20_extension":3,"atr_pct":2,"realized_vol_20d":20}])
 try:r.prepare_signals(frame);check(False)
 except RuntimeError as e:check(str(e)=="AUTOPAPER_HOLDOUT_SEALED")
 check(r.LABEL=="RETROSPECTIVE_DIAGNOSTIC" and c["authority"]=="DIAGNOSTIC_ONLY_NO_PRODUCTION_CHANGE")
 check(r.digest(c)==r.digest(r.frozen_contract()))
 sessions=[str(x.date()) for x in pd.bdate_range("2020-01-01",periods=15)]
 bars={date:{"X":(100.,102.,99.,101.,1_000_000.,100_000_000.)} for date in sessions}
 signal={"decision_id":"S","opportunity_id":"S","trade_date":sessions[0],"signal_date":sessions[0],
     "canonical_security_id":"X","symbol":"X","primary_strategy":"VCP","sector":"UNKNOWN","traded_value":100_000_000.,
     "selection_inputs":{"features":{name:1. for name in s.FEATURE_MANIFEST["features"]},"atr_pct":2.,
         "rv20_daily_pct":1.,"risk_proxy_pct":2.,"risk_proxy_coverage":"ATR_AND_RV20","eligible":True}}
 replay_a=r.replay([signal],sessions,bars,"C0"); replay_b=r.replay([signal],sessions,bars,"C0")
 check(replay_a["result_hash"]==replay_b["result_hash"] and replay_a["metrics"]["completed_trades"]==1)
 print(f"Systematic Engine V1C-R focused tests: {passed} passed")
if __name__=="__main__":main()
