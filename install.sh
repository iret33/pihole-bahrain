#!/usr/bin/env bash
# Family Internet (pihole-bahrain) — installer and updater.
#
#   curl -fsSL https://raw.githubusercontent.com/iret33/pihole-bahrain/master/install.sh | sudo bash
#
# Installs Pi-hole v6 (if missing, fully unattended), the parent page, the
# scheduler service and the service block lists. Safe to run again: it then
# updates everything and keeps your password, devices and rules.
#
# Optional settings (environment variables, e.g. `curl … | sudo PB_PASSWORD=… bash`):
#   PB_PASSWORD        parent password. Otherwise you are asked (or one is generated).
#   PB_HOSTNAME        local name for the page, default family.lan ("none" to skip)
#   PB_UPSTREAMS       upstream DNS for a NEW Pi-hole, default 1.1.1.3,1.0.0.3
#                      (Cloudflare for Families: also blocks malware and adult sites)
#   PB_TIMEZONE        e.g. Asia/Bahrain. Default: Asia/Bahrain if the system is on UTC.
#   PB_LISTS_BASE      where Pi-hole downloads the service lists
#   PB_REPO, PB_REF    git source, default this repository, branch master
#   PB_NONINTERACTIVE  1 = never prompt
set -Eeuo pipefail
shopt -s inherit_errexit   # also stop on failures inside $(…), e.g. a failed download

main() {
  # ---------------------------------------------------------------- constants
  local R="${PB_ROOT:-}"                        # test hook: install under a fake root
  APP_DIR="$R/opt/pihole-bahrain"
  CONF_DIR="$R/etc/pihole-bahrain"
  CONF_FILE="$CONF_DIR/config"
  TOML="$R/etc/pihole/pihole.toml"
  UNIT_DIR="$R/etc/systemd/system"
  BIN_LINK="$R/usr/local/bin/pihole-bahrain"
  LOG_FILE="$R/var/log/pihole-bahrain-install.log"
  DEFAULT_REPO="https://github.com/iret33/pihole-bahrain.git"
  DEFAULT_REF="master"

  mkdir -p "$(dirname "$LOG_FILE")"
  if [[ "${PB_REEXEC:-}" != 1 ]]; then
    exec > >(tee -a "$LOG_FILE") 2>&1
  fi
  trap 'on_error $LINENO' ERR
  echo
  echo "=== pihole-bahrain installer — $(date -u '+%Y-%m-%d %H:%M:%S UTC') ==="

  preflight
  local src
  src="$(locate_source)"
  if [[ "${PB_REEXEC:-}" != 1 && "$src" == "$APP_DIR/src" && -f "$src/install.sh" ]]; then
    # Always run the installer that ships with the code we are installing.
    step "Starting the installer from the downloaded version"
    PB_REEXEC=1 exec bash "$src/install.sh"
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
  "$BIN_LINK" doctor || warn "Some checks failed — see above. Run 'sudo pihole-bahrain doctor' again later."
  summary
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
can_prompt() { [[ "${PB_NONINTERACTIVE:-}" != 1 ]] && { : </dev/tty; } 2>/dev/null; }

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
  free_kb="$(df -Pk "${PB_ROOT:-/}" | awk 'NR==2 {print $4}')"
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
  if [[ -n "$dir" && -f "$dir/bin/pihole-bahrain" && -d "$dir/lists" && -d "$dir/web" ]]; then
    echo "$dir"
    return
  fi
  # Started through curl | bash: fetch the code.
  local repo="${PB_REPO:-$DEFAULT_REPO}" ref="${PB_REF:-$DEFAULT_REF}"
  local dest="$APP_DIR/src"
  {
    step "Downloading pihole-bahrain ($ref)"
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
  local env_hostname="${PB_HOSTNAME-__unset__}" env_lists="${PB_LISTS_BASE:-}"
  local env_repo="${PB_REPO:-}" env_ref="${PB_REF:-}"
  if [[ -f "$CONF_FILE" ]]; then
    # shellcheck disable=SC1090
    . "$CONF_FILE"
  fi
  [[ "$env_hostname" != "__unset__" ]] && PB_HOSTNAME="$env_hostname"
  PB_HOSTNAME="${PB_HOSTNAME-family.lan}"
  [[ "$PB_HOSTNAME" == none ]] && PB_HOSTNAME=""
  if [[ -n "$PB_HOSTNAME" && ! "$PB_HOSTNAME" =~ ^[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?)+$ ]]; then
    die "PB_HOSTNAME '$PB_HOSTNAME' is not a valid name like family.lan"
  fi
  PB_LISTS_BASE="${env_lists:-${PB_LISTS_BASE:-https://raw.githubusercontent.com/iret33/pihole-bahrain/master/lists}}"
  PB_REPO="${env_repo:-${PB_REPO:-$DEFAULT_REPO}}"
  PB_REF="${env_ref:-${PB_REF:-$DEFAULT_REF}}"
}

detect_network() {
  step "Checking the network"
  IFACE="${PB_INTERFACE:-$(ip -4 route show default 2>/dev/null | awk '{for (i=1;i<NF;i++) if ($i=="dev") {print $(i+1); exit}}')}"
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
    local up="${PB_UPSTREAMS:-1.1.1.3,1.0.0.3}" list="" u
    IFS=',' read -r -a ups <<<"$up"
    for u in "${ups[@]}"; do
      u="${u// /}"
      [[ "$u" =~ ^[0-9A-Fa-f:.#]+$ ]] || die "Invalid PB_UPSTREAMS entry: $u"
      list+="${list:+, }\"$u\""
    done
    mkdir -p "$(dirname "$TOML")"
    cat >"$TOML" <<EOF
# Pre-seeded by pihole-bahrain for an unattended Pi-hole install.
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
  installer="${PB_PIHOLE_INSTALLER:-}"
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
  install -m 755 "$SRC/bin/pihole-bahrain" "$APP_DIR/bin/pihole-bahrain"
  rm -f "$APP_DIR"/lists/*.txt
  install -m 644 "$SRC"/lists/*.txt "$SRC/lists/services.json" "$APP_DIR/lists/"
  install -m 644 "$SRC/VERSION" "$APP_DIR/VERSION"
  install -m 755 "$SRC/uninstall.sh" "$APP_DIR/uninstall.sh"
  ln -sfn "$APP_DIR/bin/pihole-bahrain" "$BIN_LINK"

  WEBROOT="$(pihole-FTL --config -q webserver.paths.webroot 2>/dev/null || true)"
  WEBROOT="${WEBROOT:-$R/var/www/html}"
  install -d -m 755 "$WEBROOT"
  if [[ -f "$WEBROOT/index.html" ]] && ! grep -q 'name="generator" content="pihole-bahrain"' "$WEBROOT/index.html"; then
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
  install -m 644 "$SRC/VERSION" "$WEBROOT/pb.new/version.txt"
  rm -rf "$WEBROOT/pb"
  mv "$WEBROOT/pb.new" "$WEBROOT/pb"
  install -m 644 "$SRC/web/index.html" "$WEBROOT/index.html"
  ok "Page installed in $WEBROOT"
}

write_settings() {
  local tmp="$CONF_FILE.tmp"
  {
    echo "# pihole-bahrain settings. Re-run the installer after editing."
    printf 'PB_HOSTNAME=%q\n' "$PB_HOSTNAME"
    printf 'PB_LISTS_BASE=%q\n' "$PB_LISTS_BASE"
    printf 'PB_REPO=%q\n' "$PB_REPO"
    printf 'PB_REF=%q\n' "$PB_REF"
    printf 'PB_IP=%q\n' "$IPV4"
  } >"$tmp"
  chmod 644 "$tmp"
  mv "$tmp" "$CONF_FILE"
}

set_timezone() {
  command -v timedatectl >/dev/null || return 0
  local current want="${PB_TIMEZONE:-}"
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
  "$BIN_LINK" configure --ip "$IPV4" --hostname "$PB_HOSTNAME"   # "" removes the name
  ok "Parent page enabled at http://$IPV4/${PB_HOSTNAME:+ and http://$PB_HOSTNAME/}"
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
  local pw="${PB_PASSWORD:-}"
  if [[ -z "$pw" && $state -eq 0 ]]; then
    ok "Keeping the existing password"
    return
  fi
  if [[ -z "$pw" ]] && can_prompt; then
    local again
    while true; do
      read -r -s -p "  Choose a password for the parent page (8+ characters): " pw </dev/tty; echo
      if (( ${#pw} < 8 )); then echo "  Too short, try again."; continue; fi
      read -r -s -p "  Type it again: " again </dev/tty; echo
      [[ "$pw" == "$again" ]] && break
      echo "  The two passwords are different, try again."
    done
  elif [[ -z "$pw" ]]; then
    pw="$(gen_password)"
    SHOW_PASSWORD="$pw"
  fi
  (( ${#pw} >= 8 )) || die "PB_PASSWORD must be at least 8 characters."
  pihole setpassword "$pw" >/dev/null
  ok "Password set"
}

install_services() {
  step "Starting background services"
  local pihole_bin
  pihole_bin="$(command -v pihole || echo /usr/local/bin/pihole)"
  install -d -m 755 "$UNIT_DIR"
  install -m 644 "$SRC/systemd/pihole-bahrain.service" "$UNIT_DIR/pihole-bahrain.service"
  install -m 644 "$SRC/systemd/pihole-bahrain-lists.timer" "$UNIT_DIR/pihole-bahrain-lists.timer"
  sed "s|@PIHOLE@|$pihole_bin|g" "$SRC/systemd/pihole-bahrain-lists.service" >"$UNIT_DIR/pihole-bahrain-lists.service"
  chmod 644 "$UNIT_DIR/pihole-bahrain-lists.service"
  systemctl daemon-reload
  systemctl enable --quiet pihole-bahrain.service pihole-bahrain-lists.timer
  systemctl restart pihole-bahrain.service
  systemctl restart pihole-bahrain-lists.timer
  ok "Scheduler running; lists refresh every night"
}

summary() {
  local url="http://$IPV4/"
  cat <<EOF

${G}${B}Family Internet is ready.${N}

  Parent page:   ${B}$url${N}${PB_HOSTNAME:+
                 or http://$PB_HOSTNAME/ (after the router step below)}
EOF
  if [[ -n "${SHOW_PASSWORD:-}" ]]; then
    printf '  Password:      %s%s%s   (write it down; change it with: sudo pihole setpassword)\n' "$B" "$SHOW_PASSWORD" "$N"
  fi
  cat <<EOF
  Pi-hole admin: http://$IPV4/admin/

  ${B}Last step — make your home use this box:${N}
  1. Open your router's settings (usually http://$(ip -4 route show default | awk '{print $3; exit}')/).
  2. Find "DNS server" in the LAN or DHCP settings and set it to $IPV4 only.
  3. Reserve $IPV4 for this device ("DHCP reservation" / "static lease").
  4. Restart Wi-Fi on the children's devices, then add them on the parent page.

  Check the installation any time: sudo pihole-bahrain doctor
  Update:                          sudo pihole-bahrain update
  Remove:                          sudo /opt/pihole-bahrain/uninstall.sh
EOF
}

main "$@"
