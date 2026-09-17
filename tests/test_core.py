import datetime as dt
import importlib.machinery
import importlib.util
import json
import os
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
LISTS = os.path.join(ROOT, "lists")

loader = importlib.machinery.SourceFileLoader("pb", os.path.join(ROOT, "bin", "pihole-bahrain"))
spec = importlib.util.spec_from_loader("pb", loader)
pb = importlib.util.module_from_spec(spec)
loader.exec_module(pb)

import mock_pihole  # noqa: E402


class Base(unittest.TestCase):
    def setUp(self):
        self.httpd, self.store = mock_pihole.serve()
        self.api = pb.Api("http://127.0.0.1:%d" % self.httpd.server_port, password=mock_pihole.PASSWORD)
        self.api.login()
        self.catalog = pb.load_catalog(LISTS)
        self.ctl = pb.Controller(self.api, self.catalog, "https://lists.example/l")

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def group(self, name):
        return next(g for g in self.store.groups if g["name"] == name)

    def add_kid(self, client="AA:BB:CC:00:00:01"):
        gid = {g["name"]: g["id"] for g in self.store.groups}
        self.api.request("POST", "/api/clients", {"client": client, "comment": "Sara",
                                                  "groups": pb.kid_groups(gid, self.catalog)})


class SetupTests(Base):
    def test_setup_creates_everything_and_is_idempotent(self):
        self.ctl.setup(run_gravity=False)
        names = {g["name"] for g in self.store.groups}
        for s in self.catalog["services"]:
            self.assertIn("pb-svc-" + s["id"], names)
            self.assertFalse(self.group("pb-svc-" + s["id"])["enabled"], "services start allowed")
        self.assertTrue(self.group("pb-paused")["enabled"])
        self.assertFalse(self.group("pb-offline")["enabled"])
        self.assertFalse(self.group("pb-state")["enabled"])
        self.assertEqual(len(self.store.lists), len(self.catalog["services"]) + 1)
        for l in self.store.lists:
            self.assertTrue(l["enabled"], "lists must stay enabled so gravity keeps their domains")
            self.assertEqual(len(l["groups"]), 1)
            self.assertNotIn(0, l["groups"], "service lists must not hit the Default group")
        regex = [d for d in self.store.domains if d["domain"] == pb.BLOCK_ALL_REGEX]
        self.assertEqual(len(regex), 1)
        self.assertEqual(sorted(regex[0]["groups"]),
                         sorted([self.group("pb-offline")["id"], self.group("pb-paused")["id"]]))
        snapshot = json.dumps([self.store.groups, self.store.lists, self.store.domains], sort_keys=True)
        self.ctl.setup(run_gravity=False)
        self.assertEqual(snapshot, json.dumps([self.store.groups, self.store.lists, self.store.domains],
                                              sort_keys=True))

    def test_setup_adds_new_service_groups_to_existing_kids(self):
        self.ctl.setup(run_gravity=False)
        self.add_kid()
        extra = dict(self.catalog)
        extra["services"] = self.catalog["services"] + [dict(self.catalog["services"][0], id="guard2")]
        with open(os.path.join(LISTS, "guard.txt")) as fh:
            content = fh.read()
        tmp = os.path.join(LISTS, "guard2.txt")
        with open(tmp, "w") as fh:
            fh.write(content)
        try:
            pb.Controller(self.api, extra, "https://lists.example/l").setup(run_gravity=False)
        finally:
            os.remove(tmp)
        kid = self.store.clients[0]
        self.assertIn(self.group("pb-svc-guard2")["id"], kid["groups"])

    def test_list_source_change_moves_lists(self):
        pb.Controller(self.api, self.catalog, LISTS).setup(run_gravity=False)
        self.assertTrue(all(l["address"].startswith("file://") for l in self.store.lists))
        self.ctl.setup(run_gravity=False)
        self.assertEqual(len(self.store.lists), len(self.catalog["services"]) + 1)
        self.assertTrue(all(l["address"].startswith("https://lists.example/l/") for l in self.store.lists))
        yt = next(l for l in self.store.lists if l["comment"] == "pb:youtube")
        self.assertEqual(yt["address"], "https://lists.example/l/youtube.txt")
        self.assertEqual(yt["groups"], [self.group("pb-svc-youtube")["id"]])

    def test_stale_service_is_removed(self):
        self.ctl.setup(run_gravity=False)
        smaller = dict(self.catalog)
        smaller["services"] = [s for s in self.catalog["services"] if s["id"] != "chatgpt"]
        pb.Controller(self.api, smaller, "https://lists.example/l").setup(run_gravity=False)
        self.assertNotIn("pb-svc-chatgpt", {g["name"] for g in self.store.groups})
        self.assertNotIn("pb:chatgpt", {l["comment"] for l in self.store.lists})

    def test_user_objects_are_untouched(self):
        self.api.request("POST", "/api/groups", {"name": "Guests", "comment": "mine", "enabled": True})
        self.api.request("POST", "/api/lists?type=block", {"address": "https://example.com/ads.txt",
                                                           "comment": "StevenBlack"})
        self.api.request("POST", "/api/domains/deny/regex", {"domain": ".*", "comment": "user regex"})
        self.ctl.setup(run_gravity=False)
        self.ctl.remove()
        self.assertEqual({g["name"] for g in self.store.groups}, {"Default", "Guests"})
        self.assertEqual([l["comment"] for l in self.store.lists], ["StevenBlack"])
        self.assertEqual([d["domain"] for d in self.store.domains], [".*"])

    def test_migration_from_legacy_installer(self):
        self.api.request("POST", "/api/groups", {"name": "Kids", "comment": "Parental-control service blocklists"})
        self.api.request("POST", "/api/groups", {"name": "Paused", "comment": "Per-device pause"})
        kids = self.group("Kids")["id"]
        paused = self.group("Paused")["id"]
        self.api.request("POST", "/api/lists?type=block", {"address": "file:///etc/pihole/lists/youtube.txt",
                                                           "comment": "youtube", "groups": [kids]})
        self.api.request("POST", "/api/domains/deny/regex", {"domain": ".*", "groups": [0], "enabled": True})
        self.api.request("POST", "/api/domains/deny/regex", {"domain": ".+", "groups": [paused]})
        self.api.request("POST", "/api/clients", {"client": "192.168.1.21", "comment": "Sara",
                                                  "groups": [0, kids, paused]})
        self.ctl.setup(run_gravity=False)
        self.ctl.readd_legacy_devices()
        names = {g["name"] for g in self.store.groups}
        self.assertNotIn("Kids", names)
        self.assertNotIn("Paused", names)
        self.assertFalse(any(d["domain"] in (".*", ".+") for d in self.store.domains),
                         "legacy block-everyone regex must be gone")
        self.assertFalse(any(l["address"].startswith("file:///etc/pihole/lists/") for l in self.store.lists))
        sara = self.store.clients[0]
        self.assertIn(self.group("pb-kids")["id"], sara["groups"])
        self.assertIn(self.group("pb-svc-youtube")["id"], sara["groups"])


class TickTests(Base):
    def setUp(self):
        super().setUp()
        self.ctl.setup(run_gravity=False)

    def set_state(self, **kw):
        state = json.loads(json.dumps(pb.DEFAULT_STATE))
        state.update(kw)
        self.ctl.write_state(state)

    def state(self):
        return pb.parse_state(self.group("pb-state")["comment"])

    def enabled(self, name):
        return self.group(name)["enabled"]

    def test_timer_restores_snapshot(self):
        now = dt.datetime(2026, 9, 17, 16, 0)
        # Before the timer: youtube blocked. Free time unblocked it.
        self.set_state(timer={"mode": "free", "until": now.timestamp() - 1,
                              "snapshot": {"services": {"youtube": True}, "offline": False}})
        actions = self.ctl.tick(now)
        self.assertTrue(actions)
        self.assertTrue(self.enabled("pb-svc-youtube"))
        self.assertFalse(self.enabled("pb-svc-tiktok"))
        self.assertIsNone(self.state()["timer"])

    def test_block_timer_restores_previous_not_everything(self):
        now = dt.datetime(2026, 9, 17, 16, 0)
        self.api.put_group("pb-offline", "", True)
        self.set_state(timer={"mode": "block", "until": now.timestamp() - 1,
                              "snapshot": {"services": {"tiktok": True}, "offline": False}})
        self.ctl.tick(now)
        self.assertFalse(self.enabled("pb-offline"))
        self.assertTrue(self.enabled("pb-svc-tiktok"), "previously blocked service stays blocked")

    def test_timer_not_due_does_nothing(self):
        now = dt.datetime(2026, 9, 17, 16, 0)
        self.set_state(timer={"mode": "free", "until": now.timestamp() + 60,
                              "snapshot": {"services": {"youtube": True}, "offline": False}})
        writes = self.store.writes
        self.assertEqual(self.ctl.tick(now), [])
        self.assertEqual(self.store.writes, writes, "no writes when nothing changes")

    def test_bedtime_edges(self):
        sched = {"enabled": True, "start": "21:00", "end": "06:00", "days": [4]}  # Thursday nights
        self.set_state(schedule=sched)
        self.ctl.tick(dt.datetime(2026, 9, 17, 20, 59))       # Thu
        self.assertFalse(self.enabled("pb-offline"))
        self.ctl.tick(dt.datetime(2026, 9, 17, 21, 0))
        self.assertTrue(self.enabled("pb-offline"))
        self.assertTrue(self.state()["scheduleActive"])
        # Parent turns internet back on by hand mid-window: we must not fight it.
        self.api.put_group("pb-offline", "", False)
        self.ctl.tick(dt.datetime(2026, 9, 18, 2, 0))          # Fri 02:00, still Thursday's window
        self.assertFalse(self.enabled("pb-offline"))
        self.api.put_group("pb-offline", "", True)
        self.ctl.tick(dt.datetime(2026, 9, 18, 6, 0))
        self.assertFalse(self.enabled("pb-offline"))
        self.assertFalse(self.state()["scheduleActive"])

    def test_bedtime_cancels_free_time(self):
        now = dt.datetime(2026, 9, 17, 21, 0)
        self.set_state(schedule={"enabled": True, "start": "21:00", "end": "06:00", "days": [4]},
                       timer={"mode": "free", "until": now.timestamp() + 1800,
                              "snapshot": {"services": {"youtube": True}, "offline": False}})
        self.ctl.tick(now)
        self.assertTrue(self.enabled("pb-offline"))
        self.assertTrue(self.enabled("pb-svc-youtube"))
        self.assertIsNone(self.state()["timer"])

    def test_timer_ending_inside_bedtime_keeps_internet_off(self):
        start = dt.datetime(2026, 9, 17, 22, 0)
        self.set_state(schedule={"enabled": True, "start": "21:00", "end": "06:00", "days": [4]},
                       scheduleActive=True,
                       timer={"mode": "free", "until": start.timestamp() - 1,
                              "snapshot": {"services": {}, "offline": False}})
        self.ctl.tick(start)
        self.assertTrue(self.enabled("pb-offline"))

    def test_schedule_written_by_page_is_preserved(self):
        now = dt.datetime(2026, 9, 17, 16, 0)
        self.set_state(timer={"mode": "free", "until": now.timestamp() - 1,
                              "snapshot": {"services": {}, "offline": False}})
        real_group_map = self.api.group_map
        calls = {"n": 0}

        def racing_group_map():
            calls["n"] += 1
            if calls["n"] == 2:   # between the daemon's read and write, the page saves a schedule
                s = pb.parse_state(self.group("pb-state")["comment"])
                s["schedule"]["enabled"] = True
                self.ctl.write_state(s)
            return real_group_map()
        with mock.patch.object(self.api, "group_map", racing_group_map):
            self.ctl.tick(now)
        self.assertTrue(self.state()["schedule"]["enabled"])
        self.assertIsNone(self.state()["timer"])


class PureTests(unittest.TestCase):
    def test_in_schedule_same_day(self):
        s = {"enabled": True, "start": "14:00", "end": "17:00", "days": [0]}   # Sunday afternoon
        self.assertTrue(pb.in_schedule(s, dt.datetime(2026, 9, 20, 15, 0)))   # Sun
        self.assertFalse(pb.in_schedule(s, dt.datetime(2026, 9, 21, 15, 0)))  # Mon
        self.assertFalse(pb.in_schedule(s, dt.datetime(2026, 9, 20, 17, 0)))

    def test_in_schedule_overnight_belongs_to_start_day(self):
        s = {"enabled": True, "start": "22:00", "end": "05:00", "days": [6]}   # Saturday night
        self.assertTrue(pb.in_schedule(s, dt.datetime(2026, 9, 19, 23, 0)))   # Sat 23:00
        self.assertTrue(pb.in_schedule(s, dt.datetime(2026, 9, 20, 4, 59)))   # Sun 04:59
        self.assertFalse(pb.in_schedule(s, dt.datetime(2026, 9, 20, 23, 0)))  # Sun 23:00
        self.assertFalse(pb.in_schedule(dict(s, enabled=False), dt.datetime(2026, 9, 19, 23, 0)))

    def test_parse_state_rejects_garbage(self):
        self.assertEqual(pb.parse_state("not json"), pb.DEFAULT_STATE)
        st = pb.parse_state(json.dumps({"timer": {"mode": "evil", "until": 1},
                                        "schedule": {"start": "25:00", "days": [1, 9, "x"]}}))
        self.assertIsNone(st["timer"])
        self.assertEqual(st["schedule"]["start"], "21:00")
        self.assertEqual(st["schedule"]["days"], [1])

    def test_port_discovery(self):
        cases = {
            "80o,443os,[::]:80o,[::]:443os": "http://127.0.0.1:80",
            "8080": "http://127.0.0.1:8080",
            "443s": "https://127.0.0.1:443",
            "80r,443s": "https://127.0.0.1:443",
            "127.0.0.1:8081o": "http://127.0.0.1:8081",
        }
        for ports, want in cases.items():
            with mock.patch.object(pb, "ftl_config", return_value=ports), \
                    mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("PB_API_URL", None)
                self.assertEqual(pb.discover_base_url(), want, ports)

    def test_cli_array_and_hosts(self):
        self.assertEqual(pb.parse_cli_array("[]"), [])
        self.assertEqual(pb.parse_cli_array("[ 192.168.1.5 nas.lan, 10.0.0.2 family.lan ]\n"),
                         ["192.168.1.5 nas.lan", "10.0.0.2 family.lan"])
        self.assertEqual(pb.merged_hosts(["192.168.1.5 nas.lan", "10.0.0.2 family.lan"], "10.0.0.9", "family.lan"),
                         ["192.168.1.5 nas.lan", "10.0.0.9 family.lan"])

    def test_config_file_and_list_base(self):
        import tempfile
        with tempfile.NamedTemporaryFile("w", delete=False) as fh:
            fh.write("# comment\nPB_LISTS_BASE='https://cdn.example/lists/'\nPB_HOSTNAME=family.lan\n")
        try:
            conf = pb.read_config(fh.name)
        finally:
            os.remove(fh.name)
        self.assertEqual(conf["PB_HOSTNAME"], "family.lan")
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PB_LISTS_BASE", None)
            self.assertEqual(pb.lists_base(conf), "https://cdn.example/lists")
            self.assertEqual(pb.lists_base({}), pb.DEFAULT_LISTS_BASE)
        self.assertEqual(pb.list_address("x", "/opt/l"), "file:///opt/l/x.txt")

    def test_catalog_and_lists_are_consistent(self):
        cat = pb.load_catalog(LISTS)
        cats = {c["id"] for c in cat["categories"]}
        for s in cat["services"]:
            self.assertIn(s["category"], cats)
            with open(os.path.join(LISTS, s["id"] + ".txt")) as fh:
                lines = [l.strip() for l in fh if l.strip() and not l.startswith("!")]
            self.assertTrue(lines, s["id"])
            for line in lines:
                self.assertRegex(line, r"^\|\|[a-z0-9.-]+\.[a-z]{2,}\^$", "%s: %s" % (s["id"], line))


class ListSafetyTests(unittest.TestCase):
    # Blocking any of these (with ||domain^, which includes all subdomains)
    # would break phones, PCs or unrelated apps for the whole family.
    NEVER = {"google.com", "googleapis.com", "gstatic.com", "googleusercontent.com", "apple.com",
             "icloud.com", "microsoft.com", "microsoftonline.com", "live.com", "windows.com", "office.com",
             "office.net", "msftncsi.com", "msftconnecttest.com", "amazonaws.com", "cloudfront.net",
             "akamaihd.net", "akamaized.net", "fastly.net", "cloudflare.com", "fbcdn.net", "github.com",
             "raw.githubusercontent.com", "azureedge.net", "edgekey.net", "akamai.net", "appspot.com"}

    def test_no_critical_infrastructure_is_blocked(self):
        for name in os.listdir(LISTS):
            if not name.endswith(".txt"):
                continue
            with open(os.path.join(LISTS, name)) as fh:
                for line in fh:
                    line = line.strip()
                    if line.startswith("||"):
                        domain = line[2:-1]
                        self.assertNotIn(domain, self.NEVER, "%s blocks %s" % (name, domain))


if __name__ == "__main__":
    unittest.main()
