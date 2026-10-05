#!/usr/bin/env bash
# tools/firstboot.sh on a fake root, the way a flashed ready-made box runs it: Sinko installed, a flag file left by
# the seal, the golden unit's SSH keys and address still in place, and a different network around it now.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=tests/lib_stubs.sh disable=SC1091
. "$HERE/lib_stubs.sh"
PORT="${PORT:-18084}"
fake_system_init
FIRSTBOOT="$ROOT/opt/sinko/tools/firstboot.sh"
export SINKO_FIRSTBOOT_WAIT=0                       # do not wait for an address in the tests
export SINKO_FIRSTBOOT_TLS_WAIT=0                   # ... nor for Pi-hole's HTTPS key
cat >"$WORK/os-release" <<'EOF'
PRETTY_NAME="Armbian 25.8 trixie"
ID=debian
EOF
export SINKO_OS_RELEASE="$WORK/os-release"

run_firstboot() { bash "$FIRSTBOOT" >"$WORK/$1.out" 2>&1; }
keys_digest() { cat "$ROOT"/etc/ssh/ssh_host_* 2>/dev/null | sha256sum; }

# A box as it comes out of the image: installed on the seller's network (192.168.1.50), then sealed, now on 10.0.0.77.
make_box() {
  fresh_start
  mkdir -p "$ROOT/etc/ssh" "$ROOT/etc/avahi"
  echo golden-opi >"$ROOT/etc/hostname"
  printf '127.0.0.1 localhost\n127.0.1.1 golden-opi\n' >"$ROOT/etc/hosts"
  SINKO_HOSTNAME="${BOX_LOCAL_NAME:-family.lan}" bash "$REPO/install.sh" >"$WORK/box-install.out" 2>&1 || { cat "$WORK/box-install.out"; fail "the box could not be installed"; }
  local t
  for t in rsa ecdsa ed25519; do
    echo "golden private key $t" >"$ROOT/etc/ssh/ssh_host_${t}_key"; echo "golden public key $t" >"$ROOT/etc/ssh/ssh_host_${t}_key.pub"
  done
  printf '%s\n' 0123456789abcdef0123456789abcdef >"$ROOT/var/lib/sinko/install-id"
  ( umask 077; printf 'hostname=sinko\n' >"$ROOT/var/lib/sinko/firstboot" )
  touch "$WORK/units/sinko-firstboot.service.enabled" "$WORK/units/ssh.service.enabled"
  # The seal removed the golden unit's HTTPS key; Pi-hole's FTL made a new one when it started (before this unit runs).
  printf -- '-----BEGIN EC PRIVATE KEY-----\nnew\n-----END EC PRIVATE KEY-----\n-----BEGIN CERTIFICATE-----\nnew\n-----END CERTIFICATE-----\n' >"$ROOT/etc/pihole/tls.pem"
  echo 10.0.0.77 >"$WORK/ipaddr"
  : >"$WORK/calls.log"
}
world_digest() { ( cd "$ROOT" && find . -type f -print0 | LC_ALL=C sort -z | xargs -0 -r sha256sum | sha256sum ); }

echo "--- a box that has been set up already does nothing"
make_box
rm "$ROOT/var/lib/sinko/firstboot"
before="$(world_digest)"
run_firstboot nothing || { cat "$WORK/nothing.out"; fail "firstboot failed without a flag"; }
grep -q "nothing to do" "$WORK/nothing.out" || fail "no message that there was nothing to do"
[[ "$(world_digest)" == "$before" ]] || fail "firstboot changed something without the flag"
grep -q "ssh-keygen\|hostnamectl" "$WORK/calls.log" && fail "firstboot ran commands without the flag"

echo "--- the first start: keys, name, address, counter id; the flag goes only at the end"
make_box
golden_keys="$(keys_digest)"
grep -q '^SINKO_IP=192.168.1.50$' "$ROOT/etc/sinko/config" || fail "test setup: the golden address is not saved"
grep -q '192.168.1.50 family.lan' "$WORK/ftl.json" || fail "test setup: the local name does not point at the golden address"
sed '/^SINKO_IP=/d' "$ROOT/etc/sinko/config" >"$WORK/config.before"
run_firstboot first || { cat "$WORK/first.out"; fail "the first start failed"; }
[[ "$(keys_digest)" != "$golden_keys" ]] || fail "the SSH host keys are still the golden unit's"
for t in rsa ecdsa ed25519; do
  [[ -s "$ROOT/etc/ssh/ssh_host_${t}_key" && -s "$ROOT/etc/ssh/ssh_host_${t}_key.pub" ]] || fail "no new $t host key"
  grep -q "golden" "$ROOT/etc/ssh/ssh_host_${t}_key" && fail "the $t host key is the golden one"
done
grep -q "ssh-keygen -A -f $ROOT" "$WORK/calls.log" || fail "ssh-keygen was not asked for the missing keys under the root"
grep -q "systemctl --no-block restart ssh.service" "$WORK/calls.log" || fail "ssh was not told to use the new keys (or the call could block on its own ordering)"
[[ "$(cat "$ROOT/etc/hostname")" == sinko ]] || fail "the host name is not sinko"
grep -q '^127.0.1.1 sinko$' "$ROOT/etc/hosts" || fail "/etc/hosts has no entry for the new name"
grep -q golden-opi "$ROOT/etc/hosts" && fail "/etc/hosts still names the golden unit"
grep -q "systemctl --no-block try-restart avahi-daemon.service" "$WORK/calls.log" || fail "avahi was not asked to announce the new name"
[[ ! -e "$ROOT/var/lib/sinko/install-id" ]] || fail "a stale install-id is left"
grep -q '10.0.0.77 family.lan' "$WORK/ftl.json" || { cat "$WORK/ftl.json"; fail "the local name does not point at this network's address"; }
grep -q '192.168.1.50' "$WORK/ftl.json" && fail "the golden address is still in Pi-hole's local names"
grep -q '192.168.1.9 nas.lan' "$WORK/ftl.json" || fail "a local name that is not ours was removed"
grep -q '^SINKO_IP=10.0.0.77$' "$ROOT/etc/sinko/config" || fail "SINKO_IP was not updated"
diff <(grep -v '^SINKO_IP=' "$ROOT/etc/sinko/config") "$WORK/config.before" >/dev/null || fail "firstboot changed other settings"
[[ "$(stat -c %a "$ROOT/etc/sinko/config")" == 644 ]] || fail "the settings file lost its mode"
[[ ! -e "$ROOT/var/lib/sinko/firstboot" ]] || fail "the flag is still there after a complete first start"
[[ ! -e "$WORK/units/sinko-firstboot.service.enabled" ]] || fail "the first-start service was not disabled"
grep -q "restart pihole-FTL" "$WORK/calls.log" && fail "Pi-hole was restarted although its HTTPS key was already there (a pointless break of the home's DNS)"
echo "--- and when it runs again it does nothing"
after_first="$(world_digest)"
run_firstboot again || fail "a second run failed"
grep -q "nothing to do" "$WORK/again.out" || fail "the second run did not say there was nothing to do"
[[ "$(world_digest)" == "$after_first" ]] || fail "the second run changed something"

echo "--- no network yet: everything local is done, the flag stays, the exit is clean, nothing is done twice"
make_box
touch "$WORK/net-down"
run_firstboot down1 || { cat "$WORK/down1.out"; fail "firstboot failed without a network (it must end well and retry at the next start)"; }
grep -q "network is not up yet" "$WORK/down1.out" || fail "no message about the network"
[[ -f "$ROOT/var/lib/sinko/firstboot" ]] || fail "the flag was removed before the address was set"
grep -q '^keys=done$' "$ROOT/var/lib/sinko/firstboot" || fail "the flag does not remember that the keys were made"
grep -q '^hostname=sinko$' "$ROOT/var/lib/sinko/firstboot" || fail "the flag lost the name"
[[ "$(stat -c %a "$ROOT/var/lib/sinko/firstboot")" == 600 ]] || fail "the flag lost its mode"
[[ "$(cat "$ROOT/etc/hostname")" == sinko && ! -e "$ROOT/var/lib/sinko/install-id" ]] || fail "the local steps were not done"
grep -q '^SINKO_IP=192.168.1.50$' "$ROOT/etc/sinko/config" || fail "SINKO_IP changed without an address"
first_keys="$(keys_digest)"
grep -q '^ids=done$' "$ROOT/var/lib/sinko/firstboot" || fail "the flag does not remember that the id was dealt with"
grep -q '^tries=1$' "$ROOT/var/lib/sinko/firstboot" || fail "the flag does not count the start that went without an address"
echo "    a retry does not delete the counter's id again (the scheduler has made one since, and the parent may have said yes)"
printf '%s\n' fedcba9876543210fedcba9876543210 >"$ROOT/var/lib/sinko/install-id"
run_firstboot down2 || fail "the second start without a network failed"
[[ "$(keys_digest)" == "$first_keys" ]] || fail "the host keys were made a second time"
[[ "$(cat "$ROOT/var/lib/sinko/install-id")" == fedcba9876543210fedcba9876543210 ]] || fail "a retry deleted the counter's id: the box would be counted twice"
echo "    the cable arrives: the next start finishes"
rm "$WORK/net-down"
run_firstboot up || { cat "$WORK/up.out"; fail "the start with a network failed"; }
[[ ! -e "$ROOT/var/lib/sinko/firstboot" && "$(keys_digest)" == "$first_keys" ]] || fail "the retry did not finish cleanly, or made new keys"
[[ -f "$ROOT/var/lib/sinko/install-id" ]] || fail "the counter's id was deleted by the start that finished"
grep -q '^SINKO_IP=10.0.0.77$' "$ROOT/etc/sinko/config" || fail "SINKO_IP not updated after the retry"
[[ "$(grep -c '^ssh-keygen' "$WORK/calls.log")" == 1 ]] || fail "ssh-keygen ran more than once"

echo "--- three starts without an address: the first-start service lets go, the scheduler's address watch takes over"
make_box
touch "$WORK/net-down"
run_firstboot give1 || fail "start 1 without a network failed"
run_firstboot give2 || fail "start 2 without a network failed"
[[ -f "$ROOT/var/lib/sinko/firstboot" ]] || fail "the flag was removed after two starts without a network"
run_firstboot give3 || { cat "$WORK/give3.out"; fail "start 3 without a network failed"; }
grep -q "left to the scheduler's address watch" "$WORK/give3.out" || { cat "$WORK/give3.out"; fail "no note that the address watch sets the address"; }
[[ ! -e "$ROOT/var/lib/sinko/firstboot" && ! -e "$WORK/units/sinko-firstboot.service.enabled" ]] || fail "the service stays armed: it would hold back SSH and the scheduler at every start"
grep -q '^SINKO_IP=192.168.1.50$' "$ROOT/etc/sinko/config" || fail "SINKO_IP was changed without an address"

echo "--- Pi-hole's HTTPS key: not touched when it is there; when it is not, FTL is restarted once, and the first start only ends well when the key exists"
echo "    no key yet, and Pi-hole makes one when it is restarted"
make_box
rm "$ROOT/etc/pihole/tls.pem"
touch "$WORK/ftl-makes-cert"
run_firstboot tls1 || { cat "$WORK/tls1.out"; fail "the first start failed without an HTTPS key"; }
grep -q "systemctl restart pihole-FTL.service" "$WORK/calls.log" || fail "Pi-hole was not restarted to make its key"
[[ "$(grep -c 'systemctl restart pihole-FTL.service' "$WORK/calls.log")" == 1 ]] || fail "Pi-hole was restarted more than once"
grep -q 'PRIVATE KEY' "$ROOT/etc/pihole/tls.pem" || fail "there is no HTTPS key"
[[ ! -e "$ROOT/var/lib/sinko/firstboot" ]] || fail "the first start did not finish although the key is there now"
echo "    Pi-hole cannot make one: the flag stays (everything else is done), and the next start finishes when it can"
make_box
rm "$ROOT/etc/pihole/tls.pem"
run_firstboot tls2 || { cat "$WORK/tls2.out"; fail "a missing key must not fail the unit"; }
grep -q "still has no HTTPS key" "$WORK/tls2.out" || fail "no note that the key is missing"
[[ -f "$ROOT/var/lib/sinko/firstboot" ]] || fail "the first start declared success although Pi-hole has no HTTPS key"
grep -q '^SINKO_IP=192.168.1.50$' "$ROOT/etc/sinko/config" || fail "the address step ran before the key was there"
touch "$WORK/ftl-makes-cert"
run_firstboot tls3 || fail "the retry failed"
[[ ! -e "$ROOT/var/lib/sinko/firstboot" && -s "$ROOT/etc/pihole/tls.pem" ]] || fail "the retry did not finish with a key"
echo "    a file without a private key in it (an empty or half-written one) does not count"
make_box
echo "-----BEGIN CERTIFICATE-----" >"$ROOT/etc/pihole/tls.pem"
touch "$WORK/ftl-makes-cert"
run_firstboot tls4 || fail "start with a half-written key failed"
grep -q 'PRIVATE KEY' "$ROOT/etc/pihole/tls.pem" || fail "a certificate without a key was accepted as Pi-hole's HTTPS key"
echo "    the file Pi-hole's settings name is the one that counts, and without https there is nothing to wait for"
make_box
mkdir -p "$ROOT/etc/pihole/custom"; mv "$ROOT/etc/pihole/tls.pem" "$ROOT/etc/pihole/custom/web.pem"
pihole-FTL --config webserver.tls.cert "$ROOT/etc/pihole/custom/web.pem"
run_firstboot tls5 || fail "start with a custom key path failed"
grep -q "restart pihole-FTL" "$WORK/calls.log" && fail "Pi-hole was restarted although the key it names is there"
make_box
rm "$ROOT/etc/pihole/tls.pem"
pihole-FTL --config webserver.port 80
run_firstboot tls6 || fail "start without https failed"
grep -q "restart pihole-FTL" "$WORK/calls.log" && fail "Pi-hole was restarted for a key although it serves no https"
[[ ! -e "$ROOT/var/lib/sinko/firstboot" ]] || fail "the first start did not finish on a box without https"

echo "--- Pi-hole not ready for the address: the flag stays and the exit is clean"
make_box
touch "$WORK/ftl-fail"
run_firstboot ftl || { cat "$WORK/ftl.out"; fail "a failing configure must not fail the unit"; }
grep -q "did not accept the new address" "$WORK/ftl.out" || fail "no message about Pi-hole"
[[ -f "$ROOT/var/lib/sinko/firstboot" ]] || fail "the flag was removed although the address was not set"
grep -q '^SINKO_IP=192.168.1.50$' "$ROOT/etc/sinko/config" || fail "SINKO_IP was changed although Pi-hole did not take the address"
rm "$WORK/ftl-fail"
run_firstboot ftl2 || fail "the retry failed"
[[ ! -e "$ROOT/var/lib/sinko/firstboot" ]] || fail "the retry did not finish"

echo "--- a box without a local name for the page: the name is removed, not invented"
BOX_LOCAL_NAME=none make_box
grep -q "^SINKO_HOSTNAME=''\$" "$ROOT/etc/sinko/config" || fail "test setup: the box should have no local name"
run_firstboot noname || { cat "$WORK/noname.out"; fail "firstboot failed for a box without a local name"; }
grep -q 'family.lan' "$WORK/ftl.json" && fail "a local name appeared although the box has none"
grep -q '10.0.0.77' "$WORK/ftl.json" && fail "an address was registered for a name that does not exist"
grep -q '^SINKO_IP=10.0.0.77$' "$ROOT/etc/sinko/config" || fail "SINKO_IP not updated"

echo "--- a problem on this box is an error, and the flag stays"
make_box
touch "$WORK/keygen-fail"
if run_firstboot keygen; then fail "a failed key generation ended well"; fi
grep -q "could not make new SSH host keys" "$WORK/keygen.out" || fail "no message about the keys"
grep -q '^keys=done' "$ROOT/var/lib/sinko/firstboot" && fail "the keys were recorded as done"
[[ -f "$ROOT/var/lib/sinko/firstboot" ]] || fail "the flag was removed after a failure"
rm "$WORK/keygen-fail"
run_firstboot keygen2 || fail "the retry after the problem failed"
[[ ! -e "$ROOT/var/lib/sinko/firstboot" ]] || fail "the retry did not finish"
echo "    Sinko missing"
make_box
mv "$ROOT/opt/sinko/bin/sinko" "$WORK/sinko.bak"
if run_firstboot nosinko; then fail "ended well although Sinko is missing"; fi
grep -q "Sinko is not installed" "$WORK/nosinko.out" || fail "no message about the missing Sinko"
mv "$WORK/sinko.bak" "$ROOT/opt/sinko/bin/sinko"

echo "--- a flag with a bad name falls back to sinko; a name pinned in avahi's settings follows; a commented one is left"
make_box
printf 'hostname=Bad Name;rm\n' >"$ROOT/var/lib/sinko/firstboot"
printf '[server]\n#host-name=foo\nhost-name=golden-opi\n' >"$ROOT/etc/avahi/avahi-daemon.conf"
run_firstboot badname || { cat "$WORK/badname.out"; fail "firstboot failed with a bad name in the flag"; }
grep -q "is not valid; using sinko" "$WORK/badname.out" || fail "no note about the bad name"
[[ "$(cat "$ROOT/etc/hostname")" == sinko ]] || fail "the fallback name was not used"
grep -q '^host-name=sinko$' "$ROOT/etc/avahi/avahi-daemon.conf" || fail "the name pinned in avahi's settings was not updated"
grep -q '^#host-name=foo$' "$ROOT/etc/avahi/avahi-daemon.conf" || fail "a commented line in avahi's settings was changed"
echo "--- the name in the flag is the one that is set"
make_box
printf 'hostname=kidsbox\n' >"$ROOT/var/lib/sinko/firstboot"
run_firstboot custom || { cat "$WORK/custom.out"; fail "firstboot failed with another name"; }
[[ "$(cat "$ROOT/etc/hostname")" == kidsbox ]] || fail "the name from the flag was not used"
grep -q '^127.0.1.1 kidsbox$' "$ROOT/etc/hosts" || fail "/etc/hosts does not have the name from the flag"

echo "firstboot tests passed"
