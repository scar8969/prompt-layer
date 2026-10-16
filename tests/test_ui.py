"""The rules the "run it against a model you name" box has to keep.

The box takes an API key, so the guarantees around it are the point of the feature,
not a detail of it. These are asserted against the page source because that is where
they live: this is the one part of the tool whose whole design is that the server is
not involved.
"""

import os
import re

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAGE = os.path.join(REPO, "index.html")

# The block that talks to the endpoint the user names. Everything below is about
# this slice of the page, not about the page as a whole — the rest of the file
# legitimately stores model sets in localStorage.
_START = "// ------------------------------------------------- running it for real"
_END = "\n  function attr(value) {"


def _page():
    with open(PAGE, encoding="utf-8") as fh:
        return fh.read()


def _run_section():
    html = _page()
    start = html.index(_START)
    end = html.index(_END, start)
    return html[start:end]


def _tag(element_id):
    match = re.search(r"<input[^>]*id=\"%s\"[^>]*>" % element_id, _page())
    assert match, f"no input with id {element_id}"
    return match.group(0)


def test_the_key_field_is_a_password_field_the_browser_is_told_not_to_remember():
    """A plain text field would be autofilled and offered to save; neither is wanted."""
    tag = _tag("rKey")
    assert 'type="password"' in tag, tag
    assert 'autocomplete="off"' in tag, tag


def _code_only(text):
    """Strip comments, so a rule can be documented in prose next to the code.

    Without this the assertion matches the sentence that explains the rule — which is
    how a check starts failing for being well documented. `//` after a colon is left
    alone so a URL in a string is not mistaken for a comment.
    """
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"(?m)(?<!:)//.*$", "", text)


def test_the_key_is_never_written_to_storage():
    """The run section must not touch localStorage at all.

    Model sets are stored (they are a preference), which is exactly why this has to
    be asserted per-section: a later edit could reach for the same habit here and
    quietly persist a credential.
    """
    code = _code_only(_run_section())
    assert "localStorage" not in code, "the run box must not persist anything"
    assert "sessionStorage" not in code


def test_the_key_never_goes_to_this_tools_own_server():
    """The request goes to the URL in the field, not to an /api route of ours.

    This is the reason there is no endpoint behind this feature. A route here would
    mean the key passing through this server, and a public deployment would then be
    relaying credentials for people it cannot authenticate.
    """
    section = _run_section()
    assert "fetch(url" in section, "the request must go to the URL the user typed"
    assert "${API}" not in section, "the run box must not call this tool's API"
    assert "/api/" not in section, "the run box must not call this tool's API"


def test_no_api_route_takes_a_key_from_the_request():
    """No function of ours accepts a credential from a caller.

    `api/compile.py` does send an `Authorization` header — that is the operator's own
    key, read from the environment, on an *outbound* request. That is the documented
    gate and a different thing from a route that takes a key out of the request it was
    handed, which is what this forbids. The server side of the same promise the page
    makes, and it would catch someone adding a convenient proxy later.
    """
    api_dir = os.path.join(REPO, "api")
    for name in sorted(os.listdir(api_dir)):
        if not name.endswith(".py"):
            continue
        with open(os.path.join(api_dir, name), encoding="utf-8") as fh:
            source = fh.read()
        for forbidden in ('headers.get("Authorization")', "headers.get('Authorization')"):
            assert forbidden not in source, f"api/{name} reads a key off the request"
        for field in ("api_key", "apiKey", "key", "token"):
            for shape in (f'body.get("{field}")', f"body.get('{field}')",
                          f'body["{field}"]', f"body['{field}']"):
                assert shape not in source, f"api/{name} takes {shape} from the request"


def test_the_box_says_where_the_request_goes():
    """The claim has to be on screen, not only in the README.

    Someone pasting a key needs to be able to read, at the field, that it is used
    for one request and not stored — otherwise the honest design is invisible.
    """
    html = _page()
    assert "not stored" in html
    assert "goes to whatever is in that field" in html


def test_the_endpoint_field_ships_prefilled_and_complete():
    """The shipped default is a working endpoint, not an empty box.

    Hardcoded on request, to a gateway that is OpenAI-compatible and allows calls from
    a page. Both were checked against the live gateway before it was hardcoded: a
    request from this page reached it and came back 401 for the missing key, rather
    than being blocked by the browser or 404ing on the path.

    The URL is asserted whole, including `/v1/chat/completions`, because the box posts
    to exactly what is in the field: a base URL alone would send the request to
    `/` and the gateway would answer 404 for the path.
    """
    tag = _tag("rUrl")
    assert 'value="https://asiasouth.up.railway.app/v1/chat/completions"' in tag, tag
    # the placeholder stays a generic example, distinct from the shipped value
    assert 'placeholder="https://api.openai.com/v1/chat/completions"' in tag, tag


def test_the_default_endpoint_is_named_on_screen():
    """The default URL is visible next to the box, so it is not a hidden setting."""
    html = _page()
    assert "asiasouth.up.railway.app" in html
    assert "filled in for you" in html


def test_a_body_that_is_not_json_is_refused_before_anything_is_sent():
    """Sending a malformed body would be an invented request; refuse instead."""
    section = _run_section()
    assert "JSON.parse($('rBody').value)" in section
    assert "Nothing was sent" in section
