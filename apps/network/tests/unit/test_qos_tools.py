"""Tests for QoS rule tools."""

import os
from unittest.mock import patch

import pytest

os.environ.setdefault("UNIFI_HOST", "127.0.0.1")
os.environ.setdefault("UNIFI_USERNAME", "test")
os.environ.setdefault("UNIFI_PASSWORD", "test")


@pytest.mark.asyncio
async def test_simple_create_preview_requires_confirmation():
    with patch("unifi_network_mcp.tools.qos.qos_manager") as mock_qm:
        from unifi_network_mcp.tools.qos import create_simple_qos_rule

        result = await create_simple_qos_rule(
            {"name": "Zoom Upload Limit", "interface": "wan", "direction": "upload", "limit_kbps": 2000},
            confirm=False,
        )

    assert result["success"] is True
    assert result["requires_confirmation"] is True
    assert result["preview"]["will_create"]["bandwidth_limit_kbps"] == 2000
    mock_qm.create_qos_rule.assert_not_called()
