#!/usr/bin/env python3
"""Print the CHANGELOG.md section of one version (the text of its GitHub release).

    tools/changelog-section.py 3.0.0        # the "## [3.0.0] - date" section, without its heading
Exits 1 when the version has no section, so a release cannot go out without notes.
"""
import os
import re
import sys

root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
version = sys.argv[1].lstrip("v") if len(sys.argv) > 1 else ""
if not re.fullmatch(r"\d+\.\d+\.\d+", version):
    sys.exit("usage: changelog-section.py X.Y.Z")
with open(os.path.join(root, "CHANGELOG.md"), encoding="utf-8") as fh:
    text = fh.read()
m = re.search(r"^## \[%s\][^\n]*\n(.*?)(?=^## \[|\Z)" % re.escape(version), text, re.S | re.M)
if not m or not m.group(1).strip():
    sys.exit("CHANGELOG.md has no notes for %s" % version)
print(m.group(1).strip())
