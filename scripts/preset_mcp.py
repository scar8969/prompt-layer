#!/usr/bin/env python
"""Stdio transport for the catalogue MCP server.

    python scripts/preset_mcp.py

Speaks newline-delimited JSON-RPC 2.0 on stdin/stdout, which is what an MCP
client launches. The dispatcher is `studio.mcp`: it reads the same preset and
cinema documents the HTTP routes read, and it can only look and compile. It has
no credential, opens no database and cannot queue a generation.

Logs go to stderr so stdout stays a clean protocol stream.
"""

from __future__ import annotations

import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from studio.mcp import handle  # noqa: E402


def main() -> int:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except ValueError:
            sys.stdout.write(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {"code": -32700, "message": "Parse error"},
                    }
                )
                + "\n"
            )
            sys.stdout.flush()
            continue
        if not isinstance(message, dict):
            continue
        response = handle(message)
        # A notification carries no id and gets no reply.
        if message.get("id") is None and "error" not in response:
            continue
        sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
