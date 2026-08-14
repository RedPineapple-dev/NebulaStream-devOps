"""Unit tests for the EWMA smoother and hysteresis logic."""

import pytest

from ewma import EWMASmoother, compute_ewma

# ── compute_ewma (stateless helper) ──────────────────────────────────────────


def test_compute_ewma_single_step():
    """alpha * new + (1 - alpha) * prev"""
    assert compute_ewma(100, 200, 0.5) == 150.0


def test_compute_ewma_alpha_1():
    """alpha=1 should return new value directly."""
    assert compute_ewma(50, 300, 1.0) == 300.0


def test_compute_ewma_alpha_0():
    """alpha=0 should return previous value unchanged."""
    assert compute_ewma(100, 999, 0.0) == 100.0


# ── EWMASmoother ──────────────────────────────────────────────────────────────


@pytest.fixture()
def smoother():
    return EWMASmoother(alpha=0.5, threshold_ms=200.0, recovery_delta_ms=30.0)


def test_cold_start_seeds_with_first_value(smoother):
    """On first update the EWMA is seeded with the raw value."""
    smoother.update(150.0)
    assert smoother.value == 150.0


def test_ewma_converges_toward_new_value(smoother):
    smoother.update(100.0)
    smoother.update(200.0)
    # alpha=0.5 → 0.5*200 + 0.5*100 = 150
    assert smoother.value == 150.0


def test_no_trigger_below_threshold(smoother):
    smoother.update(180.0)  # below 200
    assert not smoother.should_trigger


def test_triggers_when_ewma_crosses_threshold(smoother):
    """After enough high-latency samples EWMA should exceed threshold."""
    for _ in range(10):
        smoother.update(300.0)
    assert smoother.should_trigger


def test_hysteresis_prevents_immediate_recovery(smoother):
    """Once triggered, the smoother stays triggered until EWMA falls below
    (threshold - recovery_delta) = 200 - 30 = 170."""
    # Trigger
    for _ in range(10):
        smoother.update(300.0)
    assert smoother.should_trigger

    # Single dip to 190 — above recovery line (170), should remain triggered
    smoother.update(190.0)
    assert smoother._triggered  # still in breach episode

    # Drop below recovery line
    for _ in range(10):
        smoother.update(100.0)
    assert smoother.should_recover
    assert not smoother._triggered


def test_to_dict_contains_expected_keys(smoother):
    smoother.update(100.0)
    d = smoother.to_dict()
    assert "ewma_ms" in d
    assert "threshold_ms" in d
    assert "triggered" in d
    assert d["threshold_ms"] == 200.0
