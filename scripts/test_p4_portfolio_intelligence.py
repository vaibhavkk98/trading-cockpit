import os,sys,unittest,pandas as pd
sys.path.insert(0,os.path.abspath(os.path.join(os.path.dirname(__file__),'..')))
from scripts.run_p4_portfolio_intelligence import DATA,select
class TestP4(unittest.TestCase):
 def test_candidates_reconcile_and_capital(self):
  x=select(pd.read_csv(DATA).head(50),2); self.assertEqual(len(x),50); self.assertTrue((x.capital_impact_inr<=100000).all()); self.assertTrue(set(x.status).issubset({'ALLOCATED','QUALIFIED — CAPITAL CAP','QUALIFIED — SECTOR CONCENTRATION'}))
if __name__=='__main__':unittest.main()
