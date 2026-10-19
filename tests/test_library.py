"""The library and persistence layer, exercised through the repositories directly.

Every test here states one guarantee the platform depends on and says, in its
docstring, why losing it would matter to a person using the studio. Nothing talks
HTTP: each case builds a real SQLite database with ``studio.db.Database`` +
``.migrate()``, binds the repositories with ``studio.repository.repositories``,
and calls the method a route handler would call.

Two places where the code differs from what its own prose claims
----------------------------------------------------------------
Both are asserted as they behave, not as documented, and the docstring says so:

* ``LEGAL_TRANSITIONS`` declares two self-edges (``processing -> processing`` and
  ``downloading -> downloading``) and the comment above the table calls them "the
  deliberate exception -- a polling worker re-asserts processing on every pass to
  record what the provider said". ``JobRepository._check_transition`` refuses them
  anyway, because it tests ``if target == status or target not in allowed``. The
  declared self-edges are therefore unreachable through ``transition``;
  ``annotate`` is the path that actually records a repeat observation.
* The parameter names a built-in preset carries (``cfg_scale``, ``steps``,
  ``sampler``, ``duration_seconds``, ``fps``, ``motion_strength``) are not the
  names a model schema from ``studio.adapters.catalog`` declares
  (``guidance_scale``, ``num_inference_steps`` and so on), so such a schema refuses
  a preset parameter payload outright. ``presets_data`` declares no
  supported-parameter field of its own, so the per-modality vocabulary asserted
  below is read off the catalogue itself.

Run:  python -m pytest tests/test_library.py -q
"""

from __future__ import annotations

import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from studio import presets_data  # noqa: E402
from studio.adapters.base import (  # noqa: E402
    CAMERA_MOVES_BY_KEY,
    IMAGE_MODES,
    VIDEO_MODES,
    parse_parameter_schema,
)
from studio.adapters.mock import MockAdapter  # noqa: E402
from studio.config import load_config  # noqa: E402
from studio.db import Database  # noqa: E402
from studio.errors import CONFLICT, NOT_FOUND, VALIDATION_FAILED, ApiError  # noqa: E402
from studio.repository import (  # noqa: E402
    JOB_STATES,
    LEGAL_TRANSITIONS,
    AuditRepository,
    repositories,
)
from studio.security.accounts import AccountService  # noqa: E402

PASSWORD = "correct horse battery staple"


# ---------------------------------------------------------------------------
# Fixtures: one migrated database for the module, one workspace per test
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def studio(tmp_path_factory):
    """A real, migrated database in a temporary directory, plus the repositories.

    Module scoped so the seven migration files run once rather than once per
    case; every test then registers its own accounts, so no two tests share a
    workspace and nothing outside the temporary directory is touched.
    """
    folder = tmp_path_factory.mktemp("studio-library")
    db = Database(folder / "studio.db")
    db.migrate()
    config = load_config(
        {
            "STUDIO_DATA_DIR": str(folder / "data"),
            "STUDIO_ENV": "development",
        }
    )
    return {
        "db": db,
        "repos": repositories(db),
        "accounts": AccountService(db, config),
        "config": config,
    }


def _register(studio, label):
    """Create one account, and with it one personal workspace.

    ``AccountService.register`` is the only place users and personal workspaces
    are created, so using it means the rows under test are the ones the product
    actually writes.
    """
    address = "%s-%s@example.com" % (label, os.urandom(6).hex())
    return studio["accounts"].register(address, PASSWORD, "Ada")


@pytest.fixture()
def account(studio):
    """A fresh account in its own workspace, for the test that asked for it."""
    return _register(studio, "user")


def _other_account(studio):
    """A second, independent account in its own workspace."""
    return _register(studio, "other")


def _new_job(studio, registration, **overrides):
    """Enqueue one bare image job in the account workspace."""
    payload = {"modality": "image", "mode": "text_to_image", "prompt": "a lighthouse at dusk"}
    payload.update(overrides)
    return studio["repos"].jobs.create(registration.workspace_id, registration.user_id, payload)


#: How to walk a freshly created job to each state using legal edges only.
#: ``completed`` is the exception: it needs a recorded output first, because
#: ``_check_payload`` refuses "completed" while ``output_count`` is zero.
PATH_TO = {
    "draft": ("draft", []),
    "queued": ("queued", []),
    "validating": ("queued", ["validating"]),
    "submitting": ("queued", ["submitting"]),
    "processing": ("queued", ["submitting", "processing"]),
    "downloading": ("queued", ["submitting", "processing", "downloading"]),
    "failed": ("queued", ["failed"]),
    "cancelled": ("queued", ["cancelled"]),
    "timed_out": ("queued", ["timed_out"]),
    "completed": ("queued", ["submitting", "processing", "OUTPUT-THEN-COMPLETE"]),
}


def _place(studio, registration, state):
    """Create a job and drive it to ``state`` through legal transitions only."""
    created_as, steps = PATH_TO[state]
    job = _new_job(studio, registration, status=created_as)
    repos = studio["repos"]
    for target in steps:
        if target == "OUTPUT-THEN-COMPLETE":
            repos.jobs.add_output(
                job["id"], registration.workspace_id, 0, provider_url="https://example.invalid/out.png"
            )
            repos.jobs.transition(
                registration.workspace_id, job["id"], state="completed", message="Finished."
            )
            continue
        repos.jobs.transition(
            registration.workspace_id,
            job["id"],
            state=target,
            error=(
                {"code": "provider_error", "message": "the provider said no"}
                if target == "failed"
                else None
            ),
        )
    return job["id"]



# ===========================================================================
# 1. The preset catalogue
# ===========================================================================
# The catalogue is served verbatim to the client (studio/routes/library.py does
# "presets": list(builtin) + list(saved)), so everything below is data a user
# sees as a grid of cards and clicks.


def test_the_catalogue_has_forty_image_presets_and_thirty_video_presets():
    """Forty and thirty are the counts the studio grid and its API advertise.

    ``tests/test_studio.py`` pins ``builtin_count`` at 40 and 30 over HTTP; this
    pins the same numbers at the source, so a preset added or dropped without the
    client expectations being revisited fails here rather than as a card that
    silently disappears from the grid.
    """
    image = presets_data.presets_for("image")
    video = presets_data.presets_for("video")
    assert len(image) == 40
    assert len(video) == 30
    assert len(presets_data.PRESETS) == len(image) + len(video) == 70
    assert all(entry["modality"] == "image" for entry in image)
    assert all(entry["modality"] == "video" for entry in video)


def test_every_preset_sits_in_exactly_one_declared_category():
    """A preset in no declared category is a card no category filter can reach.

    Twenty image categories and seventeen video categories are what the studio
    renders as the filter row; a preset whose category is undeclared would be
    served but unreachable, so the declared sets and the categories actually used
    have to be the same set.
    """
    assert len(presets_data.IMAGE_CATEGORIES) == 20
    assert len(presets_data.VIDEO_CATEGORIES) == 17
    assert len(set(presets_data.IMAGE_CATEGORIES)) == 20
    assert len(set(presets_data.VIDEO_CATEGORIES)) == 17

    for modality, declared in (
        ("image", presets_data.IMAGE_CATEGORIES),
        ("video", presets_data.VIDEO_CATEGORIES),
    ):
        entries = presets_data.presets_for(modality)
        for entry in entries:
            category = entry["category"]
            assert isinstance(category, str), entry["key"]
            assert category in declared, "%s declares undeclared category %r" % (entry["key"], category)
            assert declared.count(category) == 1, "%s names a category declared twice" % entry["key"]
        # Every declared category has at least one preset behind it, so the filter
        # row never offers an empty tab.
        used = {entry["category"] for entry in presets_data.presets_for(modality)}
        assert used == set(declared)


def test_the_category_labels_carry_a_count_for_every_declared_category():
    """The filter row shows a label and a count, and the counts must add up.

    ``all_category_labels`` is what the route returns as ``categories``, so a
    count that disagrees with the grid shows "Portraiture (2)" above three cards.
    """
    for modality, declared in (
        ("image", presets_data.IMAGE_CATEGORIES),
        ("video", presets_data.VIDEO_CATEGORIES),
    ):
        labels = presets_data.all_category_labels(modality)
        assert [item["key"] for item in labels] == list(declared)
        assert all(item["count"] >= 1 for item in labels), modality
        assert sum(item["count"] for item in labels) == len(presets_data.presets_for(modality))
        for item in labels:
            assert item["label"], item["key"]

    # A modality the catalogue does not have is an empty list, not an error: the
    # route defaults to "image", but a client may ask for anything.
    assert presets_data.all_category_labels("audio") == []
    assert presets_data.all_category_labels("") == []


def test_preset_keys_are_unique_across_the_whole_catalogue():
    """A key is the only identifier an API client is told to store.

    ``PRESETS_BY_KEY`` is built by comprehension, so a duplicate would not raise:
    the later entry would win and ``preset(key)`` would return the wrong brief,
    which is exactly what the module docstring promises cannot happen.
    """
    keys = [entry["key"] for entry in presets_data.PRESETS]
    assert len(keys) == len(set(keys))
    assert len(presets_data.PRESETS_BY_KEY) == len(presets_data.PRESETS)
    for entry in presets_data.PRESETS:
        assert presets_data.PRESETS_BY_KEY[entry["key"]] is entry
        # The key is the slug that encodes modality and category, "for humans
        # reading logs", so it has to survive a round trip through the tree.
        assert entry["key"].startswith("%s.%s." % (entry["modality"], entry["category"])), entry["key"]
        assert len(entry["key"].split(".")) >= 3, entry["key"]


def test_every_preset_requests_a_mode_its_own_modality_can_perform():
    """A preset pointing at a mode no model of its modality offers is a dead button.

    The presets_data docstring is explicit: "A preset may not name a capability
    the platform cannot request, because a preset pointing at a mode a model does
    not have is a button that fails after the user has typed everything."
    """
    for modality, vocabulary in (("image", IMAGE_MODES), ("video", VIDEO_MODES)):
        for entry in presets_data.presets_for(modality):
            assert entry["modes"], entry["key"]
            assert isinstance(entry["modes"], list), entry["key"]
            unknown = [mode for mode in entry["modes"] if mode not in vocabulary]
            assert unknown == [], "%s requests modes %r outside %s_MODES" % (
                entry["key"],
                unknown,
                modality.upper(),
            )
            assert len(entry["modes"]) == len(set(entry["modes"])), entry["key"]


def test_every_camera_move_a_preset_references_exists():
    """A camera move is a key the studio looks up, so an unknown one is unpickable.

    The module docstring claims "every camera move exists in CAMERA_MOVES", and
    the value only helps if ``camera_move(key)`` resolves it: a typo would reach
    the user as an option that silently does nothing.
    """
    moves = set()
    for entry in presets_data.PRESETS:
        camera = entry["camera"]
        assert isinstance(camera, dict), entry["key"]
        if not camera:
            # An empty camera block is meaningful: an image preset has no move.
            assert entry["modality"] == "image", entry["key"]
            continue
        move = camera["move"]
        assert move in CAMERA_MOVES_BY_KEY, "%s references unknown camera move %r" % (entry["key"], move)
        assert camera["speed"] in ("slow", "medium", "fast"), entry["key"]
        moves.add(move)
    assert moves, "no preset references a camera move at all"
    assert all(entry["camera"] for entry in presets_data.presets_for("video"))


#: The parameter names the catalogue actually carries, read off the data itself.
#: ``presets_data`` has no "supported parameters" field -- the only vocabularies it
#: declares are the category tuples and the mode lists -- so this closed set is
#: the honest form of "the parameters a preset of this modality supports".
IMAGE_PARAMETER_NAMES = frozenset({"cfg_scale", "steps", "sampler"})
VIDEO_PARAMETER_NAMES = frozenset({"duration_seconds", "fps", "motion_strength"})


def test_a_preset_names_only_parameters_its_own_modality_declares():
    """A parameter a modality does not declare is a control nothing can honour.

    The image and video vocabularies are disjoint (``cfg_scale``/``steps``/
    ``sampler`` against ``duration_seconds``/``fps``/``motion_strength``), so a
    video preset carrying ``cfg_scale`` -- or an image preset carrying ``fps`` --
    would be a knob that reaches a provider which has never heard of it.
    """
    assert IMAGE_PARAMETER_NAMES.isdisjoint(VIDEO_PARAMETER_NAMES)

    for modality, declared in (
        ("image", IMAGE_PARAMETER_NAMES),
        ("video", VIDEO_PARAMETER_NAMES),
    ):
        for entry in presets_data.presets_for(modality):
            carried = set(entry["parameters"])
            assert carried, entry["key"]
            assert carried <= declared, "%s carries %r, outside %s" % (
                entry["key"],
                sorted(carried - declared),
                modality,
            )
            # Every numeric knob is a number, so nothing needs parsing on the way
            # out -- except `sampler`, which names one of a fixed set of sampling
            # algorithms and is a discrete choice rather than free text.
            for name, value in entry["parameters"].items():
                if name == "sampler":
                    assert isinstance(value, str) and value, (entry["key"], name)
                    assert " " not in value, (entry["key"], value)
                    continue
                assert isinstance(value, (int, float)) and not isinstance(value, bool), (
                    entry["key"],
                    name,
                )

    # The real vocabularies, pinned: a new name anywhere is a deliberate change.
    carried_by_modality = {
        modality: {name for entry in presets_data.presets_for(modality) for name in entry["parameters"]}
        for modality in ("image", "video")
    }
    assert carried_by_modality == {
        "image": set(IMAGE_PARAMETER_NAMES),
        "video": set(VIDEO_PARAMETER_NAMES),
    }


def test_a_preset_parameter_payload_is_refused_by_a_documented_model_schema():
    """BEHAVIOUR NOTE: the catalogue parameter names are not the provider names.

    A model whose schema comes from ``studio.adapters.catalog`` declares
    ``guidance_scale`` and ``num_inference_steps``, never ``cfg_scale``. Applying a
    preset therefore hands the request builder a payload that model refuses, with
    the unsupported names listed. That refusal is the honest failure the platform
    is built to give rather than a silent drop -- but it does mean a built-in
    preset parameters cannot be sent to such a model unchanged. Asserted as
    behaviour, because that is what the code does.
    """
    schema = parse_parameter_schema({"parameters": [{"name": "seed", "type": "integer"}]})
    preset = presets_data.preset("image.portraiture.soft-window-light")
    assert preset is not None
    with pytest.raises(ApiError) as caught:
        schema.validate(dict(preset["parameters"]))
    assert caught.value.code == VALIDATION_FAILED
    assert set(caught.value.details["unsupported"]) == set(preset["parameters"])
    assert caught.value.details["supported"] == ["seed"]


def test_an_unknown_preset_key_returns_none_instead_of_raising():
    """A stale client holding a key from an older build must get gone, not a 500.

    ``preset`` documents "The preset with this key, or None. An unknown key never
    raises", and the route that serves a built-in
    (``/api/presets/builtin:<key>``) passes a user-supplied key straight in, so a
    raise here would turn a bookmarked link into a server error.
    """
    assert presets_data.preset("no-such-key") is None
    assert presets_data.preset("") is None
    assert presets_data.preset("image.portraiture.does-not-exist") is None
    assert presets_data.presets_in_category("no-such-category") == ()
    # The lookup that does exist still works, or the assertion above proves nothing.
    found = presets_data.preset("image.portraiture.soft-window-light")
    assert found is not None and found["category"] == "portraiture"



# ===========================================================================
# 2. Projects and assets: workspace isolation
# ===========================================================================


def test_a_project_in_one_workspace_is_absent_from_another_workspace_list(studio, account):
    """Two tenants of one deployment must never see each other's projects.

    The isolation is claimed to live in the SQL ("in the WHERE clause, on every
    statement"), and a list is the cheapest place for it to be wrong: one missing
    predicate shows every customer the names of every other customer's work.
    """
    other = _other_account(studio)
    repos = studio["repos"]
    mine = repos.projects.create(account.workspace_id, account.user_id, {"name": "Alpha project"})

    assert other.workspace_id != account.workspace_id
    assert mine["workspace_id"] == account.workspace_id
    assert [row["name"] for row in repos.projects.list(account.workspace_id)] == ["Alpha project"]
    assert repos.projects.list(other.workspace_id) == []


def test_a_foreign_project_id_reports_the_same_error_as_a_missing_one(studio, account):
    """An id must not be probeable: not-yours and not-there answer identically.

    The module docstring states the rule: "A missing row and a row that belongs
    to somebody else are reported identically -- ApiError(NOT_FOUND) with the same
    message -- so an id cannot be probed for existence by watching which error
    comes back." A different message would let anyone enumerate another tenant's
    project ids.
    """
    other = _other_account(studio)
    repos = studio["repos"]
    mine = repos.projects.create(account.workspace_id, account.user_id, {"name": "Alpha project"})

    with pytest.raises(ApiError) as foreign:
        repos.projects.get(other.workspace_id, mine["id"])
    with pytest.raises(ApiError) as missing:
        repos.projects.get(other.workspace_id, "00000000-0000-4000-8000-000000000000")

    assert foreign.value.code == NOT_FOUND == missing.value.code
    assert foreign.value.message == missing.value.message == "That project could not be found."
    assert foreign.value.status == missing.value.status == 404
    # ... and the row is still readable by the workspace that owns it.
    assert repos.projects.get(account.workspace_id, mine["id"])["id"] == mine["id"]


def test_a_foreign_asset_id_reports_the_same_error_as_a_missing_one(studio, account):
    """An asset id is a URL path segment, so it is the easiest id to try to guess.

    ``AssetRepository.get`` is what ``/api/assets/{id}`` resolves through, and it
    is documented to refuse "an asset that is missing, soft-deleted, or in another
    workspace (NOT_FOUND, identically)".
    """
    other = _other_account(studio)
    repos = studio["repos"]
    asset = repos.assets.create(
        account.workspace_id,
        account.user_id,
        {
            "kind": "image",
            "source": "upload",
            "storage_key": "objects/a.png",
            "mime_type": "image/png",
            "filename": "a.png",
        },
    )

    rows, cursor = repos.assets.list(account.workspace_id)
    assert [row["id"] for row in rows] == [asset["id"]]
    assert cursor is None  # last page when there is only one row
    other_rows, other_cursor = repos.assets.list(other.workspace_id)
    assert other_rows == []
    assert other_cursor is None

    with pytest.raises(ApiError) as foreign:
        repos.assets.get(other.workspace_id, asset["id"])
    with pytest.raises(ApiError) as missing:
        repos.assets.get(other.workspace_id, "00000000-0000-4000-8000-000000000000")
    assert foreign.value.code == NOT_FOUND == missing.value.code
    assert foreign.value.message == missing.value.message == "That asset could not be found."

    # The bulk lookup drops the foreign id rather than returning it, which is the
    # shape a caller must treat as unusable rather than fetched.
    assert repos.assets.by_ids(other.workspace_id, [asset["id"]]) == []
    assert [row["id"] for row in repos.assets.by_ids(account.workspace_id, [asset["id"]])] == [
        asset["id"]
    ]


def test_asset_listing_paginates_with_a_keyset_cursor(studio, account):
    """The repository returns ``(rows, next_cursor)`` and the cursor walks the table.

    This is the repository-level contract that ``/api/assets`` consumes. The
    unpacking bug in routes/assets.py:97 surfaced because no test pinned the
    repository's return shape to a tuple. Three rows with distinct
    ``created_at`` make the ordering deterministic, so the cursor cannot
    accidentally pass by re-reading the same row.
    """
    repos = studio["repos"]
    base = "2020-01-01T00:00:00.000000Z"
    payloads = [
        {
            "kind": "image",
            "source": "upload",
            "storage_key": f"objects/{account.workspace_id}/{name}.png",
            "mime_type": "image/png",
            "filename": name,
            "created_at": base,
        }
        for name in ("alpha.png", "beta.png", "gamma.png")
    ]
    # Give each row a different timestamp so the keyset cursor can distinguish
    # them by ``created_at`` alone, without depending on the id tie-breaker.
    for offset, payload in enumerate(payloads):
        seconds = "%02d" % offset
        payload["created_at"] = "2020-01-01T00:00:%s.000000Z" % seconds
    created = [
        repos.assets.create(account.workspace_id, account.user_id, payload)
        for payload in payloads
    ]
    # The newest first order is what the cursor walks through.
    newest_first = [row["id"] for row in reversed(created)]

    page_size = 1
    seen = []
    cursor = None
    pages = 0
    while True:
        rows, next_cursor = repos.assets.list(
            account.workspace_id, limit=page_size, cursor=cursor
        )
        assert [row["id"] for row in rows] == [newest_first[pages]]
        seen.extend(row["id"] for row in rows)
        pages += 1
        if next_cursor is None:
            break
        cursor = next_cursor
        assert pages <= len(newest_first) + 1, "the cursor did not terminate"

    assert seen == newest_first
    assert pages == len(newest_first)


def test_attaching_a_foreign_asset_skips_it_and_attaches_nothing(studio, account):
    """Attaching is a write, so a cross-tenant id must not create a membership row.

    ``attach_assets`` documents that ids "from another workspace or that no longer
    exist ... are reported in skipped rather than attached, and the call does not
    raise, because a batch that names one stale id should still attach the rest."
    A row pointing at another tenant's asset would leak that asset into this
    project's view the moment the join stopped filtering.
    """
    other = _other_account(studio)
    repos = studio["repos"]
    foreign_asset = repos.assets.create(
        other.workspace_id,
        other.user_id,
        {
            "kind": "image",
            "source": "upload",
            "storage_key": "objects/b.png",
            "mime_type": "image/png",
            "filename": "b.png",
        },
    )
    project = repos.projects.create(account.workspace_id, account.user_id, {"name": "Alpha project"})

    result = repos.projects.attach_assets(account.workspace_id, project["id"], [foreign_asset["id"]])
    assert result == {"project_id": project["id"], "attached": [], "skipped": [foreign_asset["id"]]}

    assert repos.projects.get(account.workspace_id, project["id"])["assets"] == []
    assert studio["db"].scalar(
        "SELECT COUNT(*) FROM project_assets WHERE project_id = ?", (project["id"],)
    ) == 0
    # The foreign asset is untouched: it still belongs to no project at all.
    assert repos.assets.get(other.workspace_id, foreign_asset["id"])["project_id"] is None


def test_duplicate_asset_ids_in_one_attach_do_not_duplicate_the_link(studio, account):
    """A client that sends the same id three times must count one attachment.

    ``attach_assets`` de-duplicates before writing (``dict.fromkeys``) and the
    join table is ``PRIMARY KEY (project_id, asset_id)``, so the asset count the
    UI renders cannot inflate just because a checkbox was clicked twice.
    """
    repos = studio["repos"]
    project = repos.projects.create(account.workspace_id, account.user_id, {"name": "Alpha project"})
    asset = repos.assets.create(
        account.workspace_id,
        account.user_id,
        {
            "kind": "image",
            "source": "upload",
            "storage_key": "objects/c.png",
            "mime_type": "image/png",
            "filename": "c.png",
        },
    )

    result = repos.projects.attach_assets(
        account.workspace_id, project["id"], [asset["id"], asset["id"], asset["id"]]
    )
    assert result == {"project_id": project["id"], "attached": [asset["id"]], "skipped": []}

    # Attaching again is idempotent rather than an error, because "INSERT OR
    # IGNORE" is what makes a retried form submission harmless.
    again = repos.projects.attach_assets(account.workspace_id, project["id"], [asset["id"]])
    assert again["attached"] == [asset["id"]]

    loaded = repos.projects.get(account.workspace_id, project["id"])
    assert loaded["asset_count"] == 1
    assert [row["id"] for row in loaded["assets"]] == [asset["id"]]
    assert studio["db"].scalar(
        "SELECT COUNT(*) FROM project_assets WHERE project_id = ?", (project["id"],)
    ) == 1
    assert repos.assets.get(account.workspace_id, asset["id"])["project_id"] == project["id"]



# ===========================================================================
# 3. Versioning and provenance
# ===========================================================================


def test_linking_a_parent_records_the_edge_and_how_the_job_descends(studio, account):
    """A variation must trace back to the job it came from, with a named relation.

    Provenance is the point of keeping the original job untouched: the parent
    output and request "stay exactly as they were", so a user who iterates three
    times can still find what the first attempt produced and what it cost.
    """
    repos = studio["repos"]
    parent = _new_job(studio, account)
    child = _new_job(studio, account)

    assert child["parent_job_id"] is None
    assert child["relation"] == ""

    linked = repos.jobs.link_parent(account.workspace_id, child["id"], parent["id"], "variation")
    assert linked["id"] == child["id"]
    assert linked["parent_job_id"] == parent["id"]
    assert linked["relation"] == "variation"

    # The parent is never modified by the link.
    unchanged = repos.jobs.get(account.workspace_id, parent["id"])
    assert unchanged["parent_job_id"] is None and unchanged["relation"] == ""

    # The history says where the job came from instead of leaving it to be inferred.
    events = repos.jobs.events(child["id"])
    assert [event["status"] for event in events] == ["queued", "queued"]
    assert events[-1]["message"] == "Derived from an earlier job (variation)."
    assert events[-1]["detail"]["parent_job_id"] == parent["id"]
    assert events[-1]["detail"]["relation"] == "variation"


def test_a_job_cannot_gain_a_second_parent(studio, account):
    """One edge per job keeps lineage a tree rather than a graph with loops.

    ``link_parent`` documents it: "a job that already has a parent (CONFLICT --
    one edge per job, so lineage is a tree and not a graph with surprising
    loops)". Two parents would make which job did this come from unanswerable, and
    a loop would make a provenance walk never terminate.
    """
    repos = studio["repos"]
    first = _new_job(studio, account)
    second = _new_job(studio, account)
    child = _new_job(studio, account)

    repos.jobs.link_parent(account.workspace_id, child["id"], first["id"], "variation")
    with pytest.raises(ApiError) as caught:
        repos.jobs.link_parent(account.workspace_id, child["id"], second["id"], "edit")

    assert caught.value.code == CONFLICT
    assert caught.value.message == "This job already descends from another job."
    assert caught.value.details == {"job_id": child["id"], "parent_job_id": first["id"]}
    # The first edge survived the refusal.
    assert repos.jobs.get(account.workspace_id, child["id"])["parent_job_id"] == first["id"]


def test_a_job_cannot_be_its_own_parent(studio, account):
    """A self edge is a cycle of length one, and a provenance walk would never end.

    ``link_parent`` refuses it before it even reads the row: "A job cannot be
    derived from itself." (CONFLICT, on the ``parent_job_id`` field).
    """
    repos = studio["repos"]
    job = _new_job(studio, account)

    with pytest.raises(ApiError) as caught:
        repos.jobs.link_parent(account.workspace_id, job["id"], job["id"], "edit")

    assert caught.value.code == CONFLICT
    assert caught.value.message == "A job cannot be derived from itself."
    assert caught.value.field == "parent_job_id"
    assert caught.value.details == {"job_id": job["id"]}
    reloaded = repos.jobs.get(account.workspace_id, job["id"])
    assert reloaded["parent_job_id"] is None
    assert reloaded["relation"] == ""


def test_a_parent_in_another_workspace_is_reported_as_missing(studio, account):
    """A foreign parent id must not confirm that somebody else's job exists.

    The parent lookup is the same scoped read as every other, so it refuses a
    missing or foreign parent with one NOT_FOUND: there is no way to learn from
    the error which of the two it was.
    """
    other = _other_account(studio)
    repos = studio["repos"]
    foreign = _new_job(studio, other)
    mine = _new_job(studio, account)

    with pytest.raises(ApiError) as caught:
        repos.jobs.link_parent(account.workspace_id, mine["id"], foreign["id"], "variation")
    assert caught.value.code == NOT_FOUND
    assert caught.value.message == "That parent job could not be found."
    assert repos.jobs.get(account.workspace_id, mine["id"])["parent_job_id"] is None


def _legal_edges():
    """Every edge in ``LEGAL_TRANSITIONS``, as (from, to) pairs, minus self edges.

    The two self-edges are excluded here and proven separately: see
    ``test_a_repeated_in_flight_state_is_refused_despite_the_declared_self_edge``
    for why they cannot be reached through ``transition`` at all.
    """
    return [
        (source, target)
        for source in JOB_STATES
        for target in sorted(LEGAL_TRANSITIONS[source])
        if target != source
    ]


@pytest.mark.parametrize("source,target", _legal_edges())
def test_every_legal_transition_is_accepted(studio, account, source, target):
    """The state machine the queue enforces must match the table it publishes.

    A move declared legal but refused is a stuck job: the runner would try it, get
    a conflict, and leave the row somewhere no operator expects. ``failed``
    additionally needs a diagnostic, because ``_check_payload`` refuses "a failure
    with no reason", so one is supplied here as the worker would.
    """
    repos = studio["repos"]
    job_id = _place(studio, account, source)
    assert repos.jobs.get(account.workspace_id, job_id)["status"] == source
    if target == "completed":
        # Completion is not a bare state change: the repository refuses to mark a
        # job finished until something actually came back, which is what stops a
        # job showing a result it does not have. Record the output the worker
        # would have recorded, then make the move.
        repos.jobs.add_output(
            job_id,
            account.workspace_id,
            0,
            provider_url="https://example.invalid/out.png",
        )

    moved = repos.jobs.transition(
        account.workspace_id,
        job_id,
        state=target,
        error=(
            {"code": "provider_error", "message": "the provider said no"}
            if target == "failed"
            else None
        ),
    )
    assert moved["status"] == target
    # Every move appends exactly one event, in the same transaction as the update.
    assert repos.jobs.events(job_id)[-1]["status"] == target


def test_a_transition_the_table_forbids_is_a_conflict_naming_both_states(studio, account):
    """A client asking for an impossible move must be told what was possible.

    The error carries ``from``, ``to`` and ``allowed`` so the UI can render the
    options a user still has, and the message names both states so a log line
    explains the sequence without a lookup.
    """
    repos = studio["repos"]
    job = _new_job(studio, account)  # queued

    with pytest.raises(ApiError) as caught:
        repos.jobs.transition(account.workspace_id, job["id"], state="processing")

    assert caught.value.code == CONFLICT
    assert caught.value.message == "Cannot move this job from 'queued' to 'processing'."
    assert caught.value.details["from"] == "queued"
    assert caught.value.details["to"] == "processing"
    assert caught.value.details["allowed"] == sorted(LEGAL_TRANSITIONS["queued"])
    assert repos.jobs.get(account.workspace_id, job["id"])["status"] == "queued"


def test_a_completed_job_cannot_be_completed_before_an_output_exists(studio, account):
    """A job with no artifact is not finished, and claiming so is a lie about work.

    ``_check_payload`` states the rule: "completed requires at least one recorded
    output, because a job is only finished once an artifact exists." The refusal
    is a CONFLICT with a hint, so the caller is told what to do rather than only
    what it may not do.
    """
    repos = studio["repos"]
    job = _new_job(studio, account)
    repos.jobs.transition(account.workspace_id, job["id"], state="submitting")
    repos.jobs.transition(account.workspace_id, job["id"], state="processing")
    assert repos.jobs.get(account.workspace_id, job["id"])["output_count"] == 0

    with pytest.raises(ApiError) as caught:
        repos.jobs.transition(account.workspace_id, job["id"], state="completed")

    assert caught.value.code == CONFLICT
    assert caught.value.message == "A job cannot be completed before an output exists."
    assert caught.value.hint == "Record the output with add_output, then complete the job."
    assert caught.value.details["output_count"] == 0

    # Recording an output is what unblocks it, and the count is the real number of
    # rows rather than an increment.
    repos.jobs.add_output(
        job["id"], account.workspace_id, 0, provider_url="https://example.invalid/out.png"
    )
    done = repos.jobs.transition(
        account.workspace_id, job["id"], state="completed", message="Finished."
    )
    assert done["status"] == "completed"
    assert done["output_count"] == 1
    assert done["finished_at"] is not None


def test_a_failed_move_without_a_diagnostic_is_refused(studio, account):
    """A failure recorded with no reason is not a record of anything.

    ``_check_payload``: "failed requires a diagnostic, because a failure with no
    reason is not a record of anything." A job marked failed with an empty error
    column gives the person staring at it nothing to act on.
    """
    repos = studio["repos"]
    job = _new_job(studio, account)

    with pytest.raises(ApiError) as caught:
        repos.jobs.transition(account.workspace_id, job["id"], state="failed")

    assert caught.value.code == VALIDATION_FAILED
    assert caught.value.message == "A job cannot be marked failed without a diagnostic."
    assert caught.value.field == "error"
    assert repos.jobs.get(account.workspace_id, job["id"])["status"] == "queued"


def test_a_repeated_in_flight_state_is_refused_despite_the_declared_self_edge(studio, account):
    """BEHAVIOUR NOTE: the table declares self-edges that the guard refuses.

    ``LEGAL_TRANSITIONS["processing"]`` contains ``"processing"`` and the comment
    above the table calls the two in-flight states "the deliberate exception -- a
    polling worker re-asserts processing on every pass to record what the provider
    said, and that observation is a real event, not a fake transition." But
    ``_check_transition`` tests ``if target == status or target not in allowed``,
    so the first clause refuses it. The supported path for a repeat observation is
    ``annotate``, which appends an event without moving anything. Asserted as
    behaviour, because that is what the code does.
    """
    repos = studio["repos"]
    assert "processing" in LEGAL_TRANSITIONS["processing"]
    assert "downloading" in LEGAL_TRANSITIONS["downloading"]

    job = _new_job(studio, account)
    repos.jobs.transition(account.workspace_id, job["id"], state="submitting")
    repos.jobs.transition(account.workspace_id, job["id"], state="processing")

    with pytest.raises(ApiError) as caught:
        repos.jobs.transition(
            account.workspace_id, job["id"], state="processing", message="still running"
        )
    assert caught.value.code == CONFLICT
    assert caught.value.message == "Cannot move this job from 'processing' to 'processing'."

    # The observation path records the same fact without claiming a move.
    observed = repos.jobs.annotate(
        account.workspace_id, job["id"], message="Still processing.", provider_status="IN_QUEUE"
    )
    assert observed["status"] == "processing"
    assert observed["status_message"] == "Still processing."
    last = repos.jobs.events(job["id"])[-1]
    assert last["status"] == "processing"
    assert last["message"] == "Still processing."
    assert last["detail"]["observed"] is True
    assert last["detail"]["provider_status"] == "IN_QUEUE"



# ===========================================================================
# 4. Usage honesty
# ===========================================================================


def test_an_empty_workspace_reports_zero_records_and_no_cost(studio, account):
    """A new workspace owes nothing, and a zero here is a count, not a price.

    ``summary`` documents "an empty workspace is a zero-record summary, not an
    error", and the response has to keep no records separate from zero money: an
    invented cost of 0 would read as a free generation on an invoice.
    """
    summary = studio["repos"].usage.summary(account.workspace_id)

    assert summary["workspace_id"] == account.workspace_id
    assert summary["records"] == 0
    assert summary["records_with_cost"] == 0
    assert summary["unknown_cost_records"] == 0
    assert summary["costs"] == []
    assert summary["currencies"] == []
    assert summary["costs_are_partial"] is False
    assert summary["by_billing_party"] == {}
    assert summary["by_modality"] == {}
    # The note must not be vacuously false either: "every record stated a cost"
    # over zero records is technically true and reads as a claim that there are
    # costs. An empty workspace says it has nothing to total.
    assert "nothing to total" in summary["note"], summary["note"]


def test_a_record_with_no_amount_is_counted_as_unknown_and_flags_the_total(studio, account):
    """A number nobody observed is not a number, so the total must say so.

    ``_cost_amount`` returns None for an absent amount because "treating it as
    zero would turn "we do not know" into a free generation on an invoice", and
    ``summary`` reports those rows separately so "an operator can never mistake an
    incomplete total for a complete one."
    """
    repos = studio["repos"]
    repos.usage.record(account.workspace_id, {"billing_party": "user_provider", "modality": "image"})

    summary = repos.usage.summary(account.workspace_id)
    assert summary["records"] == 1
    assert summary["records_with_cost"] == 0
    assert summary["unknown_cost_records"] == 1
    assert summary["costs"] == []
    assert summary["costs_are_partial"] is True
    assert summary["note"] == (
        "No cost was reported for 1 of 1 records, so these totals are a lower bound "
        "rather than an invoice."
    )
    assert summary["by_billing_party"]["user_provider"] == {
        "records": 1,
        "records_with_cost": 0,
        "unknown_cost_records": 1,
    }
    assert summary["by_modality"]["image"]["unknown_cost_records"] == 1


def test_two_currencies_are_summed_separately_never_added_together(studio, account):
    """Adding dollars to credits is the same lie in a different place.

    ``summary`` refuses to mix units, so the result is a list of per-currency
    totals rather than one number. A user billed in two currencies needs both
    figures to reconcile; a combined total would be arithmetic on unlike things.
    """
    repos = studio["repos"]
    repos.usage.record(
        account.workspace_id,
        {
            "billing_party": "user_provider",
            "modality": "image",
            "actual_cost": {"currency": "USD", "amount": 1.5, "is_billable": True},
        },
    )
    repos.usage.record(
        account.workspace_id,
        {
            "billing_party": "platform",
            "modality": "video",
            "actual_cost": {"currency": "EUR", "amount": 2.0, "is_billable": True},
        },
    )
    repos.usage.record(
        account.workspace_id,
        {
            "billing_party": "user_provider",
            "modality": "image",
            "actual_cost": {"currency": "USD", "amount": 0.25, "is_billable": True},
        },
    )

    summary = repos.usage.summary(account.workspace_id)
    assert {item["currency"]: item["amount"] for item in summary["costs"]} == {
        "EUR": 2.0,
        "USD": 1.75,
    }
    assert summary["currencies"] == ["EUR", "USD"]
    assert summary["records"] == 3
    assert summary["records_with_cost"] == 3
    assert summary["unknown_cost_records"] == 0
    assert summary["costs_are_partial"] is False
    # There is no combined figure anywhere: every cost entry is one currency with
    # its own amount, and 3.75 (the sum across currencies) appears nowhere.
    assert all(set(item) == {"currency", "amount"} for item in summary["costs"])
    assert 3.75 not in [item["amount"] for item in summary["costs"]]
    # The per-party and per-modality buckets keep the same separation.
    assert summary["by_billing_party"]["user_provider"]["costs"] == {
        "USD:actual": 1.75,
    }
    assert summary["by_billing_party"]["platform"]["costs"] == {"EUR:actual": 2.0}


def test_a_non_billable_zero_contributes_no_cost_at_all(studio, account):
    """The local mock reports ``{"currency": "", "amount": 0.0, "is_billable": false}``.

    ``_cost_amount`` says why that adds nothing: "A provider that says this run
    billed nothing has reported the absence of a charge, not a charge of zero;
    writing zero into the total would make an unspent record indistinguishable
    from a free one." So the record lands in ``unknown_cost_records`` and the
    totals are unchanged.
    """
    repos = studio["repos"]
    repos.usage.record(
        account.workspace_id,
        {
            "billing_party": "user_provider",
            "modality": "image",
            "actual_cost": {"currency": "USD", "amount": 3.0, "is_billable": True},
        },
    )
    before = repos.usage.summary(account.workspace_id)

    # The payload is the one the mock adapter really emits, not a hand-written guess.
    mock_cost = MockAdapter().estimate_cost(None, None, None).to_public()
    assert mock_cost["currency"] == ""
    assert mock_cost["amount"] == 0.0
    assert mock_cost["is_billable"] is False

    repos.usage.record(
        account.workspace_id,
        {"billing_party": "user_provider", "modality": "image", "actual_cost": mock_cost},
    )
    after = repos.usage.summary(account.workspace_id)

    assert before["costs"] == [{"currency": "USD", "amount": 3.0}]
    assert after["costs"] == before["costs"]
    assert after["records"] == before["records"] + 1 == 2
    assert after["records_with_cost"] == 1
    assert after["unknown_cost_records"] == before["unknown_cost_records"] + 1 == 1
    assert after["costs_are_partial"] is True
    # Nothing is lost or double counted: every record is in exactly one bucket.
    assert after["unknown_cost_records"] + after["records_with_cost"] == after["records"]


# ===========================================================================
# 5. The audit log
# ===========================================================================


def test_audit_records_append_and_list_returns_the_newest_first(studio, account):
    """An operator reads the tail of the trail, so the newest entry comes first.

    ``list`` orders by ``created_at DESC, rowid DESC``: an audit screen that made
    somebody scroll to the bottom to see what just happened would be a screen
    nobody uses during an incident.
    """
    repos = studio["repos"]
    first = repos.audit.record(
        account.workspace_id,
        account.user_id,
        "asset.delete",
        target_type="asset",
        target_id="asset-1",
        request_id="req-1",
        ip_hash="hash-value",
        detail={"reason": "user request"},
    )
    second = repos.audit.record(
        account.workspace_id, account.user_id, "project.archive", target_id="project-1"
    )

    assert first["action"] == "asset.delete"
    assert first["target_type"] == "asset" and first["target_id"] == "asset-1"
    assert first["request_id"] == "req-1"
    # The ip is stored as given: hashing is the caller's job, this layer could not
    # tell a hash from an address and does not pretend to.
    assert first["ip_hash"] == "hash-value"
    assert first["detail"] == {"reason": "user request"}
    assert second["action"] == "project.archive"

    listed = repos.audit.list(account.workspace_id)
    assert [row["action"] for row in listed] == ["project.archive", "asset.delete"]
    assert len(listed) == 2, "recording appended rather than replacing"

    # Another tenant's trail is not this tenant's trail.
    other = _other_account(studio)
    assert repos.audit.list(other.workspace_id) == []


def test_the_audit_repository_offers_no_way_to_change_or_remove_an_entry():
    """An audit trail with an edit history is not an audit trail.

    The class says so itself: "Nothing in this class updates or deletes a row.
    That is not a limitation to be fixed later: an audit trail with an edit
    history is not an audit trail." Asserting the method is absent is the only
    form of that promise a test can keep.
    """
    assert not hasattr(AuditRepository, "update")
    assert not hasattr(AuditRepository, "delete")
    assert not hasattr(AuditRepository, "soft_delete")
    assert sorted(name for name in dir(AuditRepository) if not name.startswith("_")) == [
        "TABLE",
        "list",
        "record",
    ]


def test_an_audit_entry_needs_an_action_and_a_workspace(studio, account):
    """An unlabelled entry answers no question, and an unscoped one cannot be found.

    ``record`` refuses both up front -- "an unknown action (an empty one), because
    an unlabelled entry answers no question; and an empty workspace id, since an
    entry nobody can find is not an audit."
    """
    repos = studio["repos"]
    with pytest.raises(ApiError) as blank_action:
        repos.audit.record(account.workspace_id, account.user_id, "   ")
    assert blank_action.value.code == VALIDATION_FAILED
    assert blank_action.value.field == "action"

    with pytest.raises(ApiError) as blank_scope:
        repos.audit.record("", account.user_id, "asset.delete")
    assert blank_scope.value.code == VALIDATION_FAILED
    assert blank_scope.value.field == "workspace_id"

    assert repos.audit.list(account.workspace_id) == []

