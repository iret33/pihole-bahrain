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
  touch "$WORK/units/pihole-FTL.service.active"
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
case "$*" in
  *"route show default"*) echo "default via 192.168.1.1 dev eth0 proto dhcp src $addr metric 100" ;;
  *"addr show dev eth0"*) echo "2: eth0    inet $addr/24 brd 192.168.1.255 scope global dynamic eth0" ;;
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
if [[ -e "$WORK/apt-fail" ]]; then echo "E: apt is not available (stub)" >&2; exit 100; fi
if [[ " $* " == *" install "* ]]; then
  for a in "$@"; do
    case "$a" in -*|install) ;; *) echo "$a" >>"$WORK/dpkg-installed" ;; esac
  done
fi
exit 0
EOF
  # systemctl keeps "active" and "enabled" as marker files, so tests can ask what the scripts did.
  cat >"$STUBS/systemctl" <<'EOF'
#!/usr/bin/env bash
echo "systemctl $*" >>"$WORK/calls.log"
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
  start|restart) for u in "${units[@]}"; do touch "$WORK/units/$(norm "$u").active"; done ;;
  stop) for u in "${units[@]}"; do rm -f "$WORK/units/$(norm "$u").active"; done ;;
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
if len(args) == 2:
    print(conf.get(args[1], ""))
    # Like FTL: with -q, a true/false setting is also the exit status.
    sys.exit(1 if "-q" in sys.argv and conf.get(args[1]) == "false" else 0)
val = args[2]
if args[1] == "webserver.api.password":
    with open(work + "/calls.log", "a") as log:
        log.write("pihole-FTL --config webserver.api.password <%s>\n" % ("empty" if not val else "set"))
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
  cat >"$STUBS/pihole" <<'EOF'
#!/usr/bin/env bash
echo "pihole $*" >>"$WORK/calls.log"
EOF
  # The installer's internet check probes github.com; everything else goes to the real curl (local servers only).
  # With $WORK/offline present the probe fails like it does on a box that has lost its internet.
  cat >"$STUBS/curl" <<EOF
#!/usr/bin/env bash
if [[ "\$*" == *" https://github.com" ]]; then [[ -e "\$WORK/offline" ]] && exit 7; exit 0; fi
exec "$(command -v curl)" "\$@"
EOF
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
  -T) if grep -qsi '^PasswordAuthentication no' "$ROOT"/etc/ssh/sshd_config.d/*.conf; then echo "passwordauthentication no"; else echo "passwordauthentication yes"; fi ;;
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
  local _
  for _ in $(seq 50); do curl -s -o /dev/null "http://127.0.0.1:$PORT/api/auth" && break; sleep 0.1; done
}

# Mock API controls
mock_auth()  { curl -s -X POST "http://127.0.0.1:$PORT/__mock__/require_auth" -d "{\"value\": $1}" >/dev/null; }

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
