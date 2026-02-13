"""Tests for EMA calculation."""

from tankvision.calculation.ema import (
    DEFAULT_ALPHA,
    battles_to_target,
    compute_ema_update,
    project_ema,
)


class TestComputeEmaUpdate:
    def test_zero_damage_decays_ema(self):
        ema = compute_ema_update(1000.0, 0.0)
        assert ema < 1000.0
        assert ema == 1000.0 * (1.0 - DEFAULT_ALPHA)

    def test_high_damage_increases_ema(self):
        ema = compute_ema_update(1000.0, 5000.0)
        assert ema > 1000.0

    def test_damage_equal_to_ema_unchanged(self):
        """When battle damage equals current EMA, EMA stays the same."""
        ema = compute_ema_update(2000.0, 2000.0)
        assert abs(ema - 2000.0) < 0.001

    def test_custom_alpha(self):
        alpha = 0.1
        ema = compute_ema_update(1000.0, 2000.0, alpha=alpha)
        expected = 1000.0 * 0.9 + 2000.0 * 0.1
        assert abs(ema - expected) < 0.001

    def test_convergence_over_many_battles(self):
        """EMA should converge toward the constant damage value."""
        ema = 0.0
        target = 3000.0
        for _ in range(500):
            ema = compute_ema_update(ema, target)
        assert abs(ema - target) < 1.0


class TestProjectEma:
    def test_projection_matches_update(self):
        """project_ema should give the same result as compute_ema_update."""
        current = 1500.0
        damage = 3000.0
        assert project_ema(current, damage) == compute_ema_update(current, damage)


class TestBattlesToTarget:
    def test_already_above_target(self):
        result = battles_to_target(3000.0, 2000.0, 3000.0)
        # Already above target, but first battle still computes
        # EMA of 3000 doing 3000 stays 3000, which is >= 2000 → 1 battle
        assert result == 1

    def test_reachable_target(self):
        result = battles_to_target(0.0, 2500.0, 3000.0)
        assert result is not None
        assert result > 0
        assert result < 500

    def test_unreachable_with_low_damage(self):
        # Trying to reach 5000 EMA while averaging 1000 damage is unreachable
        # (EMA converges to avg damage)
        result = battles_to_target(0.0, 5000.0, 1000.0, max_battles=500)
        assert result is None

    def test_zero_avg_damage(self):
        result = battles_to_target(1000.0, 2000.0, 0.0)
        assert result is None

    def test_negative_avg_damage(self):
        result = battles_to_target(1000.0, 2000.0, -100.0)
        assert result is None
