"""Print the exact public shape of every resource the client reads.

Not a test: a measuring tool. Run it after changing a route, a `_shape` helper or
a screen's reader, and compare the keys the server sends against the fields the
client reads (`tests/test_client_contract.py` automates the comparison; this tells
you what the payload actually looks like when you need to see it).

    python scripts/dump_shapes.py
"""

from __future__ import annotations

import json
import os
import socket
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import base64

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from studio.app import build_app  # noqa: E402
from studio.config import load_config  # noqa: E402
from studio.http import build_server  # noqa: E402

PNG = base64.b64encode(
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
).decode("ascii")


class Client:
    def __init__(self, base: str) -> None:
        self.base = base
        self.cookies: dict = {}
        self.csrf = ""

    def call(self, method: str, path: str, body=None):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {"Accept": "application/json", "Origin": self.base}
        if data is not None:
            headers["Content-Type"] = "application/json"
        if self.cookies:
            headers["Cookie"] = "; ".join("%s=%s" % item for item in self.cookies.items())
        if self.csrf and method not in ("GET", "HEAD"):
            headers["X-CSRF-Token"] = self.csrf
        request = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
        try:
            response = urllib.request.urlopen(request, timeout=30)
            status, payload, cookies = response.status, response.read(), response.headers.get_all("Set-Cookie") or []
        except urllib.error.HTTPError as error:
            status, payload, cookies = error.code, error.read(), error.headers.get_all("Set-Cookie") or []
        for line in cookies:
            name, _, value = line.split(";")[0].partition("=")
            self.cookies[name.strip()] = value.strip()
        if self.cookies.get("studio_csrf"):
            self.csrf = self.cookies["studio_csrf"]
        try:
            return status, json.loads(payload.decode("utf-8"))
        except ValueError:
            return status, {"_body": payload[:300].decode("utf-8", "replace")}


def main() -> int:
    data_dir = tempfile.mkdtemp(prefix="studio-shapes-")
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    base = "http://127.0.0.1:%d" % port
    config = load_config(
        {
            "STUDIO_DATA_DIR": data_dir,
            "STUDIO_ENV": "development",
            "STUDIO_ENCRYPTION_KEY": "a" * 64,
            "STUDIO_AUTH_SECRET": "b" * 40,
            "STUDIO_PUBLIC_APP_URL": base,
            "STUDIO_API_BASE_URL": base,
            "STUDIO_ENABLE_MOCK_PROVIDER": "1",
            "STUDIO_RATE_LIMIT_PER_MINUTE": "100000",
            "STUDIO_JOB_WORKERS": "1",
        }
    )
    app = build_app(config)
    server = build_server(app.http, host="127.0.0.1", port=port, static_root="")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = "http://127.0.0.1:%d" % server.server_address[1]

    client = Client(base)
    status, body = client.call(
        "POST",
        "/api/auth/register",
        {"email": "shapes@example.com", "password": "correct horse battery staple", "display_name": "Shapes"},
    )
    if body.get("session", {}).get("csrf_token"):
        client.csrf = body["session"]["csrf_token"]

    status, conn = client.call(
        "POST",
        "/api/connections",
        {"name": "k", "adapter_type": "mock", "base_url": "http://127.0.0.1:9/mock", "auth_scheme": "none"},
    )
    connection_id = conn["connection"]["id"]
    status, model = client.call(
        "POST",
        "/api/connections/%s/models" % connection_id,
        {
            "model_key": "mock/placeholder-image",
            "display_name": "P",
            "modality": "image",
            "modes": ["text_to_image"],
            "capabilities": {"max_outputs": 1, "aspect_ratios": ["1:1"]},
        },
    )
    model_id = model["model"]["id"]
    status, project = client.call("POST", "/api/projects", {"name": "Proj"})
    project_id = project["project"]["id"]
    status, asset = client.call(
        "POST", "/api/assets", {"file_name": "p.png", "content_type": "image/png", "data_base64": PNG}
    )
    asset_id = asset["asset"]["id"]
    created_preset = client.call("POST", "/api/presets", {"name": "P1", "kind": "image"})
    created_prompt = client.call("POST", "/api/prompts", {"title": "T", "body": "a lighthouse"})
    created_character = client.call("POST", "/api/characters", {"name": "C1"})
    status, created = client.call(
        "POST",
        "/api/generations/images",
        {
            "model_id": model_id,
            "mode": "text_to_image",
            "prompt": "a lighthouse",
            "count": 1,
            "client_request_id": "req_shapes",
        },
    )
    job_id = created["job"]["id"]
    for _ in range(80):
        status, job = client.call("GET", "/api/jobs/%s" % job_id)
        if job["job"]["status"] in ("completed", "failed", "cancelled"):
            break
        time.sleep(0.25)

    def keys(label, payload):
        if isinstance(payload, dict):
            print("\n%-16s %s" % (label, ", ".join(sorted(payload))))
        else:
            print("\n%-16s <%s: %r>" % (label, type(payload).__name__, payload))

    me = client.call("GET", "/api/auth/me")[1]
    keys("user", me.get("user"))
    keys("workspace", me.get("workspace"))
    keys("billing", me.get("billing"))
    keys("status", client.call("GET", "/api/system/status")[1].get("status"))
    keys("connection", client.call("GET", "/api/connections/%s" % connection_id)[1].get("connection"))
    keys("model", client.call("GET", "/api/models/%s" % model_id)[1].get("model"))
    keys("capabilities", client.call("GET", "/api/models/%s/capabilities" % model_id)[1])
    keys("job", client.call("GET", "/api/jobs/%s" % job_id)[1].get("job"))
    keys("job.output[0]", (client.call("GET", "/api/jobs/%s" % job_id)[1]["job"]["outputs"] or [{}])[0])
    keys("job.event[0]", (client.call("GET", "/api/jobs/%s/events" % job_id)[1].get("events") or [{}])[0])
    keys("asset", client.call("GET", "/api/assets/%s" % asset_id)[1].get("asset"))
    keys("project", client.call("GET", "/api/projects/%s" % project_id)[1].get("project"))
    keys("preset.builtin", (client.call("GET", "/api/presets?modality=image")[1].get("presets") or [{}])[0])
    keys("preset.saved", created_preset[1].get("preset"))
    keys("prompt.list", (client.call("GET", "/api/prompts")[1].get("prompts") or [{}])[0])
    keys("prompt.created", created_prompt[1].get("prompt"))
    keys("character.list", (client.call("GET", "/api/characters")[1].get("characters") or [{}])[0])
    keys("character.created", created_character[1].get("character"))
    keys("usage.row", (client.call("GET", "/api/usage")[1].get("usage") or [{}])[0])
    keys("adapters[0]", (client.call("GET", "/api/system/status")[1]["status"]["adapters"] or [{}])[0])
    print()
    print("create results:", created_preset[0], created_prompt[0], created_character[0])
    if created_preset[0] >= 400:
        print("  preset:", created_preset[1])
    if created_prompt[0] >= 400:
        print("  prompt:", created_prompt[1])
    if created_character[0] >= 400:
        print("  character:", created_character[1])

    server.shutdown()
    server.server_close()
    app.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
