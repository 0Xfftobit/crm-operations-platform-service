"""OpenTelemetry tracing (TRACING_ENABLED=true, `[otel]` extra).

Sets up a tracer provider with an OTLP/HTTP exporter and auto-instruments the FastAPI app so every
request — precheck, ingest, audit query — becomes a span (design §10: 'tracing across precheck →
rule → audit → alert → case'). Everything is lazily imported and degrades to a no-op if disabled or
if the OTel packages aren't installed, so the core never depends on it.
"""
import structlog

from app.core.config import get_settings

logger = structlog.get_logger(__name__)


def configure_tracing(app) -> bool:
    """Instrument ``app`` if tracing is enabled and the OTel libs are present. Returns success."""
    settings = get_settings()
    if not settings.tracing_enabled:
        return False
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except Exception as exc:  # noqa: BLE001 — otel not installed → degrade to no-op
        logger.warning("tracing_unavailable", error=str(exc))
        return False

    resource = Resource.create({"service.name": settings.otel_service_name})
    provider = TracerProvider(resource=resource)
    if settings.otel_exporter_otlp_endpoint:
        exporter = OTLPSpanExporter(endpoint=settings.otel_exporter_otlp_endpoint)
        provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    FastAPIInstrumentor.instrument_app(app)
    logger.info("tracing_enabled", service=settings.otel_service_name,
                endpoint=settings.otel_exporter_otlp_endpoint or "(none)")
    return True
