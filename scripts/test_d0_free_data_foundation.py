import os,unittest,pandas as pd
class TestD0(unittest.TestCase):
 def test_ohlcv_and_ledger_reconcile(self):
  o=pd.read_csv('data/foundation/daily_ohlcv.csv'); l=pd.read_csv('data/foundation/canonical_opportunity_ledger.csv'); self.assertEqual(o.duplicated(['security_id','date']).sum(),0); self.assertFalse(((o.high<o[['open','close','low']].max(axis=1))|(o.low>o[['open','close','high']].min(axis=1))).any()); self.assertEqual(len(l),1009); self.assertTrue((~l.sector_available | l.sector.notna()).all())
if __name__=='__main__':unittest.main()
