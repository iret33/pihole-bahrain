#!/usr/bin/env bash
# Build the release assets (see docs/maintainers/architecture.md, "Release assets"):
#   sinko.tar.gz         what a box downloads: one top-level folder, sinko/
#   sinko.tar.gz.sha256  "<hex>  sinko.tar.gz"
#   install.sh           the one-liner, byte for byte the file in the tag
# Usage: tools/build-release.sh [OUTPUT_DIR]      (default: dist/)
# Reproducible: the same files give the same bytes (sorted names, fixed owner, fixed time, no gzip name/time).
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
OUT="${1:-dist}"
NAME=sinko

# What a box needs. Not shipped: tests, docs, the site, the counter service, development tools.
PATHS=(bin lists web systemd install.sh uninstall.sh VERSION LICENSE NOTICE)
for t in tools/seal.sh tools/firstboot.sh; do [[ -e "$t" ]] && PATHS+=("$t"); done
for p in "${PATHS[@]}"; do [[ -e "$p" ]] || { echo "build-release: missing $p" >&2; exit 1; }; done

version="$(tr -d '[:space:]' <VERSION)"
[[ "$version" =~ ^[0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,4}$ ]] || { echo "build-release: VERSION '$version' is not X.Y.Z" >&2; exit 1; }
in_code="$(sed -n 's/^VERSION = "\(.*\)"$/\1/p' bin/sinko)"
[[ "$in_code" == "$version" ]] || { echo "build-release: bin/sinko says $in_code but VERSION says $version" >&2; exit 1; }

# A fixed time: the commit's, so a rebuild of the same commit gives the same archive.
epoch="${SOURCE_DATE_EPOCH:-$(git log -1 --format=%ct 2>/dev/null || date +%s)}"

mkdir -p "$OUT"
tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT
find "${PATHS[@]}" \( -name __pycache__ -o -name '*.pyc' -o -name .DS_Store \) -prune -o -type f -print | LC_ALL=C sort >"$tmp"
tar --create --null --files-from=<(tr '\n' '\0' <"$tmp") \
    --sort=name --mtime="@$epoch" --owner=0 --group=0 --numeric-owner --format=gnu \
    --transform "s,^,$NAME/," | gzip -n -9 >"$OUT/$NAME.tar.gz"
( cd "$OUT" && sha256sum "$NAME.tar.gz" >"$NAME.tar.gz.sha256" )
cp install.sh "$OUT/install.sh"

echo "build-release: $version -> $OUT/ ($(wc -c <"$OUT/$NAME.tar.gz") bytes, sha256 $(cut -d' ' -f1 <"$OUT/$NAME.tar.gz.sha256"))"
