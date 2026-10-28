"""Build the Studio client for the deployed mount, and check it before shipping.

Why a script rather than a build hook
-------------------------------------
Vercel would need a Node build step (install, `tsc`, `vite build`) in front of a
Python function. That works, but it makes the deployment depend on an npm
toolchain and a lockfile that the Prompt Layer side does not otherwise use, and a
failed frontend build would take down the API with it.

So the built client is committed. `web/dist` is normally ignored -- build output
does not belong in a repository -- and it is admitted deliberately here for one
reason: the deployment needs it and has no build step. This script is what keeps
that honest:

  * it rebuilds with the mount prefix the deployment uses;
  * it fails loudly if the prefix is not in the output;
  * it fails if the output refers to an API path the backend does not route.

Run it before committing a change to web/src:

    python scripts/build_client.py
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB = os.path.join(REPO, "web")
DIST = os.path.join(WEB, "dist")

#: The prefix the deployed function is mounted at. Keep in sync with the rewrite
#: in vercel.json and PREFIX in api/studio.py.
PREFIX = "/api/studio"


def run(argv, cwd):
    print("$ %s" % " ".join(argv))
    result = subprocess.run(argv, cwd=cwd, shell=(os.name == "nt"))
    if result.returncode != 0:
        raise SystemExit("failed: %s" % " ".join(argv))


def main() -> int:
    if not os.path.isdir(os.path.join(WEB, "node_modules")):
        raise SystemExit(
            "web/node_modules is missing; run `npm install` in web/ first"
        )

    print("typechecking")
    run(["npx", "tsc", "--noEmit"], WEB)

    print("building with VITE_STUDIO_API_PREFIX=%s" % PREFIX)
    env = dict(os.environ, VITE_STUDIO_API_PREFIX=PREFIX)
    result = subprocess.run(
        ["npx", "vite", "build"], cwd=WEB, env=env, shell=(os.name == "nt"),
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(result.stdout[-2000:])
        print(result.stderr[-2000:])
        raise SystemExit("vite build failed")
    for line in result.stdout.splitlines()[-6:]:
        print("  " + line)

    bundle = [name for name in os.listdir(os.path.join(DIST, "assets"))
              if name.endswith(".js")]
    if not bundle:
        raise SystemExit("no javascript bundle was produced")

    text = open(os.path.join(DIST, "assets", bundle[0]), encoding="utf-8",
                errors="replace").read()

    if PREFIX not in text:
        raise SystemExit(
            "the API prefix %r is not in the built bundle, so the client would "
            "call /api/... on the deployment and 404" % PREFIX
        )
    print("prefix present in the bundle: %s" % PREFIX)

    # Every route the client calls must exist on the backend. The route table
    # lives in studio/routes/, so this compares the bundle's paths against it.
    sys.path.insert(0, REPO)
    from studio.app import build_app  # noqa: E402

    called = set(re.findall(r"['\"`](/api/[a-z0-9_/\-]*)['\"`]", text))
    # The prefix constant itself appears as a bare string; it is not a call.
    called.discard(PREFIX)
    routes = set()
    for line in open(os.path.join(REPO, "studio", "routes", "__init__.py"), encoding="utf-8"):
        routes.update(re.findall(r'"(/api/[^"]+)"', line))
    for path in sorted(os.path.join(root, name)
                       for root, _d, names in os.walk(os.path.join(REPO, "studio", "routes"))
                       for name in names if name.endswith(".py")):
        for line in open(path, encoding="utf-8"):
            routes.update(re.findall(r'router\.add\((?:[^,]+,\s*)?"(/api/[^"]+)"', line))

    def known(client_path: str) -> bool:
        # A concrete id in the bundle is a template; compare shapes instead.
        shape = re.sub(r"/[0-9a-f]{8,}", "/{id}", client_path)
        shape = shape.rstrip("/")
        for route in routes:
            candidate = re.sub(r"\{[^}]+\}", "{id}", route).rstrip("/")
            if shape == candidate:
                return True
        return False

    unknown = sorted(path for path in called if not known(path))
    if unknown:
        raise SystemExit(
            "the client calls paths the backend does not route: %s" % unknown
        )
    print("every client path matches a backend route (%d checked)" % len(called))
    print("\ndone. web/dist is ready to commit.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
