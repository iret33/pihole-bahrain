#!/usr/bin/env bash
# The systemd units: they parse (systemd-analyze, when it is installed), and the scheduler's unit keeps the
# hardening it can have without stopping what the scheduler does (see the comments in systemd/sinko.service).
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(dirname "$HERE")"
fail() { echo "FAIL: $*" >&2; exit 1; }
UNITS="$REPO/systemd"

echo "--- every unit parses"
if command -v systemd-analyze >/dev/null; then
  WORK="$(mktemp -d -t sinko-test.XXXXXX)"
  trap 'rm -rf "$WORK"' EXIT
  for f in "$UNITS"/*.service "$UNITS"/*.timer; do
    # The programs live on the box, not here: point ExecStart at one that exists.
    sed -E 's|^(ExecStart=).*|\1/bin/true|; s|@PIHOLE@|/bin/true|' "$f" >"$WORK/$(basename "$f")"
  done
  ( cd "$WORK" && systemd-analyze verify ./*.service ./*.timer ) >"$WORK/verify.out" 2>&1 || { cat "$WORK/verify.out"; fail "systemd-analyze verify found a problem"; }
  grep -iE "unknown (key|lvalue|section)|invalid|failed" "$WORK/verify.out" && fail "systemd-analyze complained about a unit"
else
  echo "    (systemd-analyze is not installed: syntax not verified)"
fi

svc="$UNITS/sinko.service"
active() { grep -E "^$1=" "$svc" || true; }   # directives only: comment lines start with #
echo "--- sinko.service: state directory, restart policy, the hardening it already had"
[[ "$(active StateDirectory)" == "StateDirectory=sinko" ]] || fail "StateDirectory=sinko missing"
[[ "$(active StateDirectoryMode)" == "StateDirectoryMode=0700" ]] || fail "the state directory is not 0700"
[[ "$(active RuntimeDirectory)" == "RuntimeDirectory=sinko" ]] || fail "RuntimeDirectory=sinko missing (the scheduler's pulse lives in /run/sinko)"
[[ "$(active RuntimeDirectoryMode)" == "RuntimeDirectoryMode=0700" ]] || fail "the runtime directory is not 0700 (the program refuses a looser one, or changes it)"
[[ "$(active RuntimeDirectoryPreserve)" == "RuntimeDirectoryPreserve=restart" ]] || fail "the runtime directory would be deleted at every restart (the installer restarts the scheduler in an update)"
[[ "$(active Restart)" == "Restart=always" ]] || fail "the scheduler is not always restarted"
[[ "$(active RestartSec)" =~ ^RestartSec=([0-9]|[12][0-9]|30)$ ]] || fail "RestartSec is missing or longer than 30 seconds"
grep -q '^StartLimitIntervalSec=0$' "$svc" || fail "the unit can give up restarting (StartLimitIntervalSec=0 missing)"
grep -q '^ExecStart=/usr/bin/python3 /opt/sinko/bin/sinko run$' "$svc" || fail "ExecStart is not 'sinko run'"
for kept in NoNewPrivileges=yes ProtectHome=yes PrivateTmp=yes ProtectKernelTunables=yes ProtectControlGroups=yes; do
  grep -q "^$kept\$" "$svc" || fail "the existing hardening $kept was dropped"
done
echo "--- sinko.service: what the scheduler needs is not blocked"
[[ "$(active KillMode)" == "KillMode=process" ]] || fail "a restart of the scheduler would kill an update that runs as its child (KillMode=process)"
families="$(active RestrictAddressFamilies)"
for need in AF_UNIX AF_INET AF_INET6; do   # D-Bus (systemd-run, reboot), Pi-hole's API, GitHub
  [[ "$families" == *"$need"* ]] || fail "RestrictAddressFamilies lacks $need"
done
for blocking in ProtectSystem ReadWritePaths ReadOnlyPaths InaccessiblePaths PrivateDevices RestrictSUIDSGID RestrictNamespaces \
                CapabilityBoundingSet AmbientCapabilities User DynamicUser PrivateUsers SystemCallFilter MemoryDenyWriteExecute \
                ProtectProc ProcSubset PrivateNetwork IPAddressDeny UMask ProtectClock DevicePolicy DeviceAllow; do
  [[ -z "$(active "$blocking")" ]] || fail "$blocking is set: it can stop systemd-run, pihole-FTL, the installer fallback or the writes to /etc/sinko and /var/lib/sinko"
  grep -q "^#.*$blocking" "$svc" || fail "$blocking is not set, and the unit does not say why"
done
echo "--- sinko-firstboot.service: only while the flag exists, before SSH and the scheduler, after the network, a clean exit is a success"
fb="$UNITS/sinko-firstboot.service"
grep -q '^ConditionPathExists=/var/lib/sinko/firstboot$' "$fb" || fail "the first-start unit is not guarded by the flag file"
grep -q '^Type=oneshot$' "$fb" || fail "the first-start unit is not a oneshot"
grep -q '^ExecStart=/opt/sinko/tools/firstboot.sh$' "$fb" || fail "the first-start unit does not run tools/firstboot.sh"
grep -E '^Before=' "$fb" | grep -q 'ssh.service' || fail "SSH could start before its new host keys exist"
grep -E '^Before=' "$fb" | grep -q 'sinko.service' || fail "the scheduler could start (and make an install id) before firstboot"
grep -E '^Before=' "$fb" | grep -q 'ssh.socket' && fail "Before=ssh.socket makes an ordering loop with the default dependencies (basic.target waits for sockets.target); systemd then drops a job at random"
grep -E '^After=' "$fb" | grep -q 'network-online.target' || fail "the first-start unit does not wait for the network"
grep -q '^WantedBy=multi-user.target$' "$fb" || fail "the first-start unit cannot be enabled"
grep -q '^Restart=' "$fb" && fail "a restart policy would loop on a box that has no network yet"
echo "--- sinko-firstboot.service makes no ordering cycle where ssh is socket-activated (ssh.socket enabled)"
if command -v systemd-analyze >/dev/null; then
  CYC="$(mktemp -d -t sinko-test.XXXXXX)"
  trap 'rm -rf "$CYC" "${WORK:-}"' EXIT
  printf '[Unit]\nDescription=ssh socket\n[Socket]\nListenStream=22\n[Install]\nWantedBy=sockets.target\n' >"$CYC/ssh.socket"
  printf '[Unit]\nDescription=ssh\nAfter=network.target\n[Service]\nExecStart=/bin/true\n' >"$CYC/ssh.service"
  sed -E 's|^(ExecStart=).*|\1/bin/true|' "$fb" >"$CYC/real.service"
  sed -E 's|^(ExecStart=).*|\1/bin/true|; s|^(Before=.*)|\1 ssh.socket|' "$fb" >"$CYC/looping.service"
  # The units are verified together with the targets the default dependencies hang on (basic.target waits for sockets.target).
  has_cycle() {
    local out
    out="$( cd "$CYC" && systemd-analyze verify "./$1" ./ssh.socket ./ssh.service basic.target sockets.target multi-user.target 2>&1 || true )"
    grep -q "ordering cycle" <<<"$out"
  }
  if has_cycle looping.service; then
    has_cycle real.service && fail "sinko-firstboot.service makes an ordering cycle with ssh.socket"
  else
    echo "    (this systemd-analyze does not report cycles: not verified)"
  fi
else
  echo "    (systemd-analyze is not installed: ordering not verified)"
fi
echo "units tests passed"
