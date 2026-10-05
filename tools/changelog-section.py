#!/usr/bin/env python3
"""Print the CHANGELOG.md section of one version (the text of its GitHub release).

    tools/changelog-section.py 3.0.0                  # the "## [3.0.0] - date" section, without its heading
    tools/changelog-section.py --require-date 3.0.0   # the same, but only when the heading carries a release date
    tools/changelog-section.py --changelog FILE ...   # read FILE instead of the repository's CHANGELOG.md

Exits 1 when the version has no section (or an empty one), so a release cannot go out without notes. With
--require-date, which the release workflow uses, it also exits 1 when the heading is not exactly
"## [X.Y.Z] - YYYY-MM-DD" with a real date: a tag must not publish notes whose tagged file still says "Unreleased".
Without the flag the notes are printed whatever the heading says, so that a section can be previewed while it is written.
Standard library only; runs on the Python 3 of Debian and of the CI runners (3.9 and newer).
"""
import argparse
import datetime
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def find_section(text, version):
    """Returns (heading line, body) of `## [version] ...`, or raises ValueError when there is none or more than one."""
    headings = list(re.finditer(r"^## \[%s\][^\n]*$" % re.escape(version), text, re.M))
    if not headings:
        raise ValueError("CHANGELOG.md has no section for %s" % version)
    if len(headings) > 1:
        raise ValueError("CHANGELOG.md has %d sections for %s; there must be one" % (len(headings), version))
    heading = headings[0]
    following = re.search(r"^## \[", text[heading.end():], re.M)
    body = text[heading.end():heading.end() + following.start()] if following else text[heading.end():]
    return heading.group(0), body


def release_date(heading, version):
    """The date of a heading that is exactly "## [X.Y.Z] - YYYY-MM-DD", as a date; ValueError for anything else."""
    m = re.fullmatch(r"## \[%s\] - ([0-9]{4}-[0-9]{2}-[0-9]{2})" % re.escape(version), heading)
    if not m:
        raise ValueError('the heading "%s" does not end in a release date: write it as "## [%s] - YYYY-MM-DD" '
                         "before tagging (it still says what is not released yet)" % (heading, version))
    try:
        return datetime.date.fromisoformat(m.group(1))
    except ValueError:
        raise ValueError('the heading "%s" has a date that does not exist: %s' % (heading, m.group(1)))


def main(argv):
    parser = argparse.ArgumentParser(prog="changelog-section.py", add_help=True,
                                     description="Print the CHANGELOG.md section of one version.")
    parser.add_argument("version", help="X.Y.Z (a leading v is fine)")
    parser.add_argument("--require-date", action="store_true",
                        help='refuse a heading that is not "## [X.Y.Z] - YYYY-MM-DD" (for example "Unreleased")')
    parser.add_argument("--changelog", default=os.path.join(ROOT, "CHANGELOG.md"), help="the file to read")
    args = parser.parse_args(argv)
    version = args.version.lstrip("v")
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
        parser.error("the version must look like 3.0.0, not %r" % args.version)
    try:
        with open(args.changelog, encoding="utf-8") as fh:
            text = fh.read()
        heading, body = find_section(text, version)
        if not body.strip():
            raise ValueError("CHANGELOG.md has no notes for %s" % version)
        if args.require_date:
            release_date(heading, version)
    except (OSError, ValueError) as err:
        print("changelog-section: %s" % err, file=sys.stderr)
        return 1
    print(body.strip())
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
