#!/usr/bin/env bash
# Remove Family Internet (pihole-bahrain). Pi-hole itself stays installed.
#   sudo /opt/pihole-bahrain/uninstall.sh
set -Euo pipefail
R="${PB_ROOT:-}"
APP_DIR="$R/opt/pihole-bahrain"
UNIT_DIR="$R/etc/systemd/system"
BIN="$APP_DIR/bin/pihole-bahrain"

[[ "$(id -u)" -eq 0 ]] || { echo "Run as root: sudo $0" >&2; exit 1; }
if [[ "${PB_NONINTERACTIVE:-}" != 1 ]] && { : </dev/tty; } 2>/dev/null; then
  read -r -p "Remove Family Internet and all its rules and kid devices? [y/N] " answer </dev/tty
  [[ "$answer" =~ ^[Yy] ]] || { echo "Nothing changed."; exit 0; }
fi

echo "==> Stopping services"
systemctl disable --now pihole-bahrain.service pihole-bahrain-lists.timer 2>/dev/null || true
rm -f "$UNIT_DIR/pihole-bahrain.service" "$UNIT_DIR/pihole-bahrain-lists.service" "$UNIT_DIR/pihole-bahrain-lists.timer"
systemctl daemon-reload 2>/dev/null || true

if [[ -x "$BIN" ]] && command -v pihole-FTL >/dev/null; then
  echo "==> Removing groups, lists, rules and kid devices from Pi-hole"
  "$BIN" remove || echo "   (could not reach Pi-hole; its objects start with pb- and can be deleted in /admin)"
  "$BIN" configure --remove-hostname --disable-web || true
  echo "==> Updating Pi-hole's block lists"
  pihole -g >/dev/null 2>&1 || true
fi

WEBROOT="$(pihole-FTL --config -q webserver.paths.webroot 2>/dev/null || true)"
WEBROOT="${WEBROOT:-$R/var/www/html}"
if grep -qs 'name="generator" content="pihole-bahrain"' "$WEBROOT/index.html"; then
  rm -f "$WEBROOT/index.html"
fi
[[ -f "$WEBROOT/index.html.pb-backup" ]] && mv "$WEBROOT/index.html.pb-backup" "$WEBROOT/index.html"
rm -rf "$WEBROOT/pb"
rm -rf "$APP_DIR" "$R/etc/pihole-bahrain" "$R/usr/local/bin/pihole-bahrain"

echo
echo "Family Internet has been removed. Pi-hole is still installed at http://<this-device>/admin/"
echo "To remove Pi-hole too, run: sudo pihole uninstall"
