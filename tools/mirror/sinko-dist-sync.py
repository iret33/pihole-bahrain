#!/usr/bin/env python3
"""Mirror Sinko's releases, block lists and website from GitHub to the project's own server.

Boxes fetch everything they need from updates.ioai.bh, and the website lives at sinko.ioai.bh, so the repository on
GitHub can be private. This runs on that server every few minutes (tools/mirror/systemd/) and copies:

  releases   every published vX.Y.Z release that has its three files, checksum checked, into
             UPDATES/releases/download/vX.Y.Z/; UPDATES/releases/latest/download points at the release GitHub calls
             "latest", and UPDATES/api/releases/latest describes it the way api.github.com does (the box reads only
             tag_name, draft and prerelease from it)
  lists      lists/ of master, into UPDATES/lists (the nightly block-list refresh of every box reads there)
  site       site/ of master, built with site/assemble.sh, into SITE (a symbolic link to the current build)

Every copy is made beside the live one and swapped in with one rename, so a box or a visitor never sees half of one,
and a failed run leaves the last good copy in place. A private repository needs a read-only token in TOKEN_FILE
(a fine-grained token with "Contents: read" on this one repository); a public one needs none.

    sinko-dist-sync.py [releases] [lists] [site]      (no argument: all three)

Settings come from the environment: SINKO_DIST_REPO, SINKO_DIST_UPDATES, SINKO_DIST_SITE, SINKO_DIST_SITE_URL,
SINKO_DIST_TOKEN_FILE, SINKO_DIST_STATE (defaults below).
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

REPO = os.environ.get("SINKO_DIST_REPO", "iret33/sinko")
UPDATES = os.environ.get("SINKO_DIST_UPDATES", "/var/www/updates.ioai.bh")
SITE = os.environ.get("SINKO_DIST_SITE", "/var/www/sinko-site/live")
SITE_URL = os.environ.get("SINKO_DIST_SITE_URL", "https://sinko.ioai.bh")
TOKEN_FILE = os.environ.get("SINKO_DIST_TOKEN_FILE", "/etc/sinko-dist/github-token")
STATE = os.environ.get("SINKO_DIST_STATE", "/var/lib/sinko-dist")
API = os.environ.get("SINKO_DIST_API", "https://api.github.com")

ASSETS = ("install.sh", "sinko.tar.gz", "sinko.tar.gz.sha256")
TAG_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
LIST_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,60}\.(txt|json)$|^LICENSE$")
KEEP_OLD = 2          # earlier copies of the lists and the site kept beside the live one, for a quick look back


def log(msg):
    print(msg, flush=True)


class Incomplete(Exception):
    """A release that has no files yet: the Release workflow adds them a few minutes after the tag."""


def token():
    try:
        with open(TOKEN_FILE, encoding="utf-8") as fh:
            value = fh.read().strip()
    except OSError:
        return None
    return value or None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_opener = urllib.request.build_opener(_NoRedirect)


def fetch(url, accept="application/vnd.github+json", hops=5):
    """GET with the token for GitHub only. A redirect (release files, the tarball) is followed without the token: the
    storage behind GitHub refuses a second credential, and it must never leave GitHub."""
    headers = {"User-Agent": "sinko-dist-sync", "Accept": accept}
    host = urllib.parse.urlsplit(url).hostname or ""
    tok = token()
    if tok and (host == "api.github.com" or url.startswith(API)):
        headers["Authorization"] = "Bearer " + tok
        headers["X-GitHub-Api-Version"] = "2022-11-28"
    req = urllib.request.Request(url, headers=headers)
    try:
        with _opener.open(req, timeout=120) as resp:
            return resp.read()
    except urllib.error.HTTPError as err:
        if err.code in (301, 302, 303, 307, 308) and hops > 0:
            location = err.headers.get("Location")
            if not location:
                raise
            if urllib.parse.urlsplit(location).scheme != "https":
                raise RuntimeError("refusing a redirect to a non-https address")
            return fetch(location, accept="*/*", hops=hops - 1)
        raise


def fetch_json(path):
    return json.loads(fetch(API + path).decode("utf-8"))


def write_atomic(path, data, mode=0o644):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".tmp-")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def symlink_atomic(target, link):
    os.makedirs(os.path.dirname(link), exist_ok=True)
    tmp = "%s.tmp-%d" % (link, os.getpid())
    try:
        os.unlink(tmp)
    except FileNotFoundError:
        pass
    os.symlink(target, tmp)
    os.replace(tmp, link)


def read_state(name):
    try:
        with open(os.path.join(STATE, name), encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def write_state(name, value):
    write_atomic(os.path.join(STATE, name), (value + "\n").encode("utf-8"), 0o600)


def prune(parent, prefix, live):
    """Removes earlier copies named prefix* in parent, keeping the live one and the KEEP_OLD newest others."""
    others = sorted((d for d in os.listdir(parent) if d.startswith(prefix) and d != live),
                    key=lambda d: os.path.getmtime(os.path.join(parent, d)), reverse=True)
    for name in others[KEEP_OLD:]:
        shutil.rmtree(os.path.join(parent, name), ignore_errors=True)


# ---------------------------------------------------------------- releases

def parse_sha256(text):
    first = text.strip().split()[0] if text.strip() else ""
    if not re.fullmatch(r"[0-9a-f]{64}", first):
        raise RuntimeError("the checksum file does not start with a sha256")
    return first


def mirror_release(rel):
    """Copies one release's three files into releases/download/<tag>/, once. Returns the tag."""
    tag = rel["tag_name"]
    folder = os.path.join(UPDATES, "releases", "download")
    dest = os.path.join(folder, tag)
    if all(os.path.isfile(os.path.join(dest, name)) for name in ASSETS):
        return tag
    assets = {a.get("name"): a for a in rel.get("assets") or []}
    if not all(name in assets for name in ASSETS):
        raise Incomplete(tag)
    os.makedirs(folder, exist_ok=True)
    tmp = tempfile.mkdtemp(dir=folder, prefix=".tmp-%s-" % tag)
    try:
        data = {}
        for name in ASSETS:
            data[name] = fetch(assets[name]["url"], accept="application/octet-stream")
        want = parse_sha256(data["sinko.tar.gz.sha256"].decode("ascii", "replace"))
        got = hashlib.sha256(data["sinko.tar.gz"]).hexdigest()
        if got != want:
            raise RuntimeError("%s: sinko.tar.gz does not match its checksum (%s, expected %s)" % (tag, got, want))
        for name, blob in data.items():
            with open(os.path.join(tmp, name), "wb") as fh:
                fh.write(blob)
                fh.flush()
                os.fsync(fh.fileno())
            os.chmod(os.path.join(tmp, name), 0o644)
        os.chmod(tmp, 0o755)
        if os.path.exists(dest):
            shutil.rmtree(dest)
        os.rename(tmp, dest)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    log("release %s copied (%d bytes)" % (tag, len(data["sinko.tar.gz"])))
    return tag


def sync_releases():
    releases = fetch_json("/repos/%s/releases?per_page=50" % REPO)
    published = [r for r in releases if not r.get("draft") and TAG_RE.match(r.get("tag_name") or "")]
    mirrored = set()
    for rel in published:
        try:
            mirrored.add(mirror_release(rel))
        except Incomplete as err:
            log("release %s has no files yet; trying again next run" % err)
    try:
        latest = fetch_json("/repos/%s/releases/latest" % REPO)
    except urllib.error.HTTPError as err:
        if err.code == 404:
            log("GitHub has no latest release: the mirror keeps the one it has")
            return
        raise
    tag = latest.get("tag_name") or ""
    if not TAG_RE.match(tag) or latest.get("draft") or latest.get("prerelease"):
        log("GitHub's latest release '%s' is not a plain vX.Y.Z release: the mirror keeps the one it has" % tag)
        return
    if tag not in mirrored:
        log("the latest release %s is not copied yet: the mirror keeps the one it has" % tag)
        return
    symlink_atomic(os.path.join("..", "download", tag), os.path.join(UPDATES, "releases", "latest", "download"))
    body = {"tag_name": tag, "name": latest.get("name") or "Sinko " + tag, "draft": False, "prerelease": False,
            "published_at": latest.get("published_at"), "html_url": SITE_URL + "/#changelog"}
    path = os.path.join(UPDATES, "api", "releases", "latest")
    new = (json.dumps(body, indent=1) + "\n").encode("utf-8")
    try:
        with open(path, "rb") as fh:
            old = fh.read()
    except OSError:
        old = b""
    if new != old:
        write_atomic(path, new)
        log("latest is %s" % tag)


# ---------------------------------------------------------------- lists

def sync_lists():
    head = fetch_json("/repos/%s/commits?path=lists&sha=master&per_page=1" % REPO)
    sha = head[0]["sha"] if head else ""
    live = os.path.join(UPDATES, "lists")
    if sha and sha == read_state("lists.sha") and os.path.isdir(live):
        return
    entries = fetch_json("/repos/%s/contents/lists?ref=master" % REPO)
    names = [e["name"] for e in entries if e.get("type") == "file"]
    bad = [n for n in names if not LIST_NAME_RE.match(n)]
    if bad:
        raise RuntimeError("unexpected names in lists/: %s" % ", ".join(bad))
    if "services.json" not in names or "guard.txt" not in names:
        raise RuntimeError("lists/ on master has no services.json or guard.txt: not copied")
    tmp = tempfile.mkdtemp(dir=UPDATES, prefix=".lists-")
    try:
        for name in names:
            data = fetch("%s/repos/%s/contents/lists/%s?ref=master" % (API, REPO, urllib.parse.quote(name)),
                         accept="application/vnd.github.raw")
            with open(os.path.join(tmp, name), "wb") as fh:
                fh.write(data)
            os.chmod(os.path.join(tmp, name), 0o644)
        json.loads(open(os.path.join(tmp, "services.json"), encoding="utf-8").read())    # a broken catalogue stays out
        os.chmod(tmp, 0o755)
        final = os.path.join(UPDATES, "lists-" + (sha[:12] or str(int(time.time()))))
        if os.path.exists(final):
            shutil.rmtree(final)
        os.rename(tmp, final)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    if os.path.isdir(live) and not os.path.islink(live):
        os.rename(live, live + "-old-%d" % int(time.time()))      # a plain folder from a manual copy: kept aside once
    symlink_atomic(os.path.basename(final), live)
    write_state("lists.sha", sha)
    prune(UPDATES, "lists-", os.path.basename(final))
    log("lists copied from %s (%d files)" % (sha[:12], len(names)))


# ---------------------------------------------------------------- site

def sync_site():
    sha = fetch_json("/repos/%s/commits/master" % REPO)["sha"]
    if sha == read_state("site.sha") and os.path.isdir(SITE):
        return
    parent = os.path.dirname(SITE.rstrip("/"))
    work = tempfile.mkdtemp(prefix="sinko-site-")
    try:
        tarball = fetch("%s/repos/%s/tarball/%s" % (API, REPO, sha))
        path = os.path.join(work, "src.tar.gz")
        with open(path, "wb") as fh:
            fh.write(tarball)
        with tarfile.open(path) as tar:
            tar.extractall(os.path.join(work, "src"), filter="data")
        tops = os.listdir(os.path.join(work, "src"))
        if len(tops) != 1:
            raise RuntimeError("the tarball of master has %d top folders" % len(tops))
        root = os.path.join(work, "src", tops[0])
        built = os.path.join(work, "site")
        done = subprocess.run(["bash", os.path.join(root, "site", "assemble.sh"), built, SITE_URL],
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        if done.returncode != 0:
            raise RuntimeError("site/assemble.sh failed: %s" % done.stdout.strip()[-400:])
        final = os.path.join(parent, os.path.basename(SITE.rstrip("/")) + "-" + sha[:12])
        if os.path.exists(final):
            shutil.rmtree(final)
        shutil.copytree(built, final)
        for dirpath, dirnames, filenames in os.walk(final):
            os.chmod(dirpath, 0o755)
            for name in filenames:
                os.chmod(os.path.join(dirpath, name), 0o644)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    if os.path.isdir(SITE) and not os.path.islink(SITE):
        os.rename(SITE, SITE + "-old-%d" % int(time.time()))
    symlink_atomic(os.path.basename(final), SITE.rstrip("/"))
    write_state("site.sha", sha)
    prune(parent, os.path.basename(SITE.rstrip("/")) + "-", os.path.basename(final))
    log("site built from %s" % sha[:12])


STEPS = {"releases": sync_releases, "lists": sync_lists, "site": sync_site}


def main(argv):
    wanted = argv or list(STEPS)
    unknown = [w for w in wanted if w not in STEPS]
    if unknown:
        print("usage: sinko-dist-sync.py [%s]" % "] [".join(STEPS), file=sys.stderr)
        return 2
    os.makedirs(STATE, exist_ok=True)
    os.makedirs(UPDATES, exist_ok=True)
    failed = 0
    for name in wanted:
        try:
            STEPS[name]()
        except Exception as err:          # one step failing leaves the others, and the last good copy, in place
            failed += 1
            log("%s: FAILED: %s" % (name, err))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
