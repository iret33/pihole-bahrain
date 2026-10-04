#!/usr/bin/env bash
# Remove Sinko. Pi-hole itself stays installed.
#   sudo /opt/sinko/uninstall.sh
#
# Left alone on purpose, because the rest of the box may use them: Pi-hole, avahi-daemon (the <name>.local name),
# unattended-upgrades and the settings the installer gave it (security updates keep coming), and what tools/seal.sh
# did to the ready-made image (the hardware watchdog, locked SSH password logins).
set -Euo pipefail
R="${SINKO_ROOT:-}"
APP_DIR="$R/opt/sinko"
UNIT_DIR="$R/etc/systemd/system"
STATE_DIR="${SINKO_STATE_DIR:-$R/var/lib/sinko}"
BIN="$APP_DIR/bin/sinko"
TTY_DEV="${SINKO_TTY:-/dev/tty}"      # the terminal; SINKO_TTY is a test hook

[[ "$(id -u)" -eq 0 ]] || { echo "Run as root: sudo $0" >&2; exit 1; }
if [[ "${SINKO_NONINTERACTIVE:-}" != 1 ]] && { : <"$TTY_DEV"; } 2>/dev/null; then
  read -r -p "Remove Sinko and all its rules and kid devices? [y/N] " answer <"$TTY_DEV"
  [[ "$answer" =~ ^[Yy] ]] || { echo "Nothing changed."; exit 0; }
fi

echo "==> Stopping services"
systemctl disable --now sinko.service sinko-lists.timer sinko-firstboot.service 2>/dev/null || true
systemctl stop sinko-update.service 2>/dev/null || true       # an update that is running right now
rm -f "$UNIT_DIR/sinko.service" "$UNIT_DIR/sinko-lists.service" "$UNIT_DIR/sinko-lists.timer" "$UNIT_DIR/sinko-firstboot.service"
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
if grep -qs 'name="generator" content="sinko"' "$WEBROOT/index.html"; then
  rm -f "$WEBROOT/index.html"
fi
[[ -f "$WEBROOT/index.html.pb-backup" ]] && mv "$WEBROOT/index.html.pb-backup" "$WEBROOT/index.html"
rm -rf "$WEBROOT/pb"

# The old command name is a shortcut the installer left behind when it moved a pihole-bahrain box to Sinko.
# Only that shortcut goes; a file or link somebody else put there stays.
legacy_bin="$R/usr/local/bin/pihole-bahrain"
if [[ -L "$legacy_bin" && "$(readlink -f "$legacy_bin")" == "$(readlink -f "$BIN")" ]]; then
  rm -f "$legacy_bin"
fi

rm -rf "$APP_DIR" "$R/etc/sinko" "$R/usr/local/bin/sinko"
# Runtime data: update results, rollback copies, the counter's random id, the first-start flag.
if [[ -n "$STATE_DIR" && "$STATE_DIR" != "$R" && "$STATE_DIR" != / ]]; then
  rm -rf "$STATE_DIR"
fi

echo
echo "Sinko has been removed. Pi-hole is still installed at http://<this-device>/admin/"
echo "To remove Pi-hole too, run: sudo pihole uninstall"
echo "The .local name (avahi-daemon) and the automatic security updates are still on; to remove them: sudo apt remove avahi-daemon unattended-upgrades"
