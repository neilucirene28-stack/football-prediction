"""L1 identity and time gates plus shared six-market output."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from beidan_bd1.runner import run_shadow_pool
from beidan_bd1.team_strength import predict_l1


ASOF = "2026-10-09T15:20:00+08:00"
KICKOFF = "2026-10-10T15:30:00+08:00"


def samples():
    rows = []
    for i in range(40):
        h, a = (("strong", "weak") if i % 2 else ("neutral", "strong"))
        fh, fa = ((3, 0) if h == "strong" else (0, 2))
        rows.append({
            "match_id": f"id-{i}", "kickoff_at": "2026-09-01T10:00:00+08:00",
            "result_available_at": "2026-09-01T13:00:00+08:00",
            "identity_verified_at": "2026-10-09T14:00:00+08:00",
            "identity_source": "audited_team_map", "identity_verified": True,
            "home_id": h, "away_id": a, "competition_id": "league-1",
            "regular_time": True, "ft_home": fh, "ft_away": fa,
            "ht_home": min(fh, 1), "ht_away": min(fa, 1),
        })
    return rows


def predict(rows):
    return predict_l1(rows, asof_at=ASOF, kickoff_at=KICKOFF,
                      home_id="strong", away_id="weak",
                      competition_id="league-1", handicap=-1)


class TeamStrengthTests(unittest.TestCase):
    def test_opponent_adjustment_and_future_result_isolation(self):
        rows = samples()
        out = predict(rows)
        self.assertGreater(out["lambda_home"], out["lambda_away"])
        self.assertGreater(out["vectors"]["wdl"]["胜"], .5)
        self.assertEqual(len(out["score_31"]), 31)
        future = copy.deepcopy(rows[0])
        future.update(match_id="future", ft_home=0, ft_away=25,
                      ht_home=0, ht_away=12,
                      result_available_at="2026-10-09T16:00:00+08:00")
        unverified = copy.deepcopy(rows[1])
        unverified.update(match_id="unverified", ft_home=25, ft_away=0,
                          ht_home=10, ht_away=0, identity_verified=False)
        self.assertEqual(out["vectors"], predict(rows + [future, unverified])["vectors"])

    def test_unverified_history_or_missing_team_falls_back_to_l3(self):
        rows = samples()
        with tempfile.TemporaryDirectory() as tmp:
            fixture = {
                "period": "future", "match_id": "future-1", "kickoff_at": KICKOFF,
                "home_id": "strong", "away_id": "weak", "competition_id": "league-1",
                "identity_verified": True, "identity_source": "roster",
                "identity_verified_at": "2026-10-09T14:00:00+08:00",
                "sources": [{"name": "roster", "available_at": "2026-10-09T14:00:00+08:00", "status": "ok"}],
                "input_sources": {"fixture": "roster", "home_id": "roster",
                                  "away_id": "roster", "competition_id": "roster"},
            }
            report = run_shadow_pool(fixtures=[fixture], history=rows, root=tmp,
                                     expected_total=1, period="future",
                                     synthetic_at=ASOF, run_id="l1")
            entry = report["matches"][0]
            self.assertEqual(entry["route"], "L1_team_strength")
            record = json.loads((Path(tmp) / entry["snapshot_path"]).read_text())
            self.assertEqual(record["identity_ids"]["home_id"], "strong")
            self.assertGreater(record["lambda_home"], record["lambda_away"])
            self.assertTrue(record["observation_only"])  # synthetic clock
            bad = copy.deepcopy(rows)
            for r in bad:
                r["identity_verified"] = False
            report = run_shadow_pool(fixtures=[fixture], history=bad, root=Path(tmp) / "fallback",
                                     expected_total=1, period="future",
                                     synthetic_at=ASOF, run_id="fallback")
            entry = report["matches"][0]
            self.assertEqual(entry["route"], "L3_prior_only")
            self.assertIn("L1同赛事", entry["fallback_reason"])


if __name__ == "__main__":
    unittest.main()
