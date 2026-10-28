"""OpenTelemetry trace export for MCP servers.

The MCP SDK wraps every inbound message in a server span (``tools/call <tool>``
carries ``gen_ai.tool.name``) and continues a W3C trace context passed in the
request's ``_meta``. It only depends on ``opentelemetry-api``, so without a
``TracerProvider`` those spans are no-ops.

``configure_tracing`` installs a provider with an OTLP span exporter when an
OTLP endpoint is configured. Everything else follows the standard ``OTEL_*``
variables, which the OpenTelemetry SDK reads itself: sampler, resource
attributes, headers, timeouts, compression and TLS. The SDK and the OTLP/HTTP
exporter ship in the ``otel`` extra; gRPC needs its exporter installed separately.
"""

from __future__ import annotations

import importlib
import logging
import os

from unifi_core.config_helpers import parse_config_bool

_ENDPOINT_VARS = ("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "OTEL_EXPORTER_OTLP_ENDPOINT")
_PROTOCOL_VARS = ("OTEL_EXPORTER_OTLP_TRACES_PROTOCOL", "OTEL_EXPORTER_OTLP_PROTOCOL")
# The OTLP exporter specification's default protocol.
_DEFAULT_PROTOCOL = "http/protobuf"
# Protocol -> (exporter module, package that provides it).
_EXPORTERS = {
    "http/protobuf": (
        "opentelemetry.exporter.otlp.proto.http.trace_exporter",
        "opentelemetry-exporter-otlp-proto-http",
    ),
    "grpc": ("opentelemetry.exporter.otlp.proto.grpc.trace_exporter", "opentelemetry-exporter-otlp-proto-grpc"),
}


def _first_set(names: tuple[str, ...]) -> str | None:
    """Return the first of *names* set to a non-blank value, stripped."""
    return next((value for name in names if (value := os.environ.get(name, "").strip())), None)


def configure_tracing(service_name: str, *, logger: logging.Logger) -> bool:
    """Export the MCP SDK's spans over OTLP when an OTLP endpoint is configured.

    *service_name* names the service unless ``OTEL_SERVICE_NAME`` or
    ``OTEL_RESOURCE_ATTRIBUTES`` does. Returns True when spans are exported.
    Without an endpoint nothing changes. An unsupported protocol, a missing
    SDK or exporter, or an already installed provider is logged, and the
    server keeps running without this exporter.
    """
    if parse_config_bool(os.environ.get("OTEL_SDK_DISABLED"), default=False):
        return False
    if _first_set(_ENDPOINT_VARS) is None:
        return False

    protocol = _first_set(_PROTOCOL_VARS) or _DEFAULT_PROTOCOL
    if protocol not in _EXPORTERS:
        logger.warning("Unsupported OTLP protocol '%s' (use http/protobuf or grpc); traces are not exported.", protocol)
        return False

    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import SERVICE_NAME, OTELResourceDetector, Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        logger.warning(
            "An OTLP endpoint is set, but the OpenTelemetry SDK is not installed; "
            "install the 'otel' extra to export traces."
        )
        return False

    module_name, package = _EXPORTERS[protocol]
    try:
        exporter_class = importlib.import_module(module_name).OTLPSpanExporter
    except ImportError:
        logger.warning(
            "OTLP protocol '%s' needs %s, which is not installed; traces are not exported.", protocol, package
        )
        return False

    if not isinstance(trace.get_tracer_provider(), trace.ProxyTracerProvider):
        logger.info("A TracerProvider is already installed; leaving trace export to it.")
        return False

    named_by_env = SERVICE_NAME in OTELResourceDetector().detect().attributes
    provider = TracerProvider(resource=Resource.create({} if named_by_env else {SERVICE_NAME: service_name}))
    provider.add_span_processor(BatchSpanProcessor(exporter_class()))
    trace.set_tracer_provider(provider)
    logger.info("Exporting traces over OTLP (%s).", protocol)
    return True
