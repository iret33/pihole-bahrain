"""A fake GitHub for the update tests: a local HTTP server that serves release metadata and release files, and a
builder for tarballs with the layout architecture.md promises (one top folder `sinko/`).

The fake release's install.sh does what the real one has to do for the update engine to work: it installs its own
bin/sinko into $SINKO_APP_DIR/bin and records what it was told. Its bin/sinko answers `selfcheck` the way the
test asked, so install, self-check, rollback and the result file can all run for real.
"""
import hashlib
import http.server
import io
import json
import tarfile
import threading

INSTALL_SH = """#!/usr/bin/env bash
set -e
echo "install $(cat "$SINKO_SRC/VERSION") src=$SINKO_SRC noninteractive=$SINKO_NONINTERACTIVE ref=$SINKO_REF" >> "$FAKE_LOG"
if [ -n "${{SINKO_CONFIG_FILE:-}}" ]; then
  # What the real installer does before anything can go wrong later: it saves the ref it was given as SINKO_REF.
  {{ grep -v '^SINKO_REF=' "$SINKO_CONFIG_FILE" 2>/dev/null || true; echo "SINKO_REF=$SINKO_REF"; }} > "$SINKO_CONFIG_FILE.new"
  mv "$SINKO_CONFIG_FILE.new" "$SINKO_CONFIG_FILE"
fi
{install_fail}
install -D -m 755 "$SINKO_SRC/bin/sinko" "$SINKO_APP_DIR/bin/sinko"
install -D -m 644 "$SINKO_SRC/VERSION" "$SINKO_APP_DIR/VERSION"
"""

FAKE_PROGRAM = """#!/usr/bin/env python3
import sys
VERSION = "{version}"
if sys.argv[1:2] == ["selfcheck"]:
    if {selfcheck_ok}:
        print("  ok    fake check passes")
        sys.exit(0)
    print("  FAIL  the fake page is broken")
    sys.exit(1)
print(VERSION)
"""


def release_files(version, install_ok=True, selfcheck_ok=True, extra=None):
    """{path inside the tarball: (bytes, mode)} for a well-formed release."""
    files = {
        "sinko/VERSION": (version.encode() + b"\n", 0o644),
        "sinko/install.sh": (INSTALL_SH.format(install_fail="" if install_ok else "exit 1").encode(), 0o755),
        "sinko/uninstall.sh": (b"#!/bin/sh\n", 0o755),
        "sinko/bin/sinko": (FAKE_PROGRAM.format(version=version, selfcheck_ok=bool(selfcheck_ok)).encode(), 0o755),
        "sinko/lists/services.json": (b"{}", 0o644),
        "sinko/web/index.html": (b"<html></html>", 0o644),
        "sinko/systemd/sinko.service": (b"[Unit]\n", 0o644),
        "sinko/LICENSE": (b"GPL\n", 0o644),
        "sinko/NOTICE": (b"notice\n", 0o644),
    }
    files.update(extra or {})
    return files


def tar_bytes(members, gz=True):
    """members: iterable of (TarInfo, bytes or None). Returns the .tar.gz bytes."""
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w:gz" if gz else "w", format=tarfile.PAX_FORMAT) as tar:
        for info, data in members:
            tar.addfile(info, io.BytesIO(data) if data is not None else None)
    return raw.getvalue()


def file_member(name, data, mode=0o644):
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.mode = mode
    return info, data


def dir_member(name):
    info = tarfile.TarInfo(name)
    info.type = tarfile.DIRTYPE
    info.mode = 0o755
    return info, None


def build_release(version, install_ok=True, selfcheck_ok=True, extra=None, drop=()):
    members = [dir_member("sinko")]
    for name, (data, mode) in sorted(release_files(version, install_ok, selfcheck_ok, extra).items()):
        if name not in drop:
            members.append(file_member(name, data, mode))
    return tar_bytes(members)


def sha_line(tarball, name="sinko.tar.gz"):
    return ("%s  %s\n" % (hashlib.sha256(tarball).hexdigest(), name)).encode()


class Site:
    """What the server serves. `requests` records every path asked for."""

    def __init__(self):
        self.routes = {}                  # path -> (status, body, headers) or a callable(handler)
        self.requests = []
        self.posts = []                   # (path, headers, body) of every POST
        self.lock = threading.Lock()
        self.base = self.api = None

    def conf(self, **extra):
        conf = {"SINKO_RELEASE_BASE": self.base, "SINKO_RELEASE_API": self.api, "SINKO_REF": "latest"}
        conf.update(extra)
        return conf

    def add_release(self, version, tarball=None, sha=None, **build):
        tarball = build_release(version, **build) if tarball is None else tarball
        base = "/releases/download/v%s/" % version
        self.routes[base + "sinko.tar.gz"] = (200, tarball, {})
        if sha is not False:
            self.routes[base + "sinko.tar.gz.sha256"] = (200, sha_line(tarball) if sha is None else sha, {})
        return tarball

    def make_latest(self, version):
        """/releases/latest/download/... redirects to the tag, the way GitHub does."""
        for asset in ("sinko.tar.gz", "sinko.tar.gz.sha256"):
            target = "/releases/download/v%s/%s" % (version, asset)
            self.routes["/releases/latest/download/" + asset] = (302, b"", {"Location": target})

    def set_api(self, payload, status=200):
        self.routes["/api/latest"] = (status, json.dumps(payload).encode(), {"Content-Type": "application/json"})

    def release_json(self, version, **kw):
        return dict({"tag_name": "v" + version, "draft": False, "prerelease": False,
                     "html_url": "https://evil.example/not-github"}, **kw)

    def count(self, fragment):
        with self.lock:
            return sum(1 for p in self.requests if fragment in p)


def serve():
    site = Site()

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            with site.lock:
                site.posts.append((self.path, dict(self.headers), body))
            self.do_GET()

        def do_GET(self):
            with site.lock:
                site.requests.append(self.path)
            route = site.routes.get(self.path)
            if route is None:
                body = b"not found"
                self.send_response(404)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if callable(route):
                return route(self)
            status, body, headers = route
            self.send_response(status)
            for key, value in headers.items():
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True).start()
    origin = "http://127.0.0.1:%d" % httpd.server_port
    site.base = origin + "/releases"
    site.api = origin + "/api/latest"
    return httpd, site


class Counter:
    """A fake of the counter service (telemetry/): POST /v1/ping and POST /v1/forget with the rules of its contract.
    `known` is what its database holds (ids); `forgotten` the ids it was asked to delete, in order. A test can make it
    fail: `fail_forget`/`fail_ping` answer HTTP 500 that many times, `forget_body` replaces the answer to a forget."""

    ID_CHARS = set("0123456789abcdef")

    def __init__(self):
        self.lock = threading.Lock()
        self.known = set()
        self.pings = []                   # ids, in order
        self.forgotten = []
        self.requests = []                # (path, headers, body bytes) of everything it was sent
        self.fail_forget = 0
        self.fail_ping = 0
        self.forget_body = None           # bytes: the answer to a forget instead of {"forgotten": true}
        self.url = None

    def valid_id(self, value):
        return isinstance(value, str) and len(value) == 32 and set(value) <= self.ID_CHARS


def serve_counter():
    counter = Counter()

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def reply(self, status, payload):
            body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self.reply(404, {"error": "not found"})

        def do_POST(self):
            raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            with counter.lock:
                counter.requests.append((self.path, dict(self.headers), raw))
                try:
                    data = json.loads(raw.decode("utf-8"))
                except ValueError:
                    return self.reply(400, {"error": "bad json"})
                if not isinstance(data, dict) or len(raw) > 512 or not counter.valid_id(data.get("id")):
                    return self.reply(400, {"error": "bad body"})
                if self.path == "/v1/ping":
                    if counter.fail_ping:
                        counter.fail_ping -= 1
                        return self.reply(500, {"error": "database busy"})
                    counter.known.add(data["id"])
                    counter.pings.append(data["id"])
                    return self.reply(200, {"online": len(counter.known), "total": len(counter.known)})
                if self.path == "/v1/forget":
                    if set(data) != {"id"}:
                        return self.reply(400, {"error": "unexpected fields"})
                    if counter.fail_forget:
                        counter.fail_forget -= 1
                        return self.reply(500, {"error": "database busy"})
                    counter.known.discard(data["id"])
                    counter.forgotten.append(data["id"])
                    return self.reply(200, counter.forget_body if counter.forget_body is not None else {"forgotten": True})
                return self.reply(404, {"error": "not found"})

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True).start()
    counter.url = "http://127.0.0.1:%d" % httpd.server_port
    return httpd, counter


def serve_pihole():
    """tests/mock_pihole.py's server, started with a short poll interval: its serve() waits up to half a second
    when it is shut down, which adds up over a hundred tests. Same classes, same behaviour."""
    import mock_pihole
    store = mock_pihole.Store()
    handler = type("H", (mock_pihole.Handler,), {"store": store, "web_dir": None})
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True).start()
    return httpd, store


def serve_truncated(good_after=None):
    """A Pi-hole whose every answer is cut short: it promises 500 bytes and sends 11, then closes the connection (FTL
    restarting, a connection dropped half way). urllib then raises http.client.IncompleteRead, which is neither an OSError
    nor a ValueError. With `good_after` = n the first n answers are cut and the later ones are `{}` with status 200.
    Returns (httpd, calls): `calls` is a list that holds the path of every request, `httpd.url` the address."""
    calls = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def handle_any(self):
            calls.append(self.path)
            if self.headers.get("Content-Length"):
                self.rfile.read(int(self.headers["Content-Length"]))
            if good_after is not None and len(calls) > good_after:
                body = b"{}"
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", "500")
            self.end_headers()
            self.wfile.write(b'{"session":')
            self.close_connection = True

        do_GET = do_POST = do_PUT = do_DELETE = handle_any

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True).start()
    httpd.url = "http://127.0.0.1:%d" % httpd.server_port
    return httpd, calls
