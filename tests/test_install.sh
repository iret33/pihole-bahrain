#!/usr/bin/env bash
# End-to-end test of install.sh / uninstall.sh without a real Pi-hole.
# System commands (ip, systemctl, pihole, pihole-FTL, …) are replaced by stubs,
# the Pi-hole API by tests/mock_pihole.py, and files go under a temp "root".
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(dirname "$HERE")"
WORK="$(mktemp -d)"
ROOT="$WORK/root"
STUBS="$WORK/stubs"
PORT="${PORT:-18080}"
mkdir -p "$ROOT/etc/pihole" "$ROOT/var/www/html" "$STUBS"
cleanup() { [[ -n "${MOCK_PID:-}" ]] && kill "$MOCK_PID" 2>/dev/null; [[ -n "${KEEP:-}" ]] || rm -rf "$WORK"; }
trap cleanup EXIT
fail() { echo "FAIL: $*" >&2; exit 1; }

# ---------- stubs ----------
cat >"$STUBS/ip" <<'EOF'
#!/usr/bin/env bash
case "$*" in
  *"route show default"*) echo "default via 192.168.1.1 dev eth0 proto dhcp src 192.168.1.50 metric 100" ;;
  *"addr show dev eth0"*) echo "2: eth0    inet 192.168.1.50/24 brd 192.168.1.255 scope global dynamic eth0" ;;
esac
EOF
cat >"$STUBS/dpkg" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
cat >"$STUBS/systemctl" <<EOF
#!/usr/bin/env bash
echo "systemctl \$*" >>"$WORK/calls.log"
exit 0
EOF
cat >"$STUBS/timedatectl" <<EOF
#!/usr/bin/env bash
if [[ "\$1" == show ]]; then cat "$WORK/tz" 2>/dev/null || echo Etc/UTC; exit 0; fi
if [[ "\$1" == set-timezone ]]; then echo "\$2" >"$WORK/tz"; fi
EOF
# pihole-FTL --config [-q] key [value]  — backed by a JSON file
cat >"$STUBS/pihole-FTL" <<EOF
#!/usr/bin/env python3
import json, os, sys
db = "$WORK/ftl.json"
conf = json.load(open(db)) if os.path.exists(db) else {
    "webserver.serve_all": "false", "webserver.api.cli_pw": "true",
    "webserver.paths.webroot": "$ROOT/var/www/html", "webserver.port": "80o,443os",
    "dns.hosts": "[ 192.168.1.9 nas.lan ]", "resolver.macNames": "true", "dns.blocking.active": "true",
    "files.log.ftl": "$WORK/FTL.log"}
args = [a for a in sys.argv[1:] if a != "-q"]
assert args[0] == "--config", args
if len(args) == 2:
    print(conf.get(args[1], ""))
    # Like FTL: with -q, a true/false setting is also the exit status.
    sys.exit(1 if "-q" in sys.argv and conf.get(args[1]) == "false" else 0)
else:
    val = args[2]
    if args[1] == "dns.hosts":
        items = json.loads(val)
        val = "[ " + ", ".join(items) + " ]" if items else "[]"
    conf[args[1]] = val
    json.dump(conf, open(db, "w"))
EOF
cat >"$STUBS/pihole" <<EOF
#!/usr/bin/env bash
echo "pihole \$*" >>"$WORK/calls.log"
EOF
# The installer's internet check probes github.com; everything else is local.
cat >"$STUBS/curl" <<EOF
#!/usr/bin/env bash
[[ "\$*" == *" https://github.com" ]] && exit 0
exec "$(command -v curl)" "\$@"
EOF
chmod +x "$STUBS"/*

# ---------- mock API ----------
python3 - "$PORT" "$REPO" <<'EOF' &
import sys, time
sys.path.insert(0, sys.argv[2] + "/tests")
import mock_pihole
httpd, store = mock_pihole.serve(int(sys.argv[1]))
while True:
    time.sleep(3600)
EOF
MOCK_PID=$!
echo test >"$WORK/cli_pw"
for _ in $(seq 50); do curl -s -o /dev/null "http://127.0.0.1:$PORT/api/auth" && break; sleep 0.1; done

export PATH="$STUBS:$PATH" PB_ROOT="$ROOT" PB_NONINTERACTIVE=1
export PB_API_URL="http://127.0.0.1:$PORT" PB_CLI_PW_FILE="$WORK/cli_pw"
export PB_APP_DIR="$ROOT/opt/pihole-bahrain" PB_CONFIG_FILE="$ROOT/etc/pihole-bahrain/config"
touch "$ROOT/etc/pihole/pihole.toml"
command -v pihole-FTL >/dev/null || fail "stub missing"

# Pretend an old 1.x page is installed
mkdir -p "$ROOT/var/www/html/parental"
echo '<script src="/parental/app.js"></script>' >"$ROOT/var/www/html/index.html"

echo "--- install"
bash "$REPO/install.sh" >"$WORK/install.out" 2>&1 || { cat "$WORK/install.out"; fail "install.sh exited non-zero"; }
out="$(cat "$WORK/install.out")"
grep -q "Family Internet is ready" <<<"$out" || { echo "$out"; fail "no summary"; }
grep -q "Pi-hole v6 is already installed" <<<"$out" || fail "existing Pi-hole v6 (serve_all off) not recognised"
grep -q "ok    webserver.serve_all is true" <<<"$out" || fail "doctor did not confirm serve_all"
[[ -f "$ROOT/var/www/html/index.html" ]] || fail "index.html missing"
grep -q 'content="pihole-bahrain"' "$ROOT/var/www/html/index.html" || fail "wrong index.html"
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
[[ -L "$ROOT/usr/local/bin/pihole-bahrain" ]] || fail "cli link missing"
grep -q '^PB_HOSTNAME=family.lan' "$ROOT/etc/pihole-bahrain/config" || fail "config not written"
grep -q '"webserver.serve_all": "true"' "$WORK/ftl.json" || fail "serve_all not enabled"
grep -q '192.168.1.9 nas.lan, 192.168.1.50 family.lan' "$WORK/ftl.json" || { cat "$WORK/ftl.json"; fail "dns.hosts not merged"; }
grep -q '/usr/local/bin/pihole -g\|pihole -g' "$ROOT/etc/systemd/system/pihole-bahrain-lists.service" || fail "unit not rendered"
grep -q 'systemctl restart pihole-bahrain.service' "$WORK/calls.log" || fail "service not started"
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
[[ "$lists" == "20 https://raw.githubusercontent.com/iret33/pihole-bahrain/master/lists/"* ]] || fail "lists: $lists"

"$ROOT/usr/local/bin/pihole-bahrain" status | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d["version"] and d["devices"] == []' || fail "status output is not the expected JSON"
"$ROOT/usr/local/bin/pihole-bahrain" use-mac --dry-run | grep -q "Nothing to convert" || fail "use-mac did not run"
# diagnose and watch through the real CLI; the stubbed pihole-FTL has no sqlite shell, so the databases are "unreadable"
"$ROOT/usr/local/bin/pihole-bahrain" diagnose youtube >"$WORK/diagnose.out" 2>&1 || { cat "$WORK/diagnose.out"; fail "diagnose failed"; }
grep -q "Findings, most likely first" "$WORK/diagnose.out" || { cat "$WORK/diagnose.out"; fail "diagnose printed no findings"; }
grep -q "Could not read" "$WORK/diagnose.out" || fail "diagnose did not report the unreadable database"
if "$ROOT/usr/local/bin/pihole-bahrain" diagnose no-such-service >"$WORK/diagnose2.out" 2>&1; then fail "diagnose accepted an unknown service"; fi
grep -q "unknown service" "$WORK/diagnose2.out" || fail "no message for an unknown service"
: >"$WORK/pihole.log"
PB_DNSMASQ_LOG="$WORK/pihole.log" "$ROOT/usr/local/bin/pihole-bahrain" watch 0.02 >"$WORK/watch.out" 2>&1 || { cat "$WORK/watch.out"; fail "watch failed"; }
grep -q "not using this box for DNS" "$WORK/watch.out" || { cat "$WORK/watch.out"; fail "watch printed no summary"; }

echo "--- re-run (update) keeps settings, hostname change replaces host entry"
PB_HOSTNAME=kids.home bash "$REPO/install.sh" >"$WORK/install2.out" 2>&1 || { cat "$WORK/install2.out"; fail "second run failed"; }
grep -q '^PB_HOSTNAME=kids.home' "$ROOT/etc/pihole-bahrain/config" || fail "hostname not updated"
grep -q '192.168.1.50 kids.home' "$WORK/ftl.json" || fail "new host missing"
grep -q '192.168.1.9 nas.lan' "$WORK/ftl.json" || fail "user host entry lost"
grep -q 'family.lan' "$WORK/ftl.json" && fail "old host entry left behind"
bash "$REPO/install.sh" >"$WORK/install3.out" 2>&1 || fail "third run failed"
grep -q '^PB_HOSTNAME=kids.home' "$ROOT/etc/pihole-bahrain/config" || fail "saved hostname not kept"
PB_HOSTNAME=none bash "$REPO/install.sh" >/dev/null 2>&1 || fail "run without hostname failed"
grep -q 'kids.home' "$WORK/ftl.json" && fail "host entry kept after PB_HOSTNAME=none"
grep -q '192.168.1.9 nas.lan' "$WORK/ftl.json" || fail "user host entry lost"

echo "--- a user's own start page is backed up"
echo '<h1>mine</h1>' >"$ROOT/var/www/html/index.html"
PB_HOSTNAME=kids.home bash "$REPO/install.sh" >/dev/null 2>&1 || fail "run with custom page failed"
grep -q mine "$ROOT/var/www/html/index.html.pb-backup" || fail "custom page not backed up"
grep -q '192.168.1.50 kids.home' "$WORK/ftl.json" || fail "host entry not added back"

echo "--- bad input is rejected"
if PB_HOSTNAME='bad name;rm' bash "$REPO/install.sh" >/dev/null 2>&1; then fail "bad hostname accepted"; fi

echo "--- Pi-hole v5 (no pihole.toml) is refused, not overwritten"
mv "$ROOT/etc/pihole/pihole.toml" "$WORK/toml.bak"
if bash "$REPO/install.sh" >"$WORK/v5.out" 2>&1; then fail "v5 install accepted"; fi
grep -q 'older than v6' "$WORK/v5.out" || { cat "$WORK/v5.out"; fail "no v5 message"; }
[[ ! -e "$ROOT/etc/pihole/pihole.toml" ]] || fail "v6 settings written over a v5 install"
mv "$WORK/toml.bak" "$ROOT/etc/pihole/pihole.toml"

echo "--- the one-liner path: install.sh arrives on stdin, clones PB_REPO and runs itself again"
THROW="$WORK/throwaway"; mkdir -p "$THROW"
git -C "$REPO" ls-files -z | tar -C "$REPO" --null -T - -cf - | tar -x -C "$THROW"
git -C "$THROW" init -q -b master && git -C "$THROW" add -A && git -C "$THROW" -c user.name=t -c user.email=t@t commit -qm test
LOG="$ROOT/var/log/pihole-bahrain-install.log"
rm -rf "$ROOT/opt/pihole-bahrain/src"
PB_REPO="file://$THROW" bash <"$REPO/install.sh" >"$WORK/piped.out" 2>&1 || { cat "$WORK/piped.out"; fail "piped install.sh failed"; }
grep -q "Starting the installer from the downloaded version" "$WORK/piped.out" || fail "the piped run did not start the downloaded installer"
grep -q "Installing version $(cat "$REPO/VERSION") from $ROOT/opt/pihole-bahrain/src" "$WORK/piped.out" || fail "the downloaded copy was not the one installed"
grep -q "Family Internet is ready" "$WORK/piped.out" || fail "the piped run did not finish"
grep -q "Starting the installer from the downloaded version" "$LOG" || fail "the install log lost the start of the piped run"
grep -q "Installing version" "$LOG" || fail "the install log lost the rest of the piped run (re-exec lost the log copy)"

echo "--- a generated parent password never reaches the install log"
set_auth() { curl -s -X POST "http://127.0.0.1:$PORT/__mock__/require_auth" -d "{\"value\": $1}" >/dev/null; }
LOG="$ROOT/var/log/pihole-bahrain-install.log"
last_password() { grep 'pihole setpassword' "$WORK/calls.log" | tail -1 | awk '{print $3}'; }
printf '  Password:      OLD-OLD-OLD-OLD   (written by an older version)\n' >>"$LOG"
chmod 644 "$LOG"
set_auth false                                  # Pi-hole has no password: the installer generates one
echo "    without a terminal"
PB_TTY="$WORK/no-such-dir/tty" bash "$REPO/install.sh" >"$WORK/pw1.out" 2>&1 || { cat "$WORK/pw1.out"; fail "install without a password failed"; }
pw1="$(last_password)"
[[ ${#pw1} -ge 8 ]] || fail "no password was generated"
grep -qF "$pw1" "$LOG" && fail "generated password is in the install log"
grep -qF "$pw1" "$WORK/pw1.out" && fail "generated password was printed to stdout"
[[ "$(stat -c %a "$LOG")" == 600 ]] || fail "install log mode is $(stat -c %a "$LOG"), expected 600"
grep -q 'OLD-OLD-OLD-OLD' "$LOG" && fail "password line from an older version was not scrubbed"
grep -q 'Installing version' "$LOG" || fail "this run's output is missing from the log (scrubbed file swapped under tee?)"
PWFILE="$ROOT/etc/pihole-bahrain/initial-password"
[[ "$(stat -c %a "$PWFILE")" == 600 ]] || fail "initial-password mode is $(stat -c %a "$PWFILE"), expected 600"
[[ "$(cat "$PWFILE")" == "$pw1" ]] || fail "initial-password does not hold the generated password"
grep -q "$PWFILE" "$WORK/pw1.out" || fail "the path of the saved password was not shown"
echo "    with a terminal"
rm -f "$PWFILE"; : >"$WORK/fake_tty"
PB_TTY="$WORK/fake_tty" bash "$REPO/install.sh" >"$WORK/pw2.out" 2>&1 || { cat "$WORK/pw2.out"; fail "install with a terminal failed"; }
pw2="$(last_password)"
[[ -n "$pw2" && "$pw2" != "$pw1" ]] || fail "second run did not generate a new password"
grep -qF "$pw2" "$WORK/fake_tty" || fail "password was not shown on the terminal"
grep -qF "$pw2" "$LOG" && fail "generated password is in the install log (terminal run)"
grep -qF "$pw2" "$WORK/pw2.out" && fail "generated password was printed to stdout (terminal run)"
[[ ! -e "$PWFILE" ]] || fail "password file written although a terminal was available"
[[ "$(stat -c %a "$LOG")" == 600 ]] || fail "install log mode changed"
set_auth true

echo "--- uninstall"
bash "$ROOT/opt/pihole-bahrain/uninstall.sh" >"$WORK/un.out" 2>&1 || { cat "$WORK/un.out"; fail "uninstall failed"; }
[[ ! -e "$ROOT/opt/pihole-bahrain" && ! -e "$ROOT/var/www/html/pb" && ! -e "$ROOT/etc/pihole-bahrain" ]] || fail "files left behind"
grep -q mine "$ROOT/var/www/html/index.html" || fail "custom page not restored"
grep -q '"webserver.serve_all": "false"' "$WORK/ftl.json" || fail "serve_all not reverted"
grep -q 'kids.home' "$WORK/ftl.json" && fail "host entry not removed"
grep -q 'nas.lan' "$WORK/ftl.json" || fail "user host entry removed"
echo "installer tests passed"
