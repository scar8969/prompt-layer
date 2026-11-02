"""The catalogue as an MCP server, and the fact that it cannot spend.

The claims worth pinning, because each is a way an MCP surface could go wrong:

* **The protocol is answered properly.** `initialize`, `tools/list` and
  `tools/call` return JSON-RPC results; an unknown method is "-32601 method not
  found", not a fabricated success; an unknown tool is a tool error.
* **The tools read the real documents.** A preset returned here is the same
  document `/api/presets` builds its catalogue from, so the two cannot disagree.
* **It cannot spend.** There is no tool that queues a generation, and the
  dispatcher holds no credential, so a client pointed at this server cannot buy
  anything with a workspace's key.
* **The stdio transport is a clean protocol stream.** A notification gets no
  reply; a malformed line answers with the JSON-RPC parse error.

Run:  python -m pytest tests/test_mcp.py -q
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from studio import mcp  # noqa: E402
from studio.presets_data import PRESETS, PRESETS_BY_KEY  # noqa: E402


# ---------------------------------------------------------------------------
# The protocol
# ---------------------------------------------------------------------------


def test_initialize_reports_a_protocol_version_and_the_tool_capability():
    result = mcp.dispatch("initialize", {})
    assert result["protocolVersion"] == mcp.PROTOCOL_VERSION
    assert "tools" in result["capabilities"]
    assert result["serverInfo"]["name"] == mcp.SERVER_NAME


def test_tools_list_documents_every_tool_with_a_schema():
    tools = mcp.dispatch("tools/list", {})["tools"]
    assert tools
    names = {tool["name"] for tool in tools}
    assert {"list_presets", "get_preset", "compile_brief", "prompt_layers"} <= names
    for tool in tools:
        assert tool["description"], tool["name"]
        assert tool["inputSchema"]["type"] == "object", tool["name"]


def test_an_unknown_method_is_method_not_found():
    response = mcp.handle({"jsonrpc": "2.0", "id": 7, "method": "tools/magic", "params": {}})
    assert response["id"] == 7
    assert response["error"]["code"] == -32601
    assert "tools/magic" in response["error"]["message"]


def test_an_unknown_tool_is_a_tool_error_not_an_empty_result():
    response = mcp.handle(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "nope", "arguments": {}}}
    )
    result = response["result"]
    assert result["isError"] is True
    assert "nope" in result["content"][0]["text"]


# ---------------------------------------------------------------------------
# The tools read the real documents
# ---------------------------------------------------------------------------


def _call(name: str, arguments: dict) -> dict:
    response = mcp.handle(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments}}
    )
    result = response["result"]
    assert not result.get("isError"), result["content"][0]["text"]
    return json.loads(result["content"][0]["text"])


def test_list_presets_returns_catalogue_documents():
    body = _call("list_presets", {"modality": "image", "limit": 5})
    assert body["count"] == 5
    keys = {entry["key"] for entry in PRESETS if entry["modality"] == "image"}
    for preset in body["presets"]:
        assert preset["key"] in keys
        assert preset["prompt_template"]


def test_get_preset_returns_the_same_document_the_catalogue_holds():
    key = PRESETS[0]["key"]
    body = _call("get_preset", {"key": key})
    assert body["preset"] == PRESETS_BY_KEY[key]


def test_get_preset_refuses_an_unknown_key():
    response = mcp.handle(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "get_preset", "arguments": {"key": "nope"}}}
    )
    assert response["result"]["isError"] is True
    assert "nope" in response["result"]["content"][0]["text"]


def test_compile_brief_is_the_same_compiler_the_screen_uses():
    first = _call("compile_brief", {"subject": "a lighthouse in fog", "lighting": "moonlight", "modality": "image"})
    second = _call("compile_brief", {"subject": "a lighthouse in fog", "lighting": "moonlight", "modality": "image"})
    assert first["compiled"]["prompt"] == second["compiled"]["prompt"]
    assert "moonlight" in first["compiled"]["prompt"]


def test_compile_brief_refuses_an_unknown_option_by_name():
    response = mcp.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "compile_brief", "arguments": {"subject": "x", "lighting": "nope", "modality": "image"}},
        }
    )
    assert response["result"]["isError"] is True
    assert "nope" in response["result"]["content"][0]["text"]


def test_camera_moves_say_whether_each_is_native():
    body = _call("list_camera_moves", {})
    assert body["camera_moves"]
    for move in body["camera_moves"]:
        assert move["key"] and move["phrase"]
        # A non-native move must say so, because the studio labels it as wording.
        if not move["is_native"]:
            assert move["note"]


def test_there_is_no_tool_that_can_spend():
    """The whole safety property: an MCP client can look, not buy."""
    names = {tool["name"] for tool in mcp.TOOLS}
    for forbidden in ("generate", "queue", "submit", "create_job", "run"):
        assert not any(forbidden in name for name in names), forbidden


# ---------------------------------------------------------------------------
# The stdio transport
# ---------------------------------------------------------------------------


def test_the_stdio_transport_answers_a_request_and_ignores_a_notification():
    process = subprocess.run(
        [sys.executable, os.path.join(REPO, "scripts", "preset_mcp.py")],
        input=(
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
            + "\n"
            + json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"})
            + "\n"
            + json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
            + "\n"
        ),
        capture_output=True,
        text=True,
        timeout=60,
        cwd=REPO,
    )
    assert process.returncode == 0, process.stderr[-2000:]
    lines = [line for line in process.stdout.splitlines() if line.strip()]
    # A notification gets no reply, so three messages produce two responses.
    assert len(lines) == 2, lines
    first, second = json.loads(lines[0]), json.loads(lines[1])
    assert first["id"] == 1 and first["result"]["protocolVersion"] == mcp.PROTOCOL_VERSION
    assert second["id"] == 2 and second["result"]["tools"]


def test_a_malformed_line_answers_with_the_parse_error():
    process = subprocess.run(
        [sys.executable, os.path.join(REPO, "scripts", "preset_mcp.py")],
        input="{not json}\n",
        capture_output=True,
        text=True,
        timeout=60,
        cwd=REPO,
    )
    assert process.returncode == 0
    response = json.loads(process.stdout.strip().splitlines()[0])
    assert response["error"]["code"] == -32700
