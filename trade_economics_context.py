"""Product-facing consumer of frozen D1 descriptive economics; no prediction."""
from pathlib import Path
from functools import lru_cache
import json
ROOT=Path(__file__).resolve().parent;R=ROOT/'data/research'

@lru_cache(maxsize=1)
def _frozen_display_payload():
 """Load the immutable D2 display payload once per process, not per signal."""
 return json.loads((R/'d2_trade_economics_display_payload.json').read_text())

def get_trade_economics_context(strategy,current_trade_risk_fields=None):
 try: payloads=_frozen_display_payload()
 except Exception:
  return {'strategy':strategy,'display_sample_source':'INSUFFICIENT','availability':'NOT_AVAILABLE','target_context':{'status':'NOT_AVAILABLE'},'descriptive_only':True,'predictive':False}
 base=next((x for x in payloads if x['strategy']==strategy),None)
 if base is None:return {'strategy':strategy,'display_sample_source':'INSUFFICIENT','target_context':{'status':'NOT_AVAILABLE'},'descriptive_only':True,'predictive':False}
 result=dict(base);result['current_risk_context']=current_trade_risk_fields or {'risk_reference':'NOT_AVAILABLE','executable_stop':'NO_EXECUTABLE_STOP','reference_risk_per_share':'NOT_AVAILABLE','executable_stop_risk_per_share':'NOT_AVAILABLE'};return result
