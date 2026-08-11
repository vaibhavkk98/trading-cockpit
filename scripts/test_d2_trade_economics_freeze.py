from pathlib import Path
import json,sys
ROOT=Path(__file__).resolve().parents[1];R=ROOT/'data/research';sys.path.insert(0,str(ROOT));from trade_economics_context import get_trade_economics_context
def run():
 c=json.loads((R/'d2_trade_economics_product_contract.json').read_text());p=json.loads((R/'d2_trade_economics_display_payload.json').read_text());nr=next(x for x in p if 'NR7' in x['strategy']);assert len(p)==6 and c['descriptive_only'] and not c['predictive'];assert nr['display_sample_source']=='INSUFFICIENT' and nr['target_context']['status']=='NOT_AVAILABLE';assert all(x['target_context']['status']=='NOT_AVAILABLE' for x in p);x=get_trade_economics_context(p[0]['strategy'],{'risk_reference':'X','executable_stop':'Y'});assert x['current_risk_context']['risk_reference']=='X';assert not any('edge_score' in str(x).lower() or 'predicted_return' in str(x).lower() for x in p);print('D2 focused product-freeze tests: PASS')
if __name__=='__main__':run()
