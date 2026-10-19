"""What a Studio process needs before it can serve anything, named.

Why this is a separate module
-----------------------------
`studio/config.py` already knows which *variables* a feature needs, but a
deployment can fail at startup for reasons that are not a variable: a database
URL that points at a server that does not exist, a schema that cannot be created,
a driver that is not installed. Those all surface as an opaque driver exception
("OperationalError"), and a person trying to work out why the site is down cannot
act on a class name.

So this answers one question in plain words: what is missing, in the order it
must be fixed. The serverless entrypoint calls it when startup raises, which turns
a generic 500 into a list.

Deliberately shallow
--------------------
It reads configuration and the environment only. It does not connect to anything
-- that is what failed -- so it can be called safely from an error handler
without a chance of hanging on an unreachable host.
"""

from __future__ import annotations

import os
from typing import List

#: Variables a serverless deployment cannot run without, with why.
REQUIRED_ON_A_DEPLOYMENT = (
    (
        "STUDIO_DATABASE_URL",
        "A serverless filesystem is discarded between invocations, so SQLite "
        "cannot hold the queue or the library.",
    ),
    (
        "STUDIO_ENCRYPTION_KEY",
        "Provider credentials are encrypted at rest; without the key they cannot "
        "be written or read.",
    ),
    (
        "STUDIO_AUTH_SECRET",
        "Sessions are signed with it; without it nobody can stay signed in.",
    ),
)

#: Storage settings, required as a group when the backend is s3.
STORAGE_ON_A_DEPLOYMENT = (
    "STUDIO_STORAGE_ENDPOINT",
    "STUDIO_STORAGE_BUCKET",
    "STUDIO_STORAGE_ACCESS_KEY",
    "STUDIO_STORAGE_SECRET_KEY",
)


def missing_for_startup(environ: dict = None) -> List[str]:
    """Variable names that are absent right now, in the order to fix them.

    An empty list means "everything this function can check is present" -- it is
    not a promise that startup will succeed, only that a missing variable is not
    the reason it did not.
    """
    env = os.environ if environ is None else environ
    missing = [name for name, _why in REQUIRED_ON_A_DEPLOYMENT
               if not (env.get(name) or "").strip()]
    if (env.get("STUDIO_STORAGE_BACKEND") or "").strip().lower() == "s3":
        missing.extend(name for name in STORAGE_ON_A_DEPLOYMENT
                       if not (env.get(name) or "").strip())
    return missing


def explain(environ: dict = None) -> List[dict]:
    """The same information with the reason attached, for a diagnostic page."""
    env = os.environ if environ is None else environ
    return [
        {"name": name, "why": why}
        for name, why in REQUIRED_ON_A_DEPLOYMENT
        if not (env.get(name) or "").strip()
    ]
