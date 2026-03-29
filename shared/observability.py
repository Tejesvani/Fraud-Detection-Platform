"""
Shared observability setup for all fraud-detection-platform services.

Initializes three pillars in a single call:
  1. Prometheus metrics HTTP server (prometheus_client)
  2. Structured JSON logging (structlog)
  3. Distributed tracing (OpenTelemetry → Tempo via OTLP HTTP)

Usage:
    from shared.observability import setup_observability, get_logger

    # Call once at service startup (before the main loop)
    setup_observability(service_name="transaction-streamer", metrics_port=8000)

    # Get a structured logger (call after setup_observability)
    logger = get_logger()
    logger.info("event_produced", event_id="abc-123", card_id="card3", amount=42.5)
    logger.error("produce_failed", error=str(e), event_id="abc-123")

All three pillars are fault-tolerant: if a component fails to initialize (e.g.
Tempo is not running), a warning is printed and the service continues normally.
Observability failures NEVER crash a pipeline service.

Environment variables:
    OTEL_EXPORTER_OTLP_ENDPOINT  — OTel collector (default: http://localhost:4318)
    OTEL_ENABLED                 — set "false" to disable tracing (default: true)
    METRICS_ENABLED              — set "false" to disable metrics server (default: true)
"""

import logging
import os

# ── Internal module-level logger (fallback before structlog is configured) ─────

_fallback_logger = logging.getLogger("observability")


# ── Public API ─────────────────────────────────────────────────────────────────

def setup_observability(service_name: str, metrics_port: int = 8000) -> None:
    """
    Initialize Prometheus metrics server, structlog JSON logging, and OTel tracing.
    Safe to call at the top of any service's run() function.
    """
    _setup_metrics(metrics_port)
    _setup_structlog(service_name)
    _setup_tracing(service_name)


def get_logger():
    """
    Return a structlog bound logger.  Always safe to call — falls back to the
    standard library logger if structlog is not available.
    """
    try:
        import structlog
        return structlog.get_logger()
    except ImportError:
        return logging.getLogger("fraud-detection")


# ── Internal helpers ───────────────────────────────────────────────────────────

def _setup_metrics(port: int) -> None:
    """Start the Prometheus /metrics HTTP server on the given port."""
    if os.environ.get("METRICS_ENABLED", "true").lower() != "true":
        return
    try:
        from prometheus_client import start_http_server
        start_http_server(port)
        print(f"[Observability] Prometheus metrics server started on port {port}")
    except Exception as exc:
        print(f"[Observability] Could not start metrics server on port {port}: {exc}")


def _setup_structlog(service_name: str) -> None:
    """
    Configure structlog to emit JSON log lines.

    Each log line includes:
        timestamp, level, service, event, **kwargs passed by the caller

    Example output:
        {"timestamp": "2026-03-09T12:00:01.123Z", "level": "info",
         "service": "persistence-service", "event": "event_persisted",
         "topic": "transactions", "event_id": "abc-123", "latency_ms": 12}
    """
    try:
        import structlog

        structlog.configure(
            processors=[
                structlog.contextvars.merge_contextvars,
                structlog.stdlib.add_log_level,
                structlog.processors.TimeStamper(fmt="iso"),
                structlog.processors.StackInfoRenderer(),
                structlog.processors.format_exc_info,
                structlog.processors.JSONRenderer(),
            ],
            wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
            context_class=dict,
            logger_factory=structlog.PrintLoggerFactory(),
            cache_logger_on_first_use=True,
        )
        # Bind service name to every subsequent log call from this process
        structlog.contextvars.bind_contextvars(service=service_name)
        print(f"[Observability] Structured JSON logging configured for '{service_name}'")
    except Exception as exc:
        print(f"[Observability] Could not configure structlog: {exc}")


def _setup_tracing(service_name: str) -> None:
    """
    Initialize an OpenTelemetry TracerProvider with OTLP HTTP export to Tempo.

    Reads OTEL_EXPORTER_OTLP_ENDPOINT (default: http://localhost:4318).
    Silently continues if the collector is unreachable — tracing errors
    are caught lazily at export time by the BatchSpanProcessor.
    """
    if os.environ.get("OTEL_ENABLED", "true").lower() != "true":
        return
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318")
        resource = Resource(attributes={"service.name": service_name})
        provider = TracerProvider(resource=resource)
        exporter = OTLPSpanExporter(endpoint=f"{endpoint}/v1/traces")
        provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(provider)
        print(f"[Observability] OTel tracing configured → {endpoint}")
    except Exception as exc:
        print(f"[Observability] Could not initialize OTel tracing: {exc}")
