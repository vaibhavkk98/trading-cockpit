import os, sys, hashlib, unittest
ROOT=os.path.abspath(os.path.join(os.path.dirname(__file__),"..")); sys.path.insert(0,ROOT)
from scripts.run_p1_3_confirmation import frozen_definition, select_bucket
import pandas as pd

def sha(rel):
    with open(os.path.join(ROOT,rel),"rb") as f:return hashlib.sha256(f.read()).hexdigest()

class TestP13Confirmation(unittest.TestCase):
    protected=["scripts/run_mvp.py","config/mvp_config.yaml","data/ml/step_10c/step_10c_comparison.csv"]
    def test_01_validation_definitions_are_frozen(self):
        val=pd.DataFrame({"return_20d":[1.,2.,3.,4.,100.]}); test=pd.DataFrame({"return_20d":[-100.,0.,1.,2.,3.]})
        definition=frozen_definition(val,"return_20d"); self.assertEqual(definition[-1][0],4.0)
        self.assertEqual(len(select_bucket(test,"return_20d",definition[-1][0],definition[-1][1])),0)
    def test_02_no_forward_data_in_bucket_feature(self):
        df=pd.DataFrame({"upper_wick_pct_of_range":[.1,.2,.3,.4]}); self.assertEqual(len(frozen_definition(df,"upper_wick_pct_of_range")),4)
    def test_03_counts_and_frozen_artifacts(self):
        before={p:sha(p) for p in self.protected}; d=pd.read_csv(os.path.join(ROOT,"data/research/p1_qualified_signals.csv")); self.assertEqual(len(d),1009); self.assertEqual(before,{p:sha(p) for p in self.protected})
if __name__=="__main__":unittest.main()
