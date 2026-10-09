import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('registry', Path(__file__).resolve().parents[1] / 'scripts/register_prospective_freezes.py')
registry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(registry)


class RegistryTests(unittest.TestCase):
    def test_earliest_forecast_wins_even_if_input_is_reversed(self):
        fixture = dict(period='p', provider_match_id='1', provider_home_id='2', provider_away_id='3',
                       provider_league_id='4', season_year=2026, season_type=5, kickoff_at='2026-10-10T00:00:00Z')
        with tempfile.TemporaryDirectory() as tmp:
            early, late = [Path(tmp) / x for x in ('early','late')]
            for folder, sealed in [(early,'2026-10-09T01:00:00Z'), (late,'2026-10-09T02:00:00Z')]:
                folder.mkdir();(folder/'freeze_manifest.json').write_text(json.dumps({'sealed_at':sealed}))
            with patch.object(registry, 'load_bundle', return_value=({}, {'m':fixture}, {'m':{}})):
                result = registry.build([(str(late),'latehash'),(str(early),'earlyhash')])
            self.assertEqual(result['unique_predictions_n'],1)
            self.assertEqual(result['later_duplicate_predictions_excluded_n'],1)
            self.assertEqual(result['rows'][0]['manifest_sha256'],'earlyhash')

    def test_changed_binding_is_rejected_instead_of_selecting_a_version(self):
        fixture = dict(period='p', provider_match_id='1', provider_home_id='2', provider_away_id='3',
                       provider_league_id='4', season_year=2026, season_type=5, kickoff_at='2026-10-10T00:00:00Z')
        with tempfile.TemporaryDirectory() as tmp:
            paths=[]
            for n in (1,2):
                folder=Path(tmp)/str(n);folder.mkdir()
                (folder/'freeze_manifest.json').write_text(json.dumps({'sealed_at':f'2026-10-09T0{n}:00:00Z'}))
                paths.append((str(folder),'digest'))
            values=[({}, {'m':fixture}, {'m':{}}),({}, {'m':{**fixture,'provider_home_id':'999'}}, {'m':{}})]
            with patch.object(registry,'load_bundle',side_effect=values):
                with self.assertRaises(ValueError):registry.build(paths)
