#!/usr/bin/env bash
# Seal a "golden unit" before its SD card is copied to an image that is flashed onto many boxes.
#
#   sudo /opt/sinko/tools/seal.sh --dry-run     shows everything it would do and changes nothing
#   sudo /opt/sinko/tools/seal.sh               shows it, then asks you to type SEAL
#
# Sealing removes what must be unique to each box or private to the seller (SSH host keys, machine id, the counter's
# install id, query history, the parent password so the page shows its welcome screen, the children's devices and
# timers, shell history, logs), locks the default root login, and arms sinko-firstboot.service, which gives every box
# its own keys, name and address when it first starts. Power the unit off right after sealing and do not start it
# again before you copy the card: starting it uses up the seal.
#
# Options:
#   --yes               do not ask for SEAL (for scripts)
#   --dry-run           only show what would be done
#   --keep-ssh-access   leave the root password, SSH password logins and the authorized keys alone (a test unit)
#   --hostname NAME     the name of every box, so that http://NAME.local/ opens the page (default: sinko)
#   --zerofill          afterwards write zeros over the free space and delete them: the card compresses much smaller
#
# Only Sinko's own Pi-hole objects (names starting with pb-) are touched, through the sinko program; the one Pi-hole
# file deleted directly is the query history, while Pi-hole's FTL is stopped.
set -Eeuo pipefail

R="${SINKO_ROOT:-}"                                  # test hook: act on a fake root
[[ -z "$R" || "$R" == /* ]] || { echo "seal: SINKO_ROOT must be an absolute path" >&2; exit 2; }
APP_DIR="$R/opt/sinko"
BIN="$APP_DIR/bin/sinko"
CONF_FILE="$R/etc/sinko/config"
STATE_DIR="${SINKO_STATE_DIR:-$R/var/lib/sinko}"
UNIT_DIR="$R/etc/systemd/system"
TTY_DEV="${SINKO_TTY:-/dev/tty}"                     # the terminal; SINKO_TTY is a test hook

YES=0 DRY=0 KEEP_SSH=0 ZEROFILL=0 NAME=sinko

die() { printf 'seal: %s\n' "$*" >&2; exit 1; }
# A command that failed without its own message: say where, and that the box is not sealed.
trap 'printf "seal: stopped because a command failed (line %s). The box is not completely sealed; fix the problem and run seal again.\n" "$LINENO" >&2' ERR
usage() { sed -n '2,/^set -Eeuo/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'; }

while (( $# )); do
  case "$1" in
    --yes) YES=1 ;;
    --dry-run) DRY=1 ;;
    --keep-ssh-access) KEEP_SSH=1 ;;
    --zerofill) ZEROFILL=1 ;;
    --hostname)
      (( $# >= 2 )) || die "--hostname needs a name"
      NAME="$2"; shift ;;
    --hostname=*) NAME="${1#--hostname=}" ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'seal: unknown option %s\n\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done
NAME="${NAME,,}"
[[ "$NAME" =~ ^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$ ]] \
  || die "'$NAME' is not a usable host name: use letters, digits and hyphens only (one word, at most 63 characters), such as sinko"

# ------------------------------------------------------------------ what has to be there
[[ "$(id -u)" -eq 0 ]] || die "run as root: sudo $0"
[[ -x "$BIN" ]] || die "Sinko is not installed on this box ($BIN is missing). Install it first, then seal."
command -v pihole-FTL >/dev/null && [[ -f "$R/etc/pihole/pihole.toml" ]] \
  || die "Pi-hole v6 is not installed on this box. Sinko needs it: install both, then seal."
[[ -f "$UNIT_DIR/sinko-firstboot.service" && -x "$APP_DIR/tools/firstboot.sh" ]] \
  || die "this Sinko has no first-start service. Install a release that has one (tools/firstboot.sh), then seal."

# ------------------------------------------------------------------ the plan
# Every change is an entry in this list: what is shown (and asked about) is exactly what runs afterwards.
ACT_DESC=() ACT_CMD=()
add_action() {  # description function [argument...]
  local desc="$1" joined="" a
  shift
  for a in "$@"; do joined+="$a"$'\x1f'; done
  ACT_DESC+=("$desc")
  ACT_CMD+=("$joined")
}

# Existing paths only, one per line; never a name with a line break in it (it could not be passed on safely).
existing() {  # path...
  local p
  for p in "$@"; do
    if [[ ( -e "$p" || -L "$p" ) && "$p" != *$'\n'* && "$p" != *$'\x1f'* ]]; then printf '%s\n' "$p"; fi
  done
}
homes() { printf '%s\n' "$R/root"; local d; for d in "$R"/home/*/; do [[ -d "$d" ]] && printf '%s\n' "${d%/}"; done; return 0; }
join_list() { local out="" p; for p in "$@"; do out+="${out:+, }$p"; done; printf '%s' "$out"; }

# The logs: files to empty (so their owners and modes stay as the services expect) and rotated copies to delete.
is_rotated() {
  case "$1" in
    *.gz|*.xz|*.bz2|*.zst|*.old|*.[0-9]|*.[0-9][0-9]|*-20[0-9][0-9][0-9][0-9][0-9][0-9]) return 0 ;;
    *) return 1 ;;
  esac
}
log_files() {  # prints the log files under /var/log, outside the journal
  [[ -d "$R/var/log" ]] || return 0
  find "$R/var/log" -path "$R/var/log/journal" -prune -o -type f -print 2>/dev/null | LC_ALL=C sort || true
}
archived_journals() {
  [[ -d "$R/var/log/journal" ]] || return 0
  find "$R/var/log/journal" -type f \( -name '*@*.journal*' -o -name '*.journal~' \) 2>/dev/null | LC_ALL=C sort || true
}

ftl_database() {  # the query history file, from FTL's own settings
  local db
  db="$(pihole-FTL --config files.database 2>/dev/null | head -n1 || true)"
  [[ -n "$db" ]] || db="$R/etc/pihole/pihole-FTL.db"
  [[ "$db" == /* && "$db" != *..* && "$db" == *.db && ( -z "$R" || "$db" == "$R"/* ) ]] || die "unexpected location of Pi-hole's query history: $db"
  printf '%s' "$db"
}

kid_names() {  # the children's devices registered in Sinko, read-only; empty when Pi-hole cannot be asked
  "$BIN" status 2>/dev/null | python3 -c '
import json, sys
try:
    devices = json.load(sys.stdin).get("devices", [])
except ValueError:
    devices = []
print(", ".join((d.get("name") or d.get("client") or "?") for d in devices))' 2>/dev/null || true
}

build_plan() {
  local list kids

  add_action "Stop the Sinko scheduler (it stays enabled: it starts again at the next boot)" act_stop_scheduler
  if grep -qs '^SINKO_TELEMETRY=' "$CONF_FILE"; then
    add_action "Forget the answer to the anonymous-counter question in $CONF_FILE (the customer is asked again)" act_forget_counter_answer
  fi
  kids="$(kid_names)"
  add_action "Remove Sinko's Pi-hole objects and start them again empty: the children's devices${kids:+ ($kids)}, timers, bedtime, blocked apps and the pb-* groups, lists and rules. Your own Pi-hole groups, lists and clients stay." act_reset_pihole
  add_action "Remove the parent password, so that the page shows its welcome screen" act_remove_password

  if (( ! KEEP_SSH )); then
    add_action "Lock the root password and turn off SSH password logins (a drop-in in /etc/ssh/sshd_config.d; use --keep-ssh-access to skip this)" act_lock_ssh
    local -a keys=()
    mapfile -t keys < <(existing "$R/root/.ssh/authorized_keys" "$R/root/.ssh/known_hosts" "$R"/home/*/.ssh/authorized_keys "$R"/home/*/.ssh/known_hosts)
    (( ${#keys[@]} == 0 )) || add_action "Remove SSH keys and host lists of people who logged in here: $(join_list "${keys[@]}")" act_remove_paths "${keys[@]}"
  else
    add_action "Leave the root password, SSH password logins and authorized keys as they are (--keep-ssh-access)" act_nothing
  fi
  add_action "Set the host name to $NAME (so http://$NAME.local/ opens the page on every box)" act_hostname
  if [[ -e "$R/dev/watchdog" ]]; then
    add_action "Switch on the hardware watchdog, so a hung box restarts itself (/dev/watchdog was found)" act_watchdog
  else
    add_action "Hardware watchdog: skipped, there is no /dev/watchdog on this device" act_nothing
  fi
  add_action "Arm the first-start service: every box makes its own SSH keys, host name and address when it first starts" act_arm_first_start

  local db
  db="$(ftl_database)"
  local -a dbfiles=()
  mapfile -t dbfiles < <(existing "$db" "$db-wal" "$db-shm" "$db-journal")
  list="$(join_list "${dbfiles[@]}")"
  add_action "Stop Pi-hole's FTL and delete the query history${list:+: $list} (it stays stopped: the box is powered off next)" act_wipe_query_history "$db"

  local -a gone=()
  mapfile -t gone < <(existing "$R"/etc/ssh/ssh_host_* | LC_ALL=C sort)
  (( ${#gone[@]} == 0 )) || add_action "Remove the SSH host keys: $(join_list "${gone[@]}")" act_remove_paths "${gone[@]}"
  mapfile -t gone < <(existing "$STATE_DIR/install-id" "$STATE_DIR/handled.json" "$STATE_DIR/update-result.json" "$R/etc/sinko/initial-password")
  (( ${#gone[@]} == 0 )) || add_action "Remove the counter's install id and what the scheduler remembered: $(join_list "${gone[@]}")" act_remove_paths "${gone[@]}"
  local -a files=()
  local h f
  while IFS= read -r h; do
    for f in .bash_history .python_history .lesshst .viminfo .wget-hsts .sudo_as_admin_successful; do files+=("$h/$f"); done
  done < <(homes)
  files+=("$R/root/.not_logged_in_yet")
  mapfile -t gone < <(existing "${files[@]}")
  (( ${#gone[@]} == 0 )) || add_action "Remove shell history and Armbian's first-login file: $(join_list "${gone[@]}")" act_remove_paths "${gone[@]}"
  mapfile -t gone < <(existing "$R/var/lib/systemd/random-seed" "$R/var/lib/systemd/credential.secret" "$R"/var/lib/dhcp/*.leases "$R"/var/lib/NetworkManager/*.lease)
  (( ${#gone[@]} == 0 )) || add_action "Remove the random seed and the network leases of this network: $(join_list "${gone[@]}")" act_remove_paths "${gone[@]}"
  add_action "Make the machine id empty (/etc/machine-id; every box makes its own at its first start)" act_machine_id

  local total=0 rotated=0 emptied=0 journals=0
  while IFS= read -r f; do
    total=$(( total + 1 ))
    if is_rotated "$f"; then rotated=$(( rotated + 1 )); elif [[ -s "$f" ]]; then emptied=$(( emptied + 1 )); fi
  done < <(log_files)
  journals="$(archived_journals | grep -c . || true)"
  add_action "Clear the logs: empty $emptied log files and delete $rotated rotated ones under $R/var/log (the files stay, with their owners), the journal's old files ($journals), downloaded package files" act_clean_logs
  if (( ZEROFILL )); then
    add_action "Write zeros over the free space and delete them, so the image compresses small (takes a while)" act_zerofill
  fi
}

# ------------------------------------------------------------------ the actions
act_nothing() { echo "  (nothing to do)"; }

act_stop_scheduler() {
  systemctl stop sinko.service sinko-update.service 2>/dev/null || true
  echo "  stopped"
}

act_forget_counter_answer() {
  local tmp="$CONF_FILE.tmp"
  grep -v '^SINKO_TELEMETRY=' "$CONF_FILE" >"$tmp" || true     # grep exits 1 when nothing is left; that is fine
  chmod 644 "$tmp"
  mv "$tmp" "$CONF_FILE"
  echo "  done"
}

# Pi-hole must answer for this. The remove-and-setup pair is the whole reset: it deletes every pb-* group, the lists
# commented pb:, the block-all rule and the kid devices, and creates them fresh (default state, no timers, no blocked
# apps), then rebuilds the block lists. Objects that are not Sinko's are never looked at.
act_reset_pihole() {
  if ! systemctl is-active --quiet pihole-FTL.service 2>/dev/null; then
    systemctl start pihole-FTL.service || die "could not start Pi-hole's FTL, which the reset needs"
  fi
  "$BIN" password-state >/dev/null 2>&1 || [[ $? -eq 3 ]] || die "Pi-hole's API does not answer, so Sinko's objects could not be reset"
  "$BIN" remove || die "could not remove Sinko's objects from Pi-hole (see above); nothing else was changed"
  "$BIN" setup || die "could not set Sinko's objects up again (see above): run the seal again"
}

# The page offers its welcome screen (choose a password) when Pi-hole has no password. FTL's own setting is cleared
# first; if the stored hash survives, it is cleared directly. Checked either way: a box sealed with the seller's
# password would be a back door in every home.
no_password_now() {
  local rc=0
  "$BIN" password-state >/dev/null 2>&1 || rc=$?
  [[ "$rc" -eq 3 ]]
}
act_remove_password() {
  local i
  pihole-FTL --config webserver.api.password "" >/dev/null 2>&1 || true
  for i in 1 2 3 4 5 6 7 8 9 10; do no_password_now && { echo "  no password is set"; return 0; }; sleep 1; done
  pihole-FTL --config webserver.api.pwhash "" >/dev/null 2>&1 || true
  for i in 1 2 3 4 5 6 7 8 9 10; do no_password_now && { echo "  no password is set"; return 0; }; sleep 1; done
  die "the parent password could not be removed. Nothing was armed; sealing now would leave your password on every box."
}

act_lock_ssh() {
  local dir="$R/etc/ssh/sshd_config.d" file
  file="$dir/00-sinko-lockdown.conf"                 # 00-: for most options the first value found wins
  local pw=(passwd)
  [[ -z "$R" ]] || pw+=(-R "$R")
  "${pw[@]}" -l root >/dev/null || die "could not lock the root password"
  mkdir -p "$dir"
  {
    echo "# Written by Sinko's tools/seal.sh for the ready-made image: no password logins over SSH."
    echo "PasswordAuthentication no"
    echo "KbdInteractiveAuthentication no"
    echo "PermitRootLogin prohibit-password"
  } >"$file.tmp"
  chmod 644 "$file.tmp"
  mv "$file.tmp" "$file"
  if command -v sshd >/dev/null; then
    # It has to be in effect (a sshd_config without the Include line would ignore the drop-in): read it back.
    local effective sshd_t=(sshd -T)
    [[ -z "$R" ]] || sshd_t+=(-f "$R/etc/ssh/sshd_config")
    if effective="$("${sshd_t[@]}" 2>/dev/null)"; then
      grep -qi '^passwordauthentication no$' <<<"$effective" \
        || die "SSH password logins are still on: $file is not used by sshd_config (is there no Include of /etc/ssh/sshd_config.d?). Nothing was armed."
    else
      echo "  warning: sshd could not report its settings; check 'sshd -T | grep -i passwordauthentication' yourself" >&2
    fi
  fi
  echo "  root's password is locked and SSH password logins are off"
}

# The name of every box, in the two places the system reads it, so the image already has it.
act_hostname() {
  printf '%s\n' "$NAME" >"$R/etc/hostname.tmp"
  mv "$R/etc/hostname.tmp" "$R/etc/hostname"
  local hosts="$R/etc/hosts"
  if [[ -f "$hosts" ]]; then
    if grep -qE '^127\.0\.1\.1[[:space:]]' "$hosts"; then
      sed -E "s/^127\.0\.1\.1[[:space:]].*/127.0.1.1 $NAME/" "$hosts" >"$hosts.tmp"
    else
      { cat "$hosts"; echo "127.0.1.1 $NAME"; } >"$hosts.tmp"
    fi
    cat "$hosts.tmp" >"$hosts"                       # in place: /etc/hosts may be a bind mount
    rm -f "$hosts.tmp"
  fi
  echo "  host name: $NAME"
}

act_watchdog() {
  local dir="$R/etc/systemd/system.conf.d"
  mkdir -p "$dir"
  {
    echo "# Written by Sinko's tools/seal.sh: systemd keeps the hardware watchdog fed; a hung box restarts itself."
    echo "[Manager]"
    echo "RuntimeWatchdogSec=15"
    echo "RebootWatchdogSec=5min"
  } >"$dir/90-sinko-watchdog.conf.tmp"
  mv "$dir/90-sinko-watchdog.conf.tmp" "$dir/90-sinko-watchdog.conf"
  echo "  watchdog configured (active from the next start)"
}

# The flag carries the name for the first start. Armed before the SSH host keys are removed: a seal that stops in
# between leaves keys that firstboot replaces anyway, never a box without keys and without the unit that makes them.
act_arm_first_start() {
  install -d -m 700 "$STATE_DIR"
  ( umask 077; printf 'hostname=%s\n' "$NAME" >"$STATE_DIR/firstboot.tmp" )
  mv "$STATE_DIR/firstboot.tmp" "$STATE_DIR/firstboot"
  systemctl daemon-reload
  systemctl enable sinko-firstboot.service >/dev/null || die "could not enable sinko-firstboot.service"
  echo "  armed"
}

act_wipe_query_history() {
  local db="$1"
  systemctl stop pihole-FTL.service || die "could not stop Pi-hole's FTL, so its query history cannot be deleted safely"
  rm -f "$db" "$db-wal" "$db-shm" "$db-journal"
  echo "  deleted"
}

act_remove_paths() {
  local p
  for p in "$@"; do
    [[ "$p" == /* ]] || die "refusing to remove the relative path '$p'"
    rm -rf -- "$p"
  done
  echo "  removed $# item(s)"
}

act_machine_id() {
  [[ ! -e "$R/etc/machine-id" ]] || : >"$R/etc/machine-id"
  # The old copy of the id must not live on in /var/lib/dbus: it becomes a link to the one in /etc.
  local dbus="$R/var/lib/dbus/machine-id"
  if [[ -f "$dbus" && ! -L "$dbus" ]]; then
    rm -f "$dbus"
    ln -s ../../../etc/machine-id "$dbus"
  fi
  echo "  done"
}

act_clean_logs() {
  local f
  while IFS= read -r f; do
    if is_rotated "$f"; then rm -f -- "$f"; elif [[ -s "$f" ]]; then : >"$f"; fi
  done < <(log_files)
  journalctl --rotate >/dev/null 2>&1 || true
  journalctl --vacuum-time=1s >/dev/null 2>&1 || true
  while IFS= read -r f; do rm -f -- "$f"; done < <(archived_journals)
  command -v apt-get >/dev/null && apt-get clean >/dev/null 2>&1 || true
  echo "  done"
}

act_zerofill() {
  local file="$R/.sinko-zerofill"
  trap 'rm -f "$file"' EXIT
  # dd ends with "No space left on device" on purpose: that is how the free space gets filled.
  dd if=/dev/zero of="$file" bs=1M conv=fsync 2>/dev/null || true
  sync
  rm -f "$file"
  trap - EXIT
  echo "  done"
}

# ------------------------------------------------------------------ run
print_plan() {
  local i
  printf '\nSealing this box (%s) for the image. This is what will be done, in this order:\n\n' "$(hostname 2>/dev/null || echo "$NAME")"
  for i in "${!ACT_DESC[@]}"; do
    printf '  %2d. %s\n' "$(( i + 1 ))" "${ACT_DESC[$i]}"
  done
  printf '\nKept: Pi-hole and your own groups, lists and clients, the Sinko program and its release copy for rollback, the time zone, the box'"'"'s address in /etc/sinko/config until the first start.\n'
  if (( ! KEEP_SSH )); then
    printf '\n%s\n' "After this nobody can log in over SSH with a password (including you): run it from the console or keep this session open, and power the box off when it is done."
  fi
}

run_actions() {
  local i parts=()
  for i in "${!ACT_CMD[@]}"; do
    IFS=$'\x1f' read -r -a parts <<<"${ACT_CMD[$i]}"
    printf '\n==> %s\n' "${ACT_DESC[$i]}"
    "${parts[@]}"
  done
}

verify_sealed() {
  local problem=""
  [[ -f "$STATE_DIR/firstboot" ]] || problem="the first-start flag is missing"
  systemctl is-enabled --quiet sinko-firstboot.service 2>/dev/null || problem="the first-start service is not enabled"
  compgen -G "$R/etc/ssh/ssh_host_*" >/dev/null && problem="SSH host keys are still there"
  [[ ! -s "$R/etc/machine-id" ]] || problem="the machine id is not empty"
  [[ -z "$problem" ]] || die "sealing did not complete: $problem"
}

build_plan
print_plan
if (( DRY )); then
  printf '\nDry run: nothing was changed.\n'
  exit 0
fi
if (( ! YES )); then
  if ! { : <"$TTY_DEV"; } 2>/dev/null; then
    die "there is no terminal to ask on. Run it from a terminal, or add --yes."
  fi
  printf '\nType SEAL (capital letters) to continue, anything else stops here: '
  answer=""
  read -r answer <"$TTY_DEV" || true
  if [[ "$answer" != SEAL ]]; then
    printf 'Nothing was changed.\n'
    exit 1
  fi
fi

run_actions
verify_sealed
printf '\nSealed. Power the box off now:  sudo poweroff\n'
printf 'Do not start it again before you have copied the card: its first start personalises it, and that uses up the seal.\n'
