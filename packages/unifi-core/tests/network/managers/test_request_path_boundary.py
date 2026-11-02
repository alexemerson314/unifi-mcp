"""``ConnectionManager.request`` refuses paths that would leave the named resource.

Managers interpolate caller ids into controller paths, and yarl collapses dot
segments (``..`` and ``%2e%2e``) before aiounifi sends the request. A
``group_id`` of ``../firewallrule/<id>`` would otherwise reach a different
resource family than the policy gate authorized.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from aiounifi.models.api import ApiRequest, ApiRequestV2
from unifi_core.auth import UniFiAuth
from unifi_core.network.managers.connection_manager import ConnectionManager
from unifi_core.network.managers.firewall_manager import FirewallManager
from unifi_core.request_paths import InvalidRequestPathError

TRAVERSAL = "../firewallrule/5f1e2d3c4b5a697887766554"


class _Session:
    closed = False

    async def close(self):
        return None


def _manager() -> tuple[ConnectionManager, AsyncMock]:
    manager = ConnectionManager("192.0.2.1", "admin", "secret")
    controller = MagicMock()
    controller.connectivity.config.session = _Session()
    controller.request = AsyncMock(return_value={"data": []})
    manager.controller = controller
    manager._aiohttp_session = controller.connectivity.config.session
    manager._initialized = True
    return manager, controller.request


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "api_request",
    [
        ApiRequest(method="delete", path=f"/rest/firewallgroup/{TRAVERSAL}"),
        ApiRequest(method="put", path="/rest/firewallgroup/%2e%2e/firewallrule/x", data={}),
        ApiRequestV2(method="delete", path=f"/static-dns/{TRAVERSAL}"),
        ApiRequestV2(method="get", path="/static-dns/x#y"),
        ApiRequest(method="get", path="/get/setting/../../stat/admin"),
    ],
)
async def test_request_refuses_retargeting_paths_before_the_controller(api_request):
    manager, controller_request = _manager()

    with pytest.raises(InvalidRequestPathError, match="^Invalid controller request path$"):
        await manager.request(api_request)

    controller_request.assert_not_awaited()


@pytest.mark.asyncio
async def test_request_sends_a_safe_path_with_a_query_string():
    manager, controller_request = _manager()
    api_request = ApiRequestV2(method="get", path="/traffic-flow-latest-statistics?period=DAY&top=10")

    assert await manager.request(api_request) == []

    controller_request.assert_awaited_once_with(api_request)


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/v1/sites/%2e%2e/x", "/v1/sites/..", "/v1/sites\\..\\x", "/v1/sites?x=1"])
async def test_request_integration_refuses_retargeting_paths(path):
    manager = ConnectionManager("192.0.2.1", "", "", auth=UniFiAuth(api_key="synthetic-key"))

    with pytest.raises(ValueError, match="^Invalid Network Integration API path$"):
        await manager.request_integration(path)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "call",
    [
        lambda manager: manager.get_firewall_group_by_id(TRAVERSAL),
        lambda manager: manager.update_firewall_group(TRAVERSAL, {"name": "x"}),
        lambda manager: manager.delete_firewall_group(TRAVERSAL),
    ],
)
async def test_firewall_group_ids_cannot_reach_another_resource_family(call):
    connection = MagicMock()
    connection.ensure_connected = AsyncMock(return_value=True)
    connection.request = AsyncMock()
    connection.get_cached = MagicMock(return_value=None)

    with pytest.raises(InvalidRequestPathError, match="^Invalid firewall group id$"):
        await call(FirewallManager(connection))

    connection.request.assert_not_awaited()


def _integration_firewall_manager() -> tuple[FirewallManager, MagicMock]:
    connection = MagicMock()
    connection.host = "192.0.2.1"
    connection.port = 443
    auth = MagicMock()
    auth.has_api_key = True
    auth.get_api_key_session = AsyncMock()
    return FirewallManager(connection, auth=auth), auth


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    [
        f"/v1/sites/{TRAVERSAL}",
        "/v1/sites/%2e%2e/firewall/zones",
        "/v1/sites/..;/firewall/zones",
        "/v1/sites/x#y",
    ],
)
async def test_firewall_integration_api_refuses_retargeting_paths_before_the_controller(path):
    manager, auth = _integration_firewall_manager()

    with pytest.raises(InvalidRequestPathError, match="^Invalid controller request path$"):
        await manager._request_integration_api("get", path)

    auth.get_api_key_session.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("site_id", "zone_id", "message"),
    [
        (TRAVERSAL, "0b8f3c1e-7d2a-4c5b-9e6f-1a2b3c4d5e6f", "^Invalid site id$"),
        ("0b8f3c1e-7d2a-4c5b-9e6f-1a2b3c4d5e6f", "../../policies", "^Invalid firewall zone id$"),
    ],
)
async def test_firewall_integration_zone_ids_cannot_reach_another_resource(site_id, zone_id, message):
    manager, auth = _integration_firewall_manager()
    manager._require_zone_mutation_session = AsyncMock()
    manager._resolve_zone_record = AsyncMock(return_value={"_id": "5f1e2d3c4b5a697887766554", "name": "Lab"})
    manager._get_integration_site_id = AsyncMock(return_value=site_id)
    manager._get_integration_firewall_zones = AsyncMock(return_value=[])
    manager._resolve_integration_zone_record = MagicMock(return_value={"id": zone_id})

    with pytest.raises(InvalidRequestPathError, match=message):
        await manager.delete_firewall_zone("5f1e2d3c4b5a697887766554")

    auth.get_api_key_session.assert_not_awaited()
