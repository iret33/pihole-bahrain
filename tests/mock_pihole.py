"""A small in-memory imitation of the Pi-hole v6 REST API.

It implements only what sinko uses, with the same URL shapes, payloads
and quirks as FTL (groups addressed by name, lists by address + ?type=,
domains by /type/kind/domain, PUT keeps groups when "groups" is omitted,
comment is cleared when omitted, etc.).

Beyond the groups/lists/clients API it imitates what the parent page's "My box" sheet needs, with the shapes of FTL's
OpenAPI specs: a password that can be changed (PATCH /api/config with webserver.api.password invalidates every session,
like FTL) or absent ("open" mode), the Teleporter archive (GET and POST /api/teleporter, honouring the "import" JSON),
/api/info/system, sensors, version and host, and GET/PATCH /api/config.

It also serves /pb/box.json the way the box program writes it (`Store.box_info`, `None` = an older box with no such file) and answers
POST /api/action/gravity like FTL: 200 and a text stream first, the run itself after, no matter how it ends.

Test-only switches (set on the Store, no HTTP needed): clock_skew, outage_until, info_fail, max_sessions, totp_code, version,
box_info (the content of /pb/box.json, or None), box_info_age (seconds the file is behind the box's clock), gravity_seconds and
gravity_fail, and `scheduler`, a SchedulerSim that plays the scheduler's side of the request protocol in
docs/maintainers/architecture.md whenever the page writes the pb-state group (so a browser test can watch "Update now" become
running and then ok or failed; a power request that arrives while an update runs is dropped and cleared, as bin/sinko does).

Run standalone to develop the web page without a Raspberry Pi:
    python3 tests/mock_pihole.py --web web --port 8080   (password: test; --open starts without a password)
"""
from __future__ import annotations

import argparse
import email.parser
import email.policy
import hashlib
import io
import itertools
import json
import os
import random
import threading
import time
import urllib.parse
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PASSWORD = "test"
HERE = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(HERE, "..", "VERSION"), encoding="utf-8") as _fh:
    VERSION = _fh.read().strip()
# The gravity tables FTL's Teleporter can export and import (the names of its "gravity" import options).
GRAVITY_TABLES = ("group", "adlist", "adlist_by_group", "domainlist", "domainlist_by_group", "client", "client_by_group")
DOMAIN_TYPES = {("allow", "exact"): 0, ("deny", "exact"): 1, ("allow", "regex"): 2, ("deny", "regex"): 3}


def default_box_info():
    """A box as 3.0 describes itself: its address, time zone, and what it can do. `at` is added when the file is served."""
    return {"v": 1, "version": VERSION, "ip": "192.168.1.50", "tz": "Asia/Bahrain", "utcOffset": "+03:00", "counter": True, "mdns": True}


def mock_hash(password: str) -> str:
    """Stands in for FTL's balloon hash: never the password itself, same input -> same output."""
    return "$MOCK-SHA256$" + hashlib.sha256(("mock-salt:" + password).encode()).hexdigest() if password else ""

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
        # -- the password: only a hash is kept, "" means none is set (open mode, like a fresh Pi-hole) --
        self.pwhash = mock_hash(PASSWORD)
        self.totp_code = None             # a string: two-factor login is on and this is the current code
        self.max_sessions = None          # a number: POST /api/auth answers 429 once that many sessions are open
        self.password_changes = 0
        self.deleted_sessions = []        # the session ids DELETE /api/auth was called for, in order
        # -- configuration (GET/PATCH /api/config) --
        self.config = {"dns": {"hosts": []}, "webserver": {"api": {"max_sessions": 16}}}
        # -- Teleporter --
        self.exports = 0
        self.last_import = None           # {"import": <the JSON or None>, "processed": [...]} of the latest POST
        # -- /api/info/* --
        self.boot_time = time.time() - (3 * 86400 + 4 * 3600 + 600)
        self.mem_total_kb = 1003412
        self.mem_used_percent = 38.4
        self.cpu_temp = 47.3
        self.hot_limit = 60.0
        self.temp_unit = "C"
        self.nodename = "sinko"
        self.model = "OrangePi Zero3"
        self.info_fail = set()            # names among system, sensors, version, host, blocking that answer 500
        # -- test-only switches --
        self.clock_skew = 0               # seconds added to the Date header: the box's clock is wrong by this much
        self.outage_until = 0.0           # until this time the server drops every connection (services restarting)
        self.version = None               # when set, /pb/version.txt answers this instead of the VERSION file
        # What `sinko box-info` writes to /pb/box.json (architecture.md, "Amendments"). `at` is left out here: it is the box's clock at
        # the moment of the request, minus box_info_age. None = an older box, which has no such file (the page must cope).
        self.box_info = default_box_info()
        self.box_info_age = 0             # seconds the file's `at` is behind the box's clock (a scheduler that stopped writing it)
        self.box_raw = None               # bytes served as /pb/box.json instead of the above (to test garbage)
        self.gravity_runs = 0             # POST /api/action/gravity calls
        self.gravity_seconds = 0.0        # how long a run takes
        self.gravity_fail = False         # the run ends with a failure line
        self.scheduler = None             # a SchedulerSim
        self.power_log = []               # actions the scheduler simulation carried out ("reboot", "poweroff")

    def set_password(self, password):
        """Sets (or, with "", removes) the password and ends every session, as FTL does."""
        self.pwhash = mock_hash(password)
        self.sessions.clear()
        self.password_changes += 1

    def check_password(self, password):
        return bool(self.pwhash) and isinstance(password, str) and mock_hash(password) == self.pwhash

    def gid(self, name):
        return next((g["id"] for g in self.groups if g["name"] == name), None)

    # ---------- Teleporter: the gravity tables as FTL keeps them, and back ----------
    def tables(self):
        """The mock's rows as the seven gravity tables (lists of dicts), with the relation tables spelled out."""
        t = {name: [] for name in GRAVITY_TABLES}
        for g in self.groups:
            t["group"].append({"id": g["id"], "name": g["name"], "enabled": int(bool(g.get("enabled", True))),
                               "description": g.get("comment")})
        for l in self.lists:
            t["adlist"].append({"id": l["id"], "address": l["address"], "enabled": int(bool(l.get("enabled", True))),
                                "comment": l.get("comment"), "type": 0 if l.get("type", "block") == "block" else 1})
            t["adlist_by_group"] += [{"adlist_id": l["id"], "group_id": g} for g in l["groups"]]
        for d in self.domains:
            t["domainlist"].append({"id": d["id"], "type": DOMAIN_TYPES[(d["type"], d["kind"])], "domain": d["domain"],
                                    "enabled": int(bool(d.get("enabled", True))), "comment": d.get("comment")})
            t["domainlist_by_group"] += [{"domainlist_id": d["id"], "group_id": g} for g in d["groups"]]
        for c in self.clients:
            t["client"].append({"id": c["id"], "ip": c["client"], "comment": c.get("comment")})
            t["client_by_group"] += [{"client_id": c["id"], "group_id": g} for g in c["groups"]]
        return t

    def load_tables(self, t):
        """Rebuilds groups, lists, domains and clients from the seven tables. Relations to rows that do not exist are dropped."""
        try:
            groups = [{"id": r["id"], "name": r["name"], "enabled": bool(r["enabled"]), "comment": r.get("description")}
                      for r in t["group"]]
            if not any(g["id"] == 0 for g in groups):
                groups.insert(0, {"id": 0, "name": "Default", "enabled": True, "comment": "The default group"})
            known = {g["id"] for g in groups}

            def members(rel, key, row_id):
                found = sorted({r["group_id"] for r in t[rel] if r[key] == row_id and r["group_id"] in known})
                return found or [0]           # FTL's trigger puts a new row into the default group

            lists = [{"id": r["id"], "address": r["address"], "type": "block" if r["type"] == 0 else "allow",
                      "comment": r.get("comment"), "enabled": bool(r["enabled"]),
                      "groups": members("adlist_by_group", "adlist_id", r["id"]), "number": 0} for r in t["adlist"]]
            by_number = {v: k for k, v in DOMAIN_TYPES.items()}
            domains = [{"id": r["id"], "domain": r["domain"], "type": by_number[r["type"]][0], "kind": by_number[r["type"]][1],
                        "comment": r.get("comment"), "enabled": bool(r["enabled"]),
                        "groups": members("domainlist_by_group", "domainlist_id", r["id"])} for r in t["domainlist"]]
            clients = [{"id": r["id"], "client": r["ip"], "comment": r.get("comment"), "name": None,
                        "groups": members("client_by_group", "client_id", r["id"])} for r in t["client"]]
        except (KeyError, TypeError, AttributeError):
            raise ValueError("invalid_zip")   # nothing has been changed yet
        self.groups, self.lists, self.domains, self.clients = groups, lists, domains, clients
        top = max([0] + [r["id"] for rows in t.values() for r in rows if "id" in r])
        self.ids = itertools.count(max(top + 1, next(self.ids)))

    def export_zip(self):
        """Like FTL's Teleporter archive: the configuration (with the password hash in it) and the gravity tables as JSON."""
        toml = "[webserver.api]\n  pwhash = %s\n\n[dns]\n  hosts = %s\n" % (
            json.dumps(self.pwhash), json.dumps(self.config.get("dns", {}).get("hosts", [])))
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("etc/pihole/pihole.toml", toml)
            z.writestr("etc/pihole/dhcp.leases", "")
            for name, rows in self.tables().items():
                z.writestr("%s.json" % name, json.dumps(rows))
        self.exports += 1
        return buf.getvalue()

    def import_zip(self, data, wanted):
        """Applies a Teleporter archive. `wanted` is the "import" JSON (None = everything, like FTL when it is omitted).

        Returns the list of what was processed, or raises ValueError("invalid_zip").
        """
        try:
            z = zipfile.ZipFile(io.BytesIO(data))
            names = set(z.namelist())
        except zipfile.BadZipFile:
            raise ValueError("invalid_zip")
        everything = wanted is None
        wanted = wanted if isinstance(wanted, dict) else {}
        gravity = wanted.get("gravity", {}) if isinstance(wanted.get("gravity"), dict) else {}
        processed = []
        current = self.tables()
        try:
            for table in GRAVITY_TABLES:
                if (everything or gravity.get(table)) and "%s.json" % table in names:
                    rows = json.loads(z.read("%s.json" % table).decode("utf-8"))
                    if isinstance(rows, list):
                        current[table] = rows
                        processed.append("etc/pihole/gravity.db->%s" % table)
            toml = z.read("etc/pihole/pihole.toml").decode("utf-8", "replace") \
                if (everything or wanted.get("config")) and "etc/pihole/pihole.toml" in names else None
        except (ValueError, UnicodeDecodeError, KeyError, zipfile.BadZipFile):
            raise ValueError("invalid_zip")
        self.load_tables(current)                      # raises before anything is changed when the rows are unusable
        if toml is not None:
            for line in toml.splitlines():
                line = line.strip()
                if line.startswith("pwhash"):
                    new = json.loads(line.split("=", 1)[1].strip())
                    if new != self.pwhash:
                        self.pwhash = new
                        self.sessions.clear()          # FTL ends sessions when the password changes
                        self.password_changes += 1
                elif line.startswith("hosts"):
                    self.config.setdefault("dns", {})["hosts"] = json.loads(line.split("=", 1)[1].strip())
            processed.insert(0, "etc/pihole/pihole.toml")
        self.writes += 1
        self.last_import = {"import": None if everything else wanted, "processed": processed}
        return processed

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

    # ---------- the shared state in the pb-state group, for tests that play the scheduler ----------
    def state_group(self):
        return next((g for g in self.groups if g["name"] == "pb-state"), None)

    def state(self):
        g = self.state_group()
        try:
            data = json.loads(g["comment"]) if g and g.get("comment") else {}
        except ValueError:
            data = {}
        return data if isinstance(data, dict) else {}

    def edit_state(self, mutator, locked=False):
        """Read-modify-write the state like another writer would (the scheduler). Pass locked=True inside a request."""
        def go():
            st = self.state()
            mutator(st)
            self.state_group()["comment"] = json.dumps(st)
            self.writes += 1
        if locked:
            return go()
        with self.lock:
            go()


class SchedulerSim:
    """The scheduler's side of the request protocol, as docs/maintainers/architecture.md defines it ("Shared state").

    It is called whenever the page writes the pb-state group. For each request marker the page set (update.request,
    update.checkRequest, power.request) it does what the real scheduler does, in the same order: store the marker in
    "handled" (/var/lib/sinko/handled.json), clear it in the state, then act. An update becomes `running` at once and
    ends `ok` or `failed` after `run_seconds`; `outage` seconds of "the box is restarting its services" (every
    connection dropped) start with it. On success the box's version (served as /pb/version.txt) changes.

    It has no timers of its own and does not download anything: it only reacts to the page's writes.
    `run_seconds=None` never finishes (a runner that hung).
    """

    def __init__(self, store, version="3.0.0", latest=None, notes=None, run_seconds=1.0, outcome="ok",
                 error="The download did not match its checksum.", outage=0.0, check_seconds=0.2, power_outage=0.0, offline=False,
                 rolled_back=None):
        self.store = store
        self.version = version            # what the box runs now
        self.latest = latest              # what a check finds (None: up to date)
        self.notes = notes
        self.run_seconds = run_seconds
        self.outcome = outcome            # "ok" | "failed"
        self.error = error
        self.outage = outage
        self.check_seconds = check_seconds
        self.power_outage = power_outage
        self.offline = offline            # a check cannot reach GitHub: nothing changes
        self.rolled_back = rolled_back    # what a failed run reports: True (the old version is back), False, or None (not known)
        self.handled = {"update": None, "check": None, "power": None}
        self.log = []                     # ("update", "3.1.0"), ("check", None), ("power", "reboot"), ("power-dropped", "reboot") in order
        store.scheduler = self

    def _later(self, seconds, fn, *args):
        t = threading.Timer(seconds, fn, args)
        t.daemon = True
        t.start()

    def after_state_write(self, group):
        """Called inside a request, with the store lock held."""
        st = self.store.state()
        up = st.get("update") if isinstance(st.get("update"), dict) else {}
        power = st.get("power") if isinstance(st.get("power"), dict) else {}
        now = int(time.time())
        todo = []
        marker = up.get("request")
        if marker not in (None, "") and marker != self.handled["update"]:
            self.handled["update"] = marker                    # 1. remember it
            up["request"] = None                               # 2. clear it
            to = up.get("latest") or self.latest or self.version
            up.update({"status": "running", "from": self.version, "to": to, "at": now, "error": None, "rolledBack": None})
            todo.append(("update", to))                        # 3. act
        marker = up.get("checkRequest")
        if marker not in (None, "") and marker != self.handled["check"]:
            self.handled["check"] = marker
            up["checkRequest"] = None
            todo.append(("check", None))
        marker = power.get("request")
        if marker not in (None, "") and marker != self.handled["power"]:
            self.handled["power"] = marker
            action = power.get("action")
            power.update({"request": None, "action": None})
            if up.get("status") == "running":
                # bin/sinko Maintenance._power: never cut the power in the middle of an installation, and never keep the request for
                # later either. It is taken (so it cannot repeat) and dropped.
                todo.append(("power-dropped", action))
            elif action in ("reboot", "poweroff"):
                todo.append(("power", action))
        if not todo:
            return
        st["update"], st["power"] = up, power
        group["comment"] = json.dumps(st)
        self.store.writes += 1
        for kind, arg in todo:
            self.log.append((kind, arg))
            if kind == "update":
                if self.outage:
                    self.store.outage_until = time.time() + self.outage
                if self.run_seconds is not None:
                    self._later(self.run_seconds, self._finish_update, arg)
            elif kind == "check":
                self._later(self.check_seconds, self._finish_check)
            elif kind == "power-dropped":
                pass
            else:
                self.store.power_log.append(arg)
                if self.power_outage:
                    self.store.outage_until = time.time() + self.power_outage

    def _finish_update(self, to):
        with self.store.lock:
            def done(st):
                up = st.setdefault("update", {})
                now = int(time.time())
                if self.outcome == "ok":
                    up.update({"status": "ok", "to": to, "at": now, "error": None, "checked": now, "rolledBack": None})
                    if up.get("latest") == to:                 # it is installed now
                        up["latest"], up["notes"] = None, None
                else:
                    up.update({"status": "failed", "at": now, "error": self.error, "rolledBack": self.rolled_back})
            self.store.edit_state(done, locked=True)
            if self.outcome == "ok":
                self.version = to
                self.store.version = to

    def _finish_check(self):
        with self.store.lock:
            if self.offline:
                return                                          # offline: keep the old answer
            def done(st):
                up = st.setdefault("update", {})
                up.update({"checked": int(time.time()), "latest": self.latest, "notes": self.notes if self.latest else None})
            self.store.edit_state(done, locked=True)


class Handler(BaseHTTPRequestHandler):
    store: Store = None
    web_dir: str | None = None
    require_auth = True

    def log_message(self, *a):  # quiet
        pass

    def handle(self):
        try:
            super().handle()
        except (BrokenPipeError, ConnectionResetError):
            pass                                    # the browser went away (a reload, a closed tab): not worth a traceback

    # ---------- helpers ----------
    def send(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_raw(self, code, ctype, data, headers=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def err(self, code, key, msg):
        self.send(code, {"error": {"key": key, "message": msg, "hint": None}})

    def raw_body(self):
        if not hasattr(self, "_raw"):
            self._raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        return self._raw

    def body(self):
        raw = self.raw_body()
        return json.loads(raw) if raw else {}

    def json_body(self):
        """The JSON object of the request, or None (after answering 400) when it is not one."""
        try:
            b = self.body()
        except ValueError:
            b = None
        if not isinstance(b, dict):
            self.err(400, "bad_request", "Invalid request body data (no valid JSON object)")
            return None
        return b

    def date_time_string(self, timestamp=None):
        # The box's clock can be set wrong (no real-time clock): the page learns the time from this header.
        return super().date_time_string((time.time() if timestamp is None else timestamp) + self.store.clock_skew)

    def open_mode(self):
        """No password is set (a fresh Pi-hole), or the test hook switched authentication off."""
        return (not self.require_auth) or not self.store.pwhash

    def authed(self):
        return self.open_mode() or self.headers.get("sid") in self.store.sessions

    def session_json(self, valid, sid=None, validity=-1, message=None, totp=False):
        return {"session": {"valid": valid, "totp": totp, "sid": sid, "csrf": None, "validity": validity, "message": message}}

    # ---------- routing ----------
    def do_GET(self):
        self.route("GET")

    def do_POST(self):
        self.route("POST")

    def do_PUT(self):
        self.route("PUT")

    def do_PATCH(self):
        self.route("PATCH")

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
        if time.time() < self.store.outage_until:
            self.close_connection = True      # the box is restarting its services: no answer at all, not even an error
            return
        if not path.startswith("/api"):
            return self.static(path)
        parts = [urllib.parse.unquote(p) for p in path.split("/")[2:]]
        s = self.store
        if parts == ["action", "gravity"] and method == "POST":
            with s.lock:
                allowed = self.authed()
                if allowed:
                    s.gravity_runs += 1
                    delay, fail = s.gravity_seconds, s.gravity_fail
            return self.gravity(delay, fail) if allowed else self.err(401, "unauthorized", "Unauthorized")
        with s.lock:
            if parts == ["auth"]:
                if method == "GET":
                    if self.headers.get("sid") in s.sessions:
                        return self.send(200, self.session_json(True, validity=1800))
                    if self.open_mode():
                        return self.send(200, self.session_json(True, message="no auth for local user"))
                    return self.send(401, self.session_json(False, message="no SID provided", totp=bool(s.totp_code)))
                if method == "POST":
                    b = self.json_body()
                    if b is None:
                        return
                    if not s.pwhash:                       # nothing to log in to
                        return self.send(200, self.session_json(True, message="no auth for local user"))
                    if not s.check_password(b.get("password")):
                        return self.send(401, self.session_json(False, message="password incorrect", totp=bool(s.totp_code)))
                    if s.totp_code and str(b.get("totp")) != s.totp_code:
                        return self.send(401, self.session_json(False, message="2FA token missing or invalid", totp=True))
                    if s.max_sessions is not None and len(s.sessions) >= s.max_sessions:
                        return self.err(429, "api_seats_exceeded", "API seats exceeded")
                    sid = "sid%d" % next(s.ids)
                    s.sessions.add(sid)
                    return self.send(200, self.session_json(True, sid=sid, validity=1800, message="correct password"))
                if method == "DELETE":
                    s.deleted_sessions.append(self.headers.get("sid"))
                    s.sessions.discard(self.headers.get("sid"))
                    return self.send(204, {})
            if not self.authed():
                return self.err(401, "unauthorized", "Unauthorized")
            if parts and parts[0] == "info" and len(parts) == 2 and parts[1] in s.info_fail:
                return self.err(500, "internal_error", "Simulated failure of /api/info/%s" % parts[1])
            if parts == ["info", "client"]:
                return self.send(200, {"remote_addr": "192.168.1.21"})
            if parts == ["info", "version"]:
                return self.send(200, self.info_version())
            if parts == ["info", "system"]:
                return self.send(200, self.info_system())
            if parts == ["info", "sensors"]:
                return self.send(200, self.info_sensors())
            if parts == ["info", "host"]:
                return self.send(200, self.info_host())
            if parts and parts[0] == "config":
                return self.config_api(method, parts[1:])
            if parts == ["teleporter"]:
                return self.teleporter(method)
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
            if parts == ["dns", "blocking"] and method == "GET":
                if "blocking" in s.info_fail:
                    return self.err(500, "internal_error", "Simulated failure of /api/dns/blocking")
                return self.send(200, {"blocking": s.blocking, "timer": None, "took": 0.0002})
            if parts and parts[0] == "groups":
                return self.groups(method, parts[1:])
            if parts and parts[0] == "lists":
                return self.lists(method, parts[1:], query)
            if parts and parts[0] == "domains":
                return self.domains(method, parts[1:])
            if parts and parts[0] == "clients":
                return self.clients(method, parts[1:])
        return self.err(404, "not_found", "Not found")

    def gravity(self, delay, fail):
        """POST /api/action/gravity: FTL sends 200 and streams the output of `pihole -g` BEFORE the run is over, so the status says nothing
        about how it ended: a failed run is told only in the text. (api/action.c adds an error object after the last chunk.)"""
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        self.wfile.write("[i] Neutrino emissions detected...\n\n[\u2713] Pulling blocklist source list into range\n".encode())
        self.wfile.flush()
        time.sleep(delay)
        if fail:
            self.wfile.write("\n[\u2717] DNS resolution is currently unavailable\n".encode())
        else:
            self.wfile.write("\n[\u2713] Preparing new gravity database\n[\u2713] Swapping databases\n[\u2713] Pi-hole blocking is enabled\n".encode())

    # ---------- the box itself (shapes from FTL's info.yaml) ----------
    def info_version(self):
        def part(branch, version, h):
            return {"local": {"branch": branch, "version": version, "hash": h}, "remote": {"version": version, "hash": h}}
        return {"version": {"core": part("master", "v6.3", "955e36a9"), "web": part("master", "v6.3", "f69f7e88"),
                            "ftl": part("master", "v6.3", "1a2b3c4d"), "docker": {"local": None, "remote": None}}, "took": 0.0004}

    def info_system(self):
        s = self.store
        total = s.mem_total_kb
        used = int(total * s.mem_used_percent / 100)
        return {"system": {
            "uptime": int(time.time() - s.boot_time),
            "memory": {"ram": {"total": total, "free": total - used - 120000, "used": used, "available": total - used,
                               "%used": s.mem_used_percent},
                       "swap": {"total": 0, "used": 0, "free": 0, "%used": 0.0}},
            "procs": 118, "cpu": {"nprocs": 4, "%cpu": 3.2, "load": {"raw": [0.12, 0.09, 0.05], "percent": [3.0, 2.25, 1.25]}},
            "ftl": {"%mem": 2.4, "%cpu": 0.6}}, "took": 0.0003}

    def info_sensors(self):
        s = self.store
        if s.cpu_temp is None:                         # a board with no thermal sensor FTL can read
            return {"sensors": {"list": [], "cpu_temp": None, "hot_limit": s.hot_limit, "unit": s.temp_unit}, "took": 0.0002}
        return {"sensors": {
            "list": [{"name": "cpu_thermal", "path": "hwmon0", "source": "virtual/thermal/thermal_zone0",
                      "temps": [{"name": None, "value": s.cpu_temp, "max": None, "crit": 100.0, "sensor": "temp1"}]}],
            "cpu_temp": s.cpu_temp, "hot_limit": s.hot_limit, "unit": s.temp_unit}, "took": 0.0002}

    def info_host(self):
        s = self.store
        return {"host": {
            "uname": {"domainname": "(none)", "machine": "aarch64", "nodename": s.nodename, "release": "6.12.30-current-sunxi64",
                      "sysname": "Linux", "version": "#1 SMP PREEMPT Debian"},
            "model": s.model,
            "dmi": {"bios": {"vendor": None}, "board": {"name": None, "vendor": None, "version": None},
                    "product": {"name": None, "version": None, "family": None}, "sys": {"vendor": None}}}, "took": 0.0002}

    def config_api(self, method, rest):
        """GET /api/config[/a/b] and PATCH /api/config. The password is write-only: it is never returned."""
        s = self.store
        if method == "GET":
            tree = json.loads(json.dumps(s.config))
            tree.setdefault("webserver", {}).setdefault("api", {})["password"] = "********" if s.pwhash else ""

            def pick(node, path):                      # FTL answers the requested branch of the tree: {"dns": {"hosts": [...]}}
                if not path:
                    return node
                if not isinstance(node, dict) or path[0] not in node:
                    raise KeyError(path[0])
                return {path[0]: pick(node[path[0]], path[1:])}
            try:
                return self.send(200, {"config": pick(tree, rest), "took": 0.0003})
            except KeyError:
                return self.err(404, "not_found", "Item not found")
        if method == "PATCH" and not rest:
            b = self.json_body()
            if b is None:
                return
            cfg = b.get("config")
            if not isinstance(cfg, dict):
                return self.err(400, "bad_request", "No \"config\" object in body data")
            api = (cfg.get("webserver") or {}).get("api") if isinstance(cfg.get("webserver"), dict) else None
            password = api.get("password") if isinstance(api, dict) else None
            if password is not None and not isinstance(password, str):
                return self.err(400, "bad_request", "Config value webserver.api.password has invalid type")
            merged = json.loads(json.dumps(cfg))
            if isinstance(api, dict):
                merged["webserver"]["api"].pop("password", None)
            for section, values in merged.items():
                if isinstance(values, dict):
                    s.config.setdefault(section, {}).update(values)
            if password is not None:
                s.set_password(password)               # every session ends, including the caller's
            return self.send(200, {"config": cfg if password is None else dict(
                cfg, webserver=dict(cfg["webserver"], api=dict(cfg["webserver"]["api"], password="********"))), "took": 0.002})
        return self.err(404, "not_found", "Not found")

    def teleporter(self, method):
        s = self.store
        if method == "GET":
            stamp = time.strftime("%Y-%m-%d_%H-%M-%S")
            return self.send_raw(200, "application/zip", s.export_zip(), {
                "Content-Disposition": 'attachment; filename="pi-hole_%s_teleporter_%s.zip"' % (s.nodename, stamp)})
        if method != "POST":
            return self.err(405, "method_not_allowed", "Method not allowed")
        ctype = self.headers.get("Content-Type", "")
        if not ctype.startswith("multipart/form-data"):
            return self.err(400, "bad_request", "No multipart form data")
        # The standard library's cgi module is gone in Python 3.13: parse the form with the email package.
        msg = email.parser.BytesParser(policy=email.policy.HTTP).parsebytes(
            b"Content-Type: " + ctype.encode() + b"\r\nMIME-Version: 1.0\r\n\r\n" + self.raw_body())
        fields = {}
        for part in msg.iter_parts():
            fields[part.get_param("name", header="content-disposition")] = part.get_payload(decode=True)
        if "file" not in fields:
            return self.err(400, "bad_request", "No file in the form data")
        wanted = None
        if fields.get("import"):
            try:
                wanted = json.loads(fields["import"].decode("utf-8"))
            except ValueError:
                return self.err(400, "bad_request", "Invalid JSON in the import field")
            if wanted is not None and not isinstance(wanted, dict):
                return self.err(400, "bad_request", "Invalid JSON in the import field")
        try:
            processed = s.import_zip(fields["file"], wanted)
        except ValueError:
            return self.err(400, "invalid_zip", "Invalid ZIP file uploaded")
        # FTL's own code answers {"files": [...]} (its OpenAPI text says "processed", the C code never writes it).
        return self.send(200, {"files": processed, "took": 0.012})

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
            if g["name"] == "pb-state" and s.scheduler is not None:
                s.scheduler.after_state_write(g)        # the scheduler notices the page's request markers
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

    def box_json(self):
        s = self.store
        if s.box_raw is not None:
            data = s.box_raw
        elif s.box_info is None:
            return self.err(404, "not_found", "Not found")
        else:
            data = json.dumps(dict({"at": int(time.time() + s.clock_skew - s.box_info_age)}, **s.box_info), separators=(",", ":")).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def static(self, path):
        if not self.web_dir:
            return self.err(404, "not_found", "Not found")
        rel = "index.html" if path in ("/", "") else path.lstrip("/")
        if rel.startswith("pb/"):
            rel = rel[3:]
        if rel == "box.json":                                   # written by the box program, not part of the page's files
            return self.box_json()
        base = self.web_dir
        if rel == "version.txt" and self.store.version:        # an update "installed" a new version
            return self.send_raw(200, "text/plain; charset=utf-8", (self.store.version + "\n").encode())
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
                 ".json": "application/json", ".woff2": "font/woff2", ".svg": "image/svg+xml", ".png": "image/png",
                 ".txt": "text/plain; charset=utf-8"}.get(
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


_CLI = []


def load_cli():
    """bin/sinko as a module (for the catalog and the domain map)."""
    if not _CLI:
        import importlib.machinery
        import importlib.util
        loader = importlib.machinery.SourceFileLoader("pb_cli", os.path.join(HERE, "..", "bin", "sinko"))
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
    # A short poll interval makes shutdown() return at once (the default half second added up over hundreds of test cases).
    t = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
    t.start()
    return httpd, store


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--web", default=None, help="directory with index.html/app.js (served at / and /pb/)")
    ap.add_argument("--setup", action="store_true", help="run sinko setup against the mock first")
    ap.add_argument("--live", action="store_true", help="generate demo DNS traffic in the background")
    ap.add_argument("--open", action="store_true", help="start with no password set, like a fresh Pi-hole (the page offers to choose one)")
    ap.add_argument("--old-box", action="store_true", help="serve no /pb/box.json, like a box from before 3.0")
    ap.add_argument("--update", metavar="VERSION", default=None,
                    help="play the scheduler: a newer version (e.g. 3.1.0) is available and 'Update now' really runs")
    a = ap.parse_args()
    httpd, store = serve(a.port, a.web)
    if a.setup:
        import importlib.machinery
        import importlib.util
        here = os.path.dirname(os.path.abspath(__file__))
        src = os.path.join(here, "..", "bin", "sinko")
        loader = importlib.machinery.SourceFileLoader("pb", src)
        spec = importlib.util.spec_from_loader("pb", loader)
        pb = importlib.util.module_from_spec(spec)
        loader.exec_module(pb)
        api = pb.Api("http://127.0.0.1:%d" % a.port, password=PASSWORD)
        api.login()
        pb.Controller(api, pb.load_catalog(os.path.join(here, "..", "lists")),
                      os.path.join(here, "..", "lists")).setup(run_gravity=False)
        api.logout()
    if a.update:
        notes = "https://github.com/iret33/sinko/releases/tag/v" + a.update
        SchedulerSim(store, latest=a.update, notes=notes)
        if store.state_group() is not None:        # needs --setup: the state lives in a group that setup creates
            store.edit_state(lambda st: st.setdefault("update", {}).update(
                {"latest": a.update, "notes": notes, "checked": int(time.time()) - 3 * 3600}))
    if a.old_box:
        store.box_info = None
    if a.open:
        store.pwhash = ""
    if a.live:
        seed_history(store)
        start_live(store)
    print("mock Pi-hole on http://127.0.0.1:%d  (%s)" % (a.port, "no password set" if a.open else "password: " + PASSWORD))
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        httpd.shutdown()
