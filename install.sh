#!/usr/bin/env bash
# One-line installer for the Pi-hole Parental Controls fork (pihole-bahrain).
#
# - Installs Pi-hole first if it is not already present (unattended defaults).
# - Serves a password-protected parent page at "/" while leaving the original
#   dashboard untouched at /admin. The page authenticates with the SAME password
#   as the Pi-hole admin (via /api/auth), so there is no separate PIN.
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

# 0. Install Pi-hole first if it is not already present.
if [ -f "$TOML" ]; then
  echo "Pi-hole already installed — skipping install."
else
  echo "Pi-hole not detected — installing Pi-hole (unattended) ..."
  curl -sSL https://install.pi-hole.net | bash -s -- --unattended
fi

# Verify Pi-hole config now exists.
if [ ! -f "$TOML" ]; then
  echo "ERROR: Pi-hole install did not produce $TOML. Aborting." >&2
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

# 5. Register the parental-control block lists (URL-based; updated from this repo).
#    Wait briefly for FTL's API to come up after the restart.
for _ in $(seq 1 15); do curl -sk -o /dev/null https://127.0.0.1/api/info/version && break; sleep 1; done
curl -fsSL "$REPO_RAW/lists/register.sh" -o /tmp/pihole-register-lists.sh
bash /tmp/pihole-register-lists.sh
rm -f /tmp/pihole-register-lists.sh

echo
echo "Done. Open https://<pihole-ip>/ and sign in with the admin password."
echo "  (If Pi-hole was just installed, the admin password was generated and"
echo "   printed above; set a known one with: pihole setpassword)"
echo "The original dashboard is still at /admin."
