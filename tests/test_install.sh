#!/usr/bin/env bash
# End-to-end test of install.sh / uninstall.sh without a real Pi-hole.
# System commands (ip, systemctl, pihole, pihole-FTL, …) are replaced by stubs,
# the Pi-hole API by tests/mock_pihole.py, and files go under a temp "root".
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=tests/lib_stubs.sh
. "$HERE/lib_stubs.sh"
PORT="${PORT:-18080}"
fake_system_init
# The machine that runs this test is not Debian; the installer is told what it would see on the box.
cat >"$WORK/os-release" <<'EOF'
PRETTY_NAME="Armbian 25.8 trixie"
ID=debian
VERSION_CODENAME=trixie
EOF
export SINKO_OS_RELEASE="$WORK/os-release"
echo kidsbox >"$ROOT/etc/hostname"

# Pretend an old 1.x page is installed
mkdir -p "$ROOT/var/www/html/parental"
echo '<script src="/parental/app.js"></script>' >"$ROOT/var/www/html/index.html"

echo "--- install"
bash "$REPO/install.sh" >"$WORK/install.out" 2>&1 || { cat "$WORK/install.out"; fail "install.sh exited non-zero"; }
out="$(cat "$WORK/install.out")"
grep -q "Sinko is ready" <<<"$out" || { echo "$out"; fail "no summary"; }
grep -q "Pi-hole v6 is already installed" <<<"$out" || fail "existing Pi-hole v6 (serve_all off) not recognised"
grep -q "ok    webserver.serve_all is true" <<<"$out" || fail "doctor did not confirm serve_all"
[[ -f "$ROOT/var/www/html/index.html" ]] || fail "index.html missing"
grep -q 'content="sinko"' "$ROOT/var/www/html/index.html" || fail "wrong index.html"
[[ ! -e "$ROOT/var/www/html/parental" ]] || fail "legacy page not removed"
[[ ! -e "$ROOT/var/www/html/index.html.pb-backup" ]] || fail "legacy page should not be backed up"
[[ -f "$ROOT/var/www/html/pb/app.js" && -f "$ROOT/var/www/html/pb/services.json" ]] || fail "assets missing"
for f in pb-core.js pb-live.js pb-picture.js; do          # the live picture's scripts: installed, and loaded by the page
  [[ -f "$ROOT/var/www/html/pb/$f" ]] || fail "$f was not installed"
  grep -q "/pb/$f" "$ROOT/var/www/html/index.html" || fail "index.html does not load $f"
done
python3 - "$ROOT/var/www/html/pb/domains.json" <<'PYEOF' || fail "domains.json is missing or wrong"
import json, sys
d = json.load(open(sys.argv[1]))
assert d["v"] == 1 and d["domains"]["youtube.com"] == "youtube" and d["domains"]["googlevideo.com"] == "youtube", d["domains"].get("youtube.com")
assert len(d["domains"]) > 200
PYEOF
[[ -f "$ROOT/var/www/html/pb/fonts/plex-arabic-arabic-400.woff2" ]] || fail "fonts missing"
[[ -L "$ROOT/usr/local/bin/sinko" ]] || fail "cli link missing"
grep -q '^SINKO_HOSTNAME=family.lan' "$ROOT/etc/sinko/config" || fail "config not written"
grep -q '"webserver.serve_all": "true"' "$WORK/ftl.json" || fail "serve_all not enabled"
grep -q '192.168.1.9 nas.lan, 192.168.1.50 family.lan' "$WORK/ftl.json" || { cat "$WORK/ftl.json"; fail "dns.hosts not merged"; }
grep -q '/usr/local/bin/pihole -g\|pihole -g' "$ROOT/etc/systemd/system/sinko-lists.service" || fail "unit not rendered"
grep -q 'systemctl restart sinko.service' "$WORK/calls.log" || fail "service not started"
cmp -s "$REPO/systemd/sinko.service" "$ROOT/etc/systemd/system/sinko.service" || fail "the installed scheduler unit is not the one in the release"
[[ "$(stat -c %a "$ROOT/var/lib/sinko")" == 700 ]] || fail "the state folder is not 0700"
grep -q 'pihole -g' "$WORK/calls.log" || fail "gravity not run"
grep -q 'Asia/Bahrain' "$WORK/tz" || fail "timezone not set"
grep -q 'Keeping the existing password' <<<"$out" || fail "password should be kept"
curl -s "http://127.0.0.1:$PORT/api/auth" -H "sid: x" >/dev/null
lists="$(python3 - "$PORT" <<'EOF'
import json, sys, urllib.request
port = sys.argv[1]
req = urllib.request.Request("http://127.0.0.1:%s/api/auth" % port, data=b'{"password":"test"}',
                             headers={"Content-Type": "application/json"}, method="POST")
sid = json.load(urllib.request.urlopen(req))["session"]["sid"]
req = urllib.request.Request("http://127.0.0.1:%s/api/lists" % port, headers={"sid": sid})
lists = json.load(urllib.request.urlopen(req))["lists"]
print(len(lists), lists[0]["address"])
EOF
)"
[[ "$lists" == "20 https://raw.githubusercontent.com/iret33/sinko/master/lists/"* ]] || fail "lists: $lists"

"$ROOT/usr/local/bin/sinko" status | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d["version"] and d["devices"] == []' || fail "status output is not the expected JSON"
"$ROOT/usr/local/bin/sinko" use-mac --dry-run | grep -q "Nothing to convert" || fail "use-mac did not run"
# diagnose and watch through the real CLI; the stubbed pihole-FTL has no sqlite shell, so the databases are "unreadable"
"$ROOT/usr/local/bin/sinko" diagnose youtube >"$WORK/diagnose.out" 2>&1 || { cat "$WORK/diagnose.out"; fail "diagnose failed"; }
grep -q "Findings, most likely first" "$WORK/diagnose.out" || { cat "$WORK/diagnose.out"; fail "diagnose printed no findings"; }
grep -q "Could not read" "$WORK/diagnose.out" || fail "diagnose did not report the unreadable database"
if "$ROOT/usr/local/bin/sinko" diagnose no-such-service >"$WORK/diagnose2.out" 2>&1; then fail "diagnose accepted an unknown service"; fi
grep -q "unknown service" "$WORK/diagnose2.out" || fail "no message for an unknown service"
: >"$WORK/pihole.log"
SINKO_DNSMASQ_LOG="$WORK/pihole.log" "$ROOT/usr/local/bin/sinko" watch 0.02 >"$WORK/watch.out" 2>&1 || { cat "$WORK/watch.out"; fail "watch failed"; }
grep -q "not using this box for DNS" "$WORK/watch.out" || { cat "$WORK/watch.out"; fail "watch printed no summary"; }

echo "--- the local name and security updates are installed (stubbed apt and systemd)"
grep -q 'apt-get install .*avahi-daemon' "$WORK/calls.log" || fail "avahi-daemon was not installed"
grep -q 'apt-get install .*unattended-upgrades' "$WORK/calls.log" || fail "unattended-upgrades was not installed"
[[ -e "$WORK/units/avahi-daemon.service.enabled" && -e "$WORK/units/avahi-daemon.service.active" ]] || fail "avahi-daemon was not enabled and started"
grep -q 'http://kidsbox.local/' "$WORK/install.out" || { cat "$WORK/install.out"; fail "the summary does not show the .local name"; }
ua="$ROOT/etc/apt/apt.conf.d/52sinko-unattended-upgrades"
[[ -f "$ua" ]] || fail "no unattended-upgrades settings written"
grep -q '^#clear Unattended-Upgrade::Origins-Pattern;$' "$ua" || fail "the package's own origin list is not cleared (apt lists add up)"
# shellcheck disable=SC2016  # apt's macro, literal on purpose
grep -qF 'origin=Debian,codename=${distro_codename}-security,label=Debian-Security' "$ua" || fail "Debian security origin missing"
grep -q 'label=Debian"' "$ua" && fail "an origin other than Debian security is allowed"
grep -q 'Origins-Pattern {' "$ua" || fail "no origin list"
grep -q '^Unattended-Upgrade::Automatic-Reboot "false";$' "$ua" || fail "automatic reboot is not switched off"
grep -q '^APT::Periodic::Unattended-Upgrade "1";$' "$ua" || fail "periodic upgrades are not switched on"
grep -q '^SINKO_MDNS=1$' "$ROOT/etc/sinko/config" || fail "SINKO_MDNS is not saved"
grep -q '^SINKO_OS_UPDATES=1$' "$ROOT/etc/sinko/config" || fail "SINKO_OS_UPDATES is not saved"
[[ "$(grep -c 'apt-get install .*avahi-daemon' "$WORK/calls.log")" == 1 ]] || fail "avahi-daemon installed more than once"

echo "--- re-run (update) keeps settings, hostname change replaces host entry"
SINKO_HOSTNAME=kids.home bash "$REPO/install.sh" >"$WORK/install2.out" 2>&1 || { cat "$WORK/install2.out"; fail "second run failed"; }
grep -q '^SINKO_HOSTNAME=kids.home' "$ROOT/etc/sinko/config" || fail "hostname not updated"
grep -q '192.168.1.50 kids.home' "$WORK/ftl.json" || fail "new host missing"
grep -q '192.168.1.9 nas.lan' "$WORK/ftl.json" || fail "user host entry lost"
grep -q 'family.lan' "$WORK/ftl.json" && fail "old host entry left behind"
bash "$REPO/install.sh" >"$WORK/install3.out" 2>&1 || fail "third run failed"
grep -q '^SINKO_HOSTNAME=kids.home' "$ROOT/etc/sinko/config" || fail "saved hostname not kept"
SINKO_HOSTNAME=none bash "$REPO/install.sh" >/dev/null 2>&1 || fail "run without hostname failed"
grep -q 'kids.home' "$WORK/ftl.json" && fail "host entry kept after SINKO_HOSTNAME=none"
grep -q '192.168.1.9 nas.lan' "$WORK/ftl.json" || fail "user host entry lost"

echo "--- a user's own start page is backed up"
echo '<h1>mine</h1>' >"$ROOT/var/www/html/index.html"
SINKO_HOSTNAME=kids.home bash "$REPO/install.sh" >/dev/null 2>&1 || fail "run with custom page failed"
grep -q mine "$ROOT/var/www/html/index.html.pb-backup" || fail "custom page not backed up"
grep -q '192.168.1.50 kids.home' "$WORK/ftl.json" || fail "host entry not added back"

echo "--- bad input is rejected"
if SINKO_HOSTNAME='bad name;rm' bash "$REPO/install.sh" >/dev/null 2>&1; then fail "bad hostname accepted"; fi

echo "--- Pi-hole v5 (no pihole.toml) is refused, not overwritten"
mv "$ROOT/etc/pihole/pihole.toml" "$WORK/toml.bak"
if bash "$REPO/install.sh" >"$WORK/v5.out" 2>&1; then fail "v5 install accepted"; fi
grep -q 'older than v6' "$WORK/v5.out" || { cat "$WORK/v5.out"; fail "no v5 message"; }
[[ ! -e "$ROOT/etc/pihole/pihole.toml" ]] || fail "v6 settings written over a v5 install"
mv "$WORK/toml.bak" "$ROOT/etc/pihole/pihole.toml"

echo "--- the one-liner path: install.sh arrives on stdin, clones SINKO_REPO and runs itself again"
THROW="$WORK/throwaway"; mkdir -p "$THROW"
git -C "$REPO" ls-files -z --cached --others --exclude-standard | tar -C "$REPO" --null -T - -cf - | tar -x -C "$THROW"
git -C "$THROW" init -q -b master && git -C "$THROW" add -A && git -C "$THROW" -c user.name=t -c user.email=t@t commit -qm test
LOG="$ROOT/var/log/sinko-install.log"
rm -rf "$ROOT/opt/sinko/src"
SINKO_REPO="file://$THROW" SINKO_REF=master bash <"$REPO/install.sh" >"$WORK/piped.out" 2>&1 || { cat "$WORK/piped.out"; fail "piped install.sh failed"; }
grep -q "Starting the installer from the downloaded version" "$WORK/piped.out" || fail "the piped run did not start the downloaded installer"
grep -q "Installing version $(cat "$REPO/VERSION") from $ROOT/opt/sinko/src" "$WORK/piped.out" || fail "the downloaded copy was not the one installed"
grep -q "Sinko is ready" "$WORK/piped.out" || fail "the piped run did not finish"
grep -q "Starting the installer from the downloaded version" "$LOG" || fail "the install log lost the start of the piped run"
grep -q "Installing version" "$LOG" || fail "the install log lost the rest of the piped run (re-exec lost the log copy)"

echo "--- a generated parent password never reaches the install log"
set_auth() { mock_auth "$1"; }
LOG="$ROOT/var/log/sinko-install.log"
last_password() { grep 'pihole setpassword' "$WORK/calls.log" | tail -1 | awk '{print $3}'; }
printf '  Password:      OLD-OLD-OLD-OLD   (written by an older version)\n' >>"$LOG"
chmod 644 "$LOG"
set_auth false                                  # Pi-hole has no password: the installer generates one
echo "    without a terminal"
SINKO_TTY="$WORK/no-such-dir/tty" bash "$REPO/install.sh" >"$WORK/pw1.out" 2>&1 || { cat "$WORK/pw1.out"; fail "install without a password failed"; }
pw1="$(last_password)"
[[ ${#pw1} -ge 8 ]] || fail "no password was generated"
grep -qF "$pw1" "$LOG" && fail "generated password is in the install log"
grep -qF "$pw1" "$WORK/pw1.out" && fail "generated password was printed to stdout"
[[ "$(stat -c %a "$LOG")" == 600 ]] || fail "install log mode is $(stat -c %a "$LOG"), expected 600"
grep -q 'OLD-OLD-OLD-OLD' "$LOG" && fail "password line from an older version was not scrubbed"
grep -q 'Installing version' "$LOG" || fail "this run's output is missing from the log (scrubbed file swapped under tee?)"
PWFILE="$ROOT/etc/sinko/initial-password"
[[ "$(stat -c %a "$PWFILE")" == 600 ]] || fail "initial-password mode is $(stat -c %a "$PWFILE"), expected 600"
[[ "$(cat "$PWFILE")" == "$pw1" ]] || fail "initial-password does not hold the generated password"
grep -q "$PWFILE" "$WORK/pw1.out" || fail "the path of the saved password was not shown"
echo "    with a terminal"
rm -f "$PWFILE"; : >"$WORK/fake_tty"
SINKO_TTY="$WORK/fake_tty" bash "$REPO/install.sh" >"$WORK/pw2.out" 2>&1 || { cat "$WORK/pw2.out"; fail "install with a terminal failed"; }
pw2="$(last_password)"
[[ -n "$pw2" && "$pw2" != "$pw1" ]] || fail "second run did not generate a new password"
grep -qF "$pw2" "$WORK/fake_tty" || fail "password was not shown on the terminal"
grep -qF "$pw2" "$LOG" && fail "generated password is in the install log (terminal run)"
grep -qF "$pw2" "$WORK/pw2.out" && fail "generated password was printed to stdout (terminal run)"
[[ ! -e "$PWFILE" ]] || fail "password file written although a terminal was available"
[[ "$(stat -c %a "$LOG")" == 600 ]] || fail "install log mode changed"
set_auth true

echo "--- the counter question: asked once, only when somebody can answer, nothing saved when nobody was asked"
conf="$ROOT/etc/sinko/config"
forget_answer() { sed -i '/^SINKO_TELEMETRY=/d' "$conf"; }
forget_answer
bash "$REPO/install.sh" >"$WORK/t1.out" 2>&1 || { cat "$WORK/t1.out"; fail "non-interactive run failed"; }
grep -q '^SINKO_TELEMETRY=' "$conf" && fail "an answer was saved although nobody was asked"
grep -q "Count this box" "$WORK/t1.out" && fail "a non-interactive run asked the question"
echo y >"$WORK/answer"
SINKO_TTY="$WORK/answer" bash "$REPO/install.sh" >"$WORK/t2.out" 2>&1 || fail "run with SINKO_NONINTERACTIVE=1 failed"
grep -q "Count this box" "$WORK/t2.out" && fail "SINKO_NONINTERACTIVE=1 did not stop the question"
grep -q '^SINKO_TELEMETRY=' "$conf" && fail "an answer was saved although SINKO_NONINTERACTIVE=1"
echo "    SINKO_TELEMETRY=1|0 answers without asking and is saved"
SINKO_TELEMETRY=1 bash "$REPO/install.sh" >"$WORK/t3.out" 2>&1 || fail "run with SINKO_TELEMETRY=1 failed"
grep -q '^SINKO_TELEMETRY=1$' "$conf" || fail "SINKO_TELEMETRY=1 was not saved"
grep -q "Count this box" "$WORK/t3.out" && fail "asked although SINKO_TELEMETRY was given"
echo "    an answer already saved survives later non-interactive runs (updates)"
bash "$REPO/install.sh" >"$WORK/t4.out" 2>&1 || fail "update run failed"
grep -q '^SINKO_TELEMETRY=1$' "$conf" || fail "the saved answer was lost by an update"
SINKO_TELEMETRY=0 bash "$REPO/install.sh" >"$WORK/t5.out" 2>&1 || fail "run with SINKO_TELEMETRY=0 failed"
grep -q '^SINKO_TELEMETRY=0$' "$conf" || fail "SINKO_TELEMETRY=0 did not replace the answer"
if SINKO_TELEMETRY=maybe bash "$REPO/install.sh" >"$WORK/t6.out" 2>&1; then fail "SINKO_TELEMETRY=maybe accepted"; fi
grep -q "SINKO_TELEMETRY must be 1" "$WORK/t6.out" || fail "no message for a bad SINKO_TELEMETRY"
echo "    interactive: asked with a description and a pointer to docs/privacy.md; default is no"
forget_answer
echo y >"$WORK/answer"
env -u SINKO_NONINTERACTIVE SINKO_TTY="$WORK/answer" bash "$REPO/install.sh" >"$WORK/t7.out" 2>&1 || { cat "$WORK/t7.out"; fail "interactive run failed"; }
grep -q "Count this box in the anonymous number of Sinko boxes online? \[y/N\]" "$WORK/t7.out" || fail "the question was not asked"
grep -q "docs/privacy.md" "$WORK/t7.out" || fail "no pointer to docs/privacy.md"
grep -q '^SINKO_TELEMETRY=1$' "$conf" || fail "the answer yes was not saved"
env -u SINKO_NONINTERACTIVE SINKO_TTY="$WORK/answer" bash "$REPO/install.sh" >"$WORK/t8.out" 2>&1 || fail "second interactive run failed"
grep -q "Count this box" "$WORK/t8.out" && fail "asked again although the box was already answered"
forget_answer
echo >"$WORK/answer"                                   # just Enter
env -u SINKO_NONINTERACTIVE SINKO_TTY="$WORK/answer" bash "$REPO/install.sh" >"$WORK/t9.out" 2>&1 || fail "run answering with Enter failed"
grep -q '^SINKO_TELEMETRY=0$' "$conf" || fail "Enter (the default) did not save no"
forget_answer
: >"$WORK/answer"                                      # the terminal gives no answer at all (Ctrl-D)
env -u SINKO_NONINTERACTIVE SINKO_TTY="$WORK/answer" bash "$REPO/install.sh" >"$WORK/t10.out" 2>&1 || fail "run without an answer failed"
grep -q '^SINKO_TELEMETRY=' "$conf" && fail "a missing answer was saved as an answer"
grep -q "No answer" "$WORK/t10.out" || fail "no hint that the page asks later"
echo "    a counter address that was set is kept, and must be a web address"
SINKO_TELEMETRY_URL=https://counter.example.org bash "$REPO/install.sh" >"$WORK/t11.out" 2>&1 || fail "run with SINKO_TELEMETRY_URL failed"
bash "$REPO/install.sh" >"$WORK/t12.out" 2>&1 || fail "update failed"
grep -q '^SINKO_TELEMETRY_URL=https://counter.example.org$' "$conf" || fail "SINKO_TELEMETRY_URL was not kept"
if SINKO_TELEMETRY_URL='javascript:alert(1)' bash "$REPO/install.sh" >"$WORK/t13.out" 2>&1; then fail "a bad SINKO_TELEMETRY_URL was accepted"; fi
sed -i '/^SINKO_TELEMETRY_URL=/d' "$conf"

echo "--- packages that are already there are left alone, and the switches stay where they were put"
: >"$WORK/calls.log"
rm -f "$ua"
bash "$REPO/install.sh" >"$WORK/e1.out" 2>&1 || { cat "$WORK/e1.out"; fail "run with the packages present failed"; }
grep -q 'apt-get install' "$WORK/calls.log" && fail "installed a package that was already installed"
[[ ! -e "$ua" ]] || fail "rewrote the settings of an unattended-upgrades that was already installed"
grep -q "already installed (left as it is)" "$WORK/e1.out" || fail "no note about avahi"
grep -q "already set up (left as they are)" "$WORK/e1.out" || fail "no note about the automatic updates"
echo "    avahi that is not running: no .local line in the summary"
rm -f "$WORK/units/avahi-daemon.service.active"
bash "$REPO/install.sh" >"$WORK/e1b.out" 2>&1 || fail "run with avahi stopped failed"
grep -q '\.local/' "$WORK/e1b.out" && fail "the summary shows a .local name although avahi is not running"
echo "    SINKO_MDNS=0 and SINKO_OS_UPDATES=0 install nothing and are remembered"
sed -i '/^avahi-daemon$/d; /^unattended-upgrades$/d' "$WORK/dpkg-installed"
: >"$WORK/calls.log"
SINKO_MDNS=0 SINKO_OS_UPDATES=0 bash "$REPO/install.sh" >"$WORK/e2.out" 2>&1 || fail "run with both switches off failed"
bash "$REPO/install.sh" >"$WORK/e3.out" 2>&1 || fail "run after switching off failed"
grep -q 'apt-get install' "$WORK/calls.log" && fail "installed a package although the switch is off (or forgot it)"
grep -q '^SINKO_MDNS=0$' "$ROOT/etc/sinko/config" || fail "SINKO_MDNS=0 was not remembered"
grep -q '^SINKO_OS_UPDATES=0$' "$ROOT/etc/sinko/config" || fail "SINKO_OS_UPDATES=0 was not remembered"
[[ ! -e "$ua" ]] || fail "settings written although the switch is off"
if SINKO_MDNS=maybe bash "$REPO/install.sh" >"$WORK/e4.out" 2>&1; then fail "SINKO_MDNS=maybe accepted"; fi
grep -q "SINKO_MDNS must be 1 or 0" "$WORK/e4.out" || fail "no message for a bad SINKO_MDNS"
echo "    when apt cannot install them the installation still succeeds, with a warning"
touch "$WORK/apt-fail"
SINKO_MDNS=1 SINKO_OS_UPDATES=1 bash "$REPO/install.sh" >"$WORK/e5.out" 2>&1 || { cat "$WORK/e5.out"; fail "a failing apt broke the installation"; }
grep -q "Could not install avahi-daemon" "$WORK/e5.out" || fail "no warning about avahi"
grep -q "Could not install unattended-upgrades" "$WORK/e5.out" || fail "no warning about unattended-upgrades"
grep -q "Sinko is ready" "$WORK/e5.out" || fail "the installation did not finish"
[[ ! -e "$ua" ]] || fail "settings written although the package is not installed"
rm -f "$WORK/apt-fail"
echo "    Ubuntu has its own security origin"
printf 'PRETTY_NAME="Ubuntu 24.04"\nID=ubuntu\nID_LIKE=debian\n' >"$WORK/os-release"
bash "$REPO/install.sh" >"$WORK/e6.out" 2>&1 || fail "run on Ubuntu failed"
# shellcheck disable=SC2016  # apt's macro, literal on purpose
grep -qF 'origin=Ubuntu,codename=${distro_codename}-security,label=Ubuntu' "$ua" || { cat "$ua"; fail "no Ubuntu security origin"; }
grep -q 'origin=Debian' "$ua" && fail "Debian origins on Ubuntu"
grep -q 'http://kidsbox.local/' "$WORK/e6.out" || fail "avahi was installed again but the .local name is not shown"

echo "--- uninstall"
# Things a running box has: runtime data, the first-start unit, the shortcut an earlier migration left, a start page of the user's own.
printf '%s\n' 0123456789abcdef0123456789abcdef >"$ROOT/var/lib/sinko/install-id"
echo '{"update": 1}' >"$ROOT/var/lib/sinko/handled.json"
touch "$ROOT/var/lib/sinko/firstboot" "$WORK/units/sinko-firstboot.service.enabled"
ln -s "$ROOT/opt/sinko/bin/sinko" "$ROOT/usr/local/bin/pihole-bahrain"
echo "    declining the question changes nothing"
echo n >"$WORK/answer"
env -u SINKO_NONINTERACTIVE SINKO_TTY="$WORK/answer" bash "$ROOT/opt/sinko/uninstall.sh" >"$WORK/un0.out" 2>&1 || fail "declined uninstall failed"
grep -q "Nothing changed" "$WORK/un0.out" || fail "no confirmation that nothing changed"
[[ -x "$ROOT/opt/sinko/bin/sinko" && -d "$ROOT/var/lib/sinko" ]] || fail "uninstall removed files although the answer was no"
: >"$WORK/calls.log"
bash "$ROOT/opt/sinko/uninstall.sh" >"$WORK/un.out" 2>&1 || { cat "$WORK/un.out"; fail "uninstall failed"; }
[[ ! -e "$ROOT/opt/sinko" && ! -e "$ROOT/var/www/html/pb" && ! -e "$ROOT/etc/sinko" ]] || fail "files left behind"
[[ ! -e "$ROOT/var/lib/sinko" ]] || fail "the runtime data folder (install-id, update results, first-start flag) was left behind"
[[ ! -e "$ROOT/etc/systemd/system/sinko-firstboot.service" && ! -e "$ROOT/etc/systemd/system/sinko.service" && ! -e "$ROOT/etc/systemd/system/sinko-lists.timer" ]] || fail "a unit file was left behind"
[[ ! -e "$WORK/units/sinko-firstboot.service.enabled" && ! -e "$WORK/units/sinko.service.enabled" ]] || fail "a unit was left enabled"
grep -q 'systemctl disable --now sinko.service sinko-lists.timer sinko-firstboot.service' "$WORK/calls.log" || fail "the first-start unit was not disabled"
[[ ! -e "$ROOT/usr/local/bin/pihole-bahrain" && ! -L "$ROOT/usr/local/bin/pihole-bahrain" ]] || fail "the old-command shortcut was left behind"
grep -q 'apt-get \(remove\|purge\)' "$WORK/calls.log" && fail "uninstall removed a package (avahi-daemon and unattended-upgrades stay)"
grep -qx 'avahi-daemon' "$WORK/dpkg-installed" || fail "avahi-daemon is gone"
[[ -f "$ua" ]] || fail "the security-update settings were removed: security updates would stop with Sinko"
grep -q mine "$ROOT/var/www/html/index.html" || fail "custom page not restored"
grep -q '"webserver.serve_all": "false"' "$WORK/ftl.json" || fail "serve_all not reverted"
grep -q 'kids.home' "$WORK/ftl.json" && fail "host entry not removed"
grep -q 'nas.lan' "$WORK/ftl.json" || fail "user host entry removed"
echo "    a link of somebody else's that has the old command's name stays"
ln -s /bin/true "$ROOT/usr/local/bin/pihole-bahrain"
mkdir -p "$ROOT/opt/sinko/bin"
printf '#!/bin/sh\nexit 0\n' >"$ROOT/opt/sinko/bin/sinko"; chmod +x "$ROOT/opt/sinko/bin/sinko"
bash "$REPO/uninstall.sh" >"$WORK/un2.out" 2>&1 || { cat "$WORK/un2.out"; fail "uninstall of a partial installation failed"; }
[[ -L "$ROOT/usr/local/bin/pihole-bahrain" ]] || fail "uninstall removed a link that is not ours"
[[ ! -e "$ROOT/opt/sinko" ]] || fail "the partial installation was not removed"
echo "installer tests passed"
