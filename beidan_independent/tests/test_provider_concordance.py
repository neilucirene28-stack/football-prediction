import unittest
from beidan_bd1.provider_concordance import compare_native_fixture


class ConcordanceTest(unittest.TestCase):
    def inputs(self):
        binding={'provider_home':'Cerezo Osaka','provider_away':'Yokohama F. Marinos',
                 'kickoff_at':'2026-10-10T05:00:00Z','season_year':2026,'season_type':14287,
                 'provider_league_id':'750','proposed_provider_event_id':'100'}
        row={'home':'Cerezo Osaka','away':'Yokohama F. Marinos',
             'kickoff_at':'2026-10-10T13:00:00+08:00','provider_match_id':2,
             'provider_home_id':3,'provider_away_id':4,'status':'NS',
             'league':{'id':98,'season':2027}}
        return binding,row

    def test_equal_utc_and_names_do_not_approve_different_season_labels(self):
        b,r=self.inputs();out=compare_native_fixture(b,[r])
        self.assertTrue(out['unique_literal_name_and_utc_match'])
        self.assertFalse(out['matches'][0]['season_year_label_equal'])
        self.assertFalse(out['cross_provider_id_join_approved'])
        self.assertFalse(out['season_stage_equivalence_approved'])

    def test_suffix_swaps_and_duplicate_events_are_not_unique_matches(self):
        b,r=self.inputs()
        self.assertFalse(compare_native_fixture(b,[r,r])['unique_literal_name_and_utc_match'])
        for different in [dict(r,home='Cerezo Osaka United'),
                          dict(r,home=r['away'],away=r['home']),
                          dict(r,kickoff_at='2026-10-10T05:01:00Z')]:
            self.assertEqual(compare_native_fixture(b,[different])['literal_name_and_utc_match_n'],0)
