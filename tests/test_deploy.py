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

    pyproject.toml and uv.lock are deliberately not on this list. They used to
    be build artifacts the CLI wrote; now the platform reads the lock to
    install the function's dependencies, so they have to be uploaded. See
    test_the_deployed_functions_have_their_dependencies.
    """
    ignored = set(_lines(".vercelignore"))
    for entry in ("tests/", "scripts/", "docs/", "dev.py", "requirements-dev.txt",
                  ".python-version", "var/", ".studio_env.sh", "prompt-studio.env"):
        assert entry in ignored, f"{entry} would be published at the deployment's URL"


def test_the_files_the_cli_writes_are_kept_out_of_the_repo():
    """A local `vercel build` writes .python-version.

    It is a one-line version pin with nothing in it, and the platform reads it
    to choose the runtime, so it is not committed -- but it does have to be in
    the upload, which the test above covers.

    pyproject.toml and uv.lock used to be on this list. They were build output
    then, describing a project called "app" with no dependencies. Now they are
    the deployment's dependency list: the platform reads the lock to install
    cryptography, psycopg and psycopg-pool, and without them the built
    function ships pip and nothing else. So they are committed, and they are
    uploaded.
    """
    ignored = set(_lines(".gitignore"))
    for entry in (".python-version", ".vercel/", ".env*"):
        assert entry in ignored, entry


def test_a_function_keeps_what_it_reads_at_runtime():
    """A function that opens a path must keep that path in its bundle.

    Vercel bundles every project file reachable at build time, minus the ones the
    function's ``excludeFiles`` pattern removes. A pattern that removes something
    the handler opens is a 404 in production that no local test can see, because
    locally the whole working tree is present.

    This bit us once: ``api/studio.py`` serves the built React client out of
    ``web/dist``, and its excludeFiles listed ``web/dist/**``. Every page request
    would have 404'd on the deployment while working perfectly on this machine.

    Only the *first* matching pattern customises a function. Vercel rejects a
    pattern that a preceding pattern already matched, so the wildcard ``api/*.py``
    never applies to a file that has its own entry -- see
    test_a_specific_function_pattern_is_listed_before_the_wildcard.
    """
    #: What each function reads at runtime, and therefore must ship with it.
    required = {
        "api/studio.py": ("studio/**", "web/dist/**", "promptlayer/**"),
        "api/studio_cron.py": ("studio/**",),
        "api/compile.py": ("promptlayer/**",),
        "api/preflight.py": ("promptlayer/**",),
        "api/improve.py": ("promptlayer/**",),
        "api/models.py": ("promptlayer/**",),
        "api/presets.py": ("promptlayer/**",),
        "api/storyboard.py": ("promptlayer/**",),
        "api/reference.py": ("promptlayer/**",),
        "api/video.py": ("promptlayer/**",),
    }
    functions = json.loads(_read("vercel.json"))["functions"]
    for name, needs in required.items():
        # The first pattern that matches is the one that customises this file.
        excluded = ""
        for pattern, options in functions.items():
            if pattern == name or fnmatch.fnmatch(name, pattern):
                excluded = options.get("excludeFiles", "")
                break
        else:
            raise AssertionError(f"{name} has no entry in vercel.json")
        for need in needs:
            assert need not in excluded, (
                f"{name} reads {need} at runtime but its excludeFiles removes "
                f"it, so the deployed function would 404"
            )


def test_the_root_serves_one_application_and_not_a_second_page():
    """There is one product, so there is one front door.

    The repository used to ship two: a static preflight page at `/` and the
    React studio at `/studio`. They shared a dataset but not a session, a
    navigation or a model registry, and the static page's tools were invisible
    from the studio. The page is gone and every path that is not the API is a
    request for the built client, which is the same application the API belongs
    to. A second page reappearing at the root is a regression, not a feature.
    """
    assert not os.path.exists(os.path.join(REPO, "index.html")), (
        "index.html is a second front door; the product is the React app in web/dist"
    )

    rewrites = json.loads(_read("vercel.json"))["rewrites"]
    sources = [entry["source"] for entry in rewrites]
    assert "/((?!api/).*)" in sources, (
        "every non-API path must reach the application that serves the client"
    )
    catch_all = next(entry for entry in rewrites if entry["source"] == "/((?!api/).*)")
    assert catch_all["destination"] == "/api/studio"

    # The function that serves the page also serves the layer dataset now, so it
    # has to ship the package rather than being told to exclude it.
    excluded = json.loads(_read("vercel.json"))["functions"]["api/studio.py"]["excludeFiles"]
    assert "promptlayer/**" not in excluded, (
        "the studio function reads promptlayer/ for the Prompt Lab routes"
    )


def test_the_studio_client_is_committed_because_the_deployment_has_no_build_step():
    """web/dist is a build output that is committed on purpose.

    The platform runs Python functions and has no Node build step, so without the
    built client in the bundle a deployed Studio has an API and no page. That is
    why web/dist is admitted by .gitignore, and why the function that serves it
    must keep it.
    """
    ignored = set(_lines(".gitignore"))
    assert "web/dist/" not in ignored, (
        "web/dist is the deployment's client; ignoring it means no page"
    )
    assert "web/node_modules/" in ignored, "node_modules is never committed"


def test_a_local_env_file_is_never_published():
    """The template is published; a real .env is not.

    Vercel serves the project directory as-is, so an .env sitting on the machine
    that ran `vercel build` would be downloadable from the deployment's URL. The
    template is a list of variables with placeholders only, and three documents
    tell the reader to copy it, so it stays.
    """
    ignored = set(_lines(".vercelignore"))
    assert ".env*" in ignored, "a local .env would be published at the deployment's URL"
    assert "!.env.example" in ignored, "the template should still be published"

    gitignored = set(_lines(".gitignore"))
    assert ".env*" in gitignored, "a local .env must never be committed"
    assert "!.env.example" in gitignored, (
        "docs/SETUP.md tells the reader to copy .env.example, so it must be tracked"
    )


def test_the_runtime_state_is_never_published():
    """var/ holds the encryption key, the auth secret and the database.

    This is the worst of the three ways a file can leak, because it is not one
    file but a directory of them. A build on a machine that had run Studio
    locally publishes:

        var/studio/.studio-encryption-key   opens every sealed provider key
        var/studio/.studio-auth-secret      signs sessions and CSRF tokens
        var/studio/studio.db                 every user, email and password hash

    Vercel serves the project directory as-is at the domain root, so
    /var/studio/.studio-encryption-key would be a public URL. Both layers must
    exclude it: the static layer (.vercelignore) and every function bundle
    (excludeFiles).
    """
    static = set(_lines(".vercelignore"))
    assert "var/" in static, (
        "var/ holds the encryption key and the database; it must not be published"
    )
    assert ".studio_env.sh" in static, "the generated keys in plaintext must not be published"
    assert "prompt-studio.env" in static, "the env file naming the database must not be published"

    functions = json.loads(_read("vercel.json"))["functions"]
    for pattern, options in functions.items():
        excluded = options.get("excludeFiles", "")
        assert "var/**" in excluded, (
            f"{pattern} ships var/ in its bundle, which holds the encryption key"
        )


def test_every_function_excludes_the_same_credential_paths():
    """A new function must not be able to forget the guard.

    Two layers ship a file to a deployment and they are configured separately:
    the static layer (.vercelignore) and each function's bundle (excludeFiles).
    A credential excluded from one and not the other still ships, so every
    function carries the same set of credential and runtime-state patterns.
    """
    required = (
        "var/**",
        ".env*",
        ".studio_env.sh",
        "prompt-studio.env",
        "web/node_modules/**",
    )
    functions = json.loads(_read("vercel.json"))["functions"]
    for pattern, options in functions.items():
        excluded = options.get("excludeFiles", "")
        for need in required:
            assert need in excluded, f"{pattern} does not exclude {need}"


def test_the_deployed_functions_have_their_dependencies():
    """The platform prefers uv.lock over requirements.txt when both exist.

    requirements.txt is empty on purpose -- it is what stops Vercel detecting a
    framework preset. But that meant the built function bundle shipped pip and
    nothing else: studio/crypto.py raises EncryptionUnavailable when a
    credential is stored, and studio/db.py raises MigrationError when
    STUDIO_DATABASE_URL points at Postgres. Both packages are declared in
    pyproject.toml instead, which is what uv.lock is built from.

    That makes pyproject.toml and uv.lock the deployment's dependency list, so
    they have to be both tracked and uploaded. Excluding them from the upload
    would bring the same failure back, silently.
    """
    pyproject = _read("pyproject.toml")
    for package in ("cryptography", "psycopg", "psycopg-pool"):
        assert package in pyproject, f"{package} is not declared in pyproject.toml"

    lock = _read("uv.lock")
    for package in ("cryptography", "psycopg", "psycopg-pool"):
        assert package in lock, f"{package} is not in uv.lock, so it would not be bundled"

    # The lock must not be excluded from the upload, or nothing is installed.
    uploaded = set(_lines(".vercelignore"))
    assert "pyproject.toml" not in uploaded, (
        "the platform reads pyproject.toml and uv.lock to install dependencies"
    )
    assert "uv.lock" not in uploaded, (
        "the platform reads uv.lock to install dependencies"
    )

    # And they must be tracked, or the deployment has no dependency list.
    tracked = set(_lines(".gitignore"))
    assert "pyproject.toml" not in tracked, "the dependency list must be committed"
    assert "uv.lock" not in tracked, "the dependency lock must be committed"
