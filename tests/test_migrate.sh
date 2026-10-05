#!/usr/bin/env bash
# Moving a box from pihole-bahrain (<= 2.2.x) to Sinko: a legacy fake root (old units, old settings with PB_* keys,
# the old page, the old command), the real new installer started the way an old "pihole-bahrain update" starts it
# (from inside /opt/pihole-bahrain/src, with PB_* variables), and the mock Pi-hole holding the family's objects.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=tests/lib_stubs.sh disable=SC1091
. "$HERE/lib_stubs.sh"
PORT="${PORT:-18082}"
RELPORT="${RELPORT:-18092}"        # the fake release server of the one-liner scenario
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
  # LEGACY_GIT=1: it is a git checkout, as the old `pihole-bahrain update` leaves it (git clone --depth 1 of master).
  if [[ -n "${LEGACY_GIT:-}" ]]; then mkdir -p "$old/src/.git"; fi
}

# run_migration NAME [env args...]: the new installer, started from the old folder, with PB_* variables only.
run_migration() {
  local name="$1"; shift
  env -u SINKO_NONINTERACTIVE PB_NONINTERACTIVE=1 "$@" bash "$ROOT/opt/pihole-bahrain/src/install.sh" >"$WORK/$name.out" 2>&1
}

tree_digest() {  # what a second run must not change (box.json carries the time it was written: it is rewritten every run)
  ( cd "$ROOT" && {
      find etc/sinko etc/systemd/system opt/sinko var/www/html usr/local/bin -type f ! -name box.json -print0 | sort -z | xargs -0 sha256sum
      find usr/local/bin -type l -printf '%p -> %l\n' | sort
      find etc/sinko opt/sinko var/www/html etc/systemd/system ! -name box.json -printf '%p %m\n' | sort
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
# Every list as Pi-hole holds it: a list that is deleted and registered again gets a new id and an empty cache, and
# blocks nothing until the next gravity run has downloaded it.
lists_state() { mock_api GET /api/lists | json_get 'sorted((l["id"], l["address"], l["comment"], l["enabled"], sorted(l["groups"])) for l in d["lists"])'; }
lists_before="$(lists_state)"
# What 2.x left for a box without a terminal: the generated parent password, to be read once (the only record of it).
echo "Abcd-Efgh-Jklm" >"$ROOT/etc/pihole-bahrain/initial-password"; chmod 600 "$ROOT/etc/pihole-bahrain/initial-password"

echo 500000 >"$WORK/df-free-kb"        # a card with 488 MB free: enough for the move (only a first installation needs 1 GB)
run_migration migrate PB_REF=release-candidate || { cat "$WORK/migrate.out"; fail "the migration failed"; }
rm -f "$WORK/df-free-kb"
out="$(cat "$WORK/migrate.out")"
grep -q "Found an earlier version (pihole-bahrain)" <<<"$out" || fail "the old installation was not recognised"
grep -q "Sinko is ready" <<<"$out" || fail "the migration did not finish"

echo "    nothing the user chose is lost"
conf="$ROOT/etc/sinko/config"
grep -q '^SINKO_HOSTNAME=kids.home$' "$conf" || { cat "$conf"; fail "the host name was lost"; }
grep -q '^SINKO_REF=release-candidate$' "$conf" || fail "the ref was lost"
grep -q "^SINKO_LISTS_BASE=$OLD_LISTS\$" "$conf" || { cat "$conf"; fail "the lists address of the old project was not kept (a changed address deletes and registers every list again)"; }
grep -q '^SINKO_REPO=https://github.com/iret33/sinko.git$' "$conf" || fail "the repository that pointed at the old project was not replaced"
grep -q '^SINKO_REPO_SLUG=iret33/sinko$' "$conf" || fail "no repository slug"
grep -q '^SINKO_IP=192.168.1.50$' "$conf" || fail "no address"
grep -q 'PB_' "$conf" && fail "old PB_ keys are still in the new settings"
grep -q '192.168.1.50 kids.home' "$WORK/ftl.json" || fail "the local name is not registered in Pi-hole"
echo "    the Pi-hole objects are kept, and no list is deleted or registered again (same ids, same address)"
[[ "$(mock_api GET /api/groups | json_get 'sorted(g["name"] for g in d["groups"])')" == "$groups_before" ]] || fail "groups changed"
[[ "$(mock_api GET /api/clients | json_get 'sorted((c["client"], c["comment"], sorted(c["groups"])) for c in d["clients"] if c["client"].startswith("aa:"))')" == "$kids_before" ]] || fail "children's devices changed"
[[ "$(mock_api GET /api/groups | json_get '[g["comment"] for g in d["groups"] if g["name"] == "pb-state"][0]')" == "$state_before" ]] || fail "the bedtime (state) changed"
[[ "$(lists_state)" == "$lists_before" ]] || fail "a list was deleted, registered again or changed during the migration"
mock_api GET /api/lists | json_get 'len([l for l in d["lists"] if l["address"].startswith("'"$OLD_LISTS"'/")])' | grep -qx 20 || fail "the lists are not registered under their old address any more"
mock_api GET /api/lists | json_get 'len([l for l in d["lists"] if l["address"].startswith("'"$NEW_LISTS"'/")])' | grep -qx 0 || fail "lists were registered under the new address (a window without blocking)"
assert_user_objects_intact
echo "    the parent password the old version saved is kept (root only), the old folder is gone"
[[ "$(cat "$ROOT/etc/sinko/initial-password")" == "Abcd-Efgh-Jklm" && "$(stat -c %a "$ROOT/etc/sinko/initial-password")" == 600 ]] || fail "the saved parent password was lost or is readable by others"
grep -q "Abcd-Efgh-Jklm" "$WORK/migrate.out" "$ROOT/var/log/sinko-install.log" && fail "the parent password reached the output or the log"
echo "    the old scheduler is gone and the new units are on"
for u in pihole-bahrain.service pihole-bahrain-lists.service pihole-bahrain-lists.timer; do
  [[ ! -e "$ROOT/etc/systemd/system/$u" ]] || fail "old unit $u is still installed"
done
[[ ! -e "$WORK/units/pihole-bahrain.service.enabled" && ! -e "$WORK/units/pihole-bahrain.service.active" \
   && ! -e "$WORK/units/pihole-bahrain-lists.timer.enabled" ]] || fail "an old unit is still enabled or running"
[[ -e "$WORK/units/sinko.service.enabled" && -e "$WORK/units/sinko.service.active" && -e "$WORK/units/sinko-lists.timer.enabled" ]] \
  || fail "the new units are not enabled and running"
grep -q "systemctl disable --now pihole-bahrain.service pihole-bahrain-lists.timer" "$WORK/calls.log" || fail "the old units were not stopped and disabled"
echo "    the old units are only stopped until the new scheduler runs; they are disabled after it has started"
call_line() { grep -n -m1 -- "$1" "$WORK/calls.log" | cut -d: -f1; }
stop_at="$(call_line '^systemctl stop pihole-bahrain.service')"
gravity_at="$(call_line '^pihole -g')"
enable_at="$(call_line '^systemctl enable --quiet sinko.service')"
restart_at="$(call_line '^systemctl restart sinko.service')"
disable_at="$(call_line '^systemctl disable --now pihole-bahrain.service')"
[[ -n "$stop_at" && -n "$gravity_at" && -n "$enable_at" && -n "$restart_at" && -n "$disable_at" ]] || { cat "$WORK/calls.log"; fail "test setup: a systemctl call is missing"; }
(( stop_at < gravity_at )) || fail "the old scheduler was not stopped before gravity ran (two schedulers at once)"
(( enable_at < disable_at && restart_at < disable_at )) || fail "the old units were disabled before the new scheduler was enabled and running (a power cut in between leaves the box without a scheduler)"
grep -q "disable.*pihole-bahrain" <(head -n "$((enable_at - 1))" "$WORK/calls.log") && fail "an old unit was disabled early"
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

echo "--- the ref master is the 2.x default, not a choice: boxes of the official project follow the releases"
# 2.x wrote PB_REF=master for everybody, and its "update" command passes it to this installer every time.
legacy_master() { build_legacy <<EOF
PB_HOSTNAME=family.lan
PB_REF=master
PB_REPO=https://github.com/iret33/pihole-bahrain.git
EOF
}
echo "    from the old settings file only"
fresh_start; legacy_master
run_migration ref-file || { cat "$WORK/ref-file.out"; fail "migration (settings file only) failed"; }
grep -q '^SINKO_REF=latest$' "$ROOT/etc/sinko/config" || { cat "$ROOT/etc/sinko/config"; fail "a legacy PB_REF=master was kept: the box would never see a release"; }
echo "    from the environment, the way the old 'pihole-bahrain update' starts the installer"
fresh_start; legacy_master
run_migration ref-env PB_REF=master || { cat "$WORK/ref-env.out"; fail "migration (PB_REF=master in the environment) failed"; }
grep -q '^SINKO_REF=latest$' "$ROOT/etc/sinko/config" || { cat "$ROOT/etc/sinko/config"; fail "PB_REF=master from the old command was kept: the box would never see a release"; }
echo "    a SINKO_REF=master that somebody gave, or saved, is a developer's choice and stays"
fresh_start; legacy_master
run_migration ref-dev SINKO_REF=master || fail "migration with SINKO_REF=master failed"
grep -q '^SINKO_REF=master$' "$ROOT/etc/sinko/config" || fail "an explicit SINKO_REF=master was changed"
fresh_start; legacy_master
run_migration ref-saved || fail "migration failed"
sed -i 's/^SINKO_REF=.*/SINKO_REF=master/' "$ROOT/etc/sinko/config"
bash "$REPO/install.sh" >"$WORK/ref-saved2.out" 2>&1 || { cat "$WORK/ref-saved2.out"; fail "the run after saving SINKO_REF=master failed"; }
grep -q '^SINKO_REF=master$' "$ROOT/etc/sinko/config" || fail "a saved SINKO_REF=master was changed by the next run"
echo "    the one-liner on such a box installs the verified release, not the tip of the master branch"
fresh_start; legacy_master
SERVE="$WORK/serve"; BASE="http://127.0.0.1:$RELPORT/releases"
mkdir -p "$SERVE/releases/latest"
make_release "$SERVE/releases/latest/download" "$(tr -d '[:space:]' <"$REPO/VERSION")"
serve_dir "$SERVE" "$RELPORT"
env -u SINKO_NONINTERACTIVE PB_NONINTERACTIVE=1 SINKO_RELEASE_BASE="$BASE" bash <"$REPO/install.sh" >"$WORK/ref-oneliner.out" 2>&1 \
  || { cat "$WORK/ref-oneliner.out"; fail "the one-liner on a migrated box failed"; }
grep -q "Downloaded and verified sinko" "$WORK/ref-oneliner.out" || { cat "$WORK/ref-oneliner.out"; fail "the release was not downloaded and verified"; }
grep -q "development version" "$WORK/ref-oneliner.out" && fail "the one-liner installed a development branch"
[[ -f "$ROOT/var/lib/sinko/cache/sinko-$(tr -d '[:space:]' <"$REPO/VERSION").tar.gz" ]] || fail "no rollback copy of the verified release"
grep -q '^SINKO_REF=latest$' "$ROOT/etc/sinko/config" || fail "SINKO_REF=latest not saved"
grep -q "^SINKO_RELEASE_BASE=$BASE\$" "$ROOT/etc/sinko/config" || fail "the update source that was given is not saved (every later update would go back to GitHub)"
VERSION_NOW="$(tr -d '[:space:]' <"$REPO/VERSION")"
echo "    the old updater's own run (a git checkout of master inside the old folder) installs the verified release, not the checkout"
fresh_start; LEGACY_GIT=1 legacy_master
printf '9.9.9\n' >"$ROOT/opt/pihole-bahrain/src/VERSION"        # what the checkout says it is: it must not be what ends up installed
run_migration from-checkout PB_REF=master SINKO_RELEASE_BASE="$BASE" || { cat "$WORK/from-checkout.out"; fail "the move from the old updater's checkout failed"; }
grep -q "Downloaded and verified sinko $VERSION_NOW" "$WORK/from-checkout.out" || { cat "$WORK/from-checkout.out"; fail "the release was not downloaded and verified for a box that follows the releases"; }
grep -q "Installing version $VERSION_NOW from $ROOT/opt/sinko/src" "$WORK/from-checkout.out" || fail "the checkout was installed, not the verified release"
grep -q "Installing version 9.9.9" "$WORK/from-checkout.out" && fail "the old updater's checkout was installed"
[[ "$(cat "$ROOT/opt/sinko/VERSION")" == "$VERSION_NOW" && -d "$ROOT/opt/sinko/src" && -f "$ROOT/var/lib/sinko/cache/sinko-$VERSION_NOW.tar.gz" ]] \
  || fail "no release copy (source folder, rollback tarball) was left behind"
grep -q "Sinko is ready" "$WORK/from-checkout.out" || fail "the migration did not finish from the verified release"
[[ ! -e "$ROOT/opt/pihole-bahrain" && -e "$WORK/units/sinko.service.enabled" ]] || fail "the old folder is still there, or the new scheduler is not on"
grep -q '^SINKO_REF=latest$' "$ROOT/etc/sinko/config" || fail "SINKO_REF=latest was not saved"
echo "    ... when the release cannot be had, the checkout is installed as before, with a note (a connection that is down must not stop the move)"
fresh_start; LEGACY_GIT=1 legacy_master
run_migration checkout-fallback PB_REF=master SINKO_RELEASE_BASE="http://127.0.0.1:1/releases" || { cat "$WORK/checkout-fallback.out"; fail "the move failed when the release could not be had"; }
grep -q "The verified release could not be had now" "$WORK/checkout-fallback.out" || fail "no note that the checkout was installed instead of the release"
grep -q "Could not reach" "$WORK/checkout-fallback.out" || fail "the note does not say why the release could not be had"
grep -q "Installing version $VERSION_NOW from $ROOT/opt/pihole-bahrain/src" "$WORK/checkout-fallback.out" || fail "the checkout was not installed after the release failed"
grep -q "Sinko is ready" "$WORK/checkout-fallback.out" || fail "the migration did not finish from the checkout"
[[ ! -e "$ROOT/opt/pihole-bahrain" ]] || fail "the old folder is still there"
[[ ! -e "$ROOT/opt/sinko/src.tmp" && -z "$(ls -d "$ROOT"/opt/sinko/.download.* 2>/dev/null)" ]] || fail "a failed download left its temporary files"
echo "    ... a developer's SINKO_REF=master is a choice: the checkout is installed and no release is looked for"
fresh_start; LEGACY_GIT=1 legacy_master
run_migration checkout-dev SINKO_REF=master SINKO_RELEASE_BASE="http://127.0.0.1:1/releases" || { cat "$WORK/checkout-dev.out"; fail "the developer's move failed"; }
grep -q "The verified release could not be had now\|Downloaded and verified" "$WORK/checkout-dev.out" && fail "a release was looked for although the ref is a branch"
grep -q "Installing version $VERSION_NOW from $ROOT/opt/pihole-bahrain/src" "$WORK/checkout-dev.out" || fail "the developer's checkout was not installed"
kill "$HTTP_PID" 2>/dev/null || true; HTTP_PID=""

echo "--- a lists folder inside the old program folder is moved to the new one, because the old folder goes"
for form in plain file-url; do
  fresh_start
  if [[ "$form" == plain ]]; then local_base="$ROOT/opt/pihole-bahrain/lists"; else local_base="file://$ROOT/opt/pihole-bahrain/src/lists/"; fi
  build_legacy <<EOF
PB_HOSTNAME=family.lan
PB_LISTS_BASE=$local_base
PB_REF=release-candidate
EOF
  seed_pihole_objects "$ROOT/opt/pihole-bahrain/lists"
  run_migration "locallists-$form" || { cat "$WORK/locallists-$form.out"; fail "migration with a local lists folder failed ($form)"; }
  grep -q "^SINKO_LISTS_BASE=$ROOT/opt/sinko/lists\$" "$ROOT/etc/sinko/config" || { cat "$ROOT/etc/sinko/config"; fail "the lists base still points into the old folder ($form)"; }
  mock_api GET /api/lists | json_get 'len([l for l in d["lists"] if l["address"].startswith("file://'"$ROOT"'/opt/sinko/lists/")])' | grep -qx 20 || fail "the lists are not registered from the new folder ($form)"
  mock_api GET /api/lists | json_get 'len([l for l in d["lists"] if "pihole-bahrain" in l["address"]])' | grep -qx 0 || fail "a list still points into the old folder ($form)"
  [[ -f "$ROOT/opt/sinko/lists/youtube.txt" && ! -e "$ROOT/opt/pihole-bahrain" ]] || fail "the new lists folder is empty or the old one is left ($form)"
done

echo "--- a failure in the middle of the migration puts the old scheduler back, and a rerun completes it"
fresh_start
build_legacy <<EOF
PB_HOSTNAME=family.lan
PB_REF=master
EOF
echo "$OLD_PAGE" >"$ROOT/var/www/html/index.html"
echo "    an invalid password stops the installer before the old scheduler is touched"
if run_migration badpw SINKO_PASSWORD=short; then fail "an invalid password was accepted"; fi
grep -q "must be at least 8 characters" "$WORK/badpw.out" || fail "test setup: the migration did not fail on the password"
grep -q "systemctl stop pihole-bahrain" "$WORK/calls.log" && fail "the old scheduler was stopped although the installer failed before the new one was being set up"
[[ -e "$WORK/units/pihole-bahrain.service.active" && -e "$WORK/units/pihole-bahrain.service.enabled" ]] || fail "the old scheduler was disturbed by an early failure"
echo "    a failure after the old scheduler was stopped (gravity fails) starts it again"
touch "$WORK/pihole-g-fail"
if run_migration failing; then fail "a failing gravity run was ignored"; fi
grep -q "gravity failed" "$WORK/failing.out" || { cat "$WORK/failing.out"; fail "test setup: the migration did not fail where expected"; }
grep -q "The previous version keeps running" "$WORK/failing.out" || fail "no note that the old scheduler was restored"
[[ -e "$WORK/units/pihole-bahrain.service.enabled" && -e "$WORK/units/pihole-bahrain.service.active" && -e "$WORK/units/pihole-bahrain-lists.timer.enabled" ]] \
  || fail "the old scheduler was not put back after the failure"
[[ -d "$ROOT/opt/pihole-bahrain" && -f "$ROOT/etc/systemd/system/pihole-bahrain.service" ]] || fail "old files were removed although the migration failed"
[[ ! -e "$WORK/units/sinko.service.enabled" ]] || fail "the new scheduler was enabled by a failed run"
rm -f "$WORK/pihole-g-fail"
run_migration rerun || { cat "$WORK/rerun.out"; fail "rerunning the migration failed"; }
[[ ! -e "$ROOT/opt/pihole-bahrain" && -e "$WORK/units/sinko.service.enabled" && ! -e "$WORK/units/pihole-bahrain.service.enabled" ]] || fail "the rerun did not complete the migration"

echo "--- the new scheduler crashes again and again: the old version keeps running, and nothing of it is removed"
fresh_start
build_legacy <<EOF
PB_HOSTNAME=family.lan
PB_REF=master
EOF
echo "$OLD_PAGE" >"$ROOT/var/www/html/index.html"
touch "$WORK/sched-crashloop"
if run_migration crashing-scheduler SINKO_SCHEDULER_WAIT=3; then cat "$WORK/crashing-scheduler.out"; fail "a migration whose new scheduler crashes again and again was reported as finished"; fi
grep -q "The new scheduler could not be proven to work" "$WORK/crashing-scheduler.out" || { cat "$WORK/crashing-scheduler.out"; fail "no explanation of what failed"; }
grep -q "The previous version keeps running" "$WORK/crashing-scheduler.out" || fail "no note that the previous version keeps running"
[[ -e "$WORK/units/pihole-bahrain.service.enabled" && -e "$WORK/units/pihole-bahrain.service.active" \
   && -e "$WORK/units/pihole-bahrain-lists.timer.enabled" && -e "$WORK/units/pihole-bahrain-lists.timer.active" ]] \
  || fail "the old scheduler does not run after the new one failed to prove itself"
[[ ! -e "$WORK/units/sinko.service.enabled" && ! -e "$WORK/units/sinko.service.active" \
   && ! -e "$WORK/units/sinko-lists.timer.enabled" && ! -e "$WORK/units/sinko-lists.timer.active" ]] \
  || fail "the new scheduler was left on next to the old one (two schedulers on the same Pi-hole groups)"
[[ -d "$ROOT/opt/pihole-bahrain" && -d "$ROOT/etc/pihole-bahrain" && -f "$ROOT/etc/systemd/system/pihole-bahrain.service" \
   && -L "$ROOT/usr/local/bin/pihole-bahrain" ]] || fail "something of the old version was removed although the new one does not work"
grep -q "Removed the old version's files" "$WORK/crashing-scheduler.out" && fail "the old files were removed after a failed check"
echo "    after the problem is fixed, the same command completes the migration"
rm -f "$WORK/sched-crashloop"
run_migration crashing-rerun || { cat "$WORK/crashing-rerun.out"; fail "the migration did not complete after the problem was fixed"; }
[[ ! -e "$ROOT/opt/pihole-bahrain" && -e "$WORK/units/sinko.service.enabled" && -e "$WORK/units/sinko.service.active" \
   && ! -e "$WORK/units/pihole-bahrain.service.enabled" && ! -e "$WORK/units/pihole-bahrain.service.active" ]] || fail "the rerun did not complete the migration"

echo "--- the installer is ended in the middle of gravity (the long step): the old scheduler is never left disabled"
# kill_installer_during_gravity SIGNAL: starts the migration with "pihole -g" hanging, waits until it hangs, sends the signal.
kill_installer_during_gravity() {
  local signal="$1" pid
  fresh_start
  build_legacy <<EOF
PB_HOSTNAME=family.lan
PB_REF=master
EOF
  touch "$WORK/pihole-g-block"
  setsid env -u SINKO_NONINTERACTIVE PB_NONINTERACTIVE=1 bash "$ROOT/opt/pihole-bahrain/src/install.sh" >"$WORK/kill-$signal.out" 2>&1 &
  pid=$!
  for _ in $(seq 300); do [[ -e "$WORK/pihole-g-started" ]] && break; sleep 0.1; done
  [[ -e "$WORK/pihole-g-started" ]] || { cat "$WORK/kill-$signal.out"; kill -KILL -- "-$pid" 2>/dev/null || true; fail "test setup: the installer never reached gravity"; }
  [[ ! -e "$WORK/units/pihole-bahrain.service.active" ]] || fail "the old scheduler was still running while the new one was being set up"
  [[ -e "$WORK/units/pihole-bahrain.service.enabled" ]] || fail "the old scheduler was disabled although the new one does not exist yet"
  # To the whole process group, as a closed terminal, Ctrl-C or "systemctl stop" would do (the installer's children get it too).
  kill "-$signal" -- "-$pid"
  for _ in $(seq 100); do kill -0 "$pid" 2>/dev/null || break; sleep 0.1; done
  kill -KILL -- "-$pid" 2>/dev/null || true         # whatever it left running (the hanging gravity stub, tee)
  wait "$pid" 2>/dev/null || true
}
kill_installer_during_gravity KILL
[[ -e "$WORK/units/pihole-bahrain.service.enabled" && -e "$WORK/units/pihole-bahrain-lists.timer.enabled" ]] \
  || fail "after kill -9 (no exit handler runs) the old scheduler is not enabled: a reboot would leave the box without one"
[[ ! -e "$WORK/units/sinko.service.enabled" ]] || fail "the new scheduler was enabled by a killed run"
for signal in TERM HUP; do
  kill_installer_during_gravity "$signal"
  # Both old units, not only the first one: the output copy to the log (tee) is ended with the installer, and bash would
  # then die of SIGPIPE at the first note it writes ("Terminated"), in the middle of the function that starts them again.
  [[ -e "$WORK/units/pihole-bahrain.service.enabled" && -e "$WORK/units/pihole-bahrain.service.active" \
     && -e "$WORK/units/pihole-bahrain-lists.timer.enabled" && -e "$WORK/units/pihole-bahrain-lists.timer.active" ]] \
    || { cat "$WORK/kill-$signal.out"; ls "$WORK/units"; fail "after $signal the old scheduler and its list timer were not both started again"; }
  [[ ! -e "$WORK/units/sinko.service.enabled" && ! -e "$WORK/units/sinko.service.active" ]] || fail "the new scheduler runs next to the old one after $signal"
  [[ -d "$ROOT/opt/pihole-bahrain" ]] || fail "the old folder was removed after $signal"       # (no note on the screen: tee got the signal too)
done

echo "migration tests passed"
