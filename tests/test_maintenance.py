"""The scheduler's side jobs (class Maintenance): update check and install, request markers, power requests,
address watch, reconciling the update result. Pi-hole is the in-memory mock; everything slow or dangerous (network,
systemd, reboot, pihole-FTL) is a fake that records what it was asked, so no test can reboot or update this machine."""
import datetime as dt
import contextlib
import fcntl
import importlib.machinery
import importlib.util
import json
import os
import shutil
import stat
import subprocess
import tempfile
import threading
import time
import unittest
from unittest import mock

import fake_release
import mock_pihole

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
LISTS = os.path.join(ROOT, "lists")

loader = importlib.machinery.SourceFileLoader("sinko_cli", os.path.join(ROOT, "bin", "sinko"))
spec = importlib.util.spec_from_loader("sinko_cli", loader)
pb = importlib.util.module_from_spec(spec)
loader.exec_module(pb)

CONFIG = "SINKO_HOSTNAME=family.lan\nSINKO_IP=192.168.1.5\nSINKO_REF=latest\n"

# The real ones, kept before the fixture swaps them for fakes that record what a test must not reach.
REAL_RECOVER_CHECK = pb.recover_check
REAL_WRITE_BOX_INFO = pb.write_box_info
REAL_RUN_SELF_HEAL = pb.run_self_heal


class InlineJob:
    """A BackgroundJob that runs at once on the calling thread; like a thread's, its result is collected on the next
    pass (so a test can look at what the pass in between saw)."""

    def __init__(self, name):
        self.name = name
        self._busy = False
        self._outcome = None

    def busy(self):
        return self._busy

    def start(self, fn):
        if self._busy:
            return False
        self._busy = True
        try:
            self._outcome = ("ok", fn())
        except Exception as err:
            self._outcome = ("error", err)
        return True

    def take(self):
        outcome, self._outcome = self._outcome, None
        if outcome is not None:
            self._busy = False
        return outcome


class Fixture(unittest.TestCase):
    def setUp(self):
        self.httpd, self.store = fake_release.serve_pihole()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        self.api = pb.Api("http://127.0.0.1:%d" % self.httpd.server_port, password=mock_pihole.PASSWORD)
        self.api.login()
        self.catalog = pb.load_catalog(LISTS)
        self.ctl = pb.Controller(self.api, self.catalog, "https://lists.example/l")
        self.ctl.setup(run_gravity=False)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name
        self.state_dir = os.path.join(self.tmp, "state")
        self.run_dir = os.path.join(self.tmp, "run")
        self.webroot = os.path.join(self.tmp, "www")
        env = mock.patch.dict(os.environ, {"SINKO_STATE_DIR": self.state_dir, "SINKO_RUN_DIR": self.run_dir,
                                           "SINKO_WEBROOT": self.webroot})
        env.start()
        self.addCleanup(env.stop)
        for key in ("SINKO_RELEASE_BASE", "SINKO_RELEASE_API", "SINKO_REF", "SINKO_REPO", "SINKO_REPO_SLUG",
                    "SINKO_TELEMETRY_URL"):
            os.environ.pop(key, None)
        self.reached = []
        for name in ("run_power", "start_update_runner", "apply_new_address", "default_route_ipv4", "check_for_update",
                     "send_ping", "telemetry_body", "send_forget", "run_self_heal", "recover_check"):
            patch = mock.patch.object(pb, name, side_effect=lambda *a, _n=name, **k: self.reached.append(_n))
            patch.start()
            self.addCleanup(patch.stop)
        patch = mock.patch.object(pb, "write_box_info", lambda conf=None, **kw: self.fake_box_info(conf, **kw))
        patch.start()
        self.addCleanup(patch.stop)
        self.addCleanup(lambda: self.assertEqual(self.reached, [], "a test reached the real thing (reboot, systemd, pihole-FTL, GitHub)"))
        self.config_path = os.path.join(self.tmp, "config")
        with open(self.config_path, "w") as fh:
            fh.write(CONFIG)
        os.chmod(self.config_path, 0o644)
        self.now = dt.datetime(2026, 9, 17, 12, 0)             # a Thursday, noon
        self.mono = 1000.0
        self.found = None                                       # what the fake release check answers
        self.check_calls = []
        self.runner_calls = []
        self.runner_ok = True
        self.power_calls = []
        self.ip = "192.168.1.5"
        self.address_calls = []
        self.clock_trusted = True
        self.ping_calls = []
        self.ping_answer = {"online": 5, "total": 20}
        self.jitter_calls = []
        self.seen_at_call = {}
        self.boxinfo_calls = []
        self.boxinfo_error = None
        self.forget_calls = []
        self.forget_answer = True
        self.heal_calls = []
        self.heal_error = None
        self.recover_calls = []
        self.recover_answer = {"ok": True, "detail": ""}
        self.m = pb.Maintenance(self.api, config_path=self.config_path, monotonic=lambda: self.mono,
                                job_factory=InlineJob, clock_ok=lambda: self.clock_trusted,
                                check=self.fake_check, start_runner=self.fake_runner, power=self.fake_power,
                                default_ip=lambda: self.ip, apply_address=self.fake_apply, ping=self.fake_ping,
                                body=lambda: "BODY", jitter=self.fake_jitter, box_info=self.fake_box_info,
                                forget=self.fake_forget, heal=self.fake_heal, recover=self.fake_recover,
                                catalog=lambda: self.catalog)

    # ----- fakes -----
    def fake_check(self, conf):
        self.check_calls.append(conf)
        if isinstance(self.found, Exception):
            raise self.found
        return self.found

    def fake_runner(self):
        self.runner_calls.append(self.state()["update"]["status"])
        self.seen_at_call["runner"] = (self.state(), pb.load_handled())
        if isinstance(self.runner_ok, Exception):
            raise self.runner_ok
        return self.runner_ok

    def fake_power(self, action):
        self.power_calls.append(action)
        self.seen_at_call["power"] = (self.state(), pb.load_handled())
        if getattr(self, "power_error", None):
            raise self.power_error

    def fake_ping(self, url, body):
        self.ping_calls.append((url, body))
        if isinstance(self.ping_answer, Exception):
            raise self.ping_answer
        return self.ping_answer

    def fake_jitter(self, low, high):
        self.jitter_calls.append((low, high))
        return 0

    def fake_box_info(self, conf, **kw):
        self.boxinfo_calls.append((conf, kw))
        if self.boxinfo_error:
            raise self.boxinfo_error
        return "written"

    def fake_forget(self, url, ident):
        self.forget_calls.append((url, ident))
        if isinstance(self.forget_answer, Exception):
            raise self.forget_answer
        return self.forget_answer

    def fake_heal(self):
        self.heal_calls.append(self.state()["update"]["status"] if self.group_exists("pb-state") else None)
        if self.heal_error:
            raise self.heal_error

    def fake_recover(self):
        self.recover_calls.append(1)
        if isinstance(self.recover_answer, Exception):
            raise self.recover_answer
        return self.recover_answer

    def group_exists(self, name):
        return any(g["name"] == name for g in self.store.groups)

    def fake_apply(self, ip, conf, path):
        self.address_calls.append((ip, dict(conf), path))
        if getattr(self, "address_error", None):
            raise self.address_error

    # ----- helpers -----
    def group(self, name):
        return next(g for g in self.store.groups if g["name"] == name)

    def state(self):
        return pb.parse_state(self.group("pb-state")["comment"])

    def set_state(self, mutate):
        state = self.state()
        mutate(state)
        self.ctl.write_state(state)

    def release(self, version="3.1.0"):
        return {"version": version, "tag": "v" + version, "notes": "https://github.com/iret33/sinko/releases/tag/v" + version}

    def advance(self, seconds):
        self.now += dt.timedelta(seconds=seconds)
        self.mono += seconds

    def stamp(self, seconds_ago=0):
        """A request marker as the page writes it: the time of the request in ms on the box's clock."""
        return int((self.now.timestamp() - seconds_ago) * 1000)

    def pass_(self):
        return self.m.run(self.now)

    def settle(self):
        """A pass that starts the jobs, and one that collects what they returned."""
        return self.pass_() + self.pass_()

    def rules(self):
        return json.dumps([g for g in self.store.groups if g["name"] != "pb-state"], sort_keys=True)


class QuietTests(Fixture):
    def test_nothing_to_do_means_no_writes_no_calls_no_actions(self):
        writes = self.store.writes
        for _ in range(5):
            self.advance(15)
            self.assertEqual(self.pass_(), [])
        self.assertEqual(self.store.writes, writes, "a state write makes Pi-hole reload: only write on change")
        self.assertEqual((self.check_calls, self.runner_calls, self.power_calls, self.address_calls), ([], [], [], []))

    def test_before_setup_has_made_the_state_nothing_happens(self):
        self.api.delete_group("pb-state")
        self.assertEqual(self.pass_(), [])

    def test_pihole_being_down_is_logged_once_and_never_raises(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        with self.assertLogs("sinko", level="WARNING") as logs:
            for _ in range(10):
                self.advance(15)
                self.assertEqual(self.pass_(), [])
        self.assertEqual(len(logs.records), 1, "not one line per 15 seconds")

    def test_the_jobs_never_change_a_rule(self):
        before = self.rules()
        self.found = self.release("3.1.0")
        self.ip = "192.168.1.77"
        self.set_state(lambda s: s["update"].update(request=111, checkRequest=222, auto=True))
        self.set_state(lambda s: s["power"].update(request=333, action="reboot"))
        self.advance(200)
        self.settle()
        self.assertEqual(self.rules(), before)


class JobIsolationTests(Fixture):
    def test_one_failing_job_does_not_stop_the_others(self):
        self.set_state(lambda s: s["power"].update(request=5, action="reboot"))
        with mock.patch.object(pb.Maintenance, "_reconcile", side_effect=RuntimeError("boom")), \
                mock.patch.object(pb.Maintenance, "_check", side_effect=RuntimeError("boom")), \
                self.assertLogs("sinko", level="WARNING") as logs:
            self.settle()
        self.assertEqual(self.power_calls, ["reboot"])
        self.assertEqual(len(logs.records), 2, "one line per failing job, no more")

    def test_a_failure_is_logged_at_most_every_half_hour(self):
        with mock.patch.object(pb.Maintenance, "_address", side_effect=RuntimeError("down")), \
                self.assertLogs("sinko", level="WARNING") as logs:
            for _ in range(100):                       # 25 minutes of ticks
                self.advance(15)
                self.pass_()
            self.assertEqual(len(logs.records), 1)
            self.advance(600)
            self.pass_()
            self.assertEqual(len(logs.records), 2, "again after 30 minutes")

    def test_a_job_that_raises_a_non_api_error_is_still_contained(self):
        self.set_state(lambda s: s["power"].update(request=5, action="poweroff"))
        self.power_error = OSError("systemctl is gone")
        with self.assertLogs("sinko", level="WARNING"):
            self.settle()
        self.assertEqual(self.power_calls, ["poweroff"])


class UpdateRequestTests(Fixture):
    def test_a_request_stores_the_marker_clears_it_and_only_then_starts_the_runner(self):
        self.found = self.release("3.1.0")
        marker = self.stamp()
        self.set_state(lambda s: s["update"].update(request=marker, latest="3.1.0"))
        before = self.state()["update"]
        actions = self.pass_()
        self.assertIn("update requested by the page", actions)
        u = self.state()["update"]
        self.assertEqual((u["status"], u["from"], u["to"], u["request"], u["error"]), ("running", "3.0.0", "3.1.0", None, None))
        self.assertEqual(u["at"], self.now.timestamp())
        self.assertEqual(pb.load_handled(), {"update": marker})
        self.assertEqual(before["status"], "idle")
        state_at_launch, handled_at_launch = self.seen_at_call["runner"]
        self.assertEqual(state_at_launch["update"]["status"], "running", "the state said running before the runner started")
        self.assertIsNone(state_at_launch["update"]["request"], "and the request was already cleared")
        self.assertEqual(handled_at_launch, {"update": marker}, "and the marker was already stored")
        self.assertEqual(self.runner_calls, ["running"])

    def test_the_marker_is_durable_before_the_state_is_written(self):
        order = []
        real_put = self.api.put_group

        def spy(name, comment, enabled, new_name=None):
            if name == "pb-state":
                order.append(("state write", pb.load_handled().get("update"), pb.parse_state(comment)["update"]["request"]))
            return real_put(name, comment, enabled, new_name)
        self.api.put_group = spy
        self.set_state(lambda s: s["update"].update(request=42))
        order.clear()
        self.pass_()
        self.assertEqual(order, [("state write", 42, None)])

    def test_the_request_is_acted_on_once_not_every_tick(self):
        self.set_state(lambda s: s["update"].update(request=42))
        self.settle()
        self.settle()
        self.advance(30)
        self.settle()
        self.assertEqual(self.runner_calls, ["running"])

    def test_a_request_left_from_before_a_restart_is_cleared_and_not_acted_on(self):
        pb.set_handled("update", 42)
        self.set_state(lambda s: s["update"].update(request=42))
        self.settle()
        self.assertEqual(self.runner_calls, [])
        self.assertIsNone(self.state()["update"]["request"])
        self.assertEqual(self.state()["update"]["status"], "idle")

    def test_a_different_request_after_a_restart_is_acted_on(self):
        pb.set_handled("update", 41)
        self.set_state(lambda s: s["update"].update(request=42))
        self.settle()
        self.assertEqual(self.runner_calls, ["running"])
        self.assertEqual(pb.load_handled()["update"], 42)

    def test_a_request_during_a_run_is_dropped_not_queued(self):
        self.set_state(lambda s: s["update"].update(status="running", at=self.now.timestamp(), request=7))
        self.settle()
        self.assertEqual(self.runner_calls, [])
        self.assertIsNone(self.state()["update"]["request"])
        self.assertEqual(self.state()["update"]["status"], "running")
        self.assertEqual(pb.load_handled(), {"update": 7})

    def test_a_request_right_after_a_run_waits_for_the_five_minute_gap(self):
        self.set_state(lambda s: s["update"].update(status="failed", at=self.now.timestamp() - 120, request=9))
        self.settle()
        self.assertEqual(self.runner_calls, [])
        self.assertEqual(self.state()["update"]["request"], 9, "kept, not lost")
        self.assertEqual(pb.load_handled(), {})
        self.advance(181)
        self.settle()
        self.assertEqual(self.runner_calls, ["running"])

    def test_a_runner_that_cannot_be_started_is_reported_as_failed(self):
        self.runner_ok = False
        self.set_state(lambda s: s["update"].update(request=1))
        with self.assertLogs("sinko", level="WARNING"):
            self.settle()
        u = self.state()["update"]
        self.assertEqual(u["status"], "failed")
        self.assertEqual(u["error"], "The update could not be started.")
        self.assertIsNone(u["rolledBack"], "nothing was installed and nothing was checked: not claimed either way")

    def test_a_runner_start_that_raises_is_reported_as_failed(self):
        self.runner_ok = OSError("no systemd")
        self.set_state(lambda s: s["update"].update(request=1))
        with self.assertLogs("sinko", level="WARNING"):
            self.settle()
        self.assertEqual(self.state()["update"]["status"], "failed")

    def test_a_runner_that_started_anyway_is_not_called_failed(self):
        self.runner_ok = False
        holder = pb.RunLock()
        self.assertTrue(holder.acquire())
        self.addCleanup(holder.release)
        self.set_state(lambda s: s["update"].update(request=1))
        with self.assertLogs("sinko", level="WARNING"):
            self.settle()
        self.assertEqual(self.state()["update"]["status"], "running")

    def test_a_request_the_page_replaces_in_the_meantime_is_left_for_the_next_tick(self):
        self.set_state(lambda s: s["update"].update(request=1))
        real = self.api.group_map
        calls = {"n": 0}

        def racing():
            calls["n"] += 1
            if calls["n"] == 2:                        # between the first read and the write
                self.set_state(lambda s: s["update"].update(request=2))
            return real()
        with mock.patch.object(self.api, "group_map", racing):
            self.pass_()
        self.assertEqual(self.runner_calls, [], "the first request must not be acted on with the second one pending")
        self.assertEqual(self.state()["update"]["request"], 2)
        self.assertEqual(pb.load_handled(), {}, "the marker was given back")
        self.settle()
        self.assertEqual(self.runner_calls, ["running"])
        self.assertEqual(pb.load_handled(), {"update": 2})

    def test_when_the_state_cannot_be_written_the_marker_is_given_back(self):
        self.set_state(lambda s: s["update"].update(request=1))
        with mock.patch.object(self.api, "put_group", side_effect=pb.ApiError(500, "boom")), \
                self.assertLogs("sinko", level="WARNING"):
            self.pass_()
        self.assertEqual(pb.load_handled(), {})
        self.assertEqual(self.runner_calls, [])
        self.settle()
        self.assertEqual(self.runner_calls, ["running"], "the request was not lost")

    def test_the_result_of_an_earlier_run_is_removed_before_the_runner_starts(self):
        pb.write_update_result("ok", "2.9.0", "3.0.0", None, 5.0)
        self.set_state(lambda s: s["update"].update(request=1))
        self.pass_()
        self.assertIsNone(pb.read_update_result())


class AutoUpdateTests(Fixture):
    def setUp(self):
        super().setUp()
        self.now = dt.datetime(2026, 9, 18, 3, 30)                 # inside the window
        self.set_state(lambda s: s["update"].update(auto=True, latest="3.1.0"))

    def test_inside_the_window_a_newer_release_is_installed(self):
        actions = self.pass_()
        self.assertIn("automatic update to 3.1.0", actions)
        u = self.state()["update"]
        self.assertEqual((u["status"], u["from"], u["to"]), ("running", "3.0.0", "3.1.0"))
        self.assertEqual(self.runner_calls, ["running"])
        self.assertEqual(pb.load_handled(), {}, "no request, so no marker")

    def test_window_edges(self):
        for hour, minute, expect in ((2, 59, False), (3, 0, True), (4, 59, True), (5, 0, False), (12, 0, False), (23, 30, False)):
            self.runner_calls.clear()
            self.set_state(lambda s: s["update"].update(status="idle", at=0))
            self.now = dt.datetime(2026, 9, 18, hour, minute)
            self.settle()
            self.assertEqual(bool(self.runner_calls), expect, "%02d:%02d" % (hour, minute))

    def test_not_when_the_parent_has_not_switched_it_on(self):
        self.set_state(lambda s: s["update"].update(auto=False))
        self.settle()
        self.assertEqual(self.runner_calls, [])

    def test_not_when_nothing_newer_is_known_or_it_is_already_installed(self):
        for latest in (None, "3.0.0", "2.9.0"):
            self.set_state(lambda s: s["update"].update(latest=latest))
            self.settle()
            self.assertEqual(self.runner_calls, [], latest)

    def test_not_while_another_run_is_going(self):
        self.set_state(lambda s: s["update"].update(status="running", at=self.now.timestamp() - 60))
        self.settle()
        self.assertEqual(self.runner_calls, [])

    def test_not_when_the_clock_cannot_be_trusted(self):
        self.clock_trusted = False
        self.settle()
        self.assertEqual(self.runner_calls, [])
        self.assertEqual(self.state()["update"]["status"], "idle")
        self.clock_trusted = True
        self.settle()
        self.assertEqual(self.runner_calls, ["running"])

    def test_a_release_that_failed_in_the_last_week_is_not_tried_again(self):
        self.set_state(lambda s: s["update"].update(status="failed", to="3.1.0", error="x",
                                                    at=self.now.timestamp() - 6 * 86400))
        self.settle()
        self.assertEqual(self.runner_calls, [])

    def test_after_a_week_it_is_tried_again(self):
        self.set_state(lambda s: s["update"].update(status="failed", to="3.1.0", error="x",
                                                    at=self.now.timestamp() - 7 * 86400 - 1))
        self.settle()
        self.assertEqual(self.runner_calls, ["running"])

    def test_a_failure_of_an_older_release_does_not_block_a_newer_one(self):
        self.set_state(lambda s: s["update"].update(status="failed", to="3.0.5", error="x", at=self.now.timestamp() - 3600))
        self.settle()
        self.assertEqual(self.runner_calls, ["running"])

    def test_a_rollback_by_hand_blocks_the_release_that_was_undone(self):
        # what `sinko rollback` leaves behind is an ordinary failed update of the release that was undone
        self.set_state(lambda s: s["update"].update(status="failed", to="3.1.0", error="Went back to 3.0.0 by hand.",
                                                    at=self.now.timestamp() - 3600))
        self.settle()
        self.assertEqual(self.runner_calls, [])

    def test_not_again_within_five_minutes_of_the_last_run(self):
        self.set_state(lambda s: s["update"].update(status="ok", at=self.now.timestamp() - 100))
        self.settle()
        self.assertEqual(self.runner_calls, [])

    def test_the_parent_can_still_update_by_hand_after_a_failure(self):
        self.set_state(lambda s: s["update"].update(status="failed", to="3.1.0", error="x", at=self.now.timestamp() - 3600,
                                                    request=77))
        self.settle()
        self.assertEqual(self.runner_calls, ["running"])


class ReconcileTests(Fixture):
    def running(self, **kw):
        fields = dict(status="running", **{"from": "3.0.0"})
        fields.update(to="3.1.0", at=self.now.timestamp() - 60, latest="3.1.0")
        fields.update(kw)
        self.set_state(lambda s: s["update"].update(fields))

    def test_an_ok_result_is_copied_into_the_state_and_the_file_removed(self):
        self.running()
        pb.write_update_result("ok", "3.0.0", "3.1.0", None, self.now.timestamp() - 5)
        with mock.patch.object(pb, "VERSION", "3.1.0"):
            actions = self.pass_()
        u = self.state()["update"]
        self.assertEqual((u["status"], u["from"], u["to"], u["error"]), ("ok", "3.0.0", "3.1.0", None))
        self.assertEqual(u["at"], self.now.timestamp() - 5)
        self.assertIsNone(u["latest"], "the release that is installed is no longer an update")
        self.assertIsNone(u["notes"])
        self.assertIsNone(pb.read_update_result())
        self.assertIn("update finished", actions)

    def test_a_failed_result_keeps_the_release_on_offer_and_shows_the_reason(self):
        self.running()
        pb.write_update_result("failed", "3.0.0", "3.1.0", "The installer stopped with an error.", self.now.timestamp() - 5)
        self.pass_()
        u = self.state()["update"]
        self.assertEqual((u["status"], u["error"], u["latest"]), ("failed", "The installer stopped with an error.", "3.1.0"))
        self.assertIsNone(pb.read_update_result())

    def test_a_result_that_says_nothing_to_do_clears_a_stale_offer(self):
        self.running(to=None)
        pb.write_update_result("ok", "3.0.0", "3.1.0", None, self.now.timestamp())
        self.pass_()
        self.assertIsNone(self.state()["update"]["latest"])

    def test_a_result_older_than_this_run_is_not_this_runs_result(self):
        self.running()
        pb.write_update_result("ok", "2.9.0", "3.0.0", None, self.now.timestamp() - 3600)
        self.pass_()
        self.assertEqual(self.state()["update"]["status"], "running")
        self.assertIsNotNone(pb.read_update_result(), "left alone")

    def test_a_result_without_a_running_update_is_ignored(self):
        pb.write_update_result("ok", "3.0.0", "3.1.0", None, self.now.timestamp())
        self.settle()
        self.assertEqual(self.state()["update"]["status"], "idle")

    def test_a_garbled_result_file_is_ignored(self):
        self.running()
        pb.ensure_state_dir()
        pb.atomic_write(pb.state_path("update-result.json"), "{not json")
        self.pass_()
        self.assertEqual(self.state()["update"]["status"], "running")

    def test_a_run_with_no_runner_for_over_three_minutes_is_looked_at_and_then_judged(self):
        # The box lost power in the middle of an update, or the runner was killed: nothing holds the update lock.
        # What the box looks like decides what is said (see the tests of that below).
        self.running(at=self.now.timestamp() - 4 * 60)
        self.recover_answer = {"ok": False, "detail": "the page files are not installed"}
        self.pass_()
        self.assertEqual(self.state()["update"]["status"], "running", "the check is running in the background")
        actions = self.pass_()
        u = self.state()["update"]
        self.assertEqual((u["status"], u["rolledBack"]), ("failed", False))
        self.assertEqual(u["error"], "The update did not finish, and the box does not pass its own check (the page files are not installed).")
        self.assertEqual(u["at"], self.now.timestamp())
        self.assertIn("update did not finish (the box was checked)", actions)
        self.assertEqual(len(self.recover_calls), 1)

    def test_a_run_that_is_still_alive_is_never_called_stuck(self):
        self.running(at=self.now.timestamp() - 3 * 3600)
        holder = pb.RunLock()
        self.assertTrue(holder.acquire())
        self.addCleanup(holder.release)
        self.pass_()
        self.assertEqual(self.state()["update"]["status"], "running")

    def test_a_young_run_is_left_alone(self):
        self.running(at=self.now.timestamp() - 170)
        writes = self.store.writes
        self.settle()
        self.assertEqual(self.state()["update"]["status"], "running")
        self.assertEqual(self.store.writes, writes)
        self.assertEqual(self.recover_calls, [])

    def test_the_result_arriving_after_a_scheduler_restart_is_still_picked_up(self):
        # The installer restarts this service: the new process has no memory of the run, only the state and the file.
        self.running()
        pb.write_update_result("ok", "3.0.0", "3.1.0", None, self.now.timestamp())
        fresh = pb.Maintenance(self.api, config_path=self.config_path, monotonic=lambda: self.mono, job_factory=InlineJob)
        with mock.patch.object(pb, "VERSION", "3.1.0"):
            fresh.run(self.now)
        self.assertEqual(self.state()["update"]["status"], "ok")

    def test_an_offer_that_is_installed_by_hand_is_forgotten(self):
        self.set_state(lambda s: s["update"].update(latest="3.1.0", notes="https://github.com/iret33/sinko/releases/tag/v3.1.0"))
        with mock.patch.object(pb, "VERSION", "3.1.0"):
            self.pass_()
        self.assertIsNone(self.state()["update"]["latest"])
        self.assertIsNone(self.state()["update"]["notes"])

    def test_an_error_text_from_the_result_is_capped(self):
        self.running()
        pb.ensure_state_dir()
        pb.atomic_write(pb.state_path("update-result.json"),
                        json.dumps({"status": "failed", "from": "3.0.0", "to": "3.1.0", "error": "e" * 900, "at": self.now.timestamp()}))
        self.pass_()
        self.assertEqual(len(self.state()["update"]["error"]), 200)


class RolledBackTests(Fixture):
    """update.rolledBack: from the runner's result into the state, and what the scheduler itself says when the runner is gone."""

    def running(self, **kw):
        fields = dict(status="running", **{"from": "3.0.0"})
        fields.update(to="3.1.0", at=self.now.timestamp() - 60, latest="3.1.0")
        fields.update(kw)
        self.set_state(lambda s: s["update"].update(fields))

    def test_the_runners_answer_is_copied_for_a_failure_and_dropped_for_a_success(self):
        for rolled, want in ((True, True), (False, False), (None, None)):
            self.running()
            pb.write_update_result("failed", "3.0.0", "3.1.0", "x", self.now.timestamp() - 5, rolled_back=rolled)
            self.pass_()
            self.assertIs(self.state()["update"]["rolledBack"], want, rolled)
        self.running(rolledBack=True)
        pb.write_update_result("ok", "3.0.0", "3.1.0", None, self.now.timestamp() - 5, rolled_back=True)
        with mock.patch.object(pb, "VERSION", "3.1.0"):
            self.pass_()
        self.assertEqual((self.state()["update"]["status"], self.state()["update"]["rolledBack"]), ("ok", None))

    def test_a_new_run_forgets_the_last_ones_answer(self):
        self.set_state(lambda s: s["update"].update(status="failed", rolledBack=True, at=0, request=3, latest="3.1.0"))
        self.pass_()
        self.assertEqual((self.state()["update"]["status"], self.state()["update"]["rolledBack"]), ("running", None))

    def test_a_failure_that_names_no_release_is_remembered_for_the_retry_rule_under_the_one_the_state_names(self):
        # No room on the card: the runner stops before it has asked which release, so its result has none. The state keeps
        # the one the scheduler started it for, and the retry rule only knows a failure by that.
        self.running()
        pb.write_update_result("failed", "3.0.0", None, "There is not enough free space on the box: 150 MB are free and "
                               "more than 200 MB are needed. Nothing on the box was changed.",
                               self.now.timestamp() - 5, rolled_back=True, transient=True)
        self.pass_()
        u = self.state()["update"]
        self.assertEqual((u["status"], u["to"], u["rolledBack"]), ("failed", "3.1.0", True))
        self.assertTrue(pb.failure_is_transient(u), "tried again within the hour, not after a week")

    def test_a_result_that_says_the_failure_was_the_network_is_remembered_for_the_retry_rule(self):
        self.running()
        pb.write_update_result("failed", "3.0.0", "3.1.0", "The server answered HTTP 502 for sinko.tar.gz.",
                               self.now.timestamp() - 5, rolled_back=True, transient=True)
        self.pass_()
        self.assertTrue(pb.failure_is_transient(self.state()["update"]))
        self.running()
        pb.write_update_result("failed", "3.0.0", "3.1.0", "The installer stopped with an error (exit status 1).",
                               self.now.timestamp() - 4, rolled_back=True, transient=False)
        self.pass_()
        self.assertFalse(pb.failure_is_transient(self.state()["update"]))

    def gone(self, **kw):
        self.running(at=self.now.timestamp() - 5 * 60, **kw)

    def test_the_new_version_in_place_and_passing_its_check_means_the_update_did_finish(self):
        # The box lost power during the 90 seconds of the check: the update was fine, and it must not be called failed.
        self.gone()
        with mock.patch.object(pb, "VERSION", "3.1.0"):
            self.settle()
        u = self.state()["update"]
        self.assertEqual((u["status"], u["error"], u["rolledBack"], u["latest"]), ("ok", None, None, None))
        self.assertEqual(u["at"], self.now.timestamp())

    def test_the_old_version_still_working_is_said_with_rolled_back_true(self):
        self.gone()
        self.settle()
        u = self.state()["update"]
        self.assertEqual((u["status"], u["rolledBack"]), ("failed", True))
        self.assertEqual(u["error"], "The update did not finish. This box still works with version 3.0.0.")
        self.assertEqual(u["latest"], "3.1.0", "the release stays on offer")

    def test_a_box_that_fails_its_check_is_said_to_have_nothing_to_go_back_to(self):
        self.gone()
        self.recover_answer = {"ok": False, "detail": "scheduler service is not running"}
        self.settle()
        u = self.state()["update"]
        self.assertEqual((u["status"], u["rolledBack"]), ("failed", False))
        self.assertIn("scheduler service is not running", u["error"])
        self.assertLessEqual(len(u["error"]), 200)

    def test_a_check_that_cannot_run_is_a_failure_that_says_so(self):
        self.gone()
        self.recover_answer = OSError("no pihole-FTL")
        with self.assertLogs("sinko", level="WARNING"):
            self.settle()
        u = self.state()["update"]
        self.assertEqual((u["status"], u["rolledBack"]), ("failed", False))

    def test_nothing_is_decided_while_a_runner_is_alive_or_the_clock_is_not_believed(self):
        self.gone()
        holder = pb.RunLock()
        self.assertTrue(holder.acquire())
        self.settle()
        self.assertEqual((self.state()["update"]["status"], self.recover_calls), ("running", []))
        holder.release()
        self.clock_trusted = False
        self.settle()
        self.assertEqual((self.state()["update"]["status"], self.recover_calls), ("running", []))
        self.clock_trusted = True
        self.settle()
        self.assertEqual(self.state()["update"]["status"], "failed")

    def test_a_runner_that_took_the_lock_while_the_check_was_running_wins(self):
        self.gone()
        holder = pb.RunLock()
        self.pass_()                                            # the check ran; its answer is collected on the next pass
        self.assertTrue(holder.acquire())
        self.addCleanup(holder.release)
        self.pass_()
        self.assertEqual(self.state()["update"]["status"], "running")

    def test_a_result_that_arrives_while_the_check_runs_is_not_overwritten(self):
        self.gone()
        self.pass_()
        pb.write_update_result("failed", "3.0.0", "3.1.0", "The installer stopped with an error (exit status 1).",
                               self.now.timestamp(), rolled_back=True)
        self.set_state(lambda s: s["update"].update(at=self.now.timestamp() - 1))        # the page or a new run moved on
        self.pass_()
        self.assertNotIn("This box still works", self.state()["update"]["error"] or "")

    def test_the_real_recover_check_uses_the_selfcheck_without_asking_whether_the_scheduler_runs(self):
        seen = {}

        def round_(catalog, scheduler=None):
            seen["scheduler"] = scheduler()
            return [(True, "a"), (False, "the page is old"), (False, "second")]
        with mock.patch.object(pb, "load_catalog_safely", return_value={"services": []}), \
                mock.patch.object(pb, "selfcheck_round", round_):
            self.assertEqual(REAL_RECOVER_CHECK(), {"ok": False, "detail": "the page is old"})
        self.assertEqual(seen["scheduler"][0], True)
        with mock.patch.object(pb, "load_catalog_safely", side_effect=pb.CatalogError("missing list file")):
            result = REAL_RECOVER_CHECK()
        self.assertFalse(result["ok"])
        self.assertIn("missing list file", result["detail"])
        with mock.patch.object(pb, "load_catalog_safely", return_value={"services": []}), \
                mock.patch.object(pb, "selfcheck_round", lambda catalog, scheduler=None: [(True, "a")]):
            self.assertEqual(REAL_RECOVER_CHECK(), {"ok": True, "detail": ""})


class RetryPolicyTests(Fixture):
    """A failed check, or a failed update that was only the network, is not given up on for a day or a week."""

    def test_a_failed_check_is_tried_again_in_half_an_hour_to_an_hour_not_tomorrow(self):
        self.found = pb.UpdateError("The server answered HTTP 403 for latest.")
        self.advance(121)
        with self.assertLogs("sinko", level="WARNING"):
            self.settle()
        self.assertEqual(len(self.check_calls), 1)
        self.assertIn((0, 1800), self.jitter_calls)
        self.advance(1799)
        self.settle()
        self.assertEqual(len(self.check_calls), 1, "not before half an hour")
        self.found = self.release("3.1.0")
        self.advance(2)
        self.settle()
        self.assertEqual(len(self.check_calls), 2)
        self.assertEqual(self.state()["update"]["latest"], "3.1.0", "found a day sooner than before")

    def test_the_retry_is_spread_so_that_boxes_behind_one_address_do_not_ask_together(self):
        self.found = pb.UpdateError("offline")
        self.m.jitter = lambda low, high: high
        self.advance(121)
        with self.assertLogs("sinko", level="WARNING"):
            self.settle()
        self.advance(3599)
        self.settle()
        self.assertEqual(len(self.check_calls), 1)
        self.advance(2)
        with self.assertLogs("sinko", level="WARNING"):
            self.settle()
        self.assertEqual(len(self.check_calls), 2)

    def test_a_good_check_still_waits_a_day(self):
        self.found = self.release("3.1.0")
        self.advance(121)
        self.settle()
        self.advance(3 * 3600)
        self.settle()
        self.assertEqual(len(self.check_calls), 1)

    def failed(self, transient, age):
        self.set_state(lambda s: s["update"].update(auto=True, latest="3.1.0", status="failed", to="3.1.0", error="x",
                                                    at=self.now.timestamp() - age))
        pb.note_failure({"at": self.now.timestamp() - age, "to": "3.1.0", "transient": transient})

    def test_a_failure_of_the_network_is_tried_again_the_same_night_after_half_an_hour(self):
        self.now = dt.datetime(2026, 9, 18, 3, 30)
        self.failed(True, 29 * 60)
        self.settle()
        self.assertEqual(self.runner_calls, [])
        self.advance(121)
        self.settle()
        self.assertEqual(self.runner_calls, ["running"])

    def test_a_failure_of_the_release_itself_keeps_the_week(self):
        self.now = dt.datetime(2026, 9, 18, 3, 30)
        self.failed(False, 3 * 3600)
        self.settle()
        self.assertEqual(self.runner_calls, [])

    def test_the_note_belongs_to_one_run_only(self):
        self.now = dt.datetime(2026, 9, 18, 3, 30)
        self.set_state(lambda s: s["update"].update(auto=True, latest="3.1.0", status="failed", to="3.1.0", error="x",
                                                    at=self.now.timestamp() - 3600))
        pb.note_failure({"at": 12345.0, "to": "3.1.0", "transient": True})          # about an older run
        self.settle()
        self.assertEqual(self.runner_calls, [])

    def test_the_parent_can_always_try_again_by_hand(self):
        self.failed(True, 60)
        self.set_state(lambda s: s["update"].update(request=9))
        self.advance(300)
        self.settle()
        self.assertEqual(self.runner_calls, ["running"])


class CheckTests(Fixture):
    def test_the_first_check_is_two_minutes_after_start_and_then_daily(self):
        self.found = self.release("3.1.0")
        self.advance(60)
        self.settle()
        self.assertEqual(self.check_calls, [], "too early")
        self.advance(61)
        self.settle()
        self.assertEqual(len(self.check_calls), 1)
        u = self.state()["update"]
        self.assertEqual((u["latest"], u["notes"], u["checked"]), ("3.1.0", self.found["notes"], self.now.timestamp()))
        self.advance(23 * 3600)
        self.settle()
        self.assertEqual(len(self.check_calls), 1)
        self.advance(3600)
        self.settle()
        self.assertEqual(len(self.check_calls), 2)

    def test_an_unchanged_answer_is_not_written_again_within_a_day_but_is_after(self):
        self.found = self.release("3.1.0")
        self.advance(121)
        self.settle()
        writes = self.store.writes
        self.advance(self.m.CHECK_EVERY - 3600)        # the next check is due, the answer is the same, checked is < a day old
        self.m._next_check = self.mono
        self.settle()
        self.assertEqual(len(self.check_calls), 2)
        self.assertEqual(self.store.writes, writes, "same answer, checked less than a day ago")
        self.advance(3601)
        self.m._next_check = self.mono
        self.settle()
        self.assertEqual(self.state()["update"]["checked"], self.now.timestamp(), "older than a day: refreshed")

    def test_no_newer_release_clears_an_old_offer(self):
        self.set_state(lambda s: s["update"].update(latest="3.1.0", notes="https://github.com/iret33/sinko/releases/tag/v3.1.0"))
        self.found = None
        self.advance(121)
        self.settle()
        u = self.state()["update"]
        self.assertEqual((u["latest"], u["notes"], u["checked"]), (None, None, self.now.timestamp()))

    def test_a_failed_check_keeps_the_old_answer_and_says_little(self):
        self.set_state(lambda s: s["update"].update(latest="3.1.0", checked=123.0))
        self.found = pb.UpdateError("offline")
        self.advance(121)
        writes = self.store.writes
        with self.assertLogs("sinko", level="WARNING") as logs:
            self.settle()
        self.assertEqual(len(logs.records), 1)
        u = self.state()["update"]
        self.assertEqual((u["latest"], u["checked"]), ("3.1.0", 123.0))
        self.assertEqual(self.store.writes, writes)

    def test_a_check_request_checks_at_once_and_always_records_the_time(self):
        self.found = self.release("3.1.0")
        self.set_state(lambda s: s["update"].update(latest="3.1.0", notes=self.found["notes"], checked=self.now.timestamp() - 5))
        self.set_state(lambda s: s["update"].update(checkRequest=555))
        actions = self.settle()
        self.assertIn("check requested by the page", actions)
        self.assertEqual(len(self.check_calls), 1)
        u = self.state()["update"]
        self.assertIsNone(u["checkRequest"])
        self.assertEqual(u["checked"], self.now.timestamp(), "the page can see that the box looked")
        self.assertEqual(pb.load_handled(), {"check": 555})

    def test_a_check_request_is_acted_on_once(self):
        self.found = self.release("3.1.0")
        self.set_state(lambda s: s["update"].update(checkRequest=555))
        self.settle()
        self.advance(15)
        self.settle()
        self.assertEqual(len(self.check_calls), 1)

    def test_a_stale_check_request_after_a_restart_is_cleared_without_checking(self):
        pb.set_handled("check", 555)
        self.set_state(lambda s: s["update"].update(checkRequest=555))
        self.settle()
        self.assertEqual(self.check_calls, [])
        self.assertIsNone(self.state()["update"]["checkRequest"])

    def test_a_check_request_while_a_check_is_running_is_not_lost(self):
        self.found = self.release("3.1.0")
        self.advance(121)
        self.pass_()                                   # the periodic check is running (its result is not collected yet)
        self.set_state(lambda s: s["update"].update(checkRequest=9))
        self.pass_()                                   # collects the first, and the request finds the job busy or free
        self.pass_()
        self.pass_()
        self.assertEqual(len(self.check_calls), 2)
        self.assertIsNone(self.state()["update"]["checkRequest"])

    def test_a_developer_box_never_checks(self):
        with open(self.config_path, "w") as fh:
            fh.write(CONFIG.replace("SINKO_REF=latest", "SINKO_REF=master"))
        self.set_state(lambda s: s["update"].update(checkRequest=1))
        self.advance(200)
        self.settle()
        self.assertEqual(self.check_calls, [])
        self.assertIsNone(self.state()["update"]["checkRequest"], "the request is answered by doing nothing, not kept forever")

    def test_a_pinned_box_checks_for_its_pin(self):
        with open(self.config_path, "w") as fh:
            fh.write(CONFIG.replace("SINKO_REF=latest", "SINKO_REF=v3.2.0"))
        self.advance(121)
        self.settle()
        self.assertEqual(len(self.check_calls), 1)
        self.assertEqual(self.check_calls[0]["SINKO_REF"], "v3.2.0")

    def test_no_periodic_check_while_the_clock_cannot_be_trusted(self):
        self.clock_trusted = False
        self.advance(500)
        self.settle()
        self.assertEqual(self.check_calls, [])
        self.clock_trusted = True
        self.settle()
        self.assertEqual(len(self.check_calls), 1)

    def test_a_check_request_does_not_wait_for_the_clock(self):
        self.clock_trusted = False
        self.set_state(lambda s: s["update"].update(checkRequest=3))
        self.settle()
        self.assertEqual(len(self.check_calls), 1, "the parent asked")


class PowerTests(Fixture):
    def test_a_reboot_request_is_stored_and_cleared_before_the_reboot_is_issued(self):
        marker = self.stamp()
        self.set_state(lambda s: s["power"].update(request=marker, action="reboot"))
        actions = self.pass_()
        self.assertIn("power request: reboot", actions)
        state_then, handled_then = self.seen_at_call["power"]
        self.assertEqual(state_then["power"], {"request": None, "action": None}, "cleared BEFORE the reboot")
        self.assertEqual(handled_then, {"power": marker}, "stored BEFORE the reboot")
        self.assertEqual(self.power_calls, ["reboot"])

    def test_poweroff(self):
        self.set_state(lambda s: s["power"].update(request=2, action="poweroff"))
        self.settle()
        self.assertEqual(self.power_calls, ["poweroff"])

    def test_the_box_coming_back_does_not_reboot_again(self):
        self.set_state(lambda s: s["power"].update(request=77, action="reboot"))
        self.settle()
        self.assertEqual(self.power_calls, ["reboot"])
        # The clearing was cut off by the reboot itself: the marker is still in the state when the box is back.
        self.set_state(lambda s: s["power"].update(request=77, action="reboot"))
        fresh = pb.Maintenance(self.api, config_path=self.config_path, monotonic=lambda: self.mono, job_factory=InlineJob,
                               power=self.fake_power)
        fresh.run(self.now)
        fresh.run(self.now)
        self.assertEqual(self.power_calls, ["reboot"], "not twice")
        self.assertEqual(self.state()["power"], {"request": None, "action": None})

    def test_a_new_request_after_the_reboot_is_acted_on(self):
        self.set_state(lambda s: s["power"].update(request=77, action="reboot"))
        self.settle()
        self.set_state(lambda s: s["power"].update(request=78, action="poweroff"))
        self.settle()
        self.assertEqual(self.power_calls, ["reboot", "poweroff"])

    def test_a_power_request_during_an_update_is_dropped_not_held(self):
        # Held in the shared state it would survive a power cycle (the parent is told to unplug a box that is being
        # updated...) and fire at a moment nobody chose. The page disables the buttons during an update and withdraws a
        # request nobody answered; the scheduler never keeps one.
        self.set_state(lambda s: s["update"].update(status="running", at=self.now.timestamp()))
        self.set_state(lambda s: s["power"].update(request=5, action="reboot"))
        actions = self.settle()
        self.assertEqual(self.power_calls, [])
        self.assertIn("power request ignored: an update is running", actions)
        self.assertEqual(self.state()["power"], {"request": None, "action": None}, "cleared, not kept")
        self.assertEqual(pb.load_handled(), {"power": 5}, "and never to be acted on")
        self.set_state(lambda s: s["update"].update(status="ok"))
        self.advance(15)
        self.settle()
        self.assertEqual(self.power_calls, [], "not even once the update is over")

    def test_a_request_the_box_already_dropped_is_not_acted_on_after_a_restart_either(self):
        pb.set_handled("power", 5)
        self.set_state(lambda s: s["power"].update(request=5, action="reboot"))
        self.settle()
        self.assertEqual(self.power_calls, [])
        self.assertIsNone(self.state()["power"]["request"])

    def test_a_new_request_after_the_update_is_acted_on(self):
        self.set_state(lambda s: s["update"].update(status="running", at=self.now.timestamp()))
        self.set_state(lambda s: s["power"].update(request=5, action="reboot"))
        self.settle()
        self.set_state(lambda s: s["update"].update(status="ok"))
        self.set_state(lambda s: s["power"].update(request=6, action="reboot"))
        self.settle()
        self.assertEqual(self.power_calls, ["reboot"])

    def test_a_failing_command_is_logged_and_not_retried(self):
        self.power_error = subprocess.CalledProcessError(1, "systemctl")
        self.set_state(lambda s: s["power"].update(request=5, action="reboot"))
        with self.assertLogs("sinko", level="WARNING"):
            self.settle()
        self.settle()
        self.assertEqual(self.power_calls, ["reboot"])
        self.assertEqual(self.state()["power"]["request"], None)

    def test_a_page_state_without_an_action_is_not_a_request(self):
        self.set_state(lambda s: s["power"].update(request=5, action=None))
        self.settle()
        self.assertEqual(self.power_calls, [])

    def test_when_the_state_cannot_be_written_no_reboot_happens_and_the_request_is_kept(self):
        self.set_state(lambda s: s["power"].update(request=5, action="reboot"))
        with mock.patch.object(self.api, "put_group", side_effect=pb.ApiError(500, "boom")), \
                self.assertLogs("sinko", level="WARNING"):
            self.settle()
        self.assertEqual(self.power_calls, [])
        self.assertEqual(pb.load_handled(), {})
        self.settle()
        self.assertEqual(self.power_calls, ["reboot"])


class StaleRequestTests(Fixture):
    """A request is for now (architecture.md, "Request protocol"): the page writes its time on the box's clock, and the scheduler does not
    act on one that is much older (or from the future). The case it exists for is a restored backup, whose state holds the requests that
    were waiting when it was made: a request this box never handled looks new to the "different from handled" rule."""

    def test_old_requests_of_every_kind_are_dropped_and_never_acted_on(self):
        old = self.stamp(3600)
        self.found = self.release("3.1.0")
        self.set_state(lambda s: s["update"].update(request=old, checkRequest=old + 1, latest="3.1.0"))
        self.set_state(lambda s: s["power"].update(request=old + 2, action="reboot"))
        with self.assertLogs("sinko", level="INFO") as logs:
            self.settle()
        messages = [r.getMessage() for r in logs.records]
        for kind in ("update", "check", "power"):
            self.assertIn("%s request ignored: it is too old to be a parent's" % kind, messages)
        self.assertEqual((self.runner_calls, self.check_calls, self.power_calls), ([], [], []))
        u = self.state()["update"]
        self.assertEqual((u["request"], u["checkRequest"], self.state()["power"], u["status"]), (None, None, {"request": None, "action": None}, "idle"),
                         "taken out of the state, not kept for later")
        self.assertEqual(pb.load_handled(), {}, "the memory of what was handled is left as it was")
        self.set_state(lambda s: s["update"].update(request=old))
        self.settle()
        self.assertEqual((self.runner_calls, self.state()["update"]["request"]), ([], None), "and putting one back changes nothing")

    def test_a_request_made_a_moment_ago_is_acted_on_and_so_is_the_next_one_after_an_old_one(self):
        self.found = self.release("3.1.0")
        self.set_state(lambda s: s["update"].update(request=self.stamp(7200), latest="3.1.0"))
        self.settle()
        self.assertEqual(self.runner_calls, [])
        self.set_state(lambda s: s["update"].update(request=self.stamp(20), checkRequest=self.stamp(25)))
        self.settle()
        self.assertEqual(self.runner_calls, ["running"])
        self.assertEqual(len(self.check_calls), 1)
        self.set_state(lambda s: s["update"].update(status="ok"))
        self.set_state(lambda s: s["power"].update(request=self.stamp(10), action="poweroff"))
        self.settle()
        self.assertEqual(self.power_calls, ["poweroff"])

    def test_a_request_from_the_future_is_dropped_too(self):
        self.set_state(lambda s: s["power"].update(request=self.stamp(-3600), action="reboot"))
        self.settle()
        self.assertEqual(self.power_calls, [])
        self.assertEqual(self.state()["power"], {"request": None, "action": None})

    def test_the_limit_is_fifteen_minutes_either_way(self):
        for index, (seconds, acted) in enumerate(((14 * 60, True), (16 * 60, False), (-14 * 60, True), (-16 * 60, False))):
            self.power_calls.clear()
            self.set_state(lambda s: s["power"].update(request=self.stamp(seconds) + index, action="reboot"))      # a different marker every time
            self.settle()
            self.assertEqual(bool(self.power_calls), acted, seconds)

    def test_a_marker_that_is_not_a_time_is_opaque_and_acted_on_once(self):
        for marker in ("abc", 42, 99999999999):
            self.power_calls.clear()
            self.set_state(lambda s: s["power"].update(request=marker, action="reboot"))
            self.settle()
            self.assertEqual(self.power_calls, ["reboot"], repr(marker))
            self.assertEqual(self.state()["power"], {"request": None, "action": None})

    def test_a_clock_that_is_not_believed_judges_nothing(self):
        # No real-time clock, and the time server has not answered yet: the time of the request cannot be compared with anything.
        self.clock_trusted = False
        self.set_state(lambda s: s["power"].update(request=self.stamp(3600), action="reboot"))
        self.settle()
        self.assertEqual(self.power_calls, ["reboot"], "the parent's request is obeyed, as before")


class AddressTests(Fixture):
    def test_the_first_look_is_after_half_a_minute_then_every_five(self):
        self.ip = "192.168.1.77"
        self.advance(20)
        self.settle()
        self.assertEqual(self.address_calls, [])
        self.advance(11)
        self.settle()
        self.assertEqual(len(self.address_calls), 1)
        self.advance(200)
        self.settle()
        self.assertEqual(len(self.address_calls), 1, "not before five minutes")

    def test_a_changed_address_is_applied_with_the_configured_name(self):
        self.ip = "192.168.1.77"
        self.advance(31)
        actions = self.settle()
        self.assertIn("address changed from 192.168.1.5 to 192.168.1.77", actions)
        ip, conf, path = self.address_calls[0]
        self.assertEqual((ip, conf["SINKO_HOSTNAME"], path), ("192.168.1.77", "family.lan", self.config_path))

    def test_the_same_address_changes_nothing(self):
        self.advance(31)
        self.settle()
        self.assertEqual(self.address_calls, [])

    def test_no_route_or_a_useless_address_changes_nothing(self):
        for ip in (None, "", "127.0.0.1", "169.254.3.4", "0.0.0.0", "224.0.0.1", "not an ip", "fe80::1"):
            self.ip = ip
            self.advance(301)
            self.settle()
        self.assertEqual(self.address_calls, [])

    def test_a_box_without_a_remembered_address_is_left_alone(self):
        with open(self.config_path, "w") as fh:
            fh.write("SINKO_HOSTNAME=family.lan\n")
        self.ip = "192.168.1.77"
        self.advance(31)
        self.settle()
        self.assertEqual(self.address_calls, [])
        os.unlink(self.config_path)
        self.advance(301)
        self.settle()
        self.assertEqual(self.address_calls, [])

    def test_a_failure_is_logged_and_retried_five_minutes_later(self):
        self.ip = "192.168.1.77"
        self.address_error = RuntimeError("pihole-FTL did not answer")
        self.advance(31)
        with self.assertLogs("sinko", level="WARNING") as logs:
            self.settle()
            self.assertEqual(len(self.address_calls), 1)
            self.advance(100)
            self.settle()
            self.assertEqual(len(self.address_calls), 1)
            self.advance(201)
            self.settle()
        self.assertEqual(len(self.address_calls), 2)
        self.assertEqual(len(logs.records), 1)
        self.address_error = None
        self.advance(301)
        self.settle()
        self.assertEqual(len(self.address_calls), 3)

    def test_a_slow_apply_does_not_start_a_second_one(self):
        gate = threading.Event()
        started = []

        def slow(ip, conf, path):
            started.append(ip)
            gate.wait(5)
        m = pb.Maintenance(self.api, config_path=self.config_path, monotonic=lambda: self.mono, default_ip=lambda: "192.168.1.77",
                           apply_address=slow, check=lambda conf: None)
        self.addCleanup(gate.set)
        self.advance(31)
        began = time.monotonic()
        m.run(self.now)
        self.assertLess(time.monotonic() - began, 1, "the tick does not wait for pihole-FTL")
        for _ in range(100):
            if started:
                break
            time.sleep(0.01)
        self.advance(301)
        m.run(self.now)
        time.sleep(0.05)
        self.assertEqual(started, ["192.168.1.77"])


class ApplyAddressTests(unittest.TestCase):
    """The real apply: what `sinko configure --ip` does to Pi-hole, and the one line it rewrites in the settings."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = os.path.join(tmp.name, "config")
        with open(self.path, "w") as fh:
            fh.write("# sinko settings\nSINKO_HOSTNAME=family.lan\nSINKO_REPO=https://github.com/iret33/sinko.git\n"
                     "SINKO_IP=192.168.1.5\nSINKO_REF=latest\n")
        os.chmod(self.path, 0o644)
        self.calls = []
        self.hosts = "[ 192.168.1.5 family.lan, 192.168.1.9 nas.lan ]"
        for target, fake in (("ftl_config", self.fake_get), ("ftl_set_config", self.fake_set)):
            patch = mock.patch.object(pb, target, fake)
            patch.start()
            self.addCleanup(patch.stop)

    def fake_get(self, key):
        return self.hosts if key == "dns.hosts" else "true"

    def fake_set(self, key, value):
        self.calls.append((key, value))

    def test_the_local_name_follows_the_new_address_and_the_setting_is_rewritten(self):
        pb.apply_new_address("10.0.0.7", {"SINKO_HOSTNAME": "family.lan"}, self.path)
        hosts = [json.loads(v) for k, v in self.calls if k == "dns.hosts"]
        self.assertEqual(hosts, [["192.168.1.9 nas.lan", "10.0.0.7 family.lan"]])
        with open(self.path) as fh:
            text = fh.read()
        self.assertEqual(text, "# sinko settings\nSINKO_HOSTNAME=family.lan\nSINKO_REPO=https://github.com/iret33/sinko.git\n"
                               "SINKO_IP=10.0.0.7\nSINKO_REF=latest\n")
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o644)

    def test_a_box_without_a_local_name_only_rewrites_its_setting(self):
        pb.apply_new_address("10.0.0.7", {"SINKO_HOSTNAME": ""}, self.path)
        self.assertEqual([k for k, _ in self.calls if k == "dns.hosts"], [])
        with open(self.path) as fh:
            self.assertIn("SINKO_IP=10.0.0.7\n", fh.read())

    def test_write_config_value_adds_a_missing_key_and_collapses_a_repeated_one(self):
        with open(self.path, "w") as fh:
            fh.write("A=1\nSINKO_IP=1.1.1.1\n#SINKO_IP=2.2.2.2\nSINKO_IP=3.3.3.3\n")
        pb.write_config_value("SINKO_IP", "10.0.0.7", self.path)
        with open(self.path) as fh:
            self.assertEqual(fh.read(), "A=1\nSINKO_IP=10.0.0.7\n#SINKO_IP=2.2.2.2\n")
        pb.write_config_value("NEW_KEY", "x", self.path)
        with open(self.path) as fh:
            self.assertTrue(fh.read().endswith("NEW_KEY=x\n"))

    def test_write_config_value_refuses_anything_a_shell_would_interpret(self):
        for bad in ("a b", "x;y", "$(id)", "", "a\nb", "'q'"):
            with self.assertRaises(ValueError, msg=repr(bad)):
                pb.write_config_value("SINKO_IP", bad, self.path)

    def test_a_failing_pihole_leaves_the_setting_alone(self):
        with mock.patch.object(pb, "ftl_set_config", side_effect=subprocess.CalledProcessError(1, "pihole-FTL")):
            with self.assertRaises(subprocess.CalledProcessError):
                pb.apply_new_address("10.0.0.7", {"SINKO_HOSTNAME": "family.lan"}, self.path)
        with open(self.path) as fh:
            self.assertIn("SINKO_IP=192.168.1.5\n", fh.read(), "retried later from the old value")

    def test_valid_lan_addresses(self):
        for good in ("192.168.1.5", "10.1.2.3", "172.16.0.9", "100.64.0.2"):
            self.assertTrue(pb.valid_lan_ip(good), good)
        for bad in ("127.0.0.1", "169.254.1.1", "0.0.0.0", "224.0.0.1", "255.255.255.255", "::1", "fe80::1", "x", None, "1.2.3"):
            self.assertFalse(pb.valid_lan_ip(bad), bad)

    def test_the_address_comes_from_the_kernels_choice_towards_the_router(self):
        class FakeSocket:
            def __init__(self, *a):
                self.connected = None

            def settimeout(self, t):
                pass

            def connect(self, addr):
                self.connected = addr
                FakeSocket.last = addr

            def getsockname(self):
                return ("192.168.1.77", 40000)

            def close(self):
                pass
        with mock.patch.object(pb.SystemProbe, "default_gateway", return_value="192.168.1.1"), \
                mock.patch.object(pb.socket, "socket", FakeSocket):
            self.assertEqual(pb.default_route_ipv4(), "192.168.1.77")
        self.assertEqual(FakeSocket.last, ("192.168.1.1", 9))
        with mock.patch.object(pb.SystemProbe, "default_gateway", return_value=None):
            self.assertIsNone(pb.default_route_ipv4())
        with mock.patch.object(pb.SystemProbe, "default_gateway", return_value="192.168.1.1"), \
                mock.patch.object(pb.socket, "socket", side_effect=OSError("no network")):
            with self.assertRaises(OSError):
                pb.default_route_ipv4()

    def test_a_loopback_answer_is_not_an_address(self):
        route = os.path.join(os.path.dirname(self.path), "route")
        with open(route, "w") as fh:
            fh.write("Iface\tDestination\tGateway\tFlags\tRefCnt\tUse\tMetric\tMask\tMTU\tWindow\tIRTT\n"
                     "lo\t00000000\t0100007F\t0003\t0\t0\t0\t00000000\t0\t0\t0\n")
        with mock.patch.dict(os.environ, {"SINKO_ROUTE_FILE": route}):
            self.assertIsNone(pb.default_route_ipv4())


class RunnerStartTests(unittest.TestCase):
    """How the update runner is started so that it survives the installer restarting the scheduler."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        env = mock.patch.dict(os.environ, {"SINKO_STATE_DIR": os.path.join(tmp.name, "state"), "SINKO_RELEASE_BASE": "http://x/r",
                                           "HOME": "/root", "NOT_SINKO": "1"})
        env.start()
        self.addCleanup(env.stop)

    def test_it_runs_as_its_own_transient_unit_with_the_sinko_environment(self):
        with mock.patch.object(pb.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "", "")) as run, \
                mock.patch.object(pb.subprocess, "Popen") as popen:
            self.assertTrue(pb.start_update_runner())
        popen.assert_not_called()
        cmd = run.call_args[0][0]
        self.assertEqual(cmd[0], "systemd-run")
        self.assertEqual(cmd[cmd.index("--unit") + 1], "sinko-update")
        self.assertIn("--collect", cmd)
        self.assertIn("--setenv=SINKO_RELEASE_BASE=http://x/r", cmd)
        self.assertFalse([c for c in cmd if "NOT_SINKO" in c or c.startswith("--setenv=HOME")])
        self.assertEqual(cmd[-3:], ["update", "--yes", "--from-panel"])
        self.assertTrue(cmd[cmd.index("--") + 2].endswith("sinko"), "python, then the program")
        self.assertLessEqual(run.call_args[1]["timeout"], 15)

    def test_nothing_that_looks_like_a_secret_is_put_on_the_command_line(self):
        with mock.patch.dict(os.environ, {"SINKO_PASSWORD": "hunter2", "SINKO_API_TOKEN": "t0k3n", "SINKO_SECRET_X": "s",
                                          "SINKO_SSH_KEY": "k", "SINKO_STATE_DIR": "/var/lib/sinko"}):
            with mock.patch.object(pb.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "", "")) as run:
                pb.start_update_runner()
        text = " ".join(run.call_args[0][0])
        for secret in ("hunter2", "t0k3n", "SINKO_SECRET_X", "SINKO_SSH_KEY"):
            self.assertNotIn(secret, text)
        self.assertIn("--setenv=SINKO_STATE_DIR=/var/lib/sinko", text)

    def test_when_systemd_run_fails_it_falls_back_to_a_detached_process(self):
        with mock.patch.object(pb.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, "", "no bus")), \
                mock.patch.object(pb, "unit_active", return_value=False), \
                mock.patch.object(pb.subprocess, "Popen") as popen, self.assertLogs("sinko", level="WARNING"):
            self.assertTrue(pb.start_update_runner())
        kwargs = popen.call_args[1]
        self.assertTrue(kwargs["start_new_session"])
        self.assertEqual(popen.call_args[0][0][-3:], ["update", "--yes", "--from-panel"])
        self.assertEqual(stat.S_IMODE(os.stat(pb.state_path("update.log")).st_mode), 0o600)

    def test_when_systemd_run_is_missing_it_falls_back_too(self):
        with mock.patch.object(pb.subprocess, "run", side_effect=FileNotFoundError("systemd-run")), \
                mock.patch.object(pb, "unit_active", return_value=False), \
                mock.patch.object(pb.subprocess, "Popen") as popen, self.assertLogs("sinko", level="WARNING"):
            self.assertTrue(pb.start_update_runner())
        popen.assert_called_once()

    def test_a_runner_that_is_already_going_is_not_started_twice(self):
        with mock.patch.object(pb.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, "", "already exists")), \
                mock.patch.object(pb, "unit_active", return_value=True), mock.patch.object(pb.subprocess, "Popen") as popen:
            self.assertTrue(pb.start_update_runner())
        popen.assert_not_called()

    def test_the_repair_runs_as_a_unit_of_its_own_too_and_falls_back_to_its_own_log(self):
        with mock.patch.object(pb.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "", "")) as run, \
                mock.patch.object(pb.subprocess, "Popen") as popen:
            self.assertTrue(pb.start_repair_runner())
        popen.assert_not_called()
        cmd = run.call_args[0][0]
        self.assertEqual(cmd[cmd.index("--unit") + 1], "sinko-repair", "not the update's unit: both can be told apart and neither blocks the other's name")
        self.assertIn("--description=Sinko repair", cmd)
        self.assertEqual(cmd[-1:], ["repair"])
        with mock.patch.object(pb.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, "", "no bus")), \
                mock.patch.object(pb, "unit_active", return_value=False) as active, \
                mock.patch.object(pb.subprocess, "Popen") as popen, self.assertLogs("sinko", level="WARNING") as logs:
            self.assertTrue(pb.start_repair_runner())
        active.assert_called_with("sinko-repair")
        self.assertEqual(popen.call_args[0][0][-1:], ["repair"])
        self.assertIn("could not start the repair", logs.records[0].getMessage())
        self.assertTrue(os.path.exists(pb.state_path("repair.log")))
        self.assertFalse(os.path.exists(pb.state_path("update.log")))

    def test_power_commands(self):
        with mock.patch.object(pb.subprocess, "run") as run:
            pb.run_power("reboot")
            pb.run_power("poweroff")
        self.assertEqual([c[0][0] for c in run.call_args_list], [["systemctl", "--no-block", "reboot"], ["systemctl", "--no-block", "poweroff"]])
        with mock.patch.object(pb.subprocess, "run") as run:
            for bad in ("halt", "reboot; rm -rf /", None, ""):
                with self.assertRaises(ValueError, msg=repr(bad)):
                    pb.run_power(bad)
            run.assert_not_called()


class BoxInfoJobTests(Fixture):
    """/pb/box.json is refreshed by a job of its own, every five minutes, while the rules are being applied."""

    def calls(self):
        return [kw for _, kw in self.boxinfo_calls]

    def test_the_first_write_is_soon_after_start_and_then_every_five_minutes(self):
        self.advance(9)
        self.settle()
        self.assertEqual(self.boxinfo_calls, [])
        self.advance(2)
        self.settle()
        self.assertEqual(self.calls(), [{"force": False}])
        self.advance(299)
        self.settle()
        self.assertEqual(len(self.boxinfo_calls), 1)
        self.advance(2)
        self.settle()
        self.assertEqual(len(self.boxinfo_calls), 2)

    def test_it_gets_the_settings_that_are_in_force_now(self):
        self.advance(11)
        self.settle()
        self.assertEqual(self.boxinfo_calls[0][0]["SINKO_IP"], "192.168.1.5")

    def test_not_while_the_tick_is_failing_because_the_file_says_the_scheduler_works(self):
        self.m.tick_ok = False
        self.advance(400)
        self.settle()
        self.assertEqual(self.boxinfo_calls, [])
        self.m.tick_ok = True
        self.settle()
        self.assertEqual(len(self.boxinfo_calls), 1)

    def test_not_while_an_update_is_installing_because_the_installer_swaps_the_folder_it_lives_in(self):
        self.set_state(lambda s: s["update"].update(status="running", at=self.now.timestamp()))
        self.advance(20)
        self.settle()
        self.assertEqual(self.boxinfo_calls, [])
        holder = pb.RunLock()
        self.assertTrue(holder.acquire())
        self.addCleanup(holder.release)
        self.set_state(lambda s: s["update"].update(status="ok"))
        self.settle()
        self.assertEqual(self.boxinfo_calls, [], "a runner holds the lock even before it has said running")
        holder.release()
        self.settle()
        self.assertEqual(len(self.boxinfo_calls), 1)

    def test_a_clock_that_is_not_believed_writes_nothing_and_the_moment_it_is_believed_writes_at_once(self):
        self.clock_trusted = False
        self.advance(400)
        self.settle()
        self.assertEqual(self.boxinfo_calls, [])
        self.clock_trusted = True
        self.pass_()
        self.assertEqual(self.calls(), [{"force": True}], "the time in the file must match the clock again")
        self.advance(15)
        self.settle()
        self.assertEqual(len(self.boxinfo_calls), 1, "once, then back to the five minutes")

    def test_a_clock_that_is_set_makes_the_file_fresh_at_once_and_not_at_the_next_five_minutes(self):
        # No internet when the box started: its clock was the last saved one and was believed after ten minutes. Days
        # later the time server answers and the clock jumps. box.json holds the old time until the next write, and the page
        # (which compares it with the box's clock) would call the scheduler dead and tell the parent to unplug the box.
        self.advance(11)
        self.settle()
        self.assertEqual(self.calls(), [{"force": False}])
        self.advance(40)
        self.settle()
        self.assertEqual(len(self.boxinfo_calls), 1, "time passing as it should: nothing to do before five minutes")
        self.now += dt.timedelta(days=3)                    # the clock is set; the monotonic clock did not move
        self.pass_()
        self.assertEqual(self.calls(), [{"force": False}, {"force": True}], "written in the pass that saw the step")
        self.advance(15)
        self.settle()
        self.assertEqual(len(self.boxinfo_calls), 2, "and then back to every five minutes")
        self.advance(300)
        self.settle()
        self.assertEqual(len(self.boxinfo_calls), 3)

    def test_a_clock_that_is_set_back_is_noticed_too(self):
        self.advance(11)
        self.settle()
        self.now -= dt.timedelta(hours=2)
        self.pass_()
        self.assertEqual(self.calls()[-1], {"force": True})

    def test_a_clock_that_is_a_little_off_is_not_a_step(self):
        self.advance(11)
        self.settle()
        for drift in (5, -5, 20, -20):
            self.now += dt.timedelta(seconds=drift)         # what slewing by a time server does, and a slow pass
            self.pass_()
        self.assertEqual(len(self.boxinfo_calls), 1)

    def test_a_step_during_an_update_is_remembered_until_the_file_may_be_written_again(self):
        self.advance(11)
        self.settle()
        self.set_state(lambda s: s["update"].update(status="running", at=self.now.timestamp()))
        self.now += dt.timedelta(seconds=120)               # (a step; small enough that the runner is not yet called gone)
        self.settle()
        self.assertEqual(len(self.boxinfo_calls), 1, "the installer is swapping the folder the file lives in")
        self.set_state(lambda s: s["update"].update(status="ok", at=self.now.timestamp()))
        self.advance(15)
        self.settle()
        self.assertEqual(self.calls()[-1], {"force": True})
        self.assertEqual(len(self.boxinfo_calls), 2)

    def test_a_step_while_the_tick_fails_waits_for_a_pass_that_works(self):
        self.advance(11)
        self.settle()
        self.m.tick_ok = False
        self.now += dt.timedelta(days=1)
        self.settle()
        self.assertEqual(len(self.boxinfo_calls), 1)
        self.m.tick_ok = True
        self.advance(15)
        self.settle()
        self.assertEqual(self.calls()[-1], {"force": True})

    def test_the_file_on_disk_carries_the_new_time_in_the_pass_that_saw_the_step(self):
        os.makedirs(os.path.join(self.webroot, "pb"))
        path = os.path.join(self.webroot, "pb", "box.json")

        def write(conf, force=False):
            return REAL_WRITE_BOX_INFO(conf, ip=None, force=force, root=self.webroot, when=self.now.timestamp(), mdns=False)
        m = pb.Maintenance(self.api, config_path=self.config_path, monotonic=lambda: self.mono, job_factory=InlineJob,
                           clock_ok=lambda: True, box_info=write, catalog=lambda: self.catalog, check=lambda c: None,
                           default_ip=lambda: "192.168.1.5")
        self.advance(11)
        m.run(self.now)
        m.run(self.now)
        with open(path) as fh:
            before = json.load(fh)["at"]
        self.assertEqual(before, int(self.now.timestamp()))
        self.advance(60)
        m.run(self.now)
        self.now += dt.timedelta(days=2)
        m.run(self.now)
        m.run(self.now)
        with open(path) as fh:
            after = json.load(fh)["at"]
        self.assertEqual(after, int(self.now.timestamp()), "the file is not hours or days behind the box's clock")

    def test_a_failure_to_write_is_logged_now_and_then_never_reaches_the_tick_and_is_retried_in_a_minute(self):
        self.boxinfo_error = OSError(30, "Read-only file system")
        self.advance(11)
        with self.assertLogs("sinko", level="WARNING") as logs:
            actions = self.settle()
            self.assertIsInstance(actions, list, "the pass went on")
            self.advance(61)
            self.settle()
            self.advance(61)
            self.settle()
        self.assertGreaterEqual(len(self.boxinfo_calls), 3, "tried again every minute")
        self.assertEqual(len(logs.records), 1, "but said once")
        self.assertIn("box.json", logs.records[0].getMessage())
        self.boxinfo_error = None
        self.advance(61)
        self.settle()
        count = len(self.boxinfo_calls)
        self.advance(100)
        self.settle()
        self.assertEqual(len(self.boxinfo_calls), count, "back to five minutes")

    def test_a_missing_page_folder_is_not_a_failure_at_all(self):
        # the real function, a web root without a pb folder: "nothing to write"
        m = pb.Maintenance(self.api, config_path=self.config_path, monotonic=lambda: self.mono, job_factory=InlineJob,
                           box_info=REAL_WRITE_BOX_INFO, catalog=lambda: self.catalog, check=lambda c: None,
                           default_ip=lambda: "192.168.1.5")
        os.makedirs(self.webroot)
        self.advance(11)
        with mock.patch.object(pb, "unit_active", return_value=True), mock.patch.object(pb, "default_route_ipv4", return_value=None):
            m.run(self.now)
            with self.assertNoLogs("sinko", level="WARNING") if hasattr(self, "assertNoLogs") else contextlib.nullcontext():
                m.run(self.now)
        self.assertEqual(os.listdir(self.webroot), [])

    def test_the_real_writer_leaves_a_pulse_the_page_can_read(self):
        m = pb.Maintenance(self.api, config_path=self.config_path, monotonic=lambda: self.mono, job_factory=InlineJob,
                           box_info=REAL_WRITE_BOX_INFO, catalog=lambda: self.catalog, check=lambda c: None,
                           default_ip=lambda: "192.168.1.5")
        os.makedirs(os.path.join(self.webroot, "pb"))
        self.advance(11)
        with mock.patch.object(pb, "unit_active", return_value=True), \
                mock.patch.object(pb, "default_route_ipv4", return_value="192.168.1.50"):
            m.run(self.now)
            m.run(self.now)
        with open(os.path.join(self.webroot, "pb", "box.json")) as fh:
            info = json.load(fh)
        self.assertEqual((info["v"], info["version"], info["ip"], info["mdns"], info["counter"]),
                         (1, pb.VERSION, "192.168.1.50", True, False))
        self.assertLess(abs(info["at"] - time.time()), 30)

    def test_a_slow_writer_never_delays_the_tick(self):
        gate = threading.Event()
        self.addCleanup(gate.set)
        started = []

        def slow(conf, **kw):
            started.append(1)
            gate.wait(5)
            return "written"
        m = pb.Maintenance(self.api, config_path=self.config_path, monotonic=lambda: self.mono, box_info=slow,
                           catalog=lambda: self.catalog, check=lambda c: None, default_ip=lambda: None)
        self.advance(11)
        began = time.monotonic()
        for _ in range(5):
            m.run(self.now)
        self.assertLess(time.monotonic() - began, 1.5)
        for _ in range(100):
            if started:
                break
            time.sleep(0.01)
        self.assertEqual(started, [1], "one at a time")


class HealTests(Fixture):
    """A group or list that Sinko needs went missing from Pi-hole: run setup again, in a job, at most once an hour."""

    def passes(self, count):
        out = []
        for _ in range(count):
            self.advance(15)
            out += self.pass_()
        return out

    def delete_group(self, name):
        self.store.groups[:] = [g for g in self.store.groups if g["name"] != name]

    def test_a_deleted_service_group_is_put_back_after_a_few_passes_not_at_the_first_sight(self):
        self.delete_group("pb-svc-youtube")
        with self.assertLogs("sinko", level="WARNING") as logs:
            self.passes(2)
            self.assertEqual(self.heal_calls, [], "FTL may be rebuilding: it has to look like this for a while")
            self.passes(1)
        self.assertEqual(len(self.heal_calls), 1)
        self.assertIn("pb-svc-youtube", logs.records[0].getMessage())

    def test_a_deleted_list_is_found_within_five_minutes(self):
        self.store.lists[:] = [l for l in self.store.lists if l["comment"] != "pb:tiktok"]
        with self.assertLogs("sinko", level="WARNING") as logs:
            self.passes(3)
        self.assertEqual(len(self.heal_calls), 1)
        self.assertIn("the list of tiktok", logs.records[0].getMessage())

    def test_a_list_that_goes_missing_later_is_seen_at_the_next_look(self):
        self.passes(1)
        self.store.lists[:] = [l for l in self.store.lists if l["comment"] != "pb:guard"]
        self.passes(3)
        self.assertEqual(self.heal_calls, [], "the lists are looked at every five minutes")
        with self.assertLogs("sinko", level="WARNING"):
            self.passes(24)
        self.assertEqual(len(self.heal_calls), 1)

    def test_not_more_than_once_an_hour_and_the_log_says_so_at_most_every_half_hour(self):
        self.delete_group("pb-svc-youtube")                      # the fake repair does not fix it
        with self.assertLogs("sinko", level="WARNING") as logs:
            self.passes(3)
            self.assertEqual(len(self.heal_calls), 1)
            self.passes(200)                                     # fifty minutes
            self.assertEqual(len(self.heal_calls), 1)
            self.passes(40)                                      # now over the hour
            self.assertEqual(len(self.heal_calls), 2)
        self.assertEqual(len(logs.records), 2, "one line per repair")

    def test_a_group_that_comes_back_by_itself_cancels_the_count(self):
        self.delete_group("pb-svc-youtube")
        self.passes(2)
        self.ctl.setup(run_gravity=False)
        self.passes(5)
        self.assertEqual(self.heal_calls, [])

    def test_nothing_is_repaired_while_an_update_runs_or_after_sinko_was_removed(self):
        self.delete_group("pb-svc-youtube")
        self.set_state(lambda s: s["update"].update(status="running", at=self.now.timestamp()))
        self.passes(6)
        self.assertEqual(self.heal_calls, [])
        self.set_state(lambda s: s["update"].update(status="idle"))
        pb.ensure_state_dir()
        pb.atomic_write(pb.state_path("removed"), "1\n")
        self.passes(6)
        self.assertEqual(self.heal_calls, [], "somebody ran `sinko remove`: that is not damage")
        os.unlink(pb.state_path("removed"))
        with self.assertLogs("sinko", level="WARNING"):
            self.passes(3)
        self.assertEqual(len(self.heal_calls), 1)

    def test_an_answer_without_pihole_s_own_group_is_not_believed(self):
        self.store.groups[:] = []
        self.passes(8)
        self.assertEqual(self.heal_calls, [])

    def test_when_the_shared_state_itself_is_gone_the_repair_still_runs(self):
        self.delete_group("pb-state")
        with self.assertLogs("sinko", level="WARNING") as logs:
            self.passes(3)
        self.assertEqual(self.heal_calls, [None])
        self.assertIn("pb-state", logs.records[0].getMessage())

    def test_a_repair_that_fails_is_logged_and_tried_again_after_the_hour(self):
        self.delete_group("pb-svc-youtube")
        self.heal_error = RuntimeError("gravity could not be updated")
        with self.assertLogs("sinko", level="WARNING") as logs:
            self.passes(3)
            self.assertEqual(len(self.heal_calls), 1)
            self.passes(60)
            self.assertEqual(len(self.heal_calls), 1)
        self.assertTrue(any("self-repair" in r.getMessage() for r in logs.records))
        self.heal_error = None
        with self.assertLogs("sinko", level="INFO"):
            self.passes(200)
        self.assertEqual(len(self.heal_calls), 2)

    def test_an_unreadable_catalog_means_nothing_to_compare_with(self):
        def broken():
            raise pb.CatalogError("missing list file")
        self.m.catalog = broken
        self.delete_group("pb-svc-youtube")
        self.passes(6)
        self.assertEqual(self.heal_calls, [])

    def test_an_intact_box_is_never_touched(self):
        self.m._next_check = 10 ** 9                              # (the daily check writes its answer: not what is looked at here)
        writes = self.store.writes
        self.passes(30)
        self.assertEqual(self.heal_calls, [])
        self.assertEqual(self.store.writes, writes)


class RealHealTests(unittest.TestCase):
    """The real repair against the mock Pi-hole: pb-* objects are put back, nothing else is touched."""

    def setUp(self):
        self.httpd, self.store = fake_release.serve_pihole()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        with open(os.path.join(tmp.name, "pw"), "w") as fh:
            fh.write(mock_pihole.PASSWORD)
        env = mock.patch.dict(os.environ, {"SINKO_API_URL": "http://127.0.0.1:%d" % self.httpd.server_port,
                                           "SINKO_STATE_DIR": os.path.join(tmp.name, "state")})
        env.start()
        self.addCleanup(env.stop)
        self.catalog = pb.load_catalog(LISTS)
        self.gravity = []
        for patch in (mock.patch.object(pb, "CLI_PW_FILE", os.path.join(tmp.name, "pw")),
                      mock.patch.object(pb, "load_catalog", lambda *a, **k: self.catalog),
                      mock.patch.object(pb.Controller, "gravity", staticmethod(lambda: self.gravity.append(1)))):
            patch.start()
            self.addCleanup(patch.stop)
        self.api = pb.Api()
        self.api.login()
        pb.Controller(self.api, self.catalog, "https://lists.example/l").setup(run_gravity=False)

    def snapshot(self, only_user=False):
        groups = [g for g in self.store.groups if not only_user or not g["name"].startswith("pb-")]
        lists = [l for l in self.store.lists if not only_user or not (l.get("comment") or "").startswith("pb:")]
        domains = [d for d in self.store.domains if not only_user or d.get("comment") != pb.BLOCK_ALL_COMMENT]
        return json.dumps([groups, lists, domains, self.store.clients], sort_keys=True)

    def test_what_went_missing_comes_back_and_gravity_is_updated_once(self):
        self.api.request("POST", "/api/groups", {"name": "Guests", "comment": "mine", "enabled": True})
        self.api.request("POST", "/api/lists?type=block", {"address": "https://example.com/ads.txt", "comment": "StevenBlack"})
        self.api.request("POST", "/api/domains/deny/regex", {"domain": "ads\\.example", "comment": "user regex"})
        self.api.request("POST", "/api/groups", {"name": "Kids", "comment": "Parental-control service blocklists"})
        self.api.request("POST", "/api/domains/deny/regex", {"domain": ".*", "comment": "left by the 1.x installer", "groups": [0]})
        before_user = self.snapshot(only_user=True)
        self.store.groups[:] = [g for g in self.store.groups if g["name"] != "pb-svc-youtube"]
        self.store.lists[:] = [l for l in self.store.lists if l["comment"] != "pb:tiktok"]
        sessions = len(self.store.sessions)
        REAL_RUN_SELF_HEAL()
        self.assertIn("pb-svc-youtube", {g["name"] for g in self.store.groups})
        self.assertIn("pb:tiktok", {l["comment"] for l in self.store.lists})
        self.assertEqual(self.gravity, [1], "the lists must be in gravity again")
        self.assertEqual(self.snapshot(only_user=True), before_user, "nothing that is not pb-* was touched (not even the 1.x leftovers)")
        self.assertEqual(len(self.store.sessions), sessions, "the session of the repair was given back")

    def test_a_failing_repair_still_gives_the_session_back(self):
        sessions = len(self.store.sessions)
        with mock.patch.object(pb.Controller, "setup", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                REAL_RUN_SELF_HEAL()
        self.assertEqual(len(self.store.sessions), sessions)

    def test_the_owners_own_block_everything_rule_is_not_touched_by_a_repair_either(self):
        self.api.request("DELETE", "/api/domains/deny/regex/" + pb.Api.q(pb.BLOCK_ALL_REGEX))
        self.api.request("POST", "/api/domains/deny/regex", {"domain": pb.BLOCK_ALL_REGEX, "comment": "my allow-list mode", "groups": [0]})
        self.store.groups[:] = [g for g in self.store.groups if g["name"] != "pb-svc-youtube"]
        REAL_RUN_SELF_HEAL()
        rule = next(d for d in self.store.domains if d["domain"] == pb.BLOCK_ALL_REGEX)
        self.assertEqual(rule["comment"], "my allow-list mode")


class BackgroundJobTests(unittest.TestCase):
    def test_a_result_is_collected_once(self):
        job = pb.BackgroundJob("t")
        self.assertIsNone(job.take())
        self.assertTrue(job.start(lambda: 42))
        for _ in range(200):
            outcome = job.take()
            if outcome:
                break
            time.sleep(0.01)
        self.assertEqual(outcome, ("ok", 42))
        self.assertIsNone(job.take())
        self.assertFalse(job.busy())

    def test_an_exception_is_returned_not_raised(self):
        job = pb.BackgroundJob("t")

        def boom():
            raise RuntimeError("x")
        job.start(boom)
        for _ in range(200):
            outcome = job.take()
            if outcome:
                break
            time.sleep(0.01)
        self.assertEqual(outcome[0], "error")
        self.assertIsInstance(outcome[1], RuntimeError)

    def test_it_stays_busy_until_collected_so_one_runs_at_a_time(self):
        job = pb.BackgroundJob("t")
        gate = threading.Event()
        self.assertTrue(job.start(gate.wait))
        self.assertTrue(job.busy())
        self.assertFalse(job.start(lambda: 1))
        self.assertIsNone(job.take(), "still running")
        gate.set()
        for _ in range(200):
            if job.take():
                break
            time.sleep(0.01)
        self.assertFalse(job.busy())
        self.assertTrue(job.start(lambda: 1))


class TickDoesNotWaitTests(Fixture):
    def test_a_slow_network_never_delays_the_tick(self):
        gate = threading.Event()
        self.addCleanup(gate.set)

        def slow_check(conf):
            gate.wait(10)
            return self.release("3.1.0")
        m = pb.Maintenance(self.api, config_path=self.config_path, monotonic=lambda: self.mono, check=slow_check,
                           default_ip=lambda: None)
        self.advance(121)
        began = time.monotonic()
        for _ in range(5):
            m.run(self.now)
        self.assertLess(time.monotonic() - began, 1.5, "five passes while the check is stuck")
        self.assertEqual(self.state()["update"]["latest"], None)
        gate.set()
        for _ in range(300):
            m.run(self.now)
            if self.state()["update"]["latest"]:
                break
            time.sleep(0.01)
        self.assertEqual(self.state()["update"]["latest"], "3.1.0")

    def test_the_scheduler_loop_runs_the_tick_first_and_the_jobs_after_it(self):
        order = []

        class FakeController:
            def __init__(self, api, catalog, clock_trusted=None):
                pass

            def tick(self):
                order.append("tick")

        class FakeMaintenance:
            api = None

            def __init__(self, clock_ok=None, **kw):
                pass

            def run(self, now):
                order.append("maintenance")
                return []
        stop = {}

        def fake_signal(sig, handler):
            stop["handler"] = handler
        sleeps = []

        def fake_sleep(seconds):
            sleeps.append(seconds)
            if len(sleeps) >= 20:
                stop["handler"]()
        with mock.patch.object(pb.signal, "signal", fake_signal), mock.patch.object(pb.time, "sleep", fake_sleep), \
                mock.patch.object(pb, "Controller", FakeController), mock.patch.object(pb, "Maintenance", FakeMaintenance), \
                mock.patch.object(pb, "Api", lambda: mock.Mock()), mock.patch.object(pb, "load_catalog", return_value={}), \
                mock.patch.object(pb, "TICK_SECONDS", 3):
            self.assertEqual(pb.cmd_run(None), 0)
        self.assertEqual(order[:4], ["tick", "maintenance", "tick", "maintenance"])
        self.assertTrue(os.path.isdir(os.path.join(self.state_dir, "cache")), "the runtime folder is made at start")

    def test_a_failing_tick_does_not_run_the_jobs_and_backs_off(self):
        calls = []

        class FailingController:
            def __init__(self, api, catalog, clock_trusted=None):
                pass

            def tick(self):
                calls.append("tick")
                raise pb.ApiError(500, "FTL is restarting")

        class FakeMaintenance:
            api = None

            def __init__(self, clock_ok=None, **kw):
                pass

            def run(self, now):
                calls.append("maintenance")
        stop = {}
        sleeps = []

        def fake_sleep(seconds):
            sleeps.append(seconds)
            if len(sleeps) >= 12:
                stop["handler"]()
        with mock.patch.object(pb.signal, "signal", lambda sig, handler: stop.update(handler=handler)), \
                mock.patch.object(pb.time, "sleep", fake_sleep), mock.patch.object(pb, "Controller", FailingController), \
                mock.patch.object(pb, "Maintenance", FakeMaintenance), mock.patch.object(pb, "Api", lambda: mock.Mock()), \
                mock.patch.object(pb, "load_catalog", return_value={}), self.assertLogs("sinko", level="WARNING"):
            pb.cmd_run(None)
        self.assertNotIn("maintenance", calls)
        self.assertGreaterEqual(len(calls), 2)


class OneTickTests(Fixture):
    """Everything the page can ask for at once, and a restart in the middle of it."""

    def everything_at_once(self):
        self.ip = "192.168.1.77"
        self.found = self.release("3.1.0")
        self.set_state(lambda s: s["update"].update(request=11, checkRequest=12))
        self.advance(200)                                  # the first check and the first address look are due

    def test_an_update_request_a_check_request_and_an_address_change_in_one_tick(self):
        self.everything_at_once()
        actions = self.settle()
        self.assertIn("update requested by the page", actions)
        self.assertIn("check requested by the page", actions)
        self.assertIn("address changed from 192.168.1.5 to 192.168.1.77", actions)
        self.assertEqual(self.runner_calls, ["running"])
        self.assertEqual(len(self.check_calls), 1)
        self.assertEqual([c[0] for c in self.address_calls], ["192.168.1.77"])
        u = self.state()["update"]
        self.assertEqual((u["status"], u["request"], u["checkRequest"], u["latest"]), ("running", None, None, "3.1.0"))
        self.assertEqual(pb.load_handled(), {"update": 11, "check": 12})

    def test_a_power_request_in_the_same_tick_is_dropped_because_the_update_has_just_begun(self):
        self.everything_at_once()
        self.set_state(lambda s: s["power"].update(request=13, action="reboot"))
        actions = self.settle()
        self.assertEqual(self.power_calls, [], "never reboot in the middle of an installation")
        self.assertIn("power request ignored: an update is running", actions)
        self.assertEqual(self.state()["power"]["request"], None, "and never kept for after it")
        self.set_state(lambda s: s["update"].update(status="ok"))
        self.settle()
        self.assertEqual(self.power_calls, [])

    def test_after_a_restart_nothing_that_was_handled_happens_again(self):
        self.everything_at_once()
        self.settle()
        self.set_state(lambda s: s["update"].update(status="ok", at=0))
        self.set_state(lambda s: s["power"].update(request=13, action="reboot"))
        self.settle()
        self.assertEqual((len(self.runner_calls), len(self.check_calls), self.power_calls), (1, 1, ["reboot"]))
        # The service restarts (the installer does that). The page's markers are still in the state, because the box
        # went down before the clearing reached Pi-hole; the handled markers survive on disk.
        self.set_state(lambda s: s["update"].update(request=11, checkRequest=12, status="idle"))
        self.set_state(lambda s: s["power"].update(request=13, action="reboot"))
        fresh = pb.Maintenance(self.api, config_path=self.config_path, monotonic=lambda: self.mono, job_factory=InlineJob,
                               clock_ok=lambda: True, check=self.fake_check, start_runner=self.fake_runner,
                               power=self.fake_power, default_ip=lambda: "192.168.1.77", apply_address=self.fake_apply,
                               ping=self.fake_ping, body=lambda: "BODY", jitter=self.fake_jitter)
        for _ in range(3):
            fresh.run(self.now)
        self.assertEqual((len(self.runner_calls), self.power_calls), (1, ["reboot"]), "no second update, no second reboot")
        u = self.state()["update"]
        self.assertEqual((u["request"], u["checkRequest"], self.state()["power"]["request"]), (None, None, None),
                         "the stale markers are cleaned up")

    def test_a_new_marker_after_a_restart_is_a_new_request(self):
        self.everything_at_once()
        self.settle()
        self.set_state(lambda s: s["update"].update(request=21, status="ok", at=0))
        self.settle()
        self.assertEqual(len(self.runner_calls), 2)


class EverythingFailsTests(Fixture):
    def test_every_network_and_system_call_failing_at_once_is_survived(self):
        self.found = pb.UpdateError("offline")
        self.runner_ok = OSError("no systemd")
        self.power_error = OSError("no systemctl")
        self.address_error = OSError("no pihole-FTL")
        self.ip = "192.168.1.77"
        self.set_state(lambda s: s["update"].update(request=1, checkRequest=2, auto=True, latest="3.1.0"))
        self.set_state(lambda s: s["power"].update(request=3, action="reboot"))
        self.advance(200)
        with self.assertLogs("sinko", level="WARNING") as logs:
            for _ in range(10):
                self.settle()
                self.advance(15)
        messages = [r.getMessage() for r in logs.records]
        self.assertEqual(len(messages), len(set(m.split(":")[0] for m in messages)), "each job complains once: %s" % messages)
        u = self.state()["update"]
        self.assertEqual(u["status"], "failed", "the runner could not start")
        self.assertIsNone(u["request"])
        self.assertIsNone(u["checkRequest"])
        self.assertEqual(self.state()["power"], {"request": None, "action": None})
        self.assertEqual(self.power_calls, [], "the power request came while the update was starting: dropped")
        self.assertEqual(self.rules(), json.dumps([g for g in self.store.groups if g["name"] != "pb-state"], sort_keys=True))


class PageRepairTests(Fixture):
    """An update cut off between the program and the page leaves a box of two versions that nothing else puts right:
    after ten minutes of that, with nothing running and Sinko not removed on purpose, the scheduler starts `sinko repair`
    (the installer, from the copy on the box), at most once an hour, and the note of the last try survives its restart."""

    def setUp(self):
        super().setUp()
        self.shown = "2.9.0"                                    # the page of the version before
        self.repairs = []
        self.repair_result = True
        self.app = os.path.join(self.tmp, "app")
        os.makedirs(os.path.join(self.app, "src"))
        self.put_source(pb.VERSION)
        patch = mock.patch.object(pb, "APP_DIR", self.app)
        patch.start()
        self.addCleanup(patch.stop)
        self.arm(self.m)

    def put_source(self, version):
        with open(os.path.join(self.app, "src", "VERSION"), "w") as fh:
            fh.write(version + "\n")

    def start_repair(self):
        self.repairs.append(self.now)
        self.seen_note = os.path.exists(pb.state_path("repair.json"))
        if isinstance(self.repair_result, Exception):
            raise self.repair_result
        return self.repair_result

    def arm(self, m):
        m.page_version = lambda: self.shown
        m.start_repair = self.start_repair
        return m

    def watch(self, minutes, m=None):
        """Time passes, a minute at a time (the page's version is looked at once a minute), passes of the scheduler between."""
        for _ in range(int(minutes)):
            self.advance(60)
            if m is None:
                self.settle()
            else:
                m.run(self.now)
                m.run(self.now)

    def test_nothing_happens_while_the_versions_agree(self):
        self.shown = pb.VERSION
        self.watch(120)
        self.assertEqual(self.repairs, [])
        self.assertFalse(os.path.exists(pb.state_path("repair.json")))

    def test_ten_minutes_of_two_versions_start_one_repair_and_nine_do_not(self):
        self.watch(8)
        self.assertEqual(self.repairs, [])
        with self.assertLogs("sinko", level="WARNING") as logs:
            self.watch(5)
        self.assertEqual(len(self.repairs), 1)
        self.assertIn("(2.9.0) and the program (%s) have been of different versions" % pb.VERSION, logs.records[0].getMessage())
        self.assertIn("at most once an hour", logs.records[0].getMessage())
        self.assertTrue(self.seen_note, "the note that it was tried is on disk before the repair starts: it restarts this scheduler")

    def test_a_page_that_is_right_again_starts_the_ten_minutes_over(self):
        self.watch(8)
        self.shown = pb.VERSION
        self.watch(2)
        self.shown = "2.9.0"
        self.watch(8)
        self.assertEqual(self.repairs, [], "eight and eight is not ten in a row")
        with self.assertLogs("sinko", level="WARNING"):
            self.watch(4)
        self.assertEqual(len(self.repairs), 1)

    def test_a_page_without_a_version_file_counts_as_a_different_version(self):
        self.shown = None
        with self.assertLogs("sinko", level="WARNING") as logs:
            self.watch(13)
        self.assertEqual(len(self.repairs), 1)
        self.assertIn("no version", logs.records[0].getMessage())

    def test_not_while_an_update_or_an_installation_runs_and_the_ten_minutes_start_when_it_is_over(self):
        holder = pb.RunLock()
        self.assertTrue(holder.acquire())
        self.watch(15)                                           # (an update's runner holds this lock for as long as it lives)
        holder.release()
        self.assertEqual(self.repairs, [])
        install = pb.open_lock_file("install.lock")              # the installer, started by hand
        fcntl.flock(install, fcntl.LOCK_EX)
        self.watch(15)
        self.assertEqual(self.repairs, [])
        os.close(install)
        self.watch(9)
        self.assertEqual(self.repairs, [], "the ten minutes begin when it is over, not when it began")
        with self.assertLogs("sinko", level="WARNING"):
            self.watch(3)
        self.assertEqual(len(self.repairs), 1)

    def test_not_while_the_state_says_an_update_is_running(self):
        for _ in range(15):
            self.set_state(lambda s: s["update"].update(status="running", at=self.now.timestamp(), to="3.1.0"))
            self.watch(1)
        self.assertEqual(self.repairs, [])

    def test_not_after_sinko_was_removed_on_purpose(self):
        pb.ensure_state_dir()
        pb.atomic_write(pb.removed_flag(), "1\n")
        self.watch(120)
        self.assertEqual(self.repairs, [])

    def test_not_without_a_copy_of_this_very_version_to_install_from(self):
        self.put_source("2.9.0")
        self.watch(30)
        self.assertEqual(self.repairs, [], "the copy is another version's")
        shutil.rmtree(os.path.join(self.app, "src"))
        self.watch(30)
        self.assertEqual(self.repairs, [], "no copy at all")
        os.makedirs(os.path.join(self.app, "src"))
        self.put_source(pb.VERSION)
        with self.assertLogs("sinko", level="WARNING"):
            self.watch(2)
        self.assertEqual(len(self.repairs), 1, "and as soon as there is one")

    def test_not_on_a_clock_that_is_not_believed(self):
        self.clock_trusted = False
        self.watch(30)
        self.assertEqual(self.repairs, [])
        self.clock_trusted = True
        with self.assertLogs("sinko", level="WARNING"):
            self.watch(1)
        self.assertEqual(len(self.repairs), 1)

    def test_at_most_once_an_hour_whatever_restarts_in_between(self):
        with self.assertLogs("sinko", level="WARNING"):
            self.watch(12)
        self.assertEqual(len(self.repairs), 1)
        # The repair restarted the scheduler: a new process, with no memory but the note on disk. The page is still wrong.
        fresh = self.arm(pb.Maintenance(self.api, config_path=self.config_path, monotonic=lambda: self.mono, job_factory=InlineJob,
                                        clock_ok=lambda: self.clock_trusted, check=lambda c: None, default_ip=lambda: self.ip,
                                        box_info=self.fake_box_info, catalog=lambda: self.catalog))
        self.watch(40, fresh)
        self.assertEqual(len(self.repairs), 1, "not within the hour (the old process started it 52 minutes ago)")
        with self.assertLogs("sinko", level="WARNING"):
            self.watch(20, fresh)
        self.assertEqual(len(self.repairs), 2, "and again once the hour is over")

    def test_a_note_from_the_future_does_not_block_for_ever(self):
        pb.ensure_state_dir()
        pb.atomic_write(pb.state_path("repair.json"), json.dumps({"at": self.now.timestamp() + 10 * 86400}))
        with self.assertLogs("sinko", level="WARNING"):
            self.watch(13)
        self.assertEqual(len(self.repairs), 1)
        for junk in ("garbage", "[]", '{"at": "x"}', '{"at": 1' + "0" * 400 + "}"):
            pb.atomic_write(pb.state_path("repair.json"), junk)
            self.assertTrue(pb.repair_due(self.now.timestamp()), junk)

    def test_a_failing_look_is_logged_once_and_never_stops_anything(self):
        def broken():
            raise OSError("the card is read-only")
        self.m.page_version = broken
        with self.assertLogs("sinko", level="WARNING") as logs:
            self.watch(20)
        self.assertEqual(len([r for r in logs.records if "version of the page" in r.getMessage()]), 1)
        self.assertEqual(self.repairs, [])

    def test_a_repair_that_cannot_be_started_is_logged_and_the_hour_still_counts(self):
        self.repair_result = RuntimeError("no systemd")
        with self.assertLogs("sinko", level="WARNING") as logs:
            self.watch(13)
        self.assertEqual(len(self.repairs), 1)
        self.assertTrue([r for r in logs.records if "repair: no systemd" in r.getMessage()], [r.getMessage() for r in logs.records])
        self.assertFalse(pb.repair_due(self.now.timestamp()), "so that a failing start is not tried every minute")

    def test_a_runner_that_says_it_did_not_start_is_logged_too(self):
        self.repair_result = False
        with self.assertLogs("sinko", level="WARNING") as logs:
            self.watch(13)
        self.assertEqual(len(self.repairs), 1)
        self.assertTrue([r for r in logs.records if "it could not be started" in r.getMessage()])

    def test_the_repair_is_off_unless_the_scheduler_gave_it_both_hands(self):
        for missing in ("page_version", "start_repair"):
            m = pb.Maintenance(self.api, config_path=self.config_path, monotonic=lambda: self.mono, job_factory=InlineJob,
                               clock_ok=lambda: self.clock_trusted, check=lambda c: None, default_ip=lambda: self.ip,
                               box_info=self.fake_box_info, catalog=lambda: self.catalog)
            self.arm(m)
            setattr(m, missing, None)
            self.watch(30, m)
        self.assertEqual(self.repairs, [])
        self.assertEqual(pb.Maintenance(self.api).page_version, None)
        self.assertEqual(pb.Maintenance(self.api).start_repair, None)

    def test_the_scheduler_builds_its_maintenance_with_the_real_ones(self):
        built = {}

        class Spy:
            def __init__(self, **kw):
                built.update(kw)
        with mock.patch.object(pb, "Maintenance", Spy):
            pb.Scheduler()
        self.assertEqual(built["start_repair"], pb.start_repair_runner)
        self.assertEqual(built["page_version"].__self__.__class__, pb.PageWatch)

    def test_the_look_at_the_page_asks_pihole_for_the_web_root_once(self):
        os.makedirs(os.path.join(self.webroot, "pb"))
        with open(os.path.join(self.webroot, "pb", "version.txt"), "w") as fh:
            fh.write("3.0.0\n")
        with mock.patch.dict(os.environ):
            os.environ.pop("SINKO_WEBROOT")
            with mock.patch.object(pb, "ftl_config", return_value=self.webroot) as asked:
                watch = pb.PageWatch()
                self.assertEqual((watch.version(), watch.version(), watch.version()), ("3.0.0",) * 3)
                self.assertEqual(asked.call_count, 1)
            with mock.patch.object(pb, "ftl_config", side_effect=OSError("no pihole-FTL")) as asked, \
                    mock.patch.object(pb, "page_version", return_value="3.0.0") as read:
                watch = pb.PageWatch()
                with mock.patch.object(pb.os.path, "isdir", return_value=False), self.assertRaises(OSError) as caught:
                    watch.version()
                self.assertIn("cannot be asked of Pi-hole", str(caught.exception), "not 'there is no page': nothing is known")
                read.assert_not_called()
                with mock.patch.object(pb.os.path, "isdir", return_value=True):
                    self.assertEqual(watch.version(), "3.0.0")
                self.assertEqual(asked.call_count, 2, "an answer that did not come is asked for again")
                self.assertEqual(read.call_args[0][0], "/var/www/html", "and meanwhile the usual place is looked at, if the page is there")
        self.assertEqual(pb.page_version(self.webroot), "3.0.0")
        self.assertIsNone(pb.page_version(os.path.join(self.webroot, "nowhere")))


class ClockGuardTests(unittest.TestCase):
    def guard(self, uptime=100.0, answers=(False,), mono=None):
        self.asked = []
        replies = iter(answers)
        self.clock = [0.0] if mono is None else mono

        def probe():
            self.asked.append(self.clock[0])
            return next(replies)
        return pb.ClockGuard(uptime=lambda: uptime, synchronized=probe, monotonic=lambda: self.clock[0])

    def test_a_clock_that_is_not_synchronised_is_not_believed_in_the_first_ten_minutes(self):
        with self.assertLogs("sinko", level="INFO") as logs:
            self.assertFalse(self.guard(uptime=0).trusted())
        self.assertIn("not synchronised", logs.output[0])
        with self.assertLogs("sinko", level="INFO"):
            self.assertFalse(self.guard(uptime=599.9).trusted())

    def test_after_ten_minutes_it_is_believed_without_even_asking(self):
        for uptime in (600, 601, 86400 * 30):
            guard = self.guard(uptime=uptime)
            self.assertTrue(guard.trusted(), uptime)
            self.assertEqual(self.asked, [], "no need to run timedatectl")

    def test_a_synchronised_clock_is_believed_and_stays_believed(self):
        guard = self.guard(answers=(True, False))
        self.assertTrue(guard.trusted())
        self.clock[0] += 60
        self.assertTrue(guard.trusted())
        self.assertEqual(len(self.asked), 1)

    def test_when_the_answer_is_unknown_the_clock_is_believed(self):
        # No systemd (a container, a development machine) must not hold bedtime for ten minutes.
        guard = self.guard(answers=(None,))
        self.assertTrue(guard.trusted())
        self.assertTrue(pb.ClockGuard(uptime=lambda: None, synchronized=lambda: False).trusted(), "unknown uptime")

    def test_the_answer_is_not_asked_for_more_than_once_every_five_seconds(self):
        guard = self.guard(answers=(False, False, False))
        with self.assertLogs("sinko", level="INFO"):
            for _ in range(4):
                self.assertFalse(guard.trusted())
                self.clock[0] += 1
            self.assertEqual(len(self.asked), 1)
            self.clock[0] += 5
            self.assertFalse(guard.trusted())
        self.assertEqual(len(self.asked), 2)

    def test_when_the_clock_gets_synchronised_the_hold_ends_and_it_is_said_once(self):
        guard = self.guard(answers=(False, True))
        with self.assertLogs("sinko", level="INFO") as logs:
            self.assertFalse(guard.trusted())
            self.assertFalse(guard.trusted())
            self.clock[0] += 6
            self.assertTrue(guard.trusted())
            self.assertTrue(guard.trusted())
        self.assertEqual(len(logs.records), 2, "one line when holding starts, one when it ends")

    def test_the_hold_ends_by_itself_after_ten_minutes_of_uptime(self):
        uptime = [100.0]
        guard = pb.ClockGuard(uptime=lambda: uptime[0], synchronized=lambda: False, monotonic=lambda: uptime[0])
        with self.assertLogs("sinko", level="INFO"):
            self.assertFalse(guard.trusted())
            uptime[0] = 600
            self.assertTrue(guard.trusted())

    def test_timedatectl_is_asked_for_exactly_this_with_a_short_timeout(self):
        def run(answer, code=0):
            return mock.patch.object(pb.subprocess, "run", return_value=subprocess.CompletedProcess([], code, answer, ""))
        with run("yes\n") as m:
            self.assertIs(pb.ntp_synchronized(), True)
        self.assertEqual(m.call_args[0][0], ["timedatectl", "show", "-p", "NTPSynchronized", "--value"])
        self.assertLessEqual(m.call_args[1]["timeout"], 2)
        with run("no\n"):
            self.assertIs(pb.ntp_synchronized(), False)
        for odd in ("", "maybe", "n/a"):
            with run(odd):
                self.assertIsNone(pb.ntp_synchronized(), odd)
        with run("no\n", code=1):
            self.assertIsNone(pb.ntp_synchronized(), "a failing command is not an answer")
        for error in (FileNotFoundError("timedatectl"), subprocess.TimeoutExpired("timedatectl", 1), PermissionError()):
            with mock.patch.object(pb.subprocess, "run", side_effect=error):
                self.assertIsNone(pb.ntp_synchronized())

    def test_a_timedatectl_that_does_not_answer_in_time_counts_as_not_yet_for_the_guard_but_not_for_the_doctor(self):
        timeout = subprocess.TimeoutExpired("timedatectl", 1)
        with mock.patch.object(pb.subprocess, "run", side_effect=timeout):
            self.assertIsNone(pb.ntp_synchronized(), "by default: it could not say")
            self.assertIs(pb.ntp_synchronized(on_timeout=False), False)
            self.assertIsNone(pb.SystemProbe().clock_synchronized(), "the doctor does not warn about a slow answer")
        with mock.patch.object(pb.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "yes\n", "")) as run:
            self.assertIs(pb.SystemProbe().clock_synchronized(), True)
            self.assertEqual(run.call_args[1]["timeout"], 5, "and gives it longer than the scheduler does")
            pb.ntp_synchronized(timeout=3)
            self.assertEqual(run.call_args[1]["timeout"], 3)

    def real_guard(self, uptime, mono=None):
        self.clock = [0.0] if mono is None else mono
        return pb.ClockGuard(uptime=lambda: uptime[0], monotonic=lambda: self.clock[0])

    def test_with_the_real_probe_a_slow_timedatectl_in_the_first_ten_minutes_is_not_a_trusted_clock(self):
        # After a power cut the clock is the last one saved, up to an hour old. If a slow timedatectl read as "believe it",
        # bedtime would end at 02:00 on a clock that says 21:30, with the children's internet back until it got synchronised.
        uptime = [40.0]
        guard = self.real_guard(uptime)
        with mock.patch.object(pb.subprocess, "run", side_effect=subprocess.TimeoutExpired("timedatectl", 1)) as run, \
                self.assertLogs("sinko", level="INFO") as logs:
            self.assertFalse(guard.trusted(), "the first question timed out: not believed")
            self.assertFalse(guard.trusted())
            self.assertEqual(run.call_count, 1, "and it is not asked again at once")
            self.clock[0] += 6
            uptime[0] += 6
            self.assertFalse(guard.trusted())
            self.assertEqual(run.call_count, 2, "but again after five seconds")
        self.assertIn("not synchronised", logs.output[0])

    def test_with_the_real_probe_the_hold_ends_when_timedatectl_does_answer_and_by_itself_after_ten_minutes(self):
        uptime = [40.0]
        guard = self.real_guard(uptime)
        timeout = subprocess.TimeoutExpired("timedatectl", 1)
        with mock.patch.object(pb.subprocess, "run", side_effect=[timeout, subprocess.CompletedProcess([], 0, "yes\n", "")]), \
                self.assertLogs("sinko", level="INFO"):
            self.assertFalse(guard.trusted())
            self.clock[0] += 6
            self.assertTrue(guard.trusted(), "synchronised at the second question")
        late = self.real_guard([599.0])
        with mock.patch.object(pb.subprocess, "run", side_effect=timeout), self.assertLogs("sinko", level="INFO"):
            self.assertFalse(late.trusted())
        later = self.real_guard([600.0])
        with mock.patch.object(pb.subprocess, "run", side_effect=AssertionError("must not be asked")):
            self.assertTrue(later.trusted(), "a home without internet still gets its bedtime after ten minutes")

    def test_with_the_real_probe_a_machine_that_cannot_be_asked_at_all_is_believed(self):
        for error in (FileNotFoundError("timedatectl"), PermissionError("no"), OSError("exec format error")):
            guard = self.real_guard([40.0])
            with mock.patch.object(pb.subprocess, "run", side_effect=error):
                self.assertTrue(guard.trusted(), repr(error))

    def test_uptime_is_read_from_proc_uptime_or_the_override(self):
        with tempfile.NamedTemporaryFile("w", delete=False) as fh:
            fh.write("123.45 678.9\n")
        self.addCleanup(os.remove, fh.name)
        with mock.patch.dict(os.environ, {"SINKO_UPTIME_FILE": fh.name}):
            self.assertEqual(pb.read_uptime(), 123.45)
        with mock.patch.dict(os.environ, {"SINKO_UPTIME_FILE": fh.name + ".missing"}):
            self.assertIsNone(pb.read_uptime())
        with open(fh.name, "w") as out:
            out.write("junk")
        with mock.patch.dict(os.environ, {"SINKO_UPTIME_FILE": fh.name}):
            self.assertIsNone(pb.read_uptime())


class ClockHoldTests(Fixture):
    """A board without a clock starts with an old time: the scheduler must hold the state, not flip it."""

    def controller(self, trusted):
        return pb.Controller(self.api, self.catalog, "https://lists.example/l", clock_trusted=lambda: trusted[0])

    def enabled(self, name):
        return self.group(name)["enabled"]

    def bedtime(self, **extra):
        sched = {"enabled": True, "start": "21:00", "end": "06:00", "days": [0, 1, 2, 3, 4, 5, 6]}
        self.set_state(lambda s: s.update(schedule=sched, **extra))

    def test_bedtime_is_not_started_by_a_clock_that_is_not_believed_yet(self):
        self.bedtime()
        trusted = [False]
        ctl = self.controller(trusted)
        writes = self.store.writes
        self.assertEqual(ctl.tick(dt.datetime(2026, 9, 17, 22, 0)), [])
        self.assertFalse(self.enabled("pb-offline"))
        self.assertEqual(self.store.writes, writes, "the state is not even rewritten")
        trusted[0] = True
        self.assertTrue(ctl.tick(dt.datetime(2026, 9, 17, 22, 0)))
        self.assertTrue(self.enabled("pb-offline"))

    def test_bedtime_is_not_ended_by_an_old_time_that_says_it_is_afternoon(self):
        # The box lost power at night and restarts believing it is 14:00 of an earlier day.
        self.bedtime(scheduleActive=True)
        self.api.put_group("pb-offline", "", True)
        trusted = [False]
        ctl = self.controller(trusted)
        self.assertEqual(ctl.tick(dt.datetime(2026, 9, 15, 14, 0)), [])
        self.assertTrue(self.enabled("pb-offline"), "the children's internet stays off")
        self.assertTrue(self.state()["scheduleActive"])
        trusted[0] = True                              # the time server answered: it is 23:10 for real
        ctl.tick(dt.datetime(2026, 9, 17, 23, 10))
        self.assertTrue(self.enabled("pb-offline"))
        self.assertTrue(self.state()["scheduleActive"])

    def test_a_timer_is_not_ended_by_an_old_time_either(self):
        until = dt.datetime(2026, 9, 17, 16, 0).timestamp()
        self.set_state(lambda s: s.update(timer={"mode": "free", "until": until,
                                                 "snapshot": {"services": {"youtube": True}, "offline": False}}))
        trusted = [False]
        ctl = self.controller(trusted)
        self.assertEqual(ctl.tick(dt.datetime(2026, 9, 17, 17, 0)), [], "past the end by the box's time, which is not believed")
        self.assertIsNotNone(self.state()["timer"])
        trusted[0] = True
        self.assertTrue(ctl.tick(dt.datetime(2026, 9, 17, 17, 0)))
        self.assertIsNone(self.state()["timer"])

    def test_without_a_guard_the_tick_behaves_as_before(self):
        self.bedtime()
        ctl = pb.Controller(self.api, self.catalog, "https://lists.example/l")
        self.assertTrue(ctl.tick(dt.datetime(2026, 9, 17, 22, 0)))
        self.assertTrue(self.enabled("pb-offline"))

    def test_the_guard_does_not_stop_the_requests_of_the_page(self):
        # Held rules are not held requests: a parent who presses "Restart" is obeyed whatever the clock says.
        self.set_state(lambda s: s["power"].update(request=1, action="reboot"))
        self.clock_trusted = False
        self.settle()
        self.assertEqual(self.power_calls, ["reboot"])

    def test_the_real_guard_drives_both_the_tick_and_the_side_jobs(self):
        self.bedtime()
        guard = pb.ClockGuard(uptime=lambda: 30.0, synchronized=lambda: False)
        ctl = pb.Controller(self.api, self.catalog, "https://lists.example/l", clock_trusted=guard.trusted)
        self.set_state(lambda s: s["update"].update(auto=True, latest="3.1.0"))
        m = pb.Maintenance(self.api, config_path=self.config_path, monotonic=lambda: self.mono, job_factory=InlineJob,
                           clock_ok=guard.trusted, check=self.fake_check, start_runner=self.fake_runner,
                           power=self.fake_power, default_ip=lambda: self.ip, apply_address=self.fake_apply,
                           ping=self.fake_ping, body=lambda: "BODY")
        night = dt.datetime(2026, 9, 18, 3, 30)
        with self.assertLogs("sinko", level="INFO"):
            self.assertEqual(ctl.tick(night), [])
            m.run(night)
            m.run(night)
        self.assertEqual(self.runner_calls, [], "no automatic update on a clock nobody believes")
        self.assertFalse(self.enabled("pb-offline"))

    def test_the_scheduler_loop_hands_the_guard_to_both(self):
        seen = {}

        class FakeController:
            def __init__(self, api, catalog, clock_trusted=None):
                seen["controller"] = clock_trusted

            def tick(self):
                return []

        class FakeMaintenance:
            api = None

            def __init__(self, clock_ok=None, **kw):
                seen["maintenance"] = clock_ok

            def run(self, now):
                return []
        stop = {}

        def fake_sleep(seconds):
            stop["handler"]()
        with mock.patch.object(pb.signal, "signal", lambda sig, handler: stop.update(handler=handler)), \
                mock.patch.object(pb.time, "sleep", fake_sleep), mock.patch.object(pb, "Controller", FakeController), \
                mock.patch.object(pb, "Maintenance", FakeMaintenance), mock.patch.object(pb, "Api", lambda: mock.Mock()), \
                mock.patch.object(pb, "load_catalog", return_value={}):
            pb.cmd_run(None)
        self.assertEqual(seen["controller"].__self__, seen["maintenance"].__self__, "one guard, shared")
        self.assertIsInstance(seen["controller"].__self__, pb.ClockGuard)


if __name__ == "__main__":
    unittest.main()
