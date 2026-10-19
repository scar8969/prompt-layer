#!/usr/bin/env python3
"""Quick audit summary: how much code, broken down by area."""
import os
import sys

if len(sys.argv) < 2:
    print("usage: audit_summary.py <path>")
    sys.exit(1)

root = sys.argv[1]
counts = {}
total = 0
for dp, dirs, files in os.walk(root):
    rel = os.path.relpath(dp, root)
    for f in files:
        if f.endswith(".py") or f.endswith(".sql") or f.endswith(".tsx") or f.endswith(".ts") or f.endswith(".md"):
            counts.setdefault(rel, 0)
            counts[rel] += 1
            total += 1
for d, n in sorted(counts.items()):
    print(f"{n:4d}  {d}")
print("----")
print(f"{total:4d}  TOTAL")
