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

echo "--- uninstall"
bash "$ROOT/opt/sinko/uninstall.sh" >"$WORK/un.out" 2>&1 || { cat "$WORK/un.out"; fail "uninstall failed"; }
[[ ! -e "$ROOT/opt/sinko" && ! -e "$ROOT/var/www/html/pb" && ! -e "$ROOT/etc/sinko" ]] || fail "files left behind"
grep -q mine "$ROOT/var/www/html/index.html" || fail "custom page not restored"
grep -q '"webserver.serve_all": "false"' "$WORK/ftl.json" || fail "serve_all not reverted"
grep -q 'kids.home' "$WORK/ftl.json" && fail "host entry not removed"
grep -q 'nas.lan' "$WORK/ftl.json" || fail "user host entry removed"
echo "installer tests passed"
