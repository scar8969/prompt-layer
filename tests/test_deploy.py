"""The shape the deployment needs, asserted.

Every rule here was found by running `vercel build` against the real project, not
by reading the docs: the first two were hard build failures, and the third was
found by looking at what the build actually published.
"""

import fnmatch
import json
import os

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(name):
    with open(os.path.join(REPO, name), encoding="utf-8") as fh:
        return fh.read()


def _lines(name):
    return [line.strip() for line in _read(name).splitlines()
            if line.strip() and not line.strip().startswith("#")]


def test_the_framework_preset_is_pinned_to_none():
    """A detected preset beats file-based functions, and then the build fails.

    requirements.txt in the root is enough for Vercel to decide this is a single
    Python application: it goes looking for one WSGI/ASGI entrypoint (app.py,
    main.py, server.py...) and refuses to build --

        No python entrypoint found in default locations, but found potential
        entrypoints: api/compile.py (variable: handler), ...

    Which preset applies is a project setting in the dashboard; `framework: null`
    is "Other", and it is read from the repo, so the deployment does not depend on
    how somebody happened to import the project.
    """
    config = json.loads(_read("vercel.json"))
    assert config["framework"] is None, config.get("framework")


def test_a_specific_function_pattern_is_listed_before_the_wildcard():
    """A pattern that another pattern already matched is a build error.

    With `api/*.py` first it claims every file, and the `api/compile.py` entry under
    it matches nothing --

        The pattern "api/compile.py" defined in `functions` doesn't match any
        Serverless Functions inside the `api` directory.

    So the one file that needs a different timeout is listed first. Order is the
    only thing that decides this; the values would merge either way.
    """
    keys = list(json.loads(_read("vercel.json"))["functions"])
    for index, key in enumerate(keys):
        if "*" not in key:
            continue
        prefix, _, suffix = key.partition("*")
        shadowed = [later for later in keys[index + 1:]
                    if later.startswith(prefix) and later.endswith(suffix)]
        assert shadowed == [], f"{key} comes before the specific {shadowed} it matches"


def test_every_function_is_covered_by_a_pattern():
    """A new endpoint must not quietly fall outside the configured patterns."""
    names = sorted(n for n in os.listdir(os.path.join(REPO, "api")) if n.endswith(".py"))
    assert names, "no functions found"
    patterns = list(json.loads(_read("vercel.json"))["functions"])
    for name in names:
        path = f"api/{name}"
        assert any(fnmatch.fnmatch(path, pattern) for pattern in patterns), path


def test_the_vendor_schemas_are_kept_out_of_the_function_bundle():
    """The fal schemas are large and are build-time input, not runtime input."""
    for pattern, options in json.loads(_read("vercel.json"))["functions"].items():
        excluded = options.get("excludeFiles", "")
        assert "promptlayer/vendor/**" in excluded, pattern
        assert "tests/**" in excluded and "scripts/**" in excluded, pattern


def test_the_dev_only_paths_are_not_published():
    """Vercel serves the project directory as static files, as-is.

    Without .vercelignore the whole root goes up and is downloadable: dev.py, the
    test suite, the parsers, the design notes and the screenshots. The local
    emulator already refuses those paths (dev.py serves an allowlist), so this is
    the deployed half of the same rule -- otherwise what you check locally is not
    what you shipped.
    """
    ignored = set(_lines(".vercelignore"))
    for entry in ("tests/", "scripts/", "docs/", "dev.py", "requirements-dev.txt",
                  ".python-version", "pyproject.toml", "uv.lock"):
        assert entry in ignored, f"{entry} would be published at the deployment's URL"


def test_the_files_the_cli_writes_are_kept_out_of_the_repo():
    """A local `vercel build` writes .python-version, pyproject.toml and uv.lock.

    They describe a project that does not exist (name "app", no dependencies), and a
    pyproject.toml in the root is itself one of the files that makes Vercel detect a
    Python framework preset. They are build output.
    """
    ignored = set(_lines(".gitignore"))
    for entry in (".python-version", "pyproject.toml", "uv.lock", ".vercel/", ".env*"):
        assert entry in ignored, entry
