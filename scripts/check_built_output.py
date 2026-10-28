#!/usr/bin/env python3
"""Check a built Vercel output for anything that must not be published.

Three things are checked against every function bundle and against the static
layer:

  * credential-bearing files (.env, the generated key file, the database);
  * the runtime state directory (var/), which holds the encryption key that
    opens every sealed provider credential;
  * the third-party packages the function imports at runtime.

Run `vercel build --prod --yes` first. This reads the built output rather
than the configuration, so it reports what would actually be deployed.
"""

import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT = os.path.join(REPO, ".vercel", "output")

#: A path that must never appear in a deployment.
FORBIDDEN_PREFIXES = (
    "var/",
    ".env",
    ".studio_env.sh",
    "prompt-studio.env",
    ".pytest_cache/",
)

#: The packages each function needs at runtime.
REQUIRED_PACKAGES = {
    "studio.func": ("psycopg", "cryptography"),
    "studio_cron.func": ("psycopg", "cryptography"),
    "compile.func": (),
    "preflight.func": (),
    "improve.func": (),
    "models.func": (),
    "presets.func": (),
    "storyboard.func": (),
    "reference.func": (),
    "video.func": (),
    "health.func": (),
}


def forbidden(path: str) -> bool:
    return any(path.startswith(prefix) for prefix in FORBIDDEN_PREFIXES)


def main() -> int:
    if not os.path.isdir(OUTPUT):
        print("no build output; run `vercel build --prod --yes` first")
        return 2

    problems = 0

    functions_dir = os.path.join(OUTPUT, "functions", "api")
    for name in sorted(os.listdir(functions_dir)):
        config_path = os.path.join(functions_dir, name, ".vc-config.json")
        if not os.path.isfile(config_path):
            continue
        with open(config_path, encoding="utf-8") as fh:
            config = json.load(fh)
        shipped = list(config.get("filePathMap", {}).keys())

        leaks = sorted(p for p in shipped if forbidden(p))
        missing = [
            package
            for package in REQUIRED_PACKAGES.get(name, ())
            if not any(p.startswith("_vendor/" + package) for p in shipped)
        ]

        status = "OK"
        if leaks or missing:
            status = "PROBLEM"
            problems += 1
        print(f"{status:8s} {name:18s} files={len(shipped):5d}")
        for path in leaks:
            print(f"           LEAK     {path}")
        for package in missing:
            print(f"           MISSING  dependency {package}")

    static_dir = os.path.join(OUTPUT, "static")
    static_files = []
    for dirpath, _dirnames, filenames in os.walk(static_dir):
        for filename in filenames:
            static_files.append(
                os.path.relpath(os.path.join(dirpath, filename), static_dir).replace(os.sep, "/")
            )
    static_leaks = sorted(p for p in static_files if forbidden(p))
    if static_leaks:
        problems += 1
    print(f"{'PROBLEM' if static_leaks else 'OK':8s} {'static':18s} files={len(static_files):5d}")
    for path in static_leaks:
        print(f"           LEAK     {path}")

    print()
    print(f"{problems} problem(s)" if problems else "the built output publishes nothing it should not")
    return 0


if __name__ == "__main__":
    sys.exit(main())
