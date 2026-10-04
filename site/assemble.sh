#!/usr/bin/env bash
# Builds the folder that GitHub Pages serves: this site, plus what it shares with the rest of the repository.
# The pages workflow and a person previewing the site run exactly this, so they cannot differ.
#
#   bash site/assemble.sh OUTDIR [BASE_URL]
#
#   OUTDIR    created fresh (an existing one is removed)
#   BASE_URL  the address the site will be served from, no trailing slash, for example https://iret33.github.io/sinko
#             (the "base_url" that actions/configure-pages reports). It replaces __SITE_URL__ in index.html, because
#             the Open Graph image and the canonical address must be absolute and crawlers do not run JavaScript.
#             Without it the placeholder becomes empty, which is fine for a local preview.
#
# Copied from other places in the repository, at deploy time, so there is one copy of each:
#   docs/img/*      -> img/     screenshots and social-preview.png (a missing one is only a warning: the page copes)
#   web/fonts/*     -> fonts/   IBM Plex Sans Arabic and its licence (SIL OFL)
#   web/icon.svg    -> icon.svg
set -Eeuo pipefail

die() { printf 'assemble.sh: %s\n' "$*" >&2; exit 1; }

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
out="${1:-}"
base="${2:-}"
[[ -n "$out" ]] || die "usage: bash site/assemble.sh OUTDIR [BASE_URL]"
# The base goes into a sed replacement and into HTML attributes: allow only what an https address needs.
if [[ -n "$base" ]]; then
  base="${base%/}"
  [[ "$base" =~ ^https://[A-Za-z0-9.-]+(:[0-9]+)?(/[A-Za-z0-9._~-]+)*$ ]] || die "BASE_URL '$base' is not a plain https address"
fi

rm -rf "$out"
mkdir -p "$out/img" "$out/fonts"

# The site itself, without its tests, this script and the readme.
while IFS= read -r -d '' file; do
  file="${file#./}"
  mkdir -p "$out/$(dirname "$file")"
  install -m 0644 "$root/site/$file" "$out/$file"
done < <(cd "$root/site" && find . -type f ! -path './test/*' ! -name assemble.sh ! -name README.md -print0)

shopt -s nullglob
images=("$root"/docs/img/*)
if ((${#images[@]} == 0)); then
  printf '::warning::docs/img has no pictures, so the site is built without screenshots and a social preview image\n'
fi
for file in "${images[@]}"; do
  [[ -f "$file" ]] && install -m 0644 "$file" "$out/img/"
done

fonts=("$root"/web/fonts/*)
((${#fonts[@]} > 0)) || die "web/fonts is empty: the site needs IBM Plex Sans Arabic"
for file in "${fonts[@]}"; do
  [[ -f "$file" ]] && install -m 0644 "$file" "$out/fonts/"
done
[[ -f "$root/web/icon.svg" ]] || die "web/icon.svg is missing"
install -m 0644 "$root/web/icon.svg" "$out/icon.svg"

[[ -f "$out/index.html" ]] || die "site/index.html is missing"
sed -i "s|__SITE_URL__|${base}|g" "$out/index.html"
if grep -q '__SITE_URL__' "$out/index.html"; then die "a placeholder was left in index.html"; fi

# Not a Jekyll site: nothing may be skipped or rewritten because of its name.
: >"$out/.nojekyll"
printf 'assemble.sh: built %s (%s files)\n' "$out" "$(find "$out" -type f | wc -l)"
