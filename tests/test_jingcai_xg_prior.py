"""Tests for Understat xG-informed lambda priors."""
import pytest

from engine.jingcai_strengths import estimate_lambdas
from engine.jingcai_xg_prior import get_xg_prior


class TestXgPriorLookup:
    def test_known_team(self):
        prior = get_xg_prior("巴萨")
        assert prior is not None
        attack, defense = prior
        assert attack > 1.5  # Barcelona strong attack
        assert 0.5 <= attack <= 2.0  # clamped
        assert 0.5 <= defense <= 2.0

    def test_weak_team(self):
        prior = get_xg_prior("赫塔费")
        assert prior is not None
        attack, defense = prior
        assert attack < 1.0  # Getafe weak attack

    def test_unknown_team(self):
        assert get_xg_prior("不存在的球队") is None
        assert get_xg_prior("") is None
        assert get_xg_prior(None) is None

    def test_non_xg_league_team(self):
        # K联赛不在xG覆盖范围
        assert get_xg_prior("首尔FC") is None


class TestXgPriorLambda:
    def test_no_data_uses_xg_prior(self):
        """With zero form data, lambda should reflect xG prior, not flat 1.0."""
        xg_h = get_xg_prior("巴萨")
        xg_a = get_xg_prior("赫塔费")
        lh, la, notes = estimate_lambdas(
            [], [], 2.7, home_xg_prior=xg_h, away_xg_prior=xg_a)
        # Barcelona should have clearly higher lambda than baseline
        assert lh > 1.8, f"expected Barcelona lambda > 1.8, got {lh}"
        assert la < 1.0, f"expected Getafe lambda < 1.0, got {la}"
        assert notes["xg_prior_home"] is True
        assert notes["xg_prior_away"] is True

    def test_no_xg_falls_back_to_naive(self):
        """Without xG prior, behavior unchanged (naive 1.0 shrinkage)."""
        lh, la, notes = estimate_lambdas([], [], 2.7)
        # Should be close to league baseline (flat)
        assert abs(lh - 1.426) < 0.01
        assert abs(la - 1.274) < 0.01
        assert notes["xg_prior_home"] is False

    def test_partial_xg(self):
        """Only home team has xG data."""
        xg_h = get_xg_prior("巴萨")
        lh, la, notes = estimate_lambdas(
            [], [], 2.7, home_xg_prior=xg_h, away_xg_prior=None)
        assert lh > 1.8
        assert notes["xg_prior_home"] is True
        assert notes["xg_prior_away"] is False

    def test_strong_data_overrides_xg(self):
        """With abundant form data, xG prior has minimal influence."""
        # Simulate 10 matches of strong form
        strong_form = [{"venue": "H", "gf": 3, "ga": 0} for _ in range(10)]
        weak_form = [{"venue": "A", "gf": 0, "ga": 3} for _ in range(10)]
        xg_h = get_xg_prior("赫塔费")  # weak xG prior
        xg_a = get_xg_prior("巴萨")  # strong xG prior
        lh, la, notes = estimate_lambdas(
            strong_form, weak_form, 2.7,
            home_xg_prior=xg_h, away_xg_prior=xg_a)
        # Form data should dominate: home strong, away weak
        assert lh > la, f"form should dominate: lh={lh}, la={la}"

    def test_lambda_bounds_respected(self):
        """xG prior cannot push lambda outside [0.15, 4.5] / [0.10, 4.0]."""
        # Extreme priors are clamped in get_xg_prior to [0.5, 2.0]
        # so lambda should stay in bounds
        xg_h = (2.0, 0.5)  # max attack, best defense
        xg_a = (0.5, 2.0)  # min attack, worst defense
        lh, la, _ = estimate_lambdas(
            [], [], 2.7, home_xg_prior=xg_h, away_xg_prior=xg_a)
        assert 0.15 <= lh <= 4.5
        assert 0.10 <= la <= 4.0
