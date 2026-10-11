import unittest

from beidan_bd1.football_data_csv import audit_csv
from scripts.audit_extracted_csv import restore_row_boundaries


HEADER = "Div,Date,Time,HomeTeam,AwayTeam,FTHG,FTAG,FTR,HTHG,HTAG,HTR,B365CH,AHh\n"
GOOD = "E3,02/08/2025,15:00,Home,Away,2,1,H,1,0,H,1.25,-0.5\n"


def audit(text):
    return audit_csv(text.encode(), league_code="E3", season_code="2526",
                     source_url="https://www.football-data.co.uk/mmz4281/2526/E3.csv",
                     verified_at="2026-10-09T14:00:00+00:00")


class CSVProvenanceTests(unittest.TestCase):
    def test_score_observation_does_not_acquire_identity_or_odds(self):
        rows, report = audit(HEADER + GOOD)
        self.assertEqual(rows[0]["ht_home"], 1)
        self.assertNotIn("kickoff_at", rows[0])
        self.assertNotIn("result_available_at", rows[0])
        self.assertNotIn("provider_home_id", rows[0])
        self.assertNotIn("B365CH", rows[0])
        self.assertFalse(report["canonical_identity_approved"])
        self.assertFalse(report["odds_or_handicap_imported"])

    def test_missing_halftime_is_not_zero(self):
        rows, report = audit(HEADER + GOOD.replace("1,0,H,1.25", ",,,1.25"))
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0]["ht_home"])
        self.assertEqual(report["explicit_ht_n"], 0)

    def test_contradictory_and_fractional_scores_are_rejected(self):
        for row in (GOOD.replace("2,1,H", "2,1,A"), GOOD.replace("1,0,H,1.25", "3,0,H,1.25"),
                    GOOD.replace("2,1,H", "2.5,1,H"), GOOD.replace("2,1,H", "-1,1,H")):
            with self.subTest(row=row):
                rows, report = audit(HEADER + row)
                self.assertEqual(rows, [])
                self.assertEqual(len(report["rejected"]), 1)

    def test_duplicates_do_not_double_count_and_conflicts_fail_file(self):
        rows, report = audit(HEADER + GOOD + GOOD)
        self.assertEqual(len(rows), 1)
        self.assertEqual(report["exact_duplicate_n"], 1)
        with self.assertRaisesRegex(ValueError, "conflicting_duplicate"):
            audit(HEADER + GOOD + GOOD.replace("2,1,H", "3,1,H"))

    def test_division_future_date_and_missing_columns_are_not_accepted(self):
        for row in (GOOD.replace("E3,", "E1,"), GOOD.replace("02/08/2025", "02/12/2026")):
            rows, report = audit(HEADER + row)
            self.assertEqual(rows, [])
            self.assertEqual(len(report["rejected"]), 1)
        with self.assertRaisesRegex(ValueError, "headers"):
            audit("Div,Date,HomeTeam,AwayTeam\nE3,02/08/2025,H,A\n")

    def test_flattened_extraction_requires_unambiguous_row_boundaries(self):
        flat = (HEADER + GOOD + GOOD.replace("Home,Away", "Other,Third")).replace("\n", " ").strip()
        restored, report = restore_row_boundaries(flat, "E3")
        rows, _ = audit(restored)
        self.assertEqual(len(rows), 2)
        self.assertEqual(report["boundaries_restored_n"], 2)
        with self.assertRaisesRegex(ValueError, "quoted"):
            restore_row_boundaries(flat.replace("Home", '"Home"'), "E3")


if __name__ == "__main__":
    unittest.main()
