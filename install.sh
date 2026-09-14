#!/usr/bin/env bash
# One-line installer for the Pi-hole Parental Controls fork (pihole-bahrain).
#
# Serves a password-protected parent page at "/" while leaving the original
# dashboard untouched at /admin. The page authenticates with the SAME password
# as the Pi-hole admin (via /api/auth), so there is no separate PIN.
#
# Usage:
#   curl -sSL https://raw.githubusercontent.com/iret33/pihole-bahrain/master/install.sh | sudo bash
set -euo pipefail

REPO_RAW="https://raw.githubusercontent.com/iret33/pihole-bahrain/master"
TOML="/etc/pihole/pihole.toml"

if [ "$(id -u)" -ne 0 ]; then
  echo "This installer needs root. Re-run with: sudo bash install.sh" >&2
  exit 1
fi

# 1. Resolve the web root (defaults to /var/www/html)
WEBROOT="$(grep -E '^\s*webroot\s*=' "$TOML" 2>/dev/null | head -1 | sed -E 's/.*=\s*"([^"]*)".*/\1/')"
[ -n "$WEBROOT" ] || WEBROOT="/var/www/html"
echo "webroot: $WEBROOT"

# 2. Fetch the parent page (index at the root, assets under /parental/)
mkdir -p "$WEBROOT/parental"
curl -fsSL "$REPO_RAW/parental/index.html" -o "$WEBROOT/index.html"
curl -fsSL "$REPO_RAW/parental/app.js"     -o "$WEBROOT/parental/app.js"
curl -fsSL "$REPO_RAW/parental/style.css"  -o "$WEBROOT/parental/style.css"
chmod 644 "$WEBROOT/index.html" "$WEBROOT/parental/app.js" "$WEBROOT/parental/style.css"
echo "installed page files"

# 3. Let the web server serve files from the webroot (so "/" serves our page).
#    Back up the config first.
cp "$TOML" "$TOML.bak-$(date +%Y%m%d-%H%M%S)"
if grep -qE '^\s*serve_all\s*=\s*false' "$TOML"; then
  sed -i -E 's/^(\s*serve_all\s*=\s*)false/\1true/' "$TOML"
  echo "enabled webserver.serve_all"
elif grep -qE '^\s*serve_all\s*=\s*true' "$TOML"; then
  echo "webserver.serve_all already enabled"
else
  awk '/^\[webserver\]/{print; print "  serve_all = true"; next} {print}' "$TOML" > "$TOML.new" && mv "$TOML.new" "$TOML"
  echo "added webserver.serve_all = true"
fi

# 4. Restart FTL so the webserver picks up the change
if command -v systemctl >/dev/null 2>&1; then systemctl restart pihole-FTL 2>/dev/null || true; fi
if command -v service >/dev/null 2>&1; then service pihole-FTL restart 2>/dev/null || true; fi
echo "restarted pihole-FTL"

echo
echo "Done. Open https://<pihole-ip>/ and sign in with the admin password."
echo "The original dashboard is still at /admin."
