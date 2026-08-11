import os,unittest,pandas as pd
class TestBridge(unittest.TestCase):
 def test_bridge_is_one_to_one(self):
  b=pd.read_csv('data/foundation/opportunity_execution_bridge.csv'); self.assertEqual(len(b),526); m=b[b.match_status=='MATCHED_EXACT']; self.assertEqual(m.execution_id.nunique(),len(m)); self.assertTrue((b.match_status.isin(['MATCHED_EXACT','MATCHED_HIGH_CONFIDENCE','AMBIGUOUS','UNMATCHED_NOT_ALLOCATED','UNMATCHED_CONTRACT_GAP'])).all())
if __name__=='__main__':unittest.main()
