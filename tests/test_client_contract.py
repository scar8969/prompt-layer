"""Every field the client reads must exist in the payload the server sends.

Why this test exists
--------------------
`tests/test_contract.py` proves the client calls routes that exist. It cannot see
the failure that actually shipped three times in one day: the client read
`job.state` from a payload that carries `job.status`, read `preset.id` from a
catalogue entry that has `preset.key`, and read `negative_prompt_template` from a
dataset that says `negative_template`.

Each of those was invisible to every existing test, because the server was right
in each case. The client was reading a name nobody sent, and a missing field in
JavaScript is not an error — it is `undefined`, which renders as a blank, a zero
or an empty list.

So this test reads the reader functions in `web/src/api/endpoints.ts`, extracts
every top-level field they touch on the raw payload, and compares that set against
what a live server actually returns for the same resource. A field the client
reads and the server never sends is a failure, with the field named.

What it cannot see: a field that exists with the wrong *type*, or a nested object
read through a different variable. Both are named in the failure message when they
happen, rather than guessed at here.

Run:  python -m pytest tests/test_client_contract.py -q
"""

from __future__ import annotations

import base64
import json
import os
import re
import socket
import sys
import threading
import time
import urllib.error
import urllib.request

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from studio.app import build_app  # noqa: E402
from studio.config import load_config  # noqa: E402
from studio.http import build_server  # noqa: E402

ENDPOINTS = os.path.join(REPO, "web", "src", "api", "endpoints.ts")
PASSWORD = "correct horse battery staple"

PNG = base64.b64encode(
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
).decode("ascii")


def reader_fields() -> dict:
    """Every top-level field each `readX(raw)` function touches on `raw`.

    Nested blocks use their own variable names (`modelRaw`, `providerRaw`), so a
    top-level scan is exactly the set of names the payload must actually carry.
    """
    with open(ENDPOINTS, encoding="utf-8") as handle:
        source = handle.read()

    starts = [
        (match.start(), match.group(1))
        for match in re.finditer(r"^(?:export )?function (read[A-Za-z]+)\(raw: unknown\)", source, re.M)
    ]
    fields: dict = {}
    for index, (start, name) in enumerate(starts):
        # A top-level function ends at the first closing brace in column 0. Using
        # the next reader as the boundary over-runs: not every neighbour has this
        # signature, and the fields after it belong to somebody else.
        closing = re.search(r"^\}$", source[start:], re.M)
        end = start + closing.end() if closing else len(source)
        body = source[start:end]
        # Comments describe the shape; they are not reads.
        body = "\n".join(
            line for line in body.splitlines() if not line.strip().startswith(("//", "*", "/*"))
        )
        # Readers narrow the payload into a local first (`const r = isRecord(raw)`),
        # then read `r.field`; a few read `raw.field` directly. Both are top-level
        # reads of this payload. A nested object gets its own alias and is skipped
        # on purpose: its keys belong to a sub-reader, not to this one.
        alias = re.search(r"const (\w+) = isRecord\(raw\)", body)
        target = alias.group(1) if alias else "raw"
        touched = set(re.findall(r"\b%s\.([a-zA-Z_][a-zA-Z0-9_]*)" % re.escape(target), body))
        fields[name] = touched
    return fields


class Client:
    def __init__(self, base: str) -> None:
        self.base = base
        self.cookies: dict = {}
        self.csrf = ""

    def call(self, method: str, path: str, body=None, raw: bool = False):
        data = None
        headers = {"Accept": "application/json", "Origin": self.base}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
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
        if raw:
            return status, payload
        try:
            return status, json.loads(payload.decode("utf-8"))
        except ValueError:
            return status, {"_body": payload[:200].decode("utf-8", "replace")}


@pytest.fixture(scope="module")
def studio(tmp_path_factory, studio_database):
    data_dir = str(tmp_path_factory.mktemp("client-contract"))
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
            "STUDIO_DATABASE_URL": studio_database,
        }
    )
    app = build_app(config)
    server = build_server(app.http, host="127.0.0.1", port=port, static_root="")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield type("Studio", (), {"app": app, "base": "http://127.0.0.1:%d" % server.server_address[1]})
    server.shutdown()
    server.server_close()
    app.shutdown()


@pytest.fixture(scope="module")
def samples(studio):
    """One real payload per resource the client reads."""
    client = Client(studio.base)
    status, body = client.call(
        "POST",
        "/api/auth/register",
        {"email": "contract@example.com", "password": PASSWORD, "display_name": "Ada"},
    )
    assert status == 201, body
    if body.get("session", {}).get("csrf_token"):
        client.csrf = body["session"]["csrf_token"]
    me = body
    client_id = client.csrf

    status, connection = client.call(
        "POST",
        "/api/connections",
        {
            "name": "contract key",
            "adapter_type": "mock",
            "base_url": "http://127.0.0.1:9/mock",
            "auth_scheme": "none",
        },
    )
    assert status == 201, connection
    connection_id = connection["connection"]["id"]

    status, model = client.call(
        "POST",
        "/api/connections/%s/models" % connection_id,
        {
            "model_key": "mock/placeholder-image",
            "display_name": "Placeholder image",
            "modality": "image",
            "modes": ["text_to_image"],
            "capabilities": {"max_outputs": 2, "aspect_ratios": ["1:1"], "supports_seed": True},
        },
    )
    assert status == 201, model
    model_id = model["model"]["id"]

    status, project = client.call("POST", "/api/projects", {"name": "Contract project"})
    assert status == 201, project
    project_id = project["project"]["id"]

    status, asset = client.call(
        "POST",
        "/api/assets",
        {"file_name": "pixel.png", "content_type": "image/png", "data_base64": PNG},
    )
    assert status == 201, asset
    asset_id = asset["asset"]["id"]

    status, preset = client.call(
        "POST", "/api/presets", {"name": "Contract preset", "kind": "image", "prompt_template": "x"}
    )
    assert status == 201, preset
    status, prompt = client.call("POST", "/api/prompts", {"title": "Lighthouse", "body": "a lighthouse"})
    assert status == 201, prompt
    status, character = client.call("POST", "/api/characters", {"name": "Contract character"})
    assert status == 201, character

    status, created = client.call(
        "POST",
        "/api/generations/images",
        {
            "model_id": model_id,
            "mode": "text_to_image",
            "prompt": "a lighthouse",
            "count": 1,
            "client_request_id": "req_contract",
        },
    )
    assert status in (200, 201, 202), created
    job_id = created["job"]["id"]
    for _ in range(80):
        status, job = client.call("GET", "/api/jobs/%s" % job_id)
        if job["job"]["status"] in ("completed", "failed", "cancelled"):
            break
        time.sleep(0.25)

    resolved = {}
    # A deployment may report no billing block at all; that is absence, not drift.
    payloads = {
        "readUser": client.call("GET", "/api/auth/me")[1].get("user"),
        "readWorkspace": client.call("GET", "/api/auth/me")[1].get("workspace"),
        "readBilling": client.call("GET", "/api/auth/me")[1].get("billing"),
        "readSystemStatus": client.call("GET", "/api/system/status")[1].get("status"),
        "readConnection": client.call("GET", "/api/connections/%s" % connection_id)[1].get("connection"),
        "readModel": client.call("GET", "/api/models/%s" % model_id)[1].get("model"),
        "readCapabilities": client.call("GET", "/api/models/%s/capabilities" % model_id)[1].get("capabilities"),
        "readJob": client.call("GET", "/api/jobs/%s" % job_id)[1].get("job"),
        "readAsset": client.call("GET", "/api/assets/%s" % asset_id)[1].get("asset"),
        "readProject": client.call("GET", "/api/projects/%s" % project_id)[1].get("project"),
        "readPreset": (client.call("GET", "/api/presets?modality=image")[1].get("presets") or [{}])[0],
        "readPrompt": (client.call("GET", "/api/prompts")[1].get("prompts") or [{}])[0],
        # The list projection carries `reference_count`; the detail carries the
        # reference rows. The screen reads both, so the richer payload is sampled.
        "readCharacter": client.call("GET", "/api/characters/%s" % character["character"]["id"])[1].get("character"),
    }
    resolved.update(payloads)
    resolved["_session"] = me and client_id
    return resolved


OPTIONAL = {"readBilling"}


def test_every_reader_finds_a_sample(samples):
    """A reader without a sample would pass this suite by being compared to nothing."""
    incomplete = [
        name
        for name, payload in samples.items()
        if not name.startswith("_") and not payload and name not in OPTIONAL
    ]
    assert incomplete == [], "no payload was produced for: %r" % (incomplete,)


def test_no_reader_reads_a_field_the_server_never_sends(samples):
    readers = reader_fields()
    problems = []
    for name, payload in samples.items():
        if name.startswith("_") or not payload:
            # No payload means no comparison: either the reader is optional (see
            # OPTIONAL) or the fixture failed, which the first test reports.
            continue
        fields = readers.get(name)
        assert fields, "reader %s is not parsed from endpoints.ts" % name
        sent = set(payload.keys())
        missing = sorted(fields - sent)
        if missing:
            problems.append(
                "%s reads %r but the payload carries %r"
                % (name, missing, sorted(sent))
            )
    assert problems == [], "\n".join(problems)


# --------------------------------------------------------------------------
# The other direction: what the client *sends*
# --------------------------------------------------------------------------

REPOSITORY = os.path.join(REPO, "studio", "repository.py")

#: The three library writes, and the tuple in `studio/repository.py` that lists
#: the payload keys each one accepts.
WRITE_SPECS = {
    "presetBody": "_PRESET_FIELDS",
    "promptBody": "_PROMPT_FIELDS",
    "characterBody": "_CHARACTER_FIELDS",
}


def accepted_fields(variable: str) -> set:
    """The keys a repository create/patch accepts, read from its whitelist."""
    with open(REPOSITORY, encoding="utf-8") as handle:
        text = handle.read()
    match = re.search(r"%s: Tuple\[str, \.\.\.\] = \((.*?)\)" % variable, text, re.S)
    assert match, "no %s whitelist found in studio/repository.py" % variable
    return set(re.findall(r'"([a-z_]+)"', match.group(1)))


def body_keys(function: str) -> set:
    """The keys a client body builder assigns."""
    with open(ENDPOINTS, encoding="utf-8") as handle:
        text = handle.read()
    start = text.find("function %s(" % function)
    assert start >= 0, "no %s in endpoints.ts" % function
    closing = re.search(r"^\}$", text[start:], re.M)
    body = text[start : start + closing.end()] if closing else text[start:]
    return set(re.findall(r"body\.([a-zA-Z_]+)\s*=", body))


def test_every_key_the_client_sends_is_a_key_the_server_accepts():
    """An unknown key is refused by name, so a wrong one is a 400, not a no-op.

    This is the write-side twin of the read check above: the preset form sent
    `modality` to a route that accepts `kind`, and every save from the library
    failed with "unknown field".
    """
    problems = []
    for function, whitelist in WRITE_SPECS.items():
        sent = body_keys(function)
        assert sent, "%s does not assign any body keys" % function
        unknown = sorted(sent - accepted_fields(whitelist))
        if unknown:
            problems.append("%s sends %r, which %s does not accept" % (function, unknown, whitelist))
    assert problems == [], "\n".join(problems)


def test_the_write_surface_is_a_deliberate_subset_of_what_the_route_accepts():
    """Not every accepted key belongs in a form, and the exceptions are named.

    `slug` is derived from the name by the repository, `supported_modes` and
    `required_references` default to empty, and a character's
    `parent_character_id` is a variant relationship no form sets yet. Anything
    outside this list is either a missing control or a stale whitelist, and this
    test is what makes somebody say which.
    """
    documented_exclusions = {
        "presetBody": {"slug", "supported_modes", "required_references"},
        "promptBody": {"modality"},
        "characterBody": {"parent_character_id"},
    }
    problems = []
    for function, whitelist in WRITE_SPECS.items():
        accepted = accepted_fields(whitelist)
        sent = body_keys(function)
        unreachable = sorted(accepted - sent - documented_exclusions.get(function, set()))
        if unreachable:
            problems.append("%s cannot set %r, which %s accepts" % (function, unreachable, whitelist))
    assert problems == [], "\n".join(problems)


def test_the_readers_cover_the_payloads_the_screens_use(samples):
    """A reader that ignores most of a payload is not automatically a bug.

    This is reported rather than failed: a narrow reader is a deliberate choice
    (`readJob` ignores `request`, which is a large internal document), while a
    missing one is not. It exists so the counts are visible in a test run.
    """
    readers = reader_fields()
    for name, payload in sorted(samples.items()):
        if name.startswith("_") or not payload:
            continue
        fields = readers.get(name) or set()
        ignored = sorted(set(payload.keys()) - fields)
        print("%-18s reads %2d of %2d fields; ignores %r" % (name, len(fields), len(payload), ignored))
