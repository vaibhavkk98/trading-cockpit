import unittest
import pandas as pd
from universe_engine import (
    get_universe_as_of,
    is_constituent,
    get_universe_metadata,
    get_security_universe_as_of,
    HistoricalUniverseNotVerifiedError
)


class TestUniverseEngine(unittest.TestCase):

    def test_current_anchor_snapshot(self):
        univ = get_universe_as_of("2026-08-10", mode="research")
        self.assertEqual(len(univ), 500)
        self.assertIn("RELIANCE", univ)
        self.assertIn("TCS", univ)

    def test_strict_mode_boundary_enforcement(self):
        # 2026 date allowed in strict mode
        univ_recent = get_universe_as_of("2026-03-31", mode="strict")
        self.assertEqual(len(univ_recent), 497)

        # 2020 date raises HistoricalUniverseNotVerifiedError in strict mode
        with self.assertRaises(HistoricalUniverseNotVerifiedError):
            get_universe_as_of("2020-03-31", mode="strict")

    def test_research_mode_pre_2024(self):
        univ_hist = get_universe_as_of("2020-03-31", mode="research")
        self.assertGreater(len(univ_hist), 400)

    def test_is_constituent_symbol_alias_mapping(self):
        # LTI mapped to LTM
        is_lti = is_constituent("LTI", "2026-08-10", mode="research")
        self.assertTrue(is_lti)

        is_ltm = is_constituent("LTM", "2026-08-10", mode="research")
        self.assertTrue(is_ltm)

    def test_universe_metadata(self):
        meta = get_universe_metadata("2026-08-10")
        self.assertEqual(meta["evidence_status"], "OFFICIAL_CURRENT_SNAPSHOT")
        self.assertEqual(meta["universe_count"], 500)

        meta_hist = get_universe_metadata("2020-03-31")
        self.assertEqual(meta_hist["evidence_status"], "UNVERIFIED_RECONSTRUCTION")
        self.assertEqual(meta_hist["survivorship_bias_risk"], "HIGH")

    def test_security_universe(self):
        sec_univ = get_security_universe_as_of("2026-08-10", mode="research")
        self.assertEqual(len(sec_univ), 500)
        self.assertIn("security_id", sec_univ[0])
        self.assertIn("canonical_symbol", sec_univ[0])


if __name__ == "__main__":
    unittest.main()
