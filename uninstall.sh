#!/usr/bin/env bash
# Remove Sinko. Pi-hole itself stays installed.
#   sudo /opt/sinko/uninstall.sh            (--force: remove the files even when Sinko's objects could not be taken out of Pi-hole)
#
# Left alone on purpose, because the rest of the box may use them: Pi-hole, avahi-daemon (the <name>.local name),
# unattended-upgrades and the settings the installer gave it (security updates keep coming), and what tools/seal.sh
# did to the ready-made image (the hardware watchdog, locked SSH password logins).
#
# The order matters: Sinko's groups, rules and devices are taken out of Pi-hole FIRST. If that does not work (Pi-hole is
# stopped, its API does not answer), nothing else is removed, and the scheduler is started again: the program that could
# still do it later, the page and the scheduler stay, and the message says what to do. Deleting them first could leave
# the children's devices in a group that blocks everything ("internet off", bedtime), with no tool left to undo it.
set -Euo pipefail
R="${SINKO_ROOT:-}"
APP_DIR="$R/opt/sinko"
UNIT_DIR="$R/etc/systemd/system"
STATE_DIR="${SINKO_STATE_DIR:-$R/var/lib/sinko}"
BIN="$APP_DIR/bin/sinko"
TTY_DEV="${SINKO_TTY:-/dev/tty}"      # the terminal; SINKO_TTY is a test hook

FORCE=0
for arg in "$@"; do
  case "$arg" in
    --force) FORCE=1 ;;
    -h|--help) sed -n '2,/^set -Euo/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "uninstall: unknown option $arg (only --force)" >&2; exit 2 ;;
  esac
done

[[ "$(id -u)" -eq 0 ]] || { echo "Run as root: sudo $0" >&2; exit 1; }
if [[ "${SINKO_NONINTERACTIVE:-}" != 1 ]] && { : <"$TTY_DEV"; } 2>/dev/null; then
  read -r -p "Remove Sinko and all its rules and kid devices? [y/N] " answer <"$TTY_DEV"
  [[ "$answer" =~ ^[Yy] ]] || { echo "Nothing changed."; exit 0; }
fi

# ---- 1. Sinko's objects in Pi-hole (nothing is deleted from this computer until that worked)
if [[ -x "$BIN" ]] && command -v pihole-FTL >/dev/null; then
  was_running=0
  systemctl is-active --quiet sinko.service 2>/dev/null && was_running=1
  echo "==> Stopping the scheduler"
  systemctl stop sinko.service sinko-update.service 2>/dev/null || true     # an update that is running right now, too
  echo "==> Removing groups, lists, rules and kid devices from Pi-hole"
  if ! "$BIN" remove; then
    if (( FORCE )); then
      echo "   (could not remove them; going on because of --force)" >&2
      echo "   WARNING: Sinko's groups (names start with pb-), its lists and its block-everything rule are still in Pi-hole." >&2
      echo "   While a child's device is in one of them its internet can stay off. Delete them in Pi-hole's admin page (Groups, Adlists, Domains)." >&2
    else
      (( was_running )) && systemctl start sinko.service 2>/dev/null
      cat >&2 <<EOF

Sinko was NOT removed: it could not take its groups, rules and children's devices out of Pi-hole (see above).
The program, the parent page and the scheduler are still in place$( (( was_running )) && echo ", and the scheduler is running again").

  * If Pi-hole is stopped or restarting, start it (sudo systemctl start pihole-FTL), wait a minute, and run
      sudo $0
    again.
  * If bedtime or "internet off" is active right now, the children's internet may stay off until this has worked.
    End it on the parent page, or switch off the groups whose names start with pb- in Pi-hole's admin page (Groups).
  * To remove the files anyway, run   sudo $0 --force   and then delete the pb- groups, the lists commented "pb:"
    and the rule  ^.*$  that Sinko added by hand in Pi-hole's admin page (http://<this-device>/admin/).
EOF
      exit 1
    fi
  fi
  "$BIN" configure --remove-hostname --disable-web || true
  echo "==> Updating Pi-hole's block lists"
  pihole -g >/dev/null 2>&1 || true
else
  echo "==> Nothing to take out of Pi-hole (the Sinko program or Pi-hole is not installed)"
fi

# ---- 2. The services, the page and the program
echo "==> Stopping services"
systemctl disable --now sinko.service sinko-lists.timer sinko-firstboot.service 2>/dev/null || true
systemctl stop sinko-update.service 2>/dev/null || true       # an update that is running right now
rm -f "$UNIT_DIR/sinko.service" "$UNIT_DIR/sinko-lists.service" "$UNIT_DIR/sinko-lists.timer" "$UNIT_DIR/sinko-firstboot.service"
systemctl daemon-reload 2>/dev/null || true

WEBROOT="$(pihole-FTL --config -q webserver.paths.webroot 2>/dev/null || true)"
WEBROOT="${WEBROOT:-$R/var/www/html}"
if grep -qs 'name="generator" content="sinko"' "$WEBROOT/index.html"; then
  rm -f "$WEBROOT/index.html"
fi
[[ -f "$WEBROOT/index.html.pb-backup" ]] && mv "$WEBROOT/index.html.pb-backup" "$WEBROOT/index.html"
rm -rf "$WEBROOT/pb" "$WEBROOT/pb.new" "$WEBROOT/pb.old"
rm -f "$WEBROOT/index.html.sinko-new"

# The old command name is a shortcut the installer left behind when it moved a pihole-bahrain box to Sinko.
# Only that shortcut goes; a file or link somebody else put there stays.
legacy_bin="$R/usr/local/bin/pihole-bahrain"
if [[ -L "$legacy_bin" && "$(readlink -f "$legacy_bin")" == "$(readlink -f "$BIN")" ]]; then
  rm -f "$legacy_bin"
fi

rm -rf "$APP_DIR" "$R/etc/sinko" "$R/usr/local/bin/sinko" "$R/usr/local/bin/sinko.sinko-new"
# Runtime data: update results, rollback copies, the counter's random id, the first-start flag, the install lock.
if [[ -n "$STATE_DIR" && "$STATE_DIR" != "$R" && "$STATE_DIR" != / ]]; then
  rm -rf "$STATE_DIR"
fi

echo
echo "Sinko has been removed. Pi-hole is still installed at http://<this-device>/admin/"
echo "To remove Pi-hole too, run: sudo pihole uninstall"
echo "The .local name (avahi-daemon) and the automatic security updates are still on; to remove them: sudo apt remove avahi-daemon unattended-upgrades"
