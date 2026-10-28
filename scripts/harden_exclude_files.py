#!/usr/bin/env python3
"""Apply the same credential and runtime-state exclusions to every function.

Two layers ship a file to a deployment and they are configured separately:

  * the static layer, published at the domain root -- .vercelignore;
  * each function's bundle -- that function's excludeFiles in vercel.json.

A file excluded from one and not the other still ships. This applies the same
guard to every function, so a new function cannot forget one, and so the two
layers stay in step.

Run it after adding a function to vercel.json:

    python scripts/harden_exclude_files.py
"""

from __future__ import annotations

import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VERCEL = os.path.join(REPO, "vercel.json")

#: Patterns every function must exclude, whatever else it excludes.
#:
#: ``var/**`` is the important one: that directory holds the SQLite database and
#: the generated encryption and auth key files, and the encryption key opens
#: every sealed provider credential in the database.
REQUIRED = [
    "var/**",
    "web/node_modules/**",
    ".env*",
    ".studio_env.sh",
    "prompt-studio.env",
    ".pytest_cache/**",
    "**/__pycache__/**",
    "**/test_*.py",
    "tests/**",
    "scripts/**",
    "docs/**",
    "requirements-dev.txt",
    "dev.py",
]


def main() -> int:
    with open(VERCEL, encoding="utf-8") as fh:
        config = json.load(fh)

    for pattern, options in config["functions"].items():
        raw = options.get("excludeFiles", "").strip()
        inner = raw[1:-1] if raw.startswith("{") and raw.endswith("}") else raw
        parts = [p.strip() for p in inner.split(",") if p.strip()]

        for required in REQUIRED:
            if required not in parts:
                parts.append(required)

        # A stable order, so the file does not churn between runs.
        options["excludeFiles"] = "{" + ",".join(sorted(set(parts))) + "}"

    with open(VERCEL, "w", encoding="utf-8") as fh:
        json.dump(config, fh, indent=2)
        fh.write("\n")

    print("rewrote excludeFiles for:")
    for pattern in config["functions"]:
        print(f"  {pattern}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
