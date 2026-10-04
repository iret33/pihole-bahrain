#!/usr/bin/env bash
# Moving a box from pihole-bahrain (<= 2.2.x) to Sinko: a legacy fake root (old units, old settings with PB_* keys,
# the old page, the old command), the real new installer started the way an old "pihole-bahrain update" starts it
# (from inside /opt/pihole-bahrain/src, with PB_* variables), and the mock Pi-hole holding the family's objects.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=tests/lib_stubs.sh disable=SC1091
. "$HERE/lib_stubs.sh"
PORT="${PORT:-18082}"
fake_system_init

OLD_LISTS="https://raw.githubusercontent.com/iret33/pihole-bahrain/master/lists"
NEW_LISTS="https://raw.githubusercontent.com/iret33/sinko/master/lists"
OLD_PAGE='<!doctype html><html><head><meta name="generator" content="pihole-bahrain"><title>Family Internet</title></head></html>'

# build_legacy: everything pihole-bahrain 2.2.x leaves on a box. The config text arrives on stdin.
build_legacy() {
  local old="$ROOT/opt/pihole-bahrain"
  mkdir -p "$old/bin" "$old/lists" "$ROOT/etc/pihole-bahrain" "$ROOT/usr/local/bin" "$ROOT/etc/systemd/system" "$ROOT/var/www/html/pb"
  printf '#!/bin/sh\necho old program\n' >"$old/bin/pihole-bahrain"; chmod 755 "$old/bin/pihole-bahrain"
  echo 2.2.0 >"$old/VERSION"
  cat >"$ROOT/etc/pihole-bahrain/config"
  local u
  for u in pihole-bahrain.service pihole-bahrain-lists.service pihole-bahrain-lists.timer; do
    printf '[Unit]\nDescription=old %s\n' "$u" >"$ROOT/etc/systemd/system/$u"
  done
  touch "$WORK/units/pihole-bahrain.service.enabled" "$WORK/units/pihole-bahrain.service.active" \
        "$WORK/units/pihole-bahrain-lists.timer.enabled" "$WORK/units/pihole-bahrain-lists.timer.active"
  ln -s "$old/bin/pihole-bahrain" "$ROOT/usr/local/bin/pihole-bahrain"
  echo "old install log" >"$ROOT/var/log/pihole-bahrain-install.log"
  echo "old script" >"$ROOT/var/www/html/pb/app.js"
  # What the old "pihole-bahrain update" leaves: a checkout of the new code inside the old folder, run from there.
  mkdir -p "$old/src"
  tar -C "$REPO" --exclude=.git -cf - . | tar -x -C "$old/src"
}

# run_migration NAME [env args...]: the new installer, started from the old folder, with PB_* variables only.
run_migration() {
  local name="$1"; shift
  env -u SINKO_NONINTERACTIVE PB_NONINTERACTIVE=1 "$@" bash "$ROOT/opt/pihole-bahrain/src/install.sh" >"$WORK/$name.out" 2>&1
}

tree_digest() {  # what a second run must not change
  ( cd "$ROOT" && {
      find etc/sinko etc/systemd/system opt/sinko var/www/html usr/local/bin -type f -print0 | sort -z | xargs -0 sha256sum
      find usr/local/bin -type l -printf '%p -> %l\n' | sort
      find etc/sinko opt/sinko var/www/html etc/systemd/system -printf '%p %m\n' | sort
    } | sha256sum; )
}

echo "--- an old box with the default settings, children, a bedtime and the user's own Pi-hole objects"
build_legacy <<EOF
# pihole-bahrain settings. Re-run the installer after editing.
PB_HOSTNAME=kids.home
PB_LISTS_BASE=$OLD_LISTS
PB_REPO=https://github.com/iret33/pihole-bahrain.git
PB_REF=release-candidate
PB_IP=192.168.1.50
EOF
echo "$OLD_PAGE" >"$ROOT/var/www/html/index.html"
seed_pihole_objects "$OLD_LISTS"
groups_before="$(mock_api GET /api/groups | json_get 'sorted(g["name"] for g in d["groups"])')"
kids_before="$(mock_api GET /api/clients | json_get 'sorted((c["client"], c["comment"], sorted(c["groups"])) for c in d["clients"] if c["client"].startswith("aa:"))')"
state_before="$(mock_api GET /api/groups | json_get '[g["comment"] for g in d["groups"] if g["name"] == "pb-state"][0]')"
mock_api GET /api/lists | json_get 'len([l for l in d["lists"] if l["address"].startswith("'"$OLD_LISTS"'/")])' | grep -qx 20 || fail "test setup: the old lists were not registered"

run_migration migrate PB_REF=release-candidate || { cat "$WORK/migrate.out"; fail "the migration failed"; }
out="$(cat "$WORK/migrate.out")"
grep -q "Found an earlier version (pihole-bahrain)" <<<"$out" || fail "the old installation was not recognised"
grep -q "Sinko is ready" <<<"$out" || fail "the migration did not finish"

echo "    nothing the user chose is lost"
conf="$ROOT/etc/sinko/config"
grep -q '^SINKO_HOSTNAME=kids.home$' "$conf" || { cat "$conf"; fail "the host name was lost"; }
grep -q '^SINKO_REF=release-candidate$' "$conf" || fail "the ref was lost"
grep -q "^SINKO_LISTS_BASE=$NEW_LISTS\$" "$conf" || fail "the lists address that pointed at the old repository was not replaced by the new default"
grep -q '^SINKO_REPO=https://github.com/iret33/sinko.git$' "$conf" || fail "the repository that pointed at the old project was not replaced"
grep -q '^SINKO_REPO_SLUG=iret33/sinko$' "$conf" || fail "no repository slug"
grep -q '^SINKO_IP=192.168.1.50$' "$conf" || fail "no address"
grep -q 'PB_' "$conf" && fail "old PB_ keys are still in the new settings"
grep -q '192.168.1.50 kids.home' "$WORK/ftl.json" || fail "the local name is not registered in Pi-hole"
echo "    the Pi-hole objects are kept and the lists are registered under the new address"
[[ "$(mock_api GET /api/groups | json_get 'sorted(g["name"] for g in d["groups"])')" == "$groups_before" ]] || fail "groups changed"
[[ "$(mock_api GET /api/clients | json_get 'sorted((c["client"], c["comment"], sorted(c["groups"])) for c in d["clients"] if c["client"].startswith("aa:"))')" == "$kids_before" ]] || fail "children's devices changed"
[[ "$(mock_api GET /api/groups | json_get '[g["comment"] for g in d["groups"] if g["name"] == "pb-state"][0]')" == "$state_before" ]] || fail "the bedtime (state) changed"
mock_api GET /api/lists | json_get 'len([l for l in d["lists"] if l["address"].startswith("'"$NEW_LISTS"'/")])' | grep -qx 20 || fail "the lists were not registered under the new address"
mock_api GET /api/lists | json_get 'len([l for l in d["lists"] if "pihole-bahrain" in l["address"]])' | grep -qx 0 || fail "lists under the old address are left"
assert_user_objects_intact
echo "    the old scheduler is gone and the new units are on"
for u in pihole-bahrain.service pihole-bahrain-lists.service pihole-bahrain-lists.timer; do
  [[ ! -e "$ROOT/etc/systemd/system/$u" ]] || fail "old unit $u is still installed"
done
[[ ! -e "$WORK/units/pihole-bahrain.service.enabled" && ! -e "$WORK/units/pihole-bahrain.service.active" \
   && ! -e "$WORK/units/pihole-bahrain-lists.timer.enabled" ]] || fail "an old unit is still enabled or running"
[[ -e "$WORK/units/sinko.service.enabled" && -e "$WORK/units/sinko.service.active" && -e "$WORK/units/sinko-lists.timer.enabled" ]] \
  || fail "the new units are not enabled and running"
grep -q "systemctl disable --now pihole-bahrain.service pihole-bahrain-lists.timer" "$WORK/calls.log" || fail "the old units were not stopped and disabled"
echo "    the old page is replaced, not kept as somebody's page; the old files go at the very end"
grep -q 'name="generator" content="sinko"' "$ROOT/var/www/html/index.html" || fail "the old page was not replaced"
[[ ! -e "$ROOT/var/www/html/index.html.pb-backup" ]] || fail "the old page was backed up as if it were the user's own"
[[ ! -e "$ROOT/opt/pihole-bahrain" && ! -e "$ROOT/etc/pihole-bahrain" && ! -e "$ROOT/var/log/pihole-bahrain-install.log" ]] || fail "old paths are left"
final="$(grep -n "Removed the old version's files" "$WORK/migrate.out" | cut -d: -f1)"
check="$(grep -n "Final check" "$WORK/migrate.out" | cut -d: -f1)"
ready="$(grep -n "Sinko is ready" "$WORK/migrate.out" | cut -d: -f1)"
[[ -n "$final" && "$check" -lt "$final" && "$final" -lt "$ready" ]] || fail "the old folders were not removed after the final check and before the summary"
echo "    the old command still works, as a symlink to sinko"
[[ -L "$ROOT/usr/local/bin/pihole-bahrain" ]] || fail "no compatibility symlink"
[[ "$(readlink -f "$ROOT/usr/local/bin/pihole-bahrain")" == "$(readlink -f "$ROOT/usr/local/bin/sinko")" ]] || fail "the old command does not lead to sinko"
grep -q "The command is now called sinko" "$WORK/migrate.out" || fail "no notice about the new command name"
"$ROOT/usr/local/bin/pihole-bahrain" status | python3 -c 'import json, sys; json.load(sys.stdin)' || fail "the old command does not run"

echo "--- a second run changes nothing"
digest1="$(tree_digest)"
kids1="$(mock_api GET /api/clients | json_get 'sorted((c["client"], sorted(c["groups"])) for c in d["clients"])')"
bash "$REPO/install.sh" >"$WORK/again.out" 2>&1 || { cat "$WORK/again.out"; fail "the second run failed"; }
grep -q "Found an earlier version" "$WORK/again.out" && fail "the second run thinks there is something to migrate"
[[ "$(tree_digest)" == "$digest1" ]] || fail "the second run changed files"
[[ "$(mock_api GET /api/clients | json_get 'sorted((c["client"], sorted(c["groups"])) for c in d["clients"])')" == "$kids1" ]] || fail "the second run changed Pi-hole's clients"
[[ -L "$ROOT/usr/local/bin/pihole-bahrain" ]] || fail "the second run removed the compatibility symlink"

echo "--- a variant: a chosen lists address and a fork of the old project are kept, no host name, a user's own start page"
fresh_start
build_legacy <<EOF
PB_HOSTNAME=''
PB_LISTS_BASE=https://example.org/my/lists
PB_REPO=https://github.com/someone/pihole-bahrain.git
PB_REF=master
PB_IP=192.168.1.50
EOF
echo '<h1>mine</h1>' >"$ROOT/var/www/html/index.html"
seed_pihole_objects "https://example.org/my/lists"
run_migration variant || { cat "$WORK/variant.out"; fail "the variant migration failed"; }
conf="$ROOT/etc/sinko/config"
grep -q "^SINKO_HOSTNAME=''\$" "$conf" || { cat "$conf"; fail "'no local name' was not kept"; }
grep -q '^SINKO_LISTS_BASE=https://example.org/my/lists$' "$conf" || fail "a chosen lists address was replaced"
grep -q '^SINKO_REPO=https://github.com/someone/pihole-bahrain.git$' "$conf" || fail "a fork of the old project was replaced"
grep -q '^SINKO_REPO_SLUG=someone/pihole-bahrain$' "$conf" || fail "the fork's slug was not derived"
grep -q '^SINKO_REF=master$' "$conf" || fail "the ref master was not kept"
grep -q mine "$ROOT/var/www/html/index.html.pb-backup" || fail "a user's own page was not backed up"
mock_api GET /api/lists | json_get 'len([l for l in d["lists"] if l["address"].startswith("https://example.org/my/lists/")])' | grep -qx 20 || fail "the lists changed address although they point elsewhere"
assert_user_objects_intact
[[ ! -e "$ROOT/opt/pihole-bahrain" ]] || fail "old folder left behind (variant)"

echo "--- environment variables of the old installer (PB_*) are understood, and a SINKO_* variable wins over them"
fresh_start
build_legacy <<EOF
PB_HOSTNAME=family.lan
PB_REF=master
EOF
run_migration envs PB_REF=from-env PB_HOSTNAME=env.home SINKO_HOSTNAME=sinko.home || { cat "$WORK/envs.out"; fail "migration with PB_ variables failed"; }
grep -q '^SINKO_REF=from-env$' "$ROOT/etc/sinko/config" || fail "PB_REF from the environment was ignored"
grep -q '^SINKO_HOSTNAME=sinko.home$' "$ROOT/etc/sinko/config" || fail "SINKO_HOSTNAME did not win over PB_HOSTNAME"

echo "--- a failure in the middle of the migration puts the old scheduler back, and a rerun completes it"
fresh_start
build_legacy <<EOF
PB_HOSTNAME=family.lan
PB_REF=master
EOF
echo "$OLD_PAGE" >"$ROOT/var/www/html/index.html"
if run_migration failing SINKO_PASSWORD=short; then fail "an invalid password was accepted"; fi
grep -q "must be at least 8 characters" "$WORK/failing.out" || fail "test setup: the migration did not fail where expected"
grep -q "The previous version keeps running" "$WORK/failing.out" || fail "no note that the old scheduler was restored"
[[ -e "$WORK/units/pihole-bahrain.service.enabled" && -e "$WORK/units/pihole-bahrain.service.active" && -e "$WORK/units/pihole-bahrain-lists.timer.enabled" ]] \
  || fail "the old scheduler was not put back after the failure"
[[ -d "$ROOT/opt/pihole-bahrain" && -f "$ROOT/etc/systemd/system/pihole-bahrain.service" ]] || fail "old files were removed although the migration failed"
[[ ! -e "$WORK/units/sinko.service.enabled" ]] || fail "the new scheduler was enabled by a failed run"
run_migration rerun || { cat "$WORK/rerun.out"; fail "rerunning the migration failed"; }
[[ ! -e "$ROOT/opt/pihole-bahrain" && -e "$WORK/units/sinko.service.enabled" && ! -e "$WORK/units/pihole-bahrain.service.enabled" ]] || fail "the rerun did not complete the migration"

echo "migration tests passed"
