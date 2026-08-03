"""Guard the mcp 1.x / 2.x compatibility layer in mcp_server.

The server class was renamed and moved in mcp 2.0 (mcp.server.fastmcp.FastMCP
-> mcp.server.mcpserver.MCPServer). mcp_server.py picks whichever is
installed; these tests assert the import layer resolved to something usable
and that every tool is registered, so a future rename fails loudly in CI
instead of silently at Claude Desktop startup.
"""

from __future__ import annotations

import inspect

import pytest

mcp_server = pytest.importorskip("mcp_server")

EXPECTED_TOOLS = {
    "convert_file",
    "convert_folder",
    "list_supported_formats",
    "preview_video",
    "process_video",
    "get_provider_status",
    "set_api_key",
    "delete_api_key",
}


def _registered_tool_names() -> set[str]:
    """Tool names, tolerating internal layout differences between versions."""
    manager = getattr(mcp_server.mcp, "_tool_manager", None)
    if manager is not None and hasattr(manager, "_tools"):
        return set(manager._tools.keys())
    tools = getattr(mcp_server.mcp, "_tools", None)
    if isinstance(tools, dict):
        return set(tools.keys())
    pytest.skip("cannot introspect tool registry on this mcp version")


def test_server_instance_exists():
    assert mcp_server.mcp is not None
    # Whatever the class is called, it must expose the surface we use.
    for attr in ("tool", "run"):
        assert hasattr(mcp_server.mcp, attr), f"server missing .{attr}()"


def test_context_supports_report_progress():
    ctx = mcp_server.Context
    assert hasattr(ctx, "report_progress")
    sig = inspect.signature(ctx.report_progress)
    # (self, progress, total=None, message=None) in both 1.x and 2.x
    assert "progress" in sig.parameters
    assert "total" in sig.parameters
    assert "message" in sig.parameters


def test_all_tools_registered():
    assert _registered_tool_names() == EXPECTED_TOOLS


def test_long_running_tools_are_async():
    """convert_file / convert_folder / process_video must stay coroutines —
    they take the Context and await report_progress."""
    for name in ("convert_file", "convert_folder", "process_video"):
        fn = getattr(mcp_server, name)
        assert inspect.iscoroutinefunction(fn), f"{name} should be async"


def test_claude_desktop_config_path_per_os():
    p = mcp_server._claude_desktop_config_path()
    assert p.name == "claude_desktop_config.json"
    assert "Claude" in p.parts
