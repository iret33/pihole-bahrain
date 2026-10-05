"""The mock Pi-hole's "My box" half: password changes, open mode, the Teleporter archive, /api/info/*, /api/config, and the
scheduler simulation. The browser tests lean on all of it, so its behaviour (and its resemblance to FTL, whose shapes come from the
OpenAPI specs) is pinned here."""
import http.client
import io
import json
import os
import time
import unittest
import zipfile

import mock_pihole

HERE = os.path.dirname(os.path.abspath(__file__))


class Box:
    """A tiny HTTP client for one mock server."""

    def __init__(self, port):
        self.port = port
        self.sid = None

    def req(self, method, path, body=None, headers=None, raw=None):
        h = dict(headers or {})
        if self.sid and "sid" not in h:
            h["sid"] = self.sid
        data = raw
        if body is not None:
            data = json.dumps(body).encode()
            h.setdefault("Content-Type", "application/json")
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            conn.request(method, path, body=data, headers=h)
            r = conn.getresponse()
            payload = r.read()
            return r.status, payload, dict(r.getheaders())
        finally:
            conn.close()

    def json(self, method, path, body=None, headers=None):
        status, payload, _ = self.req(method, path, body, headers)
        return status, (json.loads(payload) if payload else {})

    def login(self, password=mock_pihole.PASSWORD, **extra):
        status, j = self.json("POST", "/api/auth", dict({"password": password}, **extra))
        if status == 200 and j["session"]["sid"]:
            self.sid = j["session"]["sid"]
        return status, j


def multipart(fields):
    """fields: name -> (filename or None, bytes). Returns (content type, body)."""
    boundary = "----mockboundary7d1"
    out = b""
    for name, (filename, data) in fields.items():
        out += ("--%s\r\nContent-Disposition: form-data; name=\"%s\"%s\r\n" % (
            boundary, name, '; filename="%s"' % filename if filename else "")).encode()
        out += b"Content-Type: application/zip\r\n\r\n" if filename else b"\r\n"
        out += data + b"\r\n"
    out += ("--%s--\r\n" % boundary).encode()
    return "multipart/form-data; boundary=" + boundary, out


class MockCase(unittest.TestCase):
    def setUp(self):
        self.httpd, self.store = mock_pihole.serve()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        self.box = Box(self.httpd.server_port)

    def add_group(self, name, comment="", enabled=True):
        status, j = self.box.json("POST", "/api/groups", {"name": name, "comment": comment, "enabled": enabled})
        self.assertEqual(status, 201)


class AuthTests(MockCase):
    def test_a_password_protected_box_refuses_a_visitor_without_a_session(self):
        status, j = self.box.json("GET", "/api/auth")
        self.assertEqual(status, 401)
        self.assertFalse(j["session"]["valid"])
        self.assertEqual(self.box.json("GET", "/api/groups")[0], 401)

    def test_open_mode_answers_valid_without_a_sid_and_needs_no_login(self):
        self.store.set_password("")
        status, j = self.box.json("GET", "/api/auth")
        self.assertEqual(status, 200)
        self.assertTrue(j["session"]["valid"])
        self.assertIsNone(j["session"]["sid"])
        self.assertEqual(j["session"]["validity"], -1, "FTL's 'no auth for local user' answer")
        self.assertEqual(self.box.json("GET", "/api/groups")[0], 200)
        status, j = self.box.json("POST", "/api/auth", {"password": "anything"})
        self.assertTrue(status == 200 and j["session"]["valid"] and j["session"]["sid"] is None)

    def test_the_old_test_hook_still_switches_authentication_off(self):
        status, _, _ = self.box.req("POST", "/__mock__/require_auth", {"value": False})
        self.assertEqual(status, 200)
        self.assertTrue(self.box.json("GET", "/api/auth")[1]["session"]["valid"])
        self.box.req("POST", "/__mock__/require_auth", {"value": True})
        self.assertEqual(self.box.json("GET", "/api/auth")[0], 401)

    def test_a_valid_sid_gets_a_valid_session_with_a_lifetime(self):
        self.box.login()
        status, j = self.box.json("GET", "/api/auth")
        self.assertEqual(status, 200)
        self.assertEqual(j["session"]["validity"], 1800)

    def test_wrong_password_and_totp(self):
        self.assertEqual(self.box.login("nope")[0], 401)
        self.store.totp_code = "123456"
        status, j = self.box.login()
        self.assertEqual(status, 401)
        self.assertTrue(j["session"]["totp"], "the page is told it needs a code")
        self.assertEqual(self.box.login(totp=111111)[0], 401)
        self.assertEqual(self.box.login(totp=123456)[0], 200)

    def test_too_many_sessions_is_a_429(self):
        self.store.max_sessions = 1
        self.assertEqual(self.box.login()[0], 200)
        self.assertEqual(Box(self.httpd.server_port).login()[0], 429)

    def test_delete_ends_only_that_session(self):
        a, b = self.box, Box(self.httpd.server_port)
        a.login()
        b.login()
        self.assertEqual(b.req("DELETE", "/api/auth")[0], 204)
        self.assertEqual(self.store.deleted_sessions, [b.sid], "the mock remembers which session was deleted")
        self.assertEqual(a.json("GET", "/api/groups")[0], 200)
        self.assertEqual(b.json("GET", "/api/groups")[0], 401)


class PasswordChangeTests(MockCase):
    BODY = {"config": {"webserver": {"api": {"password": "new-secret-1"}}}}

    def test_patch_changes_the_password_and_ends_every_session_like_ftl(self):
        a, b = self.box, Box(self.httpd.server_port)
        a.login()
        b.login()
        status, j = a.json("PATCH", "/api/config", self.BODY)
        self.assertEqual(status, 200)
        self.assertEqual(j["config"]["webserver"]["api"]["password"], "********", "the password is never echoed back")
        self.assertEqual(a.json("GET", "/api/groups")[0], 401, "the caller's own session is gone too")
        self.assertEqual(b.json("GET", "/api/groups")[0], 401)
        self.assertEqual(Box(self.httpd.server_port).login(mock_pihole.PASSWORD)[0], 401, "the old password no longer works")
        self.assertEqual(Box(self.httpd.server_port).login("new-secret-1")[0], 200)
        self.assertEqual(self.store.password_changes, 1)

    def test_the_password_is_write_only_and_only_a_hash_is_kept(self):
        self.box.login()
        self.box.json("PATCH", "/api/config", self.BODY)
        self.box.login("new-secret-1")
        status, payload, _ = self.box.req("GET", "/api/config")
        self.assertEqual(status, 200)
        self.assertNotIn(b"new-secret-1", payload)
        self.assertNotIn("new-secret-1", self.store.pwhash)

    def test_patch_needs_a_session_when_a_password_is_set(self):
        self.assertEqual(self.box.json("PATCH", "/api/config", self.BODY)[0], 401)
        self.assertEqual(self.store.password_changes, 0)

    def test_a_fresh_box_can_be_claimed_without_a_session(self):
        self.store.set_password("")
        status, _ = self.box.json("PATCH", "/api/config", self.BODY)
        self.assertEqual(status, 200)
        self.assertEqual(self.box.json("GET", "/api/auth")[0], 401, "the box wants a password from now on")
        self.assertEqual(self.box.login("new-secret-1")[0], 200)

    def test_bad_bodies_are_400_and_change_nothing(self):
        self.box.login()
        for body in ({}, {"config": 5}, {"config": {"webserver": {"api": {"password": 12}}}}):
            self.assertEqual(self.box.json("PATCH", "/api/config", body)[0], 400, body)
        status, _, _ = self.box.req("PATCH", "/api/config", headers={"Content-Type": "application/json"}, raw=b"{not json")
        self.assertEqual(status, 400)
        self.assertEqual(self.store.password_changes, 0)
        self.assertEqual(self.box.json("GET", "/api/groups")[0], 200, "the session survived the refused changes")

    def test_other_settings_can_be_patched_and_read_back_by_path(self):
        self.box.login()
        self.box.json("PATCH", "/api/config", {"config": {"dns": {"hosts": ["192.168.1.50 family.lan"]}}})
        status, j = self.box.json("GET", "/api/config/dns/hosts")
        self.assertEqual((status, j["config"]), (200, {"dns": {"hosts": ["192.168.1.50 family.lan"]}}))
        self.assertEqual(self.box.json("GET", "/api/config/dns/nothing")[0], 404)


class TeleporterTests(MockCase):
    def setUp(self):
        super().setUp()
        self.box.login()
        self.add_group("pb-kids", "kids")
        self.add_group("pb-state", '{"v":1}', False)
        self.box.json("POST", "/api/clients", {"client": "AA:BB:CC:00:00:01", "comment": "Sara", "groups": [0, self.store.gid("pb-kids")]})
        self.box.json("POST", "/api/domains/deny/regex", {"domain": "^bad", "comment": "x", "groups": [0]})
        self.box.json("POST", "/api/lists?type=block", {"address": "https://example.org/l.txt", "comment": "pb:youtube", "groups": [self.store.gid("pb-kids")]})

    def archive(self):
        status, payload, headers = self.box.req("GET", "/api/teleporter")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "application/zip")
        self.assertIn("attachment", headers["Content-Disposition"])
        return payload

    def upload(self, data, import_json=None):
        fields = {"file": ("backup.zip", data)}
        if import_json is not None:
            fields["import"] = (None, json.dumps(import_json).encode())
        ctype, body = multipart(fields)
        return self._post(ctype, body)

    def _post(self, ctype, body):
        status, payload, _ = self.box.req("POST", "/api/teleporter", headers={"Content-Type": ctype}, raw=body)
        return status, json.loads(payload)

    GRAVITY_ONLY = {"config": False, "dhcp_leases": False,
                    "gravity": {t: True for t in mock_pihole.GRAVITY_TABLES}}

    def test_the_archive_holds_the_configuration_and_the_seven_tables(self):
        z = zipfile.ZipFile(io.BytesIO(self.archive()))
        names = set(z.namelist())
        self.assertIn("etc/pihole/pihole.toml", names)
        for table in mock_pihole.GRAVITY_TABLES:
            self.assertIsInstance(json.loads(z.read(table + ".json")), list, table)
        toml = z.read("etc/pihole/pihole.toml").decode()
        self.assertIn("pwhash", toml)
        self.assertNotIn(mock_pihole.PASSWORD, toml.replace("MOCK", ""), "the password itself is not in the archive, its hash is")
        groups = {g["name"]: g for g in json.loads(z.read("group.json"))}
        self.assertEqual(groups["pb-state"]["description"], '{"v":1}')
        self.assertEqual(json.loads(z.read("client.json"))[0]["comment"], "Sara")

    def test_the_download_needs_a_session(self):
        self.assertEqual(Box(self.httpd.server_port).req("GET", "/api/teleporter")[0], 401)

    def test_restoring_the_tables_brings_back_what_was_deleted_and_nothing_else(self):
        data = self.archive()
        self.box.json("DELETE", "/api/clients/AA:BB:CC:00:00:01")
        self.store.groups = [g for g in self.store.groups if g["name"] != "pb-kids"]
        self.box.json("PATCH", "/api/config", {"config": {"webserver": {"api": {"password": "changed-after-backup"}}}})
        self.box.login("changed-after-backup")
        status, j = self.upload(data, self.GRAVITY_ONLY)
        self.assertEqual(status, 200)
        self.assertIn("etc/pihole/gravity.db->client", j["files"])
        self.assertNotIn("etc/pihole/pihole.toml", j["files"], "the configuration was not imported")
        self.assertEqual([c["comment"] for c in self.store.clients], ["Sara"])
        self.assertIsNotNone(self.store.gid("pb-kids"))
        self.assertEqual(self.box.json("GET", "/api/groups")[0], 200, "the password was not touched: the session still works")
        self.assertEqual(Box(self.httpd.server_port).login("changed-after-backup")[0], 200, "and neither was the password")
        self.assertEqual(self.store.last_import["import"], self.GRAVITY_ONLY)

    def test_relations_come_back_with_the_tables(self):
        data = self.archive()
        kids = self.store.gid("pb-kids")
        self.assertEqual(next(c for c in self.store.clients)["groups"], [0, kids])
        self.store.clients[0]["groups"] = [0]
        self.upload(data, self.GRAVITY_ONLY)
        self.assertEqual(self.store.clients[0]["groups"], [0, kids])
        self.assertEqual(self.store.lists[0]["groups"], [kids])

    def test_an_import_without_the_import_json_restores_everything_including_the_password(self):
        data = self.archive()
        self.box.json("PATCH", "/api/config", {"config": {"webserver": {"api": {"password": "changed-after-backup"}}}})
        self.box.login("changed-after-backup")
        status, j = self.upload(data)
        self.assertEqual(status, 200)
        self.assertIn("etc/pihole/pihole.toml", j["files"])
        self.assertEqual(Box(self.httpd.server_port).login(mock_pihole.PASSWORD)[0], 200, "the backup's password is back")
        self.assertEqual(self.box.json("GET", "/api/groups")[0], 401, "so every session ended")

    def test_only_the_wanted_tables_are_imported(self):
        data = self.archive()
        self.box.json("DELETE", "/api/clients/AA:BB:CC:00:00:01")
        self.box.json("DELETE", "/api/domains/deny/regex/%5Ebad")
        want = {"config": False, "gravity": {"client": True, "client_by_group": True}}
        status, j = self.upload(data, want)
        self.assertEqual(status, 200)
        self.assertEqual(sorted(j["files"]), ["etc/pihole/gravity.db->client", "etc/pihole/gravity.db->client_by_group"])
        self.assertEqual(len(self.store.clients), 1)
        self.assertEqual(self.store.domains, [], "the domain list was not asked for")

    def test_a_file_that_is_not_a_zip_is_refused_and_changes_nothing(self):
        before = json.dumps(self.store.tables(), sort_keys=True)
        status, j = self.upload(b"this is not a zip")
        self.assertEqual((status, j["error"]["key"]), (400, "invalid_zip"))
        self.assertEqual(json.dumps(self.store.tables(), sort_keys=True), before)

    def test_a_zip_with_unusable_tables_is_refused_before_anything_changes(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("etc/pihole/pihole.toml", 'pwhash = "$MOCK$other"')
            z.writestr("group.json", json.dumps([{"id": 7}]))                  # no name: unusable
        before = json.dumps(self.store.tables(), sort_keys=True)
        status, _ = self.upload(buf.getvalue())
        self.assertEqual(status, 400)
        self.assertEqual(json.dumps(self.store.tables(), sort_keys=True), before)
        self.assertEqual(self.store.password_changes, 0, "the password was not changed by a half-applied archive")

    def test_missing_file_and_bad_import_json_are_400(self):
        ctype, body = multipart({"import": (None, b"{}")})
        self.assertEqual(self._post(ctype, body)[0], 400)
        ctype, body = multipart({"file": ("a.zip", self.archive()), "import": (None, b"[1]")})
        self.assertEqual(self._post(ctype, body)[0], 400)


class InfoTests(MockCase):
    def setUp(self):
        super().setUp()
        self.box.login()

    def test_system_has_ftls_shape(self):
        s = self.box.json("GET", "/api/info/system")[1]["system"]
        self.assertGreater(s["uptime"], 3 * 86400)
        ram = s["memory"]["ram"]
        self.assertEqual({"total", "free", "used", "available", "%used"}, set(ram))
        self.assertTrue(0 < ram["%used"] < 100 and ram["used"] < ram["total"])
        self.assertEqual({"total", "used", "free", "%used"}, set(s["memory"]["swap"]))
        self.assertTrue({"procs", "cpu", "ftl"} <= set(s))

    def test_sensors_have_ftls_shape_and_can_be_missing(self):
        s = self.box.json("GET", "/api/info/sensors")[1]["sensors"]
        self.assertEqual({"list", "cpu_temp", "hot_limit", "unit"}, set(s))
        self.assertEqual(s["unit"], "C")
        self.assertEqual(set(s["list"][0]), {"name", "path", "source", "temps"})
        self.store.cpu_temp = None
        s = self.box.json("GET", "/api/info/sensors")[1]["sensors"]
        self.assertTrue(s["cpu_temp"] is None and s["list"] == [])

    def test_version_keeps_the_old_core_path_and_adds_the_rest(self):
        v = self.box.json("GET", "/api/info/version")[1]["version"]
        self.assertEqual(v["core"]["local"]["version"], "v6.3")
        self.assertNotEqual(v["web"]["local"]["version"], v["core"]["local"]["version"], "each component has its own number")
        self.store.core_version = "vDev-5f2c3d1"
        self.assertEqual(self.box.json("GET", "/api/info/version")[1]["version"]["core"]["local"]["version"], "vDev-5f2c3d1")
        self.assertTrue({"core", "web", "ftl", "docker"} <= set(v))

    def test_host_names_the_box(self):
        h = self.box.json("GET", "/api/info/host")[1]["host"]
        self.assertEqual(h["uname"]["nodename"], "sinko")
        self.assertIn("model", h)

    def test_blocking_and_failures_can_be_simulated(self):
        self.assertEqual(self.box.json("GET", "/api/dns/blocking")[1]["blocking"], "enabled")
        self.store.info_fail = {"sensors", "blocking"}
        self.assertEqual(self.box.json("GET", "/api/info/sensors")[0], 500)
        self.assertEqual(self.box.json("GET", "/api/dns/blocking")[0], 500)
        self.assertEqual(self.box.json("GET", "/api/info/system")[0], 200)

    def test_the_boxs_clock_can_be_wrong(self):
        self.store.clock_skew = 3 * 3600
        _, _, headers = self.box.req("GET", "/api/auth")
        from email.utils import parsedate_to_datetime
        seen = parsedate_to_datetime(headers["Date"]).timestamp()
        self.assertAlmostEqual(seen - time.time(), 3 * 3600, delta=5)


class SchedulerSimTests(MockCase):
    def setUp(self):
        super().setUp()
        self.box.login()
        self.add_group("pb-state", json.dumps({"v": 1}), False)
        self.sim = mock_pihole.SchedulerSim(self.store, version="3.0.0", latest="3.1.0", run_seconds=0.3,
                                            notes="https://github.com/iret33/sinko/releases/tag/v3.1.0")

    def write_state(self, mutate):
        st = self.store.state()
        mutate(st)
        status, _ = self.box.json("PUT", "/api/groups/pb-state", {"name": "pb-state", "comment": json.dumps(st), "enabled": False})
        self.assertEqual(status, 200)

    def wait_for(self, cond, seconds=5):
        end = time.time() + seconds
        while time.time() < end:
            if cond():
                return True
            time.sleep(0.05)
        return False

    def test_update_follows_the_protocol_and_the_version_changes(self):
        self.write_state(lambda st: st.setdefault("update", {}).update({"request": 1800000000000, "latest": "3.1.0"}))
        up = self.store.state()["update"]
        self.assertIsNone(up["request"], "the marker is cleared")
        self.assertEqual((up["status"], up["from"], up["to"]), ("running", "3.0.0", "3.1.0"))
        self.assertEqual(self.sim.handled["update"], 1800000000000, "and remembered first")
        self.assertIsNone(self.store.version, "the old version is still served while it runs")
        self.assertTrue(self.wait_for(lambda: self.store.state()["update"]["status"] == "ok"))
        up = self.store.state()["update"]
        self.assertIsNone(up["latest"], "installed, so no longer 'available'")
        self.assertEqual(self.store.version, "3.1.0")

    def test_a_failed_update_says_why_and_keeps_the_old_version(self):
        self.sim.outcome = "failed"
        self.write_state(lambda st: st.setdefault("update", {}).update({"request": 1, "latest": "3.1.0"}))
        self.assertTrue(self.wait_for(lambda: self.store.state()["update"]["status"] == "failed"))
        up = self.store.state()["update"]
        self.assertIn("checksum", up["error"])
        self.assertEqual(up["latest"], "3.1.0")
        self.assertIsNone(self.store.version)

    def test_the_same_marker_is_never_acted_on_twice(self):
        self.write_state(lambda st: st.setdefault("update", {}).update({"request": 7, "latest": "3.1.0"}))
        self.assertTrue(self.wait_for(lambda: self.store.state()["update"]["status"] == "ok"))
        self.write_state(lambda st: st["update"].update({"request": 7}))
        self.assertEqual(self.store.state()["update"]["request"], 7, "an old marker is left alone")
        self.assertEqual([k for k, _ in self.sim.log], ["update"])

    def test_the_box_drops_connections_while_it_restarts_its_services(self):
        self.sim.outage = 0.6
        self.write_state(lambda st: st.setdefault("update", {}).update({"request": 3, "latest": "3.1.0"}))
        with self.assertRaises((http.client.HTTPException, OSError)):
            self.box.req("GET", "/api/groups")
        self.assertTrue(self.wait_for(self.answers), "the box answers again once the outage is over")
        self.assertEqual(self.box.json("GET", "/api/groups")[0], 200, "and the session survived the restart")

    def answers(self):
        try:
            return self.box.req("GET", "/api/auth")[0] == 200
        except (http.client.HTTPException, OSError):
            return False

    def test_a_hung_runner_never_finishes(self):
        self.sim.run_seconds = None
        self.write_state(lambda st: st.setdefault("update", {}).update({"request": 4, "latest": "3.1.0"}))
        time.sleep(0.5)
        self.assertEqual(self.store.state()["update"]["status"], "running")

    def test_check_again_finds_the_newer_version_unless_offline(self):
        self.write_state(lambda st: st.setdefault("update", {}).update({"checkRequest": 11}))
        self.assertIsNone(self.store.state()["update"]["checkRequest"])
        self.assertTrue(self.wait_for(lambda: self.store.state()["update"].get("latest") == "3.1.0"))
        self.assertGreater(self.store.state()["update"]["checked"], 0)
        self.sim.offline, self.sim.latest = True, "9.9.9"
        before = self.store.state()["update"]["checked"]
        self.write_state(lambda st: st["update"].update({"checkRequest": 12}))
        time.sleep(0.6)
        self.assertEqual(self.store.state()["update"]["latest"], "3.1.0", "offline: the old answer stays")
        self.assertEqual(self.store.state()["update"]["checked"], before)

    def test_power_requests_are_recorded_cleared_and_never_repeat(self):
        self.write_state(lambda st: st.setdefault("power", {}).update({"request": 99, "action": "reboot"}))
        self.assertEqual(self.store.power_log, ["reboot"])
        self.assertEqual(self.store.state()["power"], {"request": None, "action": None})
        self.write_state(lambda st: st["power"].update({"request": 99, "action": "reboot"}))
        self.assertEqual(self.store.power_log, ["reboot"], "the same marker does not reboot twice")
        self.write_state(lambda st: st["power"].update({"request": 100, "action": "poweroff"}))
        self.assertEqual(self.store.power_log, ["reboot", "poweroff"])

    def test_writes_that_ask_for_nothing_are_left_alone(self):
        self.write_state(lambda st: st.update({"scheduleActive": True}))
        self.assertEqual(self.sim.log, [])
        self.assertTrue(self.store.state()["scheduleActive"])

    def test_a_power_request_during_an_update_is_dropped_and_cleared_like_the_real_scheduler_does(self):
        self.sim.run_seconds = None
        self.write_state(lambda st: st.setdefault("update", {}).update({"request": 5, "latest": "3.1.0"}))
        self.assertEqual(self.store.state()["update"]["status"], "running")
        self.write_state(lambda st: st.setdefault("power", {}).update({"request": 6, "action": "reboot"}))
        self.assertEqual(self.store.state()["power"], {"request": None, "action": None}, "taken and cleared, not kept for later")
        self.assertEqual(self.store.power_log, [], "and never acted on")
        self.assertIn(("power-dropped", "reboot"), self.sim.log)
        self.store.edit_state(lambda st: st["update"].update({"status": "ok"}))
        self.write_state(lambda st: st["power"].update({"request": 6, "action": "reboot"}))
        self.assertEqual(self.store.power_log, [], "the same marker never fires later")

    def test_a_power_request_in_the_same_write_as_an_update_request_is_dropped_too(self):
        self.sim.run_seconds = None
        self.write_state(lambda st: (st.setdefault("update", {}).update({"request": 8, "latest": "3.1.0"}),
                                     st.setdefault("power", {}).update({"request": 9, "action": "poweroff"})))
        self.assertEqual(self.store.power_log, [])
        self.assertEqual(self.store.state()["power"], {"request": None, "action": None})

    def test_a_failed_run_says_whether_the_old_version_is_back(self):
        marker = 100
        for value in (True, False, None):
            marker += 1
            self.sim.outcome, self.sim.rolled_back = "failed", value
            self.store.edit_state(lambda st: st.setdefault("update", {}).update({"status": "idle", "rolledBack": "x"}))
            self.write_state(lambda st: st["update"].update({"request": marker}))
            self.assertTrue(self.wait_for(lambda: self.store.state()["update"]["status"] == "failed"))
            self.assertIs(self.store.state()["update"]["rolledBack"], value)


class BoxJsonTests(MockCase):
    """/pb/box.json as the box program writes it: served without a session, `at` on the box's own clock, absent on an older box."""

    def setUp(self):
        super().setUp()
        self.httpd.RequestHandlerClass.web_dir = os.path.join(HERE, "..", "web")

    def get(self):
        return Box(self.httpd.server_port).req("GET", "/pb/box.json")

    def test_it_is_served_to_anybody_with_the_fields_the_architecture_names(self):
        status, payload, headers = self.get()
        self.assertEqual(status, 200)
        self.assertTrue(headers["Content-Type"].startswith("application/json"))
        j = json.loads(payload)
        self.assertEqual(sorted(j), ["at", "counter", "ip", "mdns", "tz", "utcOffset", "v", "version"])
        self.assertEqual((j["v"], j["counter"], j["mdns"]), (1, True, True))
        self.assertAlmostEqual(j["at"], time.time(), delta=5)

    def test_at_follows_the_boxs_own_clock_and_can_fall_behind(self):
        self.store.clock_skew = 3 * 3600
        self.assertAlmostEqual(json.loads(self.get()[1])["at"], time.time() + 3 * 3600, delta=5)
        self.store.clock_skew, self.store.box_info_age = 0, 1500
        self.assertAlmostEqual(json.loads(self.get()[1])["at"], time.time() - 1500, delta=5)

    def test_an_older_box_has_no_file(self):
        self.store.box_info = None
        self.assertEqual(self.get()[0], 404)

    def test_garbage_can_be_served_to_test_the_page(self):
        self.store.box_raw = b"{not json"
        self.assertEqual(self.get()[1], b"{not json")

    def test_nothing_in_it_names_a_person_or_a_secret(self):
        text = json.dumps(self.store.box_info)
        for word in ("password", "pwhash", "family", "child"):
            self.assertNotIn(word, text.lower())


class StaticFilesTests(MockCase):
    """The page's own files, served the way the browser tests need them: plain by default, and on request like Pi-hole's web server
    (civetweb: a cache lifetime of an hour for every file) and like the installer left them (@VERSION@ filled in)."""

    def setUp(self):
        super().setUp()
        self.httpd.RequestHandlerClass.web_dir = os.path.join(HERE, "..", "web")

    def get(self, path, headers=None):
        return Box(self.httpd.server_port).req("GET", path, headers=headers)

    def test_a_query_string_is_ignored_and_nothing_says_how_long_to_keep_a_file_by_default(self):
        status, plain, headers = self.get("/pb/app.js")
        self.assertEqual(status, 200)
        status, versioned, _ = self.get("/pb/app.js?v=@VERSION@")
        self.assertEqual((status, versioned), (200, plain), "the development page asks for /pb/app.js?v=@VERSION@ and gets the file")
        self.assertEqual(self.get("/pb/style.css?v=3.1.0")[0], 200)
        self.assertNotIn("Cache-Control", headers)
        self.assertNotIn("ETag", headers)
        self.assertIn(b"@VERSION@", self.get("/")[1], "index.html is served as it is in the repository: @VERSION@ is the installer's to fill in")

    def test_like_civetweb_a_file_is_kept_for_an_hour_and_a_revalidation_gets_304(self):
        self.store.static_cache = True
        for path in ("/pb/app.js?v=3.0.0", "/", "/pb/version.txt", "/pb/box.json", "/pb/services.json"):
            status, body, headers = self.get(path)
            self.assertEqual(status, 200, path)
            self.assertEqual(headers["Cache-Control"], "max-age=3600", path)
            self.assertTrue(headers["ETag"].startswith('"'), path)
        status, body, headers = self.get("/pb/app.js")
        again = self.get("/pb/app.js", {"If-None-Match": headers["ETag"]})
        self.assertEqual((again[0], again[1]), (304, b""))
        self.assertEqual(again[2]["ETag"], headers["ETag"])
        self.assertEqual(self.get("/pb/app.js", {"If-None-Match": '"something else"'})[0], 200)

    def test_a_new_version_is_a_new_file_for_the_validator_too(self):
        self.store.static_cache = True
        self.store.stamp_pages = True
        self.store.version = "3.0.0"
        old = self.get("/pb/app.js")[2]["ETag"]
        self.store.version = "3.1.0"
        status, body, headers = self.get("/pb/app.js", {"If-None-Match": old})
        self.assertEqual(status, 200, "a release changes every script, so a browser that asks again gets the new one")
        self.assertNotEqual(headers["ETag"], old)

    def test_the_installers_stamp_fills_in_the_version_and_each_script_names_its_release(self):
        self.store.stamp_pages = True
        self.store.version = "3.1.0"
        page = self.get("/")[1].decode()
        self.assertNotIn("@VERSION@", page)
        self.assertIn('<meta name="sinko-version" content="3.1.0">', page)
        for name in ("style.css", "pb-core.js", "pb-live.js", "pb-picture.js", "pb-box.js", "app.js"):
            self.assertIn("/pb/%s?v=3.1.0" % name, page, name)
        js = self.get("/pb/pb-box.js?v=3.1.0")[1].decode()
        self.assertIn('["pb-box.js"] = "3.1.0"', js)
        self.assertNotIn("__pbAssets", self.get("/pb/style.css")[1].decode())
        self.store.version = None
        self.assertIn('content="%s"' % mock_pihole.VERSION, self.get("/")[1].decode(), "without a set version it is the repository's VERSION")

    def test_what_the_page_asked_for_is_kept_when_asked(self):
        self.get("/pb/app.js?v=3.0.0")
        self.assertEqual(self.store.static_hits, [], "off by default: a long-running dev server must not grow a list")
        self.store.log_static = True
        self.get("/pb/app.js?v=3.0.0")
        self.get("/pb/style.css?v=3.0.0")
        self.get("/pb/missing.js")
        self.assertEqual(self.store.static_hits, ["/pb/app.js?v=3.0.0", "/pb/style.css?v=3.0.0"])


class GravityTests(MockCase):
    """POST /api/action/gravity as FTL does it: the status is 200 before the run is over, so only the text tells how it ended."""

    def setUp(self):
        super().setUp()
        self.box.login()

    def run_gravity(self):
        status, payload, headers = self.box.req("POST", "/api/action/gravity")
        return status, payload.decode("utf-8"), headers

    def test_a_run_streams_text_and_is_counted(self):
        status, text, headers = self.run_gravity()
        self.assertEqual(status, 200)
        self.assertTrue(headers["Content-Type"].startswith("text/plain"))
        self.assertIn("Swapping databases", text)
        self.assertNotIn("✗", text)
        self.assertEqual(self.store.gravity_runs, 1)

    def test_a_failed_run_is_still_a_200_and_says_so_in_the_text(self):
        self.store.gravity_fail = True
        status, text, _ = self.run_gravity()
        self.assertEqual(status, 200, "FTL has sent 200 before it knows")
        self.assertIn("✗", text)

    def test_the_run_takes_as_long_as_it_is_told_to(self):
        self.store.gravity_seconds = 0.6
        started = time.time()
        self.run_gravity()
        self.assertGreaterEqual(time.time() - started, 0.55)

    def test_it_needs_a_session(self):
        self.assertEqual(Box(self.httpd.server_port).req("POST", "/api/action/gravity")[0], 401)
        self.assertEqual(self.store.gravity_runs, 0)


if __name__ == "__main__":
    unittest.main()
