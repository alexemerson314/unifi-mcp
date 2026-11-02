"""Tests for the opt-in preview-token store and its settings."""

import logging
import os

import pytest
from unifi_mcp_shared.preview_token import (
    DEFAULT_TTL_SECONDS,
    MAX_TTL_SECONDS,
    MIN_TTL_SECONDS,
    PreviewTokenStore,
    check_preview_token_settings,
    preview_token_required,
    preview_token_ttl_seconds,
    request_digest,
)


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    for key in list(os.environ):
        if "PREVIEW_TOKEN" in key or key.endswith("TOOL_PERMISSION_MODE") or key == "UNIFI_AUTO_CONFIRM":
            monkeypatch.delenv(key)


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class TestPreviewTokenRequired:
    def test_off_by_default(self):
        assert preview_token_required("network") is False

    @pytest.mark.parametrize("value", ["true", "1", "yes", "on", " TRUE "])
    def test_global_truthy(self, monkeypatch, value):
        monkeypatch.setenv("UNIFI_REQUIRE_PREVIEW_TOKEN", value)
        assert preview_token_required("network") is True

    def test_server_overrides_global(self, monkeypatch):
        monkeypatch.setenv("UNIFI_REQUIRE_PREVIEW_TOKEN", "true")
        monkeypatch.setenv("UNIFI_NETWORK_REQUIRE_PREVIEW_TOKEN", "false")
        assert preview_token_required("network") is False
        assert preview_token_required("protect") is True

    def test_empty_server_value_falls_through_to_global(self, monkeypatch):
        monkeypatch.setenv("UNIFI_REQUIRE_PREVIEW_TOKEN", "true")
        monkeypatch.setenv("UNIFI_NETWORK_REQUIRE_PREVIEW_TOKEN", "  ")
        assert preview_token_required("network") is True

    def test_unrecognized_value_fails_closed(self, monkeypatch, caplog):
        monkeypatch.setenv("UNIFI_ACCESS_REQUIRE_PREVIEW_TOKEN", "ture")
        with caplog.at_level(logging.WARNING, logger="unifi_mcp_shared.preview_token"):
            assert preview_token_required("access") is True
        assert "UNIFI_ACCESS_REQUIRE_PREVIEW_TOKEN" in caplog.text
        assert "ture" not in caplog.text


class TestTtl:
    def test_default(self):
        assert preview_token_ttl_seconds() == DEFAULT_TTL_SECONDS

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [("120", 120), ("1", MIN_TTL_SECONDS), ("999999", MAX_TTL_SECONDS), ("abc", DEFAULT_TTL_SECONDS)],
    )
    def test_parsed_and_clamped(self, monkeypatch, raw, expected):
        monkeypatch.setenv("UNIFI_PREVIEW_TOKEN_TTL_SECONDS", raw)
        assert preview_token_ttl_seconds() == expected


class TestRequestDigest:
    def test_ignores_confirm_and_token(self):
        base = request_digest("unifi_update_network", {"network_id": "n1", "name": "IoT"})
        assert (
            request_digest(
                "unifi_update_network", {"network_id": "n1", "name": "IoT", "confirm": True, "preview_token": "x"}
            )
            == base
        )

    def test_key_order_does_not_matter(self):
        assert request_digest("t", {"a": 1, "b": {"y": 2, "x": 1}}) == request_digest(
            "t", {"b": {"x": 1, "y": 2}, "a": 1}
        )

    @pytest.mark.parametrize(
        ("tool", "arguments"),
        [
            ("unifi_update_network", {"network_id": "n1", "name": "Guest"}),
            ("unifi_update_network", {"network_id": "n2", "name": "IoT"}),
            ("unifi_delete_network", {"network_id": "n1", "name": "IoT"}),
            ("unifi_update_network", {"network_id": "n1", "name": "IoT", "extra": None}),
        ],
    )
    def test_changes_with_tool_or_arguments(self, tool, arguments):
        assert request_digest(tool, arguments) != request_digest(
            "unifi_update_network", {"network_id": "n1", "name": "IoT"}
        )


class TestPreviewTokenStore:
    def test_token_redeems_once(self):
        store = PreviewTokenStore(60)
        token = store.issue("d1")
        assert store.redeem(token, "d1") is None
        assert store.redeem(token, "d1") == "it is unknown or was already used"

    def test_unknown_token(self):
        assert PreviewTokenStore(60).redeem("nope", "d1") == "it is unknown or was already used"

    def test_wrong_digest_is_refused_and_spends_the_token(self):
        store = PreviewTokenStore(60)
        token = store.issue("d1")
        assert store.redeem(token, "d2") == "it was issued for different arguments"
        assert store.redeem(token, "d1") == "it is unknown or was already used"

    def test_expiry(self):
        clock = FakeClock()
        store = PreviewTokenStore(60, clock=clock)
        token = store.issue("d1")
        clock.now += 60
        assert store.redeem(token, "d1") == "it has expired"

    def test_valid_just_before_expiry(self):
        clock = FakeClock()
        store = PreviewTokenStore(60, clock=clock)
        token = store.issue("d1")
        clock.now += 59.9
        assert store.redeem(token, "d1") is None

    def test_tokens_are_distinct_for_the_same_digest(self):
        store = PreviewTokenStore(60)
        first, second = store.issue("d1"), store.issue("d1")
        assert first != second
        assert store.redeem(first, "d1") is None
        assert store.redeem(second, "d1") is None

    def test_issue_prunes_expired_tokens(self):
        clock = FakeClock()
        store = PreviewTokenStore(60, clock=clock)
        store.issue("d1")
        store.issue("d2")
        clock.now += 61
        store.issue("d3")
        assert len(store) == 1

    def test_full_store_drops_oldest(self):
        store = PreviewTokenStore(60, max_tokens=2)
        oldest = store.issue("d1")
        middle = store.issue("d2")
        newest = store.issue("d3")
        assert len(store) == 2
        assert store.redeem(oldest, "d1") == "it is unknown or was already used"
        assert store.redeem(middle, "d2") is None
        assert store.redeem(newest, "d3") is None


class TestStartupCheck:
    def test_silent_when_off(self, caplog):
        with caplog.at_level(logging.INFO):
            check_preview_token_settings("network", logging.getLogger("t"))
        assert caplog.records == []

    def test_logs_when_on(self, monkeypatch, caplog):
        monkeypatch.setenv("UNIFI_REQUIRE_PREVIEW_TOKEN", "true")
        with caplog.at_level(logging.INFO):
            check_preview_token_settings("network", logging.getLogger("t"))
        assert len(caplog.records) == 1
        assert caplog.records[0].levelno == logging.INFO
        assert "requires a preview_token for network (TTL 300s)" in caplog.text

    def test_warns_when_bypass_makes_it_moot(self, monkeypatch, caplog):
        monkeypatch.setenv("UNIFI_REQUIRE_PREVIEW_TOKEN", "true")
        monkeypatch.setenv("UNIFI_NETWORK_TOOL_PERMISSION_MODE", "bypass")
        with caplog.at_level(logging.INFO):
            check_preview_token_settings("network", logging.getLogger("t"))
        assert len(caplog.records) == 1
        assert caplog.records[0].levelno == logging.WARNING
        assert "bypass mode" in caplog.text
