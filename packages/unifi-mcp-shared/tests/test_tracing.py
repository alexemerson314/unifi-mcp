"""Tests for the optional OTLP trace export."""

import logging
import os
import sys
from types import SimpleNamespace

import pytest
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http import trace_exporter as http_trace_exporter
from opentelemetry.sdk.resources import SERVICE_NAME
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from unifi_mcp_shared.tracing import configure_tracing

LOGGER = logging.getLogger("test-tracing")
ENDPOINT = "http://127.0.0.1:4318"
GRPC_EXPORTER_MODULE = "opentelemetry.exporter.otlp.proto.grpc.trace_exporter"


@pytest.fixture(autouse=True)
def clean_otel_env(monkeypatch):
    for var in [name for name in os.environ if name.startswith("OTEL_")]:
        monkeypatch.delenv(var)


@pytest.fixture()
def installed(monkeypatch):
    """Record the provider configure_tracing installs instead of setting it globally."""
    state = SimpleNamespace(provider=None)
    monkeypatch.setattr(trace, "get_tracer_provider", trace.ProxyTracerProvider)
    monkeypatch.setattr(trace, "set_tracer_provider", lambda provider: setattr(state, "provider", provider))
    yield state
    if state.provider is not None:
        state.provider.shutdown()


@pytest.fixture()
def exporters(monkeypatch):
    """Replace both OTLP span exporters with in-memory ones, keyed by protocol.

    The otel extra does not install the gRPC exporter, so a stand-in module provides it.
    """
    created = {}

    def factory_for(protocol):
        def factory():
            created[protocol] = InMemorySpanExporter()
            return created[protocol]

        return factory

    monkeypatch.setattr(http_trace_exporter, "OTLPSpanExporter", factory_for("http/protobuf"))
    monkeypatch.setitem(sys.modules, GRPC_EXPORTER_MODULE, SimpleNamespace(OTLPSpanExporter=factory_for("grpc")))
    return created


@pytest.mark.parametrize(
    "env",
    [
        {},
        {"OTEL_EXPORTER_OTLP_ENDPOINT": "   "},
        {"OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": "\t", "OTEL_EXPORTER_OTLP_ENDPOINT": ""},
    ],
)
def test_no_endpoint_leaves_tracing_off(monkeypatch, installed, exporters, env):
    for name, value in env.items():
        monkeypatch.setenv(name, value)

    assert configure_tracing("unifi-test-mcp", logger=LOGGER) is False
    assert installed.provider is None
    assert exporters == {}


def test_sdk_disabled_wins_over_an_endpoint(monkeypatch, installed, exporters):
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", ENDPOINT)
    monkeypatch.setenv("OTEL_SDK_DISABLED", "true")

    assert configure_tracing("unifi-test-mcp", logger=LOGGER) is False
    assert installed.provider is None
    assert exporters == {}


@pytest.mark.parametrize(
    ("env", "expected_protocol"),
    [
        ({"OTEL_EXPORTER_OTLP_ENDPOINT": ENDPOINT}, "http/protobuf"),
        (
            {
                "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": "http://127.0.0.1:4317",
                "OTEL_EXPORTER_OTLP_PROTOCOL": "grpc",
            },
            "grpc",
        ),
        (
            {
                "OTEL_EXPORTER_OTLP_ENDPOINT": ENDPOINT,
                "OTEL_EXPORTER_OTLP_PROTOCOL": "http/protobuf",
                "OTEL_EXPORTER_OTLP_TRACES_PROTOCOL": "grpc",
            },
            "grpc",
        ),
        (
            {
                "OTEL_EXPORTER_OTLP_ENDPOINT": ENDPOINT,
                "OTEL_EXPORTER_OTLP_PROTOCOL": "grpc",
                "OTEL_EXPORTER_OTLP_TRACES_PROTOCOL": " ",
            },
            "grpc",
        ),
    ],
)
def test_endpoint_selects_the_exporter_by_protocol(monkeypatch, installed, exporters, env, expected_protocol):
    for name, value in env.items():
        monkeypatch.setenv(name, value)

    assert configure_tracing("unifi-test-mcp", logger=LOGGER) is True
    assert installed.provider is not None
    assert list(exporters) == [expected_protocol]


def test_spans_reach_the_exporter(monkeypatch, installed, exporters):
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", ENDPOINT)
    assert configure_tracing("unifi-test-mcp", logger=LOGGER) is True

    with installed.provider.get_tracer("test").start_as_current_span("tools/call unifi_example"):
        pass
    assert installed.provider.force_flush() is True

    spans = exporters["http/protobuf"].get_finished_spans()
    assert len(spans) == 1
    assert spans[0].name == "tools/call unifi_example"
    assert spans[0].resource.attributes[SERVICE_NAME] == "unifi-test-mcp"


@pytest.mark.parametrize(
    ("env", "expected_name"),
    [
        ({}, "unifi-test-mcp"),
        ({"OTEL_SERVICE_NAME": "unifi-network-lab"}, "unifi-network-lab"),
        (
            {"OTEL_RESOURCE_ATTRIBUTES": "service.name=unifi-from-attributes,deployment.environment=lab"},
            "unifi-from-attributes",
        ),
    ],
)
def test_service_name_defaults_to_the_server(monkeypatch, installed, exporters, env, expected_name):
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", ENDPOINT)
    for name, value in env.items():
        monkeypatch.setenv(name, value)

    assert configure_tracing("unifi-test-mcp", logger=LOGGER) is True
    assert installed.provider.resource.attributes[SERVICE_NAME] == expected_name


def test_unsupported_protocol_is_logged_and_not_exported(monkeypatch, installed, exporters, caplog):
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", ENDPOINT)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_PROTOCOL", "http/json")

    with caplog.at_level(logging.WARNING, logger=LOGGER.name):
        assert configure_tracing("unifi-test-mcp", logger=LOGGER) is False
    assert installed.provider is None
    assert exporters == {}
    assert "Unsupported OTLP protocol 'http/json'" in caplog.text


def test_missing_sdk_is_logged_and_not_exported(monkeypatch, installed, caplog):
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", ENDPOINT)
    monkeypatch.setitem(sys.modules, "opentelemetry.sdk.trace", None)

    with caplog.at_level(logging.WARNING, logger=LOGGER.name):
        assert configure_tracing("unifi-test-mcp", logger=LOGGER) is False
    assert installed.provider is None
    assert "install the 'otel' extra" in caplog.text


def test_missing_grpc_exporter_is_logged_and_not_exported(monkeypatch, installed, caplog):
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:4317")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_PROTOCOL", "grpc")
    monkeypatch.setitem(sys.modules, GRPC_EXPORTER_MODULE, None)

    with caplog.at_level(logging.WARNING, logger=LOGGER.name):
        assert configure_tracing("unifi-test-mcp", logger=LOGGER) is False
    assert installed.provider is None
    assert "OTLP protocol 'grpc' needs opentelemetry-exporter-otlp-proto-grpc, which is not installed" in caplog.text


def test_existing_provider_is_left_in_place(monkeypatch, installed, exporters, caplog):
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", ENDPOINT)
    existing = TracerProvider()
    monkeypatch.setattr(trace, "get_tracer_provider", lambda: existing)

    with caplog.at_level(logging.INFO, logger=LOGGER.name):
        assert configure_tracing("unifi-test-mcp", logger=LOGGER) is False
    existing.shutdown()
    assert installed.provider is None
    assert exporters == {}
    assert "already installed" in caplog.text
