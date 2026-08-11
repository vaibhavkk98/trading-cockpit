import os,sys,unittest,pandas as pd
sys.path.insert(0,os.path.abspath(os.path.join(os.path.dirname(__file__),'..')))
from scripts.run_p4_3_canonical_ledger import P1
class TestP43(unittest.TestCase):
 def test_missing_is_explicit_and_duplicates_consolidate(self):
  p='data/research/p4_3_canonical_portfolio_ledger.csv'; self.assertTrue(os.path.exists(p)); d=pd.read_csv(p); self.assertEqual(len(d),len(pd.read_csv(P1))); self.assertEqual(d.groupby(['signal_date','symbol']).is_unique_opportunity.sum().max(),1); self.assertTrue((d.stop_available==False).all())
if __name__=='__main__':unittest.main()
