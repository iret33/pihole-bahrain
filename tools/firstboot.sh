#!/usr/bin/env bash
# First start of a ready-made Sinko box: make the things that must be unique to this box its own.
# Run by sinko-firstboot.service, which only starts while /var/lib/sinko/firstboot exists (tools/seal.sh puts it there).
#
#   1. new SSH host keys            (the golden unit's keys are on every card that was flashed from its image)
#   2. the host name, and with it the <name>.local that avahi announces
#   3. no stale install-id          (the anonymous counter's random number must not be shared between boxes)
#   4. Pi-hole's own HTTPS key      (the seal removed the golden unit's; Pi-hole's FTL makes a new one when it starts
#                                    without: this waits until the file is there, and restarts FTL once if it is not)
#   5. the box's address for THIS network: "sinko configure --ip <address> --hostname <name>" and SINKO_IP
#
# Steps 1-4 need nothing from the network and are done first. The flag is removed when all of it worked. When the network
# is not up yet (cable not plugged in, router slow), or Pi-hole is not ready for step 5, the script ends well and leaves
# the flag, so the next start tries again (nothing already done is done twice: the flag remembers the keys and the id).
# After three starts without an address it lets go: the scheduler's address watch (every 5 minutes) sets the address as
# soon as there is one, and a flag that stays would hold back SSH and the scheduler at every start.
#
# It is meant to be quick: it waits only for what it needs (a cable that is not there, a key that FTL has not written yet),
# and it never restarts Pi-hole when the key is already there. Until a parent has chosen a password on the welcome screen,
# anybody on the home network could do it first: the shorter the start, the shorter that time.
set -uo pipefail

R="${SINKO_ROOT:-}"                                  # test hook: act on a fake root
STATE_DIR="${SINKO_STATE_DIR:-$R/var/lib/sinko}"
FLAG="$STATE_DIR/firstboot"
CONF_FILE="$R/etc/sinko/config"
BIN="$R/opt/sinko/bin/sinko"
WAIT="${SINKO_FIRSTBOOT_WAIT:-30}"                   # seconds to wait for an address at the first start; SINKO_FIRSTBOOT_WAIT is a test hook
RETRY_WAIT=10                                        # ... and at the later starts
MAX_TRIES=3                                          # starts without an address before the scheduler's address watch takes over
TLS_WAIT_FIRST="${SINKO_FIRSTBOOT_TLS_WAIT:-10}"     # seconds FTL gets to write its HTTPS key by itself; SINKO_FIRSTBOOT_TLS_WAIT is a test hook
TLS_WAIT_AFTER="${SINKO_FIRSTBOOT_TLS_WAIT:-30}"     # ... and after it was restarted

log() { echo "sinko-firstboot: $*"; }
# A problem on this box (not the network): visible as a failed unit, and tried again at the next start.
fail() { echo "sinko-firstboot: $*" >&2; exit 1; }

[[ "$(id -u)" -eq 0 ]] || fail "run as root"
if [[ ! -f "$FLAG" ]]; then
  log "nothing to do: this box has already been set up"
  exit 0
fi
# The seal's zero file. A seal that was cut short (a power cut during the zero-fill) leaves it, and it fills the whole card: this
# box could then write nothing (not its keys, not the flag, not Pi-hole's database). It goes first, before anything is written.
if [[ -e "$R/.sinko-zerofill" || -L "$R/.sinko-zerofill" ]]; then
  rm -f "$R/.sinko-zerofill" || fail "cannot remove the leftover zero file $R/.sinko-zerofill (it fills the card)"
  log "removed the leftover zero file of an interrupted seal (it filled the card)"
fi
[[ -x "$BIN" ]] || fail "Sinko is not installed ($BIN is missing)"

# ------------------------------------------------------------------ what the flag says
flag_value() {  # key
  sed -n "s/^$1=//p" "$FLAG" | head -n1
}
name="$(flag_value hostname)"
if [[ ! "$name" =~ ^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$ ]]; then
  [[ -z "$name" ]] || log "the host name '$name' in the flag is not valid; using sinko"
  name=sinko
fi
keys_done="$(flag_value keys)"
ids_done="$(flag_value ids)"
tries="$(flag_value tries)"
[[ "$tries" =~ ^[0-9]{1,3}$ ]] || tries=0

write_flag() {  # what is done so far is in keys_done, ids_done and tries
  local tmp="$FLAG.tmp"
  ( umask 077; printf 'hostname=%s\nkeys=%s\nids=%s\ntries=%s\n' "$name" "$keys_done" "$ids_done" "$tries" >"$tmp" ) || fail "cannot write $FLAG"
  mv "$tmp" "$FLAG" || fail "cannot write $FLAG"
}

# ------------------------------------------------------------------ 1. SSH host keys
if [[ "$keys_done" != "done" ]]; then
  if command -v ssh-keygen >/dev/null && [[ -d "$R/etc/ssh" ]]; then
    rm -f "$R"/etc/ssh/ssh_host_* || fail "cannot remove the old SSH host keys"
    keygen=(-A)
    [[ -z "$R" ]] || keygen+=(-f "$R")
    ssh-keygen "${keygen[@]}" >/dev/null || fail "could not make new SSH host keys"
    log "made new SSH host keys"
  else
    log "no SSH server on this box: no host keys to make"
  fi
  keys_done="done"
  write_flag                                         # a retry must not make them a second time
  # A server that was refused for having no keys starts with the new ones. --no-block: ssh.service is ordered after this
  # unit, so waiting for its job from in here would wait for ourselves.
  if systemctl is-enabled --quiet ssh.service 2>/dev/null; then
    systemctl --no-block restart ssh.service >/dev/null 2>&1 || log "could not restart ssh (it starts with the new keys at the next start)"
  fi
fi

# ------------------------------------------------------------------ 2. host name and the avahi name
current="$(head -n1 "$R/etc/hostname" 2>/dev/null || true)"
if [[ "$current" != "$name" ]]; then
  if ! { command -v hostnamectl >/dev/null && hostnamectl set-hostname "$name" >/dev/null 2>&1; } \
     || [[ "$(head -n1 "$R/etc/hostname" 2>/dev/null || true)" != "$name" ]]; then
    printf '%s\n' "$name" >"$R/etc/hostname" || fail "cannot set the host name"
    [[ -n "$R" ]] || hostname "$name" 2>/dev/null || true
  fi
  log "host name is now $name"
fi
# Tools such as sudo look the own name up in /etc/hosts and complain when it is not there.
hosts="$R/etc/hosts"
if [[ -f "$hosts" ]] && ! grep -qE "^127\.0\.1\.1[[:space:]]+$name([[:space:]]|\$)" "$hosts"; then
  tmp="$hosts.tmp"
  if grep -qE '^127\.0\.1\.1[[:space:]]' "$hosts"; then
    sed -E "s/^127\.0\.1\.1[[:space:]].*/127.0.1.1 $name/" "$hosts" >"$tmp"
  else
    { cat "$hosts"; echo "127.0.1.1 $name"; } >"$tmp"
  fi
  cat "$tmp" >"$hosts" && rm -f "$tmp"               # rewrite in place: /etc/hosts may be a bind mount or a link
fi
avahi_conf="$R/etc/avahi/avahi-daemon.conf"
if [[ -f "$avahi_conf" ]] && grep -qE '^[[:space:]]*host-name=' "$avahi_conf"; then
  sed -i -E "s/^[[:space:]]*host-name=.*/host-name=$name/" "$avahi_conf"     # a name pinned there would win over the host name
fi
systemctl --no-block try-restart avahi-daemon.service >/dev/null 2>&1 || true   # announce the new name now

# ------------------------------------------------------------------ 3. the counter's id
# Once only: a retry (the network was not there at the first start) must not delete the id the scheduler has made since,
# or the box would be counted twice.
if [[ "$ids_done" != "done" ]]; then
  rm -f "$STATE_DIR/install-id" || fail "cannot remove the stale install-id"
  ids_done="done"
  write_flag
fi

# ------------------------------------------------------------------ 4. Pi-hole's HTTPS key
# The seal removed the golden unit's key (it must not be on every box). FTL writes a new key and certificate when it
# starts without one and serves https (an "s" in webserver.port; with http only there is nothing to make), but it started
# before this unit and may not have written them yet. Wait for the file; if it is still not there, restart FTL once,
# which makes it write them, and wait again. (FTL runs as its own user: a key written from here could not be read by it.)
ftl_setting() {  # key: Pi-hole's own value, or nothing
  pihole-FTL --config "$1" 2>/dev/null | head -n1 | tr -d '"' || true
}
tls_cert="$(ftl_setting webserver.tls.cert)"
[[ "$tls_cert" == /* && "$tls_cert" != *..* && ( -z "$R" || "$tls_cert" == "$R"/* ) ]] || tls_cert="$R/etc/pihole/tls.pem"
tls_ready() {  # a key and a certificate in the file
  [[ -s "$tls_cert" ]] && grep -q 'PRIVATE KEY' "$tls_cert" 2>/dev/null && grep -q 'BEGIN CERTIFICATE' "$tls_cert" 2>/dev/null
}
tls_wait() {  # seconds: 0 when the key is there, once it is (or is not) there after that long
  local waited=0
  while ! tls_ready; do
    (( waited >= $1 )) && return 1
    sleep 1; waited=$(( waited + 1 ))
  done
}
if [[ "$(ftl_setting webserver.port)" != *s* ]]; then
  log "Pi-hole serves no https: no HTTPS key to make"
elif tls_wait "$TLS_WAIT_FIRST"; then
  log "Pi-hole has its own HTTPS key"
else
  log "Pi-hole has not written an HTTPS key: restarting it so that it makes one"
  systemctl restart pihole-FTL.service >/dev/null 2>&1 || log "could not restart Pi-hole's FTL"
  if tls_wait "$TLS_WAIT_AFTER"; then
    log "Pi-hole made its own HTTPS key"
  else
    log "Pi-hole still has no HTTPS key: it is looked at again at the next start (everything else is done)"
    exit 0
  fi
fi

# ------------------------------------------------------------------ 5. the address on this network
default_iface() { ip -4 route show default 2>/dev/null | awk '{for (i=1;i<NF;i++) if ($i=="dev") {print $(i+1); exit}}'; }
current_ipv4() {
  local iface line addr
  iface="$(default_iface)"
  [[ -n "$iface" ]] || return 1
  line="$(ip -4 -o addr show dev "$iface" scope global 2>/dev/null | head -n1)"
  addr="$(awk '{print $4}' <<<"$line")"
  addr="${addr%/*}"
  [[ "$addr" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]] || return 1
  printf '%s' "$addr"
}

finish() {
  rm -f "$FLAG" || fail "cannot remove the first-start flag"
  systemctl disable sinko-firstboot.service >/dev/null 2>&1 || true
  log "first start finished"
  exit 0
}
# No address now. Starts 1 and 2: stay armed and try again at the next start. The third: the scheduler's address watch sets
# the address as soon as there is one (it compares the default route's address with SINKO_IP every five minutes), and
# an armed flag would only hold back SSH and the scheduler at every start from now on.
address_not_set() {  # what happened
  tries=$(( tries + 1 ))
  if (( tries >= MAX_TRIES )); then
    log "$1: the address is left to the scheduler's address watch (it sets it as soon as the network is there)"
    finish
  fi
  write_flag
  log "$1: the address is set at the next start (everything else is done)"
  exit 0
}

wait_limit="$WAIT"
if (( tries > 0 && RETRY_WAIT < WAIT )); then wait_limit="$RETRY_WAIT"; fi
ip_now=""
waited=0
while ! ip_now="$(current_ipv4)"; do
  if (( waited >= wait_limit )); then
    address_not_set "the network is not up yet"
  fi
  sleep 1; waited=$(( waited + 1 ))
done

# The name the page is reached by (family.lan) is Pi-hole's local name for the box, not the host name above.
saved_name="$(
  set +u
  unset SINKO_HOSTNAME
  # shellcheck disable=SC1090
  . "$CONF_FILE" >/dev/null 2>&1
  if [[ -n "${SINKO_HOSTNAME+x}" ]]; then printf 'set:%s' "$SINKO_HOSTNAME"; fi
)"
configure=(configure --ip "$ip_now")
[[ "$saved_name" != set:* ]] || configure+=(--hostname "${saved_name#set:}")
if ! out="$("$BIN" "${configure[@]}" 2>&1)"; then
  address_not_set "Pi-hole did not accept the new address yet (it said: $(tail -n 3 <<<"$out" | tr '\n' ' '))"
fi
log "this box is at $ip_now on this network"

# SINKO_IP in the settings follows (the scheduler's address watch compares against it).
if [[ -f "$CONF_FILE" ]]; then
  tmp="$CONF_FILE.tmp"
  if grep -q '^SINKO_IP=' "$CONF_FILE"; then
    sed "s|^SINKO_IP=.*|SINKO_IP=$ip_now|" "$CONF_FILE" >"$tmp"
  else
    { cat "$CONF_FILE"; echo "SINKO_IP=$ip_now"; } >"$tmp"
  fi
  chmod 644 "$tmp" || fail "cannot update SINKO_IP in $CONF_FILE"
  mv "$tmp" "$CONF_FILE" || fail "cannot update SINKO_IP in $CONF_FILE"
fi

# ------------------------------------------------------------------ done
finish
