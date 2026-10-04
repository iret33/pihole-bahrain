#!/usr/bin/env bash
# Nay — installer and updater.
#
#   curl -fsSL https://raw.githubusercontent.com/iret33/nay/master/install.sh | sudo bash
#
# Installs Pi-hole v6 (if missing, fully unattended), the parent page, the
# scheduler service and the service block lists. Safe to run again: it then
# updates everything and keeps your password, devices and rules.
#
# Optional settings (environment variables, e.g. `curl … | sudo NAY_PASSWORD=… bash`):
#   NAY_PASSWORD        parent password. Otherwise you are asked (or one is generated).
#   NAY_HOSTNAME        local name for the page, default family.lan ("none" to skip)
#   NAY_UPSTREAMS       upstream DNS for a NEW Pi-hole, default 1.1.1.3,1.0.0.3
#                      (Cloudflare for Families: also blocks malware and adult sites)
#   NAY_TIMEZONE        e.g. Asia/Bahrain. Default: Asia/Bahrain if the system is on UTC.
#   NAY_LISTS_BASE      where Pi-hole downloads the service lists
#   NAY_REPO, NAY_REF    git source, default this repository, branch master
#   NAY_NONINTERACTIVE  1 = never prompt
set -Eeuo pipefail
shopt -s inherit_errexit   # also stop on failures inside $(…), e.g. a failed download

main() {
  # ---------------------------------------------------------------- constants
  local R="${NAY_ROOT:-}"                        # test hook: install under a fake root
  APP_DIR="$R/opt/nay"
  CONF_DIR="$R/etc/nay"
  CONF_FILE="$CONF_DIR/config"
  TOML="$R/etc/pihole/pihole.toml"
  UNIT_DIR="$R/etc/systemd/system"
  BIN_LINK="$R/usr/local/bin/nay"
  LOG_FILE="$R/var/log/nay-install.log"
  DEFAULT_REPO="https://github.com/iret33/nay.git"
  DEFAULT_REF="master"

  mkdir -p "$(dirname "$LOG_FILE")"
  if [[ "${NAY_REEXEC:-}" != 1 ]]; then
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
  echo "=== nay installer — $(date -u '+%Y-%m-%d %H:%M:%S UTC') ==="

  preflight
  local src
  src="$(locate_source)"
  if [[ "${NAY_REEXEC:-}" != 1 && "$src" == "$APP_DIR/src" && -f "$src/install.sh" ]]; then
    # Always run the installer that ships with the code we are installing.
    step "Starting the installer from the downloaded version"
    NAY_REEXEC=1 exec bash "$src/install.sh"
  fi
  SRC="$src"
  VERSION="$(cat "$SRC/VERSION" 2>/dev/null || echo unknown)"
  ok "Installing version $VERSION from $SRC"

  load_settings
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
  "$BIN_LINK" doctor || warn "Some checks failed — see above. Run 'sudo nay doctor' again later."
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
TTY_DEV="${NAY_TTY:-/dev/tty}"      # the terminal; NAY_TTY is a test hook
can_prompt() { [[ "${NAY_NONINTERACTIVE:-}" != 1 ]] && { : <"$TTY_DEV"; } 2>/dev/null; }
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
  free_kb="$(df -Pk "${NAY_ROOT:-/}" | awk 'NR==2 {print $4}')"
  (( free_kb > 1024 * 1024 )) || die "At least 1 GB of free disk space is needed."
  ok "Disk space: $(( free_kb / 1024 )) MB free"

  local missing=()
  for pkg in git curl ca-certificates python3 iproute2; do
    dpkg -s "$pkg" >/dev/null 2>&1 || missing+=("$pkg")
  done
  if (( ${#missing[@]} )); then
    step "Installing ${missing[*]}"
    DEBIAN_FRONTEND=noninteractive apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends "${missing[@]}" >/dev/null
  fi
  curl -fsS --max-time 15 -o /dev/null https://github.com \
    || die "No internet connection (cannot reach github.com)."
  ok "Internet connection works"
}

locate_source() {
  local self="${BASH_SOURCE[0]:-}" dir=""
  if [[ -n "$self" && -f "$self" ]]; then
    dir="$(cd "$(dirname "$self")" && pwd)"
  fi
  if [[ -n "$dir" && -f "$dir/bin/nay" && -d "$dir/lists" && -d "$dir/web" ]]; then
    echo "$dir"
    return
  fi
  # Started through curl | bash: fetch the code.
  local repo="${NAY_REPO:-$DEFAULT_REPO}" ref="${NAY_REF:-$DEFAULT_REF}"
  local dest="$APP_DIR/src"
  {
    step "Downloading nay ($ref)"
    mkdir -p "$APP_DIR"
    rm -rf "$dest.tmp"
    git clone --quiet --depth 1 --branch "$ref" "$repo" "$dest.tmp"
    rm -rf "$dest"
    mv "$dest.tmp" "$dest"
    ok "Downloaded $(git -C "$dest" rev-parse --short HEAD)"
  } >&2
  echo "$dest"
}

load_settings() {
  # Precedence: environment > saved config > defaults.
  local env_hostname="${NAY_HOSTNAME-__unset__}" env_lists="${NAY_LISTS_BASE:-}"
  local env_repo="${NAY_REPO:-}" env_ref="${NAY_REF:-}"
  if [[ -f "$CONF_FILE" ]]; then
    # shellcheck disable=SC1090
    . "$CONF_FILE"
  fi
  [[ "$env_hostname" != "__unset__" ]] && NAY_HOSTNAME="$env_hostname"
  NAY_HOSTNAME="${NAY_HOSTNAME-family.lan}"
  [[ "$NAY_HOSTNAME" == none ]] && NAY_HOSTNAME=""
  if [[ -n "$NAY_HOSTNAME" && ! "$NAY_HOSTNAME" =~ ^[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?)+$ ]]; then
    die "NAY_HOSTNAME '$NAY_HOSTNAME' is not a valid name like family.lan"
  fi
  NAY_LISTS_BASE="${env_lists:-${NAY_LISTS_BASE:-https://raw.githubusercontent.com/iret33/nay/master/lists}}"
  NAY_REPO="${env_repo:-${NAY_REPO:-$DEFAULT_REPO}}"
  NAY_REF="${env_ref:-${NAY_REF:-$DEFAULT_REF}}"
}

detect_network() {
  step "Checking the network"
  IFACE="${NAY_INTERFACE:-$(ip -4 route show default 2>/dev/null | awk '{for (i=1;i<NF;i++) if ($i=="dev") {print $(i+1); exit}}')}"
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
    local up="${NAY_UPSTREAMS:-1.1.1.3,1.0.0.3}" list="" u
    IFS=',' read -r -a ups <<<"$up"
    for u in "${ups[@]}"; do
      u="${u// /}"
      [[ "$u" =~ ^[0-9A-Fa-f:.#]+$ ]] || die "Invalid NAY_UPSTREAMS entry: $u"
      list+="${list:+, }\"$u\""
    done
    mkdir -p "$(dirname "$TOML")"
    cat >"$TOML" <<EOF
# Pre-seeded by nay for an unattended Pi-hole install.
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
  installer="${NAY_PIHOLE_INSTALLER:-}"
  if [[ -z "$installer" ]]; then
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
  install -d -m 755 "$APP_DIR" "$APP_DIR/bin" "$APP_DIR/lists" "$CONF_DIR" "$(dirname "$BIN_LINK")"
  install -m 755 "$SRC/bin/nay" "$APP_DIR/bin/nay"
  rm -f "$APP_DIR"/lists/*.txt
  install -m 644 "$SRC"/lists/*.txt "$SRC/lists/services.json" "$APP_DIR/lists/"
  install -m 644 "$SRC/VERSION" "$APP_DIR/VERSION"
  install -m 755 "$SRC/uninstall.sh" "$APP_DIR/uninstall.sh"
  ln -sfn "$APP_DIR/bin/nay" "$BIN_LINK"

  WEBROOT="$(pihole-FTL --config -q webserver.paths.webroot 2>/dev/null || true)"
  WEBROOT="${WEBROOT:-$R/var/www/html}"
  install -d -m 755 "$WEBROOT"
  if [[ -f "$WEBROOT/index.html" ]] && ! grep -q 'name="generator" content="nay"' "$WEBROOT/index.html"; then
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
    echo "# nay settings. Re-run the installer after editing."
    printf 'NAY_HOSTNAME=%q\n' "$NAY_HOSTNAME"
    printf 'NAY_LISTS_BASE=%q\n' "$NAY_LISTS_BASE"
    printf 'NAY_REPO=%q\n' "$NAY_REPO"
    printf 'NAY_REF=%q\n' "$NAY_REF"
    printf 'NAY_IP=%q\n' "$IPV4"
  } >"$tmp"
  chmod 644 "$tmp"
  mv "$tmp" "$CONF_FILE"
}

set_timezone() {
  command -v timedatectl >/dev/null || return 0
  local current want="${NAY_TIMEZONE:-}"
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
  "$BIN_LINK" configure --ip "$IPV4" --hostname "$NAY_HOSTNAME"   # "" removes the name
  ok "Parent page enabled at http://$IPV4/${NAY_HOSTNAME:+ and http://$NAY_HOSTNAME/}"
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
  local pw="${NAY_PASSWORD:-}"
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
  (( ${#pw} >= 8 )) || die "NAY_PASSWORD must be at least 8 characters."
  pihole setpassword "$pw" >/dev/null
  ok "Password set"
}

install_services() {
  step "Starting background services"
  local pihole_bin
  pihole_bin="$(command -v pihole || echo /usr/local/bin/pihole)"
  install -d -m 755 "$UNIT_DIR"
  install -m 644 "$SRC/systemd/nay.service" "$UNIT_DIR/nay.service"
  install -m 644 "$SRC/systemd/nay-lists.timer" "$UNIT_DIR/nay-lists.timer"
  sed "s|@PIHOLE@|$pihole_bin|g" "$SRC/systemd/nay-lists.service" >"$UNIT_DIR/nay-lists.service"
  chmod 644 "$UNIT_DIR/nay-lists.service"
  systemctl daemon-reload
  systemctl enable --quiet nay.service nay-lists.timer
  systemctl restart nay.service
  systemctl restart nay-lists.timer
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

${G}${B}Nay is ready.${N}

  Parent page:   ${B}$url${N}${NAY_HOSTNAME:+
                 or http://$NAY_HOSTNAME/ (after the router step below)}
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

  Check the installation any time: sudo nay doctor
  Update:                          sudo nay update
  Remove:                          sudo /opt/nay/uninstall.sh
EOF
}

main "$@"
