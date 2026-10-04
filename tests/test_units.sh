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
                ProtectProc ProcSubset PrivateNetwork IPAddressDeny UMask; do
  [[ -z "$(active "$blocking")" ]] || fail "$blocking is set: it can stop systemd-run, pihole-FTL, the installer fallback or the writes to /etc/sinko and /var/lib/sinko"
  grep -q "^#.*$blocking" "$svc" || fail "$blocking is not set, and the unit does not say why"
done
echo "units tests passed"
