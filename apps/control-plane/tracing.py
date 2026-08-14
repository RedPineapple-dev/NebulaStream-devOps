"""OpenTelemetry setup for NebulaStream control plane.

Configures a TracerProvider that exports spans to a Jaeger-compatible
OTLP endpoint (gRPC or HTTP), then auto-instruments FastAPI.

Environment variables:
  OTEL_EXPORTER_OTLP_ENDPOINT  — e.g. ``http://jaeger:4317`` (gRPC default)
  OTEL_SERVICE_NAME            — defaults to ``nebula-control-plane``
  OTEL_ENABLED                 — set to ``false`` to disable entirely
"""

from __future__ import annotations

import os

from logging_config import get_logger

log = get_logger("control_plane.tracing")


_OTEL_AVAILABLE = False
try:
    from opentelemetry import trace
    from opentelemetry.sdk.resources import SERVICE_NAME, Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    _OTEL_AVAILABLE = True
except ImportError:
    pass


def setup_tracing(app=None) -> None:
    """Initialise OpenTelemetry tracing.  Safe to call even if OTel is not installed."""
    enabled = os.getenv("OTEL_ENABLED", "true").lower() != "false"
    if not enabled or not _OTEL_AVAILABLE:
        log.info("otel_tracing_disabled")
        return

    service_name = os.getenv("OTEL_SERVICE_NAME", "nebula-control-plane")
    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://jaeger:4317")

    resource = Resource(attributes={SERVICE_NAME: service_name})
    provider = TracerProvider(resource=resource)

    try:
        # Prefer gRPC exporter; fall back to HTTP if unavailable.
        try:
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
                OTLPSpanExporter as GrpcExporter,
            )

            exporter = GrpcExporter(endpoint=endpoint, insecure=True)
            log.info("otel_exporter", protocol="grpc", endpoint=endpoint)
        except ImportError:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                OTLPSpanExporter as HttpExporter,
            )

            http_endpoint = endpoint.replace(":4317", ":4318")
            exporter = HttpExporter(endpoint=f"{http_endpoint}/v1/traces")
            log.info("otel_exporter", protocol="http", endpoint=http_endpoint)

        provider.add_span_processor(BatchSpanProcessor(exporter))
    except Exception as exc:
        log.warning("otel_exporter_setup_failed", error=str(exc)[:120])

    trace.set_tracer_provider(provider)

    # Auto-instrument FastAPI if app is provided.
    if app is not None:
        try:
            from opentelemetry.instrumentation.fastapi import (
                FastAPIInstrumentor,
            )

            FastAPIInstrumentor.instrument_app(app)
            log.info("otel_fastapi_instrumented", service=service_name)
        except ImportError:
            log.warning("otel_fastapi_instrumentor_not_installed")

    log.info("otel_tracing_configured", service=service_name)


def get_tracer(name: str = "nebula"):
    """Return a tracer — a no-op tracer if OTel is disabled/unavailable."""
    if _OTEL_AVAILABLE:
        from opentelemetry import trace

        return trace.get_tracer(name)

    # Return a minimal no-op tracer compatible with `with tracer.start_as_current_span(...)`.
    return _NoOpTracer()


class _NoOpSpan:
    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def set_attribute(self, *_):
        pass

    def record_exception(self, *_):
        pass


class _NoOpTracer:
    def start_as_current_span(self, name: str, **_):
        return _NoOpSpan()
