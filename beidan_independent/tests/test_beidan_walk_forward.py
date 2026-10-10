import copy
import unittest

from beidan_bd1.walk_forward import run_walk_forward, summarize_pairs
from test_beidan_baseline import sample_history


def case():
    history = sample_history()
    folds, results = [], []
    for day in (7, 8, 9):
        period = f"pool{day}"
        ko = f"2026-10-{day:02}T18:00:00+08:00"
        fixture = {"period": period, "match_id": period + "-1", "sport": "football", "kickoff_at": ko,
                   "fixture_source": {"name": "synthetic_test", "status": "ok",
                                      "available_at": f"2026-10-{day:02}T09:00:00+08:00"}}
        folds.append({"period": period, "cutoff_at": f"2026-10-{day:02}T10:00:00+08:00",
                      "expected_total": 1, "fixtures": [fixture]})
        label = {"period": period, "match_id": fixture["match_id"], "kickoff_at": ko,
                 "verified_at": f"2026-10-{day:02}T21:00:00+08:00", "regular_time": True,
                 "ft_home": 0, "ft_away": 0, "ht_home": 0, "ht_away": 0}
        results.append(label)
        history.append({**label, "competition_family": None})
    return history, folds, results


def run(history, folds, results):
    return run_walk_forward(history=history, folds=folds, results=results,
                            evaluated_at="2026-10-09T23:00:00+08:00", min_selection_matches=1)


class WalkForwardTests(unittest.TestCase):
    def test_prospective_expired_rows_stay_blocked_without_probability_generation(self):
        from unittest.mock import patch
        h, f, _ = case()
        fold = f[0]
        now = fold['cutoff_at']
        current = copy.deepcopy(fold['fixtures'][0])
        expired = {**current, 'match_id': 'expired', 'kickoff_at': now}
        fold.update(expected_total=2, fixtures=[expired, current])
        from beidan_bd1.walk_forward import predict_l3
        with patch('beidan_bd1.walk_forward.predict_l3', wraps=predict_l3) as predictor:
            out = run_walk_forward(history=h, folds=[fold], results=[], evaluated_at=now,
                                   allow_expired_blocked=True)
        self.assertEqual(predictor.call_count, 1)
        self.assertEqual((out['offered_n'], out['predicted_n'], out['paired_n']), (2, 1, 0))
        blocked = out['folds'][0]['matches'][0]
        self.assertEqual(blocked['status'], 'blocked')
        self.assertIn('已经开球', blocked['reason'])
        for field in ('alternatives', 'baseline', 'candidate', 'vectors', 'score_31'):
            self.assertNotIn(field, blocked)
        self.assertEqual(out['pending_ids'], [current['match_id']])

    def test_default_replay_still_rejects_expired_complete_pool(self):
        h, f, _ = case()
        f[0]['fixtures'][0]['kickoff_at'] = f[0]['cutoff_at']
        with self.assertRaisesRegex(ValueError, '已经开球'):
            run_walk_forward(history=h, folds=f[:1], results=[], evaluated_at=f[0]['cutoff_at'])

    def test_expired_mode_rejects_results_multiple_periods_or_backdated_cutoff(self):
        h, f, r = case()
        for folds, results, now in ((f[:1], r, f[0]['cutoff_at']),
                                    (f, [], f[-1]['cutoff_at']),
                                    (f[:1], [], '2026-10-09T23:00:00+08:00')):
            with self.assertRaisesRegex(ValueError, '单期'):
                run_walk_forward(history=h, folds=folds, results=results, evaluated_at=now,
                                 allow_expired_blocked=True)

    def test_actual_refit_past_only_selection_and_mean_constraint(self):
        h, f, r = case()
        report = run(h, f, r)
        self.assertEqual([x["training_n"] for x in report["folds"]], [35, 36, 37])
        self.assertEqual([x["selection_n"] for x in report["selections"]], [0, 1, 2])
        self.assertIsNone(report["selections"][0]["selected_kappa"])
        self.assertEqual(report["selections"][1]["selected_kappa"], 25.)
        self.assertEqual((report["predicted_n"], report["paired_n"]), (3, 3))
        self.assertEqual(report["prediction_coverage"], 1)
        self.assertFalse(report["production_gate_passed"])
        for fold in report["folds"]:
            row = fold["matches"][0]
            self.assertNotIn(row["match_id"], row["training_ids"])
            self.assertLess(abs(row["candidate"]["mean_drift"]), 1e-10)
            self.assertIsNone(row["candidate"]["vectors"]["handicap_wdl"])

    def test_current_future_test_labels_cannot_change_current_prediction(self):
        h, f, r = case()
        before = run(h, f, r)
        altered_h, altered_r = copy.deepcopy(h), copy.deepcopy(r)
        altered_h[-2]["ft_home"] = altered_r[1]["ft_home"] = 8
        after = run(altered_h, f, altered_r)
        self.assertEqual(before["selections"][:2], after["selections"][:2])
        self.assertEqual(before["folds"][1]["matches"][0]["candidate"],
                         after["folds"][1]["matches"][0]["candidate"])

    def test_late_label_is_excluded_from_fit_and_selection(self):
        h, f, r = case()
        h[-3]["verified_at"] = r[0]["verified_at"] = "2026-10-09T09:00:00+08:00"
        report = run(h, f, r)
        self.assertEqual([x["training_n"] for x in report["folds"]], [35, 35, 37])
        self.assertEqual(report["selections"][1]["selection_n"], 0)
        self.assertIsNone(report["selections"][1]["selected_kappa"])

    def test_blocked_sources_and_nonfootball_stay_in_ledger(self):
        h, f, r = case()
        f[0]["fixtures"][0]["fixture_source"]["available_at"] = None
        other = {**f[0]["fixtures"][0], "match_id": "tennis", "sport": "tennis"}
        f[0]["fixtures"].append(other)
        f[0]["expected_total"] = 2
        report = run(h, f, r)
        self.assertEqual((report["offered_n"], report["football_offered_n"], report["predicted_n"]), (4, 3, 2))
        self.assertEqual([x["status"] for x in report["folds"][0]["matches"]], ["blocked", "blocked"])
        self.assertEqual(report["selections"][1]["selection_n"], 0)

    def test_pending_results_do_not_get_fake_brier(self):
        h, f, r = case()
        report = run(h, f[:1], [])
        self.assertEqual(report["paired_n"], 0)
        self.assertIsNone(report["delta_brier"])
        self.assertIsNone(report["observed_nondegradation"])

    def test_integer_handicap_requires_its_own_source_and_propagates(self):
        h, f, r = case()
        f[0]["fixtures"][0]["official_handicap"] = -1
        missing = run(h, f, r)
        self.assertEqual(missing["folds"][0]["predicted_n"], 0)
        f[0]["fixtures"][0]["handicap_source"] = f[0]["fixtures"][0]["fixture_source"]
        good = run(h, f, r)
        vector = good["folds"][0]["matches"][0]["candidate"]["vectors"]["handicap_wdl"]
        self.assertAlmostEqual(sum(vector.values()), 1.)
        f[0]["fixtures"][0]["official_handicap"] = -.5
        self.assertEqual(run(h, f, r)["folds"][0]["predicted_n"], 0)

    def test_block_bootstrap_sample_and_coverage_gate(self):
        pairs = [{"period": f"period{i // 100}", "baseline": .2, "candidate": .19,
                  "score31_logloss_baseline": 1.4, "score31_logloss_candidate": 1.3,
                  "floored_probabilities": 0}
                 for i in range(500)]
        good = summarize_pairs(pairs, football_offered_n=500, predicted_n=500)
        self.assertTrue(good["numerical_nondegradation_gate"])
        self.assertFalse(good["production_gate_passed"])
        bad = summarize_pairs([{**p, "candidate": .21} for p in pairs],
                              football_offered_n=500, predicted_n=500)
        self.assertFalse(bad["numerical_nondegradation_gate"])
        incomplete = summarize_pairs(pairs, football_offered_n=501, predicted_n=500)
        self.assertFalse(incomplete["numerical_nondegradation_gate"])
        score_worse = summarize_pairs([{**p, "score31_logloss_candidate": 1.5} for p in pairs],
                                      football_offered_n=500, predicted_n=500)
        self.assertFalse(score_worse["numerical_nondegradation_gate"])
        small = summarize_pairs(pairs[:100], football_offered_n=100, predicted_n=100)
        self.assertIsNone(small["block_bootstrap_upper_95"])
        self.assertFalse(small["numerical_nondegradation_gate"])

    def test_duplicate_incomplete_or_inconsistent_input_rejected(self):
        for kind in ("duplicate", "incomplete", "wrong_result"):
            h, f, r = case()
            if kind == "duplicate":
                f[1]["fixtures"][0]["match_id"] = f[0]["fixtures"][0]["match_id"]
            elif kind == "incomplete":
                f[0]["expected_total"] = 2
            else:
                r[0]["ft_home"] = 9
            with self.assertRaises(ValueError):
                run(h, f, r)


if __name__ == "__main__":
    unittest.main()
