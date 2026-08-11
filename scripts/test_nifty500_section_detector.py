import os
import unittest
from scripts.nifty500_section_extractor import extract_nifty500_events_generic, PROTOTYPE_DOCS


class TestNifty500SectionDetector(unittest.TestCase):

    def test_prototype_event_counts(self):
        """
        Verify exact ground-truth event counts for all 5 prototype documents:
        March 2024: 34 Adds / 34 Dels = 68
        September 2024: 27 Adds / 27 Dels = 54
        March 2023: 20 Adds / 20 Dels = 40
        September 2023: 5 Adds / 6 Dels = 11
        March 2022: 32 Adds / 32 Dels = 64
        Total: 118 Adds / 119 Dels = 237 Events
        """
        expected_counts = {
            "ind_prs28022024.pdf": (34, 34, 68),
            "ind_prs23082024.pdf": (27, 27, 54),
            "ind_prs17022023_1.pdf": (20, 20, 40),
            "ind_prs23082023.pdf": (5, 6, 11),
            "ind_prs24022022_1.pdf": (32, 32, 64)
        }

        total_adds = 0
        total_dels = 0
        total_events = 0

        for fname, period in PROTOTYPE_DOCS:
            res = extract_nifty500_events_generic(fname, period)
            self.assertEqual(res["section_detection_status"], "PASS")

            exp_adds, exp_dels, exp_tot = expected_counts[fname]
            self.assertEqual(res["additions_count"], exp_adds, f"Adds mismatch for {fname}")
            self.assertEqual(res["deletions_count"], exp_dels, f"Dels mismatch for {fname}")
            self.assertEqual(res["total_events"], exp_tot, f"Total events mismatch for {fname}")

            total_adds += res["additions_count"]
            total_dels += res["deletions_count"]
            total_events += res["total_events"]

        self.assertEqual(total_adds, 118)
        self.assertEqual(total_dels, 119)
        self.assertEqual(total_events, 237)

    def test_negative_test_bscdcl_absent(self):
        """
        Verify BSCDCL (Municipal Bond Index on Page 35 of March 2024 PDF) is NOT extracted into Nifty 500 events.
        """
        res = extract_nifty500_events_generic("ind_prs28022024.pdf", "March 2024")
        syms = [e["symbol"] for e in res["events"]]
        self.assertNotIn("BSCDCL", syms, "BSCDCL must NOT be present in Nifty 500 events!")

    def test_absent_document_returns_not_present(self):
        """
        Verify PDF without Nifty 500 section returns status NOT_PRESENT and empty events without throwing an exception.
        """
        res = extract_nifty500_events_generic("ind_prs24032026.pdf", "March 2026")
        self.assertIn(res["section_detection_status"], ["NOT_PRESENT", "FAILED"])
        self.assertEqual(len(res["events"]), 0)


if __name__ == "__main__":
    unittest.main()
