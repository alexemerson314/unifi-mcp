"""End-to-end tests for the opt-in preview-token mode of permissioned tools.

Tools are registered on a real ``StrictKwargFastMCP`` whose manifest, like the
committed ones, has no ``preview_token`` argument, and called through
``call_tool`` so FastMCP's argument validation runs.
"""

import json
import logging
import os
import pathlib
from unittest.mock import MagicMock

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from unifi_mcp_shared import preview_token
from unifi_mcp_shared.permissioned_tool import create_permissioned_tool, preview_token_tools
from unifi_mcp_shared.strict_dispatch import StrictKwargFastMCP
from unifi_mcp_shared.tool_index import get_tool_index

READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=False)


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    for key in list(os.environ):
        if "PREVIEW_TOKEN" in key or key.endswith("TOOL_PERMISSION_MODE") or key == "UNIFI_AUTO_CONFIRM":
            monkeypatch.delenv(key)
    # Restored after the test so the recorded mode does not reach other modules.
    monkeypatch.setattr(preview_token, "_mode_enabled", False)


def _manifest(tmp_path: pathlib.Path, tools: dict[str, list[str]]) -> pathlib.Path:
    path = tmp_path / "tools_manifest.json"
    entries = [
        {"name": name, "schema": {"input": {"type": "object", "properties": {a: {} for a in args}}}}
        for name, args in tools.items()
    ]
    path.write_text(json.dumps({"tools": entries}), encoding="utf-8")
    return path


class Harness:
    """A StrictKwargFastMCP with a permissioned decorator and an execution log."""

    def __init__(self, tmp_path: pathlib.Path, prefix: str, tools: dict[str, list[str]]):
        self.server = StrictKwargFastMCP("test", tools_manifest_path=_manifest(tmp_path, tools))
        self.index: dict[str, dict] = {}
        self.executed: list[dict] = []
        checker = MagicMock()
        checker.check.return_value = True

        def register(name, input_schema=None, **_):
            self.index[name] = input_schema

        self.tool = create_permissioned_tool(
            original_tool_decorator=self.server.tool,
            policy_gate_checker=checker,
            server_prefix=prefix,
            register_tool_fn=register,
            diagnostics_enabled_fn=lambda: False,
            wrap_tool_fn=lambda func, name: func,
            logger=logging.getLogger("test"),
        )

    def add_rename(self, name: str) -> None:
        @self.tool(name=name, description="rename", permission_category="networks", permission_action="update")
        async def rename(network_id: str, new_name: str, dry_note: str = "", confirm: bool = False):
            if not confirm:
                return {
                    "success": True,
                    "requires_confirmation": True,
                    "preview": {"current": {"id": network_id}, "proposed": {"name": new_name}},
                    "message": "Will rename. Set confirm=true to execute.",
                }
            self.executed.append({"network_id": network_id, "new_name": new_name})
            return {"success": True, "data": {"id": network_id, "name": new_name}}

    async def call(self, name: str, arguments: dict) -> dict:
        result = await self.server.call_tool(name, arguments)
        return result.structured_content


RENAME_ARGS = ["network_id", "new_name", "dry_note", "confirm"]


@pytest.fixture
def enabled(tmp_path, monkeypatch):
    monkeypatch.setenv("UNIFI_NETWORK_REQUIRE_PREVIEW_TOKEN", "true")
    harness = Harness(tmp_path, "network", {"net_rename": RENAME_ARGS})
    harness.add_rename("net_rename")
    return harness


@pytest.mark.asyncio
async def test_disabled_by_default_leaves_tools_unchanged(tmp_path):
    harness = Harness(tmp_path, "network", {"off_rename": RENAME_ARGS})
    harness.add_rename("off_rename")

    tools = await harness.server.list_tools()
    schema = next(t for t in tools if t.name == "off_rename").input_schema
    assert "preview_token" not in schema["properties"]
    assert "preview_token" not in harness.index["off_rename"]["properties"]
    assert "off_rename" not in preview_token_tools()

    result = await harness.call("off_rename", {"network_id": "n1", "new_name": "IoT", "confirm": True})
    assert result == {"success": True, "data": {"id": "n1", "name": "IoT"}}
    assert harness.executed == [{"network_id": "n1", "new_name": "IoT"}]


@pytest.mark.asyncio
async def test_enabled_advertises_the_argument(enabled):
    tools = await enabled.server.list_tools()
    schema = next(t for t in tools if t.name == "net_rename").input_schema
    assert "preview_token" in schema["properties"]
    assert "preview_token" not in schema.get("required", [])
    assert enabled.index["net_rename"]["properties"]["preview_token"]["type"] == "string"
    assert "net_rename" in preview_token_tools()


@pytest.mark.asyncio
async def test_confirm_without_token_is_refused(enabled):
    result = await enabled.call("net_rename", {"network_id": "n1", "new_name": "IoT", "confirm": True})
    assert result["success"] is False
    assert "requires a preview_token" in result["error"]
    assert enabled.executed == []


@pytest.mark.asyncio
async def test_preview_then_confirm_with_token_executes_once(enabled):
    preview = await enabled.call("net_rename", {"network_id": "n1", "new_name": "IoT"})
    assert preview["requires_confirmation"] is True
    assert preview["preview"] == {"current": {"id": "n1"}, "proposed": {"name": "IoT"}}
    assert preview["preview_token_expires_in"] == 300
    assert preview["message"].endswith("Send the preview_token from this response with confirm=true.")
    token = preview["preview_token"]
    assert isinstance(token, str) and len(token) >= 32

    # Omitted defaults and explicit defaults bind to the same request.
    args = {"network_id": "n1", "new_name": "IoT", "dry_note": "", "confirm": True, "preview_token": token}
    result = await enabled.call("net_rename", args)
    assert result == {"success": True, "data": {"id": "n1", "name": "IoT"}}
    assert enabled.executed == [{"network_id": "n1", "new_name": "IoT"}]

    replay = await enabled.call("net_rename", args)
    assert replay["success"] is False
    assert "unknown or was already used" in replay["error"]
    assert len(enabled.executed) == 1


@pytest.mark.asyncio
async def test_token_cannot_execute_different_arguments(enabled):
    preview = await enabled.call("net_rename", {"network_id": "n1", "new_name": "IoT"})
    token = preview["preview_token"]

    changed = await enabled.call(
        "net_rename", {"network_id": "n1", "new_name": "Guest", "confirm": True, "preview_token": token}
    )
    assert changed["success"] is False
    assert "issued for different arguments" in changed["error"]

    # The refused attempt spent the token.
    original = await enabled.call(
        "net_rename", {"network_id": "n1", "new_name": "IoT", "confirm": True, "preview_token": token}
    )
    assert original["success"] is False
    assert "unknown or was already used" in original["error"]
    assert enabled.executed == []


@pytest.mark.asyncio
async def test_token_cannot_execute_another_tool(tmp_path, monkeypatch):
    monkeypatch.setenv("UNIFI_REQUIRE_PREVIEW_TOKEN", "true")
    harness = Harness(tmp_path, "network", {"pair_a": RENAME_ARGS, "pair_b": RENAME_ARGS})
    harness.add_rename("pair_a")
    harness.add_rename("pair_b")

    token = (await harness.call("pair_a", {"network_id": "n1", "new_name": "IoT"}))["preview_token"]
    result = await harness.call(
        "pair_b", {"network_id": "n1", "new_name": "IoT", "confirm": True, "preview_token": token}
    )
    assert result["success"] is False
    assert "issued for different arguments" in result["error"]
    assert harness.executed == []


@pytest.mark.asyncio
async def test_failed_preview_gets_no_token(tmp_path, monkeypatch):
    monkeypatch.setenv("UNIFI_REQUIRE_PREVIEW_TOKEN", "true")
    harness = Harness(tmp_path, "network", {"bad_preview": ["network_id", "confirm"]})

    @harness.tool(name="bad_preview", description="t", permission_category="networks", permission_action="delete")
    async def bad_preview(network_id: str, confirm: bool = False):
        return {"success": False, "error": "Failed to preview delete: network not found"}

    result = await harness.call("bad_preview", {"network_id": "n9"})
    assert result == {"success": False, "error": "Failed to preview delete: network not found"}


@pytest.mark.asyncio
async def test_bypass_mode_needs_no_token(tmp_path, monkeypatch):
    monkeypatch.setenv("UNIFI_REQUIRE_PREVIEW_TOKEN", "true")
    monkeypatch.setenv("UNIFI_TOOL_PERMISSION_MODE", "bypass")
    harness = Harness(tmp_path, "network", {"bypass_rename": RENAME_ARGS})
    harness.add_rename("bypass_rename")

    result = await harness.call("bypass_rename", {"network_id": "n1", "new_name": "IoT", "confirm": True})
    assert result == {"success": True, "data": {"id": "n1", "name": "IoT"}}
    assert harness.executed == [{"network_id": "n1", "new_name": "IoT"}]


@pytest.mark.asyncio
async def test_per_server_setting_scopes_the_mode(tmp_path, monkeypatch):
    monkeypatch.setenv("UNIFI_NETWORK_REQUIRE_PREVIEW_TOKEN", "true")
    harness = Harness(tmp_path, "protect", {"cam_rename": RENAME_ARGS})
    harness.add_rename("cam_rename")

    with pytest.raises(ToolError, match="unknown arguments {preview_token}"):
        await harness.call("cam_rename", {"network_id": "c1", "new_name": "Door", "preview_token": "x"})
    result = await harness.call("cam_rename", {"network_id": "c1", "new_name": "Door", "confirm": True})
    assert result["success"] is True


@pytest.mark.asyncio
async def test_read_tools_are_unaffected(tmp_path, monkeypatch):
    monkeypatch.setenv("UNIFI_REQUIRE_PREVIEW_TOKEN", "true")
    harness = Harness(tmp_path, "network", {"read_tool": ["site"]})

    @harness.tool(name="read_tool", description="t", annotations=READ_ONLY)
    async def read_tool(site: str = "default"):
        return {"success": True, "data": [site]}

    tools = await harness.server.list_tools()
    assert "preview_token" not in next(t for t in tools if t.name == "read_tool").input_schema["properties"]
    assert await harness.call("read_tool", {}) == {"success": True, "data": ["default"]}


def test_creating_the_decorator_logs_the_mode(monkeypatch, caplog):
    """Shared logs the mode at setup, so apps need no import of their own."""
    monkeypatch.setenv("UNIFI_REQUIRE_PREVIEW_TOKEN", "true")
    with caplog.at_level(logging.INFO, logger="setup"):
        create_permissioned_tool(
            original_tool_decorator=MagicMock(),
            policy_gate_checker=MagicMock(),
            server_prefix="network",
            register_tool_fn=MagicMock(),
            diagnostics_enabled_fn=lambda: False,
            wrap_tool_fn=lambda func, name: func,
            logger=logging.getLogger("setup"),
        )
    assert "[preview-token] confirm=true requires a preview_token for network" in caplog.text


def _lazy_index_schemas(manifest_path: pathlib.Path) -> dict[str, dict]:
    index = get_tool_index(registration_mode="lazy", manifest_path=manifest_path, include_schemas=True)
    return {tool["name"]: tool["schema"]["input"]["properties"] for tool in index["tools"]}


@pytest.mark.parametrize("enable", [True, False])
def test_lazy_tool_index_lists_the_argument_only_with_the_mode_on(tmp_path, monkeypatch, enable):
    """The lazy index reads the committed manifest, which is generated with the mode off."""
    path = tmp_path / "committed_manifest.json"
    entries = [
        ("lazy_rename", "update", ["network_id", "confirm"]),
        ("lazy_reboot", "update", ["device_id"]),
        ("lazy_list", None, ["site"]),
    ]
    path.write_text(
        json.dumps(
            {
                "tools": [
                    {
                        "name": name,
                        **({"permission_action": action} if action else {}),
                        "schema": {"input": {"type": "object", "properties": {a: {} for a in args}}},
                    }
                    for name, action, args in entries
                ]
            }
        ),
        encoding="utf-8",
    )
    if enable:
        monkeypatch.setenv("UNIFI_NETWORK_REQUIRE_PREVIEW_TOKEN", "true")
    # Creating the decorator records the mode; no tool is loaded, as in lazy mode.
    Harness(tmp_path, "network", {})

    schemas = _lazy_index_schemas(path)

    assert ("preview_token" in schemas["lazy_rename"]) is enable
    assert "preview_token" not in schemas["lazy_reboot"]
    assert "preview_token" not in schemas["lazy_list"]
    if enable:
        assert schemas["lazy_rename"]["preview_token"]["type"] == "string"
        # The cached manifest itself is left as committed.
        monkeypatch.setattr(preview_token, "_mode_enabled", False)
        assert "preview_token" not in _lazy_index_schemas(path)["lazy_rename"]
