#!/usr/bin/env bash
# Where install.sh gets its code from: the newest release, a pinned release, an extracted folder (SINKO_SRC) and the
# developer path. Fake releases are served by a local web server; nothing here reaches the real GitHub.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=tests/lib_stubs.sh
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

echo "--- only one installer at a time"
exec 8>"$ROOT/var/lock/sinko-install.lock"
flock -n 8 || fail "test could not take the lock"
if SINKO_SRC="$EXTRACT/sinko" bash "$EXTRACT/sinko/install.sh" >"$WORK/lock.out" 2>&1; then fail "a second installer ran while the first held the lock"; fi
grep -q "Another Sinko installation or update is running" "$WORK/lock.out" || fail "no message about the lock"
exec 8>&-
SINKO_SRC="$EXTRACT/sinko" bash "$EXTRACT/sinko/install.sh" >"$WORK/lock2.out" 2>&1 || { cat "$WORK/lock2.out"; fail "the installer did not run after the lock was released"; }

echo "fetch tests passed"
