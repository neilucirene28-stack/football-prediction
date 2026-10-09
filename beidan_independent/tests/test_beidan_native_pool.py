import copy
import unittest
from beidan_bd1.native_pool import collect_batches


def batch(seq=18, league='1', eid='100'):
    return {'verified_at': '2026-10-09T12:00:00Z',
            'results': [{'record': {'match_id': 'espn:' + eid,
                'provider_league_id': league, 'season_year': 2026, 'season_type': 1,
                'verified_at': '2026-10-09T12:00:00Z'}}],
            'research_predictions': [{'seq': seq,
                'status': 'native_research_binding_unapproved',
                'binding': {'proposed_provider_event_id': eid + '0',
                            'canonical_identity_approved': False}}]}


class NativePoolTest(unittest.TestCase):
    def collect(self, *batches):
        return collect_batches(list(batches), cutoff_at='2026-10-09T12:30:00Z')

    def test_two_leagues_keep_their_stages(self):
        history, bindings, research = self.collect(batch(), batch(98, '2', '200'))
        self.assertEqual(len(history), 2)
        self.assertEqual(bindings[98]['provider_league_id'], '2')
        self.assertEqual(set(research), {18, 98})

    def test_late_archive_cannot_enter_frozen_pool(self):
        b = batch(); b['verified_at'] = '2026-10-09T13:00:00Z'
        with self.assertRaises(ValueError): self.collect(b)
        b = batch(); b['results'][0]['record']['verified_at'] = '2026-10-09T13:00:00Z'
        with self.assertRaises(ValueError): self.collect(b)

    def test_one_provider_fixture_cannot_bind_two_sequences(self):
        with self.assertRaises(ValueError): self.collect(batch(), batch(98))

    def test_one_sequence_cannot_bind_two_provider_fixtures(self):
        with self.assertRaises(ValueError): self.collect(batch(), batch(18, '2', '200'))

    def test_mixed_stage_history_is_rejected(self):
        b = batch(); other = copy.deepcopy(b['results'][0])
        other['record']['season_type'] = 2; b['results'].append(other)
        with self.assertRaises(ValueError): self.collect(b)

    def test_conflicting_history_is_not_silently_overwritten(self):
        b = batch(98); b['results'][0]['record']['ft_home'] = 7
        with self.assertRaises(ValueError): self.collect(batch(), b)

    def test_canonical_approval_cannot_be_claimed(self):
        b = batch(); b['research_predictions'][0]['binding']['canonical_identity_approved'] = True
        with self.assertRaises(ValueError): self.collect(b)

    def test_blocked_forecast_retains_binding_but_no_prediction(self):
        b = batch(); b['research_predictions'][0]['status'] = 'blocked'
        _, bindings, research = self.collect(b)
        self.assertEqual(set(bindings), {18}); self.assertEqual(research, {})


if __name__ == '__main__': unittest.main()
