#!/usr/bin/env bash
# Where install.sh gets its code from: the newest release, a pinned release, an extracted folder (SINKO_SRC) and the
# developer path. Fake releases are served by a local web server; nothing here reaches the real GitHub.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=tests/lib_stubs.sh disable=SC1091
. "$HERE/lib_stubs.sh"
PORT="${PORT:-18081}"
RELPORT="${RELPORT:-18091}"
fake_system_init

VERSION_NOW="$(tr -d '[:space:]' <"$REPO/VERSION")"
SERVE="$WORK/serve"
BASE="http://127.0.0.1:$RELPORT/releases"
mkdir -p "$SERVE/releases/latest" "$SERVE/releases/download"
make_release "$SERVE/releases/latest/download" "$VERSION_NOW"
make_release "$SERVE/releases/download/v1.2.3" "1.2.3"
serve_dir "$SERVE" "$RELPORT"

# run_install NAME [VAR=value ...]: the installer arrives on stdin, as with curl | bash. Output in $WORK/NAME.out.
run_install() {
  local name="$1"; shift
  env "$@" bash <"$REPO/install.sh" >"$WORK/$name.out" 2>&1
}
expect_refused() {  # NAME pattern what — the run must fail, say the pattern, and install nothing
  local name="$1" pattern="$2" what="$3"
  if run_install "$name" "${@:4}"; then cat "$WORK/$name.out"; fail "$what: the installer accepted it"; fi
  grep -q -- "$pattern" "$WORK/$name.out" || { cat "$WORK/$name.out"; fail "$what: expected message '$pattern'"; }
  [[ ! -e "$ROOT/opt/sinko/bin/sinko" ]] || fail "$what: something was installed anyway"
}

echo "--- a missing release, a server that cannot be reached"
expect_refused nonexist "no published release" "no release yet" SINKO_RELEASE_BASE="http://127.0.0.1:$RELPORT/nothing"
grep -q "SINKO_REF=master" "$WORK/nonexist.out" || fail "the 'no release yet' message does not suggest SINKO_REF=master"
expect_refused offline "Could not reach" "server down" SINKO_RELEASE_BASE="http://127.0.0.1:1/releases"
grep -qi "internet" "$WORK/offline.out" || fail "the offline message does not mention the internet"

echo "--- a download that does not match its checksum is refused"
mkdir -p "$SERVE/releases/bad/latest/download"
cp "$SERVE/releases/latest/download/"* "$SERVE/releases/bad/latest/download/"
printf 'tampered' >>"$SERVE/releases/bad/latest/download/sinko.tar.gz"
expect_refused mismatch "does not match its published checksum" "tampered tarball" SINKO_RELEASE_BASE="$BASE/bad"
echo "--- a malformed checksum file is refused"
mkdir -p "$SERVE/releases/badsum/latest/download"
cp "$SERVE/releases/latest/download/sinko.tar.gz" "$SERVE/releases/badsum/latest/download/"
echo "not-a-hash  sinko.tar.gz" >"$SERVE/releases/badsum/latest/download/sinko.tar.gz.sha256"
expect_refused badsum "checksum published with the release is not in the expected form" "malformed checksum" SINKO_RELEASE_BASE="$BASE/badsum"
printf '%s  other-file.tar.gz\n' "$(cut -d' ' -f1 "$SERVE/releases/latest/download/sinko.tar.gz.sha256")" >"$SERVE/releases/badsum/latest/download/sinko.tar.gz.sha256"
expect_refused badname "checksum published with the release is not in the expected form" "checksum of another file" SINKO_RELEASE_BASE="$BASE/badsum"

echo "--- tarballs with unsafe or missing members are refused even when the checksum matches"
evil_release() {  # NAME KIND: a tarball that carries one bad member (or lacks bin/sinko), with a matching checksum
  local dir="$SERVE/releases/$1/latest/download"
  mkdir -p "$dir"
  python3 - "$dir/sinko.tar.gz" "$2" <<'PYEOF2'
import io, sys, tarfile
out, kind = sys.argv[1], sys.argv[2]
members = [("sinko/VERSION", b"9.9.9\n", 0o644), ("sinko/install.sh", b"#!/bin/sh\n", 0o755), ("sinko/bin/sinko", b"#!/bin/sh\n", 0o755)]
if kind == "missing":
    members = members[:2]
with tarfile.open(out, "w:gz") as tar:
    for name, data, mode in members:
        info = tarfile.TarInfo(name); info.size = len(data); info.mode = mode
        tar.addfile(info, io.BytesIO(data))
    extra = {"dotdot": "sinko/../../escaped.txt", "absolute": "/tmp/escaped-absolute.txt", "outside": "other/file.txt",
             "symlink": "sinko/web"}.get(kind)
    if extra:
        info = tarfile.TarInfo(extra)
        if kind == "symlink":
            info.type, info.linkname = tarfile.SYMTYPE, "/etc"
            tar.addfile(info)
        else:
            info.size = 1
            tar.addfile(info, io.BytesIO(b"x"))
PYEOF2
  ( cd "$dir" && sha256sum sinko.tar.gz >sinko.tar.gz.sha256 )
}
for kind in dotdot absolute symlink outside missing; do
  evil_release "evil-$kind" "$kind"
  expect_refused "evil-$kind" "not a valid Sinko release" "tarball with a $kind member" SINKO_RELEASE_BASE="$BASE/evil-$kind"
done
[[ ! -e "$WORK/escaped.txt" && ! -e "$ROOT/escaped.txt" && ! -e /tmp/escaped-absolute.txt ]] || fail "a member escaped the extraction folder"
[[ ! -e "$ROOT/opt/sinko/src" && ! -e "$ROOT/opt/sinko/src.tmp" ]] || fail "a refused download left a source folder behind"
ls "$ROOT"/opt/sinko/.download.* >/dev/null 2>&1 && fail "a refused download left its temporary folder behind"

echo "--- the newest release is downloaded, verified and installed"
run_install latest SINKO_RELEASE_BASE="$BASE" || { cat "$WORK/latest.out"; fail "installing the newest release failed"; }
grep -q "Downloaded and verified sinko $VERSION_NOW" "$WORK/latest.out" || { cat "$WORK/latest.out"; fail "no download message"; }
grep -q "Starting the installer from the downloaded version" "$WORK/latest.out" || fail "the downloaded installer was not started"
grep -q "Installing version $VERSION_NOW from $ROOT/opt/sinko/src" "$WORK/latest.out" || fail "installed from the wrong place"
grep -q "Sinko is ready" "$WORK/latest.out" || fail "the install did not finish"
[[ "$(cat "$ROOT/opt/sinko/VERSION")" == "$VERSION_NOW" ]] || fail "wrong version installed"
grep -q '^SINKO_REF=latest$' "$ROOT/etc/sinko/config" || fail "SINKO_REF=latest not saved"
grep -q '^SINKO_REPO_SLUG=iret33/sinko$' "$ROOT/etc/sinko/config" || fail "SINKO_REPO_SLUG not saved"
grep -q '^SINKO_REPO=https://github.com/iret33/sinko.git$' "$ROOT/etc/sinko/config" || fail "SINKO_REPO not saved"
grep -q "raw.githubusercontent.com/iret33/sinko/master/lists" "$ROOT/etc/sinko/config" || fail "default lists address not derived from the repository"
[[ "$(stat -c %a "$ROOT/var/lib/sinko")" == 700 ]] || fail "the state folder is not 0700"
[[ "$(stat -c %a "$ROOT/var/lib/sinko/cache/sinko-$VERSION_NOW.tar.gz")" == 600 ]] || fail "the verified tarball was not kept for rollback (0600)"
cmp -s "$ROOT/var/lib/sinko/cache/sinko-$VERSION_NOW.tar.gz" "$SERVE/releases/latest/download/sinko.tar.gz" || fail "the cached tarball is not the download"
ls "$ROOT"/opt/sinko/.download.* >/dev/null 2>&1 && fail "the temporary download folder was left behind"
[[ -x "$ROOT/opt/sinko/tools/seal.sh" && -x "$ROOT/opt/sinko/tools/firstboot.sh" ]] || fail "the image tools are not in /opt/sinko/tools"
[[ "$(find "$ROOT/opt/sinko/tools" -type f | wc -l)" == 2 ]] || fail "something other than seal.sh and firstboot.sh is in /opt/sinko/tools"
[[ -f "$ROOT/etc/systemd/system/sinko-firstboot.service" && ! -e "$WORK/units/sinko-firstboot.service.enabled" ]] || fail "the first-start unit must be installed, not enabled"

echo "--- a password given to the one-liner survives the start of the downloaded installer, and is in no program's environment"
rm -f "$WORK/password-in-env"
run_install pw-oneliner SINKO_RELEASE_BASE="$BASE" SINKO_PASSWORD="Oneliner-Pass-2024" || { cat "$WORK/pw-oneliner.out"; fail "the one-liner with a password failed"; }
grep -q "Starting the installer from the downloaded version" "$WORK/pw-oneliner.out" || fail "test setup: the downloaded installer was not started"
mock_password_works "Oneliner-Pass-2024" || fail "the password given to the one-liner did not reach Pi-hole (lost when the downloaded installer was started?)"
[[ ! -e "$WORK/password-in-env" ]] || { cat "$WORK/password-in-env"; fail "SINKO_PASSWORD was in the environment of a program the installer started"; }
grep -qF "Oneliner-Pass-2024" "$WORK/pw-oneliner.out" "$ROOT/var/log/sinko-install.log" && fail "the password reached the output or the log"
mock_set_password test                       # the password the rest of this file signs in with

echo "--- a failed download does not damage the installed program or its source"
echo marker >"$ROOT/opt/sinko/src/marker"
if run_install failed-update SINKO_RELEASE_BASE="http://127.0.0.1:1/releases"; then fail "install from an unreachable server succeeded"; fi
ls "$ROOT"/opt/sinko/.download.* >/dev/null 2>&1 && fail "a failed download left its temporary folder behind"
[[ -f "$ROOT/opt/sinko/src/marker" && -x "$ROOT/opt/sinko/bin/sinko" ]] || fail "a failed download removed the installed source or program"

echo "--- SINKO_REF=vX.Y.Z installs that release"
run_install tag SINKO_RELEASE_BASE="$BASE" SINKO_REF=v1.2.3 || { cat "$WORK/tag.out"; fail "installing a pinned release failed"; }
grep -q "Installing version 1.2.3 from" "$WORK/tag.out" || { cat "$WORK/tag.out"; fail "pinned release not installed"; }
grep -q '^SINKO_REF=v1.2.3$' "$ROOT/etc/sinko/config" || fail "the pin was not saved"
echo "--- ... and the saved pin is kept by the next run without any setting"
run_install pinned SINKO_RELEASE_BASE="$BASE" || fail "run after pinning failed"
grep -q "Installing version 1.2.3 from" "$WORK/pinned.out" || fail "the saved pin was ignored"
echo "--- ... and an explicit SINKO_REF=latest beats it"
run_install unpin SINKO_RELEASE_BASE="$BASE" SINKO_REF=latest || fail "unpinning failed"
grep -q "Installing version $VERSION_NOW from" "$WORK/unpin.out" || fail "SINKO_REF=latest did not win over the saved pin"

echo "--- SINKO_SRC: an extracted release is used as it is, even without internet"
EXTRACT="$WORK/extracted"
mkdir -p "$EXTRACT"
tar -xzf "$SERVE/releases/latest/download/sinko.tar.gz" -C "$EXTRACT"
before="$(cd "$EXTRACT" && find . -type f -print0 | sort -z | xargs -0 sha256sum | sha256sum)"
touch "$WORK/offline"
SINKO_SRC="$EXTRACT/sinko" bash "$EXTRACT/sinko/install.sh" >"$WORK/src1.out" 2>&1 || { cat "$WORK/src1.out"; fail "install from SINKO_SRC (offline) failed"; }
grep -q "Installing version $VERSION_NOW from $EXTRACT/sinko" "$WORK/src1.out" || fail "SINKO_SRC not used"
grep -q "Starting the installer from the downloaded version" "$WORK/src1.out" && fail "the installer re-executed itself although it already is the one in SINKO_SRC"
grep -qi "no internet" "$WORK/src1.out" && fail "a run from SINKO_SRC checked the internet"
SINKO_SRC="$EXTRACT/sinko" bash "$REPO/install.sh" >"$WORK/src2.out" 2>&1 || { cat "$WORK/src2.out"; fail "install from SINKO_SRC with another installer failed"; }
grep -q "Starting the installer from the downloaded version" "$WORK/src2.out" || fail "the installer shipped in SINKO_SRC was not the one that ran"
after="$(cd "$EXTRACT" && find . -type f -print0 | sort -z | xargs -0 sha256sum | sha256sum)"
[[ "$before" == "$after" ]] || fail "the installer changed the SINKO_SRC folder"
rm -f "$WORK/offline"
if SINKO_SRC="$WORK/does-not-exist" bash "$REPO/install.sh" >"$WORK/src3.out" 2>&1; then fail "a missing SINKO_SRC was accepted"; fi
grep -q "is not a folder" "$WORK/src3.out" || fail "no message for a missing SINKO_SRC"
mkdir -p "$WORK/half"; echo 1.0.0 >"$WORK/half/VERSION"
if SINKO_SRC="$WORK/half" bash "$REPO/install.sh" >"$WORK/src4.out" 2>&1; then fail "an incomplete SINKO_SRC was accepted"; fi
grep -q "does not hold a complete Sinko release" "$WORK/src4.out" || fail "no message for an incomplete SINKO_SRC"

echo "--- the developer path needs the internet, the release path says so too"
touch "$WORK/offline"
rm -rf "$ROOT/opt/sinko/src"
if run_install dev-offline SINKO_REF=master SINKO_REPO=https://github.com/iret33/sinko.git; then fail "developer path without internet succeeded"; fi
grep -q "No internet connection" "$WORK/dev-offline.out" || { cat "$WORK/dev-offline.out"; fail "no offline message on the developer path"; }
rm -f "$WORK/offline"

echo "--- names are validated before they reach curl, git or the settings file"
for bad in "-oProxyCommand=evil" "../x" "a b" "x;y" "master/../../etc"; do
  if run_install bad-ref SINKO_REF="$bad" SINKO_SRC=; then fail "SINKO_REF '$bad' accepted"; fi
  grep -q "SINKO_REF" "$WORK/bad-ref.out" || fail "no message for SINKO_REF '$bad'"
done
for bad in "noslash" "a/b/c" "../x" "a/.." "bad name/x" "x/y;rm"; do
  if run_install bad-slug SINKO_REPO_SLUG="$bad"; then fail "SINKO_REPO_SLUG '$bad' accepted"; fi
  grep -q "SINKO_REPO_SLUG" "$WORK/bad-slug.out" || fail "no message for SINKO_REPO_SLUG '$bad'"
done
if run_install bad-repo SINKO_REPO="--upload-pack=evil"; then fail "SINKO_REPO starting with - accepted"; fi
grep -q "SINKO_REPO" "$WORK/bad-repo.out" || fail "no message for a bad SINKO_REPO"
if run_install bad-base SINKO_RELEASE_BASE="ftp://example.org/x"; then fail "SINKO_RELEASE_BASE ftp accepted"; fi
grep -q "SINKO_RELEASE_BASE" "$WORK/bad-base.out" || fail "no message for a bad SINKO_RELEASE_BASE"

echo "--- a fork: SINKO_REPO_SLUG decides the repository and the default lists, and is kept"
SINKO_REPO_SLUG=me/fork SINKO_SRC="$EXTRACT/sinko" bash "$EXTRACT/sinko/install.sh" >"$WORK/fork.out" 2>&1 || { cat "$WORK/fork.out"; fail "install for a fork failed"; }
grep -q '^SINKO_REPO_SLUG=me/fork$' "$ROOT/etc/sinko/config" || fail "fork slug not saved"
grep -q '^SINKO_REPO=https://github.com/me/fork.git$' "$ROOT/etc/sinko/config" || fail "fork repository not derived from the slug"
grep -q 'raw.githubusercontent.com/me/fork/master/lists' "$ROOT/etc/sinko/config" || fail "fork lists address not derived"
SINKO_SRC="$EXTRACT/sinko" bash "$EXTRACT/sinko/install.sh" >"$WORK/fork2.out" 2>&1 || fail "second run for the fork failed"
grep -q '^SINKO_REPO_SLUG=me/fork$' "$ROOT/etc/sinko/config" || fail "the fork slug was not kept by the next run"
SINKO_REPO=https://github.com/other/thing.git SINKO_SRC="$EXTRACT/sinko" bash "$EXTRACT/sinko/install.sh" >"$WORK/fork3.out" 2>&1 || fail "run with SINKO_REPO failed"
grep -q '^SINKO_REPO_SLUG=other/thing$' "$ROOT/etc/sinko/config" || fail "the slug was not derived from SINKO_REPO"
grep -q 'raw.githubusercontent.com/other/thing/master/lists' "$ROOT/etc/sinko/config" || fail "a saved default lists address did not follow the new project"
SINKO_LISTS_BASE=https://example.org/my-lists SINKO_SRC="$EXTRACT/sinko" bash "$EXTRACT/sinko/install.sh" >"$WORK/lists1.out" 2>&1 || fail "run with SINKO_LISTS_BASE failed"
SINKO_REPO_SLUG=me/fork2 SINKO_SRC="$EXTRACT/sinko" bash "$EXTRACT/sinko/install.sh" >"$WORK/lists2.out" 2>&1 || fail "run with a new slug failed"
grep -q '^SINKO_LISTS_BASE=https://example.org/my-lists$' "$ROOT/etc/sinko/config" || fail "a lists address somebody chose was replaced"

echo "--- only one installer at a time, and the lock is where only root can reach it (not in the world-writable /run/lock)"
LOCKFILE="$ROOT/var/lib/sinko/install.lock"
[[ -f "$LOCKFILE" && "$(stat -c %a "$LOCKFILE")" == 600 && "$(stat -c %a "$ROOT/var/lib/sinko")" == 700 ]] || fail "the lock file is missing, or it or its folder is open to other users"
[[ ! -e "$ROOT/var/lock/sinko-install.lock" ]] || fail "the installer still uses a lock in the shared lock folder"
exec 8>>"$LOCKFILE"
flock -n 8 || fail "test could not take the lock"
if SINKO_SRC="$EXTRACT/sinko" bash "$EXTRACT/sinko/install.sh" >"$WORK/lock.out" 2>&1; then fail "a second installer ran while the first held the lock"; fi
grep -q "Another Sinko installation or update is running" "$WORK/lock.out" || fail "no message about the lock"
exec 8>&-
SINKO_SRC="$EXTRACT/sinko" bash "$EXTRACT/sinko/install.sh" >"$WORK/lock2.out" 2>&1 || { cat "$WORK/lock2.out"; fail "the installer did not run after the lock was released"; }
echo "    the updater's own lock (a different file: it holds it while it runs the installer) does not stop the installer"
exec 8>>"$ROOT/var/lib/sinko/lock"
flock -n 8 || fail "test could not take the updater's lock"
SINKO_SRC="$EXTRACT/sinko" bash "$EXTRACT/sinko/install.sh" >"$WORK/lock3.out" 2>&1 || { cat "$WORK/lock3.out"; fail "the installer was stopped by the updater's lock, which it is started under"; }
exec 8>&-

echo "--- the update source: https only (plain http only to this machine itself), no redirect to anything else, and it is saved"
for plain in http://example.org/releases http://127.0.0.1.example.org/releases http://localhost@example.org/releases ftp://example.org/x; do
  if run_install plain-base SINKO_RELEASE_BASE="$plain"; then fail "the update source $plain was accepted"; fi
  grep -q "SINKO_RELEASE_BASE .* must be an https:// address" "$WORK/plain-base.out" || { cat "$WORK/plain-base.out"; fail "no https message for the update source $plain"; }
done
if run_install plain-api SINKO_RELEASE_API="http://api.example.org/latest"; then fail "a plain-http SINKO_RELEASE_API was accepted"; fi
grep -q "SINKO_RELEASE_API .* must be an https:// address" "$WORK/plain-api.out" || fail "no https message for SINKO_RELEASE_API"
: >"$WORK/curl.log"
if run_install https-base SINKO_RELEASE_BASE="https://127.0.0.1:1/releases"; then fail "an https update source that is not there was accepted"; fi
grep -q "Could not reach" "$WORK/https-base.out" || { cat "$WORK/https-base.out"; fail "no message that the update source cannot be reached"; }
grep -q -- "--proto =https --proto-redir =https .*https://127.0.0.1:1/releases" "$WORK/curl.log" || { cat "$WORK/curl.log"; fail "curl was not limited to https (and https redirects) for an https update source"; }
grep -q -- "--proto =http,https" "$WORK/curl.log" && fail "an https update source was fetched with plain http allowed"
: >"$WORK/curl.log"
run_install loopback-base SINKO_RELEASE_BASE="$BASE" || { cat "$WORK/loopback-base.out"; fail "a server on this machine itself was refused"; }
grep -q -- "--proto =http,https --proto-redir =http,https .*$BASE/latest/download/sinko.tar.gz" "$WORK/curl.log" || { cat "$WORK/curl.log"; fail "the test server was not fetched under the test-hook protocol rule"; }
echo "    the first connection check is limited to https as well"
touch "$WORK/offline"; rm -rf "$ROOT/opt/sinko/src"; : >"$WORK/curl.log"
run_install dev-offline2 SINKO_REF=master SINKO_REPO=https://github.com/iret33/sinko.git || true
rm -f "$WORK/offline"
grep -q -- "--proto =https --proto-redir =https .*https://github.com" "$WORK/curl.log" || fail "the internet check is not limited to https"
echo "    a mirror that was given once is saved (so updates and the update check use it), the project's own address is not"
grep -q "^SINKO_RELEASE_BASE=$BASE\$" "$ROOT/etc/sinko/config" || { cat "$ROOT/etc/sinko/config"; fail "the update source was not saved"; }
run_install saved-base || { cat "$WORK/saved-base.out"; fail "a run without the variable failed"; }
grep -q "Downloaded and verified" "$WORK/saved-base.out" || fail "the saved update source was not used by the next run"
run_install dflt-base SINKO_REPO_SLUG=iret33/sinko SINKO_RELEASE_BASE=https://github.com/iret33/sinko/releases SINKO_SRC="$EXTRACT/sinko" \
  || { cat "$WORK/dflt-base.out"; fail "install with the project's own update address failed"; }
grep -q '^SINKO_RELEASE_BASE=' "$ROOT/etc/sinko/config" && fail "the project's own address was saved as if it were a mirror"
grep -q '^SINKO_RELEASE_API=' "$ROOT/etc/sinko/config" && fail "the project's own release address was saved"
run_install api-mirror SINKO_REPO_SLUG=iret33/sinko SINKO_RELEASE_API="https://mirror.example.org/latest" SINKO_SRC="$EXTRACT/sinko" \
  || { cat "$WORK/api-mirror.out"; fail "an https SINKO_RELEASE_API was refused"; }
grep -q '^SINKO_RELEASE_API=https://mirror.example.org/latest$' "$ROOT/etc/sinko/config" || fail "SINKO_RELEASE_API was not saved"

echo "fetch tests passed"
