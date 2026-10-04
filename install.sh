#!/usr/bin/env bash
# Sinko — installer and updater.
#
#   curl -fsSL https://github.com/iret33/sinko/releases/latest/download/install.sh | sudo bash
#
# Installs Pi-hole v6 (if missing, fully unattended), the parent page, the
# scheduler service and the service block lists. Safe to run again: it then
# updates everything and keeps your password, devices and rules.
#
# By default it downloads the newest release (sinko.tar.gz) from GitHub, checks it
# against the published sha256 and refuses to install it if the two differ.
#
# Optional settings (environment variables, e.g. `curl … | sudo SINKO_PASSWORD=… bash`).
# What you set once is saved in /etc/sinko/config and kept by later runs and updates.
#   SINKO_PASSWORD        parent password. Otherwise you are asked (or one is generated).
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
#   SINKO_RELEASE_BASE    where releases are downloaded from, default https://github.com/<slug>/releases
#   SINKO_NONINTERACTIVE  1 = never prompt
set -Eeuo pipefail
shopt -s inherit_errexit   # also stop on failures inside $(…), e.g. a failed download

main() {
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
  echo
  echo "=== sinko installer — $(date -u '+%Y-%m-%d %H:%M:%S UTC') ==="

  if [[ "${SINKO_REEXEC:-}" != 1 ]]; then
    # One installer at a time (a parent running it by hand while "Update now" is working would corrupt both).
    # The re-executed installer inherits this descriptor, and with it the lock.
    mkdir -p "$R/var/lock"
    exec 9>"$R/var/lock/sinko-install.lock"
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
    SINKO_REEXEC=1 SINKO_SRC="$src" exec bash "$src/install.sh"
  fi
  SRC="$src"
  VERSION="$(cat "$SRC/VERSION" 2>/dev/null || echo unknown)"
  ok "Installing version $VERSION from $SRC"

  detect_network
  install_pihole
  install_files
  set_timezone
  configure_pihole          # before write_settings: it reads the previous hostname
  write_settings
  set_password
  step "Setting up groups, rules and block lists (downloads lists, can take a minute)"
  "$BIN_LINK" setup
  ok "Pi-hole is set up"
  install_services
  step "Final check"
  "$BIN_LINK" doctor || warn "Some checks failed — see above. Run 'sudo sinko doctor' again later."
  summary
  show_generated_password
}

# ------------------------------------------------------------------ output
if [[ -t 1 ]]; then B=$'\e[1m'; G=$'\e[32m'; Y=$'\e[33m'; RD=$'\e[31m'; N=$'\e[0m'; else B=; G=; Y=; RD=; N=; fi
step() { printf '\n%s==>%s %s\n' "$B" "$N" "$*"; }
ok()   { printf '  %s✓%s %s\n' "$G" "$N" "$*"; }
warn() { printf '  %s!%s %s\n' "$Y" "$N" "$*"; }
die()  { printf '\n%sError:%s %s\n' "$RD" "$N" "$*" >&2; exit 1; }
on_error() {
  printf '\n%sInstallation failed%s (line %s). Full log: %s\n' "$RD" "$N" "$1" "$LOG_FILE" >&2
  printf 'It is safe to run the installer again after fixing the problem.\n' >&2
}
TTY_DEV="${SINKO_TTY:-/dev/tty}"      # the terminal; SINKO_TTY is a test hook
can_prompt() { [[ "${SINKO_NONINTERACTIVE:-}" != 1 ]] && { : <"$TTY_DEV"; } 2>/dev/null; }
has_tty() { { : >>"$TTY_DEV"; } 2>/dev/null; }

# ------------------------------------------------------------------ steps
# shellcheck disable=SC1091  # /etc/os-release is read at runtime
preflight() {
  step "Checking this device"
  [[ "$(id -u)" -eq 0 ]] || die "Run as root: curl -fsSL <url> | sudo bash"
  [[ -r /etc/os-release ]] || die "Unknown operating system (no /etc/os-release)."
  local id like pretty
  id="$(. /etc/os-release && echo "${ID:-}")"
  like="$(. /etc/os-release && echo "${ID_LIKE:-}")"
  pretty="$(. /etc/os-release && echo "${PRETTY_NAME:-$id}")"
  if [[ ! " $id $like " =~ [[:space:]](debian|ubuntu)[[:space:]] ]]; then
    die "Unsupported system: $pretty. Use Armbian, Debian, Ubuntu or Raspberry Pi OS."
  fi
  ok "System: $pretty ($(uname -m))"
  case "$(uname -m)" in
    aarch64|arm64|x86_64|armv7l) ;;
    *) warn "Architecture $(uname -m) is untested." ;;
  esac
  command -v systemctl >/dev/null || die "systemd is required."
  local free_kb
  free_kb="$(df -Pk "${SINKO_ROOT:-/}" | awk 'NR==2 {print $4}')"
  (( free_kb > 1024 * 1024 )) || die "At least 1 GB of free disk space is needed."
  ok "Disk space: $(( free_kb / 1024 )) MB free"

  local missing=()
  for pkg in git curl ca-certificates python3 iproute2; do
    dpkg -s "$pkg" >/dev/null 2>&1 || missing+=("$pkg")
  done
  if (( ${#missing[@]} )); then
    step "Installing ${missing[*]}"
    { DEBIAN_FRONTEND=noninteractive apt-get update -qq </dev/null \
        && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends "${missing[@]}" </dev/null >/dev/null; } \
      || die "Could not install ${missing[*]}. Check the internet connection and run the installer again."
  fi
  # The internet is checked where it is needed (fetching the release, installing Pi-hole): an offline
  # "sinko rollback" or an install from SINKO_SRC must keep working.
}

need_internet() {
  curl -fsS --max-time 15 -o /dev/null https://github.com 2>/dev/null \
    || die "No internet connection (cannot reach github.com). Check the cable and the router, then run the installer again."
}

# ------------------------------------------------------------------ settings
# What can be saved in /etc/sinko/config (and be overridden by an environment variable of the same name).
SETTING_KEYS="HOSTNAME LISTS_BASE REPO REPO_SLUG REF IP RELEASE_BASE"
declare -A ENVV=() SAVED=()

# Reads the KEY=value lines of a settings file (shell syntax, as write_settings writes it) into the associative array
# $3, for the keys the file really sets. Runs in a subshell so nothing leaks into the installer, and with the
# environment's own values removed first so they cannot be mistaken for the file's.
read_settings_file() {  # file prefix array-name
  local file="$1" prefix="$2" name="$3" out
  [[ -f "$file" ]] || return 0
  out="$(
    set +eu
    for k in $SETTING_KEYS; do unset "SINKO_$k"; done
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
  # Precedence: environment > saved config > defaults.
  local k v
  ENVV=(); SAVED=()
  for k in $SETTING_KEYS; do
    v="SINKO_$k"
    if [[ -n "${!v+x}" ]]; then ENVV[$k]="${!v}"; fi
  done
  read_settings_file "$CONF_FILE" SINKO_ SAVED

  if v="$(env_value HOSTNAME)" || v="$(saved_value HOSTNAME)"; then SINKO_HOSTNAME="$v"; else SINKO_HOSTNAME="family.lan"; fi
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
    repo="$(saved_value REPO || true)"; slug="$(saved_value REPO_SLUG || true)"
    [[ -n "$slug" || -z "$repo" ]] || slug="$(slug_of_repo "$repo")"
  fi
  SINKO_REPO_SLUG="${slug:-$DEFAULT_SLUG}"
  SINKO_REPO="${repo:-https://github.com/$SINKO_REPO_SLUG.git}"
  [[ "$SINKO_REPO_SLUG" =~ ^[A-Za-z0-9_.-]{1,60}/[A-Za-z0-9_.-]{1,60}$ && "$SINKO_REPO_SLUG" != ./* && "$SINKO_REPO_SLUG" != ../* \
     && "$SINKO_REPO_SLUG" != */. && "$SINKO_REPO_SLUG" != */.. ]] \
    || die "SINKO_REPO_SLUG '$SINKO_REPO_SLUG' is not a GitHub name like iret33/sinko"
  [[ "$SINKO_REPO" =~ ^(https://|file://|git@)[A-Za-z0-9._~:/@+%=-]+$ ]] \
    || die "SINKO_REPO '$SINKO_REPO' is not a git address (https://… or file://…)"

  SINKO_REF="$(env_value REF || saved_value REF || echo "$DEFAULT_REF")"
  [[ -n "$SINKO_REF" ]] || SINKO_REF="$DEFAULT_REF"
  if [[ ! "$SINKO_REF" =~ ^[A-Za-z0-9][A-Za-z0-9._/-]{0,100}$ || "$SINKO_REF" == *..* ]]; then
    die "SINKO_REF '$SINKO_REF' is not a version or branch name (use latest, vX.Y.Z or a branch such as master)"
  fi

  # A saved list address that is just the default of the project saved with it was never a choice: it follows
  # the project if that changes (a fork, a rename). Any other saved or given address stays.
  local kept_lists kept_slug
  kept_lists="$(saved_value LISTS_BASE || true)"
  kept_slug="$(saved_value REPO_SLUG || true)"
  if [[ "$kept_lists" == "https://raw.githubusercontent.com/${kept_slug:-$DEFAULT_SLUG}/master/lists" ]]; then kept_lists=""; fi
  SINKO_LISTS_BASE="$(env_value LISTS_BASE || echo "$kept_lists")"
  SINKO_LISTS_BASE="${SINKO_LISTS_BASE:-https://raw.githubusercontent.com/$SINKO_REPO_SLUG/master/lists}"
  SINKO_RELEASE_BASE="$(env_value RELEASE_BASE || saved_value RELEASE_BASE || true)"
  SINKO_RELEASE_BASE="${SINKO_RELEASE_BASE:-https://github.com/$SINKO_REPO_SLUG/releases}"
  SINKO_RELEASE_BASE="${SINKO_RELEASE_BASE%/}"
  [[ "$SINKO_RELEASE_BASE" =~ ^https?://[A-Za-z0-9._~:/@+%=-]+$ ]] \
    || die "SINKO_RELEASE_BASE '$SINKO_RELEASE_BASE' is not a web address (https://…)"
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

tree_complete() {  # DIR
  local d="$1" f
  for f in bin/sinko install.sh VERSION lists/services.json web/index.html systemd/sinko.service; do
    [[ -f "$d/$f" ]] || return 1
  done
}

# curl to a file. Returns 0 on HTTP 200, 1 on any other HTTP answer (the code is in HTTP_CODE), 2 when the server
# could not be reached at all, 3 when the file is bigger than allowed.
http_get() {  # url dest max-bytes
  local rc=0
  HTTP_CODE="$(curl -sSL --connect-timeout 15 --max-time 600 --max-filesize "$3" -o "$2" -w '%{http_code}' "$1" 2>/dev/null </dev/null)" || rc=$?
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
  git clone --quiet --depth 1 --branch "$SINKO_REF" -- "$SINKO_REPO" "$dest.tmp" </dev/null \
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
    curl -fsSL https://install.pi-hole.net -o "$installer"
  fi
  bash "$installer" --unattended </dev/null
  rm -rf "$tmp"
  command -v pihole-FTL >/dev/null || die "Pi-hole did not install correctly."
  ok "Pi-hole installed"
}

install_files() {
  step "Installing the parent page"
  install -d -m 755 "$APP_DIR" "$APP_DIR/bin" "$APP_DIR/lists" "$APP_DIR/tools" "$CONF_DIR" "$(dirname "$BIN_LINK")"
  install -m 755 "$SRC/bin/sinko" "$APP_DIR/bin/sinko"
  rm -f "$APP_DIR"/lists/*.txt
  install -m 644 "$SRC"/lists/*.txt "$SRC/lists/services.json" "$APP_DIR/lists/"
  install -m 644 "$SRC/VERSION" "$APP_DIR/VERSION"
  install -m 755 "$SRC/uninstall.sh" "$APP_DIR/uninstall.sh"
  # The ready-made image tools. A git checkout holds other scripts too (release build, …): only these two belong on a box.
  rm -f "$APP_DIR"/tools/*.sh
  local tool
  for tool in seal.sh firstboot.sh; do
    if [[ -f "$SRC/tools/$tool" ]]; then install -m 755 "$SRC/tools/$tool" "$APP_DIR/tools/$tool"; fi
  done
  ln -sfn "$APP_DIR/bin/sinko" "$BIN_LINK"
  # Runtime data (update results, the counter's id, rollback copies): root only, never reachable from the page.
  install -d -m 700 "$STATE_DIR" "$STATE_DIR/cache"
  chmod 700 "$STATE_DIR"

  WEBROOT="$(pihole-FTL --config -q webserver.paths.webroot 2>/dev/null || true)"
  WEBROOT="${WEBROOT:-$R/var/www/html}"
  install -d -m 755 "$WEBROOT"
  if [[ -f "$WEBROOT/index.html" ]] && ! grep -q 'name="generator" content="sinko"' "$WEBROOT/index.html"; then
    if grep -q 'parental/app.js' "$WEBROOT/index.html"; then
      rm -f "$WEBROOT/index.html"             # page from the 1.x installer
    elif [[ ! -f "$WEBROOT/index.html.pb-backup" ]]; then
      mv "$WEBROOT/index.html" "$WEBROOT/index.html.pb-backup"
      ok "Kept the previous start page as index.html.pb-backup"
    fi
  fi
  rm -rf "$WEBROOT/parental" "$WEBROOT/pb.new"
  install -d -m 755 "$WEBROOT/pb.new" "$WEBROOT/pb.new/fonts"
  local f
  for f in "$SRC"/web/*; do
    [[ -f "$f" && "$(basename "$f")" != index.html ]] && install -m 644 "$f" "$WEBROOT/pb.new/"
  done
  install -m 644 "$SRC"/web/fonts/* "$WEBROOT/pb.new/fonts/"
  install -m 644 "$SRC/lists/services.json" "$WEBROOT/pb.new/services.json"
  "$BIN_LINK" domain-map >"$WEBROOT/pb.new/domains.json"   # domain -> app, so the page can name what a device opens
  chmod 644 "$WEBROOT/pb.new/domains.json"
  install -m 644 "$SRC/VERSION" "$WEBROOT/pb.new/version.txt"
  rm -rf "$WEBROOT/pb"
  mv "$WEBROOT/pb.new" "$WEBROOT/pb"
  install -m 644 "$SRC/web/index.html" "$WEBROOT/index.html"
  ok "Page installed in $WEBROOT"
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

set_password() {
  step "Parent password"
  local state=0
  "$BIN_LINK" password-state || state=$?
  SHOW_PASSWORD=""
  local pw="${SINKO_PASSWORD:-}"
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
  elif [[ -z "$pw" ]]; then
    pw="$(gen_password)"
    SHOW_PASSWORD="$pw"
  fi
  (( ${#pw} >= 8 )) || die "SINKO_PASSWORD must be at least 8 characters."
  pihole setpassword "$pw" >/dev/null
  ok "Password set"
}

install_services() {
  step "Starting background services"
  local pihole_bin
  pihole_bin="$(command -v pihole || echo /usr/local/bin/pihole)"
  install -d -m 755 "$UNIT_DIR"
  install -m 644 "$SRC/systemd/sinko.service" "$UNIT_DIR/sinko.service"
  install -m 644 "$SRC/systemd/sinko-lists.timer" "$UNIT_DIR/sinko-lists.timer"
  sed "s|@PIHOLE@|$pihole_bin|g" "$SRC/systemd/sinko-lists.service" >"$UNIT_DIR/sinko-lists.service"
  chmod 644 "$UNIT_DIR/sinko-lists.service"
  # Installed on every box but enabled only by tools/seal.sh, on the ready-made image (it is also guarded by a flag file).
  if [[ -f "$SRC/systemd/sinko-firstboot.service" ]]; then
    install -m 644 "$SRC/systemd/sinko-firstboot.service" "$UNIT_DIR/sinko-firstboot.service"
  fi
  systemctl daemon-reload
  systemctl enable --quiet sinko.service sinko-lists.timer
  systemctl restart sinko.service
  systemctl restart sinko-lists.timer
  ok "Scheduler running; lists refresh every night"
}

# A password we generated is never written to stdout, because stdout is copied into the install log.
# It goes to the terminal only; without a terminal it is saved in a root-only file instead.
show_generated_password() {
  [[ -n "${SHOW_PASSWORD:-}" ]] || return 0
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
  local url="http://$IPV4/"
  cat <<EOF

${G}${B}Sinko is ready.${N}

  Parent page:   ${B}$url${N}${SINKO_HOSTNAME:+
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
