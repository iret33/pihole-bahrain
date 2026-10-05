"""The scheduler's loop (class Scheduler, `sinko run`): it must survive anything Pi-hole answers and anything the shared
state holds, it must give every Pi-hole session back, and it leaves a pulse for the self-check. Pi-hole is the in-memory
mock; time never passes (a pass returns how long it would wait)."""
import importlib.machinery
import importlib.util
import json
import logging
import os
import random
import shutil
import stat
import unittest
from unittest import mock

import fake_release
import mock_pihole
import test_maintenance as tm

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
LISTS = os.path.join(ROOT, "lists")

loader = importlib.machinery.SourceFileLoader("sinko_cli", os.path.join(ROOT, "bin", "sinko"))
spec = importlib.util.spec_from_loader("sinko_cli", loader)
pb = importlib.util.module_from_spec(spec)
loader.exec_module(pb)

REAL_LOAD_CATALOG = pb.load_catalog


class LoopFixture(tm.Fixture):
    """The maintenance fixture, plus what the loop itself needs: a way to log in (its own Api), a clock that is believed."""

    def setUp(self):
        super().setUp()
        with open(os.path.join(self.tmp, "pw"), "w") as fh:
            fh.write(mock_pihole.PASSWORD)
        env = mock.patch.dict(os.environ, {"SINKO_API_URL": "http://127.0.0.1:%d" % self.httpd.server_port})
        env.start()
        self.addCleanup(env.stop)
        for patch in (mock.patch.object(pb, "CLI_PW_FILE", os.path.join(self.tmp, "pw")),
                      mock.patch.object(pb, "load_catalog", lambda *a, **k: self.catalog)):
            patch.start()
            self.addCleanup(patch.stop)
        self.gate_clock = [0.0]
        self.repairs = []
        self.sched = pb.Scheduler(clock=pb.ClockGuard(uptime=lambda: None, synchronized=lambda: None), maintenance=self.m,
                                  gate=pb.LogGate(monotonic=lambda: self.gate_clock[0]),
                                  repair=lambda: self.repairs.append(1) or [], monotonic=lambda: self.gate_clock[0])
        self.addCleanup(self.sched.close)

    def open_sessions(self):
        return len(self.store.sessions)


class GoodPassTests(LoopFixture):
    def test_a_good_pass_ticks_runs_the_side_jobs_and_leaves_a_pulse(self):
        self.set_state(lambda s: s["update"].update(request=5))
        wait = self.sched.step()
        self.assertEqual(wait, pb.TICK_SECONDS)
        self.assertEqual(self.runner_calls, ["running"], "the side jobs ran after the tick")
        beat = pb.read_heartbeat()
        self.assertEqual((beat["pid"], beat["version"], beat["ticks"]), (os.getpid(), pb.VERSION, 1))
        self.sched.step()
        self.assertEqual(pb.read_heartbeat()["ticks"], 2)

    def test_the_session_is_kept_between_good_passes_and_given_back_at_the_end(self):
        for _ in range(5):
            self.sched.step()
        self.assertEqual(self.open_sessions(), 2, "the test's own login and the scheduler's one")
        before = len(self.store.deleted_sessions)
        self.sched.close()
        self.assertEqual(len(self.store.deleted_sessions), before + 1)
        self.assertEqual(self.open_sessions(), 1)
        self.sched.close()                                  # twice is fine

    def test_a_pass_while_the_clock_is_not_believed_still_counts_as_alive(self):
        sched = pb.Scheduler(clock=pb.ClockGuard(uptime=lambda: 30.0, synchronized=lambda: False), maintenance=self.m)
        self.addCleanup(sched.close)
        with self.assertLogs("sinko", level="INFO"):
            sched.step()
        self.assertEqual(pb.read_heartbeat()["ticks"], 1, "holding bedtime on purpose is not a failure")

    def test_a_pulse_that_cannot_be_written_is_logged_not_raised(self):
        with mock.patch.object(pb, "write_heartbeat", side_effect=OSError("read-only file system")), \
                self.assertLogs("sinko", level="WARNING") as logs:
            self.assertEqual(self.sched.step(), pb.TICK_SECONDS)
            self.sched.step()
        self.assertEqual(len(logs.records), 1, "once")


class SeatLeakTests(LoopFixture):
    """FTL has 16 seats (webserver.api.max_sessions). A loop that fails and starts over must not use them up."""

    def setUp(self):
        super().setUp()
        self.store.max_sessions = 16

    def test_a_tick_that_always_raises_never_holds_more_than_one_seat(self):
        waits = []
        with mock.patch.object(pb.Controller, "tick", side_effect=AttributeError("'list' object has no attribute 'items'")), \
                self.assertLogs("sinko", level="WARNING") as logs:
            for _ in range(60):
                waits.append(self.sched.step())
                self.assertLessEqual(self.open_sessions(), 2, "the test's login plus at most the scheduler's own")
        self.assertEqual(self.open_sessions(), 1, "the scheduler's seat was given back after the last failure")
        self.assertGreaterEqual(len(self.store.deleted_sessions), 59)
        self.assertEqual(waits[:8], [5, 10, 20, 40, 80, 120, 120, 120], "backs off, never beyond two minutes")
        self.assertEqual(len(logs.records), 1, "the same failure is logged once, not on every retry")
        self.api.login()                                    # a parent signing in at this moment gets a seat
        self.assertTrue(self.api.sid)

    def test_a_transport_error_gives_the_seat_back_before_trying_again(self):
        with mock.patch.object(pb.Controller, "tick", side_effect=pb.ApiError(500, "FTL is restarting")), \
                self.assertLogs("sinko", level="WARNING"):
            for _ in range(40):
                self.sched.step()
        self.assertEqual(self.open_sessions(), 1)

    def test_a_logout_that_fails_does_not_stop_the_loop(self):
        self.sched.step()
        with mock.patch.object(self.sched.api, "logout", side_effect=OSError("connection reset")), \
                mock.patch.object(pb.Controller, "tick", side_effect=pb.ApiError(503, "x")), \
                self.assertLogs("sinko", level="WARNING"):
            self.assertEqual(self.sched.step(), 5)
        self.assertIsNone(self.sched.api)

    def test_the_real_loop_gives_the_seat_back_when_it_is_stopped_in_the_middle_of_failures(self):
        stop = {}
        sleeps = []

        def fake_sleep(seconds):
            sleeps.append(seconds)
            if len(sleeps) >= 50:
                stop["handler"]()
        guard = pb.ClockGuard(uptime=lambda: None, synchronized=lambda: None)
        with mock.patch.object(pb.signal, "signal", lambda sig, handler: stop.update(handler=handler)), \
                mock.patch.object(pb.time, "sleep", fake_sleep), \
                mock.patch.object(pb.Controller, "tick", side_effect=TypeError("boom")), \
                mock.patch.object(pb, "Maintenance", lambda clock_ok=None: self.m), \
                mock.patch.object(pb, "ClockGuard", lambda: guard), \
                self.assertLogs("sinko", level="INFO") as logs:
            self.assertEqual(pb.cmd_run(None), 0)
        self.assertEqual(self.open_sessions(), 1)
        self.assertIn("scheduler stopped", [r.getMessage() for r in logs.records])


class BugsAreSurvivedTests(LoopFixture):
    def test_a_bug_in_the_tick_still_lets_the_page_update_and_restart_the_box(self):
        # The way out of a release whose scheduler fails is the next release: "Update now" must still work.
        self.set_state(lambda s: s["update"].update(request=1))
        self.set_state(lambda s: s["power"].update(request=2, action="reboot"))
        with mock.patch.object(pb.Controller, "tick", side_effect=TypeError("bug")), self.assertLogs("sinko", level="WARNING"):
            wait = self.sched.step()
        self.assertEqual(wait, 5)
        self.assertEqual(self.runner_calls, ["running"])
        self.assertFalse(self.m.tick_ok, "and the side jobs were told that the pass failed")
        self.assertIsNone(self.sched.api, "the session was given back after the side jobs")

    def test_when_pihole_does_not_answer_the_side_jobs_are_left_alone(self):
        self.set_state(lambda s: s["update"].update(request=1))
        with mock.patch.object(pb.Controller, "tick", side_effect=pb.ApiError(502, "bad gateway")), \
                self.assertLogs("sinko", level="WARNING"):
            self.sched.step()
        self.assertEqual(self.runner_calls, [])

    def test_a_pass_that_raises_anything_at_all_is_contained(self):
        for error in (AttributeError("x"), TypeError("x"), KeyError("x"), IndexError("x"), ZeroDivisionError("x"),
                      RecursionError("x"), MemoryError("x"), UnicodeDecodeError("utf-8", b"\xff", 0, 1, "x"),
                      pb.ApiError(0, "x"), OSError("x"), ValueError("x"), json.JSONDecodeError("x", "", 0)):
            with mock.patch.object(pb.Controller, "tick", side_effect=error), self.assertLogs("sinko", level="WARNING"):
                self.assertGreater(self.sched.step(), 0, repr(error))

    def test_a_maintenance_that_raises_is_contained_too(self):
        with mock.patch.object(self.m, "run", side_effect=RuntimeError("bug in a side job")), \
                self.assertLogs("sinko", level="WARNING") as logs:
            self.assertEqual(self.sched.step(), pb.TICK_SECONDS)
            self.sched.step()
        self.assertEqual(len(logs.records), 1)
        self.assertEqual(pb.read_heartbeat()["ticks"], 2, "the rules were still applied and the pulse written")

    def test_a_failure_is_logged_again_after_half_an_hour_and_a_new_one_at_once(self):
        with mock.patch.object(pb.Controller, "tick", side_effect=TypeError("same")), \
                self.assertLogs("sinko", level="WARNING") as logs:
            self.sched.step()
            self.sched.step()
            self.assertEqual(len(logs.records), 1)
            self.gate_clock[0] += 1799
            self.sched.step()
            self.assertEqual(len(logs.records), 1)
            self.gate_clock[0] += 2
            self.sched.step()
            self.assertEqual(len(logs.records), 2, "after 30 minutes")
        with mock.patch.object(pb.Controller, "tick", side_effect=KeyError("different")), \
                self.assertLogs("sinko", level="WARNING") as logs:
            self.sched.step()
        self.assertEqual(len(logs.records), 1, "a different failure is news")

    def test_a_bug_is_logged_with_its_traceback_and_a_transport_error_without(self):
        with mock.patch.object(pb.Controller, "tick", side_effect=TypeError("bug")), self.assertLogs("sinko", level="WARNING") as logs:
            self.sched.step()
        self.assertIsNotNone(logs.records[0].exc_info)
        with mock.patch.object(pb.Controller, "tick", side_effect=pb.ApiError(500, "down")), \
                self.assertLogs("sinko", level="WARNING") as logs:
            self.sched.step()
        self.assertIsNone(logs.records[0].exc_info)


class CatalogTests(LoopFixture):
    """services.json or a list file that cannot be read (an update cut off by a power failure) must not make the
    scheduler crash and be restarted every ten seconds."""

    def test_an_unreadable_catalog_does_not_end_the_process_and_the_side_jobs_still_run(self):
        self.set_state(lambda s: s["update"].update(request=1))
        with mock.patch.object(pb, "load_catalog", side_effect=SystemExit("missing list file: /opt/sinko/lists/guard.txt")), \
                self.assertLogs("sinko", level="WARNING") as logs:
            wait = self.sched.step()
            self.sched.step()
        self.assertEqual(wait, 5)
        self.assertEqual(self.runner_calls, ["running"], "a parent can still ask for an update, which may repair it")
        self.assertEqual(len(logs.records), 1)
        self.assertIn("missing list file", logs.records[0].getMessage())
        self.assertIsNone(pb.read_heartbeat(), "no rules were applied, so no pulse")

    def test_a_catalog_that_is_garbled_json_is_an_error_not_a_crash(self):
        for error in (json.JSONDecodeError("Expecting value", "", 0), KeyError("services"), OSError("I/O error"),
                      TypeError("not a dict")):
            with mock.patch.object(pb, "load_catalog", side_effect=error), self.assertLogs("sinko", level="WARNING"):
                self.assertGreater(self.sched.step(), 0, repr(error))

    def test_when_the_catalog_is_readable_again_the_scheduler_carries_on_by_itself(self):
        with mock.patch.object(pb, "load_catalog", side_effect=SystemExit("missing list file")), \
                self.assertLogs("sinko", level="WARNING"):
            self.sched.step()
        self.assertEqual(self.sched.step(), pb.TICK_SECONDS)
        self.assertEqual(pb.read_heartbeat()["ticks"], 1)

    def test_load_catalog_safely_turns_the_command_line_exit_into_an_exception(self):
        with mock.patch.object(pb, "load_catalog", side_effect=SystemExit("services.json: duplicate service id")):
            with self.assertRaises(pb.CatalogError) as caught:
                pb.load_catalog_safely()
        self.assertIn("duplicate service id", str(caught.exception))
        with mock.patch.object(pb, "load_catalog", return_value={"services": []}):
            self.assertEqual(pb.load_catalog_safely(), {"services": []})


class RepairTests(LoopFixture):
    """An update cut short by a power failure can leave a list or services.json empty or missing. The scheduler puts
    back what cannot be read from a copy of this version that is whole, and touches nothing else."""

    def setUp(self):
        super().setUp()
        self.app = os.path.join(self.tmp, "app")
        self.lists = os.path.join(self.app, "lists")
        os.makedirs(self.lists)
        for name in os.listdir(LISTS):
            shutil.copy(os.path.join(LISTS, name), os.path.join(self.lists, name))
        env = mock.patch.object(pb, "APP_DIR", self.app)
        env.start()
        self.addCleanup(env.stop)
        # the real catalog reader again (the loop fixture gives the scheduler a ready-made one), reading this box's folder
        patch = mock.patch.object(pb, "load_catalog", lambda lists_dir=None: REAL_LOAD_CATALOG(lists_dir or self.lists))
        patch.start()
        self.addCleanup(patch.stop)

    def put_source(self, version=None):
        src = os.path.join(self.app, "src")
        shutil.copytree(LISTS, os.path.join(src, "lists"))
        with open(os.path.join(src, "VERSION"), "w") as fh:
            fh.write((version or pb.VERSION) + "\n")
        return src

    def put_cached(self, version=None, broken=False):
        version = version or pb.VERSION
        extra = {"sinko/lists/" + n: (self.raw(os.path.join(LISTS, n)), 0o644) for n in os.listdir(LISTS)}
        data = fake_release.build_release(version, extra=extra)
        os.makedirs(pb.cache_dir(), exist_ok=True)
        with open(pb.cache_file(pb.VERSION), "wb") as fh:
            fh.write(data[:len(data) // 2] if broken else data)

    @staticmethod
    def raw(path):
        with open(path, "rb") as fh:
            return fh.read()

    def read(self, name):
        return self.raw(os.path.join(self.lists, name))

    def test_nothing_is_written_when_everything_reads(self):
        self.put_source()
        before = {n: os.stat(os.path.join(self.lists, n)).st_ino for n in os.listdir(self.lists)}
        self.assertEqual(pb.repair_catalog_files(), [])
        self.assertEqual({n: os.stat(os.path.join(self.lists, n)).st_ino for n in os.listdir(self.lists)}, before)

    def test_a_missing_empty_or_garbled_file_is_put_back_and_the_others_are_not_touched(self):
        self.put_source()
        os.unlink(os.path.join(self.lists, "tiktok.txt"))
        open(os.path.join(self.lists, "guard.txt"), "w").close()
        with open(os.path.join(self.lists, "services.json"), "w") as fh:
            fh.write('{"services": [')
        untouched = os.stat(os.path.join(self.lists, "youtube.txt")).st_ino
        fixed = pb.repair_catalog_files()
        self.assertEqual(sorted(fixed), ["guard.txt", "services.json", "tiktok.txt"])
        for name in fixed:
            self.assertEqual(self.read(name), self.raw(os.path.join(LISTS, name)), name)
            self.assertEqual(stat.S_IMODE(os.stat(os.path.join(self.lists, name)).st_mode), 0o644)
        self.assertEqual(os.stat(os.path.join(self.lists, "youtube.txt")).st_ino, untouched)
        self.assertEqual(pb.load_catalog(self.lists)["services"][0]["id"], pb.load_catalog(LISTS)["services"][0]["id"])
        self.assertEqual([n for n in os.listdir(self.lists) if n.startswith(".")], [], "no temporary file is left")

    def test_a_file_that_is_there_and_readable_is_never_replaced_even_when_it_differs(self):
        self.put_source()
        with open(os.path.join(self.lists, "youtube.txt"), "a") as fh:
            fh.write("||added.example^\n")
        os.unlink(os.path.join(self.lists, "guard.txt"))
        self.assertEqual(pb.repair_catalog_files(), ["guard.txt"])
        self.assertIn(b"added.example", self.read("youtube.txt"))

    def test_the_source_tree_of_another_version_is_not_used(self):
        self.put_source("2.9.0")
        os.unlink(os.path.join(self.lists, "guard.txt"))
        self.assertEqual(pb.repair_catalog_files(), [])
        self.assertFalse(os.path.exists(os.path.join(self.lists, "guard.txt")))

    def test_without_a_whole_source_tree_the_stored_copy_is_used_and_nothing_is_left_behind(self):
        src = self.put_source()
        os.unlink(os.path.join(src, "lists", "guard.txt"))        # the tree is itself damaged
        self.put_cached()
        os.unlink(os.path.join(self.lists, "guard.txt"))
        self.assertEqual(pb.repair_catalog_files(), ["guard.txt"])
        self.assertEqual(self.read("guard.txt"), self.raw(os.path.join(LISTS, "guard.txt")))
        self.assertEqual([n for n in os.listdir(self.app) if n.startswith(".stage-")], [])

    def test_a_stored_copy_that_is_damaged_or_of_another_version_is_no_help(self):
        os.unlink(os.path.join(self.lists, "guard.txt"))
        self.put_cached(broken=True)
        self.assertEqual(pb.repair_catalog_files(), [])
        self.put_cached(version="2.9.0")
        self.assertEqual(pb.repair_catalog_files(), [])
        self.assertEqual([n for n in os.listdir(self.app) if n.startswith(".stage-")], [])

    def test_with_nothing_to_repair_from_it_says_so_by_returning_nothing(self):
        os.unlink(os.path.join(self.lists, "guard.txt"))
        self.assertEqual(pb.repair_catalog_files(), [])

    def scheduler(self, repair):
        return pb.Scheduler(clock=pb.ClockGuard(uptime=lambda: None, synchronized=lambda: None), maintenance=self.m,
                            gate=pb.LogGate(monotonic=lambda: self.gate_clock[0]), repair=repair,
                            monotonic=lambda: self.gate_clock[0])

    def test_the_scheduler_repairs_the_lists_once_and_carries_on_in_the_same_pass(self):
        self.put_source()
        os.unlink(os.path.join(self.lists, "guard.txt"))
        sched = self.scheduler(pb.repair_catalog_files)
        self.addCleanup(sched.close)
        with self.assertLogs("sinko", level="WARNING") as logs:
            self.assertEqual(sched.step(), pb.TICK_SECONDS)
        self.assertEqual(len(logs.records), 1)
        self.assertIn("guard.txt", logs.records[0].getMessage())
        self.assertEqual(pb.read_heartbeat()["ticks"], 1)

    def test_a_repair_that_finds_nothing_is_not_tried_more_than_once_an_hour(self):
        calls = []
        sched = self.scheduler(lambda: calls.append(1) or [])
        self.addCleanup(sched.close)
        os.unlink(os.path.join(self.lists, "guard.txt"))
        with self.assertLogs("sinko", level="WARNING"):
            for _ in range(5):
                sched.step()
        self.assertEqual(len(calls), 1)
        self.gate_clock[0] += 3601
        with self.assertLogs("sinko", level="WARNING"):
            sched.step()
        self.assertEqual(len(calls), 2)

    def test_a_repair_that_raises_is_no_worse_than_none(self):
        def boom():
            raise RuntimeError("disk gone")
        sched = self.scheduler(boom)
        self.addCleanup(sched.close)
        os.unlink(os.path.join(self.lists, "guard.txt"))
        with self.assertLogs("sinko", level="WARNING"):
            self.assertEqual(sched.step(), 5)


def garbage(rng, depth=0):
    """A JSON-shaped value of any kind, from the plausible to the hostile."""
    kinds = ["none", "bool", "int", "float", "str", "weird", "list", "dict"] if depth < 3 else ["none", "bool", "int", "str"]
    kind = rng.choice(kinds)
    if kind == "none":
        return None
    if kind == "bool":
        return rng.random() < 0.5
    if kind == "int":
        return rng.choice([0, 1, -1, 7, 255, 2 ** 31, 2 ** 70, -2 ** 70, 1700000000, 1790000000123])
    if kind == "float":
        return rng.choice([0.0, -0.0, 1.5, 1e308, -1e308, 1e-320, 1700000000.5, float("nan"), float("inf"), float("-inf")])
    if kind == "str":
        return rng.choice(["", " ", "x", "3.1.0", "21:00", "reboot", "free", "failed", "https://github.com/a/b/releases",
                           "سلام", "a" * 500, "\x00", "\n", "﻿", "../..", "NaN", "{}", "[]"])
    if kind == "weird":
        return rng.choice([[], {}, [[]], {"": {}}, [None], {"a": [1, {"b": None}]}, "0" * 40, 10 ** 400 if False else 10 ** 30])
    if kind == "list":
        return [garbage(rng, depth + 1) for _ in range(rng.randint(0, 4))]
    return {rng.choice(["a", "mode", "until", "snapshot", "services", "offline", "enabled", "start", "days", "on", "online", "at",
                        "request", "action", "status", "error", "latest", "v", "done", ""]): garbage(rng, depth + 1)
            for _ in range(rng.randint(0, 4))}


STATE_KEYS = ["v", "timer", "schedule", "scheduleActive", "update", "power", "telemetry", "community", "setup"]


def garbage_state(rng):
    """A pb-state description: mostly the real shape with some fields replaced, sometimes nothing like it."""
    roll = rng.random()
    if roll < 0.08:
        return rng.choice(["", "null", "[]", "42", '"text"', "{", "not json at all", "{\"timer\": NaN}", "\x00\x01\x02", "[" * 2000])
    if roll < 0.25:
        return json.dumps(garbage(rng), allow_nan=True)
    state = json.loads(json.dumps(pb.DEFAULT_STATE))
    state["timer"] = {"mode": rng.choice(["free", "block", "x"]), "until": 1790000000,
                      "snapshot": {"services": {"youtube": True}, "offline": False}}
    for key in rng.sample(STATE_KEYS, rng.randint(1, 5)):
        state[key] = garbage(rng)
    if rng.random() < 0.5:
        for sub in ("timer", "schedule", "update", "power", "telemetry", "community", "setup"):
            if isinstance(state.get(sub), dict) and rng.random() < 0.5:
                inner = state[sub]
                for key in list(inner)[: rng.randint(0, 3)]:
                    inner[key] = garbage(rng)
    return json.dumps(state, allow_nan=True)


def check_shape(test, state):
    """parse_state's answer always has exactly the documented shape and types."""
    test.assertEqual(set(state), set(pb.DEFAULT_STATE))
    test.assertEqual(state["v"], 1)
    timer = state["timer"]
    if timer is not None:
        test.assertIn(timer["mode"], ("free", "block"))
        test.assertTrue(pb._is_number(timer["until"]))
        test.assertIsInstance(timer["snapshot"]["services"], dict)
        test.assertTrue(all(isinstance(k, str) and isinstance(v, bool) for k, v in timer["snapshot"]["services"].items()))
        test.assertIsInstance(timer["snapshot"]["offline"], bool)
    sched = state["schedule"]
    test.assertIsInstance(sched["enabled"], bool)
    test.assertTrue(pb.valid_hhmm(sched["start"]) and pb.valid_hhmm(sched["end"]))
    test.assertTrue(all(isinstance(d, int) and 0 <= d <= 6 for d in sched["days"]))
    test.assertIsInstance(state["scheduleActive"], bool)
    up = state["update"]
    test.assertEqual(set(up), set(pb.DEFAULT_STATE["update"]))
    test.assertIsInstance(up["auto"], bool)
    test.assertIn(up["status"], pb.UPDATE_STATUSES)
    for key in ("latest", "from", "to"):
        test.assertTrue(up[key] is None or pb.SEMVER_RE.fullmatch(up[key]), (key, up[key]))
    test.assertTrue(up["notes"] is None or pb.NOTES_RE.fullmatch(up["notes"]))
    test.assertTrue(pb._is_count(up["checked"]) and pb._is_count(up["at"]))
    test.assertTrue(up["error"] is None or (isinstance(up["error"], str) and 0 < len(up["error"]) <= 200))
    test.assertTrue(up["rolledBack"] is None or isinstance(up["rolledBack"], bool))
    for key in ("request", "checkRequest"):
        test.assertTrue(up[key] is None or pb._nonce(up[key]) == up[key])
    test.assertIn(state["power"]["action"], (None, "reboot", "poweroff"))
    test.assertEqual(state["power"]["action"] is None, state["power"]["request"] is None or state["power"]["action"] is None)
    test.assertTrue(state["telemetry"]["on"] is None or isinstance(state["telemetry"]["on"], bool))
    test.assertTrue(state["community"] is None or (pb._is_count(state["community"]["online"]) and pb._is_number(state["community"]["at"])))
    test.assertIsInstance(state["setup"]["done"], bool)


class FuzzTests(LoopFixture):
    """Random JSON-shaped garbage, fixed seeds. Nothing in the shared state and nothing Pi-hole answers may raise out of
    the scheduler's loop or give a state that the page's parser would not accept."""

    def test_parse_state_is_total_normalising_and_idempotent(self):
        rng = random.Random(20260404)
        for _ in range(1500):
            raw = garbage_state(rng)
            state = pb.parse_state(raw)
            check_shape(self, state)
            text = json.dumps(state, allow_nan=False, separators=(",", ":"))            # no NaN or Infinity can be written back
            self.assertEqual(pb.parse_state(text), state, raw[:200])

    def test_parse_state_accepts_non_text_without_raising(self):
        for raw in (None, 0, 1.5, [], {}, b"{}", object, True):
            try:
                state = pb.parse_state(raw)
            except TypeError:
                continue                                    # only text is ever stored in a group description
            check_shape(self, state)

    def test_the_loop_survives_garbage_in_the_state_and_in_pihole_answers(self):
        rng = random.Random(7)
        self.store.max_sessions = 16
        real_request = pb.Api.request

        def odd_answers(api, method, path, body=None, _retry=True):
            payload = real_request(api, method, path, body, _retry)
            if method != "GET" or rng.random() > 0.35:
                return payload
            roll = rng.random()
            if roll < 0.15 or not isinstance(payload, dict):
                return garbage(rng)
            payload = json.loads(json.dumps(payload))
            for key, value in list(payload.items()):
                if isinstance(value, list):
                    payload[key] = [garbage(rng, 2) if rng.random() < 0.3 else
                                    ({k: garbage(rng, 2) if rng.random() < 0.3 else v for k, v in row.items()}
                                     if isinstance(row, dict) else row) for row in value]
                elif rng.random() < 0.3:
                    payload[key] = garbage(rng)
            return payload
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)
        waits = set()
        with mock.patch.object(pb.Api, "request", odd_answers):
            for _ in range(200):
                next(g for g in self.store.groups if g["name"] == "pb-state")["comment"] = garbage_state(rng)
                self.advance(rng.choice([1, 15, 300, 3600]))
                wait = self.sched.step()
                self.assertTrue(isinstance(wait, (int, float)) and 0 < wait <= 120, wait)
                waits.add(wait)
                self.assertLessEqual(self.open_sessions(), 2)
        self.assertGreater(len(waits), 1, "some passes failed and some did not: the fuzz reached both paths")
        self.sched.close()
        self.assertEqual(self.open_sessions(), 1, "no seat was lost on the way")

    def test_maintenance_alone_survives_garbage_in_the_state(self):
        rng = random.Random(99)
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)
        for _ in range(150):
            next(g for g in self.store.groups if g["name"] == "pb-state")["comment"] = garbage_state(rng)
            self.advance(rng.choice([1, 15, 300, 3600]))
            self.assertIsInstance(self.pass_(), list)
        self.assertEqual(self.reached, [], "and none of it reached anything real")


if __name__ == "__main__":
    unittest.main()
