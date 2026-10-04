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
import random
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PASSWORD = "test"

# FTL's query status strings (get_query_status_str); the blocked ones are what Pi-hole counts as "blocked".
BLOCKED = {"GRAVITY", "REGEX", "DENYLIST", "EXTERNAL_BLOCKED_IP", "EXTERNAL_BLOCKED_NULL", "EXTERNAL_BLOCKED_NXRA",
           "EXTERNAL_BLOCKED_EDE15", "GRAVITY_CNAME", "REGEX_CNAME", "DENYLIST_CNAME", "DBBUSY", "SPECIAL_DOMAIN"}
CACHED = {"CACHE", "CACHE_STALE"}
FORWARDED = {"FORWARDED", "RETRIED", "RETRIED_DNSSEC"}


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
        # Queries per client address: in the last hour, and two hours ago (must not count as recent).
        self.query_counts = {}
        self.old_query_counts = {}
        self.history_hidden = False       # privacy level >= 2: FTL returns no per-client history
        self.blocking = "enabled"
        self.query_log = []               # newest last; rows shaped like FTL's /api/queries items
        self.query_ids = itertools.count(1)
        self.privacy = 0                  # FTL privacy level: 1 hides domains, 2 also clients, 3 hides all queries
        self.gravity_domains = 123456
        self.dns_restarts = 0             # POST /api/action/restartdns calls (clears the resolver's cache)

    def gid(self, name):
        return next((g["id"] for g in self.groups if g["name"] == name), None)

    def add_query(self, domain, status="FORWARDED", client="192.168.1.21", name=None, when=None):
        """Append one DNS query to the log; returns its id. `when` is a unix time (default: now)."""
        qid = next(self.query_ids)
        blocked = status in BLOCKED
        self.query_log.append({
            "id": qid, "time": float(time.time() if when is None else when), "type": "A", "domain": domain,
            "status": status, "client": {"ip": client, "name": name}, "dnssec": "UNKNOWN",
            "reply": {"type": "IP", "time": 0.0002 if status in CACHED else 0.03},
            "upstream": None if blocked or status in CACHED else "1.1.1.3#53", "list_id": 1 if blocked else None,
            "cname": None, "ede": {"code": -1, "text": None}})
        return qid


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
        if path == "/__mock__/require_auth" and method == "POST":
            # Test hook: False = Pi-hole has no password set (so the installer has to generate one).
            type(self).require_auth = bool(self.body().get("value"))
            return self.send(200, {})
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
                # FTL returns only 10 devices with 3 addresses each unless the caller asks for more.
                max_devices = int((query.get("max_devices") or [10])[0])
                max_addresses = int((query.get("max_addresses") or [3])[0])
                return self.send(200, {"devices": [dict(d, ips=d["ips"][:max_addresses])
                                                   for d in s.devices[:max_devices]]})
            if parts == ["history", "clients"]:
                return self.history_clients(query)
            if parts == ["history"]:
                return self.history()
            if parts == ["stats", "summary"]:
                return self.stats_summary()
            if parts == ["stats", "top_domains"]:
                return self.top_domains(query)
            if parts == ["stats", "top_clients"]:
                return self.top_clients(query)
            if parts == ["queries"]:
                return self.queries(query)
            if parts == ["action", "restartdns"] and method == "POST":
                s.dns_restarts += 1
                return self.send(200, {"status": "success"})
            if parts == ["dns", "blocking"] and method == "GET":
                return self.send(200, {"blocking": s.blocking, "timer": None})
            if parts and parts[0] == "groups":
                return self.groups(method, parts[1:])
            if parts and parts[0] == "lists":
                return self.lists(method, parts[1:], query)
            if parts and parts[0] == "domains":
                return self.domains(method, parts[1:])
            if parts and parts[0] == "clients":
                return self.clients(method, parts[1:])
        return self.err(404, "not_found", "Not found")

    # ---------- statistics (same JSON shapes as FTL's api/stats.c, api/history.c, api/queries.c) ----------
    def recent(self, seconds=86400):
        cutoff = time.time() - seconds
        return [q for q in self.store.query_log if q["time"] >= cutoff]

    def stats_summary(self):
        qs = self.recent()
        total = len(qs)
        blocked = sum(q["status"] in BLOCKED for q in qs)
        statuses = {}
        for q in qs:
            statuses[q["status"]] = statuses.get(q["status"], 0) + 1
        clients = {q["client"]["ip"] for q in qs}
        lists = [l for l in self.store.lists if l.get("enabled", True)]
        return self.send(200, {
            "queries": {"total": total, "blocked": blocked, "percent_blocked": round(100.0 * blocked / total, 2) if total else 0.0,
                        "unique_domains": len({q["domain"] for q in qs}),
                        "forwarded": sum(q["status"] in FORWARDED for q in qs), "cached": sum(q["status"] in CACHED for q in qs),
                        "frequency": round(total / 86400.0, 4), "types": {"A": total}, "status": statuses, "replies": {}},
            "clients": {"active": len(clients), "total": len(clients)},
            "gravity": {"domains_being_blocked": self.store.gravity_domains if lists else 0,
                        "last_update": int(time.time()) - 3600},
            "took": 0.001})

    def history(self):
        now = int(time.time())
        first = now - now % 600 - 143 * 600
        slots = [{"timestamp": first + i * 600, "total": 0, "cached": 0, "blocked": 0, "forwarded": 0} for i in range(144)]
        for q in self.recent():
            i = (int(q["time"]) - first) // 600
            if 0 <= i < 144:
                slots[i]["total"] += 1
                slots[i]["blocked"] += q["status"] in BLOCKED
                slots[i]["cached"] += q["status"] in CACHED
                slots[i]["forwarded"] += q["status"] in FORWARDED
        return self.send(200, {"history": slots})

    def top_domains(self, query):
        qs = self.recent()
        blocked_only = (query.get("blocked") or ["false"])[0] == "true"
        count = int((query.get("count") or [10])[0])
        total, blocked = len(qs), sum(q["status"] in BLOCKED for q in qs)
        if self.store.privacy >= 1:                    # FTL: get_top_domains() answers {-1} from PRIVACY_HIDE_DOMAINS (level 1) up
            return self.send(200, {"domains": [], "total_queries": -1, "blocked_queries": -1})
        tally = {}
        for q in qs:
            if (q["status"] in BLOCKED) == blocked_only:
                tally[q["domain"]] = tally.get(q["domain"], 0) + 1
        top = sorted(tally.items(), key=lambda kv: -kv[1])[:count]
        return self.send(200, {"domains": [{"domain": d, "count": n} for d, n in top], "total_queries": total, "blocked_queries": blocked})

    def top_clients(self, query):
        qs = self.recent()
        count = int((query.get("count") or [10])[0])
        total, blocked = len(qs), sum(q["status"] in BLOCKED for q in qs)
        if self.store.privacy >= 2:
            return self.send(200, {"clients": [], "total_queries": -1, "blocked_queries": -1})
        tally = {}
        for q in qs:
            key = (q["client"]["ip"], q["client"]["name"])
            tally[key] = tally.get(key, 0) + 1
        top = sorted(tally.items(), key=lambda kv: -kv[1])[:count]
        return self.send(200, {"clients": [{"name": n, "ip": ip, "count": c} for (ip, n), c in top],
                               "total_queries": total, "blocked_queries": blocked})

    def queries(self, query):
        """Newest first. `from`/`until` are unix times (>= / <=), `length` caps the rows, as in FTL."""
        s = self.store
        if s.privacy >= 3:
            return self.send(200, {"queries": [], "cursor": None})
        rows = [q for q in s.query_log if q["time"] >= float((query.get("from") or [0])[0])]
        if query.get("until"):
            rows = [q for q in rows if q["time"] <= float(query["until"][0])]
        length = int((query.get("length") or [100])[0])
        out = []
        for q in sorted(rows, key=lambda q: -q["id"])[:max(1, min(length, 1000))]:
            q = json.loads(json.dumps(q))
            if s.privacy >= 1:
                q["domain"] = "hidden"
            if s.privacy >= 2:
                q["client"] = {"ip": "0.0.0.0", "name": None}
            out.append(q)
        return self.send(200, {"queries": out, "cursor": s.query_log[-1]["id"] if s.query_log else None,
                               "recordsTotal": len(s.query_log), "recordsFiltered": len(rows), "draw": 0, "took": 0.001})

    def history_clients(self, query):
        """Per-client query counts in 10-minute slots, shaped like FTL's /api/history/clients."""
        s = self.store
        if s.history_hidden:
            return self.send(200, {"history": [], "clients": []})
        now = int(time.time())
        slots = []
        if s.old_query_counts:
            slots.append({"timestamp": now - 7200, "data": dict(s.old_query_counts, others=0)})
        slots.append({"timestamp": now - 1800, "data": dict({ip: n // 2 for ip, n in s.query_counts.items()}, others=0)})
        slots.append({"timestamp": now - 300, "data": dict({ip: n - n // 2 for ip, n in s.query_counts.items()}, others=0)})
        return self.send(200, {"history": slots, "clients": {ip: {"name": None} for ip in s.query_counts}})

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
        elif rel == "domains.json":
            body = json.dumps(load_cli().build_domain_map(load_cli().load_catalog(os.path.join(HERE, "..", "lists")),
                                                         os.path.join(HERE, "..", "lists")), separators=(",", ":")).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
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


HERE = os.path.dirname(os.path.abspath(__file__))
_CLI = []


def load_cli():
    """bin/pihole-bahrain as a module (for the catalog and the domain map)."""
    if not _CLI:
        import importlib.machinery
        import importlib.util
        loader = importlib.machinery.SourceFileLoader("pb_cli", os.path.join(HERE, "..", "bin", "pihole-bahrain"))
        spec = importlib.util.spec_from_loader("pb_cli", loader)
        mod = importlib.util.module_from_spec(spec)
        loader.exec_module(mod)
        _CLI.append(mod)
    return _CLI[0]


def seed_history(store, count=2600, seed=11, now=None):
    """A believable day of DNS traffic spread over the last 24 hours (busier in the afternoon and evening).

    Children's devices ask for apps (answered GRAVITY while that app's group is enabled), a parent's phone and the
    router mostly make ordinary lookups. Deterministic for a given seed.
    """
    rng = random.Random(seed)
    now = time.time() if now is None else now
    lists_dir = os.path.join(HERE, "..", "lists")
    apps = {sid: [l[2:-1] for l in open(os.path.join(lists_dir, sid + ".txt")).read().split() if l.startswith("||")]
            for sid in ("youtube", "tiktok", "instagram", "roblox", "snapchat", "netflix", "minecraft", "discord")}
    devices = [("192.168.1.21", "Sara-iPad", True, 4), ("192.168.1.22", "ali-galaxy", True, 3), ("192.168.1.9", "parents-phone", False, 2),
               ("192.168.1.1", "router", False, 1)]
    noise = ["www.apple.com", "api.github.com", "time.android.com", "www.bahrain.bh", "fonts.gstatic.com", "play.googleapis.com",
             "connectivitycheck.gstatic.com", "mesu.apple.com", "ocsp.digicert.com", "www.msftconnecttest.com"]
    weights = [1, 1, 1, 1, 1, 2, 2, 3, 3, 3, 3, 4, 4, 4, 5, 5, 6, 6, 7, 7, 7, 6, 4, 2]       # by hour of day
    start = now - 86400
    made = 0
    while made < count:
        t = start + rng.random() * 86400
        hour = int(time.strftime("%H", time.localtime(t)))
        if rng.random() * 7 > weights[hour]:
            continue
        ip, name, child, _w = rng.choices(devices, weights=[d[3] for d in devices])[0]
        if rng.random() < (0.5 if child else 0.12):
            sid = rng.choice(list(apps))
            domain = rng.choice(apps[sid][:10])
            group = next((g for g in store.groups if g["name"] == "pb-svc-" + sid), None)
            status = "GRAVITY" if child and group is not None and group["enabled"] else rng.choice(["FORWARDED", "FORWARDED", "CACHE"])
        else:
            domain, status = rng.choice(noise), rng.choice(["FORWARDED", "CACHE", "CACHE"])
        store.add_query(domain, status, ip, name, t)
        made += 1
    store.query_log.sort(key=lambda q: q["time"])
    for i, q in enumerate(store.query_log, 1):          # ids grow with time, as in FTL
        q["id"] = i
    store.query_ids = itertools.count(len(store.query_log) + 1)


def start_live(store, seed=7):
    """Background traffic for demos and screenshots: two children, a parent, some noise.

    A child's query for an app is answered GRAVITY while that app's group is enabled (blocked), like Pi-hole.
    """
    rng = random.Random(seed)
    domains = {sid: [l[2:-1] for l in open(os.path.join(HERE, "..", "lists", sid + ".txt")).read().split() if l.startswith("||")]
               for sid in ("youtube", "tiktok", "instagram", "roblox", "snapchat", "netflix")}
    devices = [("192.168.1.21", "Sara-iPad", True), ("192.168.1.22", "ali-galaxy", True), ("192.168.1.9", "parents-phone", False)]
    noise = ["www.apple.com", "api.github.com", "time.android.com", "www.bahrain.bh", "fonts.gstatic.com", "play.googleapis.com"]

    def loop():
        while True:
            ip, name, child = rng.choice(devices)
            if rng.random() < 0.55:
                sid = rng.choice(list(domains))
                domain = rng.choice(domains[sid][:6])
                group = next((g for g in store.groups if g["name"] == "pb-svc-" + sid), None)
                blocked = child and group is not None and group["enabled"]
                status = "GRAVITY" if blocked else rng.choice(["FORWARDED", "CACHE"])
            else:
                domain, status = rng.choice(noise), rng.choice(["FORWARDED", "CACHE", "CACHE"])
            store.add_query(domain, status, ip, name)
            time.sleep(rng.uniform(0.25, 1.1))
    threading.Thread(target=loop, daemon=True).start()


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
    ap.add_argument("--live", action="store_true", help="generate demo DNS traffic in the background")
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
    if a.live:
        seed_history(store)
        start_live(store)
    print("mock Pi-hole on http://127.0.0.1:%d  (password: %s)" % (a.port, PASSWORD))
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        httpd.shutdown()
