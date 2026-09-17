"""A small in-memory imitation of the Pi-hole v6 REST API.

It implements only what pihole-bahrain uses, with the same URL shapes, payloads
and quirks as FTL (groups addressed by name, lists by address + ?type=,
domains by /type/kind/domain, PUT keeps groups when "groups" is omitted,
comment is cleared when omitted, etc.).

Run standalone to develop the web page without a Raspberry Pi:
    python3 tests/mock_pihole.py --web web --port 8080   (password: test)
"""
from __future__ import annotations

import argparse
import itertools
import json
import os
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PASSWORD = "test"


class Store:
    def __init__(self):
        self.lock = threading.Lock()
        self.ids = itertools.count(1)
        self.groups = [{"id": 0, "name": "Default", "enabled": True, "comment": "The default group"}]
        self.lists = []
        self.domains = []
        self.clients = []
        self.sessions = set()
        self.devices = [
            {"id": 1, "hwaddr": "aa:bb:cc:00:00:01", "macVendor": "Apple, Inc.", "lastQuery": int(time.time()) - 30,
             "numQueries": 1200, "ips": [{"ip": "192.168.1.21", "name": "Sara-iPad"}]},
            {"id": 2, "hwaddr": "aa:bb:cc:00:00:02", "macVendor": "Samsung", "lastQuery": int(time.time()) - 400,
             "numQueries": 800, "ips": [{"ip": "192.168.1.22", "name": "ali-galaxy"}]},
            {"id": 3, "hwaddr": "ip-192.168.1.30", "macVendor": "", "lastQuery": int(time.time()) - 5000,
             "numQueries": 10, "ips": [{"ip": "192.168.1.30", "name": ""}]},
        ]
        self.writes = 0

    def gid(self, name):
        return next((g["id"] for g in self.groups if g["name"] == name), None)


class Handler(BaseHTTPRequestHandler):
    store: Store = None
    web_dir: str | None = None
    require_auth = True

    def log_message(self, *a):  # quiet
        pass

    # ---------- helpers ----------
    def send(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def err(self, code, key, msg):
        self.send(code, {"error": {"key": key, "message": msg, "hint": None}})

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        return json.loads(self.rfile.read(n))

    def authed(self):
        return (not self.require_auth) or self.headers.get("sid") in self.store.sessions

    # ---------- routing ----------
    def do_GET(self):
        self.route("GET")

    def do_POST(self):
        self.route("POST")

    def do_PUT(self):
        self.route("PUT")

    def do_DELETE(self):
        self.route("DELETE")

    def route(self, method):
        url = urllib.parse.urlsplit(self.path)
        path = url.path
        query = urllib.parse.parse_qs(url.query)
        if not path.startswith("/api"):
            return self.static(path)
        parts = [urllib.parse.unquote(p) for p in path.split("/")[2:]]
        s = self.store
        with s.lock:
            if parts == ["auth"]:
                if method == "GET":
                    ok = self.authed()
                    return self.send(200 if ok else 401, {"session": {"valid": ok, "totp": False,
                                     "sid": None, "validity": 1800 if ok else -1}})
                if method == "POST":
                    if self.body().get("password") == PASSWORD:
                        sid = "sid%d" % next(s.ids)
                        s.sessions.add(sid)
                        return self.send(200, {"session": {"valid": True, "totp": False, "sid": sid,
                                               "validity": 1800}})
                    return self.send(401, {"session": {"valid": False, "totp": False, "sid": None,
                                           "message": "password incorrect"}})
                if method == "DELETE":
                    s.sessions.discard(self.headers.get("sid"))
                    return self.send(204, {})
            if not self.authed():
                return self.err(401, "unauthorized", "Unauthorized")
            if parts == ["info", "client"]:
                return self.send(200, {"remote_addr": "192.168.1.21"})
            if parts == ["info", "version"]:
                return self.send(200, {"version": {"core": {"local": {"version": "v6.3"}}}})
            if parts == ["network", "devices"]:
                return self.send(200, {"devices": s.devices})
            if parts and parts[0] == "groups":
                return self.groups(method, parts[1:])
            if parts and parts[0] == "lists":
                return self.lists(method, parts[1:], query)
            if parts and parts[0] == "domains":
                return self.domains(method, parts[1:])
            if parts and parts[0] == "clients":
                return self.clients(method, parts[1:])
        return self.err(404, "not_found", "Not found")

    def set_groups(self, row, b):
        if "groups" in b:
            row["groups"] = sorted(set(b["groups"]))

    def groups(self, method, rest):
        s = self.store
        if method == "GET":
            if rest:
                return self.send(200, {"groups": [g for g in s.groups if g["name"] == rest[0]]})
            return self.send(200, {"groups": s.groups})
        b = self.body()
        if method == "POST":
            names = b["name"] if isinstance(b["name"], list) else [b["name"]]
            for n in names:
                if s.gid(n) is not None:
                    return self.err(400, "database_error", "UNIQUE constraint failed: group.name")
                s.groups.append({"id": next(s.ids), "name": n, "enabled": b.get("enabled", True),
                                 "comment": b.get("comment") or None})
            s.writes += 1
            return self.send(201, {"groups": [g for g in s.groups if g["name"] in names]})
        name = rest[0]
        g = next((g for g in s.groups if g["name"] == name), None)
        if method == "PUT":
            if g is None:
                g = {"id": next(s.ids), "name": name}
                s.groups.append(g)
            g.update(name=b.get("name", name), enabled=b.get("enabled", True), comment=b.get("comment") or None)
            s.writes += 1
            return self.send(200, {"groups": [g]})
        if method == "DELETE":
            if g is None:
                return self.err(404, "not_found", "Not found")
            s.groups.remove(g)
            for coll in (s.lists, s.domains, s.clients):
                for row in coll:
                    row["groups"] = [x for x in row["groups"] if x != g["id"]]
            return self.send(204, {})

    def lists(self, method, rest, query):
        s = self.store
        if method == "GET":
            return self.send(200, {"lists": s.lists})
        if "type" not in query:
            return self.err(400, "bad_request", "Specify type parameter")
        b = self.body()
        if method == "POST":
            addr = b["address"]
            if any(l["address"] == addr for l in s.lists):
                return self.err(400, "database_error", "UNIQUE constraint failed")
            row = {"id": next(s.ids), "address": addr, "type": query["type"][0], "comment": b.get("comment") or None,
                   "enabled": b.get("enabled", True), "groups": [0], "number": 0}
            self.set_groups(row, b)
            s.lists.append(row)
            return self.send(201, {"lists": [row]})
        addr = rest[0]
        row = next((l for l in s.lists if l["address"] == addr), None)
        if method == "PUT":
            if row is None:
                return self.err(404, "not_found", "no such list")
            row.update(comment=b.get("comment") or None, enabled=b.get("enabled", True))
            self.set_groups(row, b)
            return self.send(200, {"lists": [row]})
        if method == "DELETE":
            if row is None:
                return self.err(404, "not_found", "no such list")
            s.lists.remove(row)
            return self.send(204, {})

    def domains(self, method, rest):
        s = self.store
        if method == "GET":
            return self.send(200, {"domains": s.domains})
        b = self.body()
        typ, kind = rest[0], rest[1]
        if method == "POST":
            d = b["domain"]
            if any(x["domain"] == d and x["type"] == typ and x["kind"] == kind for x in s.domains):
                return self.err(400, "database_error", "UNIQUE constraint failed")
            row = {"id": next(s.ids), "domain": d, "type": typ, "kind": kind, "comment": b.get("comment") or None,
                   "enabled": b.get("enabled", True), "groups": [0]}
            self.set_groups(row, b)
            s.domains.append(row)
            return self.send(201, {"domains": [row]})
        d = rest[2]
        row = next((x for x in s.domains if x["domain"] == d and x["type"] == typ and x["kind"] == kind), None)
        if method == "PUT":
            if row is None:
                row = {"id": next(s.ids), "domain": d, "type": typ, "kind": kind, "groups": [0]}
                s.domains.append(row)
            if "type" not in b or "kind" not in b:
                return self.err(400, "bad_request", "type/kind missing")
            row.update(comment=b.get("comment") or None, enabled=b.get("enabled", True))
            self.set_groups(row, b)
            return self.send(200, {"domains": [row]})
        if method == "DELETE":
            if row is None:
                return self.err(404, "not_found", "Not found")
            s.domains.remove(row)
            return self.send(204, {})

    def clients(self, method, rest):
        s = self.store
        if method == "GET":
            return self.send(200, {"clients": s.clients})
        b = self.body()
        if method == "POST":
            c = b["client"]
            if any(x["client"].lower() == c.lower() for x in s.clients):
                return self.err(400, "database_error", "UNIQUE constraint failed")
            row = {"id": next(s.ids), "client": c, "comment": b.get("comment") or None, "groups": [0], "name": None}
            self.set_groups(row, b)
            s.clients.append(row)
            return self.send(201, {"clients": [row]})
        c = rest[0]
        row = next((x for x in s.clients if x["client"].lower() == c.lower()), None)
        if method == "PUT":
            if row is None:
                row = {"id": next(s.ids), "client": c, "groups": [0], "name": None}
                s.clients.append(row)
            row["comment"] = b.get("comment") or None
            self.set_groups(row, b)
            return self.send(200, {"clients": [row]})
        if method == "DELETE":
            if row is None:
                return self.err(404, "not_found", "Not found")
            s.clients.remove(row)
            return self.send(204, {})

    def static(self, path):
        if not self.web_dir:
            return self.err(404, "not_found", "Not found")
        rel = "index.html" if path in ("/", "") else path.lstrip("/")
        if rel.startswith("pb/"):
            rel = rel[3:]
        base = self.web_dir
        if rel == "version.txt":
            rel = "VERSION"
            base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
        elif rel == "services.json":
            base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lists")
        full = os.path.realpath(os.path.join(base, rel))
        if not full.startswith(os.path.realpath(base)) or not os.path.isfile(full):
            return self.err(404, "not_found", "Not found")
        ctype = {".html": "text/html; charset=utf-8", ".js": "text/javascript", ".css": "text/css",
                 ".json": "application/json", ".woff2": "font/woff2", ".svg": "image/svg+xml"}.get(
            os.path.splitext(full)[1], "application/octet-stream")
        with open(full, "rb") as fh:
            data = fh.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Security-Policy",
                         "default-src 'none'; connect-src 'self'; font-src 'self'; frame-ancestors 'none'; "
                         "img-src 'self'; manifest-src 'self'; script-src 'self'; "
                         "style-src 'self' 'unsafe-inline'; form-action 'self'")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def serve(port=0, web_dir=None, store=None):
    store = store or Store()
    handler = type("H", (Handler,), {"store": store, "web_dir": web_dir})
    httpd = ThreadingHTTPServer(("127.0.0.1", port), handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return httpd, store


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--web", default=None, help="directory with index.html/app.js (served at / and /pb/)")
    ap.add_argument("--setup", action="store_true", help="run pihole-bahrain setup against the mock first")
    a = ap.parse_args()
    httpd, store = serve(a.port, a.web)
    if a.setup:
        import importlib.machinery
        import importlib.util
        here = os.path.dirname(os.path.abspath(__file__))
        src = os.path.join(here, "..", "bin", "pihole-bahrain")
        loader = importlib.machinery.SourceFileLoader("pb", src)
        spec = importlib.util.spec_from_loader("pb", loader)
        pb = importlib.util.module_from_spec(spec)
        loader.exec_module(pb)
        api = pb.Api("http://127.0.0.1:%d" % a.port, password=PASSWORD)
        api.login()
        pb.Controller(api, pb.load_catalog(os.path.join(here, "..", "lists")),
                      os.path.join(here, "..", "lists")).setup(run_gravity=False)
        api.logout()
    print("mock Pi-hole on http://127.0.0.1:%d  (password: %s)" % (a.port, PASSWORD))
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        httpd.shutdown()
