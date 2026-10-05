#!/usr/bin/env bash
# Seal a "golden unit" before its SD card is copied to an image that is flashed onto many boxes.
#
#   sudo /opt/sinko/tools/seal.sh --dry-run     shows everything it would do and changes nothing
#   sudo /opt/sinko/tools/seal.sh               shows it, then asks you to type SEAL
#
# Sealing removes what must be unique to each box or private to the seller (SSH host keys, machine id, the counter's
# install id, query history, the parent password, two-factor secret and application password so the page shows its
# welcome screen, Pi-hole's own HTTPS key, Pi-hole's saved older settings and databases, the children's devices and timers,
# saved Wi-Fi networks, shell history, logs), turns off Armbian's automatic root login on the console, arms
# sinko-firstboot.service, which gives every box its own keys, name and address when it first starts, and, as the very
# last step, locks the root password and the password logins over SSH. Then it writes zeros over the free space, because
# deleting a file only frees its blocks. Power the unit off right after sealing and do not start it again before you copy
# the card: starting it uses up the seal.
#
# It first checks that the unit has what the customer's first minutes and years depend on (avahi-daemon so that sinko.local
# works, an address from the router, the services enabled, Debian's automatic security updates, a time service, and no
# account other than root that can log in on the console) and refuses to seal otherwise.
#
# Options:
#   --yes               do not ask for SEAL (for scripts)
#   --dry-run           only show what would be done (and what is wrong with this unit)
#   --keep-ssh-access   leave the root password, SSH password logins and the authorized keys alone (a test unit)
#   --hostname NAME     the name of every box, so that http://NAME.local/ opens the page (default: sinko)
#   --no-zerofill       do NOT overwrite the free space (a test unit only: what was deleted can then be read from the
#                       image with ordinary undelete tools; zeros are the default, --zerofill is accepted and does nothing)
#   --skip-checks       do not check that the unit is ready (a test unit only)
#
# Pi-hole's own groups, lists and clients are never touched, only Sinko's (names starting with pb-), through the sinko
# program. The Pi-hole files deleted directly, while Pi-hole's FTL is stopped: the query history (and its temporary
# copy), the HTTPS key and certificates, the saved older settings (config_backups) and the older gravity databases.
set -Eeuo pipefail

R="${SINKO_ROOT:-}"                                  # test hook: act on a fake root
[[ -z "$R" || "$R" == /* ]] || { echo "seal: SINKO_ROOT must be an absolute path" >&2; exit 2; }
APP_DIR="$R/opt/sinko"
BIN="$APP_DIR/bin/sinko"
CONF_FILE="$R/etc/sinko/config"
STATE_DIR="${SINKO_STATE_DIR:-$R/var/lib/sinko}"
UNIT_DIR="$R/etc/systemd/system"
TTY_DEV="${SINKO_TTY:-/dev/tty}"                     # the terminal; SINKO_TTY is a test hook

YES=0 DRY=0 KEEP_SSH=0 ZEROFILL=1 SKIP_CHECKS=0 NAME=sinko
TLS_CERT=""
ZEROFILE="$R/.sinko-zerofill"                        # the file the zero-fill writes: it fills the whole card while it exists

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
    --no-zerofill) ZEROFILL=0 ;;
    --skip-checks) SKIP_CHECKS=1 ;;
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

# ------------------------------------------------------------------ is this unit ready to be copied?
PROBLEMS=()
problem() { PROBLEMS+=("$*"); }

# A netplan file that holds Wi-Fi settings and wired ones too cannot be cleaned by deleting it (the wired network would go).
netplan_has_wired() { grep -qE '^[[:space:]]+(ethernets|bridges|bonds|vlans|tunnels|modems|vrfs|dummy-devices|nm-devices|virtual-ethernets):' "$1"; }
netplan_has_wifi() { grep -qE '^[[:space:]]+(wifis|access-points):' "$1"; }

# Is the package really installed? (`dpkg -s` also answers yes for one that was removed but not purged.) dpkg's status words are
# English in every locale; the C locale is asked for anyway.
# shellcheck disable=SC2016  # ${Status} is dpkg's own field name, not a variable of ours
pkg_installed() { [[ "$(LC_ALL=C dpkg-query -W -f='${Status}' "$1" 2>/dev/null)" == "install ok installed" ]]; }

# The last APT::Periodic::Unattended-Upgrade setting in apt's configuration, as apt reads it (the files of apt.conf.d in the
# order of their names, those with no extension or .conf, and the last value wins): prints "<value> <file>", or nothing when
# no file sets it. The files are read, not asked of apt-config: the fake root of the tests, and a seal that must not depend on
# a program's idea of the box it runs on. Only the plain form is understood (APT::Periodic::Unattended-Upgrade "1";), which is
# the one Debian's own package and this installer write.
apt_periodic_setting() {
  local f v last=""
  while IFS= read -r -d '' f; do
    [[ "${f##*/}" =~ ^[A-Za-z0-9_-]+(\.conf)?$ ]] || continue
    v="$(sed -n 's/^[[:space:]]*APT::Periodic::Unattended-Upgrade[[:space:]]*"\([^"]*\)".*/\1/p' "$f" | tail -n1)"
    if [[ -n "$v" ]]; then last="$v $f"; fi
  done < <(find "$R/etc/apt/apt.conf.d" -maxdepth 1 -type f -print0 2>/dev/null | LC_ALL=C sort -z)
  printf '%s' "$last"
}

# Accounts other than root that can log in on the console (a keyboard on the HDMI port, or the serial port): a password hash or
# an EMPTY password field (Debian's PAM lets an empty password in: that is what `passwd -d` makes, and what Armbian's first-login
# wizard leaves when no password was typed), not a locked ("!...") or missing ("*...") one, and a shell that is not nologin or
# false. Read from shadow and passwd of the box (of the fake root in the tests, which `getent` would not see). Root is not meant:
# its password is locked by the last step.
loginable_accounts() {
  local name hash shell
  [[ -r "$R/etc/shadow" ]] || return 0
  while IFS=: read -r name hash _; do
    [[ -n "$name" && "$name" != root ]] || continue
    [[ "$hash" == '!'* || "$hash" == '*'* ]] && continue
    shell="$(awk -F: -v n="$name" '$1 == n {print $7; exit}' "$R/etc/passwd" 2>/dev/null || true)"
    case "$shell" in */nologin|*/false) continue ;; esac
    printf '%s\n' "$name"
  done <"$R/etc/shadow"
}

preflight() {
  local unit f iface line value
  for unit in sinko.service sinko-lists.timer pihole-FTL.service; do
    systemctl is-enabled --quiet "$unit" 2>/dev/null \
      || problem "$unit is not enabled: a box made from this unit would start without it (systemctl enable $unit)"
  done
  # The quick-start card's second step is http://<name>.local/: that needs avahi on every box.
  if ! grep -qs '^SINKO_MDNS=0' "$CONF_FILE"; then
    pkg_installed avahi-daemon \
      || problem "avahi-daemon is not installed, so http://$NAME.local/ would not open on any box (run the installer again with internet; it installs it)"
    systemctl is-enabled --quiet avahi-daemon.service 2>/dev/null \
      || problem "avahi-daemon is not enabled (systemctl enable avahi-daemon)"
  fi
  # docs/selling.md promises Debian's security updates on every box, and a box nobody can log in to can never be repaired
  # afterwards. The installer only warns when it could not set them up (no internet), so a unit can reach this point without.
  # A unit that was set up with SINKO_OS_UPDATES=0 chose not to have them.
  if ! grep -qs '^SINKO_OS_UPDATES=0' "$CONF_FILE"; then
    pkg_installed unattended-upgrades \
      || problem "unattended-upgrades is not installed, so no box would get Debian's security updates (run the installer again with internet; it installs it)"
    value="$(apt_periodic_setting)"
    if [[ -z "$value" ]]; then
      problem "automatic updates are not switched on: no file in /etc/apt/apt.conf.d sets APT::Periodic::Unattended-Upgrade to \"1\" (dpkg-reconfigure -plow unattended-upgrades does it; the installer leaves an unattended-upgrades that is already installed as it is)"
    elif [[ ! "${value%% *}" =~ ^[1-9][0-9]*$ ]]; then
      problem "automatic updates are switched off: APT::Periodic::Unattended-Upgrade is \"${value%% *}\" in ${value#* } (set it to \"1\" there, or run dpkg-reconfigure -plow unattended-upgrades; the installer leaves an unattended-upgrades that is already installed as it is)"
    fi
    for unit in apt-daily.timer apt-daily-upgrade.timer; do
      systemctl is-enabled --quiet "$unit" 2>/dev/null \
        || problem "$unit is not enabled, so Debian's security updates would never run (systemctl enable $unit)"
    done
  fi
  # The board has no battery-backed clock: without a time service every box starts at the time it last saved, bedtime follows it,
  # and https downloads (updates, lists) can fail on it. The same list as the installer's.
  for unit in systemd-timesyncd.service chrony.service chronyd.service ntpsec.service ntp.service openntpd.service; do
    systemctl is-enabled --quiet "$unit" 2>/dev/null && break
    unit=""
  done
  [[ -n "$unit" ]] \
    || problem "no time service (systemd-timesyncd, chrony, ntp) is switched on: the boxes have no battery-backed clock, so their time would be wrong after every power cut (run the installer again with internet; it installs systemd-timesyncd)"
  # What every box follows. A unit pinned to a version, or installed from a branch (the developer path), sends out boxes that are
  # never offered an update: the page offers nothing newer on a pinned box, and nothing at all on one that follows a branch.
  value="$(sed -n 's/^SINKO_REF=//p' "$CONF_FILE" 2>/dev/null | tail -n1 || true)"
  value="${value//[\'\"]/}"
  if [[ -n "$value" && "$value" != latest ]]; then
    problem "this unit follows '$value' (SINKO_REF in $CONF_FILE), so no box made from it would ever be offered an update: set SINKO_REF=latest there (or run: sudo sinko update --ref latest --force)"
  fi
  # The wizard of Armbian's first login makes a user (with sudo) from what the seller types; the seal locks root, not that
  # account, and a box would let anybody with a keyboard in with the seller's password. Not with --keep-ssh-access (a test unit).
  if (( ! KEEP_SSH )); then
    while IFS= read -r f; do
      [[ -n "$f" ]] || continue
      problem "the account '$f' can log in on the console of every box (a keyboard on the HDMI port, or the serial port) with the password you gave it, and is probably in the sudo group: delete it with  userdel -r $f  (or lock it with  passwd -l $f), then seal again"
    done < <(loginable_accounts)
  fi
  # Every box takes the address its router gives it. A unit with a fixed address would send every box to that address.
  iface="$(ip -4 route show default 2>/dev/null | awk '{for (i=1;i<NF;i++) if ($i=="dev") {print $(i+1); exit}}')"
  if [[ -z "$iface" ]]; then
    problem "this unit has no network connection (no default route), so its address settings cannot be checked: connect the cable"
  else
    line="$(ip -4 -o addr show dev "$iface" scope global 2>/dev/null | head -n1)"
    grep -q dynamic <<<"$line" \
      || problem "$iface has a fixed address (not one from the router's DHCP): every box would keep it. Set the unit to DHCP first"
  fi
  if (( ! KEEP_SSH )) && [[ -f "$R/etc/ssh/sshd_config" ]]; then
    grep -qiE '^[[:space:]]*Include[[:space:]].*sshd_config\.d' "$R/etc/ssh/sshd_config" \
      || problem "$R/etc/ssh/sshd_config does not include /etc/ssh/sshd_config.d, so the lock-down of SSH password logins would be ignored: add the line  Include /etc/ssh/sshd_config.d/*.conf"
  fi
  for f in "$R"/etc/netplan/*.yaml; do
    [[ -f "$f" ]] || continue
    if netplan_has_wifi "$f" && netplan_has_wired "$f"; then
      problem "$f has Wi-Fi and wired settings in one file: take the Wi-Fi part out by hand (it holds the Wi-Fi password)"
    fi
  done
  return 0
}

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

# The folders that hold logs: /var/log and, on Armbian, the card's own copy of it. Armbian keeps /var/log in memory
# (armbian-ramlog) and copies it to the card at shutdown, without deleting what is on the card; the rotated logs (Pi-hole's
# query log among them, one file per day) are never loaded into memory, so they only ever exist in that copy.
# Its settings call it HDD_LOG: /var/log.hdd by default. (It is a mount point: its files are cleaned, it is never removed.)
log_dirs() {
  local hdd=/var/log.hdd cfg="$R/etc/default/armbian-ramlog" v
  if [[ -r "$cfg" ]]; then
    v="$(sed -n 's/^[[:space:]]*HDD_LOG=//p' "$cfg" | tail -n1 | tr -d "\"'" || true)"
    if [[ "$v" == /* && "$v" != *..* ]]; then hdd="$v"; fi
  fi
  if [[ -d "$R/var/log" ]]; then printf '%s\n' "$R/var/log"; fi
  if [[ -d "$R$hdd" && "$R$hdd" != "$R/var/log" ]]; then printf '%s\n' "$R$hdd"; fi
  return 0
}
# The logs: files to empty (so their owners and modes stay as the services expect) and rotated copies to delete.
# Of Pi-hole's own files only the query database may be deleted, so even its rotated logs are emptied, never removed.
is_rotated() {
  case "$1" in */pihole/*) return 1 ;; esac
  case "$1" in
    *.gz|*.xz|*.bz2|*.zst|*.old|*.[0-9]|*.[0-9][0-9]|*-20[0-9][0-9][0-9][0-9][0-9][0-9]) return 0 ;;
    *) return 1 ;;
  esac
}
log_files() {  # prints the log files of every log folder, outside the journal
  local d
  while IFS= read -r d; do
    find "$d" -xdev -path "$d/journal*" -prune -o -type f -print 2>/dev/null
  done < <(log_dirs) | LC_ALL=C sort -u || true
}
archived_journals() {
  local d
  while IFS= read -r d; do
    [[ -d "$d/journal/" ]] || continue            # the trailing slash: Armbian makes /var/log/journal a link into the card's copy
    find "$d/journal/" -type f \( -name '*@*.journal*' -o -name '*.journal~' \) 2>/dev/null
  done < <(log_dirs) | LC_ALL=C sort -u || true
}

# FTL's own answer, without quotes and blanks (a write-only secret prints ******** when it is set, and nothing when not).
# Fails when FTL cannot be asked: "cannot tell" must never count as "empty".
ftl_value() {  # key
  local out
  out="$(pihole-FTL --config "$1" 2>/dev/null)" || return 1
  printf '%s' "$out" | head -n1 | tr -d "[:space:]\"'"
}
# Where Pi-hole's own settings say a file is (the default when they say nothing), checked before anything is deleted there.
ftl_path() {  # setting-key default-path [suffix]
  local p
  p="$(pihole-FTL --config "$1" 2>/dev/null | head -n1 || true)"
  [[ -n "$p" ]] || p="$R$2"
  [[ "$p" == /* && "$p" != *..* && ( -z "$R" || "$p" == "$R"/* ) && "$p" == *${3:-} ]] \
    || die "unexpected location of Pi-hole's $1: $p"
  printf '%s' "$p"
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

# Console logins that need no password. Armbian's first-login wizard removes them; a locked root password does not stop them
# (agetty --autologin starts the login without asking). The two globs are kept apart on purpose: only a getty drop-in
# that really says --autologin is meant, not every file with a User= line.
autologin_files() {
  local f
  for f in "$UNIT_DIR"/*getty*.service.d/*.conf "$UNIT_DIR"/*getty*.service; do
    if [[ -f "$f" ]] && grep -qE -- '--autologin' "$f"; then existing "$f"; fi
  done
  for f in "$R"/etc/lightdm/lightdm.conf.d/*.conf "$R"/etc/sddm.conf.d/*.conf; do
    if [[ -f "$f" ]] && grep -qiE '^[[:space:]]*(autologin-user|User)[[:space:]]*=[[:space:]]*[^[:space:]]' "$f"; then existing "$f"; fi
  done
  return 0
}

# Wi-Fi networks somebody set up on this unit (Armbian's first-login wizard offers it): the keys are in plain text.
wifi_files() {
  local f
  for f in "$R"/etc/netplan/*.yaml; do
    [[ -f "$f" ]] || continue
    if netplan_has_wifi "$f" && ! netplan_has_wired "$f"; then printf '%s\n' "$f"; fi
  done
  for f in "$R"/etc/NetworkManager/system-connections/*; do
    if [[ -f "$f" ]] && grep -qiE '^[[:space:]]*type[[:space:]]*=[[:space:]]*(wifi|802-11-wireless)[[:space:]]*$' "$f"; then printf '%s\n' "$f"; fi
  done
  for f in "$R"/etc/wpa_supplicant/*.conf; do
    if [[ -f "$f" ]] && grep -qE '^[[:space:]]*network[[:space:]]*=' "$f"; then printf '%s\n' "$f"; fi
  done
  # Armbian's headless first-start file (the .off copy is what it leaves after it has been used) with a Wi-Fi key in it.
  for f in "$R"/boot/armbian_first_run.txt "$R"/boot/armbian_first_run.txt.off; do
    if [[ -f "$f" ]] && grep -qE "^[[:space:]]*FR_net_wifi_key=['\"]?[^'\"[:space:]]" "$f"; then printf '%s\n' "$f"; fi
  done
  return 0
}
# Files that still carry a network secret after the Wi-Fi networks are gone.
leftover_secrets() {
  grep -rIlsE '^[[:space:]]*(psk|wep-key[0-9]|password|leap-password)[[:space:]]*[=:]|wpa-psk|wpa_psk' \
    "$R/etc/netplan" "$R/etc/NetworkManager/system-connections" "$R/etc/wpa_supplicant" "$R/etc/network/interfaces" "$R/etc/network/interfaces.d" 2>/dev/null || true
  grep -Ils "^[[:space:]]*FR_net_wifi_key=['\"]?[^'\"[:space:]]" "$R"/boot/armbian_first_run.txt* 2>/dev/null || true
}

build_plan() {
  local list kids

  # An earlier seal that was cut short (a power cut, a kill) can have left its zero file: it fills the whole card, so that the
  # box could write nothing (not its database, not the first-start flag), and a card copied in that state would ship so. First.
  if [[ -e "$ZEROFILE" || -L "$ZEROFILE" ]]; then
    add_action "Delete $ZEROFILE, left by an earlier seal that did not finish (it fills the whole card)" act_remove_paths "$ZEROFILE"
  fi
  add_action "Stop the Sinko scheduler and the list refresh (both stay enabled: they start again at the next boot)" act_stop_scheduler
  if grep -qs '^SINKO_TELEMETRY=' "$CONF_FILE"; then
    add_action "Forget the answer to the anonymous-counter question in $CONF_FILE (the customer is asked again)" act_forget_counter_answer
  fi
  kids="$(kid_names)"
  add_action "Remove Sinko's Pi-hole objects and start them again empty: the children's devices${kids:+ ($kids)}, timers, bedtime, blocked apps and the pb-* groups, lists and rules. Your own Pi-hole groups, lists and clients stay." act_reset_pihole
  add_action "Remove the parent password, any two-factor secret and any application password, so that the page shows its welcome screen" act_remove_password
  add_action "Set the host name to $NAME (so http://$NAME.local/ opens the page on every box)" act_hostname
  if [[ -e "$R/dev/watchdog" ]]; then
    add_action "Switch on the hardware watchdog, so a hung box restarts itself (/dev/watchdog was found)" act_watchdog
  else
    add_action "Hardware watchdog: skipped, there is no /dev/watchdog on this device" act_nothing
  fi
  add_action "Arm the first-start service: every box makes its own SSH keys, host name, address and HTTPS key when it first starts" act_arm_first_start

  local db tmpdb gravity gdir
  db="$(ftl_path files.database /etc/pihole/pihole-FTL.db .db)"
  tmpdb="$(ftl_path files.tmp_db /etc/pihole/pihole-tmp.db .db)"
  gravity="$(ftl_path files.gravity /etc/pihole/gravity.db .db)"
  gdir="$(dirname "$gravity")"
  local -a pfiles=()
  mapfile -t pfiles < <(existing "$db" "$db-wal" "$db-shm" "$db-journal" "$tmpdb" "$tmpdb-wal" "$tmpdb-shm" "$tmpdb-journal" \
                                 "$gdir/gravity_old.db" "$gdir/gravity_backups" "$R/etc/pihole/config_backups" "$R/etc/pihole/migration_backup_v6")
  list="$(join_list "${pfiles[@]}")"
  add_action "Stop Pi-hole's FTL and delete the query history, Pi-hole's saved older settings (they hold the old password hash) and its older gravity databases (they list the test devices)${list:+: $list} (FTL stays stopped: the box is powered off next)" act_wipe_pihole "$db" "$tmpdb" "$gdir"

  # After FTL is stopped (it would write a new key at once if it ran), and nothing starts it again before the power-off.
  TLS_CERT="$(ftl_path webserver.tls.cert /etc/pihole/tls.pem)"          # an assignment: an odd location stops the seal
  local -a gone=()
  mapfile -t gone < <(existing "$TLS_CERT" "${TLS_CERT%.pem}.crt" "${TLS_CERT%.pem}_ca.crt")
  (( ${#gone[@]} == 0 )) || add_action "Remove Pi-hole's HTTPS private key and certificates, so that every box makes its own when Pi-hole first starts: $(join_list "${gone[@]}")" act_remove_paths "${gone[@]}"

  mapfile -t gone < <(existing "$R"/etc/ssh/ssh_host_* | LC_ALL=C sort)
  (( ${#gone[@]} == 0 )) || add_action "Remove the SSH host keys: $(join_list "${gone[@]}")" act_remove_paths "${gone[@]}"
  if (( ! KEEP_SSH )); then
    local -a keys=()
    mapfile -t keys < <(existing "$R/root/.ssh/authorized_keys" "$R/root/.ssh/known_hosts" "$R"/home/*/.ssh/authorized_keys "$R"/home/*/.ssh/known_hosts)
    (( ${#keys[@]} == 0 )) || add_action "Remove SSH keys and host lists of people who logged in here: $(join_list "${keys[@]}")" act_remove_paths "${keys[@]}"
  fi
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
  mapfile -t gone < <(autologin_files)
  (( ${#gone[@]} == 0 )) || add_action "Remove the automatic root login on the console (HDMI keyboard / serial port): $(join_list "${gone[@]}")" act_remove_paths "${gone[@]}"
  mapfile -t gone < <(wifi_files)
  (( ${#gone[@]} == 0 )) || add_action "Remove saved Wi-Fi networks and their keys: $(join_list "${gone[@]}")" act_remove_paths "${gone[@]}"
  mapfile -t gone < <(existing "$R/var/lib/systemd/random-seed" "$R/var/lib/systemd/credential.secret" "$R"/var/lib/dhcp/*.leases \
                              "$R"/var/lib/NetworkManager/*.lease "$R/var/lib/NetworkManager/secret_key" "$R/etc/pihole/install.log")
  (( ${#gone[@]} == 0 )) || add_action "Remove the random seed, the network leases of this network, NetworkManager's secret key and Pi-hole's installer log: $(join_list "${gone[@]}")" act_remove_paths "${gone[@]}"
  add_action "Make the machine id empty (/etc/machine-id; every box makes its own at its first start)" act_machine_id

  local d total=0 rotated=0 emptied=0 journals=0 dirs
  while IFS= read -r f; do
    total=$(( total + 1 ))
    if is_rotated "$f"; then rotated=$(( rotated + 1 )); elif [[ -s "$f" ]]; then emptied=$(( emptied + 1 )); fi
  done < <(log_files)
  journals="$(archived_journals | grep -c . || true)"
  dirs=""
  while IFS= read -r d; do dirs+="${dirs:+ and }$d"; done < <(log_dirs)
  add_action "Clear the logs: empty $emptied log files (Pi-hole's too: they list every site that was asked for) and delete $rotated rotated ones under $dirs (on Armbian the second folder is the card's own copy of the first; the files stay, with their owners; Pi-hole's are only emptied), the journal's old files ($journals), downloaded package files" act_clean_logs
  if (( ZEROFILL )); then
    add_action "Write zeros over the free space and delete them: deleting only frees blocks, and everything deleted above could still be read from the image (takes a while)" act_zerofill
  else
    add_action "WARNING: the free space is NOT overwritten (--no-zerofill). The query history, keys, logs and old settings deleted above can still be read from the image with ordinary undelete tools. A test unit only: never copy this card for customers." act_nothing
  fi
  # The very last step, after everything that can still go wrong: a seller who is locked out cannot repair the unit.
  if (( ! KEEP_SSH )); then
    add_action "Lock the root password and turn off SSH password logins (a drop-in in /etc/ssh/sshd_config.d; use --keep-ssh-access to skip this). From here on nobody can log in over SSH with a password, including you" act_lock_ssh
  else
    add_action "Leave the root password, SSH password logins and authorized keys as they are (--keep-ssh-access)" act_nothing
  fi
}

# ------------------------------------------------------------------ the actions
act_nothing() { echo "  (nothing to do)"; }

act_stop_scheduler() {
  systemctl stop sinko.service sinko-update.service sinko-lists.timer 2>/dev/null || true
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
# password would be a back door in every home. Two more secrets would be just as bad, and Pi-hole's "is a password set"
# answer does not show them: a two-factor secret (after the parent has chosen a password every sign-in would ask for a
# code from the seller's authenticator, and nobody could sign in) and an application password (it signs in to the API
# next to the parent's password, without a code). They are read back from FTL itself.
no_password_now() {
  local rc=0 key v
  "$BIN" password-state >/dev/null 2>&1 || rc=$?
  [[ "$rc" -eq 3 ]] || return 1
  for key in totp_secret app_pwhash; do
    v="$(ftl_value "webserver.api.$key")" || return 1
    [[ -z "$v" ]] || return 1
  done
}
act_remove_password() {
  local i
  # First, and before the loops below (they end as soon as the web password is gone): the secrets that check cannot see.
  pihole-FTL --config webserver.api.totp_secret "" >/dev/null 2>&1 || true
  pihole-FTL --config webserver.api.app_pwhash "" >/dev/null 2>&1 || true
  pihole-FTL --config webserver.api.app_sudo false >/dev/null 2>&1 || true
  pihole-FTL --config webserver.api.password "" >/dev/null 2>&1 || true
  for i in 1 2 3 4 5 6 7 8 9 10; do no_password_now && { echo "  no password, two-factor secret or application password is set"; return 0; }; sleep 1; done
  pihole-FTL --config webserver.api.pwhash "" >/dev/null 2>&1 || true
  for i in 1 2 3 4 5 6 7 8 9 10; do no_password_now && { echo "  no password, two-factor secret or application password is set"; return 0; }; sleep 1; done
  die "the parent password, a two-factor secret or an application password could not be removed. Nothing was armed; sealing now would leave your credentials on every box."
}

# The settings sshd would use, read back from sshd itself (left in SSHD_EFFECTIVE, one "name value" per line, names in lower
# case); fails with the reason in SSHD_WHY (call it directly, not in a subshell, so that both stay set).
# By the time this runs the host keys are gone (they have to be: the plan removes them earlier), and
# `sshd -T` refuses to run without one ("sshd: no hostkeys available -- exiting.", exit status 1, nothing printed): the first
# version of this read-back therefore reported nothing on every real box, and the seal went on with a warning. So `sshd -G` is
# asked first: it prints the effective settings without loading host keys (OpenSSH 9.x and newer; Debian 13 has 10.0). An older
# sshd has no -G (usage error), and `sshd -T -h KEY` is run with a throw-away key then.
# What this does and does not prove: that the drop-in is in effect for a login that matches no "Match" block, which catches an
# sshd_config without the Include line, a PasswordAuthentication line above it, or an earlier file in the folder. A "Match User"
# or "Match Address" block that turns password logins on again would be shown only with connection details (-C), and none are
# given: it is not looked at (and a box with such a block is not a stock Armbian).
SSHD_WHY="" SSHD_EFFECTIVE=""
sshd_effective_settings() {
  local cfg=() out tmp
  [[ -z "$R" ]] || cfg=(-f "$R/etc/ssh/sshd_config")
  if out="$(sshd -G "${cfg[@]}" 2>&1)"; then SSHD_EFFECTIVE="$out"; return 0; fi
  SSHD_WHY="sshd -G: $(head -n1 <<<"$out")"
  tmp="$(mktemp -d)" || { SSHD_WHY="no temporary folder"; return 1; }
  if ssh-keygen -q -t ed25519 -N '' -f "$tmp/key" >/dev/null 2>&1; then
    if out="$(sshd -T -h "$tmp/key" "${cfg[@]}" 2>&1)"; then rm -rf "$tmp"; SSHD_EFFECTIVE="$out"; return 0; fi
    SSHD_WHY="$SSHD_WHY; sshd -T: $(head -n1 <<<"$out")"
  else
    SSHD_WHY="$SSHD_WHY; no throw-away host key could be made"
  fi
  rm -rf "$tmp"
  return 1
}

# Last of all. The drop-in is written and read back before the password is locked, so a drop-in that sshd does not use
# (no Include line, a PasswordAuthentication line above it) stops the seal with the root password still working. When it
# stops, the drop-in is taken out again: the box has no keys left by now, and a drop-in that does work would lock the seller
# out of a unit that is not sealed yet.
act_lock_ssh() {
  local dir="$R/etc/ssh/sshd_config.d" file
  file="$dir/00-sinko-lockdown.conf"                 # 00-: for most options the first value found wins
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
    if ! sshd_effective_settings; then
      rm -f "$file"
      die "sshd could not report its settings ($SSHD_WHY), so it is not known whether SSH password logins are off. The drop-in was taken out again. The root password was NOT locked; fix this and run seal again."
    fi
    if ! grep -qi '^passwordauthentication no$' <<<"$SSHD_EFFECTIVE"; then
      rm -f "$file"
      die "SSH password logins are still on: $file is not used by sshd_config (is there no Include of /etc/ssh/sshd_config.d, or a PasswordAuthentication line above it?). The drop-in was taken out again. The root password was NOT locked; fix this and run seal again."
    fi
  fi
  local pw=(passwd)
  [[ -z "$R" ]] || pw+=(-R "$R")
  "${pw[@]}" -l root >/dev/null || die "could not lock the root password"
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

# The Allwinner watchdog (sunxi_wdt, the H616/H618 of the Orange Pi Zero 3 included) takes 1 to 16 seconds at most: a larger
# value is refused by the driver, and systemd then runs without the safety net it was asked for. So both values are 15 seconds.
# What systemd does with them (read in its source, src/core/main.c and src/shutdown/shutdown.c, which are the same in systemd
# 252 of Debian 12 and 257 of Debian 13):
#   * RuntimeWatchdogSec: while the system runs, PID 1 feeds the watchdog at least every half interval, and a box that hangs
#     for 15 seconds is reset by the hardware. That is what the setting is for. It also holds in the first phase of a shutdown
#     or reboot, while PID 1 still runs.
#   * poweroff and halt (the page's "Shut down"): PID 1 closes the watchdog AND DISARMS it (WDIOS_DISABLECARD and the magic
#     close) before it hands over to systemd-shutdown, and tells that program that there is no watchdog (WATCHDOG_USEC=0): only
#     reboot and kexec carry a watchdog time over. So the watchdog cannot turn a shut down into a restart, however the board's
#     power-off ends. This relies on the driver letting it be stopped: sunxi_wdt does, unless the kernel was built with
#     CONFIG_WATCHDOG_NOWAYOUT, which only the board can show (docs/hardware-test-checklist.md: "Shut down stays off").
#   * reboot: RebootWatchdogSec is the safety net of the reboot itself. The watchdog stays armed with that time through
#     systemd-shutdown, which feeds it only at the start of each unmount pass: not during its first sync (it waits as long as the
#     amount of unwritten data keeps shrinking) and not while it ends stray processes. A stall of more than 15 seconds there
#     resets the box in the middle of the shutdown; the file system is recovered from its journal at the next start. That is
#     the price of a net that also catches a reboot that hangs, and 15 seconds is all this driver allows (RebootWatchdogSec=off
#     would take away both the price and the net).
# "ShutdownWatchdogSec" is an obsolete name of RebootWatchdogSec, not another setting: it does not apply to poweroff either.
act_watchdog() {
  local dir="$R/etc/systemd/system.conf.d"
  mkdir -p "$dir"
  {
    echo "# Written by Sinko's tools/seal.sh: systemd keeps the hardware watchdog fed; a hung box restarts itself."
    echo "# 15 seconds: the Allwinner watchdog accepts 16 at most (a longer time is refused and the watchdog would be off)."
    echo "# A shut down (poweroff) disarms the watchdog first; only a reboot keeps it armed (RebootWatchdogSec), see tools/seal.sh."
    echo "[Manager]"
    echo "RuntimeWatchdogSec=15"
    echo "RebootWatchdogSec=15"
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

# FTL is stopped first: it writes these files. The settings were last written by act_remove_password.
act_wipe_pihole() {  # query database, FTL's temporary database, the folder of the gravity database
  local db="$1" tmpdb="$2" gdir="$3"
  systemctl stop pihole-FTL.service || die "could not stop Pi-hole's FTL, so its files cannot be deleted safely"
  rm -f "$db" "$db-wal" "$db-shm" "$db-journal" "$tmpdb" "$tmpdb-wal" "$tmpdb-shm" "$tmpdb-journal" "$gdir/gravity_old.db"
  # Their folders stay (FTL and gravity make them again); what is in them goes: every earlier version of pihole.toml
  # (a seal's own settings changes push the one with the seller's password hash into it) and every earlier gravity database.
  local dir
  for dir in "$R/etc/pihole/config_backups" "$gdir/gravity_backups" "$R/etc/pihole/migration_backup_v6"; do
    if [[ -d "$dir" ]]; then find "$dir" -mindepth 1 -type f -delete; fi
  done
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
  local file="$ZEROFILE" out rc=0
  trap 'rm -f "$file"' EXIT                          # (a power cut skips it: the next seal and the first start delete the file)
  # sync first: the file system does not hand out the blocks that were freed a moment ago (by the deletions above) until
  # the journal has committed that, so without it the zeros would be written around exactly the data they are meant to hide.
  sync
  # dd ends with "No space left on device" on purpose: that is how the free space gets filled. Any other end (an input/output
  # error of a worn card, a file system that went read-only, a kill) leaves the free space unwritten and what was deleted
  # readable in the image: that is a failure, and the seal must not go on to lock the box and say "Sealed". The message is
  # read in the C locale.
  out="$(LC_ALL=C dd if=/dev/zero of="$file" bs=1M conv=fsync 2>&1 >/dev/null)" || rc=$?
  sync
  rm -f "$file"
  sync
  trap - EXIT
  if [[ "$out" != *"No space left on device"* ]]; then
    local why="dd ended with status $rc and said nothing"
    if [[ -n "$out" ]]; then why="dd said: $(head -n1 <<<"$out")"; fi
    die "overwriting the free space did not work ($why): what was deleted can still be read from the card. The zero file was removed again. Fix the problem (a worn card, a read-only file system) and run the seal again."
  fi
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
    printf '\n%s\n' "The last step locks SSH password logins (including yours): run this from the console or keep this session open, and power the box off when it is done."
  fi
}

# What must be true of a sealed unit. Every problem is collected and shown; the lock-down (the last step) is not checked
# here, it checks itself. Run before that last step, while the seller can still log in to fix things.
verify_core() {
  PROBLEMS=()
  local f
  [[ -f "$STATE_DIR/firstboot" ]] || problem "the first-start flag is missing"
  [[ ! -e "$ZEROFILE" && ! -L "$ZEROFILE" ]] || problem "the zero file $ZEROFILE is still there: it fills the whole card"
  # (not with --keep-ssh-access, a test unit). Run here too, and not only before the plan, so that --skip-checks cannot get
  # a console login with the seller's password into the image; the root password is not locked yet and the seller can fix it.
  if (( ! KEEP_SSH )) && [[ -n "$(loginable_accounts)" ]]; then
    problem "an account other than root can still log in on the console: $(loginable_accounts | head -n1)"
  fi
  systemctl is-enabled --quiet sinko-firstboot.service 2>/dev/null || problem "the first-start service is not enabled"
  compgen -G "$R/etc/ssh/ssh_host_*" >/dev/null && problem "SSH host keys are still there"
  [[ ! -s "$R/etc/machine-id" ]] || problem "the machine id is not empty"
  [[ -z "$(existing "$TLS_CERT" "${TLS_CERT%.pem}.crt" "${TLS_CERT%.pem}_ca.crt")" ]] || problem "Pi-hole's HTTPS private key or certificate is still there"
  local backups
  for backups in config_backups migration_backup_v6; do       # the second is what Pi-hole keeps of a version 5 setup (setupVars.conf: its password hash)
    if [[ -d "$R/etc/pihole/$backups" ]] && [[ -n "$(find "$R/etc/pihole/$backups" -mindepth 1 -type f -print -quit)" ]]; then
      problem "Pi-hole's saved older settings ($backups, with the old password hash) are still there"
    fi
  done
  [[ -z "$(autologin_files)" ]] || problem "the console still logs root in without a password"
  [[ -z "$(wifi_files)" ]] || problem "a saved Wi-Fi network is still there"
  [[ -z "$(leftover_secrets)" ]] || problem "a network secret is still in a file: $(leftover_secrets | head -n1)"
  credentials_clear || problem "a Pi-hole password, two-factor secret or application password is still set"
  while IFS= read -r f; do
    # the card's own copy of the logs is not written to until the shutdown: nothing may be left in it
    case "$f" in "$R/var/log"/*) continue ;; esac
    if is_rotated "$f" || { [[ -s "$f" && "$(basename "$f")" != armbian-ramlog.log ]]; }; then problem "the card still holds log content: $f"; break; fi
  done < <(log_files)
  (( ${#PROBLEMS[@]} == 0 )) || die "sealing did not complete: $(join_list "${PROBLEMS[@]}")"
}
credentials_clear() {  # no web password, two-factor secret or application password is stored, read back from FTL
  local key v
  for key in password pwhash totp_secret app_pwhash; do
    v="$(ftl_value "webserver.api.$key")" || return 1
    [[ -z "$v" ]] || return 1
  done
}

CORE_VERIFIED=0
run_actions() {
  local i parts=()
  for i in "${!ACT_CMD[@]}"; do
    IFS=$'\x1f' read -r -a parts <<<"${ACT_CMD[$i]}"
    if [[ "${parts[0]}" == act_lock_ssh ]]; then verify_core; CORE_VERIFIED=1; fi
    printf '\n==> %s\n' "${ACT_DESC[$i]}"
    "${parts[@]}"
  done
  (( CORE_VERIFIED )) || verify_core
}

if (( ! SKIP_CHECKS )); then
  preflight
  if (( ${#PROBLEMS[@]} )); then
    printf '\nThis unit is not ready to be sealed:\n\n' >&2
    for p in "${PROBLEMS[@]}"; do printf '  - %s\n' "$p" >&2; done
    printf '\nNothing was changed. Fix this, or use --skip-checks for a test unit.\n' >&2
    exit 1
  fi
else
  echo "(--skip-checks: the unit was not checked)"
fi
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
printf '\nSealed. Power the box off now:  unset HISTFILE; sudo poweroff\n'
printf 'Do not start it again before you have copied the card: its first start personalises it, and that uses up the seal.\n'
printf 'A sealed box has no password until a parent chooses one on its welcome screen, and the first device that opens the page decides:\n'
printf 'do not power a sealed unit on in a network other people use, and re-flash a unit that was started.\n'
if (( ! ZEROFILL )); then
  printf '\nWARNING: the free space was NOT overwritten (--no-zerofill): what was deleted can still be read from the image. Do not ship this card.\n'
fi
