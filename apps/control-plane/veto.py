"""
Memory-Vetoed LLM Control (MVLC).

After the LLM proposes new traffic weights, treat the top-k retrieved past
incidents as INDEPENDENT counterfactual evaluators of that proposal. Each past
incident "votes" on whether the proposed action looks like a historical success
or a historical failure. If aggregate confidence is below a threshold, the LLM
proposal is vetoed and a conservative rule-based fallback is used instead.

Why this is different from vanilla RAG:
  - Vanilla RAG: retrieved cases go into the LLM prompt; the LLM is judge.
  - MVLC: retrieved cases are NOT in the prompt for the veto step. They are an
    independent committee that scores the LLM's already-produced action. The
    LLM cannot talk its way past the veto.

The veto fires when the proposal's action vector (delta of weights) is aligned
with past actions that FAILED (high weight when 1 - outcome_score is large), or
anti-aligned with past actions that SUCCEEDED (low cosine to high-outcome past
deltas). This is a form of case-based safety supervision layered on top of
generative control.
"""
from __future__ import annotations
import math
from dataclasses import dataclass, asdict


@dataclass
class VetoVerdict:
    vetoed: bool
    confidence: float          # 0.0..1.0, higher = more aligned with successful history
    threshold: float
    cases_considered: int
    aligned_success: int       # past successes the proposal agrees with
    aligned_failure: int       # past failures the proposal mirrors (bad)
    reason: str

    def to_dict(self) -> dict:
        return asdict(self)


def _delta(after: dict, before: dict) -> dict:
    return {r: float(after.get(r, 0) - before.get(r, 0)) for r in before}


def _cosine(a: dict, b: dict) -> float:
    keys = set(a) | set(b)
    dot = sum(a.get(k, 0.0) * b.get(k, 0.0) for k in keys)
    na  = math.sqrt(sum(v * v for v in a.values()))
    nb  = math.sqrt(sum(v * v for v in b.values()))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def score_proposal(
    proposed_weights: dict,
    current_weights: dict,
    past_cases: list[dict],
) -> tuple[float, int, int, int]:
    """
    Returns (confidence in [0,1], cases_considered, aligned_success, aligned_failure).

    Each past_case must expose:
      weights_before:  dict[region -> int]
      weights_after:   dict[region -> int]
      outcome_score:   float in [0,1]   (1.0 = recovered fast, 0.0 = failed)
      similarity:      float in [0,1]   (Milvus COSINE similarity to current state)
    """
    proposed_delta = _delta(proposed_weights, current_weights)
    p_norm = math.sqrt(sum(v * v for v in proposed_delta.values()))
    if p_norm == 0.0:
        return 1.0, 0, 0, 0

    contributions: list[float] = []
    aligned_success = 0
    aligned_failure = 0

    for case in past_cases:
        before = case.get("weights_before") or {}
        after  = case.get("weights_after")  or {}
        if not before or not after:
            continue
        past_delta = _delta(after, before)
        if all(v == 0 for v in past_delta.values()):
            continue

        alignment = _cosine(proposed_delta, past_delta)
        outcome   = float(case.get("outcome_score", 0.0))
        sim       = max(0.0, float(case.get("similarity", 0.5)))

        if outcome >= 0.5:
            contrib = outcome * alignment
            if alignment > 0.3:
                aligned_success += 1
        else:
            contrib = (1.0 - outcome) * (-alignment)
            if alignment > 0.3:
                aligned_failure += 1

        contributions.append(sim * contrib)

    if not contributions:
        return 1.0, 0, aligned_success, aligned_failure

    mean = sum(contributions) / len(contributions)
    confidence = max(0.0, min(1.0, 0.5 + 0.5 * mean))
    return confidence, len(contributions), aligned_success, aligned_failure


def evaluate(
    proposed_weights: dict,
    current_weights: dict,
    past_cases: list[dict],
    threshold: float = 0.4,
    min_cases: int = 2,
) -> VetoVerdict:
    confidence, n, ok, bad = score_proposal(
        proposed_weights, current_weights, past_cases
    )

    if n < min_cases:
        return VetoVerdict(
            vetoed=False,
            confidence=confidence,
            threshold=threshold,
            cases_considered=n,
            aligned_success=ok,
            aligned_failure=bad,
            reason=f"only {n} usable past cases (need {min_cases}) — accepting proposal",
        )

    vetoed = confidence < threshold
    if vetoed:
        reason = (
            f"confidence {confidence:.2f} < {threshold:.2f} across {n} historical cases "
            f"({bad} matched past failures, {ok} matched past successes)"
        )
    else:
        reason = (
            f"confidence {confidence:.2f} ≥ {threshold:.2f} across {n} historical cases "
            f"({ok} aligned with successes, {bad} matched failures)"
        )
    return VetoVerdict(
        vetoed=vetoed,
        confidence=confidence,
        threshold=threshold,
        cases_considered=n,
        aligned_success=ok,
        aligned_failure=bad,
        reason=reason,
    )
