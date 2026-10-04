#!/usr/bin/env bash
# First start of a ready-made Sinko box: make the things that must be unique to this box its own.
# Run by sinko-firstboot.service, which only starts while /var/lib/sinko/firstboot exists (tools/seal.sh puts it there).
#
#   1. new SSH host keys            (the golden unit's keys are on every card that was flashed from its image)
#   2. the host name, and with it the <name>.local that avahi announces
#   3. no stale install-id          (the anonymous counter's random number must not be shared between boxes)
#   4. the box's address for THIS network: "sinko configure --ip <address> --hostname <name>" and SINKO_IP
#
# The flag is removed only when all of it worked. Steps 1-3 need nothing from the network and are done first. When the
# network is not up yet (cable not plugged in, router slow), or Pi-hole is not ready for step 4, the script ends well and
# leaves the flag, so the next start tries again; nothing already done is done twice (the flag remembers the keys).
set -uo pipefail

R="${SINKO_ROOT:-}"                                  # test hook: act on a fake root
STATE_DIR="${SINKO_STATE_DIR:-$R/var/lib/sinko}"
FLAG="$STATE_DIR/firstboot"
CONF_FILE="$R/etc/sinko/config"
BIN="$R/opt/sinko/bin/sinko"
WAIT="${SINKO_FIRSTBOOT_WAIT:-60}"                   # seconds to wait for an address; SINKO_FIRSTBOOT_WAIT is a test hook

log() { echo "sinko-firstboot: $*"; }
# A problem on this box (not the network): visible as a failed unit, and tried again at the next start.
fail() { echo "sinko-firstboot: $*" >&2; exit 1; }

[[ "$(id -u)" -eq 0 ]] || fail "run as root"
if [[ ! -f "$FLAG" ]]; then
  log "nothing to do: this box has already been set up"
  exit 0
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

write_flag() {  # keys-state
  local tmp="$FLAG.tmp"
  ( umask 077; printf 'hostname=%s\nkeys=%s\n' "$name" "$1" >"$tmp" ) || fail "cannot write $FLAG"
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
  write_flag "done"                                  # a retry must not make them a second time
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
rm -f "$STATE_DIR/install-id" || fail "cannot remove the stale install-id"

# ------------------------------------------------------------------ 4. the address on this network
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
ip_now=""
waited=0
while ! ip_now="$(current_ipv4)"; do
  if (( waited >= WAIT )); then
    log "the network is not up yet: the address is set at the next start (everything else is done)"
    exit 0
  fi
  sleep 2; waited=$(( waited + 2 ))
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
  log "Pi-hole did not accept the new address yet: it is set at the next start. It said: $(tail -n 3 <<<"$out" | tr '\n' ' ')"
  exit 0
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
rm -f "$FLAG" || fail "cannot remove the first-start flag"
systemctl disable sinko-firstboot.service >/dev/null 2>&1 || true
log "first start finished"
exit 0
