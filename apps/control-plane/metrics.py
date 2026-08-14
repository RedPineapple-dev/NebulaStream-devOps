"""Prometheus metrics for the NebulaStream control plane."""

from __future__ import annotations

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

registry = CollectorRegistry()

# ── Region telemetry ─────────────────────────────────────────────────────────
region_latency_ms = Gauge(
    "nebula_region_latency_ms",
    "Current RTT to a region in milliseconds",
    ["region"],
    registry=registry,
)
region_p95_ms = Gauge(
    "nebula_region_p95_ms",
    "Rolling p95 RTT for a region (10-sample window)",
    ["region"],
    registry=registry,
)
region_weight_pct = Gauge(
    "nebula_region_weight_pct",
    "Current traffic weight for a region (sums to 100 across regions)",
    ["region"],
    registry=registry,
)
region_status = Gauge(
    "nebula_region_status",
    "Region status: 0=warming, 1=healthy, 2=degraded, 3=breach",
    ["region"],
    registry=registry,
)

# ── Operational counters ─────────────────────────────────────────────────────
breaches_total = Counter(
    "nebula_breaches_total",
    "Total breach events detected",
    ["region"],
    registry=registry,
)
shifts_total = Counter(
    "nebula_shifts_total",
    "Total traffic shifts applied",
    ["outcome"],  # llm | fallback
    registry=registry,
)
recovery_seconds = Histogram(
    "nebula_recovery_seconds",
    "Recovery time after a traffic shift",
    ["region", "outcome"],  # outcome: recovered | timeout
    buckets=(1, 5, 10, 15, 30, 60, 90, 120),
    registry=registry,
)
chaos_injections_total = Counter(
    "nebula_chaos_injections_total",
    "Total chaos failures injected",
    ["region", "type"],
    registry=registry,
)

# ── LLM telemetry ────────────────────────────────────────────────────────────
llm_calls_total = Counter(
    "nebula_llm_calls_total",
    "LLM calls made by the control plane",
    ["outcome"],  # success | error
    registry=registry,
)
llm_latency_seconds = Histogram(
    "nebula_llm_latency_seconds",
    "LLM call duration in seconds",
    buckets=(0.5, 1, 2, 5, 10, 20, 30),
    registry=registry,
)

# ── Memory subsystem ─────────────────────────────────────────────────────────
incident_recalls_total = Counter(
    "nebula_incident_recalls_total",
    "Incident memory lookups",
    ["outcome"],  # hit | miss | error
    registry=registry,
)
incidents_stored_total = Counter(
    "nebula_incidents_stored_total",
    "Incidents written to vector memory",
    registry=registry,
)

# ── Memory-Vetoed LLM Control (MVLC) ─────────────────────────────────────────
vetoes_total = Counter(
    "nebula_vetoes_total",
    "MVLC verdicts on LLM proposals",
    ["outcome"],  # vetoed | accepted | skipped
    registry=registry,
)
veto_confidence = Histogram(
    "nebula_veto_confidence",
    "Counterfactual confidence score for LLM proposals (0..1)",
    buckets=(0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0),
    registry=registry,
)


# ── EWMA & Circuit Breaker telemetry ─────────────────────────────────────────
ewma_p95_ms = Gauge(
    "nebula_ewma_p95_ms",
    "Exponential Weighted Moving Average of p95 RTT for a region",
    ["region"],
    registry=registry,
)
circuit_breaker_state = Gauge(
    "nebula_circuit_breaker_state",
    "LLM circuit breaker state: 0=closed, 1=open, 2=half_open",
    registry=registry,
)
leader_active = Gauge(
    "nebula_leader_active",
    "1 if this instance is the current control-plane leader, 0 otherwise",
    registry=registry,
)

_STATUS_MAP = {"warming": 0, "healthy": 1, "degraded": 2, "breach": 3}


def record_status(region: str, status: str) -> None:
    region_status.labels(region=region).set(_STATUS_MAP.get(status, 0))


def render_metrics() -> tuple[bytes, str]:
    return generate_latest(registry), CONTENT_TYPE_LATEST

