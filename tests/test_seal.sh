#!/usr/bin/env bash
# tools/seal.sh on a fake "golden unit": a fake root with Sinko installed (by the real installer), the mock Pi-hole holding
# a family's objects, and the files a used Armbian box has (SSH keys, history, logs, machine id). Nothing here touches the
# real system: every system command is a stub and every path is under the fake root.
# shellcheck disable=SC2016  # in this file a $ in single quotes is literal on purpose: password hashes, a getty line
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=tests/lib_stubs.sh disable=SC1091
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
  # The accounts of a Debian box: root with the seller's password, and system accounts that cannot log in (no password: "*" or "!").
  printf 'root:x:0:0:root:/root:/bin/bash\ndaemon:x:1:1:daemon:/usr/sbin:/usr/sbin/nologin\nsystemd-network:x:998:998:systemd Network Management:/:/usr/sbin/nologin\n' >"$ROOT/etc/passwd"
  printf 'root:$6$seller$rootrootrootroot:19000:0:99999:7:::\ndaemon:*:19000:0:99999:7:::\nsystemd-network:!*:19000::::::\n' >"$ROOT/etc/shadow"
  chmod 640 "$ROOT/etc/shadow"
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
  echo "older queries" >"$ROOT/var/log/pihole/pihole.log.1"; echo "oldest queries" >"$ROOT/var/log/pihole/pihole.log.2.gz"
  echo active >"$ROOT/var/log/journal/0123abcd/system.journal"; echo archived >"$ROOT/var/log/journal/0123abcd/system@0000000000000001-0000000000000002.journal"
  echo "install log" >"$ROOT/var/log/sinko-install.log"
  # Pi-hole's own leftovers: its HTTPS key (generated at its first start), earlier versions of pihole.toml (one holds the
  # seller's password hash), earlier gravity databases (with the test devices), its temporary database and installer log.
  echo "PRIVATE KEY golden" >"$ROOT/etc/pihole/tls.pem"; echo "certificate golden" >"$ROOT/etc/pihole/tls.crt"; echo "ca golden" >"$ROOT/etc/pihole/tls_ca.crt"
  mkdir -p "$ROOT/etc/pihole/config_backups" "$ROOT/etc/pihole/gravity_backups"
  echo 'pwhash = "$BALLOON-SHA256$seller-hash"' >"$ROOT/etc/pihole/config_backups/pihole.toml.1"
  echo "database old" >"$ROOT/etc/pihole/gravity_old.db"; echo "database older" >"$ROOT/etc/pihole/gravity_backups/gravity.db.2"
  # What Pi-hole keeps of a version 5 setup when it is upgraded (setupVars.conf holds its password hash).
  mkdir -p "$ROOT/etc/pihole/migration_backup_v6"; echo 'WEBPASSWORD=seller-hash' >"$ROOT/etc/pihole/migration_backup_v6/setupVars.conf"
  echo "database tmp" >"$ROOT/etc/pihole/pihole-tmp.db"; echo "pihole install log" >"$ROOT/etc/pihole/install.log"
  # A two-factor secret and an application password somebody set in Pi-hole's admin page.
  pihole-FTL --config webserver.api.totp_secret JBSWY3DPEHPK3PXP; pihole-FTL --config webserver.api.app_pwhash '$BALLOON-SHA256$app-hash'
  # Armbian's automatic root login on the console, which its first-login wizard removes (a harmless getty drop-in stays).
  mkdir -p "$ROOT/etc/systemd/system/getty@.service.d" "$ROOT/etc/systemd/system/serial-getty@.service.d" "$ROOT/etc/systemd/system/getty@tty1.service.d"
  printf '[Service]\nExecStart=\nExecStart=-/sbin/agetty --noissue --autologin root %%I $TERM\n' >"$ROOT/etc/systemd/system/getty@.service.d/override.conf"
  cp "$ROOT/etc/systemd/system/getty@.service.d/override.conf" "$ROOT/etc/systemd/system/serial-getty@.service.d/override.conf"
  printf '[Service]\nTTYVTDisallocate=no\n' >"$ROOT/etc/systemd/system/getty@tty1.service.d/keep.conf"
  # Wi-Fi networks (the wired setup must stay) and NetworkManager's secret key.
  mkdir -p "$ROOT/etc/netplan" "$ROOT/etc/NetworkManager/system-connections" "$ROOT/etc/wpa_supplicant" "$ROOT/var/lib/NetworkManager"
  printf 'network:\n  version: 2\n  ethernets:\n    all:\n      dhcp4: true\n' >"$ROOT/etc/netplan/10-dhcp-all-interfaces.yaml"
  printf 'network:\n  version: 2\n  wifis:\n    wlan0:\n      dhcp4: true\n      access-points:\n        "HomeNet":\n          password: "seller-wifi-pass"\n' >"$ROOT/etc/netplan/30-wifis-dhcp.yaml"
  printf '[connection]\nid=HomeNet\ntype=wifi\n[wifi-security]\npsk=seller-wifi-pass\n' >"$ROOT/etc/NetworkManager/system-connections/HomeNet.nmconnection"
  printf '[connection]\nid=Wired\ntype=ethernet\n' >"$ROOT/etc/NetworkManager/system-connections/Wired.nmconnection"
  printf 'ctrl_interface=/run/wpa_supplicant\nnetwork={\n  ssid="HomeNet"\n  psk="seller-wifi-pass"\n}\n' >"$ROOT/etc/wpa_supplicant/wpa_supplicant.conf"
  echo "nm-secret-key" >"$ROOT/var/lib/NetworkManager/secret_key"
  mkdir -p "$ROOT/boot"
  printf "FR_net_wifi_enabled=1\nFR_net_wifi_ssid='HomeNet'\nFR_net_wifi_key='seller-wifi-pass'\n" >"$ROOT/boot/armbian_first_run.txt.off"
  printf "FR_net_wifi_enabled=0\nFR_net_wifi_key=''\n" >"$ROOT/boot/armbian_first_run.txt.template"
  # Armbian keeps /var/log in memory and the card's own copy in /var/log.hdd: rotated logs only ever exist there.
  mkdir -p "$ROOT/var/log.hdd/pihole"
  echo "card syslog" >"$ROOT/var/log.hdd/syslog"; echo "card older" >"$ROOT/var/log.hdd/syslog.1"; echo gz >"$ROOT/var/log.hdd/auth.log.2.gz"
  echo "card queries" >"$ROOT/var/log.hdd/pihole/pihole.log"; echo "card older queries" >"$ROOT/var/log.hdd/pihole/pihole.log.1"
  echo "card oldest queries" >"$ROOT/var/log.hdd/pihole/pihole.log.2.gz"; echo "files" >"$ROOT/var/log.hdd/armbian-ramlog.log"
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
             "Set the host name to sinko" "hardware watchdog" "anonymous-counter" "welcome screen" "random-seed" \
             "tls.pem" "tls_ca.crt" "config_backups" "migration_backup_v6" "gravity_old.db" "gravity_backups" "pihole-tmp.db" "two-factor secret" "application password" \
             "getty@.service.d/override.conf" "serial-getty@.service.d/override.conf" "30-wifis-dhcp.yaml" "HomeNet.nmconnection" \
             "wpa_supplicant.conf" "secret_key" "$ROOT/var/log.hdd" "Write zeros over the free space" "Lock the root password" \
             "armbian_first_run.txt.off"; do
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
grep -q "hunter2\|generated-password\|seller-wifi-pass\|seller-hash\|JBSWY3DPEHPK3PXP" "$WORK/seal.out" && fail "a secret was printed"
echo "    the plan ends with the lock-down: nothing that can still fail comes after it"
[[ "$(plan_lines "$WORK/dry.out" | tail -n1)" == *"Lock the root password"* ]] || fail "the lock-down is not the last step of the plan"

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
echo "    Pi-hole's HTTPS key and certificates are gone (every box makes its own), FTL was stopped before and not started again"
for tls in tls.pem tls.crt tls_ca.crt; do [[ ! -e "$ROOT/etc/pihole/$tls" ]] || fail "$tls is still there: every box would serve HTTPS with the golden unit's private key"; done
echo "    the seller's old settings, older gravity databases and the temporary database are gone; the folders stay"
for residue in config_backups/pihole.toml.1 migration_backup_v6/setupVars.conf gravity_old.db gravity_backups/gravity.db.2 pihole-tmp.db install.log; do
  [[ ! -e "$ROOT/etc/pihole/$residue" ]] || fail "$residue is still there (it holds the seller's password hash or the test devices)"
done
[[ -d "$ROOT/etc/pihole/config_backups" && -d "$ROOT/etc/pihole/gravity_backups" ]] || fail "a Pi-hole folder was removed (only what is in it may go)"
echo "    no password, no two-factor secret, no application password: cleared before the web password, and read back"
grep -q 'pihole-FTL --config webserver.api.totp_secret <empty>' "$WORK/calls.log" || fail "the two-factor secret was not cleared"
grep -q 'pihole-FTL --config webserver.api.app_pwhash <empty>' "$WORK/calls.log" || fail "the application password was not cleared"
[[ -z "$(pihole-FTL --config webserver.api.totp_secret)" && -z "$(pihole-FTL --config webserver.api.app_pwhash)" ]] || fail "a secret is still stored in Pi-hole's settings"
totp_line="$(grep -n 'webserver.api.totp_secret <empty>' "$WORK/calls.log" | head -1 | cut -d: -f1)"
password_line="$(grep -n 'webserver.api.password <empty>' "$WORK/calls.log" | head -1 | cut -d: -f1)"
(( totp_line < password_line )) || fail "the web password was cleared first: the check that stops as soon as it is gone never looks at the other secrets"
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
# The Allwinner watchdog (sunxi_wdt) takes 1 to 16 seconds: a longer time is refused by the driver and the watchdog stays off.
for key in RuntimeWatchdogSec RebootWatchdogSec; do
  secs="$(sed -n "s/^$key=\([0-9]*\)$/\1/p" "$ROOT/etc/systemd/system.conf.d/90-sinko-watchdog.conf")"
    if [[ ! "$secs" =~ ^[0-9]+$ ]] || (( secs < 1 || secs > 16 )); then fail "$key is '$secs': the Allwinner watchdog accepts 1 to 16 seconds (a minute-based value is refused)"; fi
done
# What the comments say must be what systemd does (read in its source: src/core/main.c hands only reboot and kexec a watchdog
# time, poweroff and halt get 0 and PID 1 disarms the watchdog first): the old text said that systemd-shutdown feeds it and the
# review took it to stay armed for a shut down too. The text is part of the product, so it is pinned.
grep -q 'A shut down (poweroff) disarms the watchdog first' "$ROOT/etc/systemd/system.conf.d/90-sinko-watchdog.conf" || fail "the drop-in does not say that a shut down disarms the watchdog"
for said in "poweroff and halt" "AND DISARMS it" "WATCHDOG_USEC=0" "reboot and kexec carry a watchdog time over" "RebootWatchdogSec is the safety net of the reboot itself" \
            "CONFIG_WATCHDOG_NOWAYOUT" "obsolete name of RebootWatchdogSec"; do
  grep -qF -- "$said" "$REPO/tools/seal.sh" || fail "the watchdog comment in tools/seal.sh no longer says: $said"
done
grep -q 'systemd-shutdown does' "$REPO/tools/seal.sh" && fail "tools/seal.sh again says that systemd-shutdown feeds the watchdog for every verb (it does not disarm or feed it for a poweroff)"
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
for emptied in var/log/syslog var/log/wtmp var/log/pihole/pihole.log var/log/pihole/FTL.log var/log/sinko-install.log \
               var/log/pihole/pihole.log.1 var/log/pihole/pihole.log.2.gz; do   # Pi-hole's rotated logs are emptied, never deleted
  [[ -f "$ROOT/$emptied" && ! -s "$ROOT/$emptied" ]] || fail "$emptied must stay as an empty file"
done
[[ ! -e "$ROOT/var/log/syslog.1" && ! -e "$ROOT/var/log/auth.log.2.gz" ]] || fail "rotated logs were left"
[[ -d "$ROOT/var/log/pihole" ]] || fail "Pi-hole's log folder was removed"
[[ ! -e "$ROOT/var/log/journal/0123abcd/system@0000000000000001-0000000000000002.journal" ]] || fail "an archived journal was left"
grep -q 'journalctl --rotate' "$WORK/calls.log" || fail "the journal was not rotated"
grep -q 'journalctl --vacuum' "$WORK/calls.log" || fail "the journal was not vacuumed"
echo "    the card's own copy of the logs (/var/log.hdd): rotated logs gone, Pi-hole's emptied, the folder itself stays"
[[ ! -e "$ROOT/var/log.hdd/syslog.1" && ! -e "$ROOT/var/log.hdd/auth.log.2.gz" ]] || fail "rotated logs were left on the card's copy"
for emptied in var/log.hdd/syslog var/log.hdd/pihole/pihole.log var/log.hdd/pihole/pihole.log.1 var/log.hdd/pihole/pihole.log.2.gz; do
  [[ -f "$ROOT/$emptied" && ! -s "$ROOT/$emptied" ]] || fail "$emptied must stay as an empty file (it listed the seller's lookups)"
done
[[ -d "$ROOT/var/log.hdd" ]] || fail "the card's log folder (a mount point) was removed"
echo "    the console no longer logs in without a password (the other getty settings stay); saved Wi-Fi networks and their keys are gone (the wired setup stays)"
[[ ! -e "$ROOT/etc/systemd/system/getty@.service.d/override.conf" && ! -e "$ROOT/etc/systemd/system/serial-getty@.service.d/override.conf" ]] || fail "the automatic root login on the console is still there"
[[ -f "$ROOT/etc/systemd/system/getty@tty1.service.d/keep.conf" ]] || fail "a getty drop-in that has no autologin was removed"
for wifi in etc/netplan/30-wifis-dhcp.yaml etc/NetworkManager/system-connections/HomeNet.nmconnection etc/wpa_supplicant/wpa_supplicant.conf \
            var/lib/NetworkManager/secret_key boot/armbian_first_run.txt.off; do
  [[ ! -e "$ROOT/$wifi" ]] || fail "$wifi is still there"
done
[[ -f "$ROOT/boot/armbian_first_run.txt.template" ]] || fail "Armbian's empty first-start template was removed"
[[ -f "$ROOT/etc/netplan/10-dhcp-all-interfaces.yaml" && -f "$ROOT/etc/NetworkManager/system-connections/Wired.nmconnection" ]] || fail "the wired network setup was removed"
grep -rq "seller-wifi-pass" "$ROOT/etc" "$ROOT/boot" && fail "the seller's Wi-Fi password is still in a file"
echo "    zeros are written over the free space by default (deleting only frees blocks), after a sync, and again synced"
dd_line="$(grep -n "^dd if=/dev/zero of=$ROOT/.sinko-zerofill " "$WORK/calls.log" | head -1 | cut -d: -f1)"
[[ -n "$dd_line" ]] || fail "no zeros were written by default"
sync_before="$(head -n "$((dd_line - 1))" "$WORK/calls.log" | grep -c '^sync$' || true)"
(( sync_before >= 1 )) || fail "no sync before the zeros: blocks freed a moment ago cannot be overwritten, so the deleted data would stay"
tail -n +"$((dd_line + 1))" "$WORK/calls.log" | grep -q '^sync$' || fail "no sync after the zeros"
[[ ! -e "$ROOT/.sinko-zerofill" ]] || fail "the zero file was left behind"
echo "    SSH's settings were read back with sshd -G (the host keys are gone by then, and sshd -T would print nothing without one)"
grep -q "^sshd -G -f $ROOT/etc/ssh/sshd_config" "$WORK/calls.log" || { grep '^sshd' "$WORK/calls.log" || true; fail "the read-back did not use sshd -G"; }
grep -q '^sshd -T' "$WORK/calls.log" && fail "sshd -T was run on a box whose host keys are gone: it prints nothing there"
echo "    the root password is locked last: nothing that changes the system comes after it"
last_change="$(grep -E '^((passwd|systemctl|journalctl|dd|apt-get|ssh-keygen|hostnamectl) .*|sync)$' "$WORK/calls.log" | tail -n1)"
[[ "$last_change" == "passwd -R $ROOT -l root" ]] || fail "the last change is '$last_change', not the lock of the root password"

echo "--- sealing again changes nothing more"
world_after="$(world_digest)"
mock_api GET /api/groups | json_get 'sorted(g["name"] for g in d["groups"])' >"$WORK/groups1.txt"
bash "$SEAL" --yes >"$WORK/seal2.out" 2>&1 || { cat "$WORK/seal2.out"; fail "sealing a second time failed"; }
[[ "$(world_digest)" == "$world_after" ]] || fail "a second seal changed files"
[[ "$(mock_api GET /api/groups | json_get 'sorted(g["name"] for g in d["groups"])')" == "$(cat "$WORK/groups1.txt")" ]] || fail "a second seal changed the groups"
[[ ! -e "$WORK/units/pihole-FTL.service.active" ]] || fail "a second seal left FTL running"

echo "--- zeros go over the free space under the fake root only, then are deleted; --zerofill is still accepted"
make_golden
bash "$SEAL" --yes --zerofill >"$WORK/zero.out" 2>&1 || { cat "$WORK/zero.out"; fail "sealing with --zerofill failed"; }
grep -q "^dd if=/dev/zero of=$ROOT/.sinko-zerofill " "$WORK/calls.log" || { cat "$WORK/calls.log"; fail "zerofill did not run on the fake root"; }
[[ ! -e "$ROOT/.sinko-zerofill" ]] || fail "the zero file was left behind"
grep -q "^sync" "$WORK/calls.log" || fail "no sync after the zeros"
grep -q "WARNING" "$WORK/zero.out" && fail "a warning about the free space although it was overwritten"
echo "--- --no-zerofill (a test unit): no zeros, and a loud warning in the plan and at the end"
make_golden
bash "$SEAL" --dry-run --no-zerofill >"$WORK/nozero-dry.out" 2>&1 || { cat "$WORK/nozero-dry.out"; fail "dry run with --no-zerofill failed"; }
grep -q "WARNING: the free space is NOT overwritten" "$WORK/nozero-dry.out" || fail "the plan does not warn that deleted data stays in the image"
grep -q "Write zeros" "$WORK/nozero-dry.out" && fail "the plan still announces zeros"
bash "$SEAL" --yes --no-zerofill >"$WORK/nozero.out" 2>&1 || { cat "$WORK/nozero.out"; fail "sealing with --no-zerofill failed"; }
grep -q 'dd if=/dev/zero' "$WORK/calls.log" && fail "zeros were written although --no-zerofill"
grep -q "WARNING: the free space was NOT overwritten" "$WORK/nozero.out" || fail "no warning at the end that deleted data can be read from the image"
diff <(plan_lines "$WORK/nozero-dry.out") <(plan_lines "$WORK/nozero.out") >/dev/null || fail "the real run did not do what the dry run announced (--no-zerofill)"

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
grep -q "could not be removed" "$WORK/stuck.out" || { cat "$WORK/stuck.out"; fail "no message about the password"; }
[[ ! -e "$ROOT/var/lib/sinko/firstboot" && ! -e "$WORK/units/sinko-firstboot.service.enabled" ]] || fail "the first start was armed after a failure"
[[ -e "$ROOT/etc/ssh/ssh_host_ed25519_key" && -e "$ROOT/etc/pihole/pihole-FTL.db" ]] || fail "something was deleted after the failure"
echo "    the stored hash is cleared when FTL's password setting does not do it"
make_golden
touch "$WORK/password-needs-pwhash"
bash "$SEAL" --yes >"$WORK/pwhash.out" 2>&1 || { cat "$WORK/pwhash.out"; fail "the pwhash fallback did not work"; }
grep -q 'webserver.api.pwhash <empty>' "$WORK/calls.log" || fail "the stored hash was not cleared"
echo "--- if SSH password logins would stay on, the seal stops and the root password is NOT locked (the lock-down is the last step)"
make_golden
touch "$WORK/sshd-open"
if bash "$SEAL" --yes >"$WORK/open.out" 2>&1; then fail "sealed although SSH password logins are still on"; fi
grep -q "SSH password logins are still on" "$WORK/open.out" || { cat "$WORK/open.out"; fail "no message about SSH"; }
grep -q "The root password was NOT locked" "$WORK/open.out" || fail "the message does not say that the root password was not locked"
grep -q "^passwd" "$WORK/calls.log" && fail "the root password was locked although SSH password logins stay on"
[[ ! -e "$ROOT/etc/ssh/sshd_config.d/00-sinko-lockdown.conf" ]] || fail "the drop-in was left in place after the seal stopped (the box has no keys by now: it would lock the seller out of an unsealed unit)"
echo "--- the read-back is real: it works without host keys (they are gone by then), falls back for an sshd without -G, and stops the seal when sshd cannot report"
make_golden
touch "$WORK/sshd-no-G"
bash "$SEAL" --yes >"$WORK/oldssh.out" 2>&1 || { cat "$WORK/oldssh.out"; fail "an sshd without -G (before OpenSSH 9.x) stopped the seal"; }
grep -q '^sshd -T -h ' "$WORK/calls.log" || fail "the old sshd was not asked with a throw-away host key"
grep -q '^ssh-keygen -q -t ed25519 -N' "$WORK/calls.log" || fail "no throw-away host key was made for the old sshd"
grep -q "^passwd -R $ROOT -l root" "$WORK/calls.log" || fail "root was not locked after a good read-back (old sshd)"
make_golden
touch "$WORK/sshd-no-G" "$WORK/sshd-no-T-h"
if bash "$SEAL" --yes >"$WORK/nossh.out" 2>&1; then fail "sealed although sshd could not say what it would do"; fi
grep -q "sshd could not report its settings" "$WORK/nossh.out" || { cat "$WORK/nossh.out"; fail "no message that the read-back failed"; }
grep -q "The root password was NOT locked" "$WORK/nossh.out" || fail "the message does not say that the root password was not locked"
grep -q "^passwd" "$WORK/calls.log" && fail "the root password was locked although the read-back failed"
[[ ! -e "$ROOT/etc/ssh/sshd_config.d/00-sinko-lockdown.conf" ]] || fail "the drop-in was left in place after a failed read-back"
echo "    a PasswordAuthentication line above the Include makes the drop-in useless: the preflight sees the Include, the read-back sees the setting"
make_golden
printf 'PasswordAuthentication yes\nInclude /etc/ssh/sshd_config.d/*.conf\n' >"$ROOT/etc/ssh/sshd_config"
if bash "$SEAL" --yes >"$WORK/above.out" 2>&1; then fail "sealed although a line above the Include keeps password logins on"; fi
grep -q "SSH password logins are still on" "$WORK/above.out" || { cat "$WORK/above.out"; fail "no message about the line above the Include"; }
grep -q "^passwd" "$WORK/calls.log" && fail "the root password was locked although password logins stay on"
[[ ! -e "$ROOT/etc/ssh/sshd_config.d/00-sinko-lockdown.conf" ]] || fail "the useless drop-in was left in place"
echo "--- a failure after the first-start service is armed but before the lock-down leaves the seller able to log in and run seal again"
make_golden
touch "$WORK/ftl-stop-fails"
if bash "$SEAL" --yes >"$WORK/midfail.out" 2>&1; then fail "sealed although Pi-hole's FTL could not be stopped"; fi
grep -q "could not stop Pi-hole's FTL" "$WORK/midfail.out" || { cat "$WORK/midfail.out"; fail "no message about FTL"; }
grep -q "^passwd" "$WORK/calls.log" && fail "the root password was locked although the seal failed half way"
[[ ! -e "$ROOT/etc/ssh/sshd_config.d/00-sinko-lockdown.conf" && -f "$ROOT/root/.ssh/authorized_keys" && -e "$ROOT/etc/ssh/ssh_host_ed25519_key" ]] \
  || fail "a failure half way left the seller locked out (or without keys)"
rm -f "$WORK/ftl-stop-fails"
bash "$SEAL" --yes >"$WORK/midfail2.out" 2>&1 || { cat "$WORK/midfail2.out"; fail "running the seal again after the failure did not complete it"; }
grep -q "^passwd -R $ROOT -l root" "$WORK/calls.log" || fail "the second run did not lock the root password"

echo "--- a two-factor secret or an application password that cannot be cleared stops the seal before anything is armed"
make_golden
touch "$WORK/credential-stuck"
if bash "$SEAL" --yes >"$WORK/cred.out" 2>&1; then fail "sealed although a two-factor secret / application password is still set"; fi
grep -q "two-factor secret or an application password could not be removed" "$WORK/cred.out" || { cat "$WORK/cred.out"; fail "no message about the secrets"; }
[[ ! -e "$ROOT/var/lib/sinko/firstboot" && -e "$ROOT/etc/ssh/ssh_host_ed25519_key" && -e "$ROOT/etc/pihole/tls.pem" ]] || fail "something was armed or deleted after the failure"

echo "--- a unit that is not ready to be copied is refused (dry run and real run), every reason is listed, nothing is changed"
make_golden
world_before="$(world_digest)"; api_before="$(api_digest)"
check_refused() {  # reason-pattern what
  local mode
  for mode in --dry-run --yes; do
    if bash "$SEAL" "$mode" >"$WORK/pre.out" 2>&1; then cat "$WORK/pre.out"; fail "$2: sealed or dry-ran although the unit is not ready ($mode)"; fi
    grep -q "not ready to be sealed" "$WORK/pre.out" || fail "$2: no 'not ready' message ($mode)"
    grep -q -- "$1" "$WORK/pre.out" || { cat "$WORK/pre.out"; fail "$2: the reason is not given ($mode)"; }
  done
  [[ "$(world_digest)" == "$world_before" && "$(api_digest)" == "$api_before" ]] || fail "$2: a refused seal changed something"
}
sed -i '/^avahi-daemon$/d' "$WORK/dpkg-installed"
check_refused "avahi-daemon is not installed" "no avahi"
echo "    ... unless the box was set up without the local name (SINKO_MDNS=0), or the checks are skipped"
cp "$ROOT/etc/sinko/config" "$WORK/config.keep"; echo "SINKO_MDNS=0" >>"$ROOT/etc/sinko/config"
bash "$SEAL" --dry-run >/dev/null 2>&1 || fail "the missing avahi was refused although the local name is switched off"
cp "$WORK/config.keep" "$ROOT/etc/sinko/config"
bash "$SEAL" --dry-run --skip-checks >"$WORK/skip.out" 2>&1 || fail "--skip-checks did not skip the checks"
grep -q "not checked" "$WORK/skip.out" || fail "--skip-checks does not say that the unit was not checked"
echo avahi-daemon >>"$WORK/dpkg-installed"
rm "$WORK/units/avahi-daemon.service.enabled"
check_refused "avahi-daemon is not enabled" "avahi not enabled"
touch "$WORK/units/avahi-daemon.service.enabled"
touch "$WORK/ip-static"
check_refused "fixed address" "a fixed address"
rm "$WORK/ip-static"
for unit in sinko.service sinko-lists.timer pihole-FTL.service; do
  mv "$WORK/units/$unit.enabled" "$WORK/unit.bak"
  check_refused "$unit is not enabled" "$unit not enabled"
  mv "$WORK/unit.bak" "$WORK/units/$unit.enabled"
done
cp "$ROOT/etc/ssh/sshd_config" "$WORK/sshd_config.keep"
printf 'PermitRootLogin yes\nPasswordAuthentication yes\n' >"$ROOT/etc/ssh/sshd_config"
world_before="$(world_digest)"
check_refused "does not include /etc/ssh/sshd_config.d" "sshd_config without Include"
bash "$SEAL" --dry-run --keep-ssh-access >/dev/null 2>&1 || fail "a test unit with --keep-ssh-access was refused for its sshd_config"
cp "$WORK/sshd_config.keep" "$ROOT/etc/ssh/sshd_config"
printf 'network:\n  version: 2\n  ethernets:\n    eth0:\n      dhcp4: true\n  wifis:\n    wlan0:\n      access-points:\n        "HomeNet":\n          password: "x"\n' >"$ROOT/etc/netplan/50-mixed.yaml"
world_before="$(world_digest)"
check_refused "50-mixed.yaml has Wi-Fi and wired settings in one file" "a netplan file with Wi-Fi and wired settings"
rm "$ROOT/etc/netplan/50-mixed.yaml"
world_before="$(world_digest)"
echo "    Debian's security updates are part of what a box must have (docs/selling.md promises them, and nobody can log in later to fix it)"
ua_file="$ROOT/etc/apt/apt.conf.d/52sinko-unattended-upgrades"
[[ -f "$ua_file" ]] || fail "test setup: the installer wrote no security-update settings into the golden unit"
sed -i '/^unattended-upgrades$/d' "$WORK/dpkg-installed"
check_refused "unattended-upgrades is not installed" "no unattended-upgrades"
printf 'unattended-upgrades\n' >"$WORK/dpkg-configfiles"
check_refused "unattended-upgrades is not installed" "unattended-upgrades removed but not purged (apt remove)"
: >"$WORK/dpkg-configfiles"; printf 'unattended-upgrades\n' >>"$WORK/dpkg-installed"
for timer in apt-daily.timer apt-daily-upgrade.timer; do
  mv "$WORK/units/$timer.enabled" "$WORK/unit.bak"
  check_refused "$timer is not enabled" "$timer not enabled"
  mv "$WORK/unit.bak" "$WORK/units/$timer.enabled"
done
cp "$ua_file" "$WORK/ua.keep"
printf 'APT::Periodic::Unattended-Upgrade "0";\n' >"$ROOT/etc/apt/apt.conf.d/99zz-off"
world_before="$(world_digest)"
check_refused 'automatic updates are switched off: APT::Periodic::Unattended-Upgrade is "0" in .*99zz-off' "a later file switches the updates off"
grep 'switched off' "$WORK/pre.out" | grep -q "run the installer again" && fail "the remedy for switched-off updates points at the installer, which leaves an installed unattended-upgrades alone (it would loop): it must name the file"
rm "$ROOT/etc/apt/apt.conf.d/99zz-off"
echo "    ... a pre-installed unattended-upgrades (the installer leaves it as it is) passes when the box's own file switches it on, and is refused when nothing does"
rm "$ua_file"
world_before="$(world_digest)"
check_refused "automatic updates are not switched on" "no file switches the updates on"
grep -q "dpkg-reconfigure -plow unattended-upgrades" "$WORK/pre.out" || fail "the message does not say how to switch them on"
printf 'APT::Periodic::Update-Package-Lists "1";\nAPT::Periodic::Unattended-Upgrade "1";\n' >"$ROOT/etc/apt/apt.conf.d/20auto-upgrades"
bash "$SEAL" --dry-run >/dev/null 2>&1 || fail "a unit with the distribution's own automatic-updates setting was refused"
rm "$ROOT/etc/apt/apt.conf.d/20auto-upgrades"
echo "    ... and a unit that was set up without them (SINKO_OS_UPDATES=0) is not asked for them"
cp "$ROOT/etc/sinko/config" "$WORK/config.keep"; echo "SINKO_OS_UPDATES=0" >>"$ROOT/etc/sinko/config"
sed -i '/^unattended-upgrades$/d' "$WORK/dpkg-installed"; mv "$WORK/units/apt-daily.timer.enabled" "$WORK/unit.bak"
bash "$SEAL" --dry-run >/dev/null 2>&1 || fail "a unit with SINKO_OS_UPDATES=0 was refused for lacking automatic updates"
mv "$WORK/unit.bak" "$WORK/units/apt-daily.timer.enabled"; printf 'unattended-upgrades\n' >>"$WORK/dpkg-installed"; cp "$WORK/config.keep" "$ROOT/etc/sinko/config"
cp "$WORK/ua.keep" "$ua_file"
world_before="$(world_digest)"
echo "    the box has no battery-backed clock: a time service must be switched on (any of them)"
mv "$WORK/units/systemd-timesyncd.service.enabled" "$WORK/unit.bak"
check_refused "no time service" "no time service"
touch "$WORK/units/chrony.service.enabled"
bash "$SEAL" --dry-run >/dev/null 2>&1 || fail "a unit whose clock is kept by chrony was refused"
rm "$WORK/units/chrony.service.enabled"; mv "$WORK/unit.bak" "$WORK/units/systemd-timesyncd.service.enabled"
echo "    no account other than root may be able to log in on the console (the seal locks root, not the seller's own user)"
cp "$ROOT/etc/passwd" "$WORK/passwd.keep"; cp "$ROOT/etc/shadow" "$WORK/shadow.keep"
printf 'seller:x:1000:1000:Seller,,,:/home/seller:/bin/bash\n' >>"$ROOT/etc/passwd"
printf 'seller:$y$j9T$salt$hash:19000:0:99999:7:::\n' >>"$ROOT/etc/shadow"
world_before="$(world_digest)"
check_refused "the account 'seller' can log in on the console" "an extra user with a password"
grep -q "userdel -r seller" "$WORK/pre.out" || fail "the message does not say how to delete the account"
sed -i 's/^seller:[^:]*:/seller::/' "$ROOT/etc/shadow"
world_before="$(world_digest)"
check_refused "the account 'seller' can log in on the console" "an extra user with an EMPTY password (passwd -d: Debian lets that in)"
printf 'other:x:1001:1001::/home/other:/bin/bash\n' >>"$ROOT/etc/passwd"; printf 'other:$6$s$h:19000::::::\n' >>"$ROOT/etc/shadow"
world_before="$(world_digest)"
check_refused "the account 'other' can log in on the console" "two extra users are both listed"
grep -q "the account 'seller'" "$WORK/pre.out" || fail "not every account is listed"
bash "$SEAL" --dry-run --keep-ssh-access >/dev/null 2>&1 || fail "a test unit with --keep-ssh-access was refused for an extra user"
cp "$WORK/passwd.keep" "$ROOT/etc/passwd"; cp "$WORK/shadow.keep" "$ROOT/etc/shadow"
echo "    ... but a locked account, a system account and an account whose shell is nologin pass"
printf 'seller:x:1000:1000:Seller,,,:/home/seller:/bin/bash\nsvc:x:1002:1002::/var/lib/svc:/usr/sbin/nologin\nmail:x:8:8:mail:/var/mail:/bin/false\n' >>"$ROOT/etc/passwd"
printf 'seller:!$y$j9T$salt$hash:19000:0:99999:7:::\nsvc:$6$s$h:19000::::::\nmail:$6$s$h:19000::::::\n' >>"$ROOT/etc/shadow"
bash "$SEAL" --dry-run >"$WORK/locked.out" 2>&1 || { cat "$WORK/locked.out"; fail "locked or non-login accounts were refused"; }
cp "$WORK/passwd.keep" "$ROOT/etc/passwd"; cp "$WORK/shadow.keep" "$ROOT/etc/shadow"
world_before="$(world_digest)"
bash "$SEAL" --dry-run >/dev/null 2>&1 || fail "a ready unit was refused after the problems were fixed"

echo "--- --skip-checks cannot put a console login into the image: the last check before the lock-down stops it, with root still unlocked"
make_golden
printf 'seller:x:1000:1000:Seller,,,:/home/seller:/bin/bash\n' >>"$ROOT/etc/passwd"
printf 'seller:$y$j9T$salt$hash:19000:0:99999:7:::\n' >>"$ROOT/etc/shadow"
if bash "$SEAL" --yes --skip-checks >"$WORK/skipacct.out" 2>&1; then fail "sealed with --skip-checks although an account can log in on the console"; fi
grep -q "an account other than root can still log in on the console: seller" "$WORK/skipacct.out" || { cat "$WORK/skipacct.out"; fail "the last check did not name the account"; }
grep -q "^passwd" "$WORK/calls.log" && fail "root was locked although the seal stopped (the seller could no longer fix it)"
echo "    after the account is deleted the same command completes the seal"
sed -i '/^seller:/d' "$ROOT/etc/passwd" "$ROOT/etc/shadow"
bash "$SEAL" --yes --skip-checks >"$WORK/skipacct2.out" 2>&1 || { cat "$WORK/skipacct2.out"; fail "the seal did not complete after the account was deleted"; }
grep -q "^passwd -R $ROOT -l root" "$WORK/calls.log" || fail "root was not locked by the completed seal"

echo "--- the zero-fill: an earlier seal's leftover zero file goes first, and a dd that did not fill the free space fails the seal"
make_golden
echo junk >"$ROOT/.sinko-zerofill"
bash "$SEAL" --dry-run >"$WORK/zf-dry.out" 2>&1 || { cat "$WORK/zf-dry.out"; fail "the dry run with a leftover zero file failed"; }
grep -q "Delete $ROOT/.sinko-zerofill, left by an earlier seal that did not finish" "$WORK/zf-dry.out" || fail "the plan does not mention the leftover zero file"
[[ -e "$ROOT/.sinko-zerofill" ]] || fail "the dry run deleted the leftover zero file"
bash "$SEAL" --yes >"$WORK/zf.out" 2>&1 || { cat "$WORK/zf.out"; fail "sealing with a leftover zero file failed"; }
[[ ! -e "$ROOT/.sinko-zerofill" ]] || fail "the leftover zero file is still there"
[[ "$(plan_lines "$WORK/zf.out" | head -n1)" == *"Delete $ROOT/.sinko-zerofill"* ]] || fail "the leftover zero file is not the first thing that is done (a full card breaks everything after it)"
for sw in dd-io-error dd-readonly dd-killed dd-no-end; do
  make_golden
  touch "$WORK/$sw"
  if bash "$SEAL" --yes >"$WORK/zf-$sw.out" 2>&1; then cat "$WORK/zf-$sw.out"; fail "sealed although the free space was not overwritten ($sw)"; fi
  grep -q "overwriting the free space did not work" "$WORK/zf-$sw.out" || { cat "$WORK/zf-$sw.out"; fail "no message that the free space was not overwritten ($sw)"; }
  grep -q "Sealed. Power the box off now" "$WORK/zf-$sw.out" && fail "the seal said it was done after a failed zero-fill ($sw)"
  [[ ! -e "$ROOT/.sinko-zerofill" ]] || fail "the zero file was left behind ($sw): it fills the card"
  grep -q "^passwd" "$WORK/calls.log" && fail "root was locked after a failed zero-fill ($sw)"
  rm -f "$WORK/$sw"
done
grep -q "Input/output error" "$WORK/zf-dd-io-error.out" || fail "the message does not say what dd said"
echo "    ... and the seal that follows completes"
bash "$SEAL" --yes >"$WORK/zf-again.out" 2>&1 || { cat "$WORK/zf-again.out"; fail "the seal did not complete after the failed zero-fill was fixed"; }

echo "--- Pi-hole's HTTPS key: the file its settings name (and the public copies beside it) go; an odd location stops the seal"
make_golden
mkdir -p "$ROOT/etc/pihole/custom"
echo key >"$ROOT/etc/pihole/custom/web.pem"; echo cert >"$ROOT/etc/pihole/custom/web.crt"; echo ca >"$ROOT/etc/pihole/custom/web_ca.crt"
pihole-FTL --config webserver.tls.cert "$ROOT/etc/pihole/custom/web.pem"
bash "$SEAL" --yes >"$WORK/tls1.out" 2>&1 || { cat "$WORK/tls1.out"; fail "sealing with a custom certificate path failed"; }
[[ ! -e "$ROOT/etc/pihole/custom/web.pem" && ! -e "$ROOT/etc/pihole/custom/web.crt" && ! -e "$ROOT/etc/pihole/custom/web_ca.crt" ]] || fail "the certificate files named in Pi-hole's settings are still there"
make_golden
pihole-FTL --config webserver.tls.cert /etc/passwd
world_before="$(world_digest)"
if bash "$SEAL" --yes >"$WORK/tls2.out" 2>&1; then fail "a certificate path outside the box was accepted"; fi
grep -q "unexpected location of Pi-hole's webserver.tls.cert" "$WORK/tls2.out" || { cat "$WORK/tls2.out"; fail "no message about the odd certificate path"; }
[[ "$(world_digest)" == "$world_before" ]] || fail "files were changed although the certificate path was refused"

echo "--- the card's copy of the logs can be somewhere else (HDD_LOG in Armbian's settings)"
make_golden
rm -rf "$ROOT/var/log.hdd"
mkdir -p "$ROOT/var/card/pihole" "$ROOT/etc/default"
echo "HDD_LOG=/var/card" >"$ROOT/etc/default/armbian-ramlog"
echo "old" >"$ROOT/var/card/syslog.1"; echo "queries" >"$ROOT/var/card/pihole/pihole.log.1"
bash "$SEAL" --yes >"$WORK/hdd.out" 2>&1 || { cat "$WORK/hdd.out"; fail "sealing with HDD_LOG failed"; }
[[ ! -e "$ROOT/var/card/syslog.1" && -f "$ROOT/var/card/pihole/pihole.log.1" && ! -s "$ROOT/var/card/pihole/pihole.log.1" ]] || fail "the card's log copy named by HDD_LOG was not cleaned"

echo "seal tests passed"
