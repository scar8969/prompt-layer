"""The Director's Panel and the style cards, over real HTTP and in the pure.

Two things are worth pinning here, and they are different kinds of claim:

* **The compiler is a function, not a guess.** The same brief always produces
  the same prompt; every clause names the control that produced it; a control
  left alone contributes nothing (it is never filled with a default); and a
  choice outside the vocabulary is refused by name rather than silently
  dropped, because a dropped clause would leave a ledger that looks complete.
* **The style card is art, not a preview.** It is deterministic, it is derived
  from the preset's own words, and the route says so in a header. Nothing in
  the card path calls a provider, and nothing pretends to be a render.

Run:  python -m pytest tests/test_cinema.py -q
"""

from __future__ import annotations

import json
import os
import socket
import sys
import threading
import urllib.error
import urllib.request

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from studio import cinema  # noqa: E402
from studio.app import build_app  # noqa: E402
from studio.config import load_config  # noqa: E402
from studio.http import build_server  # noqa: E402
from studio.preset_cards import card_svg  # noqa: E402
from studio.presets_data import PRESETS, PRESETS_BY_KEY  # noqa: E402

PASSWORD = "correct horse battery staple"


# ---------------------------------------------------------------------------
# The compiler, as a pure function
# ---------------------------------------------------------------------------


FULL_BRIEF = {
    "subject": "a woman in a wet overcoat",
    "setting": "on a neon-lit street at 2 am",
    "shots": "medium_close",
    "angles": "eye",
    "compositions": "thirds",
    "bodies": "arri_alexa_35",
    "lenses": "panavision_c",
    "focal_lengths": "50",
    "apertures": "1.8",
    "shutters": "180",
    "lighting": "neon_practicals",
    "palettes": "teal_orange",
    "grain": "fine",
    "moods": "longing",
    "movement": "dolly_in",
    "aspects": "2.39:1",
}


def test_the_same_brief_compiles_to_the_same_prompt():
    first = cinema.compile_brief(dict(FULL_BRIEF, modality="video"))
    second = cinema.compile_brief(dict(FULL_BRIEF, modality="video"))
    assert first == second
    assert first["prompt"].startswith("A woman in a wet overcoat")
    assert first["prompt"].endswith(".")


def test_every_clause_names_the_control_that_produced_it():
    result = cinema.compile_brief(dict(FULL_BRIEF, modality="video"))
    keys = [segment["key"] for segment in result["segments"]]
    assert keys == [
        "subject",
        "setting",
        "framing",
        "camera",
        "exposure",
        "light",
        "colour",
        "mood",
        "motion",
        "finish",
    ]
    for segment in result["segments"]:
        assert segment["text"]
        assert segment["enabled"] is True
        # Typed controls attribute their clause to the control group; the two
        # free-text controls say where the words came from instead.
        assert segment["source"] == ("you" if segment["key"] in ("subject", "setting") else segment["key"])
        # The clause is in the prompt verbatim — no paraphrase anywhere. The
        # first clause is capitalised as the sentence opens, which is the only
        # transformation the compiler makes.
        assert segment["text"] in result["prompt"] or segment["text"].capitalize() in result["prompt"]


def test_an_empty_control_contributes_nothing_and_is_reported():
    result = cinema.compile_brief({"subject": "a lighthouse", "modality": "image"})
    assert result["prompt"] == "A lighthouse."
    assert "Framing" in result["omitted"]
    assert "Light" in result["omitted"]
    # The panel does not invent a lens, a grade or a performance.
    for invented in ("shot on", "graded", "at f/"):
        assert invented not in result["prompt"]


def test_no_subject_is_never_invented():
    result = cinema.compile_brief({"lighting": "rembrandt", "modality": "image"})
    assert result["prompt"].startswith("A single Rembrandt key")
    levels = [warning["level"] for warning in result["warnings"]]
    assert "missing" in levels


def test_a_disabled_segment_keeps_its_text_but_leaves_the_prompt():
    result = cinema.compile_brief(dict(FULL_BRIEF, modality="video", disabled=["colour", "finish"]))
    assert "teal shadows" not in result["prompt"]
    assert "grain" not in result["prompt"].lower()
    off = {segment["key"]: segment for segment in result["segments"] if not segment["enabled"]}
    assert set(off) == {"colour", "finish"}
    # The ledger can still show what was removed; that is the point of it.
    assert "teal shadows" in off["colour"]["text"]


def test_movement_is_video_only():
    still = cinema.compile_brief(dict(FULL_BRIEF, modality="image"))
    moving = cinema.compile_brief(dict(FULL_BRIEF, modality="video"))
    assert "dolly" not in still["prompt"].lower()
    assert "dolly" in moving["prompt"].lower()
    assert still["parameters"].get("camera_move") is None
    assert moving["parameters"]["camera_move"] == "dolly_in"


def test_parameters_carry_the_aspect_and_the_aperture_as_a_label():
    result = cinema.compile_brief(dict(FULL_BRIEF, modality="image"))
    assert result["parameters"]["aspect_ratio"] == "2.39:1"
    assert result["parameters"]["aperture"] == "f/1.8"


def test_the_vocabulary_is_complete_and_every_option_adds_words():
    document = cinema.vocabulary_document()
    assert len(document["groups"]) == 10
    assert document["option_count"] > 100
    for group in document["groups"]:
        assert group["controls"], group["key"]
        for control in group["controls"]:
            if control["kind"] == "text":
                assert control["options"] == []
                continue
            assert control["options"], control["name"]
            for option in control["options"]:
                assert option["key"] and option["label"]
                # "None" is the deliberate exception: it adds nothing.
                if option["key"] != "none":
                    assert option["adds"], (control["name"], option["key"])


def test_two_controls_in_the_same_group_compose_as_one_clause():
    result = cinema.compile_brief(
        {"subject": "a bottle", "bodies": "arri_alexa_35", "lenses": "cooke_s4", "modality": "image"}
    )
    camera = next(segment for segment in result["segments"] if segment["key"] == "camera")
    assert camera["text"] == "shot on an ARRI Alexa 35, Cooke S4 primes"


def test_a_hard_combination_earns_a_craft_note_not_a_refusal():
    result = cinema.compile_brief(
        {"subject": "a face", "focal_lengths": "200", "apertures": "1.2", "modality": "image"}
    )
    assert result["prompt"]
    assert any("field is a few centimetres deep" in warning["text"] for warning in result["warnings"])


# ---------------------------------------------------------------------------
# The style cards
# ---------------------------------------------------------------------------


def test_every_catalogue_preset_gets_a_deterministic_card():
    for entry in PRESETS:
        first = card_svg(entry)
        second = card_svg(entry)
        assert first == second
        assert first.startswith("<svg")
        assert first.rstrip().endswith("</svg>")
        assert len(first) > 1200
        assert "Style card generated from this preset's tokens" in first


def test_cards_differ_between_presets():
    rendered = {entry["key"]: card_svg(entry) for entry in PRESETS[:30]}
    assert len(set(rendered.values())) == len(rendered)


def test_a_card_reads_its_palette_from_the_presets_own_words():
    from studio.preset_cards import _spec

    neon = _spec({"key": "x", "prompt_template": "neon signage, magenta spill", "category": "urban"})
    golden = _spec({"key": "y", "prompt_template": "low golden-hour sun", "category": "urban"})
    assert neon["hue"] != golden["hue"]
    assert neon["hue"] == pytest.approx(305, abs=15)


def test_the_card_is_not_a_render():
    """No provider URL, no model call, no photograph: the label says so."""
    svg = card_svg(PRESETS[0])
    # The only URL allowed is the SVG namespace itself.
    assert svg.count("http") == 1
    assert "http://www.w3.org/2000/svg" in svg
    assert "<image" not in svg
    assert "href=" not in svg


# ---------------------------------------------------------------------------
# Over real HTTP
# ---------------------------------------------------------------------------


class Client:
    """A browser-shaped client: cookies, a CSRF header, and real HTTP."""

    def __init__(self, base: str) -> None:
        self.base = base
        self.cookies: dict = {}
        self.csrf = ""

    def call(self, method: str, path: str, body=None, *, raw: bool = False):
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
            content_type = response.headers.get("Content-Type", "")
        except urllib.error.HTTPError as error:
            status, payload, cookies = error.code, error.read(), error.headers.get_all("Set-Cookie") or []
            content_type = error.headers.get("Content-Type", "")
        for line in cookies:
            name, _, value = line.split(";")[0].partition("=")
            self.cookies[name.strip()] = value.strip()
        if self.cookies.get("studio_csrf"):
            self.csrf = self.cookies["studio_csrf"]
        if raw:
            return status, payload, content_type
        try:
            return status, json.loads(payload.decode("utf-8"))
        except ValueError:
            return status, {"_body": payload[:200].decode("utf-8", "replace")}

    def register(self) -> None:
        status, payload = self.call(
            "POST",
            "/api/auth/register",
            {
                "email": "panel-%s@example.com" % os.urandom(6).hex(),
                "password": PASSWORD,
                "display_name": "Ada",
            },
        )
        assert status == 201, payload
        if payload.get("session", {}).get("csrf_token"):
            self.csrf = payload["session"]["csrf_token"]


@pytest.fixture(scope="module")
def studio(tmp_path_factory, studio_database):
    data_dir = str(tmp_path_factory.mktemp("cinema"))
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
            "STUDIO_RATE_LIMIT_PER_MINUTE": "100000",
            "STUDIO_JOB_WORKERS": "0",
            "STUDIO_DATABASE_URL": studio_database,
        }
    )
    app = build_app(config)
    server = build_server(app.http, host="127.0.0.1", port=port, static_root="")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield type("Studio", (), {"app": app, "base": base})
    server.shutdown()
    server.server_close()
    app.shutdown()


@pytest.fixture()
def client(studio):
    client = Client(studio.base)
    client.register()
    return client


def test_the_panel_routes_need_a_session(studio):
    anonymous = Client(studio.base)
    for method, path in (
        ("GET", "/api/cinema/vocabulary"),
        ("POST", "/api/cinema/compile"),
        ("GET", "/api/preset-cards/image.portraiture.soft-window-light.svg"),
    ):
        status, body = anonymous.call(method, path, {} if method == "POST" else None)
        assert status == 401, (path, status, body)
        assert body["error"]["code"] == "unauthenticated"


def test_the_vocabulary_route_serves_the_panel(client):
    status, body = client.call("GET", "/api/cinema/vocabulary")
    assert status == 200, body
    vocabulary = body["vocabulary"]
    assert [group["key"] for group in vocabulary["groups"]][0] == "subject"
    assert vocabulary["option_count"] == cinema.vocabulary_document()["option_count"]
    assert body["source"].startswith("studio/cinema.py")


def test_the_compile_route_returns_a_ledger(client):
    status, body = client.call(
        "POST",
        "/api/cinema/compile",
        {"subject": "a lighthouse in fog", "lighting": "moonlight", "modality": "image"},
    )
    assert status == 200, body
    compiled = body["compiled"]
    assert compiled["prompt"] == "A lighthouse in fog and cold blue moonlight from a high angle, deep shadow with no fill."
    assert [segment["key"] for segment in compiled["segments"]] == ["subject", "light"]


def test_an_unknown_option_is_refused_by_name(client):
    status, body = client.call(
        "POST",
        "/api/cinema/compile",
        {"subject": "a room", "lighting": "not_a_light", "modality": "image"},
    )
    assert status == 400, body
    assert body["error"]["code"] == "validation_failed"
    assert body["error"]["field"] == "lighting"


def test_a_bad_modality_is_refused(client):
    status, body = client.call("POST", "/api/cinema/compile", {"subject": "x", "modality": "audio"})
    assert status == 400
    assert body["error"]["field"] == "modality"


def test_the_card_route_serves_svg_and_says_what_it_is(client):
    status, payload, content_type = client.call(
        "GET", "/api/preset-cards/image.portraiture.soft-window-light.svg", raw=True
    )
    assert status == 200
    assert content_type.startswith("image/svg+xml")
    assert payload.startswith(b"<svg")
    assert payload == card_svg(PRESETS_BY_KEY["image.portraiture.soft-window-light"]).encode("utf-8")


def test_an_unknown_card_is_a_404(client):
    status, body = client.call("GET", "/api/preset-cards/not.a.preset.svg")
    assert status == 404
    assert body["error"]["code"] == "not_found"


def test_the_preset_list_carries_a_card_for_every_built_in(client):
    status, body = client.call("GET", "/api/presets?modality=image")
    assert status == 200, body
    builtin = [preset for preset in body["presets"] if preset.get("is_builtin")]
    assert builtin
    for preset in builtin:
        assert preset["thumbnail_url"] == "/api/preset-cards/%s.svg" % preset["slug"]
        assert preset["thumbnail_kind"] == "style_card"
