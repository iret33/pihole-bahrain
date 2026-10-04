#!/usr/bin/env bash
# Shared fixtures for the shell tests (test_install.sh, test_fetch.sh, test_migrate.sh, test_seal.sh, ...).
# Source it, then call fake_system_init. It gives a test:
#   - a fake root ($ROOT) that the scripts under test use through SINKO_ROOT,
#   - stubs in front of PATH for every command that would change the machine (systemctl, apt-get, passwd, ssh-keygen,
#     hostnamectl, journalctl, dd, ...). The tests run as root, so a command that is not stubbed would really run,
#   - the mock Pi-hole API (tests/mock_pihole.py) on $PORT,
#   - a guard that proves the real system was not touched (guard_snapshot / guard_verify).
# shellcheck shell=bash

LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(dirname "$LIB_DIR")"

fail() { echo "FAIL: $*" >&2; exit 1; }

fake_system_init() {
  WORK="$(mktemp -d -t sinko-test.XXXXXX)"
  ROOT="$WORK/root"
  STUBS="$WORK/stubs"
  PORT="${PORT:-18080}"
  mkdir -p "$ROOT/etc/pihole" "$ROOT/etc/ssh" "$ROOT/var/www/html" "$ROOT/var/log" "$STUBS" "$WORK/units"
  export WORK ROOT PORT
  : >"$WORK/calls.log"
  printf '%s\n' git curl ca-certificates python3 iproute2 >"$WORK/dpkg-installed"
  touch "$WORK/units/pihole-FTL.service.active" "$WORK/units/pihole-FTL.service.enabled"      # a Pi-hole that was installed: running and enabled
  trap fake_system_cleanup EXIT
  guard_snapshot
  make_stubs
  start_mock
  export PATH="$STUBS:$PATH" SINKO_ROOT="$ROOT" SINKO_NONINTERACTIVE=1
  export SINKO_API_URL="http://127.0.0.1:$PORT" SINKO_CLI_PW_FILE="$WORK/cli_pw"
  export SINKO_APP_DIR="$ROOT/opt/sinko" SINKO_CONFIG_FILE="$ROOT/etc/sinko/config" SINKO_STATE_DIR="$ROOT/var/lib/sinko"
  touch "$ROOT/etc/pihole/pihole.toml"
  command -v pihole-FTL >/dev/null || fail "stub missing"
}

fake_system_cleanup() {
  local rc=$?
  [[ -n "${MOCK_PID:-}" ]] && kill "$MOCK_PID" 2>/dev/null
  [[ -n "${HTTP_PID:-}" ]] && kill "$HTTP_PID" 2>/dev/null
  if [[ -n "${GUARD_BEFORE:-}" && "$(guard_state)" != "$GUARD_BEFORE" ]]; then
    echo "FAIL: the test changed the real system (hostname, ssh, machine-id, shadow, units, /opt/sinko, ...)" >&2
    rc=1
  fi
  [[ -n "${KEEP:-}" ]] || rm -rf "$WORK"
  exit "$rc"
}

# What a script that escaped its fake root would have touched. Compared before and after the test.
guard_state() {
  {
    sha256sum /etc/hostname /etc/hosts /etc/machine-id /var/lib/dbus/machine-id 2>&1 || true
    { grep '^root:' /etc/shadow 2>/dev/null || true; } | sha256sum
    { ls -la /etc/ssh /etc/ssh/sshd_config.d /etc/apt/apt.conf.d /etc/systemd/system /etc/systemd/system.conf.d \
        /opt /etc/sinko /var/lib/sinko /usr/local/bin /var/log/sinko-install.log /root/.ssh 2>&1 || true; } | grep -v '^total' || true
  } | sha256sum
}
guard_snapshot() { GUARD_BEFORE="$(guard_state)"; }

make_stubs() {
  cat >"$STUBS/ip" <<'EOF'
#!/usr/bin/env bash
[[ -e "$WORK/net-down" ]] && exit 0
addr="$(cat "$WORK/ipaddr" 2>/dev/null || echo 192.168.1.50)"
# $WORK/ip-static: the address is a fixed one (no "dynamic" in the line), not one from the router's DHCP.
dyn="dynamic "; [[ -e "$WORK/ip-static" ]] && dyn=""
case "$*" in
  *"route show default"*) echo "default via 192.168.1.1 dev eth0 proto dhcp src $addr metric 100" ;;
  *"addr show dev eth0"*) echo "2: eth0    inet $addr/24 brd 192.168.1.255 scope global ${dyn}eth0" ;;
esac
EOF
  # dpkg -s PKG answers from a list the apt-get stub extends.
  cat >"$STUBS/dpkg" <<'EOF'
#!/usr/bin/env bash
[[ "$1" == -s ]] || exit 0
grep -qxF "$2" "$WORK/dpkg-installed" 2>/dev/null
EOF
  cat >"$STUBS/apt-get" <<'EOF'
#!/usr/bin/env bash
echo "apt-get $*" >>"$WORK/calls.log"
[[ -z "${SINKO_PASSWORD+x}${PB_PASSWORD+x}" ]] || echo "apt-get $*" >>"$WORK/password-in-env"
if [[ -e "$WORK/apt-fail" ]]; then echo "E: apt is not available (stub)" >&2; exit 100; fi
# $WORK/apt-update-fail: "apt-get update" fails (a package source is unreachable), everything else works from the lists apt has.
if [[ " $* " == *" update "* && -e "$WORK/apt-update-fail" ]]; then echo "E: Failed to fetch (stub)" >&2; exit 100; fi
if [[ " $* " == *" install "* ]]; then
  for a in "$@"; do
    case "$a" in -*|install) ;; *) echo "$a" >>"$WORK/dpkg-installed" ;; esac
  done
fi
exit 0
EOF
  # apt-cache policy PKG: every package has a candidate, unless $WORK/apt-nocandidate says the sources have none.
  cat >"$STUBS/apt-cache" <<'EOF'
#!/usr/bin/env bash
echo "apt-cache $*" >>"$WORK/calls.log"
[[ "$1" == policy ]] || exit 0
shift
for p in "$@"; do
  echo "$p:"
  echo "  Installed: (none)"
  if [[ -e "$WORK/apt-nocandidate" ]]; then echo "  Candidate: (none)"; else echo "  Candidate: 1.0-1"; fi
done
EOF
  # python3: the installer asks which version it runs on; $WORK/py-old makes the answer 3.8. Everything else is the real one.
  cat >"$STUBS/python3" <<EOF
#!/usr/bin/env bash
if [[ -e "\$WORK/py-old" && "\$*" == *"sys.version_info[:2]"* ]]; then echo 3.8; exit 0; fi
exec "$(command -v python3)" "\$@"
EOF
  # systemctl keeps "active" and "enabled" as marker files, so tests can ask what the scripts did.
  cat >"$STUBS/systemctl" <<'EOF'
#!/usr/bin/env bash
echo "systemctl $*" >>"$WORK/calls.log"
# Every program the installer starts must find the parent password out of its environment (see test_install.sh).
[[ -z "${SINKO_PASSWORD+x}${PB_PASSWORD+x}" ]] || echo "systemctl $*" >>"$WORK/password-in-env"
mkdir -p "$WORK/units"
cmd=""; units=(); now=0
for a in "$@"; do
  case "$a" in
    --now) now=1 ;;
    -*) ;;
    *) if [[ -z "$cmd" ]]; then cmd="$a"; else units+=("$a"); fi ;;
  esac
done
norm() { case "$1" in *.*) echo "$1" ;; *) echo "$1.service" ;; esac; }
case "$cmd" in
  is-active)  u="$(norm "${units[0]}")"; if [[ -e "$WORK/units/$u.active" ]]; then echo active; exit 0; else echo inactive; exit 3; fi ;;
  is-enabled) u="$(norm "${units[0]}")"; if [[ -e "$WORK/units/$u.enabled" ]]; then echo enabled; exit 0; else echo disabled; exit 1; fi ;;
  start|restart) for u in "${units[@]}"; do touch "$WORK/units/$(norm "$u").active"; done
                 # $WORK/ftl-makes-cert: Pi-hole's FTL writes its HTTPS key and certificate when it starts without them.
                 if [[ -e "$WORK/ftl-makes-cert" && " ${units[*]} " == *" pihole-FTL.service "* ]]; then
                   mkdir -p "$ROOT/etc/pihole"
                   printf -- '-----BEGIN EC PRIVATE KEY-----\n%s\n-----END EC PRIVATE KEY-----\n-----BEGIN CERTIFICATE-----\ncert\n-----END CERTIFICATE-----\n' "$RANDOM$RANDOM" >"$ROOT/etc/pihole/tls.pem"
                   echo "certificate" >"$ROOT/etc/pihole/tls.crt"
                 fi ;;
  stop) # $WORK/ftl-stop-fails: Pi-hole's FTL cannot be stopped.
        if [[ -e "$WORK/ftl-stop-fails" && " ${units[*]} " == *" pihole-FTL.service "* ]]; then exit 1; fi
        for u in "${units[@]}"; do rm -f "$WORK/units/$(norm "$u").active"; done ;;
  enable) for u in "${units[@]}"; do touch "$WORK/units/$(norm "$u").enabled"; (( now )) && touch "$WORK/units/$(norm "$u").active"; done ;;
  disable) for u in "${units[@]}"; do rm -f "$WORK/units/$(norm "$u").enabled"; (( now )) && rm -f "$WORK/units/$(norm "$u").active"; done ;;
esac
exit 0
EOF
  cat >"$STUBS/timedatectl" <<'EOF'
#!/usr/bin/env bash
if [[ "$1" == show ]]; then cat "$WORK/tz" 2>/dev/null || echo Etc/UTC; exit 0; fi
if [[ "$1" == set-timezone ]]; then echo "$2" >"$WORK/tz"; fi
EOF
  # pihole-FTL --config [-q] key [value]  — backed by a JSON file. Setting the password also tells the mock API.
  cat >"$STUBS/pihole-FTL" <<'EOF'
#!/usr/bin/env python3
import json, os, sys, urllib.request
work, root = os.environ["WORK"], os.environ["ROOT"]
db = work + "/ftl.json"
conf = json.load(open(db)) if os.path.exists(db) else {
    "webserver.serve_all": "false", "webserver.api.cli_pw": "true",
    "webserver.paths.webroot": root + "/var/www/html", "webserver.port": "80o,443os",
    "dns.hosts": "[ 192.168.1.9 nas.lan ]", "resolver.macNames": "true", "dns.blocking.active": "true",
    "files.log.ftl": work + "/FTL.log", "files.database": root + "/etc/pihole/pihole-FTL.db"}
args = [a for a in sys.argv[1:] if a != "-q"]
assert args[0] == "--config", args
if os.path.exists(work + "/ftl-fail"):
    sys.exit(1)
if len(args) == 2:
    value = conf.get(args[1], "")
    if args[1] == "webserver.api.totp_secret":          # like FTL: a write-only secret prints ******** when set, nothing when not
        value = "********" if value else ""
    print(value)
    # Like FTL: with -q, a true/false setting is also the exit status.
    sys.exit(1 if "-q" in sys.argv and conf.get(args[1]) == "false" else 0)
val = args[2]
if args[1] in ("webserver.api.totp_secret", "webserver.api.app_pwhash", "webserver.api.app_sudo"):
    with open(work + "/calls.log", "a") as log:
        log.write("pihole-FTL --config %s <%s>\n" % (args[1], "empty" if not val else "set"))
    if os.path.exists(work + "/credential-stuck"):      # a secret that cannot be cleared
        sys.exit(0)
if args[1] in ("webserver.api.password", "webserver.api.pwhash"):
    # $WORK/password-stuck: nothing removes the password. $WORK/password-needs-pwhash: only clearing the hash does.
    with open(work + "/calls.log", "a") as log:
        log.write("pihole-FTL --config %s <%s>\n" % (args[1], "empty" if not val else "set"))
    works = not os.path.exists(work + "/password-stuck") and (
        args[1] == "webserver.api.pwhash" or not os.path.exists(work + "/password-needs-pwhash"))
    if works:
        req = urllib.request.Request("http://127.0.0.1:%s/__mock__/require_auth" % os.environ["PORT"],
                                     data=json.dumps({"value": bool(val)}).encode(), method="POST")
        urllib.request.urlopen(req).read()
    sys.exit(0)
if args[1] == "dns.hosts":
    items = json.loads(val)
    val = "[ " + ", ".join(items) + " ]" if items else "[]"
conf[args[1]] = val
json.dump(conf, open(db, "w"))
EOF
  # With $WORK/pihole-g-block present, "pihole -g" (gravity) hangs, like a slow list download: the test can then end the
  # installer in the middle of `sinko setup`. $WORK/pihole-g-started and pihole-g-pid tell it where the hang is.
  cat >"$STUBS/pihole" <<'EOF'
#!/usr/bin/env bash
echo "pihole $*" >>"$WORK/calls.log"
[[ -z "${SINKO_PASSWORD+x}${PB_PASSWORD+x}" ]] || echo "pihole $*" >>"$WORK/password-in-env"
if [[ "${1:-}" == -g && -e "$WORK/pihole-g-fail" ]]; then echo "gravity failed (stub)" >&2; exit 1; fi
if [[ "${1:-}" == -g && -e "$WORK/pihole-g-block" ]]; then
  echo $$ >"$WORK/pihole-g-pid"; : >"$WORK/pihole-g-started"
  exec sleep 600
fi
EOF
  # The installer's internet check probes github.com; everything else goes to the real curl (local servers only).
  # With $WORK/offline present the probe fails like it does on a box that has lost its internet.
  # Every call is written to $WORK/curl.log, so a test can see which protocol limits the installer put on it.
  cat >"$STUBS/curl" <<EOF
#!/usr/bin/env bash
echo "curl \$*" >>"\$WORK/curl.log"
if [[ "\$*" == *" https://github.com" ]]; then [[ -e "\$WORK/offline" ]] && exit 7; exit 0; fi
exec "$(command -v curl)" "\$@"
EOF
  # install, mv and rm: the real ones, except that while $WORK/tripwire exists each call first checks that the files an
  # update must never take away are all there (the program, every list, the page folder, the start page, the units).
  # A violation is written to $WORK/tripwire-hit: a power cut at that moment would have left the box broken.
  local tool
  for tool in install mv rm; do
    cat >"$STUBS/$tool" <<EOF
#!/usr/bin/env bash
if [[ -e "\$WORK/tripwire" ]]; then
  need_lists="\$(cat "\$WORK/tripwire")"
  have_lists="\$(ls "\$ROOT"/opt/sinko/lists/*.txt 2>/dev/null | wc -l)"
  web="\$ROOT/var/www/html"
  problem=""
  (( have_lists >= need_lists )) || problem="only \$have_lists lists"
  [[ -s "\$ROOT/opt/sinko/bin/sinko" ]] || problem="no program"
  [[ -s "\$ROOT/opt/sinko/lists/services.json" ]] || problem="no services.json"
  [[ -s "\$web/index.html" ]] || problem="no start page"
  [[ -d "\$web/pb" || -d "\$web/pb.old" ]] || problem="no page folder"
  [[ -s "\$ROOT/etc/systemd/system/sinko.service" ]] || problem="no scheduler unit"
  [[ -z "\$problem" ]] || echo "$tool \$*: \$problem" >>"\$WORK/tripwire-hit"
fi
exec "$(command -v "$tool")" "\$@"
EOF
  done
  cat >"$STUBS/hostnamectl" <<'EOF'
#!/usr/bin/env bash
echo "hostnamectl $*" >>"$WORK/calls.log"
if [[ "$1" == set-hostname ]]; then mkdir -p "$ROOT/etc"; echo "$2" >"$ROOT/etc/hostname"; fi
EOF
  cat >"$STUBS/hostname" <<'EOF'
#!/usr/bin/env bash
if [[ $# -gt 0 ]]; then echo "hostname $*" >>"$WORK/calls.log"; echo "$1" >"$ROOT/etc/hostname"; exit 0; fi
head -n1 "$ROOT/etc/hostname" 2>/dev/null || echo testbox
EOF
  cat >"$STUBS/passwd" <<'EOF'
#!/usr/bin/env bash
echo "passwd $*" >>"$WORK/calls.log"
EOF
  # ssh-keygen -A -f PREFIX creates the host keys that are missing, like the real one.
  cat >"$STUBS/ssh-keygen" <<'EOF'
#!/usr/bin/env bash
echo "ssh-keygen $*" >>"$WORK/calls.log"
[[ -e "$WORK/keygen-fail" ]] && exit 1
[[ "$1" == -A ]] || exit 0
prefix=""; [[ "$2" == -f ]] && prefix="$3"
mkdir -p "$prefix/etc/ssh"
for t in rsa ecdsa ed25519; do
  [[ -e "$prefix/etc/ssh/ssh_host_${t}_key" ]] || { echo "new private key $RANDOM$RANDOM" >"$prefix/etc/ssh/ssh_host_${t}_key"; echo "new public key" >"$prefix/etc/ssh/ssh_host_${t}_key.pub"; }
done
EOF
  # sshd -T prints the effective settings: here, whatever the lock-down drop-in says.
  cat >"$STUBS/sshd" <<'EOF'
#!/usr/bin/env bash
echo "sshd $*" >>"$WORK/calls.log"
case "$1" in
  -t) exit 0 ;;
  -T) if [[ ! -e "$WORK/sshd-open" ]] && grep -qsi '^PasswordAuthentication no' "$ROOT"/etc/ssh/sshd_config.d/*.conf; then echo "passwordauthentication no"; else echo "passwordauthentication yes"; fi ;;
esac
EOF
  cat >"$STUBS/journalctl" <<'EOF'
#!/usr/bin/env bash
echo "journalctl $*" >>"$WORK/calls.log"
EOF
  # dd if=/dev/zero of=FILE: writes a little and "runs out of space", as dd does on a full disk.
  cat >"$STUBS/dd" <<'EOF'
#!/usr/bin/env bash
echo "dd $*" >>"$WORK/calls.log"
for a in "$@"; do case "$a" in of=*) head -c 4096 /dev/zero >"${a#of=}"; ;; esac; done
echo "dd: error writing: No space left on device" >&2
exit 1
EOF
  cat >"$STUBS/sync" <<'EOF'
#!/usr/bin/env bash
echo "sync" >>"$WORK/calls.log"
EOF
  chmod +x "$STUBS"/*
}

start_mock() {
  python3 - "$PORT" "$REPO" "$WORK/cli_pw" <<'EOF' &
import sys, time
sys.path.insert(0, sys.argv[2] + "/tests")
import mock_pihole

# Pi-hole's command-line password (webserver.api.cli_pw, the contents of /etc/pihole/cli_pw) is a second credential that
# always signs in, whatever the web password is: the sinko program relies on it, and so a test can change the web
# password (the installer does, through the API) without locking the program out.
_cli_pw_file = sys.argv[3]
_check = mock_pihole.Store.check_password


def check_password(self, password):
    if _check(self, password):
        return True
    try:
        with open(_cli_pw_file, encoding="utf-8") as fh:
            return isinstance(password, str) and password != "" and password == fh.read().strip()
    except OSError:
        return False


mock_pihole.Store.check_password = check_password
httpd, store = mock_pihole.serve(int(sys.argv[1]))
while True:
    time.sleep(3600)
EOF
  MOCK_PID=$!
  echo test >"$WORK/cli_pw"
  local _
  for _ in $(seq 50); do curl -s -o /dev/null "http://127.0.0.1:$PORT/api/auth" && break; sleep 0.1; done
}

# Mock API controls
mock_auth()  { curl -s -X POST "http://127.0.0.1:$PORT/__mock__/require_auth" -d "{\"value\": $1}" >/dev/null; }
# What Pi-hole itself does: PATCH /api/config with webserver.api.password ("" = no password, like a fresh Pi-hole).
mock_set_password() {  # password
  mock_api PATCH /api/config "{\"config\":{\"webserver\":{\"api\":{\"password\":\"$1\"}}}}" >/dev/null
}
# Does Pi-hole sign this web password in? (a real session id comes back) With no password set, nothing can be checked: fails.
mock_password_works() {  # password
  curl -s -X POST "http://127.0.0.1:$PORT/api/auth" -H 'Content-Type: application/json' -d "{\"password\":\"$1\"}" \
    | python3 -c 'import json, sys; sys.exit(0 if json.load(sys.stdin)["session"].get("sid") else 1)'
}

# A tiny authenticated client for the mock API, for tests that look at Pi-hole's objects.
# usage: mock_api METHOD /api/path [json-body]   (prints the JSON answer)
mock_api() {
  python3 - "$PORT" "$1" "$2" "${3:-}" <<'EOF'
import json, sys, urllib.request
port, method, path, body = sys.argv[1:5]
def call(m, p, data=None, sid=None):
    req = urllib.request.Request("http://127.0.0.1:%s%s" % (port, p), data=data, method=m,
                                 headers={"Content-Type": "application/json", **({"sid": sid} if sid else {})})
    raw = urllib.request.urlopen(req).read()
    return json.loads(raw) if raw else {}
sid = call("POST", "/api/auth", b'{"password":"test"}')["session"]["sid"]
print(json.dumps(call(method, path, body.encode() if body else None, sid)))
EOF
}

# A local web server for fake releases: serve_dir DIR PORT
serve_dir() {
  python3 -m http.server "$2" --bind 127.0.0.1 --directory "$1" >/dev/null 2>&1 &
  HTTP_PID=$!
  local _
  for _ in $(seq 50); do curl -s -o /dev/null "http://127.0.0.1:$2/" && return 0; sleep 0.1; done
  fail "release server did not start"
}

# make_release OUTDIR VERSION [REPO_DIR]: sinko.tar.gz (top folder sinko/) and its .sha256, like tools/build-release.sh,
# from a copy of the working tree with VERSION set to the given number.
make_release() {
  local out="$1" version="$2" from="${3:-$REPO}" stage
  stage="$(mktemp -d)"
  mkdir -p "$stage/sinko" "$out"
  local p
  for p in bin lists web systemd install.sh uninstall.sh VERSION LICENSE NOTICE tools/seal.sh tools/firstboot.sh; do
    [[ -e "$from/$p" ]] || continue
    mkdir -p "$stage/sinko/$(dirname "$p")"
    cp -a "$from/$p" "$stage/sinko/$p"
  done
  printf '%s\n' "$version" >"$stage/sinko/VERSION"
  tar -C "$stage" --sort=name --owner=0 --group=0 --numeric-owner -cf - sinko | gzip -n -9 >"$out/sinko.tar.gz"
  ( cd "$out" && sha256sum sinko.tar.gz >sinko.tar.gz.sha256 )
  rm -rf "$stage"
}

# Starts over with an empty fake root and a fresh mock API (a second scenario in the same test).
fresh_start() {
  if [[ -n "${MOCK_PID:-}" ]]; then { kill "$MOCK_PID" 2>/dev/null || true; wait "$MOCK_PID" 2>/dev/null || true; }; fi
  rm -rf "$ROOT" "$WORK/units" "$WORK/ftl.json" "$WORK/tz" "$WORK/ipaddr" "$WORK/net-down" "$WORK/offline" "$WORK/apt-fail" \
    "$WORK/ftl-fail" "$WORK/password-stuck" "$WORK/password-needs-pwhash" "$WORK/keygen-fail" "$WORK/sshd-open" \
    "$WORK/pihole-g-block" "$WORK/pihole-g-fail" "$WORK/pihole-g-started" "$WORK/pihole-g-pid" "$WORK/apt-update-fail" "$WORK/apt-nocandidate" \
    "$WORK/py-old" "$WORK/password-in-env" "$WORK/curl.log" "$WORK/ftl-makes-cert" "$WORK/ftl-cert-fail" \
    "$WORK/ip-static" "$WORK/ftl-stop-fails" "$WORK/credential-stuck"
  mkdir -p "$ROOT/etc/pihole" "$ROOT/etc/ssh" "$ROOT/var/www/html" "$ROOT/var/log" "$WORK/units"
  touch "$ROOT/etc/pihole/pihole.toml" "$WORK/units/pihole-FTL.service.active" "$WORK/units/pihole-FTL.service.enabled"
  : >"$WORK/calls.log"
  printf '%s\n' git curl ca-certificates python3 iproute2 >"$WORK/dpkg-installed"
  start_mock
}

# json_get 'python expression on d' <<<json: prints the value, for reading answers of the mock API.
json_get() { python3 -c 'import json, sys; d = json.load(sys.stdin); print('"$1"')'; }

# What a family has on the box: Sinko's groups and lists (registered under the given lists address), two children's
# devices (one paused), a bedtime, and objects of the user's own that no Sinko command may touch: a group, a list, a
# client in that group, and a grown-up's device that sits in Sinko's pb-paused group.
seed_pihole_objects() {
  local base="$1" kid_ids paused_id mine_id
  SINKO_APP_DIR="$REPO" SINKO_CONFIG_FILE=/nonexistent SINKO_LISTS_BASE="$base" python3 "$REPO/bin/sinko" setup --no-gravity >/dev/null
  kid_ids="$(mock_api GET /api/groups | json_get '[0] + [g["id"] for g in d["groups"] if g["name"].startswith("pb-") and g["name"] not in ("pb-paused", "pb-state")]')"
  paused_id="$(mock_api GET /api/groups | json_get '[g["id"] for g in d["groups"] if g["name"] == "pb-paused"][0]')"
  mock_api POST /api/clients "{\"client\":\"aa:bb:cc:dd:ee:01\",\"comment\":\"Sara\",\"groups\":$kid_ids}" >/dev/null
  mock_api POST /api/clients "{\"client\":\"aa:bb:cc:dd:ee:02\",\"comment\":\"Omar\",\"groups\":${kid_ids%]}, $paused_id]}" >/dev/null
  mock_api PUT /api/groups/pb-state '{"enabled": false, "comment": "{\"v\":1,\"timer\":null,\"schedule\":{\"enabled\":true,\"start\":\"22:00\",\"end\":\"06:30\",\"days\":[0,1,2,3,4,5,6]},\"scheduleActive\":false}"}' >/dev/null
  mock_api POST /api/groups '{"name":"my-group","comment":"mine","enabled":true}' >/dev/null
  mine_id="$(mock_api GET /api/groups | json_get '[g["id"] for g in d["groups"] if g["name"] == "my-group"][0]')"
  mock_api POST '/api/lists?type=block' '{"address":"https://example.org/mine.txt","comment":"my list","groups":[0],"enabled":true}' >/dev/null
  mock_api POST /api/clients "{\"client\":\"192.168.1.99\",\"comment\":\"grown-up laptop\",\"groups\":[$mine_id]}" >/dev/null
  mock_api POST /api/clients "{\"client\":\"192.168.1.98\",\"comment\":\"grown-up phone\",\"groups\":[0, $paused_id]}" >/dev/null
}

# The user's own Pi-hole objects from seed_pihole_objects are all still there, unchanged.
assert_user_objects_intact() {
  local mine_id
  mock_api GET /api/groups | json_get '[g["comment"] for g in d["groups"] if g["name"] == "my-group"]' | grep -qx "\['mine'\]" || fail "the user's own group changed"
  mock_api GET /api/lists | json_get '[l["comment"] for l in d["lists"] if l["address"] == "https://example.org/mine.txt"]' | grep -qx "\['my list'\]" || fail "the user's own list changed"
  mock_api GET /api/clients | json_get '[c["comment"] for c in d["clients"] if c["client"] == "192.168.1.99"]' | grep -qx "\['grown-up laptop'\]" || fail "the user's own client changed"
  mine_id="$(mock_api GET /api/groups | json_get '[g["id"] for g in d["groups"] if g["name"] == "my-group"][0]')"
  mock_api GET /api/clients | json_get '[c["groups"] for c in d["clients"] if c["client"] == "192.168.1.99"][0] == ['"$mine_id"']' | grep -qx True || fail "the user's own client lost its group"
  mock_api GET /api/clients | json_get '[c["comment"] for c in d["clients"] if c["client"] == "192.168.1.98"]' | grep -qx "\['grown-up phone'\]" || fail "a grown-up's device was removed"
}
