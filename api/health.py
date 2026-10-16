"""GET /api/health — liveness plus how much of the dataset is loaded."""

from __future__ import annotations

import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from promptlayer import model_list  # noqa: E402
from promptlayer.http import send_json  # noqa: E402


class handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        send_json(self, 200, {"success": True, "status": "ok", "models": len(model_list())})

    def do_OPTIONS(self) -> None:  # noqa: N802
        send_json(self, 204, {})

    def log_message(self, *args):  # keep Vercel logs clean
        return
