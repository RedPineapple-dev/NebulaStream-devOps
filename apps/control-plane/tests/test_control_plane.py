import math

from fastapi.testclient import TestClient

import metrics as m
import veto as veto_mod
from llm import (
    build_prompt,
    rule_based_decision,
)
from llm import (
    validate_weights as llm_validate_weights,
)
from memory import calculate_outcome_score
from server import (
    State,
    app,
    rule_based_proposal,
)
from server import (
    validate_weights as server_validate_weights,
)

# ── Veto & Math Tests ────────────────────────────────────────────────────────


def test_veto_delta_and_cosine():
    before = {"us-east": 33, "eu-west": 33, "ap-south": 34}
    after = {"us-east": 10, "eu-west": 45, "ap-south": 45}
    delta = veto_mod._delta(after, before)
    assert delta["us-east"] == -23.0
    assert delta["eu-west"] == 12.0
    assert delta["ap-south"] == 11.0

    # Self cosine should be ~1.0
    cos_self = veto_mod._cosine(delta, delta)
    assert math.isclose(cos_self, 1.0, rel_tol=1e-4)

    # Orthogonal or zero vector cosine should be 0.0
    zero_delta = {"us-east": 0.0, "eu-west": 0.0, "ap-south": 0.0}
    assert veto_mod._cosine(delta, zero_delta) == 0.0


def test_veto_evaluation_insufficient_cases():
    proposed = {"us-east": 10, "eu-west": 45, "ap-south": 45}
    current = {"us-east": 33, "eu-west": 33, "ap-south": 34}
    cases = []  # No past cases

    verdict = veto_mod.evaluate(
        proposed_weights=proposed,
        current_weights=current,
        past_cases=cases,
        threshold=0.4,
        min_cases=2,
    )
    assert not verdict.vetoed
    assert verdict.cases_considered == 0
    assert "need 2" in verdict.reason


def test_veto_evaluation_success_alignment():
    proposed = {"us-east": 10, "eu-west": 45, "ap-south": 45}
    current = {"us-east": 33, "eu-west": 33, "ap-south": 34}
    # Past case that succeeded with identical shift
    past_cases = [
        {
            "weights_before": {"us-east": 33, "eu-west": 33, "ap-south": 34},
            "weights_after": {"us-east": 10, "eu-west": 45, "ap-south": 45},
            "outcome_score": 1.0,
            "similarity": 0.95,
        },
        {
            "weights_before": {"us-east": 33, "eu-west": 33, "ap-south": 34},
            "weights_after": {"us-east": 15, "eu-west": 40, "ap-south": 45},
            "outcome_score": 0.9,
            "similarity": 0.85,
        },
    ]

    verdict = veto_mod.evaluate(
        proposed_weights=proposed,
        current_weights=current,
        past_cases=past_cases,
        threshold=0.4,
        min_cases=2,
    )
    assert not verdict.vetoed
    assert verdict.confidence > 0.5
    assert verdict.aligned_success >= 1


def test_veto_evaluation_failure_veto():
    proposed = {"us-east": 10, "eu-west": 45, "ap-south": 45}
    current = {"us-east": 33, "eu-west": 33, "ap-south": 34}
    # Past cases that failed with identical shift
    past_cases = [
        {
            "weights_before": {"us-east": 33, "eu-west": 33, "ap-south": 34},
            "weights_after": {"us-east": 10, "eu-west": 45, "ap-south": 45},
            "outcome_score": 0.0,  # Failed completely
            "similarity": 0.95,
        },
        {
            "weights_before": {"us-east": 33, "eu-west": 33, "ap-south": 34},
            "weights_after": {"us-east": 10, "eu-west": 45, "ap-south": 45},
            "outcome_score": 0.0,  # Failed completely
            "similarity": 0.90,
        },
    ]

    verdict = veto_mod.evaluate(
        proposed_weights=proposed,
        current_weights=current,
        past_cases=past_cases,
        threshold=0.4,
        min_cases=2,
    )
    assert verdict.vetoed
    assert verdict.confidence < 0.4
    assert verdict.aligned_failure >= 1


# ── LLM & Rule-Based Proposal Tests ──────────────────────────────────────────


def test_validate_weights_sum_and_min():
    current = {"us-east": 33, "eu-west": 33, "ap-south": 34}

    # Under-sum proposal (sums to 70)
    proposed = {"us-east": 20, "eu-west": 25, "ap-south": 25}
    res = llm_validate_weights(proposed, current)
    assert sum(res.values()) == 100
    assert all(w >= 5 for w in res.values())

    # Below minimum weight proposal
    proposed_low = {"us-east": 1, "eu-west": 49, "ap-south": 50}
    res_low = server_validate_weights(proposed_low, current)
    assert sum(res_low.values()) == 100
    assert res_low["us-east"] >= 5


def test_rule_based_fallback():
    current = {"us-east": 33, "eu-west": 33, "ap-south": 34}
    latencies = {"us-east": 250.0, "eu-west": 45.0, "ap-south": 30.0}

    # Breach in us-east (>=200ms) should drop to MIN_WEIGHT (5%)
    weights, reason = rule_based_proposal(latencies, current)
    assert weights["us-east"] == 5
    assert sum(weights.values()) == 100
    assert "Rule-based" in reason

    decision = rule_based_decision(latencies, current)
    assert decision.used_fallback
    assert decision.weights["us-east"] == 5


def test_build_prompt_structure():
    latencies = {"us-east": 120.0, "eu-west": 30.0, "ap-south": 40.0}
    weights = {"us-east": 33, "eu-west": 33, "ap-south": 34}
    prompt = build_prompt(latencies, weights, past_incident="Past test incident")
    assert "LATENCIES:" in prompt
    assert "CURRENT WEIGHTS:" in prompt
    assert "SIMILAR PAST INCIDENT" in prompt
    assert "120ms p95" in prompt


# ── Metrics & Logging Tests ──────────────────────────────────────────────────


def test_metrics_rendering():
    m.region_latency_ms.labels(region="us-east").set(42.5)
    m.record_status("us-east", "healthy")
    m.breaches_total.labels(region="us-east").inc()

    body, content_type = m.render_metrics()
    assert b"nebula_region_latency_ms" in body
    assert b"nebula_region_status" in body
    assert b"nebula_breaches_total" in body
    assert "text/plain" in content_type or "text/openmetrics" in content_type


# ── Memory & Outcome Score Tests ─────────────────────────────────────────────


def test_calculate_outcome_score():
    assert calculate_outcome_score(4.0, fix_worked=True) == 1.0
    assert calculate_outcome_score(12.0, fix_worked=True) == 0.75
    assert calculate_outcome_score(25.0, fix_worked=True) == 0.5
    assert calculate_outcome_score(50.0, fix_worked=True) == 0.25
    assert calculate_outcome_score(90.0, fix_worked=True) == 0.1
    assert calculate_outcome_score(5.0, fix_worked=False) == 0.0


# ── State & Server Endpoint Tests ────────────────────────────────────────────


def test_state_sliding_window_p95():
    state = State()
    # No readings
    assert state.p95("us-east") == 0.0
    assert state.status("us-east") == "warming"

    # Fill readings
    for val in [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]:
        state.readings["us-east"].append(val)
    p = state.p95("us-east")
    assert p >= 90.0
    assert state.status("us-east") in ("healthy", "degraded")

    # High readings -> breach
    state.readings["us-east"].clear()
    for val in [210, 220, 230]:
        state.readings["us-east"].append(val)
    assert state.status("us-east") == "breach"


def test_fastapi_endpoints():
    client = TestClient(app)

    # /healthz
    res = client.get("/healthz")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}

    # /readyz
    res = client.get("/readyz")
    assert res.status_code == 200
    assert "workers" in res.json()

    # /metrics
    res = client.get("/metrics")
    assert res.status_code == 200
    assert "nebula_region_latency_ms" in res.text

    # /chaos/inject
    res = client.post("/chaos/inject?region=eu-west&delay=350")
    assert res.status_code == 200
    assert res.json()["success"] is True
    assert res.json()["delay_ms"] == 350

    # /chaos/clear
    res = client.post("/chaos/clear?region=eu-west")
    assert res.status_code == 200
    assert res.json()["success"] is True

    # Root index.html
    res = client.get("/")
    assert res.status_code == 200
    assert "NebulaStream" in res.text


def test_websocket_connection():
    client = TestClient(app)
    with client.websocket_connect("/ws") as websocket:
        data = websocket.receive_json()
        assert data["type"] == "init"
        assert "weights" in data
        assert "latencies" in data
