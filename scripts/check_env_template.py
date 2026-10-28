#!/usr/bin/env python3
"""Check that .env.example holds placeholders and no real credential.

The file is committed: docs/SETUP.md, docs/DEPLOYMENT.md and docs/PLAN.md all
tell the reader to copy it, so it has to ship. It is also published as a
static file at the deployment's URL, so a real value in it would be a
disclosure.

Run before committing any change to .env.example:

    python scripts/check_env_template.py .env.example
"""

import re
import sys

#: A value that is a placeholder rather than a real setting.
PLACEHOLDER = re.compile(
    r"^(<[^>]*>|\{[^}]*\}|\[[^\]]*\]"
    r"|replace[-_]?me.*"
    r"|your[-_].*"
    r"|changeme.*"
    r"|change[-_]me.*"
    r"|example.*"
    r"|placeholder.*"
    r"|xxx+.*"
    r"|\.\.\..*"
    r"|\*+.*"
    r"|https?://(localhost|127\.0\.0\.1)"
    r"|postgresql://postgres(:postgres)?@"
    # A relative path into the data directory: "./var/studio/studio.db".
    r"|\./[a-z0-9_/\.\-]+"
    # A comma-separated list of host names, no scheme and no secret.
    r"|[a-z0-9_:\.\-]+(,[a-z0-9_:\.\-]+)*$)",
    re.IGNORECASE,
)

#: The values that must never appear, whatever their length.
NEVER = re.compile(
    r"sk-(?:proj-|ant-|or-)?[A-Za-z0-9]{20,}"
    r"|sk_live_[A-Za-z0-9]{16,}"
    r"|AIza[0-9A-Za-z_\-]{30,}"
    r"|hf_[A-Za-z0-9]{20,}"
    r"|gh[pousr]_[A-Za-z0-9]{20,}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----"
    r"|eyJ[A-Za-z0-9_\-]{40,}"
)


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else ".env.example"
    try:
        text = open(path, encoding="utf-8").read()
    except OSError as exc:
        print(f"could not read {path}: {exc}")
        return 2

    problems = []
    for number, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if "=" not in stripped or stripped.startswith("#"):
            continue
        name, _, value = stripped.partition("=")
        value = value.strip().strip('"').strip("'")
        if not value:
            continue
        if NEVER.search(value):
            problems.append((number, name.strip(), "a value that looks like a real credential"))
        elif len(value) >= 20 and not PLACEHOLDER.match(value):
            problems.append((number, name.strip(), "a long value that is not a placeholder"))

    if problems:
        print(f"{path} has {len(problems)} value(s) that must not be committed:")
        for number, name, why in problems:
            print(f"  line {number}: {name} — {why}")
        return 1

    print(f"{path} holds placeholders only — safe to commit and to publish.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
