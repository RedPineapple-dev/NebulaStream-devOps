"""Exponential Weighted Moving Average (EWMA) smoother with hysteresis guard.

Usage::

    smoother = EWMASmoother(alpha=0.2, threshold_ms=200.0, recovery_delta_ms=30.0)

    for p95 in poll_loop():
        smoother.update(p95)
        if smoother.should_trigger:
            fire_mitigation()
        elif smoother.should_recover:
            allow_recovery()
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class EWMASmoother:
    """Per-region EWMA smoother with hysteresis-based trigger/recovery.

    Attributes:
        alpha:             Smoothing factor in (0, 1].  Higher = more reactive.
        threshold_ms:      Breach fires when ewma > threshold_ms.
        recovery_delta_ms: Recovery fires when ewma < threshold_ms - recovery_delta_ms.
        value:             Current EWMA value (updated by :meth:`update`).
        _triggered:        Internal state — True while in a breach episode.
    """

    alpha: float = 0.2
    threshold_ms: float = 200.0
    recovery_delta_ms: float = 30.0
    value: float = 0.0
    _triggered: bool = field(default=False, repr=False)

    def update(self, p95_ms: float) -> None:
        """Incorporate a new p95 sample into the EWMA."""
        if self.value == 0.0:
            # Cold start: seed with first real measurement.
            self.value = p95_ms
        else:
            self.value = self.alpha * p95_ms + (1.0 - self.alpha) * self.value

        # Update hysteresis state.
        if self.value >= self.threshold_ms:
            self._triggered = True
        elif self.value < (self.threshold_ms - self.recovery_delta_ms):
            self._triggered = False

    @property
    def should_trigger(self) -> bool:
        """True the first poll the EWMA crosses the breach threshold."""
        return self._triggered and self.value >= self.threshold_ms

    @property
    def should_recover(self) -> bool:
        """True once the EWMA falls below the recovery line."""
        return not self._triggered

    @property
    def recovery_threshold(self) -> float:
        return self.threshold_ms - self.recovery_delta_ms

    def to_dict(self) -> dict:
        return {
            "ewma_ms": round(self.value, 2),
            "threshold_ms": self.threshold_ms,
            "recovery_threshold_ms": self.recovery_threshold,
            "triggered": self._triggered,
        }


# ── Convenience function (stateless, for unit tests) ──────────────────────────


def compute_ewma(prev: float, new: float, alpha: float) -> float:
    """Single-step EWMA: ``alpha * new + (1 - alpha) * prev``."""
    return alpha * new + (1.0 - alpha) * prev
