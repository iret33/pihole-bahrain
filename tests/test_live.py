"""The data behind the parent page's live picture: the mock's statistics endpoints (they must keep the JSON shapes of
Pi-hole's real API, taken from FTL's api/stats.c, api/history.c and api/queries.c) and the domain -> app map."""
import importlib.machinery
import importlib.util
import json
import os
import time
import unittest

import mock_pihole

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
LISTS = os.path.join(ROOT, "lists")

loader = importlib.machinery.SourceFileLoader("pb", os.path.join(ROOT, "bin", "nay"))
spec = importlib.util.spec_from_loader("pb", loader)
pb = importlib.util.module_from_spec(spec)
loader.exec_module(pb)

QUERY_KEYS = {"id", "time", "type", "domain", "status", "client", "dnssec", "reply", "upstream", "list_id", "cname", "ede"}


class MockStatsTests(unittest.TestCase):
    def setUp(self):
        self.httpd, self.store = mock_pihole.serve()
        self.api = pb.Api("http://127.0.0.1:%d" % self.httpd.server_port, password=mock_pihole.PASSWORD)
        self.api.login()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        now = time.time()
        self.store.add_query("www.youtube.com", "GRAVITY", "192.168.1.21", "Sara-iPad", now - 30)
        self.store.add_query("i.instagram.com", "GRAVITY", "192.168.1.21", "Sara-iPad", now - 20)
        self.store.add_query("www.apple.com", "FORWARDED", "192.168.1.9", "parents-phone", now - 10)
        self.store.add_query("www.apple.com", "CACHE", "192.168.1.9", "parents-phone", now - 5)
        self.store.add_query("old.example", "FORWARDED", "192.168.1.9", None, now - 2 * 86400)     # outside the 24 h window

    def get(self, path):
        return self.api.request("GET", path)

    def test_summary_has_ftls_shape_and_counts_the_last_24_hours(self):
        d = self.get("/api/stats/summary")
        self.assertTrue({"queries", "clients", "gravity"} <= set(d))
        self.assertTrue({"total", "blocked", "percent_blocked", "unique_domains", "forwarded", "cached", "frequency",
                         "types", "status", "replies"} <= set(d["queries"]))
        self.assertEqual({"active", "total"}, set(d["clients"]))
        self.assertEqual({"domains_being_blocked", "last_update"}, set(d["gravity"]))
        q = d["queries"]
        self.assertEqual((q["total"], q["blocked"], q["forwarded"], q["cached"]), (4, 2, 1, 1))
        self.assertEqual(q["percent_blocked"], 50.0)
        self.assertEqual(q["unique_domains"], 3)
        self.assertEqual(d["clients"]["active"], 2)

    def test_history_is_144_ten_minute_slots(self):
        h = self.get("/api/history")["history"]
        self.assertEqual(len(h), 144)
        self.assertEqual({"timestamp", "total", "cached", "blocked", "forwarded"}, set(h[0]))
        self.assertEqual([s["timestamp"] for s in h], sorted(s["timestamp"] for s in h))
        self.assertTrue(all(b - a == 600 for a, b in zip([s["timestamp"] for s in h], [s["timestamp"] for s in h][1:])))
        self.assertEqual(sum(s["total"] for s in h), 4)
        self.assertEqual(sum(s["blocked"] for s in h), 2)

    def test_top_domains_and_clients(self):
        d = self.get("/api/stats/top_domains?blocked=true&count=5")
        self.assertEqual(set(d), {"domains", "total_queries", "blocked_queries"})
        self.assertEqual({x["domain"] for x in d["domains"]}, {"www.youtube.com", "i.instagram.com"})
        self.assertEqual((d["total_queries"], d["blocked_queries"]), (4, 2))
        allowed = self.get("/api/stats/top_domains?count=5")["domains"]
        self.assertEqual(allowed, [{"domain": "www.apple.com", "count": 2}])
        c = self.get("/api/stats/top_clients?count=5")
        self.assertEqual(set(c["clients"][0]), {"name", "ip", "count"})
        self.assertEqual(len(c["clients"]), 2)

    def test_queries_are_newest_first_filtered_by_from_and_capped_by_length(self):
        rows = self.get("/api/queries?length=10")["queries"]
        self.assertTrue(all(QUERY_KEYS <= set(r) for r in rows))
        self.assertEqual([r["id"] for r in rows], sorted((r["id"] for r in rows), reverse=True))
        self.assertEqual(len(rows), 5)
        recent = self.get("/api/queries?from=%f&length=10" % (time.time() - 15))["queries"]
        self.assertEqual({r["domain"] for r in recent}, {"www.apple.com"})
        self.assertEqual(len(self.get("/api/queries?length=2")["queries"]), 2)
        self.assertEqual(rows[-1]["id"], 1, "the oldest query comes last")
        self.assertEqual({"ip", "name"}, set(rows[0]["client"]))

    def test_privacy_levels_hide_what_ftl_hides(self):
        self.store.privacy = 1
        rows = self.get("/api/queries")["queries"]
        self.assertEqual({r["domain"] for r in rows}, {"hidden"})
        self.assertNotEqual(rows[0]["client"]["ip"], "0.0.0.0")
        self.assertEqual(self.get("/api/stats/top_domains")["total_queries"], -1, "FTL hides the top lists from level 1 up")
        self.store.privacy = 2
        self.assertEqual({r["client"]["ip"] for r in self.get("/api/queries")["queries"]}, {"0.0.0.0"})
        self.assertEqual(self.get("/api/stats/top_domains")["domains"], [])
        self.assertEqual(self.get("/api/stats/top_clients")["total_queries"], -1)
        self.store.privacy = 3
        self.assertEqual(self.get("/api/queries"), {"queries": [], "cursor": None})
        self.assertGreater(self.get("/api/stats/summary")["queries"]["total"], 0, "counters stay available")


class DomainMapTests(unittest.TestCase):
    def setUp(self):
        self.catalog = pb.load_catalog(LISTS)
        self.map = pb.build_domain_map(self.catalog, LISTS)

    def test_every_list_domain_maps_to_its_app(self):
        d = self.map["domains"]
        self.assertEqual(self.map["v"], 1)
        for s in self.catalog["services"]:
            for entry in pb.list_domains(s["id"], LISTS):
                self.assertEqual(d[pb.bare_domain(entry).lower()], s["id"], entry)
        self.assertNotIn("guard", set(d.values()), "the anti-bypass list is not an app")

    def test_no_domain_belongs_to_two_apps(self):
        seen = {}
        for s in self.catalog["services"]:
            for entry in pb.list_domains(s["id"], LISTS):
                name = pb.bare_domain(entry).lower()
                self.assertNotIn(name, seen, "%s is in %s and %s" % (name, seen.get(name), s["id"]))
                seen[name] = s["id"]

    def test_lookup_matches_the_longest_suffix_like_the_page_does(self):
        d = self.map["domains"]
        self.assertEqual(pb.service_of("r4---sn-abc.googlevideo.com", d), "youtube")
        self.assertEqual(pb.service_of("scontent.cdninstagram.com", d), "instagram")
        self.assertIsNone(pb.service_of("notyoutube.com", d))
        self.assertIsNone(pb.service_of("www.apple.com", d))

    def test_output_is_deterministic_and_small(self):
        a = json.dumps(pb.build_domain_map(self.catalog, LISTS), separators=(",", ":"))
        self.assertEqual(a, json.dumps(pb.build_domain_map(self.catalog, LISTS), separators=(",", ":")))
        self.assertLess(len(a), 20000, "the page downloads this on every visit")
        self.assertEqual(list(self.map["domains"]), sorted(self.map["domains"]))


if __name__ == "__main__":
    unittest.main()
