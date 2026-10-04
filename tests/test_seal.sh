#!/usr/bin/env bash
# tools/seal.sh on a fake "golden unit": a fake root with Sinko installed (by the real installer), the mock Pi-hole holding
# a family's objects, and the files a used Armbian box has (SSH keys, history, logs, machine id). Nothing here touches the
# real system: every system command is a stub and every path is under the fake root.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=tests/lib_stubs.sh
. "$HERE/lib_stubs.sh"
PORT="${PORT:-18083}"
fake_system_init
NEW_LISTS="https://raw.githubusercontent.com/iret33/sinko/master/lists"
SEAL="$ROOT/opt/sinko/tools/seal.sh"
cat >"$WORK/os-release" <<'EOF'
PRETTY_NAME="Armbian 25.8 trixie"
ID=debian
EOF
export SINKO_OS_RELEASE="$WORK/os-release"

world_digest() {  # every file (name, size, content) and link under the fake root
  ( cd "$ROOT" && {
      find . \( -type f -o -type l -o -type d \) -printf '%p %y %s %l\n' | LC_ALL=C sort
      find . -type f -print0 | LC_ALL=C sort -z | xargs -0 -r sha256sum
    } | sha256sum )
}
api_digest() { { mock_api GET /api/groups; mock_api GET /api/clients; mock_api GET /api/lists; mock_api GET /api/domains; } | sha256sum; }
plan_lines() { grep -E '^ +[0-9]+\. ' "$1"; }

# A box that has been used: Sinko installed and set up, children's devices, and everything a seal has to find.
make_golden() {
  fresh_start
  mkdir -p "$ROOT/etc/ssh/sshd_config.d" "$ROOT/root/.ssh" "$ROOT/home/pi/.ssh" "$ROOT/var/lib/dbus" "$ROOT/var/lib/systemd" \
           "$ROOT/var/lib/dhcp" "$ROOT/var/log/pihole" "$ROOT/var/log/journal/0123abcd" "$ROOT/dev"
  touch "$ROOT/dev/watchdog"
  echo golden-opi >"$ROOT/etc/hostname"
  printf '127.0.0.1 localhost\n127.0.1.1 golden-opi\n' >"$ROOT/etc/hosts"
  SINKO_TELEMETRY=1 bash "$REPO/install.sh" >"$WORK/golden-install.out" 2>&1 || { cat "$WORK/golden-install.out"; fail "the golden unit could not be installed"; }
  seed_pihole_objects "$NEW_LISTS"
  printf 'Include /etc/ssh/sshd_config.d/*.conf\nPermitRootLogin yes\nPasswordAuthentication yes\n' >"$ROOT/etc/ssh/sshd_config"
  local t
  for t in rsa ecdsa ed25519; do
    echo "golden private key $t" >"$ROOT/etc/ssh/ssh_host_${t}_key"; echo "golden public key $t" >"$ROOT/etc/ssh/ssh_host_${t}_key.pub"
  done
  echo 0123456789abcdef0123456789abcdef >"$ROOT/etc/machine-id"
  cp "$ROOT/etc/machine-id" "$ROOT/var/lib/dbus/machine-id"
  echo "ssh-ed25519 AAAA seller@laptop" >"$ROOT/root/.ssh/authorized_keys"; echo "seller-network-host ssh-ed25519 AAAA" >"$ROOT/root/.ssh/known_hosts"
  echo "ssh-ed25519 BBBB helper" >"$ROOT/home/pi/.ssh/authorized_keys"
  echo "sudo pihole setpassword hunter2" >"$ROOT/root/.bash_history"; echo "print(1)" >"$ROOT/root/.python_history"
  echo "ls" >"$ROOT/home/pi/.bash_history"
  echo "1" >"$ROOT/root/.not_logged_in_yet"
  echo seed >"$ROOT/var/lib/systemd/random-seed"; echo "lease { fixed-address 192.168.1.50; }" >"$ROOT/var/lib/dhcp/dhclient.leases"
  echo "generated-password" >"$ROOT/etc/sinko/initial-password"
  printf '%s\n' 0123456789abcdef0123456789abcdef >"$ROOT/var/lib/sinko/install-id"
  echo '{"update": 5, "check": 6, "power": 7}' >"$ROOT/var/lib/sinko/handled.json"
  echo '{"status": "ok"}' >"$ROOT/var/lib/sinko/update-result.json"
  echo "rollback copy" >"$ROOT/var/lib/sinko/cache/sinko-1.0.0.tar.gz"
  local f
  for f in pihole-FTL.db pihole-FTL.db-wal pihole-FTL.db-shm gravity.db; do echo "database $f" >"$ROOT/etc/pihole/$f"; done
  echo "syslog lines" >"$ROOT/var/log/syslog"; echo "older" >"$ROOT/var/log/syslog.1"; echo gz >"$ROOT/var/log/auth.log.2.gz"
  echo "queries" >"$ROOT/var/log/pihole/pihole.log"; echo "ftl" >"$ROOT/var/log/pihole/FTL.log"; echo "login records" >"$ROOT/var/log/wtmp"
  echo active >"$ROOT/var/log/journal/0123abcd/system.journal"; echo archived >"$ROOT/var/log/journal/0123abcd/system@0000000000000001-0000000000000002.journal"
  echo "install log" >"$ROOT/var/log/sinko-install.log"
  : >"$WORK/calls.log"
}

echo "--- it refuses when Sinko or Pi-hole is missing"
fresh_start
if bash "$REPO/tools/seal.sh" --yes >"$WORK/refuse1.out" 2>&1; then fail "sealed a box without Sinko"; fi
grep -q "Sinko is not installed" "$WORK/refuse1.out" || { cat "$WORK/refuse1.out"; fail "no message about the missing Sinko"; }
make_golden
mkdir -p "$WORK/stubs-noftl"; cp "$STUBS"/* "$WORK/stubs-noftl/"; rm "$WORK/stubs-noftl/pihole-FTL"
if env PATH="$WORK/stubs-noftl:${PATH#"$STUBS:"}" bash "$SEAL" --yes >"$WORK/refuse2.out" 2>&1; then fail "sealed a box without Pi-hole"; fi
grep -q "Pi-hole v6 is not installed" "$WORK/refuse2.out" || { cat "$WORK/refuse2.out"; fail "no message about the missing Pi-hole"; }
mv "$ROOT/etc/pihole/pihole.toml" "$WORK/toml.bak"
if bash "$SEAL" --yes >"$WORK/refuse3.out" 2>&1; then fail "sealed a box whose Pi-hole is not v6"; fi
grep -q "Pi-hole v6 is not installed" "$WORK/refuse3.out" || fail "no message about Pi-hole without pihole.toml"
mv "$WORK/toml.bak" "$ROOT/etc/pihole/pihole.toml"
mv "$ROOT/etc/systemd/system/sinko-firstboot.service" "$WORK/unit.bak"
if bash "$SEAL" --yes >"$WORK/refuse4.out" 2>&1; then fail "sealed without the first-start unit"; fi
grep -q "no first-start service" "$WORK/refuse4.out" || fail "no message about the missing first-start service"
mv "$WORK/unit.bak" "$ROOT/etc/systemd/system/sinko-firstboot.service"
[[ -x "$ROOT/opt/sinko/tools/seal.sh" && -x "$ROOT/opt/sinko/tools/firstboot.sh" ]] || fail "the installer did not put the image tools into /opt/sinko/tools"
[[ -f "$ROOT/etc/systemd/system/sinko-firstboot.service" && ! -e "$WORK/units/sinko-firstboot.service.enabled" ]] || fail "the first-start unit must be installed but not enabled"
if bash "$SEAL" --bogus >"$WORK/usage.out" 2>&1; then fail "an unknown option was accepted"; fi
grep -q "unknown option" "$WORK/usage.out" || fail "no message about an unknown option"

echo "--- bad host names are refused before anything happens"
for bad in "-x" "a_b" "a.b" "" "ünï" "$(printf 'a%.0s' $(seq 64))" "two words"; do
  if bash "$SEAL" --yes --hostname "$bad" >"$WORK/badname.out" 2>&1; then fail "host name '$bad' accepted"; fi
  grep -q "not a usable host name" "$WORK/badname.out" || fail "no message for host name '$bad'"
done
[[ -e "$ROOT/etc/ssh/ssh_host_ed25519_key" ]] || fail "a refused run removed keys"

echo "--- a dry run shows everything and changes nothing"
world_before="$(world_digest)"; api_before="$(api_digest)"
bash "$SEAL" --dry-run >"$WORK/dry.out" 2>&1 || { cat "$WORK/dry.out"; fail "dry run failed"; }
for shown in "Sara, Omar" "pihole-FTL.db" "ssh_host_ed25519_key" "$ROOT/root/.bash_history" "$ROOT/home/pi/.bash_history" \
             "$ROOT/root/.ssh/authorized_keys" "$ROOT/root/.not_logged_in_yet" "/etc/machine-id" "install-id" "initial-password" \
             "Set the host name to sinko" "hardware watchdog" "anonymous-counter" "welcome screen" "random-seed"; do
  grep -qF -- "$shown" "$WORK/dry.out" || { cat "$WORK/dry.out"; fail "the plan does not show: $shown"; }
done
grep -q "Dry run: nothing was changed" "$WORK/dry.out" || fail "the dry run does not say it changed nothing"
[[ "$(world_digest)" == "$world_before" ]] || fail "the dry run changed files"
[[ "$(api_digest)" == "$api_before" ]] || fail "the dry run changed Pi-hole"
grep -qE 'systemctl (stop|enable|disable|start|daemon-reload)|passwd|ssh-keygen|journalctl|apt-get|dd ' "$WORK/calls.log" && fail "the dry run ran a command that changes the system"
[[ -e "$WORK/units/sinko.service.active" && -e "$WORK/units/pihole-FTL.service.active" ]] || fail "the dry run stopped a service"

echo "--- without a terminal and without --yes it will not go on; a wrong answer changes nothing"
if SINKO_TTY="$WORK/no-such-dir/tty" bash "$SEAL" >"$WORK/noterm.out" 2>&1; then fail "sealed without a terminal and without --yes"; fi
grep -q "no terminal" "$WORK/noterm.out" || fail "no message about the missing terminal"
for wrong in seal yes y "" "SEAL please"; do
  printf '%s\n' "$wrong" >"$WORK/answer"
  if SINKO_TTY="$WORK/answer" bash "$SEAL" >"$WORK/wrong.out" 2>&1; then fail "answer '$wrong' was accepted"; fi
  grep -q "Nothing was changed" "$WORK/wrong.out" || fail "no confirmation for answer '$wrong'"
done
[[ "$(world_digest)" == "$world_before" && "$(api_digest)" == "$api_before" ]] || fail "a refused seal changed something"

echo "--- sealing: typed SEAL"
echo SEAL >"$WORK/answer"
SINKO_TTY="$WORK/answer" bash "$SEAL" >"$WORK/seal.out" 2>&1 || { cat "$WORK/seal.out"; fail "sealing failed"; }
grep -q "Type SEAL" "$WORK/seal.out" || fail "SEAL was not asked for"
grep -q "Sealed. Power the box off now" "$WORK/seal.out" || fail "no closing instruction"
diff <(plan_lines "$WORK/dry.out") <(plan_lines "$WORK/seal.out") >/dev/null || { diff <(plan_lines "$WORK/dry.out") <(plan_lines "$WORK/seal.out") || true; fail "the real run did not do what the dry run announced"; }
grep -q "hunter2\|generated-password" "$WORK/seal.out" && fail "a secret was printed"

echo "    Sinko's own state is reset, the user's own Pi-hole objects are not touched"
[[ ! -e "$WORK/units/sinko.service.active" && -e "$WORK/units/sinko.service.enabled" ]] || fail "the scheduler must be stopped but stay enabled"
grep -q '^SINKO_TELEMETRY=' "$ROOT/etc/sinko/config" && fail "the counter answer survived"
grep -q '^SINKO_HOSTNAME=' "$ROOT/etc/sinko/config" || fail "the host name setting was lost"
grep -q '^SINKO_REPO_SLUG=' "$ROOT/etc/sinko/config" || fail "the other settings were damaged"
mock_api GET /api/clients | json_get 'len([c for c in d["clients"] if c["client"].startswith("aa:")])' | grep -qx 0 || fail "children's devices are still registered"
mock_api GET /api/groups | json_get 'sorted(g["name"] for g in d["groups"] if g["name"].startswith("pb-"))' | grep -q "pb-kids" || fail "Sinko's groups were not created again"
mock_api GET /api/groups | json_get 'json.loads([g["comment"] for g in d["groups"] if g["name"] == "pb-state"][0])["schedule"]["enabled"]' | grep -qx False || fail "the bedtime was not reset"
mock_api GET /api/groups | json_get 'any(g["enabled"] for g in d["groups"] if g["name"].startswith("pb-svc-"))' | grep -qx False || fail "an app is still blocked"
mock_api GET /api/lists | json_get 'len([l for l in d["lists"] if (l["comment"] or "").startswith("pb:")])' | grep -qx 20 || fail "the block lists were not registered again"
assert_user_objects_intact
mock_api GET /api/clients | json_get '[c["groups"] for c in d["clients"] if c["client"] == "192.168.1.98"][0]' | grep -qx '\[0\]' || fail "a grown-up's device kept a Sinko group"
echo "    no parent password; Pi-hole stopped and its query history gone, the rest of Pi-hole kept"
curl -s "http://127.0.0.1:$PORT/api/auth" | grep -q '"valid": *true' || fail "Pi-hole still wants a password"
grep -q 'pihole-FTL --config webserver.api.password <empty>' "$WORK/calls.log" || fail "the password was not cleared through FTL's setting"
[[ ! -e "$WORK/units/pihole-FTL.service.active" ]] || fail "Pi-hole's FTL was left running"
[[ ! -e "$ROOT/etc/pihole/pihole-FTL.db" && ! -e "$ROOT/etc/pihole/pihole-FTL.db-wal" && ! -e "$ROOT/etc/pihole/pihole-FTL.db-shm" ]] || fail "the query history is still there"
[[ -f "$ROOT/etc/pihole/gravity.db" && -f "$ROOT/etc/pihole/pihole.toml" ]] || fail "Pi-hole's own files were removed"
echo "    SSH: root locked, password logins off, the seller's keys gone, host keys gone"
grep -q "passwd -R $ROOT -l root" "$WORK/calls.log" || fail "the root password was not locked"
grep -q '^PasswordAuthentication no$' "$ROOT/etc/ssh/sshd_config.d/00-sinko-lockdown.conf" || fail "no sshd drop-in"
grep -q '^PermitRootLogin prohibit-password$' "$ROOT/etc/ssh/sshd_config.d/00-sinko-lockdown.conf" || fail "root login is not restricted"
[[ ! -e "$ROOT/root/.ssh/authorized_keys" && ! -e "$ROOT/root/.ssh/known_hosts" && ! -e "$ROOT/home/pi/.ssh/authorized_keys" ]] || fail "SSH keys were left in the image"
compgen -G "$ROOT/etc/ssh/ssh_host_*" >/dev/null && fail "SSH host keys are still there"
echo "    name, watchdog, first-start service"
[[ "$(cat "$ROOT/etc/hostname")" == sinko ]] || fail "host name is not sinko"
grep -q '^127.0.1.1 sinko$' "$ROOT/etc/hosts" || fail "/etc/hosts does not have the new name"
grep -q golden-opi "$ROOT/etc/hosts" && fail "/etc/hosts still has the old name"
grep -q '^127.0.0.1 localhost$' "$ROOT/etc/hosts" || fail "/etc/hosts lost its localhost line"
grep -q '^RuntimeWatchdogSec=' "$ROOT/etc/systemd/system.conf.d/90-sinko-watchdog.conf" || fail "no watchdog drop-in although /dev/watchdog exists"
[[ "$(cat "$ROOT/var/lib/sinko/firstboot")" == "hostname=sinko" && "$(stat -c %a "$ROOT/var/lib/sinko/firstboot")" == 600 ]] || fail "the first-start flag is wrong"
[[ -e "$WORK/units/sinko-firstboot.service.enabled" ]] || fail "the first-start service is not enabled"
echo "    what is unique or private is gone; what is needed stays"
for gone in "$ROOT/var/lib/sinko/install-id" "$ROOT/var/lib/sinko/handled.json" "$ROOT/var/lib/sinko/update-result.json" "$ROOT/etc/sinko/initial-password" \
            "$ROOT/root/.bash_history" "$ROOT/root/.python_history" "$ROOT/home/pi/.bash_history" "$ROOT/root/.not_logged_in_yet" \
            "$ROOT/var/lib/systemd/random-seed" "$ROOT/var/lib/dhcp/dhclient.leases"; do
  [[ ! -e "$gone" ]] || fail "$gone is still there"
done
[[ -f "$ROOT/var/lib/sinko/cache/sinko-1.0.0.tar.gz" ]] || fail "the rollback copy was removed"
[[ -f "$ROOT/etc/machine-id" && ! -s "$ROOT/etc/machine-id" ]] || fail "the machine id must exist and be empty"
[[ -L "$ROOT/var/lib/dbus/machine-id" && "$(readlink "$ROOT/var/lib/dbus/machine-id")" == ../../../etc/machine-id ]] || fail "/var/lib/dbus/machine-id still holds a copy"
for emptied in var/log/syslog var/log/wtmp var/log/pihole/pihole.log var/log/pihole/FTL.log var/log/sinko-install.log; do
  [[ -f "$ROOT/$emptied" && ! -s "$ROOT/$emptied" ]] || fail "$emptied must stay as an empty file"
done
[[ ! -e "$ROOT/var/log/syslog.1" && ! -e "$ROOT/var/log/auth.log.2.gz" ]] || fail "rotated logs were left"
[[ -d "$ROOT/var/log/pihole" ]] || fail "Pi-hole's log folder was removed"
[[ ! -e "$ROOT/var/log/journal/0123abcd/system@0000000000000001-0000000000000002.journal" ]] || fail "an archived journal was left"
grep -q 'journalctl --rotate' "$WORK/calls.log" || fail "the journal was not rotated"
grep -q 'journalctl --vacuum' "$WORK/calls.log" || fail "the journal was not vacuumed"
grep -q 'dd if=/dev/zero' "$WORK/calls.log" && fail "zeros were written without --zerofill"

echo "--- sealing again changes nothing more"
world_after="$(world_digest)"
mock_api GET /api/groups | json_get 'sorted(g["name"] for g in d["groups"])' >"$WORK/groups1.txt"
bash "$SEAL" --yes >"$WORK/seal2.out" 2>&1 || { cat "$WORK/seal2.out"; fail "sealing a second time failed"; }
[[ "$(world_digest)" == "$world_after" ]] || fail "a second seal changed files"
[[ "$(mock_api GET /api/groups | json_get 'sorted(g["name"] for g in d["groups"])')" == "$(cat "$WORK/groups1.txt")" ]] || fail "a second seal changed the groups"
[[ ! -e "$WORK/units/pihole-FTL.service.active" ]] || fail "a second seal left FTL running"

echo "--- --zerofill writes zeros under the fake root only, then deletes them"
make_golden
bash "$SEAL" --yes --zerofill >"$WORK/zero.out" 2>&1 || { cat "$WORK/zero.out"; fail "sealing with --zerofill failed"; }
grep -q "^dd if=/dev/zero of=$ROOT/.sinko-zerofill " "$WORK/calls.log" || { cat "$WORK/calls.log"; fail "zerofill did not run on the fake root"; }
[[ ! -e "$ROOT/.sinko-zerofill" ]] || fail "the zero file was left behind"
grep -q "^sync" "$WORK/calls.log" || fail "no sync after the zeros"

echo "--- --keep-ssh-access and --hostname; no watchdog device, no watchdog setting"
make_golden
rm -f "$ROOT/dev/watchdog"
bash "$SEAL" --yes --keep-ssh-access --hostname Box-7 >"$WORK/keep.out" 2>&1 || { cat "$WORK/keep.out"; fail "sealing with --keep-ssh-access failed"; }
grep -q "^passwd" "$WORK/calls.log" && fail "root's password was touched although --keep-ssh-access"
[[ ! -e "$ROOT/etc/ssh/sshd_config.d/00-sinko-lockdown.conf" ]] || fail "SSH settings were changed although --keep-ssh-access"
[[ -f "$ROOT/root/.ssh/authorized_keys" && -f "$ROOT/home/pi/.ssh/authorized_keys" ]] || fail "authorized keys were removed although --keep-ssh-access"
compgen -G "$ROOT/etc/ssh/ssh_host_*" >/dev/null && fail "host keys must go even with --keep-ssh-access"
[[ "$(cat "$ROOT/etc/hostname")" == box-7 && "$(cat "$ROOT/var/lib/sinko/firstboot")" == "hostname=box-7" ]] || fail "--hostname was not applied in lower case"
[[ ! -e "$ROOT/etc/systemd/system.conf.d/90-sinko-watchdog.conf" ]] || fail "a watchdog was configured without /dev/watchdog"
grep -q "skipped, there is no /dev/watchdog" "$WORK/keep.out" || fail "the plan does not say the watchdog was skipped"

echo "--- if the parent password cannot be removed nothing is armed and no key is deleted"
make_golden
touch "$WORK/password-stuck"
if bash "$SEAL" --yes >"$WORK/stuck.out" 2>&1; then fail "sealed although the password could not be removed"; fi
grep -q "parent password could not be removed" "$WORK/stuck.out" || { cat "$WORK/stuck.out"; fail "no message about the password"; }
[[ ! -e "$ROOT/var/lib/sinko/firstboot" && ! -e "$WORK/units/sinko-firstboot.service.enabled" ]] || fail "the first start was armed after a failure"
[[ -e "$ROOT/etc/ssh/ssh_host_ed25519_key" && -e "$ROOT/etc/pihole/pihole-FTL.db" ]] || fail "something was deleted after the failure"
echo "    the stored hash is cleared when FTL's password setting does not do it"
make_golden
touch "$WORK/password-needs-pwhash"
bash "$SEAL" --yes >"$WORK/pwhash.out" 2>&1 || { cat "$WORK/pwhash.out"; fail "the pwhash fallback did not work"; }
grep -q 'webserver.api.pwhash <empty>' "$WORK/calls.log" || fail "the stored hash was not cleared"
echo "--- if SSH password logins stay on, nothing is armed"
make_golden
touch "$WORK/sshd-open"
if bash "$SEAL" --yes >"$WORK/open.out" 2>&1; then fail "sealed although SSH password logins are still on"; fi
grep -q "SSH password logins are still on" "$WORK/open.out" || { cat "$WORK/open.out"; fail "no message about SSH"; }
[[ ! -e "$ROOT/var/lib/sinko/firstboot" && -e "$ROOT/etc/ssh/ssh_host_ed25519_key" ]] || fail "armed or deleted keys after the SSH check failed"

echo "seal tests passed"
