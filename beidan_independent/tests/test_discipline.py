import copy
import hashlib
import json
from pathlib import Path
import unittest

from beidan_bd1.artifact_io import read_artifact
from beidan_bd1.discipline import extract_observation, team_features

ROOT = Path(__file__).resolve().parents[1]


class DisciplineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        folder=ROOT/'data_sample/espn_esp.1_mined_20261010_v25'
        audit=json.loads(read_artifact(ROOT/'outputs/v25_native/esp.1/audit_and_predictions.json'))
        cls.schedule=next(r['record'] for r in audit['results'] if r['record']['provider_match_id']=='401882851')
        cls.receipt=next(r for r in json.loads((folder/'VERIFIED_FILE_INDEX.json').read_bytes())['files'] if r.get('event_id')=='401882851')
        cls.raw=read_artifact(folder/cls.receipt['local_file'])

    def observation(self, raw=None):
        raw=self.raw if raw is None else raw
        return extract_observation(raw,schedule=self.schedule,league_slug='esp.1',
                                   source_available_at=self.receipt['end_utc'],
                                   verified_at='2026-10-10T02:55:00+00:00',
                                   expected_sha256=hashlib.sha256(raw).hexdigest())

    def features(self, rows, **kwargs):
        return team_features(rows,team_id='99',league_id='740',season_year=2026,season_type=14357,
                             asof_at='2026-10-10T03:00:00+00:00',**kwargs)

    def test_real_raw_statistics_have_no_unvalidated_goal_effect(self):
        row=self.observation()
        self.assertEqual(row['sides']['home']['yellow_cards'],4)
        self.assertEqual(row['sides']['away']['yellow_cards'],5)
        out=self.features([row]);self.assertEqual(out['all_observed']['yellow_cards']['observed_total'],4)
        self.assertIsNone(out['full_season_total']);self.assertIsNone(out['on_field_dismissal_probability'])
        self.assertFalse(out['goal_model_adjusted'])

    def test_missing_card_field_is_unknown_not_zero(self):
        value=json.loads(self.raw)
        stats=value['boxscore']['teams'][0]['statistics']
        value['boxscore']['teams'][0]['statistics']=[r for r in stats if r['name']!='yellowCards']
        row=self.observation(json.dumps(value).encode());out=self.features([row])
        self.assertIsNone(out['all_observed']['yellow_cards']['observed_total'])
        self.assertEqual(out['all_observed']['yellow_cards']['missing_matches_n'],1)

    def test_wrong_team_and_duplicate_statistics_rejected(self):
        for mutate in ('wrong_team','duplicate'):
            value=json.loads(self.raw)
            if mutate=='wrong_team':value['boxscore']['teams'][0]['team']['id']='999999'
            else:value['boxscore']['teams'][0]['statistics'].append(copy.deepcopy(value['boxscore']['teams'][0]['statistics'][0]))
            with self.assertRaises(ValueError):self.observation(json.dumps(value).encode())

    def test_future_observation_and_current_event_excluded(self):
        row=self.observation();future=copy.deepcopy(row);future['verified_at']='2026-10-11T00:00:00+00:00'
        self.assertEqual(self.features([future])['all_observed']['observed_matches_n'],0)
        self.assertEqual(self.features([row],exclude_match_id=row['provider_match_id'])['all_observed']['observed_matches_n'],0)

    def test_repeated_event_not_extra_match_and_conflicting_stage_rejected(self):
        row=self.observation()
        self.assertEqual(self.features([row,copy.deepcopy(row)])['all_observed']['observed_matches_n'],1)
        other=copy.deepcopy(row);other['season_type']=0
        with self.assertRaises(ValueError):self.features([row,other])
        self.assertEqual(self.features([other])['all_observed']['observed_matches_n'],0)

    def test_noninteger_count_and_raw_digest_rejected(self):
        value=json.loads(self.raw);value['boxscore']['teams'][0]['statistics'][1]['displayValue']='1.5'
        with self.assertRaises(ValueError):self.observation(json.dumps(value).encode())
        with self.assertRaises(ValueError):
            extract_observation(self.raw+b' ',schedule=self.schedule,league_slug='esp.1',
                source_available_at=self.receipt['end_utc'],verified_at='2026-10-10T02:55:00+00:00',expected_sha256=self.receipt['sha256'])
