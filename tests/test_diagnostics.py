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

    def report(self):
        lines = []
        rep = pb.Report(out=lines.append)
        pb.doctor_api_checks(rep, self.api, self.catalog)
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
