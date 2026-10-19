#!/usr/bin/env python3
"""Audit log builder.

Reads every file in the target paths, classifies it, and produces a
short, factual line per file. The audit report is then assembled
manually from this output.
"""

import os
import re
import sys

if len(sys.argv) < 2:
    print("usage: audit_log.py <path> [<path> ...]")
    sys.exit(1)


def classify(path: str) -> str:
    p = path.replace("\\", "/")
    if "/tests/" in p or p.startswith("tests/") or "/test_" in p:
        return "test"
    if p.endswith(".md"):
        return "doc"
    if p.endswith(".sql"):
        return "migration"
    if p.endswith(".tsx") or p.endswith(".ts"):
        return "client"
    if p.endswith(".py"):
        return "python"
    return "other"


def quick_observations(text: str) -> list:
    obs = []
    # Check for TODO/FIXME/XXX/HACK markers
    for marker in ("TODO", "FIXME", "XXX", "HACK", "STUB", "PLACEHOLDER"):
        for m in re.finditer(rf"\\b{marker}\\b", text):
            line_no = text[: m.start()].count("\n") + 1
            line = text.splitlines()[line_no - 1].strip()
            obs.append(f"L{line_no}: {marker} -> {line[:120]}")
    # Look for stub patterns: pass-only functions, ellipsis bodies
    if re.search(r"^\\s*pass\\s*$", text, re.MULTILINE):
        obs.append("contains `pass` (potential stub)")
    # Look for `print(...)` calls in non-script code (smell)
    py_or_client = re.search(r"\\.(py|tsx?|sql)$", text)
    if py_or_client and re.search(r"\\bprint\\(", text):
        # ignore scripts/ and docs/ — those are fine
        if "scripts/" not in text and "docs/" not in text and not text.endswith("README.md"):
            obs.append("contains print() in non-script context")
    # Look for `assert` in production code
    if re.search(r"^\\s*assert\\s+", text, re.MULTILINE) and "/tests/" not in p and "tests/" not in p:
        if p.endswith(".py"):
            obs.append("uses bare assert (stripped under -O)")
    # Look for broad except
    if re.search(r"except\\s*:", text):
        obs.append("bare except")
    # Look for hardcoded secrets
    if re.search(r"sk-[A-Za-z0-9]{20,}|AIzaSy[A-Za-z0-9]{20,}|hf_[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{20,}", text):
        obs.append("POSSIBLE HARDCODED SECRET")
    # Look for eval/exec
    if re.search(r"\b(eval|exec)\(", text):
        obs.append("uses eval/exec")
    return obs


total = 0
for root in sys.argv[1:]:
    for dp, dirs, files in os.walk(root):
        for f in files:
            full = os.path.join(dp, f)
            if f.startswith(".") or f == "__pycache__":
                continue
            total += 1
            rel = os.path.relpath(full, ".")
            try:
                with open(full, "r", encoding="utf-8", errors="replace") as fh:
                    text = fh.read()
            except OSError as exc:
                print(f"??  {rel}  unreadable: {exc}")
                continue
            lines = text.count("\n") + 1
            obs = quick_observations(text)
            if obs:
                print(f"{classify(rel):8s}  {lines:5d}  {rel}")
                for o in obs[:5]:
                    print(f"             | {o}")
            else:
                print(f"{classify(rel):8s}  {lines:5d}  {rel}")
print(f"----")
print(f"{total} files scanned")
