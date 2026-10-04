"""Tests for `doctor`, `status` and the other diagnostics (client matching, network checks, ...)."""
import importlib.machinery
import importlib.util
import json
import os
import sqlite3
import subprocess
import tempfile
import threading
import time
import unittest
from unittest import mock

import mock_pihole

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
LISTS = os.path.join(ROOT, "lists")

loader = importlib.machinery.SourceFileLoader("pb", os.path.join(ROOT, "bin", "sinko"))
spec = importlib.util.spec_from_loader("pb", loader)
pb = importlib.util.module_from_spec(spec)
loader.exec_module(pb)


class FakeProbe:
    """Stands in for pb.SystemProbe so tests do not depend on the host's routing table or IPv6 state."""

    def __init__(self, gateway="192.168.1.1", v6=(), v6_route=False, log="", own=("127.0.0.1", "::1")):
        self.gateway, self.v6, self.v6_route, self.log, self.own = gateway, list(v6), v6_route, log, set(own)

    def default_gateway(self):
        return self.gateway

    def ipv6_addresses(self):
        return self.v6

    def ipv6_default_route(self):
        return self.v6_route

    def own_addresses(self):
        return self.own

    def ftl_log_tail(self):
        return self.log


class Base(unittest.TestCase):
    def setUp(self):
        self.httpd, self.store = mock_pihole.serve()
        self.api = pb.Api("http://127.0.0.1:%d" % self.httpd.server_port, password=mock_pihole.PASSWORD)
        self.api.login()
        self.catalog = pb.load_catalog(LISTS)
        self.ctl = pb.Controller(self.api, self.catalog, "https://lists.example/l")
        self.ctl.setup(run_gravity=False)
        for row in self.store.lists:          # as if `pihole -g` had compiled every list
            row["number"] = row["abp_entries"] = 25

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def gid(self):
        return {g["name"]: g["id"] for g in self.store.groups}

    def all_pb_groups(self, paused=False):
        g = self.gid()
        names = ["pb-kids", "pb-guard", "pb-offline"] + ["pb-svc-" + s["id"] for s in self.catalog["services"]]
        return [0] + [g[n] for n in names] + ([g["pb-paused"]] if paused else [])

    def add_row(self, client, groups, comment=""):
        self.api.request("POST", "/api/clients", {"client": client, "comment": comment, "groups": groups})

    def add_device(self, hwaddr, ips, last_query=None):
        self.store.devices.append({"id": 100 + len(self.store.devices), "hwaddr": hwaddr, "macVendor": "",
                                   "lastQuery": int(last_query if last_query is not None else time.time() - 30),
                                   "numQueries": 50, "ips": [{"ip": i, "name": ""} for i in ips]})

    def report(self, probe=None, now=None):
        lines = []
        rep = pb.Report(out=lines.append)
        pb.doctor_api_checks(rep, self.api, self.catalog, probe or FakeProbe(gateway=None), now or time.time())
        rep.lines = lines
        return rep

    def status_devices(self):
        return {d["client"]: d for d in pb.build_status(self.api, self.catalog)["devices"]}


class ShadowTests(Base):
    MAC = "AA:BB:CC:00:00:23"

    def setUp(self):
        super().setUp()
        self.add_device(self.MAC.lower(), ["192.168.1.23"])
        self.add_row(self.MAC, self.all_pb_groups(), "Sara")

    def test_clean_setup_has_no_findings(self):
        rep = self.report()
        self.assertEqual(rep.fixes, [])
        self.assertEqual(self.status_devices()[self.MAC]["shadowed_by"], [])

    def test_subnet_row_in_default_only_shadows_the_mac_row(self):
        self.add_row("192.168.1.0/24", [0])
        rep = self.report()
        self.assertEqual(len(rep.fixes), 1, rep.lines)
        self.assertIn("Sara", rep.fixes[0])
        self.assertIn("192.168.1.0/24", rep.fixes[0])
        self.assertIn("Default", rep.fixes[0])
        self.assertEqual(pb.Report(out=lambda m: None).finish(), 0)
        self.assertEqual(self.status_devices()[self.MAC]["shadowed_by"],
                         [{"client": "192.168.1.0/24", "groups": ["Default"]}])

    def test_exact_ip_row_shadows_the_mac_row(self):
        self.add_row("192.168.1.23", [0])
        rep = self.report()
        self.assertEqual(len(rep.fixes), 1, rep.lines)
        self.assertIn("192.168.1.23", rep.fixes[0])
        self.assertEqual(self.status_devices()[self.MAC]["shadowed_by"][0]["client"], "192.168.1.23")

    def test_ip_row_with_every_pb_group_is_harmless(self):
        self.add_row("192.168.1.23", self.all_pb_groups())
        self.assertEqual(self.report().fixes, [])
        self.assertEqual(self.status_devices()[self.MAC]["shadowed_by"], [])

    def test_longest_prefix_wins_like_ftl(self):
        # A harmless /32 beats a harmful /24 ...
        self.add_row("192.168.1.0/24", [0])
        self.add_row("192.168.1.23", self.all_pb_groups())
        self.assertEqual(self.report().fixes, [])
        # ... and a harmful /32 beats a harmless /24.
        self.api.request("DELETE", "/api/clients/192.168.1.23")
        self.api.request("DELETE", "/api/clients/" + self.api.q("192.168.1.0/24"))
        self.add_row("192.168.1.0/24", self.all_pb_groups())
        self.add_row("192.168.1.23", [0])
        rep = self.report()
        self.assertEqual(len(rep.fixes), 1)
        self.assertIn("192.168.1.23", rep.fixes[0])

    def test_ipv6_subnet_row_shadows_too(self):
        self.store.devices[-1]["ips"] = [{"ip": "fe80::1", "name": ""}, {"ip": "2001:db8::5", "name": ""}]
        self.add_row("2001:db8::/32", [0])
        rep = self.report()
        self.assertEqual(len(rep.fixes), 1, rep.lines)
        self.assertIn("2001:db8::/32", rep.fixes[0])

    def test_address_beyond_ftls_default_limit_of_three_is_still_checked(self):
        self.store.devices[-1]["ips"] = [{"ip": a, "name": ""} for a in
                                         ("fe80::1", "fd00::1", "2001:db8::1", "192.168.1.23")]
        self.add_row("192.168.1.0/24", [0])
        self.assertEqual(len(self.report().fixes), 1, "the IPv4 address is the 4th one FTL returns")

    def test_paused_child_needs_pb_paused_on_the_shadowing_row_too(self):
        self.add_row("192.168.1.23", self.all_pb_groups())
        self.assertEqual(self.report().fixes, [])
        self.api.put_client(self.MAC, "Sara", self.all_pb_groups(paused=True))
        rep = self.report()
        self.assertEqual(len(rep.fixes), 1, "pausing the MAC row has no effect while the IP row wins")

    def test_rows_that_are_not_addresses_are_ignored(self):
        self.add_row("sara-ipad", [0])
        self.add_row("AA:BB:CC:99:99:99", [0])
        self.add_row(":eth0", [0])
        self.assertEqual(self.report().fixes, [])

    def test_ip_registered_child_and_unknown_device_are_not_flagged(self):
        self.add_row("192.168.1.77", self.all_pb_groups(), "Ali")
        self.add_row("192.168.1.0/24", [0])
        self.add_row("AA:BB:CC:55:55:55", self.all_pb_groups(), "Ghost")   # not in the network table at all
        rep = self.report()
        self.assertEqual([f for f in rep.fixes if "Ali" in f or "Ghost" in f], [])

    def test_status_includes_shadowed_by_for_every_device(self):
        devices = pb.build_status(self.api, self.catalog)["devices"]
        self.assertTrue(devices and all("shadowed_by" in d for d in devices))
        json.dumps(devices)


def ftl_values(**values):
    """A stand-in for pb.ftl_config: dotted keys are written with '__' (resolver__macNames=...)."""
    def fake(key):
        wanted = key.replace(".", "__")
        if wanted not in values:
            raise pb.subprocess.CalledProcessError(1, ["pihole-FTL", "--config", key])
        return values[wanted]
    return fake


class RouterRelayTests(Base):
    GW = "192.168.1.1"

    def test_router_relaying_most_queries_is_a_fix(self):
        self.store.query_counts = {self.GW: 900, "192.168.1.23": 60}
        rep = self.report(FakeProbe(self.GW))
        self.assertEqual(len(rep.fixes), 1, rep.lines)
        self.assertIn("relaying DNS", rep.fixes[0])
        self.assertIn(self.GW, rep.fixes[0])
        self.assertIn("DHCP DNS server", rep.fixes[0])

    def test_devices_asking_for_themselves_is_fine(self):
        self.store.query_counts = {self.GW: 10, "192.168.1.23": 400, "192.168.1.24": 300}
        rep = self.report(FakeProbe(self.GW))
        self.assertEqual(rep.fixes, [])
        self.assertTrue(any("come from the devices themselves" in l for l in rep.lines), rep.lines)

    def test_too_few_queries_to_judge(self):
        self.store.query_counts = {self.GW: 20}
        rep = self.report(FakeProbe(self.GW))
        self.assertEqual(rep.fixes, [])
        self.assertTrue(any("too few" in l for l in rep.lines))

    def test_only_the_last_hour_counts(self):
        self.store.old_query_counts = {self.GW: 5000}
        self.store.query_counts = {"192.168.1.23": 200}
        self.assertEqual(self.report(FakeProbe(self.GW)).fixes, [])

    def test_the_boxs_own_queries_are_not_lan_queries(self):
        self.store.query_counts = {"192.168.1.50": 5000, "127.0.0.1": 800, self.GW: 10, "192.168.1.23": 40}
        rep = self.report(FakeProbe(self.GW, own=("192.168.1.50", "127.0.0.1")))
        self.assertEqual(rep.fixes, [], rep.lines)

    def test_hidden_history_and_missing_gateway_are_not_judged(self):
        self.store.history_hidden = True
        rep = self.report(FakeProbe(self.GW))
        self.assertEqual(rep.fixes, [])
        self.assertTrue(any("hidden" in l for l in rep.lines))
        self.store.history_hidden = False
        self.store.query_counts = {self.GW: 900}
        self.assertEqual(self.report(FakeProbe(None)).fixes, [], "without a gateway there is nothing to compare")

    def test_rate_limiting_of_the_router_is_a_warning(self):
        now = time.time()
        stamp = lambda t: time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t))
        log = ("%s.100 CEST [1/T2] INFO: Rate-limiting %s for at least 44 seconds\n" % (stamp(now - 600), self.GW))
        rep = self.report(FakeProbe(self.GW, log=log), now)
        self.assertEqual(rep.fixes, [])
        self.assertTrue(any("rate-limited the router" in w for w in rep.warnings), rep.lines)
        old = "%s.100 CEST [1/T2] INFO: Rate-limiting %s for at least 44 seconds\n" % (stamp(now - 3 * 86400), self.GW)
        self.assertEqual(self.report(FakeProbe(self.GW, log=old), now).warnings, [], "three days old")
        other = "%s.100 CEST [1/T2] INFO: Rate-limiting 192.168.1.99 for at least 44 seconds\n" % stamp(now - 60)
        self.assertEqual(self.report(FakeProbe(self.GW, log=other), now).warnings, [], "not the router")

    def test_recent_client_counts(self):
        history = {"history": [{"timestamp": 1000, "data": {"a": 5, "others": 9}},
                               {"timestamp": 9000, "data": {"a": 2, "b": 3, "others": 1}}]}
        self.assertEqual(pb.recent_client_counts(history, now=9500), {"a": 2, "b": 3})
        self.assertEqual(pb.recent_client_counts({}, now=9500), {})


class MachineChecks(Base):
    def test_ipv6_on_the_network_is_a_warning(self):
        link_local = [("fe80::1", 0x20)]
        self.assertEqual(self.report(FakeProbe(None, v6=link_local)).warnings, [])
        rep = self.report(FakeProbe(None, v6=link_local + [("2001:db8::5", 0)]))
        self.assertEqual(len(rep.warnings), 1)
        self.assertIn("IPv6", rep.warnings[0])
        self.assertEqual(len(self.report(FakeProbe(None, v6_route=True)).warnings), 1)
        self.assertEqual(rep.fixes, [])

    def test_global_blocking_off_is_a_fix(self):
        self.store.blocking = "disabled"
        rep = self.report()
        self.assertEqual(len([f for f in rep.fixes if "blocking is switched off" in f]), 1, rep.lines)
        self.store.blocking = "enabled"
        self.assertEqual(self.report().fixes, [])
        with mock.patch.object(pb, "ftl_config", ftl_values(dns__blocking__active="false")):
            self.assertEqual(len([f for f in self.report().fixes if "switched off" in f]), 1, "pihole.toml says off")
        self.store.blocking = "failure"
        self.assertTrue(any("DNS service reports a failure" in f for f in self.report().fixes))

    def test_mac_names_off_is_a_fix_unknown_key_is_not(self):
        with mock.patch.object(pb, "ftl_config", ftl_values(resolver__macNames="false")):
            fixes = self.report().fixes
        self.assertEqual(len(fixes), 1)
        self.assertIn("resolver.macNames", fixes[0])
        self.assertIn("pihole-FTL --config resolver.macNames true", fixes[0])
        with mock.patch.object(pb, "ftl_config", ftl_values(resolver__macNames="true")):
            rep = self.report()
        self.assertEqual(rep.fixes, [])
        self.assertTrue(any("resolver.macNames is true" in l for l in rep.lines))
        with mock.patch.object(pb, "ftl_config", ftl_values()):           # older FTL: unknown key
            rep = self.report()
        self.assertEqual(rep.fixes, [])
        self.assertTrue(any("not available in this Pi-hole version" in l for l in rep.lines))
        with mock.patch.object(pb, "ftl_config", side_effect=FileNotFoundError("pihole-FTL")):
            self.assertEqual(self.report().fixes, [], "no FTL binary: reported elsewhere")


class SilentKidTests(Base):
    def kid(self, mac="AA:BB:CC:00:00:23", name="Sara"):
        self.add_row(mac, self.all_pb_groups(), name)

    def test_recent_device_is_fine(self):
        self.add_device("aa:bb:cc:00:00:23", ["192.168.1.23"])
        self.kid()
        self.assertEqual(self.report().warnings, [])

    def test_device_with_no_query_for_a_day_warns(self):
        self.add_device("aa:bb:cc:00:00:23", ["192.168.1.23"], last_query=time.time() - 2 * 86400)
        self.kid()
        rep = self.report()
        self.assertEqual(len(rep.warnings), 1)
        self.assertIn("Sara", rep.warnings[0])
        self.assertIn("Private Wi-Fi address", rep.warnings[0])
        self.assertIn("mobile data", rep.warnings[0])
        self.assertEqual(rep.fixes, [])

    def test_device_unknown_to_the_network_table_warns(self):
        self.kid(name="Ghost")
        rep = self.report()
        self.assertEqual(len(rep.warnings), 1)
        self.assertIn("not been seen", rep.warnings[0])

    def test_ip_registered_child_and_subnet_rows(self):
        self.add_device("aa:bb:cc:00:00:77", ["192.168.1.77"], last_query=time.time() - 3 * 86400)
        self.add_row("192.168.1.77", self.all_pb_groups(), "Ali")
        self.add_row("192.168.1.0/24", self.all_pb_groups(), "Whole LAN")      # nothing to look up
        rep = self.report()
        silent = [w for w in rep.warnings if "no DNS query" in w]
        self.assertEqual(len(silent), 1, rep.lines)
        self.assertIn("Ali", silent[0])
        self.assertFalse([w for w in rep.warnings if "Whole LAN" in w], "a subnet row has no device to look up")

    def test_warnings_do_not_fail_doctor(self):
        lines = []
        rep = pb.Report(out=lines.append)
        rep.warn("something to look at")
        self.assertEqual(rep.finish(), 0)
        self.assertIn("1 warning(s)", lines[-1])
        rep.fix("broken")
        self.assertEqual(rep.finish(), 1)


class IdentityTests(Base):
    """Children registered by IP address stop being filtered when the address changes."""

    def test_ip_registered_child_whose_mac_is_known_warns(self):
        self.add_device("aa:bb:cc:00:00:77", ["192.168.1.77", "2001:db8::77", "fe80::77"])
        self.add_row("192.168.1.77", self.all_pb_groups(), "Ali")
        rep = self.report()
        self.assertEqual(rep.fixes, [])
        self.assertEqual(len(rep.warnings), 1, rep.lines)
        w = rep.warnings[0]
        self.assertIn("aa:bb:cc:00:00:77", w)
        self.assertIn("use-mac 192.168.1.77", w)
        self.assertIn("2001:db8::77", w, "another address of the same device that no rule covers")
        self.assertNotIn("fe80::77", w, "link-local addresses are not worth mentioning")

    def test_other_addresses_covered_by_a_child_row_are_not_listed(self):
        self.add_device("aa:bb:cc:00:00:77", ["192.168.1.77", "2001:db8::77"])
        self.add_row("192.168.1.77", self.all_pb_groups(), "Ali")
        self.add_row("2001:db8::/64", self.all_pb_groups(), "Ali v6")
        warnings = [w for w in self.report().warnings if w.startswith("Ali (")]
        self.assertEqual(len(warnings), 1)
        self.assertNotIn("2001:db8::77", warnings[0])

    def test_ipv6_registered_child_warns_about_rotating_addresses(self):
        self.add_device("ip-2001:db8::99", ["2001:db8::99"])
        self.add_row("2001:db8::99", self.all_pb_groups(), "Tablet")
        rep = self.report()
        self.assertEqual(len(rep.warnings), 1, rep.lines)
        self.assertIn("IPv6", rep.warnings[0])
        self.assertIn("privacy addresses", rep.warnings[0])

    def test_ipv4_child_without_a_known_mac_is_left_alone(self):
        self.add_device("ip-192.168.1.30", ["192.168.1.30"])
        self.add_row("192.168.1.30", self.all_pb_groups(), "Console")
        self.assertEqual(self.report().warnings, [])

    def test_mac_registered_child_has_no_identity_warning(self):
        self.add_device("aa:bb:cc:00:00:23", ["192.168.1.23", "2001:db8::23"])
        self.add_row("AA:BB:CC:00:00:23", self.all_pb_groups(), "Sara")
        self.assertEqual(self.report().warnings, [])


class UseMacTests(Base):
    def setUp(self):
        super().setUp()
        self.add_device("aa:bb:cc:00:00:77", ["192.168.1.77", "2001:db8::77"])
        self.paused_groups = self.all_pb_groups(paused=True)
        self.add_row("192.168.1.77", self.paused_groups, "Ali")

    def rows(self):
        return {c["client"]: c for c in self.store.clients}

    def test_converts_keeping_name_and_groups(self):
        lines = self.ctl.convert_to_mac()
        self.assertEqual(len(lines), 1)
        rows = self.rows()
        self.assertNotIn("192.168.1.77", rows)
        self.assertEqual(rows["AA:BB:CC:00:00:77"]["comment"], "Ali")
        self.assertEqual(sorted(rows["AA:BB:CC:00:00:77"]["groups"]), sorted(self.paused_groups), "pb-paused is kept too")
        self.assertEqual(self.report().warnings, [], "the identity warning is gone")
        self.assertEqual(self.ctl.convert_to_mac(), [], "nothing left to convert")

    def test_dry_run_changes_nothing(self):
        before = json.dumps(self.store.clients, sort_keys=True)
        lines = self.ctl.convert_to_mac(dry_run=True)
        self.assertIn("dry run", lines[0])
        self.assertEqual(json.dumps(self.store.clients, sort_keys=True), before)

    def test_merges_into_an_existing_mac_row(self):
        self.add_row("AA:BB:CC:00:00:77", [0], "")
        self.ctl.convert_to_mac()
        rows = self.rows()
        self.assertNotIn("192.168.1.77", rows)
        self.assertEqual(sorted(rows["AA:BB:CC:00:00:77"]["groups"]), sorted(self.paused_groups))
        self.assertEqual(rows["AA:BB:CC:00:00:77"]["comment"], "Ali")

    def test_unknown_mac_and_address_filter(self):
        self.add_device("ip-192.168.1.30", ["192.168.1.30"])
        self.add_row("192.168.1.30", self.all_pb_groups(), "Console")
        lines = self.ctl.convert_to_mac(only="192.168.1.30")
        self.assertEqual(len(lines), 1)
        self.assertIn("not known yet", lines[0])
        self.assertIn("192.168.1.77", self.rows(), "the other child was not touched")
        self.assertIn("192.168.1.30", self.rows())


class SystemProbeTests(unittest.TestCase):
    def probe_with(self, **files):
        import tempfile
        d = tempfile.mkdtemp()
        env = {}
        for var, text in files.items():
            path = os.path.join(d, var)
            with open(path, "w") as fh:
                fh.write(text)
            env[var] = path
        return pb.SystemProbe(), mock.patch.dict(os.environ, env)

    def test_default_gateway_from_proc_net_route(self):
        route = ("Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\t\tMTU\tWindow\tIRTT\n"
                 "wlan0\t00000000\t0A00A8C0\t0003\t0\t0\t600\t00000000\t0\t0\t0\n"
                 "eth0\t00000000\t0101A8C0\t0003\t0\t0\t100\t00000000\t0\t0\t0\n"
                 "eth0\t0001A8C0\t00000000\t0001\t0\t0\t100\t00FFFFFF\t0\t0\t0\n")
        probe, env = self.probe_with(SINKO_ROUTE_FILE=route)
        with env:
            self.assertEqual(probe.default_gateway(), "192.168.1.1", "lowest metric wins")
        probe, env = self.probe_with(SINKO_ROUTE_FILE="Iface\tDestination\tGateway\tFlags\tRefCnt\tUse\tMetric\n")
        with env:
            self.assertIsNone(probe.default_gateway())
        with mock.patch.dict(os.environ, {"SINKO_ROUTE_FILE": "/nonexistent"}):
            self.assertIsNone(pb.SystemProbe().default_gateway())

    def test_ipv6_state_from_proc(self):
        inet6 = ("fe800000000000000a00000000000001 02 40 20 80 eth0\n"
                 "20010db8000000000000000000000005 02 40 00 80 eth0\n"
                 "00000000000000000000000000000001 01 80 10 80 lo\n")
        routes = ("00000000000000000000000000000000 00 00000000000000000000000000000000 00 "
                  "fe800000000000000000000000000001 00000400 00000001 00000000 00000003 eth0\n"
                  "00000000000000000000000000000000 00 00000000000000000000000000000000 00 "
                  "00000000000000000000000000000000 ffffffff 00000001 00000000 00200200 lo\n")
        probe, env = self.probe_with(SINKO_IF_INET6_FILE=inet6, SINKO_IPV6_ROUTE_FILE=routes)
        with env:
            self.assertIn(("2001:db8::5", 0), probe.ipv6_addresses())
            self.assertIn(("fe80::a00:0:0:1", 0x20), probe.ipv6_addresses())
            self.assertTrue(probe.ipv6_default_route())
            self.assertIn("2001:db8::5", probe.own_addresses())
        probe, env = self.probe_with(SINKO_IPV6_ROUTE_FILE=routes.splitlines()[1] + "\n")     # only the reject route on lo
        with env:
            self.assertFalse(probe.ipv6_default_route())

    def test_ftl_log_tail_reads_only_the_end(self):
        probe, env = self.probe_with(SINKO_FTL_LOG="old line\n" * 100000 + "Rate-limiting 10.0.0.1 for at least 5 seconds\n")
        with env:
            tail = probe.ftl_log_tail()
        self.assertLess(len(tail), 600 * 1024)
        self.assertIn("Rate-limiting 10.0.0.1", tail)


# A hand-written subset of Pi-hole's databases: just the tables and columns `diagnose` reads.
GRAVITY_SCHEMA = """
CREATE TABLE "group" (id INTEGER PRIMARY KEY AUTOINCREMENT, enabled BOOLEAN NOT NULL DEFAULT 1, name TEXT UNIQUE NOT NULL,
                      date_added INTEGER NOT NULL DEFAULT 0, date_modified INTEGER NOT NULL DEFAULT 0, description TEXT);
CREATE TABLE domainlist (id INTEGER PRIMARY KEY AUTOINCREMENT, type INTEGER NOT NULL DEFAULT 0, domain TEXT NOT NULL,
                         enabled BOOLEAN NOT NULL DEFAULT 1, comment TEXT, UNIQUE(domain, type));
CREATE TABLE domainlist_by_group (domainlist_id INTEGER NOT NULL, group_id INTEGER NOT NULL, PRIMARY KEY (domainlist_id, group_id));
CREATE TABLE adlist (id INTEGER PRIMARY KEY AUTOINCREMENT, address TEXT NOT NULL, enabled BOOLEAN NOT NULL DEFAULT 1,
                     comment TEXT, number INTEGER NOT NULL DEFAULT 0, abp_entries INTEGER NOT NULL DEFAULT 0, type INTEGER NOT NULL DEFAULT 0);
CREATE TABLE adlist_by_group (adlist_id INTEGER NOT NULL, group_id INTEGER NOT NULL, PRIMARY KEY (adlist_id, group_id));
CREATE TABLE gravity (domain TEXT NOT NULL, adlist_id INTEGER NOT NULL);
CREATE TABLE antigravity (domain TEXT NOT NULL, adlist_id INTEGER NOT NULL);
CREATE VIEW vw_gravity AS SELECT domain, adlist.id AS adlist_id, adlist_by_group.group_id AS group_id
    FROM gravity
    LEFT JOIN adlist_by_group ON adlist_by_group.adlist_id = gravity.adlist_id
    LEFT JOIN adlist ON adlist.id = gravity.adlist_id
    LEFT JOIN "group" ON "group".id = adlist_by_group.group_id
    WHERE adlist.enabled = 1 AND (adlist_by_group.group_id IS NULL OR "group".enabled = 1);
"""
FTL_SCHEMA = """
CREATE TABLE query_storage (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp INTEGER NOT NULL, type INTEGER, status INTEGER,
                            domain INTEGER, client INTEGER);
CREATE TABLE domain_by_id (id INTEGER PRIMARY KEY, domain TEXT UNIQUE);
CREATE TABLE client_by_id (id INTEGER PRIMARY KEY, ip TEXT UNIQUE, name TEXT);
CREATE VIEW queries AS SELECT q.id, q.timestamp, q.type, q.status, d.domain AS domain, c.ip AS client
    FROM query_storage q LEFT JOIN domain_by_id d ON q.domain = d.id LEFT JOIN client_by_id c ON q.client = c.id;
"""


class DiagnoseBase(Base):
    MAC = "AA:BB:CC:00:00:23"

    def setUp(self):
        super().setUp()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.gdb, self.fdb = os.path.join(tmp.name, "gravity.db"), os.path.join(tmp.name, "pihole-FTL.db")
        env = mock.patch.dict(os.environ, {"SINKO_GRAVITY_DB": self.gdb, "SINKO_FTL_DB": self.fdb, "SINKO_SQL_BACKEND": "python"})
        env.start()
        self.addCleanup(env.stop)
        lists = mock.patch.object(pb, "LISTS_DIR", LISTS)         # where the installed service lists would be
        lists.start()
        self.addCleanup(lists.stop)
        self.now = int(time.time())
        self.allow = []                      # (type, domain, enabled)
        self.antigravity = []
        self.queries = []                    # (timestamp, status, domain, client)
        for sid in ("youtube", "instagram"):
            self.group("pb-svc-" + sid)["enabled"] = True
        self.add_device(self.MAC.lower(), ["192.168.1.23"])
        self.add_row(self.MAC, self.all_pb_groups(), "Sara")

    def group(self, name):
        return next(g for g in self.store.groups if g["name"] == name)

    def build(self, gravity=None):
        """Write gravity.db and pihole-FTL.db from the mock's state plus what the test set up."""
        for path in (self.gdb, self.fdb):
            if os.path.exists(path):
                os.remove(path)
        con = sqlite3.connect(self.gdb)
        con.executescript(GRAVITY_SCHEMA)
        for g in self.store.groups:
            con.execute('INSERT INTO "group" (id, enabled, name, date_modified) VALUES (?,?,?,?)',
                        (g["id"], 1 if g["enabled"] else 0, g["name"], g.get("date_modified", 0)))
        for l in self.store.lists:
            con.execute("INSERT INTO adlist (id, address, enabled, comment, number, abp_entries) VALUES (?,?,?,?,?,?)",
                        (l["id"], l["address"], 1 if l["enabled"] else 0, l["comment"], l["number"], l["number"]))
            for gid in l["groups"]:
                con.execute("INSERT INTO adlist_by_group VALUES (?,?)", (l["id"], gid))
        by_comment = {l["comment"]: l["id"] for l in self.store.lists}
        if gravity is None:                                  # every list is in gravity, as after `pihole -g`
            gravity = {sid: pb.list_domains(sid, LISTS) for sid in ("youtube", "instagram")}
        for sid, domains in gravity.items():
            for d in domains:
                con.execute("INSERT INTO gravity VALUES (?,?)", (d, by_comment["pb:" + sid]))
        for typ, domain, enabled in self.allow:
            con.execute("INSERT INTO domainlist (type, domain, enabled) VALUES (?,?,?)", (typ, domain, enabled))
        for d in self.antigravity:
            con.execute("INSERT INTO antigravity VALUES (?,?)", (d, 1))
        con.commit()
        con.close()
        con = sqlite3.connect(self.fdb)
        con.executescript(FTL_SCHEMA)
        ids = {}
        for ts, status, domain, client in self.queries:
            for table, value in (("domain_by_id", domain), ("client_by_id", client)):
                if (table, value) not in ids:
                    col = "domain" if table == "domain_by_id" else "ip"
                    ids[(table, value)] = con.execute("INSERT INTO %s (%s) VALUES (?)" % (table, col), (value,)).lastrowid
            con.execute("INSERT INTO query_storage (timestamp, type, status, domain, client) VALUES (?,?,?,?,?)",
                        (ts, 1, status, ids[("domain_by_id", domain)], ids[("client_by_id", client)]))
        con.commit()
        con.close()

    def diagnose(self, targets=("youtube", "instagram"), gravity=None, probe=None):
        self.build(gravity)
        facts = pb.gather_diagnose(self.api, self.catalog, list(targets), self.now, probe=probe or FakeProbe(gateway=None))
        findings = pb.diagnose_findings(facts)
        return facts, findings, "\n".join(pb.render_diagnose(facts, findings))


class DiagnoseTests(DiagnoseBase):
    def test_healthy_setup_has_nothing_to_report(self):
        self.group("pb-svc-youtube")["date_modified"] = self.now - 600
        self.queries = [(self.now - 3000, 2, "www.youtube.com", "192.168.1.23"),       # before the toggle: allowed
                        (self.now - 100, 1, "www.youtube.com", "192.168.1.23")]        # after it: blocked
        facts, findings, text = self.diagnose()
        self.assertEqual(len(findings), 1, findings)
        self.assertIn("Nothing wrong found", findings[0])
        self.assertEqual(facts["errors"], [])
        self.assertTrue(all(b for e in facts["kids"][0]["eff"] for b in e["blocked"].values()))
        self.assertIn("youtube blocked", text)
        periods = {(q["period"], q["status"]) for q in facts["targets"][0]["queries"]}
        self.assertEqual(periods, {("before", 2), ("after", 1)})
        self.assertIn("blocked (gravity)", text)

    def test_disabled_group_is_the_first_finding(self):
        self.group("pb-svc-youtube")["enabled"] = False
        _, findings, text = self.diagnose()
        self.assertIn("YouTube: its group is disabled", findings[0])
        self.assertIn("disabled", text)

    def test_list_without_domains_and_wrong_form_in_gravity(self):
        for l in self.store.lists:
            if l["comment"] == "pb:youtube":
                l["number"] = 0
        _, findings, text = self.diagnose(gravity={"youtube": ["youtube.com"], "instagram": pb.list_domains("instagram", LISTS)})
        self.assertTrue(any("YouTube: its block list holds 0 domains" in f for f in findings), findings)
        self.assertTrue(any("none of its domains" in f and "||youtube.com^" in f for f in findings), findings)
        self.assertFalse(any("lack pb-svc-youtube" in f for f in findings),
                         "a domain missing from gravity must not be blamed on the child's groups")
        self.assertIn("NOT FOUND", text)

    def test_allow_rules_that_override(self):
        self.allow = [(0, "youtube.com", 1), (2, r"(\.|^)instagram\.com$", 1), (0, "youtu.be", 0)]
        self.antigravity = ["||youtube.com^"]
        _, findings, _ = self.diagnose()
        text = "\n".join(findings)
        self.assertIn("exact allow rule youtube.com", text)
        self.assertIn("regex allow rule", text)
        self.assertIn("an allow list contains youtube.com", text)
        self.assertNotIn("youtu.be", text, "a disabled allow rule does not count")

    def test_a_row_that_overrides_the_childs_own_row_is_named(self):
        self.add_row("192.168.1.0/24", [0])
        facts, findings, text = self.diagnose()
        self.assertEqual(facts["kids"][0]["eff"][0]["via"], "192.168.1.0/24")
        self.assertEqual(len([f for f in findings if "is treated as the client row" in f]), 1, "one finding for both apps")
        self.assertTrue(any("is treated as the client row 192.168.1.0/24" in f and "YouTube and Instagram are not blocked" in f
                            for f in findings), findings)
        self.assertIn("youtube NOT blocked", text)
        self.assertNotIn("Nothing wrong", "\n".join(findings))

    def test_a_missing_group_on_the_childs_row_is_named(self):
        self.api.put_client(self.MAC, "Sara", [g for g in self.all_pb_groups() if g != self.gid()["pb-svc-youtube"]])
        _, findings, _ = self.diagnose()
        self.assertTrue(any("lack pb-svc-youtube" in f and "YouTube is not blocked" in f for f in findings), findings)

    def test_no_child_device_added(self):
        self.api.request("DELETE", "/api/clients/" + self.api.q(self.MAC))
        _, findings, _ = self.diagnose()
        self.assertTrue(any("No child device is added" in f for f in findings), findings)

    def test_child_that_never_reaches_the_box(self):
        self.queries = [(self.now - 300, 2, "example.org", "192.168.1.99")]
        _, findings, _ = self.diagnose()
        self.assertTrue(any("no DNS query from 192.168.1.23 reached this box" in f for f in findings), findings)
        self.queries.append((self.now - 100, 1, "youtube.com", "192.168.1.23"))
        _, findings, _ = self.diagnose()
        self.assertFalse(any("reached this box" in f for f in findings), findings)

    def test_router_relaying_is_found(self):
        self.queries = [(self.now - 60, 2, "example.org", "192.168.1.1")] * 40 + [(self.now - 60, 2, "example.org", "192.168.1.23")] * 5
        _, findings, _ = self.diagnose(probe=FakeProbe(gateway="192.168.1.1"))
        self.assertTrue(any("The router (192.168.1.1) sends most DNS queries" in f for f in findings), findings)

    def test_still_answered_after_the_toggle_although_rules_look_right(self):
        self.group("pb-svc-youtube")["date_modified"] = self.now - 600
        self.queries = [(self.now - 100, 2, "m.youtube.com", "192.168.1.23")] * 3
        _, findings, _ = self.diagnose()
        self.assertEqual(len(findings), 1)
        self.assertIn("still answered normally after its group last changed", findings[0])
        self.assertIn("192.168.1.23 (3)", findings[0])

    def test_unreadable_database_is_reported_not_fatal(self):
        self.build()
        os.remove(self.gdb)
        self.group("pb-svc-youtube")["enabled"] = False
        facts = pb.gather_diagnose(self.api, self.catalog, ["youtube"], self.now, probe=FakeProbe(gateway=None))
        self.assertTrue(facts["errors"] and "gravity.db" in facts["errors"][0])
        findings = pb.diagnose_findings(facts)
        self.assertTrue(any("its group is disabled" in f for f in findings), "API-based findings still work")
        self.assertIn("Could not read", "\n".join(pb.render_diagnose(facts, findings)))

    def test_sample_domains_prefer_the_best_known_name(self):
        self.assertEqual(pb.sample_domains("youtube", 1), ["||youtube.com^"])
        self.assertEqual(pb.sample_domains("instagram", 1)[0], "||instagram.com^")
        self.assertEqual(len(pb.sample_domains("x-twitter")), 3)

    def test_missing_list_files_are_an_error_not_an_all_clear(self):
        with mock.patch.object(pb, "LISTS_DIR", "/nonexistent/lists"):
            facts, findings, text = self.diagnose()
        self.assertTrue(any("list file" in e for e in facts["errors"]), facts["errors"])
        self.assertNotIn("the rules look right", "\n".join(findings))
        self.assertIn("some checks could not run", findings[-1])

    def test_output_never_contains_credentials(self):
        _, _, text = self.diagnose()
        for secret in (mock_pihole.PASSWORD, "cli_pw", "sid"):
            self.assertNotIn(" %s " % secret, " %s " % text.replace("\n", " "))


class SqlAccessTests(unittest.TestCase):
    def test_sql_literals(self):
        self.assertEqual(pb.sql_str("||youtube.com^"), "'||youtube.com^'")
        self.assertEqual(pb.sql_str("%.youtube.com"), "'%.youtube.com'")
        for bad in ("a'b", "x; DROP TABLE y", "a b", ""):
            with self.assertRaises(ValueError):
                pb.sql_str(bad)
        self.assertEqual(pb.sql_ints([3, "4"]), "3,4")
        self.assertEqual(pb.sql_ints([]), "NULL")

    def test_pihole_ftl_sqlite3_is_used_read_only_with_json(self):
        calls = []

        def fake_run(argv, **kw):
            calls.append(argv)
            return subprocess.CompletedProcess(argv, 0, stdout='[{"n": 2}]\n', stderr="")
        env = {k: v for k, v in os.environ.items() if k != "SINKO_SQL_BACKEND"}
        with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(pb.shutil, "which", return_value="/usr/bin/pihole-FTL"), \
                mock.patch.object(pb.subprocess, "run", fake_run):
            self.assertEqual(pb.sql_rows("/etc/pihole/gravity.db", "SELECT 2 AS n"), [{"n": 2}])
        self.assertEqual(calls[0][:5], ["/usr/bin/pihole-FTL", "sqlite3", "-readonly", "-json", "/etc/pihole/gravity.db"])

    def test_empty_output_means_no_rows_and_errors_are_reported(self):
        env = {k: v for k, v in os.environ.items() if k != "SINKO_SQL_BACKEND"}
        with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(pb.shutil, "which", return_value="/x/pihole-FTL"):
            with mock.patch.object(pb.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, stdout="", stderr="")):
                self.assertEqual(pb.sql_rows("db", "SELECT 1 WHERE 0"), [])
            with mock.patch.object(pb.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, stdout="", stderr="no such table")):
                with self.assertRaises(pb.SqlError):
                    pb.sql_rows("db", "SELECT * FROM nope")


SAMPLE_LOG = """\
Oct  3 08:00:01 dnsmasq[123]: query[A] www.youtube.com from 192.168.1.23
Oct  3 08:00:01 dnsmasq[123]: gravity blocked www.youtube.com is 0.0.0.0
Oct  3 08:00:02 dnsmasq[123]: query[A] example.org from 192.168.1.23
Oct  3 08:00:02 dnsmasq[123]: reply example.org is 93.184.216.34
Oct  3 08:00:03 dnsmasq[123]: query[AAAA] i.instagram.com from 192.168.1.50
Oct  3 08:00:03 dnsmasq[123]: forwarded i.instagram.com to 1.1.1.3
Oct  3 08:00:03 dnsmasq[123]: reply i.instagram.com is 157.240.1.1
Oct  3 08:00:04 dnsmasq[123]: 77 192.168.1.23/53012 query[A] youtube.com from 192.168.1.23
Oct  3 08:00:04 dnsmasq[123]: 77 192.168.1.23/53012 regex denied youtube.com is 0.0.0.0
Oct  3 08:00:05 dnsmasq[123]: query[A] notyoutube.com from 192.168.1.23
"""


class WatchTests(unittest.TestCase):
    def dmap(self):
        return pb.service_domain_map(pb.load_catalog(LISTS), LISTS)

    def test_service_domain_map_and_suffix_matching(self):
        d = self.dmap()
        self.assertEqual(d["youtube.com"], "youtube")
        self.assertEqual(pb.service_of("a.b.YouTube.com.", d), "youtube")
        self.assertIsNone(pb.service_of("notyoutube.com", d))
        self.assertEqual(set(pb.service_domain_map(pb.load_catalog(LISTS), LISTS, only={"instagram"}).values()), {"instagram"})

    def test_feed_counts_queries_per_client_and_outcome(self):
        state = pb.WatchState(self.dmap())
        events = [state.feed(line) for line in SAMPLE_LOG.splitlines()]
        c = state.counts
        self.assertEqual(set(c), {"192.168.1.23", "192.168.1.50"}, "queries for other domains are ignored")
        self.assertEqual((c["192.168.1.23"]["queries"], c["192.168.1.23"]["blocked"], c["192.168.1.23"]["answered"]), (2, 2, 0))
        self.assertEqual((c["192.168.1.50"]["queries"], c["192.168.1.50"]["blocked"], c["192.168.1.50"]["answered"]), (1, 0, 1))
        self.assertEqual(events[0][2], "query")
        self.assertEqual(events[1][2], "blocked")
        self.assertEqual(events[6][2], "answered")
        self.assertIsNone(events[5], "forwarded lines carry no outcome")
        self.assertEqual(events[7][0], "192.168.1.23", "log-queries=extra lines are understood")

    def test_summary_verdicts(self):
        state = pb.WatchState(self.dmap())
        for line in SAMPLE_LOG.splitlines():
            state.feed(line)
        text = "\n".join(pb.watch_summary(state, {"192.168.1.23": "Sara"}, 2))
        self.assertIn("192.168.1.23 = child device Sara", text)
        self.assertIn("blocking works for this address", text)
        self.assertIn("192.168.1.50 (not a registered child device)", text)
        self.assertIn("add it as a child device", text)
        text = "\n".join(pb.watch_summary(state, {"192.168.1.50": "Ali"}, 2))
        self.assertIn("look for a row that overrides it", text)
        empty = "\n".join(pb.watch_summary(pb.WatchState({}), {}, 2))
        self.assertIn("not using this box for DNS", empty)

    def test_following_a_log_file_including_rotation(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "pihole.log")
            with open(path, "w") as fh:
                fh.write("Oct  3 07:00:00 dnsmasq[1]: query[A] youtube.com from 10.0.0.9\\n".replace("\\n", "\n"))   # before we start
            out = []
            result = {}

            def run():
                result["state"] = pb.watch_log(path, self.dmap(), 2.0, out=out.append, poll=0.02, names={"192.168.1.23": "Sara"})
            t = threading.Thread(target=run)
            t.start()
            time.sleep(0.4)
            with open(path, "a") as fh:
                fh.write("\n".join(SAMPLE_LOG.splitlines()[:2]) + "\n")
            time.sleep(0.3)
            os.rename(path, path + ".1")                                     # log rotation: a new file appears
            with open(path, "w") as fh:
                fh.write("\n".join(SAMPLE_LOG.splitlines()[4:7]) + "\n")
            t.join()
        counts = result["state"].counts
        self.assertNotIn("10.0.0.9", counts, "lines from before watching started are ignored")
        self.assertEqual(counts["192.168.1.23"]["blocked"], 1)
        self.assertEqual(counts["192.168.1.50"]["answered"], 1, "the rotated file is read from its start")
        self.assertTrue(any("Sara asks for www.youtube.com" in l for l in out), out)
        self.assertTrue(any("BLOCKED" in l for l in out))

    def test_kid_address_names(self):
        groups = {"pb-kids": {"id": 5, "name": "pb-kids"}}
        clients = [{"client": "AA:BB:CC:00:00:23", "comment": "Sara", "groups": [0, 5]},
                   {"client": "192.168.1.77", "comment": "Ali", "groups": [0, 5]},
                   {"client": "192.168.1.9", "comment": "Dad", "groups": [0]}]
        devices = [{"hwaddr": "aa:bb:cc:00:00:23", "ips": [{"ip": "192.168.1.23"}, {"ip": "2001:db8::23"}]}]
        names = pb.kid_address_names(groups, clients, devices)
        self.assertEqual(names, {"192.168.1.23": "Sara", "2001:db8::23": "Sara", "192.168.1.77": "Ali"})


class MatchingHelpers(unittest.TestCase):
    def test_parsers(self):
        self.assertTrue(pb.is_mac("aa:bb:cc:00:00:01") and pb.is_mac("AA-BB-CC-00-00-01"))
        self.assertFalse(pb.is_mac("192.168.1.1") or pb.is_mac("sara-ipad") or pb.is_mac(None))
        self.assertEqual(str(pb.parse_ip("fe80::1%eth0")), "fe80::1")
        self.assertIsNone(pb.parse_net("AA:BB:CC:00:00:01"))
        self.assertIsNone(pb.parse_net("sara-ipad"))
        self.assertEqual(pb.parse_net("192.168.1.9/24").prefixlen, 24)
        self.assertEqual(pb.device_ips({"hwaddr": "ip-fd00::9", "ips": []}), ["fd00::9"])


if __name__ == "__main__":
    unittest.main()
