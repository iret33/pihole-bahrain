"""`pihole -g` started by Sinko: one run at a time, on the lock /var/lib/sinko/gravity.lock (the unit that refreshes the
lists nightly takes the same one with flock(1)). Pi-hole's gravity.sh has no lock of its own: two runs that overlap leave
a mix of both in the live database (a service that should be blocked can stay reachable until the next run, while every
check says all is well). Nothing here runs pihole: `subprocess.run` is a fake that records what it was asked."""
import fcntl
import importlib.machinery
import importlib.util
import logging
import os
import re
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

loader = importlib.machinery.SourceFileLoader("sinko_cli", os.path.join(ROOT, "bin", "sinko"))
spec = importlib.util.spec_from_loader("sinko_cli", loader)
pb = importlib.util.module_from_spec(spec)
loader.exec_module(pb)


class GravityLockTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.state = os.path.join(tmp.name, "state")
        env = mock.patch.dict(os.environ, {"SINKO_STATE_DIR": self.state})
        env.start()
        self.addCleanup(env.stop)
        poll = mock.patch.object(pb, "GRAVITY_POLL", 0.01)
        poll.start()
        self.addCleanup(poll.stop)
        self.lock_path = os.path.join(self.state, "gravity.lock")
        self.runs = []
        self.running = 0
        self.most_at_once = 0
        self.gate = threading.Lock()

    def fake_run(self, argv, **kw):
        with self.gate:
            self.running += 1
            self.most_at_once = max(self.most_at_once, self.running)
            self.runs.append((argv, kw))
        try:
            time.sleep(getattr(self, "run_seconds", 0))
            if getattr(self, "run_fails", False):
                raise subprocess.CalledProcessError(1, argv)
        finally:
            with self.gate:
                self.running -= 1
        return subprocess.CompletedProcess(argv, 0)

    def patched(self):
        return mock.patch.object(pb.subprocess, "run", self.fake_run)

    def lock_is_held(self):
        fd = os.open(self.lock_path, os.O_RDWR)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        finally:
            os.close(fd)
        return False

    def hold(self):
        """Another run's hold on the lock (what flock(1) in the systemd unit would take)."""
        fd = pb.open_lock_file("gravity.lock")
        fcntl.flock(fd, fcntl.LOCK_EX)
        self.addCleanup(lambda: os.close(fd) if self.holder_open(fd) else None)
        return fd

    @staticmethod
    def holder_open(fd):
        try:
            os.fstat(fd)
            return True
        except OSError:
            return False

    # ----- the lock itself -----
    def test_the_lock_is_the_file_the_unit_names_in_the_private_state_folder(self):
        self.assertEqual(os.path.join(pb.DEFAULT_STATE_DIR, pb.GRAVITY_LOCK_NAME), "/var/lib/sinko/gravity.lock")
        with mock.patch.dict(os.environ):
            os.environ.pop("SINKO_STATE_DIR")
            self.assertEqual(pb.state_path(pb.GRAVITY_LOCK_NAME), "/var/lib/sinko/gravity.lock")
        with self.patched():
            pb.Controller.gravity()
        self.assertEqual(stat.S_IMODE(os.stat(self.lock_path).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(self.state).st_mode), 0o700)

    def test_pihole_runs_while_the_lock_is_held_and_not_after(self):
        seen = []

        def run(argv, **kw):
            seen.append((argv, kw, self.lock_is_held()))
            return subprocess.CompletedProcess(argv, 0)
        with mock.patch.object(pb.subprocess, "run", run):
            pb.Controller.gravity()
        (argv, kw, held), = seen
        self.assertEqual(argv, ["pihole", "-g"])
        self.assertTrue(held, "the lock is held for the whole of the run")
        self.assertIs(kw["check"], True)
        self.assertEqual(kw["timeout"], 1800)
        self.assertFalse(self.lock_is_held(), "and let go of afterwards")

    def test_a_failing_run_raises_as_before_and_lets_go_of_the_lock(self):
        self.run_fails = True
        with self.patched(), self.assertRaises(subprocess.CalledProcessError):
            pb.Controller.gravity()
        self.assertFalse(self.lock_is_held())

    def test_a_run_that_takes_too_long_raises_as_before_and_lets_go_of_the_lock(self):
        def run(argv, **kw):
            raise subprocess.TimeoutExpired(argv, kw["timeout"])
        with mock.patch.object(pb.subprocess, "run", run), self.assertRaises(subprocess.TimeoutExpired):
            pb.Controller.gravity()
        self.assertFalse(self.lock_is_held())

    # ----- two runs -----
    def test_a_second_run_waits_for_the_one_that_holds_the_lock_and_then_goes(self):
        fd = self.hold()
        done = threading.Event()

        def second():
            with self.patched():
                pb.Controller.gravity()
            done.set()
        thread = threading.Thread(target=second, daemon=True)
        with self.assertLogs("sinko", level="INFO") as logs:
            thread.start()
            self.assertFalse(done.wait(0.3), "it waits")
            self.assertEqual(self.runs, [], "and has not started pihole")
            os.close(fd)                                          # the first run is over
            self.assertTrue(done.wait(5), "and goes on as soon as the lock is free")
        thread.join(5)
        self.assertEqual(len(self.runs), 1)
        self.assertEqual(len([r for r in logs.records if "waiting for it" in r.getMessage()]), 1, "and says so once")

    def test_it_gives_up_after_the_time_it_may_wait_the_way_a_run_that_takes_too_long_does(self):
        self.hold()
        started = time.monotonic()
        with self.patched(), self.assertRaises(subprocess.TimeoutExpired):
            pb.Controller.gravity(wait=0.2)
        self.assertGreaterEqual(time.monotonic() - started, 0.2)
        self.assertEqual(self.runs, [], "pihole was not started without the lock")
        self.assertEqual(pb.GRAVITY_LOCK_WAIT, 1800, "the wait in the real thing is 30 minutes")

    def test_runs_started_from_several_places_at_once_never_overlap(self):
        self.run_seconds = 0.05
        errors = []

        def start():
            try:
                pb.Controller.gravity()
            except Exception as err:          # pragma: no cover - reported below
                errors.append(err)
        threads = [threading.Thread(target=start) for _ in range(6)]
        with self.patched():                  # once, around all of them: a patch made and undone in each thread would race
            for t in threads:
                t.start()
            for t in threads:
                t.join(30)
        self.assertEqual(errors, [])
        self.assertEqual(len(self.runs), 6)
        self.assertEqual(self.most_at_once, 1, "pihole -g is never started while another run is going")

    def test_a_lock_that_cannot_be_opened_does_not_stop_the_run_but_says_so(self):
        os.makedirs(self.state, mode=0o700)
        os.symlink(os.path.join(self.state, "elsewhere"), self.lock_path)      # a link in place of the file: refused
        with self.patched(), self.assertLogs("sinko", level="WARNING") as logs:
            pb.Controller.gravity()
        self.assertEqual(len(self.runs), 1)
        self.assertIn("without it", logs.records[0].getMessage())
        self.assertFalse(os.path.exists(os.path.join(self.state, "elsewhere")), "the link was not followed")

    # ----- every place that starts gravity goes through it -----
    def test_pihole_g_is_started_in_one_place_only(self):
        with open(os.path.join(ROOT, "bin", "sinko"), encoding="utf-8") as fh:
            source = fh.read()
        self.assertEqual(len(re.findall(r'\["pihole",\s*"-g"\]', source)), 1, "every run goes through Controller.gravity")
        self.assertNotIn("os.system", source)

    def test_setup_with_gravity_goes_through_the_locked_run(self):
        # `sinko setup` (the installer and every update), the self-repair and `Controller.setup` itself all end here.
        httpd, store = fake_release.serve_pihole()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        api = pb.Api("http://127.0.0.1:%d" % httpd.server_port, password=mock_pihole.PASSWORD)
        api.login()
        catalog = pb.load_catalog(os.path.join(ROOT, "lists"))
        seen = []

        def run(argv, **kw):
            seen.append((argv, self.lock_is_held()))
            return subprocess.CompletedProcess(argv, 0)
        with mock.patch.object(pb.subprocess, "run", run):
            pb.Controller(api, catalog, "https://lists.example/l").setup(run_gravity=True, legacy=False)
        self.assertEqual(seen, [(["pihole", "-g"], True)])
        with open(os.path.join(ROOT, "bin", "sinko"), encoding="utf-8") as fh:
            source = fh.read()
        self.assertEqual(source.count("ctl.gravity()"), 1, "cmd_setup calls the same function")


if __name__ == "__main__":
    unittest.main()
