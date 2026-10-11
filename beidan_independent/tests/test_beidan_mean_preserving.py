import math
import unittest

from beidan_bd1.baseline import predict_l3
from beidan_bd1.mean_preserving import project_fixed_mean, shadow_fixed_mean, ProjectionUnavailable
from test_beidan_baseline import sample_history


class FixedMeanTests(unittest.TestCase):
    def prior(self, line=0):
        return predict_l3(sample_history(), asof_at="2026-10-09T14:00:00+08:00",
                          kickoff_at="2026-10-10T15:00:00+08:00",
                          competition_family=None, handicap=line)

    def test_identity_is_exact(self):
        p = self.prior()
        fit = project_fixed_mean(p["vectors"]["score"], target=p["vectors"]["wdl"], line=0)
        self.assertEqual(fit["score"], p["vectors"]["score"])

    def test_constructed_kl_solution_and_zeros(self):
        # Construct a known exponential tilt with mean exactly equal to base.
        # Region conditionals: W/L total=1; D total=0 or 2, initially equal.
        base = {"0-0": .25, "1-1": .25, "1-0": .25, "0-1": .25, "2-0": 0.}
        fit = project_fixed_mean(base, target={"胜": .4, "平": .2, "负": .4}, line=0)
        self.assertAlmostEqual(fit["score"]["0-0"], .1)
        self.assertAlmostEqual(fit["score"]["1-1"], .1)
        self.assertEqual(fit["score"]["2-0"], 0.)
        # Unequal D conditional requires nonzero beta; solve has analytic answer.
        base = {"0-0": .3, "1-1": .2, "1-0": .25, "0-1": .25}
        fit = project_fixed_mean(base, target={"胜": .35, "平": .3, "负": .35}, line=0)
        self.assertAlmostEqual(fit["score"]["1-1"], .1, places=9)
        self.assertAlmostEqual(fit["beta"], math.log(.75) / 2, places=8)
        self.assertLess(abs(fit["mean_after"] - .9), 1e-10)

    def test_positive_and_negative_lines_and_six_markets(self):
        for line in (-2, -1, 0, 1, 2):
            p = self.prior(line)
            target = {k: .9 * v + .1 / 3 for k, v in p["vectors"]["handicap_wdl"].items()}
            result = shadow_fixed_mean(p, target=target, line=line)
            self.assertTrue(result["target_applied"])
            self.assertLess(abs(result["mean_before"] - result["mean_after"]), 1e-10)
            for vector in result["vectors"].values():
                self.assertAlmostEqual(sum(vector.values()), 1., places=9)
            self.assertAlmostEqual(sum(result["score_31"].values()), 1., places=9)
            for k, v in target.items():
                self.assertAlmostEqual(result["vectors"]["handicap_wdl"][k], v, places=9)
            for k in ("胜", "平", "负"):
                self.assertAlmostEqual(sum(v for label, v in result["vectors"]["half_full"].items()
                                           if label[1] == k), result["vectors"]["wdl"][k], places=9)

    def test_infeasibility_retains_prior_and_never_relaxes_mean(self):
        p = self.prior(-3)
        target = {"胜": .98, "平": .01, "负": .01}
        with self.assertRaisesRegex(ProjectionUnavailable, "可行区间"):
            project_fixed_mean(p["vectors"]["score"], target=target, line=-3)
        result = shadow_fixed_mean(p, target=target, line=-3)
        self.assertEqual(result["status"], "fallback_prior")
        self.assertEqual(result["vectors"], p["vectors"])
        self.assertFalse(result["target_applied"])
        self.assertFalse(result["production_eligible"])

    def test_invalid_inputs_and_empty_support(self):
        base = {"0-0": .3, "1-1": .2, "1-0": .25, "0-1": .25}
        q = {"胜": .35, "平": .3, "负": .35}
        for line in (True, .5):
            with self.assertRaises(ValueError):
                project_fixed_mean(base, target=q, line=line)
        for bad in ({**q, "胜": float("nan")}, {**q, "平": 0}, {**q, "负": .4}):
            with self.assertRaises(ValueError):
                project_fixed_mean(base, target=bad, line=0)
        with self.assertRaises(ProjectionUnavailable):
            project_fixed_mean({"0-0": 1.}, target=q, line=0)
        with self.assertRaisesRegex(ValueError, "不一致"):
            shadow_fixed_mean(self.prior(), target=q, line=-1)


if __name__ == "__main__":
    unittest.main()
