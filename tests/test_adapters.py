"""Contract tests for the adapter layer: schemas, capabilities, camera moves,
the mapping interpreter, the declarative security boundary, and the registry.

Every assertion here was written from observed behaviour, not from what the
docstrings claim. Where the two disagree the test asserts what the code actually
does and the docstring says so, because a test that encodes a wish instead of a
fact is worse than no test at all.

Nothing here touches the network, binds a port, or reads a fixture file: the
whole adapter layer is pure data handling, and this file proves that by importing
the modules directly and calling them with literal inputs.
"""

from __future__ import annotations

import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from studio.adapters import core, declarative  # noqa: E402
from studio.adapters.base import (  # noqa: E402
    CAMERA_MOVES,
    CAMERA_MOVES_BY_KEY,
    IMAGE_MODES,
    MAX_ENUM_OPTIONS,
    MAX_PARAMETERS_PER_MODEL,
    MODE_LABELS,
    MODES_BY_MODALITY,
    PARAMETER_TYPES,
    VIDEO_MODES,
    CameraMove,
    ParameterSchema,
    ParameterSpec,
    camera_move,
    modes_for,
    parse_parameter_schema,
)
from studio.adapters.core import adapter_types, get_adapter, known_adapters  # noqa: E402
from studio.adapters.mapping import (  # noqa: E402
    as_list,
    collect_urls,
    drop_empty,
    first_present,
    get_path,
    join_url,
    percent_to_fraction,
    render_template,
    to_int,
    unique,
)
from studio.adapters.types import CapabilityProfile  # noqa: E402
from studio.errors import ApiError, CAPABILITY_UNSUPPORTED, VALIDATION_FAILED  # noqa: E402


def refused(call, *args, **kwargs) -> ApiError:
    """Run something that must be refused, and hand back the ApiError."""
    with pytest.raises(ApiError) as caught:
        call(*args, **kwargs)
    return caught.value


#: A minimal mapping that load_spec accepts today. Several tests below start
#: from this and perturb exactly one key, so a failure names the one rule that
#: broke rather than the whole document.
VALID_SYNC_SPEC = {
    "structure": "sync",
    "submit": {"path": "/generate", "body_type": "url"},
    "response": {"type": "json", "outputs_path": "data"},
}


def spec_with(**submit_keys):
    """A valid synchronous mapping with individual submit keys overridden."""
    spec = {
        "structure": VALID_SYNC_SPEC["structure"],
        "submit": dict(VALID_SYNC_SPEC["submit"]),
        "response": dict(VALID_SYNC_SPEC["response"]),
    }
    spec["submit"].update(submit_keys)
    return spec


# ---------------------------------------------------------------------------
# 1. Parameter schemas -- base.parse_parameter_schema / ParameterSchema
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kind,extra",
    [
        ("string", {}),
        ("number", {"minimum": 0, "maximum": 10}),
        ("integer", {"minimum": 1, "maximum": 8}),
        ("boolean", {}),
        ("enum", {"options": ["a", "b"]}),
        ("array", {"item_type": "string"}),
        ("object", {}),
    ],
)
def test_parse_accepts_every_type_in_the_closed_list(kind, extra):
    """A schema builder offers exactly these controls, so each must parse.

    If any documented type failed here the wizard would present a control the
    server then rejected on save, which reads to a user as a broken form.
    """
    schema = parse_parameter_schema([dict({"name": "field", "type": kind}, **extra)])
    assert schema.names() == ("field",)
    assert schema.get("field").type == kind


def test_parse_refuses_json_as_a_type_name():
    """'json' is not in PARAMETER_TYPES; 'object' is the structured value.

    The brief lists "json" among the documented types, but the closed list is
    (string, number, integer, boolean, enum, array, object), and a schema
    declaring "json" is refused with validation_failed. The test asserts the
    refusal so nobody adds a "json" control the parser will not store.
    """
    assert "json" not in PARAMETER_TYPES
    error = refused(parse_parameter_schema, [{"name": "p", "type": "json"}])
    assert error.code == VALIDATION_FAILED
    assert error.field == "p"
    assert "object" in error.details["allowed_types"]


def test_parse_refuses_an_unknown_type():
    """A type outside the list could mean anything at execution time.

    The list is closed so a stored schema stays data: an invented type name is
    the first step towards something that would need an interpreter to honour.
    """
    error = refused(parse_parameter_schema, [{"name": "p", "type": "sql"}])
    assert error.code == VALIDATION_FAILED
    assert error.details["allowed_types"] == list(PARAMETER_TYPES)


@pytest.mark.parametrize("bad_name", ["1st", "9", "_leading", "has space", "", "a" * 65])
def test_parse_refuses_a_name_that_starts_with_a_digit(bad_name):
    """Names become JSON field names and object keys on both sides of the wire.

    Starting with a letter keeps every declared name addressable as a plain
    attribute or key, which is what the studio builder relies on.
    """
    error = refused(parse_parameter_schema, [{"name": bad_name, "type": "string"}])
    assert error.code == VALIDATION_FAILED


def test_parse_refuses_a_duplicate_parameter_name():
    """Two controls with one name would silently overwrite each other.

    The second definition would win in the value map while the first stayed
    rendered, so the user would set a control that never travels.
    """
    error = refused(
        parse_parameter_schema,
        [{"name": "steps", "type": "integer"}, {"name": "steps", "type": "number"}],
    )
    assert error.code == VALIDATION_FAILED
    assert error.field == "steps"


def test_parameter_schema_constructor_refuses_duplicate_specs():
    """The same rule holds for schemas built in Python, not just parsed ones.

    A hand-built schema reaching the same bad state would be the same bug with
    a harder-to-find cause.
    """
    error = refused(
        ParameterSchema, [ParameterSpec("x", "string"), ParameterSpec("x", "string")]
    )
    assert error.code == VALIDATION_FAILED


def test_parse_refuses_an_enum_with_no_options():
    """A choice control with nothing to choose is a button that cannot work."""
    error = refused(parse_parameter_schema, [{"name": "p", "type": "enum", "options": []}])
    assert error.code == VALIDATION_FAILED
    assert "no options" in error.message


def test_parse_refuses_more_enum_options_than_the_limit():
    """An unbounded option list is an unbounded payload from an untrusted form."""
    too_many = ["opt-%d" % i for i in range(MAX_ENUM_OPTIONS + 1)]
    error = refused(
        parse_parameter_schema, [{"name": "p", "type": "enum", "options": too_many}]
    )
    assert error.code == VALIDATION_FAILED
    assert str(MAX_ENUM_OPTIONS) in error.message
    accepted = parse_parameter_schema(
        [{"name": "p", "type": "enum", "options": too_many[:MAX_ENUM_OPTIONS]}]
    )
    assert len(accepted.get("p").options) == MAX_ENUM_OPTIONS


@pytest.mark.parametrize("payload", ["hello", 42, {"parameters": 5}, True])
def test_parse_refuses_a_non_list_payload(payload):
    """The parser runs on values that arrived over HTTP, so it assumes nothing.

    A scalar or an object where a list belongs must fail at the boundary rather
    than be iterated into nonsense.
    """
    error = refused(parse_parameter_schema, payload)
    assert error.code == VALIDATION_FAILED
    assert "list of parameters" in error.message


def test_parse_refuses_more_parameters_than_the_limit():
    """64 controls is the ceiling a generated form can render honestly."""
    entries = [
        {"name": "p%d" % i, "type": "string"}
        for i in range(MAX_PARAMETERS_PER_MODEL + 1)
    ]
    error = refused(parse_parameter_schema, entries)
    assert error.code == VALIDATION_FAILED
    assert len(parse_parameter_schema(entries[:-1])) == MAX_PARAMETERS_PER_MODEL


def test_parse_refuses_an_uncompilable_pattern():
    """A broken regular expression must fail at save, not hang a request.

    The pattern is compiled once here so no request path can sit on a bad one.
    """
    error = refused(
        parse_parameter_schema, [{"name": "p", "type": "string", "pattern": "([a-z"}]
    )
    assert error.code == VALIDATION_FAILED
    assert "regular expression" in error.message


def test_validate_returns_payload_and_prompt_values_separately():
    """Two dictionaries, because two destinations: an API field and prompt text.

    Collapsing them would either send prompt wording as a field or drop a real
    field into prose, and both produce a wrong request.
    """
    schema = ParameterSchema(
        [
            ParameterSpec("steps", "integer", minimum=1, maximum=10),
            ParameterSpec("mood", "string", prompt_mapped=True),
        ]
    )
    payload, prompt_values = schema.validate({"steps": 4, "mood": "calm"})
    assert payload == {"steps": 4}
    assert prompt_values == {"mood": "calm"}


def test_validate_refuses_a_value_for_an_unknown_parameter():
    """Sending an undeclared field is at best ignored and at worst billed.

    The docstring promises the user sees the error before submission, so an
    unknown key is an exception rather than a silent drop.
    """
    schema = ParameterSchema([ParameterSpec("steps", "integer")])
    error = refused(schema.validate, {"steps": 1, "warp_speed": 9})
    assert error.code == VALIDATION_FAILED
    assert error.details["unsupported"] == ["warp_speed"]
    assert error.details["supported"] == ["steps"]


@pytest.mark.parametrize("value", [0, 11, -3])
def test_validate_refuses_a_value_out_of_range(value):
    """A range the UI draws as a slider must also hold for a raw API caller."""
    schema = ParameterSchema([ParameterSpec("steps", "integer", minimum=1, maximum=10)])
    error = refused(schema.validate, {"steps": value})
    assert error.code == VALIDATION_FAILED
    assert error.field == "steps"


def test_validate_round_trips_a_valid_value_set():
    """Coercion must not change a value that was already right.

    A round trip that quietly converts 3 to "3" changes what the provider is
    asked for, so each type is checked for exact equality after validation.
    """
    schema = ParameterSchema(
        [
            ParameterSpec("steps", "integer", minimum=1, maximum=10),
            ParameterSpec("scale", "number", minimum=0.1, maximum=2.0),
            ParameterSpec("size", "enum", options=("small", "large")),
            ParameterSpec("safe", "boolean"),
            ParameterSpec("tags", "array", item_type="string"),
        ]
    )
    submitted = {
        "steps": 5,
        "scale": 1.5,
        "size": "large",
        "safe": True,
        "tags": ["a", "b"],
    }
    payload, prompt_values = schema.validate(submitted)
    assert payload == submitted
    assert prompt_values == {}


def test_validate_routes_a_prompt_mapped_parameter_out_of_the_payload():
    """A prose-only control must never reach the request body.

    The model has no field for it; sending it anyway would be a rejected or
    ignored key, and losing it would silently discard the user's instruction.
    """
    schema = ParameterSchema(
        [ParameterSpec("camera_note", "string", prompt_mapped=True, required=True)]
    )
    payload, prompt_values = schema.validate({"camera_note": "slow push in"})
    assert payload == {}
    assert prompt_values == {"camera_note": "slow push in"}


def test_validate_refuses_a_missing_required_field():
    """Required means required for a raw caller too, not only for the form."""
    schema = ParameterSchema([ParameterSpec("steps", "integer", required=True)])
    error = refused(schema.validate, {})
    assert error.code == VALIDATION_FAILED
    assert error.field == "steps"


def test_validate_sends_the_api_key_when_it_differs_from_the_name():
    """A provider field named 'n' is declared once and used everywhere.

    The studio label and the wire field are different jobs, and conflating them
    would force users to see provider jargon.
    """
    schema = ParameterSchema([ParameterSpec("num_images", "integer", api_key="n")])
    payload, _ = schema.validate({"num_images": 3})
    assert payload == {"n": 3}


def test_public_schema_can_be_parsed_back_with_the_same_parameter_names():
    """A saved schema must survive a save and a reload unchanged.

    The connection wizard reads to_public() from the server and posts it back,
    so a round trip that loses a name would erase a control on every edit.
    """
    original = ParameterSchema(
        [
            ParameterSpec("steps", "integer", minimum=1, maximum=10, default=4),
            ParameterSpec(
                "size",
                "enum",
                options=("1024x1024", "512x512"),
                option_labels={"1024x1024": "Square"},
            ),
            ParameterSpec("note", "string", prompt_mapped=True),
        ]
    )
    reparsed = parse_parameter_schema(original.to_public())
    assert reparsed.names() == original.names()
    assert reparsed.get("steps").minimum == 1.0
    assert reparsed.get("steps").maximum == 10.0
    # options come back as a list rather than the tuple the spec was built with;
    # the values and their order are what matter to the renderer.
    assert list(reparsed.get("size").options) == ["1024x1024", "512x512"]
    assert reparsed.get("size").option_labels["1024x1024"] == "Square"
    assert reparsed.get("note").prompt_mapped is True


def test_empty_schema_accepts_an_empty_value_set_and_nothing_else():
    """A model with no declared controls still has to answer with two dicts."""
    schema = parse_parameter_schema([])
    assert len(schema) == 0
    assert schema.validate({}) == ({}, {})
    assert refused(schema.validate, {"anything": 1}).code == VALIDATION_FAILED


# ---------------------------------------------------------------------------
# 2. Capability profiles -- types.CapabilityProfile and the mode vocabulary
# ---------------------------------------------------------------------------


def test_capability_profile_round_trips_through_its_public_payload():
    """The profile is stored as JSON and rebuilt for every request.

    Anything the round trip loses is a capability the studio stops offering,
    so equality of the whole record is the guarantee worth asserting.
    """
    profile = CapabilityProfile(
        modality="image",
        modes=("text_to_image", "inpaint"),
        aspect_ratios=("1:1", "16:9"),
        resolutions=("1024x1024",),
        durations_seconds=(),
        frame_rates=(),
        reference_roles=("main_subject",),
        max_reference_images=2,
        max_outputs=4,
        supports_negative_prompt=True,
        supports_seed=True,
        supports_audio=False,
        supports_mask=True,
        native_camera_moves=("dolly_in",),
        restrictions=("no text rendering",),
        notes=("documented by the provider",),
        max_input_bytes=2048,
    )
    assert CapabilityProfile.from_public(profile.to_public()) == profile


def test_profile_does_not_infer_inpainting_from_the_image_modality():
    """An image model is not an inpainting model, and must not be treated as one.

    Inferring modes is the exact mistake the capability model exists to stop: a
    guessed mode is a button that fails after the user has written a prompt.
    """
    profile = CapabilityProfile(modality="image")
    assert profile.modes == ()
    assert profile.supports("inpaint") is False
    assert profile.supports_mask is False


def test_profile_does_not_infer_video_support_from_the_video_modality():
    """Declaring modality='video' says nothing about which video modes exist."""
    profile = CapabilityProfile(modality="video")
    assert profile.modes == ()
    assert profile.supports("text_to_video") is False
    assert profile.supports_audio is False


def test_a_profile_built_from_an_unknown_modality_claims_nothing():
    """'other' is a real modality and its vocabulary is empty, not guessed."""
    profile = CapabilityProfile.from_public({"modality": "audio", "modes": []})
    assert profile.modality == "audio"
    assert profile.modes == ()
    assert CapabilityProfile.from_public("not-a-mapping") == CapabilityProfile(
        modality="other"
    )


def test_the_mode_vocabulary_is_closed_per_modality():
    """Modes name real request shapes, so the set is fixed and disjoint.

    A mode the platform cannot request must not exist anywhere it could be
    stored against a model, which is why the vocabulary is enumerated rather
    than derived from whatever an adapter claims.
    """
    assert MODES_BY_MODALITY["image"] == IMAGE_MODES
    assert MODES_BY_MODALITY["video"] == VIDEO_MODES
    assert MODES_BY_MODALITY["other"] == ()
    # The two vocabularies share a handful of words by design -- "upscale" means
    # the same operation in both studios -- but neither accepts a word from
    # outside its own list.
    assert set(IMAGE_MODES) & set(VIDEO_MODES) == {"upscale"}
    assert "teleport" not in IMAGE_MODES
    assert "teleport" not in VIDEO_MODES
    assert modes_for("image") == IMAGE_MODES
    assert modes_for("not-a-modality") == ()
    assert all(mode in MODE_LABELS for mode in IMAGE_MODES + VIDEO_MODES)


def test_from_public_does_not_itself_refuse_an_unknown_mode_word():
    """Discrepancy: the brief expects an unknown mode string to be refused here.

    CapabilityProfile.from_public copies whatever tuple of strings it is handed,
    so a made-up word such as 'teleport' survives the round trip. The refusal
    lives in the vocabulary (MODE_VOCABULARY, checked by the model service) and
    in CapabilityProfile.require_mode, which the job service calls. This test
    pins the actual split so nobody assumes the constructor validates.
    """
    profile = CapabilityProfile.from_public(
        {"modality": "image", "modes": ["text_to_image", "teleport"]}
    )
    assert profile.modes == ("text_to_image", "teleport")
    assert profile.supports("teleport") is True


def test_the_repository_vocabulary_refuses_an_unknown_mode_word():
    """This is where the closed set actually bites: a stored mode must be in it."""
    from studio.repository import MODE_VOCABULARY

    assert MODE_VOCABULARY["image"] == tuple(IMAGE_MODES)
    assert MODE_VOCABULARY["video"] == tuple(VIDEO_MODES)
    assert MODE_VOCABULARY["other"] == ()
    assert "teleport" not in MODE_VOCABULARY["image"]
    assert "inpaint" not in MODE_VOCABULARY["video"]


def test_require_mode_refuses_a_mode_the_profile_does_not_declare():
    """The refusal happens before anything is queued or paid for."""
    profile = CapabilityProfile(modality="image", modes=("text_to_image",))
    error = refused(profile.require_mode, "inpaint", "My Model")
    assert error.code == CAPABILITY_UNSUPPORTED
    assert error.details == {"mode": "inpaint", "available": ["text_to_image"]}
    assert "inpaint" in error.message.replace("_", " ")


def test_max_outputs_is_clamped_to_at_least_one():
    """A model that produces zero outputs cannot be asked for anything."""
    assert CapabilityProfile.from_public({"max_outputs": 0}).max_outputs == 1
    assert CapabilityProfile.from_public({"max_outputs": 3}).max_outputs == 3


# ---------------------------------------------------------------------------
# 3. Camera moves -- base.CameraMove and the catalogue
# ---------------------------------------------------------------------------


def test_the_catalogue_holds_exactly_twenty_one_camera_moves():
    """The studio renders one tile per move, and the count is a product decision.

    A silent addition or removal here changes a UI that was reviewed against a
    fixed number, so the number is asserted rather than assumed.
    """
    assert len(CAMERA_MOVES) == 21
    assert len(CAMERA_MOVES_BY_KEY) == 21


def test_camera_move_keys_are_unique():
    """Keys are what the API receives; a duplicate would make one unreachable."""
    keys = [move.key for move in CAMERA_MOVES]
    assert len(set(keys)) == len(keys)
    assert set(keys) == set(CAMERA_MOVES_BY_KEY)


def test_every_camera_move_carries_a_boolean_is_native():
    """is_native drives whether the studio promises precise control.

    A truthy non-boolean here would render as a label that is technically
    defined and useless to the client, so the type is asserted, along with the
    fact that only two moves actually have a provider field behind them.
    """
    for move in CAMERA_MOVES:
        assert isinstance(move.is_native, bool), move.key
    assert sum(1 for move in CAMERA_MOVES if move.is_native) == 2


def test_a_non_native_move_carries_no_provider_parameter_or_value():
    """Prose-only moves must have nothing that looks like a hard control.

    If a non-native move carried a parameter the request builder could send a
    field the provider never documented, turning a suggestion into a lie.
    """
    for move in CAMERA_MOVES:
        if move.is_native:
            continue
        assert move.parameter is None, move.key
        assert move.value is None, move.key
        assert move.phrase.strip(), move.key


def test_a_native_move_names_the_field_and_the_value_it_sends():
    """The two native moves are the ones with a real provider field behind them."""
    native = [move for move in CAMERA_MOVES if move.is_native]
    assert [move.key for move in native] == ["dolly_in", "dolly_out"]
    for move in native:
        assert move.parameter == "camera_move"
        assert move.value == move.key


def test_every_camera_move_has_a_label_and_a_prompt_phrase():
    """Both are rendered: the label on the tile, the phrase into the prompt."""
    for move in CAMERA_MOVES:
        assert isinstance(move, CameraMove)
        assert move.label.strip(), move.key
        assert move.phrase.strip(), move.key
        assert move.group in ("movement", "framing"), move.key


def test_looking_up_an_unknown_camera_move_returns_none():
    """The service turns None into a field-scoped error, so it must not raise,
    and must not return a default move that would silently apply instead."""
    assert camera_move("teleport") is None
    assert camera_move("dolly_in").key == "dolly_in"


def test_a_camera_move_cannot_be_mutated_after_the_catalogue_is_built():
    """Frozen dataclasses keep the shared catalogue from being edited per request."""
    from dataclasses import FrozenInstanceError

    with pytest.raises(FrozenInstanceError):
        CAMERA_MOVES[0].key = "mutated"


# ---------------------------------------------------------------------------
# 4. The mapping interpreter -- mapping.py is data, never code
# ---------------------------------------------------------------------------


def test_get_path_reads_nested_dicts_and_list_indexes():
    """Paths are the whole user-facing language of the declarative adapter."""
    payload = {"output": {"images": [{"url": "https://a/1.png"}, {"url": "https://a/2.png"}]}}
    assert get_path(payload, "output.images[1].url") == "https://a/2.png"
    assert get_path(payload, "output.images[0]") == {"url": "https://a/1.png"}


def test_get_path_returns_the_default_for_every_missing_segment():
    """A provider payload that lacks a field is a fact, not a crash."""
    payload = {"output": {"images": [{"url": "https://a/1.png"}]}}
    assert get_path(payload, "output.images[9].url", "fallback") == "fallback"
    assert get_path(payload, "output.absent", "fallback") == "fallback"
    assert get_path(payload, "output.images[0].url.deeper", "fallback") == "fallback"
    assert get_path(5, "output", "fallback") == "fallback"


def test_get_path_against_a_list_finds_the_first_element_holding_the_name():
    """A bare name applied to a list is documented as a search, not an error."""
    payload = {"items": [{"id": "a"}, {"id": "b"}]}
    assert get_path(payload, "items.id") == "a"


def test_get_path_raises_type_error_for_an_empty_path():
    """Discrepancy: get_path's docstring says 'Never raises'.

    It does raise: the no-path branch calls isinstance(data, Any), and
    typing.Any cannot be used with isinstance, so both None and "" produce
    TypeError. Nothing on the request path reaches it -- _read() in
    declarative.py short-circuits an empty path -- but the documented
    guarantee is not what the code does.
    """
    with pytest.raises(TypeError):
        get_path({"a": 1}, None)
    with pytest.raises(TypeError):
        get_path({"a": 1}, "")


def test_first_present_skips_none_and_empty_but_keeps_zero():
    """Zero and False are real provider values; only empty means absent."""
    payload = {"a": None, "b": "", "c": 0, "d": False}
    assert first_present(payload, ["a", "b", "c"]) == 0
    assert first_present(payload, ["a", "b", "d"]) is False
    assert first_present(payload, ["a", "b"]) is None
    assert first_present(["not", "a", "mapping"], ["a"]) is None


def test_render_template_substitutes_the_names_it_is_given():
    """Substitution is a literal string replace, which is why it is safe."""
    assert render_template("/jobs/{request_id}", {"request_id": "abc"}) == "/jobs/abc"
    assert (
        render_template("/jobs/{model}/{request_id}", {"model": "m1", "request_id": "7"})
        == "/jobs/m1/7"
    )
    assert render_template("", {}) == ""
    assert render_template("/static/path", {}) == "/static/path"


def test_render_template_is_a_pure_replace_with_no_formatting():
    """No format spec, no attribute access, no call: braces are only replaced.

    A doubled placeholder is substituted twice, which is the proof that no
    expression grammar is involved.
    """
    assert render_template("/{request_id}-{request_id}", {"request_id": "x"}) == "/x-x"


def test_render_template_raises_for_a_missing_key():
    """Discrepancy: the brief expects a missing key to yield an empty value.

    render_template raises KeyError instead -- for a {name} that was never
    supplied, and for a brace group that is not a name at all ({0}). A caller
    that assumed an empty string would not catch this, so the real contract is
    pinned here.
    """
    with pytest.raises(KeyError) as caught:
        render_template("/jobs/{missing}", {})
    assert caught.value.args[0] == "missing"
    with pytest.raises(KeyError):
        render_template("/jobs/{a}/{b}", {"a": "1"})
    with pytest.raises(KeyError):
        render_template("/jobs/{0}", {})


def test_collect_urls_finds_http_urls_in_nested_dicts_and_lists():
    """The fallback finds outputs in a payload whose shape was not pinned down."""
    payload = {
        "data": [
            {"url": "https://a/1.png"},
            {"nested": {"uri": "http://b/2.png"}},
            {"note": "https://c/3"},
            {"url": "not-a-url"},
        ]
    }
    assert collect_urls(payload) == ["https://a/1.png", "http://b/2.png", "https://c/3"]


def test_collect_urls_is_bounded_in_depth():
    """Bounded so it cannot wander into a large unrelated structure."""
    shallow = {"a": {"b": {"c": "https://a/1.png"}}}
    deep = "https://a/1.png"
    for _ in range(7):
        deep = {"level": deep}
    assert collect_urls(shallow) == ["https://a/1.png"]
    assert collect_urls(deep) == []


def test_collect_urls_accepts_a_bare_string_and_refuses_non_urls():
    """A string output field is a valid response; a non-URL string is not."""
    assert collect_urls("https://x/1.png") == ["https://x/1.png"]
    assert collect_urls("not-a-url") == []
    assert collect_urls(None) == []


def test_drop_empty_removes_none_and_empty_string_but_keeps_zero_and_false():
    """The request body builder drops blanks; dropping 0 would change a request."""
    assert drop_empty({"a": None, "b": "", "c": 0, "d": False, "e": "v", "f": []}) == {
        "c": 0,
        "d": False,
        "e": "v",
        "f": [],
    }


def test_the_mapping_module_contains_no_code_execution_primitive():
    """The interpreter must be readable as data handling, and provably so.

    eval, exec, __import__ and getattr are the doors from a mapping to the
    interpreter. None of them appears in the module source; the only compile
    call is re.compile, which builds a pattern object rather than running code.
    """
    path = os.path.join(REPO, "studio", "adapters", "mapping.py")
    with open(path, encoding="utf-8") as handle:
        source = handle.read()
    for forbidden in ("eval(", "exec(", "__import__", "getattr(", "importlib"):
        assert forbidden not in source, forbidden
    assert source.count("compile(") == source.count("re.compile(")


def test_the_small_coercion_helpers_are_total():
    """Every helper answers for junk input rather than raising, by design."""
    assert as_list(None) == []
    assert as_list("one") == ["one"]
    assert as_list((1, 2)) == [1, 2]
    assert to_int("5") == 5
    assert to_int(True) is None
    assert to_int("not a number") is None
    assert percent_to_fraction(50) == 0.5
    assert percent_to_fraction(0.5) == 0.5
    assert percent_to_fraction(120) is None
    assert percent_to_fraction("50%") == 0.5
    assert join_url("https://a/", "/b") == "https://a/b"
    assert join_url("https://a", "https://c/d") == "https://c/d"
    assert unique(["a", "b", "a"]) == ["a", "b"]


# ---------------------------------------------------------------------------
# 5. The declarative security boundary -- load_spec, presets, status words
# ---------------------------------------------------------------------------


def test_load_spec_accepts_a_valid_mapping_and_normalises_it():
    """The baseline: a mapping that names what its structure needs must load.

    Without this every refusal below could be the parser rejecting everything.
    """
    loaded = declarative.load_spec(VALID_SYNC_SPEC)
    assert loaded["structure"] == "sync"
    assert loaded["submit"]["path"] == "/generate"
    assert loaded["submit"]["method"] == "POST"
    assert loaded["response"]["type"] == "json"
    assert loaded["status"]["poll_after_seconds"] == declarative.DEFAULT_POLL_SECONDS


def test_load_spec_refuses_an_unknown_top_level_key():
    """A key nobody understands would be stored and then silently ignored.

    Refusing it at save names the offending key while the user can still fix
    it, instead of failing later against a provider that was already called.
    """
    spec = dict(VALID_SYNC_SPEC, teleport="yes")
    error = refused(declarative.load_spec, spec)
    assert error.code == VALIDATION_FAILED
    assert error.details["key"] == "mapping.teleport"
    assert "not recognised" in error.message


@pytest.mark.parametrize(
    'header',
    ['Authorization', 'authorization', 'Host', 'Content-Length', 'Cookie', 'Proxy-Authorization', 'Content-Type'],
)
def test_load_spec_refuses_a_header_it_would_have_to_manage(header):
    '''The platform sets this header itself, so a mapping may not set it either.

    A header that carries a credential (Authorization, Proxy-Authorization,
    Cookie) or a framing decision (Host, Content-Length, Content-Type) belongs
    to the platform. Allowing one would let a saved mapping smuggle a secret
    past the sealed credential store, or forge a body length.

    The refusal names the header. That is the difference between an operator
    being able to fix the mapping and being told only that a key is
    unrecognised.
    '''
    error = refused(declarative.load_spec, spec_with(extra_headers={header: 'x'}))
    assert error.code == VALIDATION_FAILED
    assert error.details['key'].endswith('mapping.submit.extra_headers')
    assert 'cannot be set here' in error.message


def test_the_inner_header_guard_reserves_every_credential_and_framing_name():
    '''The guard itself, exercised without the mapping loader in the way.

    Every name here is one the platform sends. A cookie or a proxy
    authorization header is a credential in the same sense Authorization is,
    so both are reserved, not merely unrecognised.
    '''
    for header in (
        'Authorization',
        'Proxy-Authorization',
        'Cookie',
        'Host',
        'Content-Length',
        'Content-Type',
        'Transfer-Encoding',
        'Connection',
        'Accept-Encoding',
    ):
        error = refused(
            declarative._clean_headers, {header: 'x'}, 'mapping.submit.extra_headers'
        )
        assert 'cannot be set here' in error.message
    assert declarative._clean_headers({'X-Trace': 'a'}, 'k') == {'X-Trace': 'a'}


def test_extra_fields_are_kept_and_capped_at_32():
    '''Static body fields are user data, so they are kept -- up to a limit.

    They used to be refused outright, because the loader walked them against an
    allowlist that was empty by construction. A provider that needs one extra
    scalar field is ordinary, so they load; the 33rd is refused, which is the
    documented cap finally being reachable through the real entry point.
    '''
    one = declarative.load_spec(spec_with(extra_fields={'quality': 'high'}))
    assert one['submit']['extra_fields'] == {'quality': 'high'}

    full = {'f%d' % i: 'v' for i in range(32)}
    assert declarative.load_spec(spec_with(extra_fields=full))['submit']['extra_fields'] == full

    too_many = {'f%d' % i: 'v' for i in range(33)}
    error = refused(declarative.load_spec, spec_with(extra_fields=too_many))
    assert 'too many entries' in error.message


def test_an_extra_field_may_not_be_nested():
    '''A nested value would turn a flat field map into an arbitrary payload tree.
    '''
    error = refused(
        declarative.load_spec, spec_with(extra_fields={'meta': {'a': 1}})
    )
    assert error.code == VALIDATION_FAILED


@pytest.mark.parametrize('count', [1, 12])
def test_reference_mappings_survive_loading(count):
    '''A declared reference must reach the request, or the edit is not an edit.

    The loader used to return an empty list for every declared reference, so a
    mapping that said "send the source image in the image field" sent nothing
    and produced an unrelated picture instead of an error. This is the exact
    failure the loud-refusal design exists to prevent, so the list now survives
    validation.
    '''
    refs = [
        {'field': 'image%d' % i, 'role': 'main_subject', 'encoding': 'url'}
        for i in range(count)
    ]
    loaded = declarative.load_spec(spec_with(references=refs))
    assert len(loaded['submit']['references']) == count
    assert [item['field'] for item in loaded['submit']['references']] == [
        'image%d' % i for i in range(count)
    ]
    assert declarative.spec_summary(loaded)['reference_fields'] == [
        'image%d' % i for i in range(count)
    ]


def test_more_than_12_reference_mappings_are_refused():
    '''Twelve is the documented cap, and it is enforced where the user can see it.
    '''
    refs = [{'field': 'image%d' % i} for i in range(13)]
    error = refused(declarative.load_spec, spec_with(references=refs))
    assert 'At most 12 reference mappings' in error.message


def test_load_spec_refuses_an_unknown_structure_word():
    """The structure decides which blocks are required, so it is a closed list."""
    error = refused(declarative.load_spec, dict(VALID_SYNC_SPEC, structure="streaming"))
    assert error.code == VALIDATION_FAILED
    assert error.details["key"] == "mapping.structure"
    assert all(word in error.message for word in declarative.STRUCTURE_TYPES)


@pytest.mark.parametrize("path", ["generate", "/g/{evil}", "//other-host/g", "/g/{bad"])
def test_load_spec_refuses_a_relative_or_placeholder_abusing_path(path):
    """Paths are URL fragments on the user's behalf, so the grammar is narrow.

    A relative path, an unknown placeholder, a protocol-relative path or an
    unbalanced brace is refused here, which is what keeps {request_id} and
    {model} the only substitution the request builder can ever perform.
    """
    error = refused(declarative.load_spec, spec_with(path=path))
    assert error.code == VALIDATION_FAILED
    assert error.details["key"] == "mapping.submit.path"


def test_the_default_mapping_loads_with_a_json_body():
    '''json is the default submit encoding, so it must be an accepted one.

    The validator checked body_type against the reference-encoding list, which
    did not include json, while the blank mapping defaulted to json. Everything
    downstream followed from that: load_spec(None) raised, the OpenAI images
    preset raised, and nothing that relied on a default could be saved.
    '''
    assert declarative.DEFAULT_SPEC['submit']['body_type'] == 'json'
    for body_type in ('json', 'url', 'base64', 'multipart'):
        loaded = declarative.load_spec(spec_with(body_type=body_type))
        assert loaded['submit']['body_type'] == body_type


def test_an_absent_body_type_falls_back_to_json():
    '''A mapping that says nothing about its encoding still has to load.
    '''
    for blank in ('', None):
        loaded = declarative.load_spec(spec_with(body_type=blank))
        assert loaded['submit']['body_type'] == 'json'


def test_an_unknown_body_type_is_refused_with_the_full_list():
    '''yaml is not a body encoding this platform can produce, and it says so.
    '''
    error = refused(declarative.load_spec, spec_with(body_type='yaml'))
    assert error.details['key'] == 'mapping.submit.body_type'
    for word in declarative.SUBMIT_BODY_TYPES:
        assert word in error.message


def test_an_empty_or_absent_mapping_is_refused_for_a_missing_path_not_an_encoding():
    '''The refusal must name the thing the user still has to supply.

    With the body-type defect gone, an empty mapping fails on its first real
    gap: a structure that needs somewhere to submit to. Naming body_type here
    would send an operator to fix a field that was never wrong.
    '''
    for raw in (None, {}, ''):
        error = refused(declarative.load_spec, raw)
        assert error.details['key'] == 'mapping.submit.path'
        assert 'submit path' in error.message


def test_load_spec_still_enforces_the_per_structure_requirements():
    '''Past encoding and paths, the structure rules still fire as documented.

    A queued provider without a status path, and a synchronous one without a
    response path, are both refused with the missing key named.
    '''
    queued = {
        'structure': 'queued',
        'submit': {'path': '/submit', 'body_type': 'json'},
        'status': {'path': '/status/{request_id}'},
    }
    error = refused(declarative.load_spec, queued)
    assert error.details['key'] == 'mapping.status.status_path'

    sync_without_response = {
        'structure': 'sync',
        'submit': {'path': '/generate', 'body_type': 'json'},
    }
    error = refused(declarative.load_spec, sync_without_response)
    assert error.details['key'] == 'mapping.response.outputs_path'


def test_load_spec_refuses_a_payload_that_is_not_an_object():
    '''A stored mapping arrives as JSON text, so the shape is checked twice.
    '''
    for raw in ([1, 2], 7):
        assert refused(declarative.load_spec, raw).code == VALIDATION_FAILED
    assert (
        declarative.load_spec(
            '{"structure": "sync", "submit": {"path": "/g", "body_type": "json"}, '
            '"response": {"outputs_path": "data"}}'
        )['structure']
        == 'sync'
    )


def test_the_openai_images_preset_loads_and_is_usable():
    '''The documented accessor has to return a spec the request builder can use.

    It raised for as long as the body type was wrong, which meant the OpenAI
    adapter could not produce a request at all -- a registered adapter that
    could never work. It now loads with its own declared structure.
    '''
    loaded = declarative.preset_spec('openai_images')
    assert loaded['structure'] == 'openai_images'
    assert loaded['submit']['body_type'] == 'json'
    assert loaded['submit']['path']
    assert declarative.spec_summary(loaded)['outputs_path']


def test_the_edit_preset_keeps_its_source_image_field():
    '''An edit preset that drops the source image is not an edit.

    The reference mapping declares the image field and the mask field; both
    must survive loading, or the request would send a mask for an image that
    was never uploaded.
    '''
    loaded = declarative.preset_spec('openai_images_edit')
    assert loaded['submit']['body_type'] == 'multipart'
    assert loaded['submit']['mask_field'] == 'mask'
    assert [item['field'] for item in loaded['submit']['references']] == ['image']
    assert declarative.spec_summary(loaded)['reference_fields'] == ['image']


def test_preset_spec_refuses_an_unknown_preset_name():
    """The preset list is closed, and the error names the known ones."""
    error = refused(declarative.preset_spec, "midjourney")
    assert error.code == VALIDATION_FAILED
    assert "openai_images" in (error.hint or "")


@pytest.mark.parametrize("word", ["weird", "mystery", "half_done", "??", "null"])
def test_an_unrecognised_status_word_maps_to_processing_never_completed(word):
    """Guessing upwards would show a result that does not exist.

    This is the single most expensive mistake the classifier could make, so an
    unknown word is treated as still working and the job keeps polling.
    """
    assert declarative.classify_status(word, {}) == core.STATE_PROCESSING
    loaded = declarative.load_spec(VALID_SYNC_SPEC)
    assert declarative.classify_status(word, loaded) == core.STATE_PROCESSING


def test_classify_status_maps_the_documented_word_lists():
    """Each list is a different outcome, and the mapping may override them."""
    loaded = declarative.load_spec(VALID_SYNC_SPEC)
    assert declarative.classify_status("succeeded", loaded) == core.STATE_COMPLETED
    assert declarative.classify_status("SUCCESS", loaded) == core.STATE_COMPLETED
    assert declarative.classify_status("failed", loaded) == core.STATE_FAILED
    assert declarative.classify_status("cancelled", loaded) == core.STATE_CANCELLED
    assert declarative.classify_status("running", loaded) == core.STATE_PROCESSING
    assert declarative.classify_status("", loaded) == core.STATE_PROCESSING
    assert declarative.classify_status(None, {}) == core.STATE_PROCESSING

    custom = {
        "status": {
            "succeeded_values": ["ok"],
            "failed_values": ["bad"],
            "cancelled_values": ["stop"],
        }
    }
    assert declarative.classify_status("ok", custom) == core.STATE_COMPLETED
    assert declarative.classify_status("succeeded", custom) == core.STATE_PROCESSING


def test_spec_supports_cancel_reads_the_per_connection_mapping():
    """Cancelling is a property of the mapping, not of the adapter class.

    A mapping that cannot be loaded reports False rather than raising, so the
    studio simply hides the cancel button.
    """
    with_cancel = dict(
        VALID_SYNC_SPEC, cancel={"path": "/cancel/{request_id}", "method": "POST"}
    )
    assert declarative.spec_supports_cancel(with_cancel) is True
    assert declarative.spec_supports_cancel(VALID_SYNC_SPEC) is False
    assert declarative.spec_supports_cancel({"submit": {"path": "/x"}}) is False


def test_render_path_substitutes_only_the_two_permitted_names():
    """Anything else in the template is left exactly as written."""
    assert declarative.render_path("/j/{request_id}", request_id="abc") == "/j/abc"
    assert (
        declarative.render_path("/m/{model}/j/{request_id}", request_id="1", model="m1")
        == "/m/m1/j/1"
    )
    assert declarative.render_path("") == ""
    assert declarative.render_path("/{other}", request_id="x") == "/{other}"


# ---------------------------------------------------------------------------
# 6. The registry -- core.known_adapters / get_adapter
# ---------------------------------------------------------------------------


def test_known_adapters_is_exactly_the_four_shipped_provider_types():
    """This tuple is the wizard's type picker, so it doubles as the allowlist.

    The database CHECK constraint lists the same four, and a drift between them
    would offer a type the schema cannot store.
    """
    assert known_adapters() == ("declarative_http", "fal_queue", "mock", "openai_images")
    from studio.repository import ADAPTER_TYPES

    assert set(ADAPTER_TYPES) == set(known_adapters())


def test_the_mock_adapter_is_the_only_adapter_flagged_as_mock():
    """Every surface that can show a model built on the mock labels it.

    A second mock slipping into the registry would make that label meaningless.
    """
    flags = {slug: get_adapter(slug).is_mock for slug in known_adapters()}
    assert flags == {
        "declarative_http": False,
        "fal_queue": False,
        "mock": True,
        "openai_images": False,
    }
    assert [slug for slug, is_mock in flags.items() if is_mock] == ["mock"]


def test_get_adapter_raises_for_an_unknown_slug_rather_than_returning_none():
    """Discrepancy: the brief says get_adapter returns None for an unknown slug.

    It raises: capability_unsupported, with the known slugs in details and a
    hint that names the wizard. That is arguably the better contract -- a caller
    cannot forget the None check and then call a method on it -- but it is not
    what the brief describes, so the exception is pinned here.
    """
    error = refused(get_adapter, "not_a_provider")
    assert error.code == CAPABILITY_UNSUPPORTED
    assert error.details["adapter_type"] == "not_a_provider"
    assert error.details["known"] == sorted(known_adapters())
    assert "not_a_provider" in error.message


def test_get_adapter_hands_out_a_fresh_instance_each_call():
    """Adapters must not carry state between requests.

    Sharing one instance would let a per-request mapping or credential survive
    into the next operation, which is exactly what AdapterContext exists to
    prevent.
    """
    first = get_adapter("mock")
    second = get_adapter("mock")
    assert type(first) is type(second)
    assert first is not second


def test_adapter_types_reports_the_same_slugs_with_their_flags():
    """The picker payload is built from the registry, so it cannot go stale."""
    described = {item["slug"]: item for item in adapter_types()}
    assert set(described) == set(known_adapters())
    for slug, item in described.items():
        assert item["is_mock"] is get_adapter(slug).is_mock
        assert isinstance(item["default_modes"], list)
        assert item["label"]


def test_the_declarative_adapter_promises_the_narrowest_honest_default():
    """A hand-written mapping earns its extra modes by declaring them.

    The default is text to image because that is the only mode the platform may
    assume about an endpoint it has never seen, and the adapter refuses to
    invent a capability profile for one either.
    """
    adapter = get_adapter("declarative_http")
    assert adapter.default_modes() == ("text_to_image",)
    assert adapter.capabilities_for(None, "anything") is None
