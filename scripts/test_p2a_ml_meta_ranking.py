import os,sys,unittest,pandas as pd
sys.path.insert(0,os.path.abspath(os.path.join(os.path.dirname(__file__),'..')))
from scripts.run_step_4f_embargo import apply_embargo
from scripts.run_p2a_ml_meta_ranking import DATA,NUM,CAT,LEAKAGE,TARGET
class TestP2A(unittest.TestCase):
 def test_temporal_split_and_target(self):
  d=pd.read_csv(DATA); s=apply_embargo(d,10); self.assertEqual(len(d),1009); self.assertEqual(len(s['val']),240); self.assertEqual(len(s['test']),129); self.assertLess(s['train'].signal_date.max(),s['val'].signal_date.min()); self.assertIn(TARGET,d); self.assertGreater(s['test'][TARGET].notna().sum(),0)
 def test_no_leakage(self): self.assertTrue(set(NUM+CAT).isdisjoint(LEAKAGE)); self.assertNotIn('symbol',NUM+CAT)
 def test_train_only_contract(self):
  code=open(os.path.join(os.path.dirname(__file__),'run_p2a_ml_meta_ranking.py')).read(); self.assertIn("m.fit(train[CAT+NUM]",code); self.assertIn('TEST was not used',code)
if __name__=='__main__':unittest.main()
