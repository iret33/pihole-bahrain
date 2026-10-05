#!/usr/bin/env bash
# Sinko — installer and updater.
#
#   curl --proto '=https' --proto-redir '=https' -fsSL https://github.com/iret33/sinko/releases/latest/download/install.sh | sudo bash
#
# Installs Pi-hole v6 (if missing, fully unattended), the parent page, the
# scheduler service and the service block lists. Safe to run again: it then
# updates everything and keeps your password, devices and rules.
#
# By default it downloads the newest release (sinko.tar.gz) from GitHub, checks it
# against the published sha256 and refuses to install it if the two differ.
#
# Optional settings (environment variables, e.g. `curl … | sudo SINKO_HOSTNAME=kids.home bash`).
# What you set once is saved in /etc/sinko/config and kept by later runs and updates (except the password, which only
# Pi-hole keeps).
#   SINKO_PASSWORD        parent password. Otherwise you are asked (or, on a new installation nobody is watching, one is
#                      generated; an update never chooses one). Better not to use it on a shared computer: a command
#                      line with the password in it (also `sudo SINKO_PASSWORD=… bash`: sudo's own arguments) stays in
#                      the shell history and shows in the process list while it runs. The installer removes it from
#                      its own environment at once. How Pi-hole gets it depends on the Pi-hole:
#                        * One that has no password yet (a new installation, a ready-made box nobody has claimed) gets it
#                          through its API, in the body of a request on this machine: never on a command line.
#                        * One that already has a password does not take a new one that way (it refuses configuration
#                          changes from the installer's command-line session), so Pi-hole's own `pihole setpassword` is
#                          used. Pi-hole's program takes a password as an argument and in no other way: for a moment the
#                          password is in that program's arguments, which every local user can read in the process list.
#   SINKO_HOSTNAME        local name for the page, default family.lan ("none" to skip)
#   SINKO_UPSTREAMS       upstream DNS for a NEW Pi-hole, default 1.1.1.3,1.0.0.3
#                      (Cloudflare for Families: also blocks malware and adult sites)
#   SINKO_TIMEZONE        e.g. Asia/Bahrain. Default: Asia/Bahrain if the system is on UTC.
#   SINKO_LISTS_BASE      where Pi-hole downloads the service lists
#   SINKO_REPO_SLUG       the GitHub project as owner/name, default iret33/sinko
#   SINKO_REF             what to install: latest (default) = the newest release,
#                      vX.Y.Z = that release, anything else (a branch such as master)
#                      = developer path: git clone of that branch of SINKO_REPO
#   SINKO_REPO            git URL for the developer path, default https://github.com/<slug>.git
#   SINKO_SRC             a folder with an already extracted release: nothing is downloaded
#                      (used by "sinko update", "sinko rollback", image builds and tests)
#   SINKO_RELEASE_BASE    where releases are downloaded from, default https://github.com/<slug>/releases. https only
#                      (plain http is accepted only for this machine itself). Saved when it is not the default, so
#                      "sinko update" and the update check use it too. SINKO_RELEASE_API (the latest-release
#                      address, default https://api.github.com/repos/<slug>/releases/latest) is kept the same way.
#   SINKO_TELEMETRY       1 = count this box in the anonymous number of Sinko boxes online, 0 = do not.
#                      Interactive installs ask (default no). Not asked and not set: nothing is saved
#                      and the parent page asks later. What is sent: docs/privacy.md
#   SINKO_MDNS            0 = do not install avahi-daemon (the box answers to <hostname>.local when it is there)
#   SINKO_OS_UPDATES      0 = do not set up automatic Debian security updates (never a restart; left alone when
#                      unattended-upgrades is already installed)
#   SINKO_NONINTERACTIVE  1 = never prompt
#
# Exit status: 0 = done, 1 = failed (the log says why), 75 = not enough free disk space (nothing was changed: "sinko update"
# can report that as a disk problem and not as a failed update).
set -Eeuo pipefail
shopt -s inherit_errexit   # also stop on failures inside $(…), e.g. a failed download

main() {
  # The parent password may arrive in the environment. It is taken out of it at once, into a variable that is not
  # exported, so none of the programs started below (apt, Pi-hole's installer, curl, git) inherit it. The one place it
  # is handed on again is the line that starts the installer shipped inside the release (see below).
  PASSWORD_GIVEN="${SINKO_PASSWORD-${PB_PASSWORD-}}"
  unset SINKO_PASSWORD PB_PASSWORD
  import_legacy_env        # an old "pihole-bahrain update" runs this installer with PB_* variables
  # ---------------------------------------------------------------- constants
  local R="${SINKO_ROOT:-}"                        # test hook: install under a fake root
  [[ -z "$R" || "$R" == /* ]] || die "SINKO_ROOT must be an absolute path."
  APP_DIR="$R/opt/sinko"
  CONF_DIR="$R/etc/sinko"
  CONF_FILE="$CONF_DIR/config"
  STATE_DIR="${SINKO_STATE_DIR:-$R/var/lib/sinko}"   # root-only runtime data (see docs/maintainers/architecture.md)
  TOML="$R/etc/pihole/pihole.toml"
  UNIT_DIR="$R/etc/systemd/system"
  BIN_LINK="$R/usr/local/bin/sinko"
  LOG_FILE="$R/var/log/sinko-install.log"
  DEFAULT_SLUG="iret33/sinko"
  DEFAULT_REF="latest"
  # What pihole-bahrain (<= 2.2.x) left behind; see "Migration from pihole-bahrain" in docs/maintainers/architecture.md.
  LEGACY_APP="$R/opt/pihole-bahrain"
  LEGACY_CONF_DIR="$R/etc/pihole-bahrain"
  LEGACY_BIN="$R/usr/local/bin/pihole-bahrain"
  LEGACY_LOG="$R/var/log/pihole-bahrain-install.log"
  LEGACY_UNITS=(pihole-bahrain.service pihole-bahrain-lists.service pihole-bahrain-lists.timer)
  MIGRATING=0 LEGACY_STOPPED=0 NEW_SCHEDULER_UP=0 EXISTING_INSTALL=0 PASSWORD_SHOWN=0
  MIN_FREE_FIRST_MB=1024 MIN_FREE_UPDATE_MB=200     # free disk space needed (see preflight)

  mkdir -p "$(dirname "$LOG_FILE")"
  if [[ "${SINKO_REEXEC:-}" != 1 ]]; then
    # Older versions printed the generated parent password into this log. Remove such lines now,
    # while nothing has the file open: sed -i replaces the file, so doing it after tee has opened
    # it would send the rest of this run into a deleted file. Then keep the log root-only.
    if [[ -f "$LOG_FILE" ]]; then
      sed -i '/^[[:space:]]*Password:/d' "$LOG_FILE"
    fi
    ( umask 077; : >>"$LOG_FILE" )
    chmod 600 "$LOG_FILE"
    exec > >(tee -a "$LOG_FILE") 2>&1
  fi
  trap 'on_error $LINENO' ERR
  trap on_exit EXIT
  echo
  echo "=== sinko installer — $(date -u '+%Y-%m-%d %H:%M:%S UTC') ==="

  if [[ "${SINKO_REEXEC:-}" != 1 ]]; then
    # One installer at a time (a parent running it by hand while "Update now" is working would corrupt both).
    # The re-executed installer inherits this descriptor, and with it the lock. The lock file lives in the state
    # folder (root only), not in /run/lock: that folder is writable by every user, so another account could keep the
    # file or hold its lock and stop every update. (It is not the updater's own lock, "lock" in the same folder: the
    # updater holds that one while it runs this installer.)
    install -d -m 700 "$STATE_DIR"
    local lock="$STATE_DIR/install.lock"
    { : >>"$lock"; } 2>/dev/null || die "Cannot open the lock file $lock. Is $STATE_DIR writable by root only?"
    chmod 600 "$lock"
    exec 9>>"$lock"
    flock -n 9 || die "Another Sinko installation or update is running. Wait until it has finished, then try again."
  fi

  preflight
  load_settings
  local src
  src="$(locate_source)"
  if [[ "${SINKO_REEXEC:-}" != 1 && ! "${BASH_SOURCE[0]:-}" -ef "$src/install.sh" ]]; then
    # Always run the installer that ships with the code we are installing.
    [[ -f "$src/install.sh" ]] || die "$src has no install.sh."
    step "Starting the installer from the downloaded version"
    # The password (if one was given) goes to the installer that does the work, which takes it out of its environment
    # again before it starts anything else.
    if [[ -n "$PASSWORD_GIVEN" ]]; then export SINKO_PASSWORD="$PASSWORD_GIVEN"; fi
    SINKO_REEXEC=1 SINKO_SRC="$src" exec bash "$src/install.sh"
  fi
  SRC="$src"
  VERSION="$(tr -d '[:space:]' <"$SRC/VERSION" 2>/dev/null || echo unknown)"
  # Only what a version looks like: it is written into the start page's addresses (see stamp_page), so nothing else may get in.
  [[ "$VERSION" =~ ^[0-9A-Za-z._-]{1,40}$ ]] || die "The VERSION file of this release does not hold a version number ('${VERSION:0:40}'). Nothing was changed."
  ok "Installing version $VERSION from $SRC"

  ask_telemetry             # first, so that whoever is at the keyboard can leave after the questions
  detect_network
  install_pihole
  detect_legacy
  install_files
  set_timezone
  seed_config_from_legacy   # so that configure_pihole knows the name the old version put in Pi-hole
  configure_pihole          # before write_settings: it reads the previous hostname
  write_settings
  set_password
  stop_legacy_scheduler     # as late as it can be: from here until install_services the box has no scheduler of its own
  step "Setting up groups, rules and block lists (downloads lists, can take a minute)"
  "$BIN_LINK" setup
  ok "Pi-hole is set up"
  install_services
  install_local_name
  install_os_updates
  install_time_sync
  write_box_info "the settings and the local name are final now"
  step "Final check"
  "$BIN_LINK" doctor || warn "Some checks failed — see above. Run 'sudo sinko doctor' again later."
  finish_legacy_migration   # the very last change: the running installer may live inside the old folder
  summary
  show_generated_password
}

# ------------------------------------------------------------------ output
if [[ -t 1 ]]; then B=$'\e[1m'; G=$'\e[32m'; Y=$'\e[33m'; RD=$'\e[31m'; N=$'\e[0m'; else B=; G=; Y=; RD=; N=; fi
step() { printf '\n%s==>%s %s\n' "$B" "$N" "$*"; }
ok()   { printf '  %s✓%s %s\n' "$G" "$N" "$*"; }
warn() { printf '  %s!%s %s\n' "$Y" "$N" "$*"; }
die()  { printf '\n%sError:%s %s\n' "$RD" "$N" "$*" >&2; exit 1; }
die_with_status() { local code="$1"; shift; printf '\n%sError:%s %s\n' "$RD" "$N" "$*" >&2; exit "$code"; }
on_error() {
  printf '\n%sInstallation failed%s (line %s). Full log: %s\n' "$RD" "$N" "$1" "$LOG_FILE" >&2
  printf 'It is safe to run the installer again after fixing the problem.\n' >&2
}
# Whatever way the installer ends: a box that was being migrated must not be left without a scheduler, because
# bedtime and timers would silently stop. The old units are only stopped (never disabled) until the new scheduler runs,
# so a power cut or a kill that skips this function still brings the old scheduler back at the next start.
# This must not look at the exit status: when the shell is ended by a signal (Ctrl-C, a dropped SSH session, kill) the
# status seen here is 0. NEW_SCHEDULER_UP is set once the new scheduler has proved that it works (install_services), and
# that is the only moment the old units are given up.
# The new scheduler is switched off first when it was already started: two schedulers must never work on the same
# Pi-hole groups (the old one would also wipe the settings of the new one that it does not know).
# SIGPIPE is ignored first. When the whole process group is ended (Ctrl-C, a closed terminal, kill), the copy of the
# output that goes to the log (tee) is ended with the installer, and the first thing bash then writes to its own
# output, a note such as "Terminated" about the program that was running, fails with a broken pipe and would end the
# shell by SIGPIPE in the middle of this function: only the first command of it would run (found by the migration test).
on_exit() {
  trap '' PIPE
  if [[ "$LEGACY_STOPPED" == 1 && "$NEW_SCHEDULER_UP" != 1 ]]; then
    local unit
    systemctl disable --now sinko.service sinko-lists.timer >/dev/null 2>&1 || true
    for unit in pihole-bahrain.service pihole-bahrain-lists.timer; do
      if [[ -f "$UNIT_DIR/$unit" ]]; then systemctl enable --now "$unit" >/dev/null 2>&1 || true; fi
    done
    printf 'The previous version keeps running: its scheduler was started again. Run the installer again after fixing the problem.\n' >&2
  fi
  # A password that was generated and set in Pi-hole is shown at the very end of a good run. When the run fails after
  # that point (gravity, the scheduler) the next run keeps "the existing password": it would be set and nobody would
  # know it. So it is shown (or saved) now.
  if [[ -n "${SHOW_PASSWORD:-}" && "$PASSWORD_SHOWN" != 1 ]]; then show_generated_password || true; fi
  return 0
}

# An old "pihole-bahrain update" starts this installer with PB_NONINTERACTIVE and PB_REF; people also had PB_* in their
# scripts. A SINKO_* variable of the same name always wins. Two are not handled here: the password (taken out of the
# environment at the top of main) and the ref (load_settings must know that it came from the old installer).
import_legacy_env() {
  local k old new
  for k in HOSTNAME UPSTREAMS TIMEZONE LISTS_BASE REPO NONINTERACTIVE INTERFACE PIHOLE_INSTALLER TTY; do
    old="PB_$k"; new="SINKO_$k"
    if [[ -n "${!old+x}" && -z "${!new+x}" ]]; then export "$new=${!old}"; fi
  done
}
TTY_DEV="${SINKO_TTY:-/dev/tty}"      # the terminal; SINKO_TTY is a test hook
can_prompt() { [[ "${SINKO_NONINTERACTIVE:-}" != 1 ]] && { : <"$TTY_DEV"; } 2>/dev/null; }
has_tty() { { : >>"$TTY_DEV"; } 2>/dev/null; }

# ------------------------------------------------------------------ steps
# shellcheck disable=SC1090,SC1091  # os-release is read at runtime
preflight() {
  OS_RELEASE="${SINKO_OS_RELEASE:-/etc/os-release}"       # SINKO_OS_RELEASE is a test hook
  step "Checking this device"
  [[ "$(id -u)" -eq 0 ]] || die "Run as root: curl -fsSL <url> | sudo bash"
  [[ -r "$OS_RELEASE" ]] || die "Unknown operating system (no /etc/os-release)."
  local id like pretty
  id="$(. "$OS_RELEASE" && echo "${ID:-}")"
  like="$(. "$OS_RELEASE" && echo "${ID_LIKE:-}")"
  pretty="$(. "$OS_RELEASE" && echo "${PRETTY_NAME:-$id}")"
  OS_ID="$id"
  if [[ ! " $id $like " =~ [[:space:]](debian|ubuntu)[[:space:]] ]]; then
    die "Unsupported system: $pretty. Use Armbian, Debian, Ubuntu or Raspberry Pi OS."
  fi
  ok "System: $pretty ($(uname -m))"
  case "$(uname -m)" in
    aarch64|arm64|x86_64|armv7l) ;;
    *) warn "Architecture $(uname -m) is untested." ;;
  esac
  command -v systemctl >/dev/null || die "systemd is required."
  # sinko-lists.service runs gravity through flock, so that two gravity runs never write the same database at once.
  [[ -x /usr/bin/flock ]] || die "flock (the util-linux package) is missing: install it with  apt-get install util-linux  and run the installer again."
  # How much room a run needs depends on what it is. A first installation may install Pi-hole and its packages (1 GB, a
  # round number with room to spare). An update, a rollback or a move from pihole-bahrain replaces a few MB of Sinko and
  # needs far less: asking an update for 1 GB would fail it on a card that is merely well used, and the rollback that
  # follows a failed update would fail on the same check. The update engine in bin/sinko uses the same 200 MB.
  local free_kb need_mb free_mb
  free_kb="$(LC_ALL=C df -Pk "${SINKO_ROOT:-/}" | awk 'NR==2 {print $4}')"
  [[ "$free_kb" =~ ^[0-9]+$ ]] || die "Could not read how much disk space is free."
  free_mb=$(( free_kb / 1024 ))
  need_mb="$MIN_FREE_FIRST_MB"
  if existing_installation; then need_mb="$MIN_FREE_UPDATE_MB"; fi
  if (( free_kb <= need_mb * 1024 )); then
    if (( need_mb == MIN_FREE_FIRST_MB )); then
      die_with_status 75 "At least 1 GB of free disk space is needed to install Pi-hole and Sinko, and this device has $free_mb MB. Nothing was changed. Free some space, or use a bigger memory card."
    fi
    die_with_status 75 "At least $need_mb MB of free disk space is needed to update Sinko, and this device has $free_mb MB. Nothing was changed. Free some space (for example: sudo apt-get clean) and try again."
  fi
  ok "Disk space: $free_mb MB free"

  local missing=()
  for pkg in git curl ca-certificates python3 iproute2; do
    pkg_installed "$pkg" || missing+=("$pkg")
  done
  if (( ${#missing[@]} )); then
    step "Installing ${missing[*]}"
    apt_install "${missing[@]}" \
      || die "Could not install ${missing[*]}. Check the internet connection and run the installer again."
  fi
  # The box program is Python 3 (standard library only) and uses features of 3.9: say so at once, in one line, rather
  # than failing with a traceback in the middle of the installation.
  local pyver
  pyver="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || true)"
  if [[ ! "$pyver" =~ ^([0-9]+)\.([0-9]+)$ ]] || (( BASH_REMATCH[1] < 3 || (BASH_REMATCH[1] == 3 && BASH_REMATCH[2] < 9) )); then
    die "Sinko needs Python 3.9 or newer, and this system has ${pyver:-no working Python 3}. Use a newer system image (Debian 11 or newer, Ubuntu 22.04 or newer)."
  fi
  ok "Python $pyver"
  # The internet is checked where it is needed (fetching the release, installing Pi-hole): an offline
  # "sinko rollback" or an install from SINKO_SRC must keep working.
}

# Is there an installation already (so that this run is an update, a rollback or the move from pihole-bahrain)? Looked at
# before anything is changed: the scheduler's unit, or what pihole-bahrain left.
existing_installation() {
  local unit
  if [[ -e "$UNIT_DIR/sinko.service" || -e "$LEGACY_APP" || -e "$LEGACY_CONF_DIR" ]]; then return 0; fi
  for unit in "${LEGACY_UNITS[@]}"; do
    if [[ -e "$UNIT_DIR/$unit" ]]; then return 0; fi
  done
  return 1
}

need_internet() {
  curl -fsS --proto '=https' --proto-redir '=https' --max-time 15 -o /dev/null https://github.com 2>/dev/null \
    || die "No internet connection (cannot reach github.com). Check the cable and the router, then run the installer again."
}

# ------------------------------------------------------------------ settings
# What can be saved in /etc/sinko/config (and be overridden by an environment variable of the same name).
SETTING_KEYS="HOSTNAME LISTS_BASE REPO REPO_SLUG REF IP RELEASE_BASE RELEASE_API TELEMETRY TELEMETRY_URL MDNS OS_UPDATES"
declare -A ENVV=() SAVED=() LEGACY=()   # environment, /etc/sinko/config, /etc/pihole-bahrain/config

# Reads the KEY=value lines of a settings file (shell syntax, as write_settings writes it) into the associative array
# $3, for the keys the file really sets. Runs in a subshell so nothing leaks into the installer, and with the
# environment's own values removed first so they cannot be mistaken for the file's.
read_settings_file() {  # file prefix array-name
  local file="$1" prefix="$2" name="$3" out
  [[ -f "$file" ]] || return 0
  out="$(
    set +eu
    for k in $SETTING_KEYS; do unset "SINKO_$k" "PB_$k"; done
    # shellcheck disable=SC1090
    . "$file" >/dev/null 2>&1
    for k in $SETTING_KEYS; do
      v="$prefix$k"
      if [[ -n "${!v+x}" ]]; then printf '%s[%s]=%q\n' "$name" "$k" "${!v}"; fi
    done
  )"
  eval "$out"
}

# Value the environment gives (empty counts as not set, except for the host name, where empty means "no name").
env_value() {  # KEY
  if [[ -n "${ENVV[$1]+x}" && ( -n "${ENVV[$1]}" || "$1" == HOSTNAME ) ]]; then printf '%s' "${ENVV[$1]}"; return 0; fi
  return 1
}
saved_value() {  # KEY
  if [[ -n "${SAVED[$1]+x}" ]]; then printf '%s' "${SAVED[$1]}"; return 0; fi
  return 1
}
# What an earlier installation chose: this version's settings first, then the pihole-bahrain ones it replaces.
kept_value() {  # KEY
  saved_value "$1" && return 0
  if [[ -n "${LEGACY[$1]+x}" ]]; then printf '%s' "${LEGACY[$1]}"; return 0; fi
  return 1
}
# Did this address belong to the old project (iret33/pihole-bahrain)? A fork of it does not count.
points_at_old_repo() {  # URL
  local u="${1,,}"
  [[ "$u" == *"github.com/iret33/pihole-bahrain"* || "$u" == *"github.com:iret33/pihole-bahrain"* \
     || "$u" == *"raw.githubusercontent.com/iret33/pihole-bahrain/"* ]]
}

# Where releases and the counter are fetched from: https, or plain http only to this machine itself (the test servers
# of the test suite). Plain http across a network would let anybody on it swap the download and its checksum together.
secure_address_ok() {  # URL
  [[ "$1" =~ ^https://[A-Za-z0-9._~:/@+%=-]+$ ]] \
    || [[ "$1" =~ ^http://(127\.0\.0\.1|localhost|\[::1\])(:[0-9]{1,5})?(/[A-Za-z0-9._~:/@+%=-]*)?$ ]]
}

# owner/name from a GitHub URL, or nothing.
slug_of_repo() {
  local u="$1"
  case "$u" in
    https://github.com/*) u="${u#https://github.com/}" ;;
    git@github.com:*) u="${u#git@github.com:}" ;;
    *) return 0 ;;
  esac
  u="${u%/}"; u="${u%.git}"
  [[ "$u" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] && printf '%s' "$u"
  return 0
}

load_settings() {
  # Precedence: environment > saved config > the old pihole-bahrain config (migration) > defaults.
  local k v
  ENVV=(); SAVED=(); LEGACY=()
  for k in $SETTING_KEYS; do
    v="SINKO_$k"
    if [[ -n "${!v+x}" ]]; then ENVV[$k]="${!v}"; fi
  done
  # The ref an old "pihole-bahrain update" passes on (PB_REF), unless SINKO_REF says otherwise. Remembered as such,
  # because for the official project the value master that the old installer always wrote is not a choice (below).
  ENV_REF_IS_LEGACY=0
  if [[ -z "${ENVV[REF]+x}" && -n "${PB_REF+x}" ]]; then ENVV[REF]="$PB_REF"; ENV_REF_IS_LEGACY=1; fi
  read_settings_file "$CONF_FILE" SINKO_ SAVED
  read_settings_file "$LEGACY_CONF_DIR/config" PB_ LEGACY

  if v="$(env_value HOSTNAME)" || v="$(kept_value HOSTNAME)"; then SINKO_HOSTNAME="$v"; else SINKO_HOSTNAME="family.lan"; fi
  [[ "$SINKO_HOSTNAME" == none ]] && SINKO_HOSTNAME=""
  if [[ -n "$SINKO_HOSTNAME" && ! "$SINKO_HOSTNAME" =~ ^[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?)+$ ]]; then
    die "SINKO_HOSTNAME '$SINKO_HOSTNAME' is not a valid name like family.lan"
  fi

  # The project on GitHub. Naming one of "repo" or "slug" in the environment decides the other as well,
  # so a stale saved value never contradicts it.
  local repo="" slug="" env_repo env_slug
  env_repo="$(env_value REPO || true)"; env_slug="$(env_value REPO_SLUG || true)"
  if [[ -n "$env_repo" ]]; then
    repo="$env_repo"; slug="${env_slug:-$(slug_of_repo "$env_repo")}"
  elif [[ -n "$env_slug" ]]; then
    slug="$env_slug"
  else
    repo="$(kept_value REPO || true)"; slug="$(saved_value REPO_SLUG || true)"
    if points_at_old_repo "$repo"; then repo=""; fi          # the new project has the old one's history, not its address
    [[ -n "$slug" || -z "$repo" ]] || slug="$(slug_of_repo "$repo")"
  fi
  SINKO_REPO_SLUG="${slug:-$DEFAULT_SLUG}"
  SINKO_REPO="${repo:-https://github.com/$SINKO_REPO_SLUG.git}"
  [[ "$SINKO_REPO_SLUG" =~ ^[A-Za-z0-9_.-]{1,60}/[A-Za-z0-9_.-]{1,60}$ && "$SINKO_REPO_SLUG" != ./* && "$SINKO_REPO_SLUG" != ../* \
     && "$SINKO_REPO_SLUG" != */. && "$SINKO_REPO_SLUG" != */.. ]] \
    || die "SINKO_REPO_SLUG '$SINKO_REPO_SLUG' is not a GitHub name like iret33/sinko"
  [[ "$SINKO_REPO" =~ ^(https://|file://|git@)[A-Za-z0-9._~:/@+%=-]+$ ]] \
    || die "SINKO_REPO '$SINKO_REPO' is not a git address (https://… or file://…)"

  SINKO_REF="$(env_value REF || kept_value REF || echo "$DEFAULT_REF")"
  [[ -n "$SINKO_REF" ]] || SINKO_REF="$DEFAULT_REF"
  # pihole-bahrain 2.x wrote PB_REF=master for everybody, and its "update" command passes it on every time. For the
  # official project that was never a choice: such a box must follow the releases. A SINKO_REF=master that somebody
  # gave or saved (a developer), a fork, and every other old value (a version, another branch) stay as they are.
  if [[ "$SINKO_REF" == master && "$SINKO_REPO_SLUG" == "$DEFAULT_SLUG" ]]; then
    if env_value REF >/dev/null; then
      if (( ENV_REF_IS_LEGACY )); then SINKO_REF=latest; fi
    elif ! saved_value REF >/dev/null && [[ -n "${LEGACY[REF]+x}" ]]; then
      SINKO_REF=latest
    fi
  fi
  if [[ ! "$SINKO_REF" =~ ^[A-Za-z0-9][A-Za-z0-9._/-]{0,100}$ || "$SINKO_REF" == *..* ]]; then
    die "SINKO_REF '$SINKO_REF' is not a version or branch name (use latest, vX.Y.Z or a branch such as master)"
  fi

  # A saved list address that is just the default of the project saved with it was never a choice: it follows
  # the project if that changes (a fork, a rename). Any other saved or given address stays, and that includes the
  # address of the old project (raw.githubusercontent.com/iret33/pihole-bahrain/...): the lists are registered in
  # Pi-hole under it, GitHub's rename redirect still serves them, and leaving the address alone means no list is deleted
  # or registered again while a box moves to Sinko (a changed address would switch the blocking off until the next
  # gravity run has downloaded every list under its new name).
  local kept_lists kept_slug
  kept_lists="$(kept_value LISTS_BASE || true)"
  kept_slug="$(saved_value REPO_SLUG || true)"
  if [[ "$kept_lists" == "https://raw.githubusercontent.com/${kept_slug:-$DEFAULT_SLUG}/master/lists" ]]; then
    kept_lists=""
  fi
  SINKO_LISTS_BASE="$(env_value LISTS_BASE || echo "$kept_lists")"
  SINKO_LISTS_BASE="${SINKO_LISTS_BASE:-https://raw.githubusercontent.com/$SINKO_REPO_SLUG/master/lists}"
  # A folder inside the old program folder (pihole-bahrain filled it itself, on every update) goes away with that
  # folder at the end of the migration: the lists are read from the new program folder, which has the same files.
  local lists_dir="${SINKO_LISTS_BASE#file://}"
  lists_dir="${lists_dir%/}"
  if [[ "$lists_dir" == "$LEGACY_APP" || "$lists_dir" == "$LEGACY_APP"/* ]]; then
    SINKO_LISTS_BASE="$APP_DIR/lists"
    ok "The lists are read from $APP_DIR/lists now (the old folder $LEGACY_APP is removed at the end)"
  fi

  # Where updates come from. Saved only when it is not the project's own address (see write_settings), so a mirror
  # that was chosen once is not forgotten, and the default keeps following the project.
  SINKO_RELEASE_BASE="$(env_value RELEASE_BASE || saved_value RELEASE_BASE || true)"
  SINKO_RELEASE_BASE="${SINKO_RELEASE_BASE:-https://github.com/$SINKO_REPO_SLUG/releases}"
  SINKO_RELEASE_BASE="${SINKO_RELEASE_BASE%/}"
  secure_address_ok "$SINKO_RELEASE_BASE" \
    || die "SINKO_RELEASE_BASE '$SINKO_RELEASE_BASE' must be an https:// address (plain http is only accepted for this machine itself)"
  SINKO_RELEASE_API="$(env_value RELEASE_API || saved_value RELEASE_API || true)"
  SINKO_RELEASE_API="${SINKO_RELEASE_API:-https://api.github.com/repos/$SINKO_REPO_SLUG/releases/latest}"
  secure_address_ok "$SINKO_RELEASE_API" \
    || die "SINKO_RELEASE_API '$SINKO_RELEASE_API' must be an https:// address (plain http is only accepted for this machine itself)"

  # The anonymous counter: 1 (count this box), 0 (do not) or empty (nobody has been asked yet: the page asks later).
  # An answer given earlier is kept; only a variable or the question below changes it.
  if v="$(env_value TELEMETRY)"; then
    [[ "$v" == 1 || "$v" == 0 ]] || die "SINKO_TELEMETRY must be 1 (count this box) or 0 (do not)."
    SINKO_TELEMETRY="$v"
  else
    v="$(saved_value TELEMETRY || true)"
    if [[ "$v" == 1 || "$v" == 0 ]]; then SINKO_TELEMETRY="$v"; else SINKO_TELEMETRY=""; fi
  fi
  # Two switches that default to on and, once turned off, stay off through updates.
  for k in MDNS OS_UPDATES; do
    v="$(env_value "$k" || saved_value "$k" || true)"
    [[ -n "$v" ]] || v=1
    [[ "$v" == 1 || "$v" == 0 ]] || die "SINKO_$k must be 1 or 0."
    printf -v "SINKO_$k" '%s' "$v"
  done
  # Where the counter lives. Empty = the CLI's built-in address; never invented here, only kept or given.
  SINKO_TELEMETRY_URL="$(env_value TELEMETRY_URL || saved_value TELEMETRY_URL || true)"
  [[ -z "$SINKO_TELEMETRY_URL" ]] || secure_address_ok "$SINKO_TELEMETRY_URL" \
    || die "SINKO_TELEMETRY_URL '$SINKO_TELEMETRY_URL' must be an https:// address (plain http is only accepted for this machine itself)"
}

# Is a counter address set up for this box? Asked of the program itself, which is the one that decides what an address is
# worth: the environment first, then the settings file, then the address built into the release (TELEMETRY_URL near the
# top of bin/sinko: releasing.md puts it there before the tag), and an address that is not https (or http to this machine)
# counts as none. `box-info` prints box.json's content, whose "counter" says exactly this. Nothing is sent and nothing is
# written by it. Any trouble reads as "no": then the question is not asked (nothing is lost, the page asks later if the
# box can count at all).
counter_configured() {
  local info
  info="$(SINKO_CONFIG_FILE="$CONF_FILE" SINKO_TELEMETRY_URL="$SINKO_TELEMETRY_URL" python3 "$SRC/bin/sinko" box-info 2>/dev/null)" || return 1
  python3 -c 'import json, sys; sys.exit(0 if json.load(sys.stdin).get("counter") is True else 1)' <<<"$info" 2>/dev/null
}

# Interactive installs ask once whether this box may be counted, but only when there is a counter to be counted in: a
# build or a fork without a counter address can never send anything, and saying "this box will be counted" would be untrue.
# Without an address nothing is said and nothing is saved. Nobody asked (no terminal, SINKO_NONINTERACTIVE=1) or no answer
# given (Ctrl-D): nothing is saved, and the parent page asks later.
ask_telemetry() {
  [[ -z "$SINKO_TELEMETRY" ]] || return 0
  can_prompt || return 0
  counter_configured || return 0
  local answer
  step "Anonymous counter (optional)"
  printf '  Sinko can add this box to a public count of how many Sinko boxes are online. It sends only a random number,\n'
  printf '  the version and the kind of device, never names, addresses, websites or anything about your family.\n'
  printf '  What is sent, exactly: docs/privacy.md (https://github.com/%s/blob/master/docs/privacy.md)\n' "$SINKO_REPO_SLUG"
  printf '  Count this box in the anonymous number of Sinko boxes online? [y/N] '
  if ! read -r answer <"$TTY_DEV"; then
    printf '\n'
    warn "No answer: the parent page will ask later."
    return 0
  fi
  case "$answer" in
    [Yy]|[Yy][Ee][Ss]) SINKO_TELEMETRY=1; ok "This box will be counted. Change it any time on the parent page (My box)." ;;
    *) SINKO_TELEMETRY=0; ok "This box will not be counted. Change it any time on the parent page (My box)." ;;
  esac
}

# ------------------------------------------------------------------ getting the code
# Prints the folder that holds the code to install. Progress and errors go to stderr, because stdout is the answer.
locate_source() {
  if [[ -n "${SINKO_SRC:-}" ]]; then
    # An already extracted release (an update, a rollback, an image build, a test). Never changed by the installer.
    [[ -d "$SINKO_SRC" ]] || die "SINKO_SRC '$SINKO_SRC' is not a folder."
    tree_complete "$SINKO_SRC" || die "SINKO_SRC '$SINKO_SRC' does not hold a complete Sinko release (bin/sinko, lists, web, systemd, install.sh, VERSION)."
    ( cd "$SINKO_SRC" && pwd )
    return
  fi
  local self="${BASH_SOURCE[0]:-}" dir=""
  if [[ -n "$self" && -f "$self" ]]; then
    dir="$(cd "$(dirname "$self")" && pwd)"
  fi
  if [[ -n "$dir" ]] && tree_complete "$dir"; then
    # The one way a pihole-bahrain 2.x box reaches Sinko is its own `pihole-bahrain update`: it clones the master branch into
    # /opt/pihole-bahrain/src and runs the install.sh it finds there. That checkout is whatever master is at that moment
    # (unreleased commits included), nobody has checked it against a checksum, and installing from it leaves no release copy
    # behind (no /opt/sinko/src, no rollback tarball in the cache). A box that follows the releases (SINKO_REF is latest or
    # a version: the 2.x default master was turned into latest by load_settings) therefore fetches the verified release
    # instead, and the release's own installer (started by main, like in a one-liner) does the work; that also means the
    # running installer no longer lives in the folder that is deleted at the end of the migration. When the release cannot
    # be had (no release published yet, no connection) the checkout is used as before, with a note: the box then follows
    # the releases from its next update on. Only a checkout inside the old program folder is treated like this: a git
    # checkout anywhere else is a developer's or a DIY owner's own copy and is installed as it is.
    if legacy_checkout "$dir" && [[ "$SINKO_REF" == latest || "$SINKO_REF" =~ ^v[0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,4}$ ]]; then
      # In a subshell, so that a failed download only ends the attempt (its die() says what went wrong, as a note). Not
      # inside an `if`: bash switches `set -e` off for everything that runs there, and a half-done unpack must stop it.
      local got=0
      set +e
      ( set -e; trap - ERR; die() { warn "$*"; exit 1; }; fetch_release "$SINKO_REF" ) >&2
      got=$?
      set -e
      if (( got == 0 )); then
        echo "$APP_DIR/src"
        return
      fi
      warn "The verified release could not be had now, so the copy that the old updater downloaded is installed (its version is $(tr -d '[:space:]' <"$dir/VERSION" 2>/dev/null || echo unknown)). 'sudo sinko update' moves to the newest release later." >&2
    fi
    echo "$dir"
    return
  fi
  # Started through curl | bash: fetch the code.
  if [[ "$SINKO_REF" == latest || "$SINKO_REF" =~ ^v[0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,4}$ ]]; then
    fetch_release "$SINKO_REF" >&2
  else
    fetch_branch >&2
  fi
  echo "$APP_DIR/src"
}

# Is this folder a git checkout inside the old program folder (what `pihole-bahrain update` leaves and runs)?
legacy_checkout() {  # DIR
  [[ ( "$1" == "$LEGACY_APP" || "$1" == "$LEGACY_APP"/* ) && -e "$1/.git" ]]    # -e: a worktree's .git is a file
}

tree_complete() {  # DIR
  local d="$1" f
  for f in bin/sinko install.sh VERSION lists/services.json web/index.html systemd/sinko.service; do
    [[ -f "$d/$f" ]] || return 1
  done
}

# curl to a file. Returns 0 on HTTP 200, 1 on any other HTTP answer (the code is in HTTP_CODE), 2 when the server
# could not be reached at all, 3 when the file is bigger than allowed.
http_get() {  # url dest max-bytes
  local rc=0 proto='=https'
  # Only https, and no redirect to anything else (curl follows an https-to-http redirect unless told not to): the
  # download and its checksum come from the same place, so plain http would let anybody on the way swap both. The
  # one exception is a server on this machine itself (the test servers), which secure_address_ok lets through.
  if [[ "$1" == http://* ]]; then proto='=http,https'; fi
  HTTP_CODE="$(curl -sSL --proto "$proto" --proto-redir "$proto" --connect-timeout 15 --max-time 600 --max-filesize "$3" \
    -o "$2" -w '%{http_code}' "$1" 2>/dev/null </dev/null)" || rc=$?
  if (( rc == 63 )); then HTTP_CODE=0; return 3; fi      # curl: larger than --max-filesize
  (( rc == 0 )) || { HTTP_CODE=0; return 2; }
  [[ "$HTTP_CODE" == 200 ]]
}

fetch_release() {  # latest | vX.Y.Z
  local tag="$1" url what
  if [[ "$tag" == latest ]]; then
    url="$SINKO_RELEASE_BASE/latest/download"; what="the newest release"
  else
    url="$SINKO_RELEASE_BASE/download/$tag"; what="release $tag"
  fi
  step "Downloading sinko ($what)"
  mkdir -p "$APP_DIR"
  local tmp
  tmp="$(mktemp -d "$APP_DIR/.download.XXXXXX")"
  # shellcheck disable=SC2064  # $tmp is expanded now on purpose
  trap "rm -rf '$tmp'" EXIT
  local item rc
  for item in sinko.tar.gz.sha256:65536 sinko.tar.gz:20971520; do
    rc=0
    http_get "$url/${item%%:*}" "$tmp/${item%%:*}" "${item##*:}" || rc=$?
    case "$rc" in
      0) ;;
      3) die "The file ${item%%:*} on the server is far bigger than a Sinko release, so it was not downloaded." ;;
      2) die "Could not reach $SINKO_RELEASE_BASE. Check the internet connection (the cable and the router), then run the installer again." ;;
      *) if [[ "$HTTP_CODE" == 404 ]]; then
           die "There is no published release of Sinko to download yet ($url/${item%%:*} answered 404). Developers can install the current development version with SINKO_REF=master."
         fi
         die "The download of ${item%%:*} failed (the server answered HTTP $HTTP_CODE). Try again in a few minutes." ;;
    esac
  done
  verify_checksum "$tmp/sinko.tar.gz" "$tmp/sinko.tar.gz.sha256"
  rm -rf "$APP_DIR/src.tmp"
  local version
  version="$(unpack_release "$tmp/sinko.tar.gz" "$APP_DIR/src.tmp")" || { rm -rf "$APP_DIR/src.tmp"; die "The release could not be unpacked, so nothing was installed."; }
  rm -rf "$APP_DIR/src"
  mv "$APP_DIR/src.tmp" "$APP_DIR/src"
  # The first thing "sinko rollback" needs after the first update is this release, so keep the verified file.
  install -d -m 700 "$STATE_DIR" "$STATE_DIR/cache"
  chmod 700 "$STATE_DIR"
  install -m 600 "$tmp/sinko.tar.gz" "$STATE_DIR/cache/sinko-$version.tar.gz"
  ok "Downloaded and verified sinko $version"
  rm -rf "$tmp"
  trap - EXIT
}

# The checksum file is one line, "<64 hex>  sinko.tar.gz". Anything else is refused, and so is a file that differs.
verify_checksum() {  # tarball checksum-file
  local lines want name got
  lines="$(grep -c . "$2" || true)"
  [[ "$lines" == 1 ]] || die "The checksum published with the release is not in the expected form. Nothing was installed."
  read -r want name <"$2" || true
  name="${name#\*}"
  [[ "$want" =~ ^[0-9A-Fa-f]{64}$ && ( -z "$name" || "$name" == sinko.tar.gz ) ]] \
    || die "The checksum published with the release is not in the expected form. Nothing was installed."
  got="$(sha256sum "$1" | awk '{print $1}')"
  if [[ "${got,,}" != "${want,,}" ]]; then
    die "The download does not match its published checksum, so it was NOT installed (expected ${want,,}, got $got). Run the installer again; if this keeps happening, the release or your connection is damaged."
  fi
}

# Unpacks a release into the empty folder $2 (without the top "sinko/" folder) and prints its version. Plain files and
# folders only, nothing outside sinko/, no absolute or ".." names, no links: the same rules "sinko update" applies.
unpack_release() {  # tarball dest
  python3 - "$1" "$2" <<'PYEOF'
import os, re, sys, tarfile

tar_path, dest = sys.argv[1], sys.argv[2]
MAX_MEMBERS, MAX_BYTES = 5000, 100 * 1024 * 1024
REQUIRED = ("VERSION", "install.sh", "bin/sinko")


def bad(why):
    sys.exit("The download is not a valid Sinko release (%s)." % why)


os.makedirs(dest)
real = os.path.realpath(dest)
files, total = set(), 0
try:
    with tarfile.open(tar_path, "r:gz") as tar:
        for count, member in enumerate(tar, 1):
            if count > MAX_MEMBERS:
                bad("too many files")
            if member.name.startswith("/"):
                bad("a file name starts with /")
            parts = [p for p in member.name.split("/") if p not in ("", ".")]
            if ".." in parts:
                bad("a file name contains ..")
            if not parts:
                continue
            if parts[0] != "sinko":
                bad("a file is outside the sinko folder")
            parts = parts[1:]
            if not parts:
                continue
            target = os.path.join(dest, *parts)
            if os.path.commonpath([real, os.path.realpath(target)]) != real:
                bad("a file would land outside the folder")
            if member.isdir():
                os.makedirs(target, exist_ok=True)
                continue
            if not member.isreg():
                bad("%s is a link or a special file" % parts[-1][:40])
            key = "/".join(parts)
            total += member.size
            if key in files or total > MAX_BYTES:
                bad("a file appears twice or the files are too big")
            files.add(key)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "wb") as out:
                src = tar.extractfile(member)
                while True:
                    block = src.read(1 << 16)
                    if not block:
                        break
                    out.write(block)
                os.fchmod(out.fileno(), 0o755 if member.mode & 0o111 else 0o644)  # never setuid or group-writable
except (tarfile.TarError, OSError, EOFError) as err:
    bad(str(err)[:80])
for name in REQUIRED:
    if name not in files:
        bad("%s is missing" % name)
for root, _dirs, _names in os.walk(dest):
    os.chmod(root, 0o755)
with open(os.path.join(dest, "VERSION"), encoding="ascii") as fh:
    version = fh.read(64).strip()
if not re.fullmatch(r"\d{1,4}\.\d{1,4}\.\d{1,4}", version):
    bad("its VERSION file is not a version number")
print(version)
PYEOF
}

# Developer path: a branch (or tag) of the git repository.
fetch_branch() {
  step "Downloading sinko (development version: $SINKO_REF)"
  [[ "$SINKO_REPO" == file://* ]] || need_internet
  mkdir -p "$APP_DIR"
  local dest="$APP_DIR/src"
  rm -rf "$dest.tmp"
  # https, ssh and a local folder only: git follows redirects, and a repository that has moved from an https address to a
  # plain http one would otherwise be fetched without protection (the rule the downloads above follow too).
  GIT_ALLOW_PROTOCOL=https:ssh:file git clone --quiet --depth 1 --branch "$SINKO_REF" -- "$SINKO_REPO" "$dest.tmp" </dev/null \
    || die "Could not download '$SINKO_REF' from $SINKO_REPO. Check the internet connection and that the name exists."
  rm -rf "$dest"
  mv "$dest.tmp" "$dest"
  ok "Downloaded $(git -C "$dest" rev-parse --short HEAD)"
}

detect_network() {
  step "Checking the network"
  IFACE="${SINKO_INTERFACE:-$(ip -4 route show default 2>/dev/null | awk '{for (i=1;i<NF;i++) if ($i=="dev") {print $(i+1); exit}}')}"
  [[ -n "$IFACE" ]] || die "No network connection with a default route. Plug in the Ethernet cable."
  local line
  line="$(ip -4 -o addr show dev "$IFACE" scope global | head -n1)"
  IPV4="$(awk '{print $4}' <<<"$line")"
  IPV4="${IPV4%/*}"
  [[ -n "$IPV4" ]] || die "Interface $IFACE has no IPv4 address."
  ok "Interface $IFACE, address $IPV4"
  if [[ "$IFACE" == wl* ]]; then
    warn "This device is on Wi-Fi. A wired Ethernet connection is more reliable."
  fi
  if grep -q dynamic <<<"$line"; then
    warn "This address comes from the router (DHCP). Reserve $IPV4 for this device in"
    warn "the router settings (\"DHCP reservation\"), so it never changes."
  fi
}

install_pihole() {
  step "Checking Pi-hole"
  if command -v pihole-FTL >/dev/null; then
    # v6 keeps its settings in pihole.toml and answers --config. (Not with -q:
    # that exits 1 whenever a true/false setting is false.) A v5 install must
    # be upgraded by Pi-hole itself, which migrates its old settings.
    if [[ -f "$TOML" ]] && pihole-FTL --config webserver.paths.webroot >/dev/null 2>&1; then
      ok "Pi-hole v6 is already installed"
      return
    fi
    die "Pi-hole is older than v6. Update it first with: sudo pihole -up"
  fi
  if [[ ! -f "$TOML" ]]; then
    # A config file makes Pi-hole's --unattended mode skip every dialog.
    local up="${SINKO_UPSTREAMS:-1.1.1.3,1.0.0.3}" list="" u
    IFS=',' read -r -a ups <<<"$up"
    for u in "${ups[@]}"; do
      u="${u// /}"
      [[ "$u" =~ ^[0-9A-Fa-f:.#]+$ ]] || die "Invalid SINKO_UPSTREAMS entry: $u"
      list+="${list:+, }\"$u\""
    done
    mkdir -p "$(dirname "$TOML")"
    cat >"$TOML" <<EOF
# Pre-seeded by sinko for an unattended Pi-hole install.
[dns]
  upstreams = [ $list ]
  interface = "$IFACE"
  listeningMode = "LOCAL"
  queryLogging = true

[database]
  maxDBdays = 30

[webserver]
  serve_all = true

[webserver.api]
  cli_pw = true
EOF
    ok "Prepared Pi-hole settings (upstream DNS: $up)"
  fi
  step "Installing Pi-hole (this takes a few minutes)"
  local tmp installer
  tmp="$(mktemp -d)"
  installer="${SINKO_PIHOLE_INSTALLER:-}"
  if [[ -z "$installer" ]]; then
    need_internet
    installer="$tmp/basic-install.sh"
    curl -fsSL --proto '=https' --proto-redir '=https' https://install.pi-hole.net -o "$installer"
  fi
  bash "$installer" --unattended </dev/null
  rm -rf "$tmp"
  command -v pihole-FTL >/dev/null || die "Pi-hole did not install correctly."
  ok "Pi-hole installed"
}

# ------------------------------------------------------------------ migration from pihole-bahrain
# Detected by the old folder, the old settings or the old units. The order is the one in docs/maintainers/architecture.md:
# new files, old scheduler stopped (stopped only: it stays enabled, so a power cut still brings it back), `sinko setup`
# (keeps groups, devices, rules, state and the lists exactly as they are: Pi-hole objects keep their pb- names, and the
# lists keep the address they were registered under), new units, and only after that the old units are disabled and
# deleted; the old folders go at the very end.
detect_legacy() {
  local unit
  if [[ -e "$LEGACY_APP" || -e "$LEGACY_CONF_DIR" ]]; then MIGRATING=1; fi
  for unit in "${LEGACY_UNITS[@]}"; do
    if [[ -e "$UNIT_DIR/$unit" ]]; then MIGRATING=1; fi
  done
  if (( MIGRATING )); then
    step "Found an earlier version (pihole-bahrain): moving it to Sinko"
    ok "Its settings, children's devices, rules and timers are kept"
  fi
  # A box that has run Sinko (its scheduler unit is installed) or pihole-bahrain before is not a new installation: an
  # update must never choose a parent password (see set_password).
  if (( MIGRATING )) || [[ -e "$UNIT_DIR/sinko.service" ]]; then EXISTING_INSTALL=1; fi
}

# The old scheduler stops before `sinko setup`, so two schedulers never work on the same Pi-hole groups. It is only
# stopped, never disabled, until the new scheduler runs (remove_legacy_units disables it): if the installer fails or is
# ended in between, on_exit starts it again, and if even that cannot run (power cut, kill -9) it is still enabled, so
# the next boot brings it back.
stop_legacy_scheduler() {
  (( MIGRATING )) || return 0
  LEGACY_STOPPED=1
  systemctl stop pihole-bahrain.service pihole-bahrain-lists.timer >/dev/null 2>&1 || true
  ok "Stopped the old scheduler (until the new one runs)"
}

# `sinko configure` replaces the name an earlier run put into Pi-hole and learns that name from the settings file, which
# an old box does not have in the new place yet.
seed_config_from_legacy() {
  (( MIGRATING )) || return 0
  [[ ! -f "$CONF_FILE" && -n "${LEGACY[HOSTNAME]+x}" ]] || return 0
  install -d -m 755 "$CONF_DIR"
  printf '# sinko settings (moved from pihole-bahrain)\nSINKO_HOSTNAME=%q\n' "${LEGACY[HOSTNAME]}" >"$CONF_FILE.tmp"
  chmod 644 "$CONF_FILE.tmp"
  mv "$CONF_FILE.tmp" "$CONF_FILE"
}

# Only called once the new scheduler is enabled and running (install_services).
remove_legacy_units() {
  (( MIGRATING )) || return 0
  local unit
  systemctl disable --now pihole-bahrain.service pihole-bahrain-lists.timer >/dev/null 2>&1 || true
  for unit in "${LEGACY_UNITS[@]}"; do rm -f "$UNIT_DIR/$unit"; done
  systemctl daemon-reload
}

# Last step, after everything else worked: the old command keeps working as a shortcut, then the old folders go.
# (An old "pihole-bahrain update" runs this installer from inside /opt/pihole-bahrain/src, so nothing may read from
# the source folder after this point.)
finish_legacy_migration() {
  (( MIGRATING )) || return 0
  if [[ -L "$LEGACY_BIN" || ! -e "$LEGACY_BIN" ]]; then
    ln -sfn "$APP_DIR/bin/sinko" "$LEGACY_BIN"
    ok "The command is now called sinko; 'pihole-bahrain' still works as a shortcut for now."
  fi
  # A box installed without a terminal got its generated parent password saved in the old settings folder, to be read
  # once. That folder is about to go: it is the only record of the password, so it moves (root only, as before).
  local old_pw="$LEGACY_CONF_DIR/initial-password" new_pw="$CONF_DIR/initial-password"
  if [[ -f "$old_pw" && ! -e "$new_pw" ]]; then
    install -d -m 755 "$CONF_DIR"
    ( umask 077; cp -- "$old_pw" "$new_pw.tmp" )
    chmod 600 "$new_pw.tmp"
    mv -f "$new_pw.tmp" "$new_pw"
    ok "The parent password saved by the old version is now in $new_pw (readable by root only). Read it, then delete the file."
  fi
  rm -rf "$LEGACY_APP" "$LEGACY_CONF_DIR" || warn "Could not remove the old folders $LEGACY_APP and $LEGACY_CONF_DIR; they are not used any more."
  rm -f "$LEGACY_LOG"
  ok "Removed the old version's files"
}

# Every file the installer puts in place is written under a temporary name next to its place and then renamed over it
# (GNU install alone deletes the old file first and then writes the new one in place, with nothing forcing the data to
# disk: a power cut a few seconds later can leave a zero-length program, page or unit file, and on a ready-made box
# nobody can log in to repair it). A rename is atomic, and ext4 writes the data of a renamed file out first, so after
# any interruption each file is either the old one or the new one. systemd ignores the *.sinko-new names.
put() {  # mode source destination
  install -m "$1" "$2" "$3.sinko-new"
  mv -f "$3.sinko-new" "$3"
}

# The start page, with the release's version put in where the template says @VERSION@ (the web folder's own files are not
# touched: only this one has the marker). index.html names its scripts and its stylesheet with that version
# (/pb/app.js?v=@VERSION@, ...): Pi-hole's web server lets a browser keep a static file for an hour, and a phone that was
# showing the page while the box updated would otherwise get the new page (which a reload always revalidates) with the
# OLD scripts and styles it still holds, and say "updated". A new release changes every one of those addresses, so every
# file is fetched again; the web server ignores the part after the ?. Written under a temporary name and renamed over the
# old page, like every other file the installer puts in place.
stamp_page() {  # template destination (VERSION was checked at the start of the run: it goes into sed's replacement text)
  sed "s/@VERSION@/$VERSION/g" "$1" >"$2.sinko-new"
  chmod 644 "$2.sinko-new"
  mv -f "$2.sinko-new" "$2"
}

# What an interrupted run can leave behind: temporary files, and a page folder swap that stopped half way.
clean_interrupted_leftovers() {
  find "$APP_DIR" -name '*.sinko-new' -delete 2>/dev/null || true
  find "$UNIT_DIR" "$(dirname "$BIN_LINK")" -maxdepth 1 -name '*.sinko-new' -delete 2>/dev/null || true
  # A download or an unpacking that was cut short (up to 20 MB each). Nothing else runs now: the lock is held, and the
  # download of this very run is finished and was removed.
  rm -rf "$APP_DIR/src.tmp" "$APP_DIR"/.download.*
}

install_files() {
  step "Installing the parent page"
  install -d -m 755 "$APP_DIR" "$APP_DIR/bin" "$APP_DIR/lists" "$APP_DIR/tools" "$CONF_DIR" "$(dirname "$BIN_LINK")"
  clean_interrupted_leftovers
  put 755 "$SRC/bin/sinko" "$APP_DIR/bin/sinko"
  # The lists: the new files go in first, and only the ones this release no longer has are deleted (the program reads
  # services.json and every list at start: they must never be missing).
  local f name
  for f in "$SRC"/lists/*.txt "$SRC/lists/services.json"; do put 644 "$f" "$APP_DIR/lists/$(basename "$f")"; done
  for f in "$APP_DIR"/lists/*.txt; do
    [[ -e "$f" && ! -f "$SRC/lists/$(basename "$f")" ]] && rm -f "$f"
  done
  put 644 "$SRC/VERSION" "$APP_DIR/VERSION"
  put 755 "$SRC/uninstall.sh" "$APP_DIR/uninstall.sh"
  # The licence texts stay with the program (docs/selling.md tells sellers where they are), whatever happens to the source copy.
  for name in LICENSE NOTICE; do
    if [[ -f "$SRC/$name" ]]; then put 644 "$SRC/$name" "$APP_DIR/$name"; fi
  done
  if [[ -f "$SRC/lists/LICENSE" ]]; then put 644 "$SRC/lists/LICENSE" "$APP_DIR/lists/LICENSE"; fi
  # The ready-made image tools. A git checkout holds other scripts too (release build, …): only these two belong on a box.
  for name in seal.sh firstboot.sh; do
    if [[ -f "$SRC/tools/$name" ]]; then put 755 "$SRC/tools/$name" "$APP_DIR/tools/$name"; fi
  done
  for f in "$APP_DIR"/tools/*.sh; do
    [[ -e "$f" ]] || continue
    name="$(basename "$f")"
    if [[ ( "$name" != seal.sh && "$name" != firstboot.sh ) || ! -f "$SRC/tools/$name" ]]; then rm -f "$f"; fi
  done
  ln -sfn "$APP_DIR/bin/sinko" "$BIN_LINK.sinko-new"
  mv -T -f "$BIN_LINK.sinko-new" "$BIN_LINK"
  # Runtime data (update results, the counter's id, rollback copies): root only, never reachable from the page.
  install -d -m 700 "$STATE_DIR" "$STATE_DIR/cache"
  chmod 700 "$STATE_DIR"

  WEBROOT="$(pihole-FTL --config -q webserver.paths.webroot 2>/dev/null || true)"
  WEBROOT="${WEBROOT:-$R/var/www/html}"
  install -d -m 755 "$WEBROOT"
  find "$WEBROOT" -maxdepth 1 -name '*.sinko-new' -delete 2>/dev/null || true
  # A page folder swap (below) that was interrupted between its two renames: put the page that worked back.
  if [[ -d "$WEBROOT/pb.old" ]]; then
    if [[ -d "$WEBROOT/pb" ]]; then rm -rf "$WEBROOT/pb.old"; else mv "$WEBROOT/pb.old" "$WEBROOT/pb"; fi
  fi
  # A start page that is not ours is kept as a backup. The ones the 1.x installer and pihole-bahrain 2.x made are ours,
  # and the new page replaces them below by rename: the page is never missing in between.
  if [[ -f "$WEBROOT/index.html" ]] && ! grep -q 'name="generator" content="sinko"' "$WEBROOT/index.html" \
     && ! grep -q 'parental/app.js' "$WEBROOT/index.html" \
     && ! grep -q 'name="generator" content="pihole-bahrain"' "$WEBROOT/index.html" \
     && [[ ! -f "$WEBROOT/index.html.pb-backup" ]]; then
    mv "$WEBROOT/index.html" "$WEBROOT/index.html.pb-backup"
    ok "Kept the previous start page as index.html.pb-backup"
  fi
  rm -rf "$WEBROOT/parental" "$WEBROOT/pb.new"
  install -d -m 755 "$WEBROOT/pb.new" "$WEBROOT/pb.new/fonts"
  for f in "$SRC"/web/*; do
    [[ -f "$f" && "$(basename "$f")" != index.html ]] && install -m 644 "$f" "$WEBROOT/pb.new/"
  done
  install -m 644 "$SRC"/web/fonts/* "$WEBROOT/pb.new/fonts/"
  install -m 644 "$SRC/lists/services.json" "$WEBROOT/pb.new/services.json"
  "$BIN_LINK" domain-map >"$WEBROOT/pb.new/domains.json"   # domain -> app, so the page can name what a device opens
  chmod 644 "$WEBROOT/pb.new/domains.json"
  install -m 644 "$SRC/VERSION" "$WEBROOT/pb.new/version.txt"
  # The scheduler's box.json (written by the box program, refreshed every few minutes) goes along, so the page does not
  # miss it in the seconds until it is written again below.
  if [[ -f "$WEBROOT/pb/box.json" ]]; then cp -p "$WEBROOT/pb/box.json" "$WEBROOT/pb.new/box.json" || true; fi
  # The swap: everything of the new page is on disk before the folders change names (sync), and there are two renames,
  # so a crash leaves the old page, the new page, or (between the renames) pb.old, which the next run puts back.
  sync
  if [[ -d "$WEBROOT/pb" ]]; then mv "$WEBROOT/pb" "$WEBROOT/pb.old"; fi
  mv "$WEBROOT/pb.new" "$WEBROOT/pb"
  rm -rf "$WEBROOT/pb.old"
  stamp_page "$SRC/web/index.html" "$WEBROOT/index.html"
  # The page reads /pb/box.json (address, version, heartbeat) at once: write it now, not at the scheduler's next round.
  write_box_info "the page is in place"
  sync
  ok "Page installed in $WEBROOT"
}

# Writes /pb/box.json (what the page may know about the box). Called twice: once the page is in place, so that the file
# exists from the first minute, and again at the end, when the settings and the local name that it reports (the counter,
# .local) are final. An older program has no such command, and a failure is not an error: the scheduler writes the file
# every few minutes anyway.
write_box_info() {  # why (for the message)
  "$BIN_LINK" box-info --write >/dev/null 2>&1 \
    || warn "The box information file was not written now ($1). The scheduler writes it a few minutes after it starts."
  return 0
}

write_settings() {
  local tmp="$CONF_FILE.tmp"
  {
    echo "# sinko settings. Re-run the installer after editing."
    printf 'SINKO_HOSTNAME=%q\n' "$SINKO_HOSTNAME"
    printf 'SINKO_LISTS_BASE=%q\n' "$SINKO_LISTS_BASE"
    printf 'SINKO_REPO=%q\n' "$SINKO_REPO"
    printf 'SINKO_REPO_SLUG=%q\n' "$SINKO_REPO_SLUG"
    printf 'SINKO_REF=%q\n' "$SINKO_REF"
    printf 'SINKO_IP=%q\n' "$IPV4"
    # An update source that is not the project's own (a mirror): the box program reads these too, so every later update
    # and update check goes there and not back to GitHub. The project's own addresses are not saved: they follow it.
    if [[ "$SINKO_RELEASE_BASE" != "https://github.com/$SINKO_REPO_SLUG/releases" ]]; then
      printf 'SINKO_RELEASE_BASE=%q\n' "$SINKO_RELEASE_BASE"
    fi
    if [[ "$SINKO_RELEASE_API" != "https://api.github.com/repos/$SINKO_REPO_SLUG/releases/latest" ]]; then
      printf 'SINKO_RELEASE_API=%q\n' "$SINKO_RELEASE_API"
    fi
    # Only what somebody answered or set: no answer is not "no".
    if [[ -n "$SINKO_TELEMETRY" ]]; then printf 'SINKO_TELEMETRY=%q\n' "$SINKO_TELEMETRY"; fi
    if [[ -n "$SINKO_TELEMETRY_URL" ]]; then printf 'SINKO_TELEMETRY_URL=%q\n' "$SINKO_TELEMETRY_URL"; fi
    printf 'SINKO_MDNS=%q\n' "$SINKO_MDNS"
    printf 'SINKO_OS_UPDATES=%q\n' "$SINKO_OS_UPDATES"
  } >"$tmp"
  chmod 644 "$tmp"
  mv "$tmp" "$CONF_FILE"
}

set_timezone() {
  command -v timedatectl >/dev/null || return 0
  local current want="${SINKO_TIMEZONE:-}"
  current="$(timedatectl show -p Timezone --value 2>/dev/null || true)"
  if [[ -z "$want" ]]; then
    case "$current" in ""|UTC|Etc/UTC|Universal|Etc/Universal|GMT|Etc/GMT) want="Asia/Bahrain" ;; *) return 0 ;; esac
  fi
  [[ "$current" == "$want" ]] && return 0
  if timedatectl set-timezone "$want" 2>/dev/null; then
    ok "Time zone set to $want (bedtime uses this). Change with: sudo timedatectl set-timezone <zone>"
  else
    warn "Could not set time zone $want"
  fi
}

configure_pihole() {
  step "Applying Pi-hole settings"
  "$BIN_LINK" configure --ip "$IPV4" --hostname "$SINKO_HOSTNAME"   # "" removes the name
  ok "Parent page enabled at http://$IPV4/${SINKO_HOSTNAME:+ and http://$SINKO_HOSTNAME/}"
}

gen_password() {
  python3 -c 'import secrets
a = "ABCDEFGHJKMNPQRSTUVWXYZabcdefghjkmnpqrstuvwxyz23456789"
print("-".join("".join(secrets.choice(a) for _ in range(4)) for _ in range(3)))'
}

# Gives Pi-hole the password through its API (PATCH /api/config on this machine): the password is read from standard
# input and travels in the request body, never in a program's arguments. This works only for a Pi-hole that has NO password
# yet: then nobody has to sign in (the parent page's welcome screen sets a password the same way). Once a password exists,
# Pi-hole's FTL refuses every configuration change from a command-line session, which is the only kind of session this
# installer could have (it knows /etc/pihole/cli_pw, not the parent's password): "The current CLI session is not allowed to
# modify Pi-hole config settings" (HTTP 403, src/api/config.c in FTL's source). So there is no sign-in here at all. The
# other way, `pihole-FTL --config webserver.api.password <pw>` and `pihole setpassword <pw>` (which ends in that same
# command), takes the password as an argument, and every local user can read a running program's arguments in
# /proc/<pid>/cmdline: that is the fallback, and the only way for a Pi-hole that already has a password.
# Prints nothing and exits 1 when it did not work (apply_password then falls back to `pihole setpassword`).
IFS= read -r -d '' SET_PASSWORD_PY <<'PYEOF' || true
import json, os, ssl, sys, urllib.error, urllib.request

ports, password = sys.argv[1], sys.stdin.readline().rstrip("\n")
base = os.environ.get("SINKO_API_URL", "").rstrip("/")
if not base:                      # the same choice the sinko program makes: an http port first, then an https one
    http = https = ""
    for tok in ports.split(","):
        tok = tok.strip()
        if tok.startswith("["):   # an IPv6 binding
            continue
        tok = tok.rsplit(":", 1)[-1]
        digits = "".join(c for c in tok if c.isdigit())
        flags = tok[len(digits):]
        if not digits or "r" in flags:
            continue
        if "s" in flags:
            https = https or digits
        else:
            http = http or digits
    base = "http://127.0.0.1:" + http if http else ("https://127.0.0.1:" + https if https else "http://127.0.0.1:80")
ctx = ssl.create_default_context()    # a self-signed certificate of this machine, reached on 127.0.0.1 only
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE


def call(method, path, body=None):
    req = urllib.request.Request(base + path, method=method, data=None if body is None else json.dumps(body).encode())
    req.add_header("Accept", "application/json")
    if body is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=20, context=ctx) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as err:
        return err.code, {}


try:
    status, answer = call("GET", "/api/auth")
    session = answer.get("session", {}) if isinstance(answer, dict) else {}
    if not (status == 200 and session.get("valid")):      # a password is set: this route is closed to us (see above)
        sys.exit(1)
    status, _ = call("PATCH", "/api/config", {"config": {"webserver": {"api": {"password": password}}}})
    sys.exit(0 if status == 200 else 1)
except (OSError, ValueError):
    sys.exit(1)
PYEOF
set_password_via_api() {  # ports (the webserver.port setting); the password on standard input
  python3 -c "$SET_PASSWORD_PY" "$1"
}

# Sets Pi-hole's password and checks that it took.
apply_password() {  # password
  local pw="$1" ports
  ports="$(pihole-FTL --config webserver.port 2>/dev/null | head -n1 || true)"
  if set_password_via_api "${ports:-80}" <<<"$pw" && "$BIN_LINK" password-state >/dev/null 2>&1; then
    return 0
  fi
  # Pi-hole's own command: it works wherever the API route did not, which is every Pi-hole that already has a password. The
  # password is then briefly in that program's arguments (the header of this file says so).
  echo "  (Pi-hole's API did not take the password (it only does while Pi-hole has none): using 'pihole setpassword')"
  pihole setpassword "$pw" >/dev/null
}

set_password() {
  step "Parent password"
  local state=0
  "$BIN_LINK" password-state || state=$?
  SHOW_PASSWORD=""
  local pw="$PASSWORD_GIVEN"
  PASSWORD_GIVEN=""
  if [[ -z "$pw" && $state -eq 0 ]]; then
    ok "Keeping the existing password"
    return
  fi
  if [[ -z "$pw" ]] && can_prompt; then
    local again
    while true; do
      read -r -s -p "  Choose a password for the parent page (8+ characters): " pw <"$TTY_DEV"; echo
      if (( ${#pw} < 8 )); then echo "  Too short, try again."; continue; fi
      read -r -s -p "  Type it again: " again <"$TTY_DEV"; echo
      [[ "$pw" == "$again" ]] && break
      echo "  The two passwords are different, try again."
    done
  elif [[ -z "$pw" ]] && (( EXISTING_INSTALL )); then
    # An update that nobody is watching (the one the page starts, an installer run from a script) finds no password on a
    # box that is waiting for its parent: a ready-made box that has not been claimed yet shows its welcome screen. A
    # password chosen here would lock the parent out of their own box, and nobody could even read it (such a box has no
    # login). So an update leaves "no password" alone; the page asks for one when it is opened.
    warn "This box has no parent password yet. The parent page asks for one the first time it is opened (an update does not choose one)."
    return
  elif [[ -z "$pw" ]]; then
    pw="$(gen_password)"
    SHOW_PASSWORD="$pw"
  fi
  (( ${#pw} >= 8 )) || die "SINKO_PASSWORD must be at least 8 characters."
  [[ "$pw" != *[[:cntrl:]]* ]] || die "SINKO_PASSWORD must not contain line breaks or other control characters."
  apply_password "$pw"
  pw=""
  ok "Password set"
}

install_services() {
  step "Starting background services"
  local pihole_bin
  pihole_bin="$(command -v pihole || echo /usr/local/bin/pihole)"
  install -d -m 755 "$UNIT_DIR"
  put 644 "$SRC/systemd/sinko.service" "$UNIT_DIR/sinko.service"
  put 644 "$SRC/systemd/sinko-lists.timer" "$UNIT_DIR/sinko-lists.timer"
  sed "s|@PIHOLE@|$pihole_bin|g" "$SRC/systemd/sinko-lists.service" >"$UNIT_DIR/sinko-lists.service.sinko-new"
  chmod 644 "$UNIT_DIR/sinko-lists.service.sinko-new"
  mv -f "$UNIT_DIR/sinko-lists.service.sinko-new" "$UNIT_DIR/sinko-lists.service"
  # Installed on every box but enabled only by tools/seal.sh, on the ready-made image (it is also guarded by a flag file).
  if [[ -f "$SRC/systemd/sinko-firstboot.service" ]]; then
    put 644 "$SRC/systemd/sinko-firstboot.service" "$UNIT_DIR/sinko-firstboot.service"
  fi
  sync                          # the unit files are on disk before systemd reads them (an empty unit file is a masked unit)
  systemctl daemon-reload
  # Started now, switched on for the next start only once it has proved that it works (below): until then a power cut
  # brings back the scheduler the box had before (the old one on a box that is being migrated), never one that may
  # crash again and again.
  systemctl restart sinko.service
  prove_scheduler
  systemctl enable --quiet sinko.service sinko-lists.timer
  systemctl restart sinko-lists.timer
  NEW_SCHEDULER_UP=1
  remove_legacy_units
  ok "Scheduler running; lists refresh every night"
}

# The scheduler has just been started. Bedtime, timers and updates depend on it, so the installation is not finished
# until the program's own self-check says that it works: the scheduler has stayed up (systemd has not had to restart it
# again and again) and has finished its passes over Pi-hole, and Pi-hole, the groups, the lists and the page are in order.
# Right after a start the scheduler reads as "starting" for about 20 seconds and needs a second pass after that, so the
# self-check is repeated (it does that itself, with --wait) for up to SINKO_SCHEDULER_WAIT seconds; a scheduler that
# crashes over and over never gets there. That is the same self-check, with the same patience, that "sinko update" applies
# before it keeps an update. SINKO_SCHEDULER_WAIT is a test hook.
prove_scheduler() {
  local wait="${SINKO_SCHEDULER_WAIT:-90}"
  [[ "$wait" =~ ^[0-9]{1,4}$ ]] || wait=90
  step "Checking that the scheduler works (up to $wait seconds)"
  "$BIN_LINK" selfcheck --wait "$wait" && return 0
  # A box that is being migrated keeps its old version running: on_exit switches the new scheduler off again and starts
  # the old one (nothing of the old version has been removed yet).
  local kept=""
  if (( MIGRATING )); then kept="The previous version keeps running. "; fi
  die "The new scheduler could not be proven to work: a check failed (the line marked FAIL above says which). ${kept}Nothing was removed, and the installer can be run again once the problem is fixed. Details: sudo journalctl -u sinko -n 50 --no-pager"
}

# Packages. `apt-get update` fails when any one of the package sources cannot be reached (Armbian adds its own next to
# Debian's), even though the lists of the others were refreshed and the package is there: so a failed update is a
# warning, and the installation goes on when apt has a version to install.
#
# Everything of apt and dpkg whose output is read here is asked for in the C locale (LC_ALL=C): apt prints its labels in the
# language of the user ("Candidate:" is "Installationskandidat:" in German, "Candidat :" in French, "Candidato:" in Spanish and
# another word in Arabic), and a box whose owner chose another locale would otherwise look as if apt had nothing to install.
# Only the one command is switched, not the installer: Pi-hole's installer and apt's messages keep the user's language.
APT_UPDATED=0
apt_has_candidate() {  # package: is there a version apt can install?
  local version
  version="$(LC_ALL=C apt-cache policy "$1" 2>/dev/null | awk '/^ *Candidate:/ {print $2; exit}')"
  [[ -n "$version" && "$version" != "(none)" ]]
}
# Is the package really installed? `dpkg -s` answers "yes" (exit status 0) for a package that was removed but not purged
# ("deinstall ok config-files", which is what `apt remove avahi-daemon unattended-upgrades` leaves), and an installer that
# believes it would leave the box without the local name and without security updates for good.
# shellcheck disable=SC2016  # ${Status} is dpkg's own field name, not a variable of ours
pkg_installed() {  # package
  [[ "$(LC_ALL=C dpkg-query -W -f='${Status}' "$1" 2>/dev/null)" == "install ok installed" ]]
}
apt_install() {  # package... : 0 when installed
  if (( ! APT_UPDATED )); then
    APT_UPDATED=1
    DEBIAN_FRONTEND=noninteractive apt-get update -qq </dev/null \
      || warn "apt could not refresh all its package lists (a package source may be unreachable). Going on with the lists it has."
  fi
  local p
  for p in "$@"; do apt_has_candidate "$p" || return 1; done
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends "$@" </dev/null >/dev/null
}

# Optional extras. Neither is allowed to fail the installation: the children's rules do not depend on them.

# avahi-daemon makes the box answer to <hostname>.local, so the page opens by a name that never changes even when the
# router hands out another address. An avahi that is already installed is left exactly as it is.
install_local_name() {
  step "Local name"
  if [[ "$SINKO_MDNS" != 1 ]]; then
    ok "Skipped (SINKO_MDNS=0): the page is reachable by its address only"
    return 0
  fi
  if pkg_installed avahi-daemon; then
    ok "avahi-daemon is already installed (left as it is)"
    return 0
  fi
  if apt_install avahi-daemon; then
    systemctl enable --now avahi-daemon.service >/dev/null 2>&1 || warn "avahi-daemon is installed but could not be started."
    ok "Installed avahi-daemon"
  else
    warn "Could not install avahi-daemon (no internet?). The page is still reachable by its address; run the installer again later."
  fi
}

# Debian's security updates, nothing else, and never an automatic restart: a restart at a random hour would cut the
# children's internet (and the box has no clock battery to bring it back on schedule). An unattended-upgrades that is
# already installed keeps its own settings.
install_os_updates() {
  step "Security updates"
  if [[ "$SINKO_OS_UPDATES" != 1 ]]; then
    ok "Skipped (SINKO_OS_UPDATES=0)"
    return 0
  fi
  if pkg_installed unattended-upgrades; then
    ok "Automatic updates are already set up (left as they are)"
    return 0
  fi
  if ! apt_install unattended-upgrades; then
    warn "Could not install unattended-upgrades (no internet?). Run the installer again later, or update the system by hand."
    return 0
  fi
  local origins file="$R/etc/apt/apt.conf.d/52sinko-unattended-upgrades"
  # ${distro_codename} is apt's own macro, not ours: single quotes on purpose.
  if [[ "$OS_ID" == ubuntu ]]; then
    # shellcheck disable=SC2016
    origins='        "origin=Ubuntu,codename=${distro_codename}-security,label=Ubuntu";'
  else
    # shellcheck disable=SC2016
    origins='        "origin=Debian,codename=${distro_codename}-security,label=Debian-Security";
        "origin=Debian,codename=${distro_codename},label=Debian-Security";'
  fi
  install -d -m 755 "$(dirname "$file")"
  {
    echo '// Written by the Sinko installer. Security updates only, and never an automatic restart.'
    echo 'APT::Periodic::Update-Package-Lists "1";'
    echo 'APT::Periodic::Unattended-Upgrade "1";'
    echo '// The package also allows ordinary updates; this replaces its list (it is read after 50unattended-upgrades).'
    echo '#clear Unattended-Upgrade::Origins-Pattern;'
    echo 'Unattended-Upgrade::Origins-Pattern {'
    echo "$origins"
    echo '};'
    echo 'Unattended-Upgrade::Automatic-Reboot "false";'
  } >"$file.tmp"
  chmod 644 "$file.tmp"
  mv "$file.tmp" "$file"
  systemctl enable --now apt-daily.timer apt-daily-upgrade.timer >/dev/null 2>&1 || true
  ok "Automatic security updates are on (no automatic restart)"
}

# The box has no battery-backed clock: after every power cut its time starts at the last moment it saved, and bedtime, the
# timers, the check for updates and every https connection depend on the time being right. A time service sets it from the
# network. One that is there (and switched on) is left alone, whatever it is. A box with none gets Debian's
# systemd-timesyncd, unless another time service is installed but switched off: that is the owner's to switch on (installing
# systemd-timesyncd next to it would remove it).
TIME_UNITS="systemd-timesyncd.service chrony.service chronyd.service ntpsec.service ntp.service openntpd.service"
TIME_PACKAGES="chrony ntpsec ntp openntpd"
install_time_sync() {
  step "Clock"
  local unit pkg
  for unit in $TIME_UNITS; do
    if systemctl is-enabled --quiet "$unit" 2>/dev/null; then
      ok "The clock is kept right by ${unit%.service} (left as it is)"
      return 0
    fi
  done
  for pkg in $TIME_PACKAGES; do
    if pkg_installed "$pkg"; then
      warn "$pkg is installed but not switched on, so this box's clock can be wrong after a power cut. Switch it on: sudo systemctl enable --now $pkg"
      return 0
    fi
  done
  if ! pkg_installed systemd-timesyncd && ! apt_install systemd-timesyncd; then
    warn "Could not install systemd-timesyncd (no internet?). Without a time service the clock can be wrong after a power cut; run the installer again later."
    return 0
  fi
  if systemctl enable --now systemd-timesyncd.service >/dev/null 2>&1; then
    ok "Installed systemd-timesyncd: the clock is set from the network"
  else
    warn "systemd-timesyncd is installed but could not be started (it does not run in some virtual machines and containers)."
  fi
}

# The name avahi publishes: the host name of the system, without a domain.
system_hostname() {
  local name=""
  if [[ -r "$R/etc/hostname" ]]; then name="$(head -n1 "$R/etc/hostname" 2>/dev/null || true)"; else name="$(hostname 2>/dev/null || true)"; fi
  name="${name%%.*}"
  [[ "$name" =~ ^[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?$ ]] && printf '%s' "$name"
  return 0
}

# A password we generated is never written to stdout, because stdout is copied into the install log.
# It goes to the terminal only; without a terminal it is saved in a root-only file instead.
show_generated_password() {
  [[ -n "${SHOW_PASSWORD:-}" ]] || return 0
  PASSWORD_SHOWN=1
  if has_tty; then
    sleep 0.3   # let the copy of stdout (tee) finish printing, so this lands last on the screen
    printf '\n  %sParent password: %s%s\n  Shown on this screen only. Change it any time with: sudo pihole setpassword\n\n' \
      "$B" "$SHOW_PASSWORD" "$N" >>"$TTY_DEV"
  else
    local file="$CONF_DIR/initial-password"
    ( umask 077; printf '%s\n' "$SHOW_PASSWORD" >"$file" )
    chmod 600 "$file"
    printf '\n  Parent password: saved in %s (readable by root only).\n  Read it, then delete the file. Change it any time with: sudo pihole setpassword\n\n' "$file"
  fi
}

summary() {
  local url="http://$IPV4/" local_name="" host
  host="$(system_hostname)"
  if [[ -n "$host" ]] && systemctl is-active --quiet avahi-daemon.service 2>/dev/null; then
    local_name="http://$host.local/"
  fi
  cat <<EOF

${G}${B}Sinko is ready.${N}

  Parent page:   ${B}$url${N}${local_name:+
                 or ${B}$local_name${N} (works on phones and computers at home as it is)}${SINKO_HOSTNAME:+
                 or http://$SINKO_HOSTNAME/ (after the router step below)}
EOF
  if [[ -n "${SHOW_PASSWORD:-}" ]]; then
    printf '  Password:      generated for you, shown below\n'
  fi
  cat <<EOF
  Pi-hole admin: http://$IPV4/admin/

  ${B}Last step — make your home use this box:${N}
  1. Open your router's settings (usually http://$(ip -4 route show default | awk '{print $3; exit}')/).
  2. Find "DNS server" in the LAN or DHCP settings and set it to $IPV4 only.
  3. Reserve $IPV4 for this device ("DHCP reservation" / "static lease").
  4. Restart Wi-Fi on the children's devices, then add them on the parent page.

  Check the installation any time: sudo sinko doctor
  Update:                          sudo sinko update
  Remove:                          sudo /opt/sinko/uninstall.sh
EOF
}

main "$@"
