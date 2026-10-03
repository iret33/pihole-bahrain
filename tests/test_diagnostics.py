"""Tests for `doctor`, `status` and the other diagnostics (client matching, network checks, ...)."""
import importlib.machinery
import importlib.util
import json
import os
import time
import unittest
from unittest import mock

import mock_pihole

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
LISTS = os.path.join(ROOT, "lists")

loader = importlib.machinery.SourceFileLoader("pb", os.path.join(ROOT, "bin", "pihole-bahrain"))
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
        self.assertEqual(len(rep.warnings), 1)
        self.assertIn("Ali", rep.warnings[0])

    def test_warnings_do_not_fail_doctor(self):
        lines = []
        rep = pb.Report(out=lines.append)
        rep.warn("something to look at")
        self.assertEqual(rep.finish(), 0)
        self.assertIn("1 warning(s)", lines[-1])
        rep.fix("broken")
        self.assertEqual(rep.finish(), 1)


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
        probe, env = self.probe_with(PB_ROUTE_FILE=route)
        with env:
            self.assertEqual(probe.default_gateway(), "192.168.1.1", "lowest metric wins")
        probe, env = self.probe_with(PB_ROUTE_FILE="Iface\tDestination\tGateway\tFlags\tRefCnt\tUse\tMetric\n")
        with env:
            self.assertIsNone(probe.default_gateway())
        with mock.patch.dict(os.environ, {"PB_ROUTE_FILE": "/nonexistent"}):
            self.assertIsNone(pb.SystemProbe().default_gateway())

    def test_ipv6_state_from_proc(self):
        inet6 = ("fe800000000000000a00000000000001 02 40 20 80 eth0\n"
                 "20010db8000000000000000000000005 02 40 00 80 eth0\n"
                 "00000000000000000000000000000001 01 80 10 80 lo\n")
        routes = ("00000000000000000000000000000000 00 00000000000000000000000000000000 00 "
                  "fe800000000000000000000000000001 00000400 00000001 00000000 00000003 eth0\n"
                  "00000000000000000000000000000000 00 00000000000000000000000000000000 00 "
                  "00000000000000000000000000000000 ffffffff 00000001 00000000 00200200 lo\n")
        probe, env = self.probe_with(PB_IF_INET6_FILE=inet6, PB_IPV6_ROUTE_FILE=routes)
        with env:
            self.assertIn(("2001:db8::5", 0), probe.ipv6_addresses())
            self.assertIn(("fe80::a00:0:0:1", 0x20), probe.ipv6_addresses())
            self.assertTrue(probe.ipv6_default_route())
            self.assertIn("2001:db8::5", probe.own_addresses())
        probe, env = self.probe_with(PB_IPV6_ROUTE_FILE=routes.splitlines()[1] + "\n")     # only the reject route on lo
        with env:
            self.assertFalse(probe.ipv6_default_route())

    def test_ftl_log_tail_reads_only_the_end(self):
        probe, env = self.probe_with(PB_FTL_LOG="old line\n" * 100000 + "Rate-limiting 10.0.0.1 for at least 5 seconds\n")
        with env:
            tail = probe.ftl_log_tail()
        self.assertLess(len(tail), 600 * 1024)
        self.assertIn("Rate-limiting 10.0.0.1", tail)


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
