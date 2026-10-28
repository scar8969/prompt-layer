#!/usr/bin/env python3
"""Remove the session scratch files from the repo root.

Only touches files this session's tooling produced. Every path is checked
against git first: a tracked file is never deleted.
"""

import os
import shutil
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: Session scratch. Each of these is produced by a script in scripts/ or by
#: an interactive shell, and none of them is part of the project.
SCRATCH_FILES = [
    # this session
    "cleanup_status.txt",
    "cleanup_report.txt",
    "diff_stat.txt",
    # earlier sessions
    "audit_studio.txt",
    "audit_tests.txt",
    "audit_web.txt",
    "gitignore_diff.txt",
    "git_branch.txt",
    "git_log.txt",
    "git_porcelain.txt",
    "git_remote.txt",
    "git_status.txt",
    "out.txt",
    "smoke_out.txt",
]

#: Empty leftover trees. ``undefined/temp/repo-verify`` came from a path
#: segment that was literally the string "undefined"; ``storage/`` is an
#: empty object-store root left by a run that never wrote to it.
SCRATCH_DIRS = [
    "undefined",
    "storage",
]


def tracked_paths():
    out = subprocess.run(
        ["git", "ls-files"], cwd=REPO, capture_output=True, text=True, check=False
    )
    return set(out.stdout.splitlines())


def main() -> int:
    tracked = tracked_paths()
    removed = []
    skipped = []

    for name in SCRATCH_FILES:
        path = os.path.join(REPO, name)
        if not os.path.exists(path):
            continue
        if name in tracked:
            skipped.append((name, "TRACKED — not deleted"))
            continue
        try:
            os.remove(path)
            removed.append(name)
        except OSError as exc:
            skipped.append((name, str(exc)))

    for name in SCRATCH_DIRS:
        path = os.path.join(REPO, name)
        if not os.path.isdir(path):
            continue
        if any(t.startswith(name + "/") for t in tracked):
            skipped.append((name + "/", "has tracked files — not deleted"))
            continue
        files = [os.path.join(dp, f) for dp, _, fs in os.walk(path) for f in fs]
        try:
            shutil.rmtree(path)
            removed.append(f"{name}/ ({len(files)} files)")
        except OSError as exc:
            skipped.append((name + "/", str(exc)))

    print("removed:")
    for name in removed:
        print("  -", name)
    if skipped:
        print("skipped:")
        for name, why in skipped:
            print(f"  - {name}: {why}")
    if not removed:
        print("nothing to remove")
    return 0


if __name__ == "__main__":
    sys.exit(main())
