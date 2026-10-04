"""The scheduler's side jobs (class Maintenance): update check and install, request markers, power requests,
address watch, reconciling the update result. Pi-hole is the in-memory mock; everything slow or dangerous (network,
systemd, reboot, pihole-FTL) is a fake that records what it was asked, so no test can reboot or update this machine."""
import datetime as dt
import importlib.machinery
import importlib.util
import json
import logging
import os
import socket
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
        env = mock.patch.dict(os.environ, {"SINKO_STATE_DIR": self.state_dir})
        env.start()
        self.addCleanup(env.stop)
        for key in ("SINKO_RELEASE_BASE", "SINKO_RELEASE_API", "SINKO_REF", "SINKO_REPO", "SINKO_REPO_SLUG",
                    "SINKO_TELEMETRY_URL"):
            os.environ.pop(key, None)
        self.reached = []
        for name in ("run_power", "start_update_runner", "apply_new_address", "default_route_ipv4", "check_for_update",
                     "send_ping", "telemetry_body"):
            patch = mock.patch.object(pb, name, side_effect=lambda *a, _n=name, **k: self.reached.append(_n))
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
        self.m = pb.Maintenance(self.api, config_path=self.config_path, monotonic=lambda: self.mono,
                                job_factory=InlineJob, clock_ok=lambda: self.clock_trusted,
                                check=self.fake_check, start_runner=self.fake_runner, power=self.fake_power,
                                default_ip=lambda: self.ip, apply_address=self.fake_apply, ping=self.fake_ping,
                                body=lambda: "BODY", jitter=self.fake_jitter)

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
        self.set_state(lambda s: s["update"].update(request=1700000000123, latest="3.1.0"))
        before = self.state()["update"]
        actions = self.pass_()
        self.assertIn("update requested by the page", actions)
        u = self.state()["update"]
        self.assertEqual((u["status"], u["from"], u["to"], u["request"], u["error"]), ("running", "3.0.0", "3.1.0", None, None))
        self.assertEqual(u["at"], self.now.timestamp())
        self.assertEqual(pb.load_handled(), {"update": 1700000000123})
        self.assertEqual(before["status"], "idle")
        state_at_launch, handled_at_launch = self.seen_at_call["runner"]
        self.assertEqual(state_at_launch["update"]["status"], "running", "the state said running before the runner started")
        self.assertIsNone(state_at_launch["update"]["request"], "and the request was already cleared")
        self.assertEqual(handled_at_launch, {"update": 1700000000123}, "and the marker was already stored")
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

    def test_running_for_over_twenty_minutes_with_no_runner_is_marked_failed(self):
        self.running(at=self.now.timestamp() - 21 * 60)
        actions = self.pass_()
        u = self.state()["update"]
        self.assertEqual((u["status"], u["error"]), ("failed", "The update did not finish."))
        self.assertIn("update did not finish", actions)

    def test_a_run_that_is_still_alive_is_never_called_stuck(self):
        self.running(at=self.now.timestamp() - 3 * 3600)
        holder = pb.RunLock()
        self.assertTrue(holder.acquire())
        self.addCleanup(holder.release)
        self.pass_()
        self.assertEqual(self.state()["update"]["status"], "running")

    def test_a_young_run_is_left_alone(self):
        self.running(at=self.now.timestamp() - 19 * 60)
        writes = self.store.writes
        self.pass_()
        self.assertEqual(self.state()["update"]["status"], "running")
        self.assertEqual(self.store.writes, writes)

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
        self.set_state(lambda s: s["power"].update(request=1700000000999, action="reboot"))
        actions = self.pass_()
        self.assertIn("power request: reboot", actions)
        state_then, handled_then = self.seen_at_call["power"]
        self.assertEqual(state_then["power"], {"request": None, "action": None}, "cleared BEFORE the reboot")
        self.assertEqual(handled_then, {"power": 1700000000999}, "stored BEFORE the reboot")
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

    def test_power_waits_while_an_update_is_installing(self):
        self.set_state(lambda s: s["update"].update(status="running", at=self.now.timestamp()))
        self.set_state(lambda s: s["power"].update(request=5, action="reboot"))
        self.settle()
        self.assertEqual(self.power_calls, [])
        self.assertEqual(self.state()["power"]["request"], 5, "kept for later")
        self.assertEqual(pb.load_handled(), {})
        self.set_state(lambda s: s["update"].update(status="ok"))
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

            def __init__(self, clock_ok=None):
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

            def __init__(self, clock_ok=None):
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
        self.assertEqual(self.power_calls, ["reboot"])
        self.assertEqual(self.rules(), json.dumps([g for g in self.store.groups if g["name"] != "pb-state"], sort_keys=True))


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

            def __init__(self, clock_ok=None):
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
