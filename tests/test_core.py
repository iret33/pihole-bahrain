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

loader = importlib.machinery.SourceFileLoader("pb", os.path.join(ROOT, "bin", "sinko"))
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


class RemoveAdoptedClientTests(Base):
    """A client row that the owner had before Sinko, or gave a group of their own since, is not Sinko's to delete: on
    `sinko remove` (and the seal's remove-and-setup) Sinko's groups come off it and the owner's stay. Only a row that
    holds nothing but Sinko's groups and the Default group goes."""

    def kid(self, client, comment, extra=(), with_default=True):
        gid = {g["name"]: g["id"] for g in self.store.groups}
        groups = sorted(set(pb.kid_groups(gid, self.catalog)) | set(extra))
        if not with_default:
            groups = [g for g in groups if g != 0]
        self.api.request("POST", "/api/clients", {"client": client, "comment": comment, "groups": groups})

    def rows(self):
        return {c["client"]: c for c in self.store.clients}

    def setup(self):
        self.ctl.setup(run_gravity=False)
        self.api.request("POST", "/api/groups", {"name": "Adults", "comment": "mine", "enabled": True})
        self.api.request("POST", "/api/groups", {"name": "NoAds", "comment": "mine too", "enabled": True})
        return {g["name"]: g["id"] for g in self.store.groups}

    def test_a_row_with_a_group_of_the_owners_survives_with_only_that_group_and_default(self):
        gid = self.setup()
        self.kid("AA:BB:CC:00:00:01", "Sara's tablet", extra=[gid["Adults"], gid["NoAds"]])
        self.ctl.remove()
        row = self.rows()["AA:BB:CC:00:00:01"]
        self.assertEqual(sorted(row["groups"]), sorted([0, gid["Adults"], gid["NoAds"]]))
        self.assertEqual(row["comment"], "Sara's tablet", "and keeps its name")
        self.assertEqual({g["name"] for g in self.store.groups}, {"Default", "Adults", "NoAds"})

    def test_a_row_that_holds_only_sinkos_groups_and_the_default_one_is_deleted(self):
        self.setup()
        self.kid("AA:BB:CC:00:00:02", "Omar")
        self.ctl.remove()
        self.assertEqual(self.rows(), {})

    def test_a_row_without_the_default_group_that_holds_only_sinkos_is_deleted_too(self):
        self.setup()
        self.kid("AA:BB:CC:00:00:03", "Layla", with_default=False)
        self.ctl.remove()
        self.assertEqual(self.rows(), {})

    def test_a_row_with_only_a_group_of_the_owners_and_no_default_keeps_that_group(self):
        gid = self.setup()
        self.kid("192.168.1.30", "Hamad", extra=[gid["Adults"]], with_default=False)
        self.ctl.remove()
        self.assertEqual(self.rows()["192.168.1.30"]["groups"], [gid["Adults"]])

    def test_each_row_is_judged_on_its_own(self):
        gid = self.setup()
        self.kid("AA:BB:CC:00:00:01", "Sara", extra=[gid["Adults"]])
        self.kid("AA:BB:CC:00:00:02", "Omar")
        self.api.request("POST", "/api/clients", {"client": "AA:BB:CC:00:00:09", "comment": "mine, never a kid",
                                                  "groups": [0, gid["Adults"]]})
        self.ctl.remove()
        self.assertEqual(sorted(self.rows()), ["AA:BB:CC:00:00:01", "AA:BB:CC:00:00:09"])
        self.assertEqual(sorted(self.rows()["AA:BB:CC:00:00:09"]["groups"]), sorted([0, gid["Adults"]]), "not touched at all")

    def test_a_paused_device_of_the_owners_loses_only_the_pause(self):
        gid = self.setup()
        self.kid("AA:BB:CC:00:00:04", "Noor", extra=[gid["Adults"], gid["pb-paused"]])
        self.ctl.remove()
        self.assertEqual(sorted(self.rows()["AA:BB:CC:00:00:04"]["groups"]), sorted([0, gid["Adults"]]))

    def test_the_seals_remove_then_setup_keeps_such_a_row_and_does_not_bring_sinko_back_onto_it(self):
        gid = self.setup()
        self.kid("AA:BB:CC:00:00:01", "Sara", extra=[gid["Adults"]])
        self.kid("AA:BB:CC:00:00:02", "Omar")
        self.ctl.remove()
        self.ctl.setup(run_gravity=False)
        self.assertEqual(sorted(self.rows()), ["AA:BB:CC:00:00:01"])
        names = {n: g for n, g in {g["name"]: g for g in self.store.groups}.items()}
        self.assertNotIn(names["pb-kids"]["id"], self.rows()["AA:BB:CC:00:00:01"]["groups"],
                         "a setup after the removal does not make it a kid device again")

    def test_removing_twice_is_harmless(self):
        gid = self.setup()
        self.kid("AA:BB:CC:00:00:01", "Sara", extra=[gid["Adults"]])
        self.ctl.remove()
        before = json.dumps(self.store.clients, sort_keys=True)
        self.ctl.remove()
        self.assertEqual(json.dumps(self.store.clients, sort_keys=True), before)


OLD_COMMENT = "pihole-bahrain: blocks everything for offline/paused kid devices"


class OwnersBlockAllRuleTests(Base):
    """An allow-list-only Pi-hole already has a deny rule for "^.*$". Pi-hole keeps one entry per pattern, so Sinko
    cannot add its own: it must join the owner's rule, never rewrite or delete it."""

    def rule(self):
        return [d for d in self.store.domains if d["domain"] == pb.BLOCK_ALL_REGEX and d.get("type", "deny") == "deny"]

    def add_owner_rule(self, comment="my allow-list-only mode", enabled=True, groups=(0,)):
        self.api.request("POST", "/api/domains/deny/regex", {"domain": pb.BLOCK_ALL_REGEX, "comment": comment,
                                                             "groups": list(groups), "enabled": enabled})
        self.api.request("POST", "/api/domains/allow/exact", {"domain": "school.example", "comment": "allowed", "groups": [0]})

    def test_the_owners_rule_keeps_its_comment_and_groups_and_gains_sinkos(self):
        self.add_owner_rule()
        self.ctl.setup(run_gravity=False)
        rule, = self.rule()
        self.assertEqual(rule["comment"], "my allow-list-only mode")
        self.assertTrue(rule["enabled"])
        self.assertEqual(sorted(rule["groups"]), sorted([0, self.group("pb-offline")["id"], self.group("pb-paused")["id"]]),
                         "the Default group is still in it: the rule still covers everybody")
        snapshot = json.dumps(self.store.domains, sort_keys=True)
        writes = self.store.writes
        self.ctl.setup(run_gravity=False)
        self.assertEqual(json.dumps(self.store.domains, sort_keys=True), snapshot)
        self.assertEqual(self.store.writes, writes, "and a second setup rewrites nothing")

    def test_removing_sinko_leaves_the_owners_rule_with_only_the_owners_groups(self):
        self.add_owner_rule()
        self.ctl.setup(run_gravity=False)
        self.add_kid()
        self.ctl.remove()
        rule, = self.rule()
        self.assertEqual((rule["comment"], rule["groups"], rule["enabled"]), ("my allow-list-only mode", [0], True))
        self.assertEqual([d["domain"] for d in self.store.domains if d["domain"] == "school.example"], ["school.example"])
        self.assertEqual(self.store.clients, [])
        self.assertEqual({g["name"] for g in self.store.groups}, {"Default"})

    def test_an_owners_rule_that_is_switched_off_is_not_switched_on_and_the_doctor_says_so(self):
        self.add_owner_rule(enabled=False)
        with self.assertLogs("sinko", level="WARNING") as logs:
            self.ctl.setup(run_gravity=False)
        self.assertIn("switched off", logs.records[0].getMessage())
        rule, = self.rule()
        self.assertEqual((rule["enabled"], rule["groups"], rule["comment"]), (False, [0], "my allow-list-only mode"))
        lines = []
        pb.block_all_check(pb.Report(out=lines.append), self.api.group_map(), self.api.domains())
        self.assertTrue(lines[0].startswith("  FIX   the ^.*$ rule is switched off"), lines)
        self.assertIn("It is your own", lines[0])

    def test_a_rule_without_a_comment_is_the_owners_too(self):
        self.add_owner_rule(comment="")
        self.ctl.setup(run_gravity=False)
        self.ctl.remove()
        rule, = self.rule()
        self.assertIn(rule["comment"], ("", None))                # (Pi-hole's own idea of "no comment")
        self.assertEqual(rule["groups"], [0])

    def test_the_rule_the_2x_release_made_is_re_owned_and_removed_with_the_rest(self):
        self.add_owner_rule(comment=OLD_COMMENT, groups=(0,))
        self.ctl.setup(run_gravity=False)
        rule, = self.rule()
        self.assertEqual(rule["comment"], pb.BLOCK_ALL_COMMENT)
        self.assertEqual(sorted(rule["groups"]), sorted([self.group("pb-offline")["id"], self.group("pb-paused")["id"]]))
        self.ctl.remove()
        self.assertEqual(self.rule(), [])

    def test_a_rule_of_sinkos_own_is_repaired_when_somebody_edited_it(self):
        self.ctl.setup(run_gravity=False)
        rule, = self.rule()
        self.api.request("PUT", "/api/domains/deny/regex/" + pb.Api.q(pb.BLOCK_ALL_REGEX),
                         {"type": "deny", "kind": "regex", "comment": pb.BLOCK_ALL_COMMENT, "groups": [0], "enabled": False})
        self.ctl.setup(run_gravity=False)
        rule, = self.rule()
        self.assertTrue(rule["enabled"])
        self.assertEqual(sorted(rule["groups"]), sorted([self.group("pb-offline")["id"], self.group("pb-paused")["id"]]))

    def test_an_allow_rule_of_the_same_pattern_does_not_stop_the_removal(self):
        self.ctl.setup(run_gravity=False)
        self.api.request("POST", "/api/domains/allow/regex", {"domain": pb.BLOCK_ALL_REGEX, "comment": "allow everything", "groups": [0]})
        self.ctl.remove()
        left = [d for d in self.store.domains if d["domain"] == pb.BLOCK_ALL_REGEX]
        self.assertEqual([(d["type"], d["comment"]) for d in left], [("allow", "allow everything")])
        self.assertEqual({g["name"] for g in self.store.groups}, {"Default"}, "the removal ran to the end")

    def test_the_doctor_checks_the_rule_behind_internet_off_and_pause(self):
        def lines_for():
            out = []
            pb.block_all_check(pb.Report(out=out.append), self.api.group_map(), self.api.domains())
            return out
        self.ctl.setup(run_gravity=False)
        self.assertEqual(lines_for(), ["  ok    the rule behind 'internet off' and 'pause' is in place"])
        self.api.request("DELETE", "/api/domains/deny/regex/" + pb.Api.q(pb.BLOCK_ALL_REGEX))
        self.assertIn("missing", lines_for()[0])
        self.api.request("POST", "/api/domains/deny/regex", {"domain": pb.BLOCK_ALL_REGEX, "comment": "mine", "groups": [0]})
        self.assertIn("does not apply to Sinko's groups", lines_for()[0])
        self.ctl.setup(run_gravity=False)
        self.assertTrue(lines_for()[0].startswith("  info  your own ^.*$ rule also serves"), lines_for())


class BlockedServiceOfANewerReleaseTests(Base):
    """After a rollback the older release does not know a service that the newer one added. A blocked one keeps its
    group (and its list): deleting them would turn the parent's block into nothing, silently, for good."""

    def newer_catalog(self):
        extra = dict(self.catalog)
        extra["services"] = self.catalog["services"] + [dict(self.catalog["services"][0], id="newapp", name="New App")]
        return extra

    def setUp(self):
        super().setUp()
        self.newer = pb.Controller(self.api, self.newer_catalog(), "https://lists.example/l")
        self.newer.setup(run_gravity=False)
        self.add_kid()

    def block(self, name):
        self.api.put_group(name, "", True)

    def test_a_blocked_service_the_release_does_not_know_keeps_its_group_list_and_kids(self):
        self.block("pb-svc-newapp")
        kid_before = list(self.store.clients[0]["groups"])
        self.ctl.setup(run_gravity=False)                         # the older release's setup, after `sinko rollback`
        self.assertTrue(self.group("pb-svc-newapp")["enabled"], "still blocked")
        self.assertIn("pb:newapp", {l["comment"] for l in self.store.lists}, "its list too: the block holds in the meantime")
        self.assertEqual(self.store.clients[0]["groups"], kid_before)

    def test_after_the_roll_forward_the_block_is_still_there(self):
        self.block("pb-svc-newapp")
        self.ctl.setup(run_gravity=False)
        self.newer.setup(run_gravity=False)
        self.assertTrue(self.group("pb-svc-newapp")["enabled"])
        self.assertEqual(len([l for l in self.store.lists if l["comment"] == "pb:newapp"]), 1)
        self.assertIn(self.group("pb-svc-newapp")["id"], self.store.clients[0]["groups"])

    def test_a_service_that_is_allowed_and_gone_from_the_catalog_is_still_removed(self):
        self.ctl.setup(run_gravity=False)
        self.assertNotIn("pb-svc-newapp", {g["name"] for g in self.store.groups})
        self.assertNotIn("pb:newapp", {l["comment"] for l in self.store.lists})

    def test_removing_sinko_removes_the_kept_group_too(self):
        self.block("pb-svc-newapp")
        self.ctl.setup(run_gravity=False)
        self.ctl.remove()
        self.assertEqual({g["name"] for g in self.store.groups}, {"Default"})
        self.assertEqual(self.store.lists, [])

    def test_the_summary_counts_only_the_services_this_release_knows(self):
        self.block("pb-svc-newapp")
        self.ctl.setup(run_gravity=False)
        groups = self.api.group_map()
        ids = [g["id"] for g in groups.values()]
        known = {s["id"] for s in self.catalog["services"]}
        total = len(known)
        self.assertIn("%d/%d service groups" % (total, total), pb.group_summary(ids, groups, known))
        self.assertIn("%d/%d service groups" % (total + 1, total + 1), pb.group_summary(ids, groups))

    def test_the_repair_run_by_the_scheduler_does_not_touch_the_leftovers_of_the_1x_installer(self):
        self.api.request("POST", "/api/groups", {"name": "Kids", "comment": "Parental-control service blocklists"})
        self.api.request("POST", "/api/domains/deny/regex", {"domain": ".*", "comment": "legacy", "groups": [0]})
        before = json.dumps([self.store.groups, self.store.domains], sort_keys=True)
        self.ctl.setup(run_gravity=False, legacy=False)
        self.assertIn("Kids", {g["name"] for g in self.store.groups})
        self.assertIn(".*", {d["domain"] for d in self.store.domains})
        self.assertEqual(json.dumps([self.store.groups, self.store.domains], sort_keys=True).count("legacy"), before.count("legacy"))


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

    def test_rule_changes_clear_the_dns_cache_once_and_only_when_something_changed(self):
        now = dt.datetime(2026, 9, 17, 16, 0)
        before = self.store.dns_restarts
        self.set_state(timer={"mode": "free", "until": now.timestamp() - 1,
                              "snapshot": {"services": {"youtube": True}, "offline": False}})
        self.ctl.tick(now)
        self.assertEqual(self.store.dns_restarts, before + 1, "a restored rule set empties the resolver's cache, once")
        self.ctl.tick(now)                                        # nothing to do now
        self.assertEqual(self.store.dns_restarts, before + 1, "and an idle pass does not touch the resolver")

    def test_a_failed_cache_flush_never_undoes_the_rule_change(self):
        now = dt.datetime(2026, 9, 17, 16, 0)
        real_request = self.api.request

        def request(method, path, *a, **k):
            if path == "/api/action/restartdns":
                raise RuntimeError("the resolver did not answer")
            return real_request(method, path, *a, **k)
        self.api.request = request
        self.set_state(timer={"mode": "free", "until": now.timestamp() - 1,
                              "snapshot": {"services": {"youtube": True}, "offline": False}})
        self.ctl.tick(now)
        self.assertTrue(self.enabled("pb-svc-youtube"))

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
                os.environ.pop("SINKO_API_URL", None)
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
            fh.write("# comment\nSINKO_LISTS_BASE='https://cdn.example/lists/'\nSINKO_HOSTNAME=family.lan\n")
        try:
            conf = pb.read_config(fh.name)
        finally:
            os.remove(fh.name)
        self.assertEqual(conf["SINKO_HOSTNAME"], "family.lan")
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SINKO_LISTS_BASE", None)
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
