#!/usr/bin/env python3
"""Apply .studio_env.sh to the current process and exec python -m studio.app.

Run from the repo root:
    python scripts/run_studio.py --port 8091
"""

from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
from pathlib import Path


def main() -> int:
    repo = Path(__file__).resolve().parent.parent
    env_file = repo / ".studio_env.sh"
    if not env_file.exists():
        sys.stderr.write(
            f"Missing {env_file}. Run `python scripts/gen_studio_env.py > .studio_env.sh` first.\n"
        )
        return 2

    for raw in env_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        # ``export KEY="value with spaces"`` must be parsed as a shell would:
        # split on the first ``=`` to separate key from value, then unquote the
        # value. ``shlex.split`` cannot do this on its own because it refuses
        # to split on ``=`` when the value is quoted.
        if "=" not in line:
            sys.stderr.write(f"Skipping malformed line: {raw}\n")
            continue
        name, _, raw_value = line.partition("=")
        name = name.strip()
        raw_value = raw_value.strip()
        try:
            value = shlex.split(raw_value, posix=True)[0]
        except (ValueError, IndexError):
            value = raw_value.strip("\"'")
        if name:
            os.environ[name] = value

    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8091)
    parser.add_argument("--no-workers", action="store_true", help="Skip starting the in-process worker threads.")
    args, passthrough = parser.parse_known_args()

    cmd = [sys.executable, "-m", "studio.app", "--port", str(args.port)]
    if args.no_workers:
        cmd.append("--no-workers")
    cmd.extend(passthrough)

    print(">>", " ".join(cmd))
    return subprocess.call(cmd, cwd=str(repo))


if __name__ == "__main__":
    sys.exit(main())