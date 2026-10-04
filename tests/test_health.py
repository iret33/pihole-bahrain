"""`sinko status` (version, update block, counter answer) and `sinko doctor` (version, pending update, disk space,
CPU temperature, clock, anonymous counter)."""
import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
import subprocess
import tempfile
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

MB = 1024 * 1024


class FakeProbe:
    """What health_checks reads from the machine."""

    def __init__(self, free=2000 * MB, temp=45.0, synced=True):
        self.free, self.temp, self.synced = free, temp, synced

    def free_disk_bytes(self):
        return self.free

    def cpu_temp_c(self):
        return self.temp

    def clock_synchronized(self):
        return self.synced


def default_state(**update):
    state = pb.parse_state("")
    state["update"].update(update)
    return state


class Env(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name
        env = mock.patch.dict(os.environ, {"SINKO_STATE_DIR": os.path.join(self.tmp, "state")})
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("SINKO_TELEMETRY_URL", None)

    def report(self, state, probe=None):
        lines = []
        rep = pb.Report(out=lines.append)
        with mock.patch.object(pb, "read_config", return_value={}):
            pb.health_checks(rep, state, probe or FakeProbe())
        return rep, lines


class HealthChecksTests(Env):
    def test_a_healthy_box_has_no_warnings_and_keeps_the_doctor_line_format(self):
        rep, lines = self.report(default_state())
        self.assertEqual(rep.warnings, [])
        self.assertEqual(rep.fixes, [])
        for line in lines:
            self.assertRegex(line, r"^  (ok    |info  |WARN  |FIX   )\S")
        self.assertIn("  info  running version %s" % pb.VERSION, lines)

    def test_the_running_version_is_always_reported(self):
        for state in (None, default_state()):
            _, lines = self.report(state)
            self.assertIn("  info  running version %s" % pb.VERSION, lines)

    def test_a_pending_update_is_info_not_a_warning(self):
        rep, lines = self.report(default_state(latest="3.1.0"))
        self.assertIn("  info  update available: 3.1.0 (install it with: sudo sinko update)", lines)
        self.assertEqual(rep.warnings, [])

    def test_an_update_that_is_installed_or_older_is_not_pending(self):
        for latest in (pb.VERSION, "2.0.0", None):
            _, lines = self.report(default_state(latest=latest))
            self.assertFalse([l for l in lines if "update available" in l], latest)

    def test_a_failed_update_is_mentioned(self):
        _, lines = self.report(default_state(status="failed", error="The installer stopped with an error."))
        self.assertTrue(any(l.startswith("  info  the last update did not work: The installer stopped") for l in lines), lines)

    def test_disk_space_under_300_mb_warns(self):
        for free, warns in ((299 * MB, True), (300 * MB, False), (50 * MB, True), (5000 * MB, False)):
            rep, lines = self.report(default_state(), FakeProbe(free=free))
            self.assertEqual(len(rep.warnings) == 1, warns, free // MB)
            if warns:
                self.assertIn("MB of disk space is free", rep.warnings[0])
                self.assertIn("  WARN  only %d MB" % (free // MB), "\n".join(lines))
            else:
                self.assertTrue(any(l.startswith("  ok    %d MB of disk space free" % (free // MB)) for l in lines))

    def test_cpu_temperature_above_80_warns(self):
        for temp, warns in ((80.0, False), (80.1, True), (95.0, True), (45.0, False)):
            rep, _ = self.report(default_state(), FakeProbe(temp=temp))
            self.assertEqual(len(rep.warnings) == 1, warns, temp)
            if warns:
                self.assertIn("CPU", rep.warnings[0])

    def test_a_clock_that_is_not_synchronised_warns_and_one_that_cannot_be_asked_says_nothing(self):
        rep, _ = self.report(default_state(), FakeProbe(synced=False))
        self.assertEqual(len(rep.warnings), 1)
        self.assertIn("clock is not synchronised", rep.warnings[0])
        rep, lines = self.report(default_state(), FakeProbe(synced=None))
        self.assertEqual(rep.warnings, [])
        self.assertFalse([l for l in lines if "clock" in l])

    def test_missing_sensors_say_nothing(self):
        rep, lines = self.report(default_state(), FakeProbe(free=None, temp=None, synced=None))
        self.assertEqual(rep.warnings, [])
        self.assertFalse([l for l in lines if "disk" in l or "CPU" in l or "clock" in l])

    def test_the_counter_state_is_info_in_all_three_cases(self):
        for on, text in ((None, "not decided yet"), (False, "off"), (True, "on")):
            state = default_state()
            state["telemetry"]["on"] = on
            rep, lines = self.report(state)
            self.assertTrue(any(l.startswith("  info  anonymous counter: " + text) for l in lines), (on, lines))
            self.assertEqual(rep.warnings, [])

    def test_on_without_an_address_says_nothing_is_sent(self):
        state = default_state()
        state["telemetry"]["on"] = True
        _, lines = self.report(state)
        self.assertTrue(any("no counter address is set up, so nothing is sent" in l for l in lines), lines)
        with mock.patch.object(pb, "read_config", return_value={"SINKO_TELEMETRY_URL": "https://counter.example"}):
            rep = pb.Report(out=lines.append)
            lines.clear()
            pb.health_checks(rep, state, FakeProbe())
        self.assertFalse(any("nothing is sent" in l for l in lines), lines)

    def test_without_the_state_the_counter_and_update_lines_are_left_out(self):
        _, lines = self.report(None)
        self.assertFalse([l for l in lines if "counter" in l or "update" in l])

    def test_the_counter_line_never_shows_the_id(self):
        secret = pb.install_id()
        state = default_state()
        state["telemetry"]["on"] = True
        _, lines = self.report(state)
        self.assertNotIn(secret, "\n".join(lines))

    def test_warnings_do_not_make_the_doctor_fail(self):
        rep, _ = self.report(default_state(), FakeProbe(free=10 * MB, temp=90.0, synced=False))
        self.assertEqual(len(rep.warnings), 3)
        self.assertEqual(rep.finish(), 0)


class SystemProbeTests(Env):
    def write(self, name, text):
        path = os.path.join(self.tmp, name)
        with open(path, "w") as fh:
            fh.write(text)
        return path

    def test_temperature_is_read_in_degrees_from_millidegrees(self):
        probe = pb.SystemProbe()
        for text, want in (("45000\n", 45.0), ("81500\n", 81.5), ("52\n", 52.0), ("-5000", -5.0)):
            with mock.patch.dict(os.environ, {"SINKO_THERMAL_FILE": self.write("t", text)}):
                self.assertEqual(probe.cpu_temp_c(), want, text)

    def test_a_missing_or_nonsense_sensor_is_none(self):
        probe = pb.SystemProbe()
        with mock.patch.dict(os.environ, {"SINKO_THERMAL_FILE": os.path.join(self.tmp, "none")}):
            self.assertIsNone(probe.cpu_temp_c())
        for text in ("", "hot", "999999999"):
            with mock.patch.dict(os.environ, {"SINKO_THERMAL_FILE": self.write("t", text)}):
                self.assertIsNone(probe.cpu_temp_c(), text)

    def test_the_default_sensor_is_the_first_thermal_zone(self):
        opened = []
        real_open = open

        def spy(path, *a, **k):
            opened.append(path)
            return real_open(path, *a, **k)
        with mock.patch.dict(os.environ):
            os.environ.pop("SINKO_THERMAL_FILE", None)
            with mock.patch("builtins.open", spy):
                pb.SystemProbe().cpu_temp_c()
        self.assertIn("/sys/class/thermal/thermal_zone0/temp", opened)

    def test_free_space_comes_from_the_disk_usage_of_the_path(self):
        with mock.patch.dict(os.environ, {"SINKO_DISK_PATH": self.tmp}):
            self.assertGreater(pb.SystemProbe().free_disk_bytes(), 0)
        with mock.patch.dict(os.environ, {"SINKO_DISK_PATH": os.path.join(self.tmp, "gone")}):
            self.assertIsNone(pb.SystemProbe().free_disk_bytes())
        with mock.patch.object(pb.shutil, "disk_usage") as usage:
            usage.return_value = mock.Mock(free=123)
            with mock.patch.dict(os.environ):
                os.environ.pop("SINKO_DISK_PATH", None)
                self.assertEqual(pb.SystemProbe().free_disk_bytes(), 123)
            usage.assert_called_once_with("/")

    def test_the_clock_comes_from_timedatectl(self):
        with mock.patch.object(pb, "ntp_synchronized", return_value=False):
            self.assertIs(pb.SystemProbe().clock_synchronized(), False)


class Pihole(Env):
    def setUp(self):
        super().setUp()
        self.httpd, self.store = fake_release.serve_pihole()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        self.api = pb.Api("http://127.0.0.1:%d" % self.httpd.server_port, password=mock_pihole.PASSWORD)
        self.api.login()
        self.catalog = pb.load_catalog(LISTS)
        self.ctl = pb.Controller(self.api, self.catalog, "https://lists.example/l")
        self.ctl.setup(run_gravity=False)
        with open(os.path.join(self.tmp, "pw"), "w") as fh:
            fh.write(mock_pihole.PASSWORD)
        env = mock.patch.dict(os.environ, {"SINKO_API_URL": "http://127.0.0.1:%d" % self.httpd.server_port})
        env.start()
        self.addCleanup(env.stop)
        for patch in (mock.patch.object(pb, "CLI_PW_FILE", os.path.join(self.tmp, "pw")),
                      mock.patch.object(pb, "load_catalog", lambda *a: self.catalog),
                      mock.patch.object(pb, "read_config", lambda *a: {})):
            patch.start()
            self.addCleanup(patch.stop)

    def set_state(self, mutate):
        state = pb.parse_state(next(g for g in self.store.groups if g["name"] == "pb-state")["comment"])
        mutate(state)
        self.ctl.write_state(state)


class StatusTests(Pihole):
    def test_status_has_the_version_the_update_block_and_the_counter_answer(self):
        self.set_state(lambda s: s["update"].update(latest="3.1.0", notes="https://github.com/iret33/sinko/releases/tag/v3.1.0",
                                                    checked=1700000000.0, status="failed", error="x", at=1700000100.0,
                                                    auto=True, request=123, checkRequest=456))
        self.set_state(lambda s: s["telemetry"].update(on=True))
        status = pb.build_status(self.api, self.catalog)
        self.assertEqual(status["version"], pb.VERSION)
        self.assertEqual(status["update"], {"auto": True, "latest": "3.1.0",
                                            "notes": "https://github.com/iret33/sinko/releases/tag/v3.1.0",
                                            "checked": 1700000000.0, "status": "failed", "from": None, "to": None,
                                            "at": 1700000100.0, "error": "x", "rolledBack": None})
        self.assertEqual(status["telemetry"], {"on": True})

    def test_the_pages_request_markers_are_not_part_of_the_picture(self):
        self.set_state(lambda s: s["update"].update(request=123, checkRequest=456))
        self.assertNotIn("request", pb.build_status(self.api, self.catalog)["update"])
        self.assertNotIn("checkRequest", pb.build_status(self.api, self.catalog)["update"])

    def test_the_counter_answer_is_null_until_the_parent_answers(self):
        self.assertEqual(pb.build_status(self.api, self.catalog)["telemetry"], {"on": None})
        self.set_state(lambda s: s["telemetry"].update(on=False))
        self.assertEqual(pb.build_status(self.api, self.catalog)["telemetry"], {"on": False})

    def test_the_existing_keys_are_all_still_there(self):
        status = pb.build_status(self.api, self.catalog)
        for key in ("version", "internet_off", "blocked", "devices", "timer", "schedule", "bedtime_now"):
            self.assertIn(key, status)

    def test_the_id_is_never_in_the_status_output(self):
        secret = pb.install_id()
        self.set_state(lambda s: s["telemetry"].update(on=True))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(pb.main(["status"]), 0)
        printed = out.getvalue()
        self.assertNotIn(secret, printed)
        data = json.loads(printed)
        self.assertEqual((data["version"], data["telemetry"]), (pb.VERSION, {"on": True}))
        self.assertIn("update", data)
        self.assertNotIn("community", data)

    def test_status_is_a_pure_read(self):
        writes = self.store.writes
        pb.build_status(self.api, self.catalog)
        self.assertEqual(self.store.writes, writes)


class DoctorTests(Pihole):
    """The whole command: the new lines appear after the old ones and the old ones are unchanged."""

    def run_doctor(self, probe, **env):
        os.makedirs(os.path.join(self.tmp, "pb"), exist_ok=True)
        with open(os.path.join(self.tmp, "pb", "app.js"), "w") as fh:
            fh.write("x")
        out = io.StringIO()
        ok = subprocess.CompletedProcess([], 0, "", "")
        with contextlib.redirect_stdout(out), mock.patch.object(pb.subprocess, "run", return_value=ok), \
                mock.patch.object(pb, "ftl_config", lambda key: "true" if "webroot" not in key else self.tmp), \
                mock.patch.object(pb, "doctor_api_checks", lambda rep, api, catalog: rep.ok("api checks ran")), \
                mock.patch.object(pb, "SystemProbe", lambda: probe), mock.patch.object(pb, "lists_base", lambda *a: "/local"):
            code = pb.main(["doctor"])
        return code, out.getvalue()

    def test_the_new_checks_are_part_of_the_doctors_output(self):
        self.set_state(lambda s: s["update"].update(latest="3.1.0"))
        self.set_state(lambda s: s["telemetry"].update(on=False))
        code, out = self.run_doctor(FakeProbe(free=100 * MB, temp=85.0, synced=False))
        lines = out.splitlines()
        self.assertEqual(code, 0, "warnings do not fail the doctor")
        self.assertIn("  ok    api checks ran", lines)
        for expected in ("  info  running version %s" % pb.VERSION,
                         "  info  update available: 3.1.0 (install it with: sudo sinko update)",
                         "  info  anonymous counter: off"):
            self.assertIn(expected, lines)
        self.assertEqual(len([l for l in lines if l.startswith("  WARN  ")]), 3)
        self.assertIn("No problems, but 3 warning(s) above.", out)
        self.assertLess(lines.index("  ok    api checks ran"), lines.index("  info  running version %s" % pb.VERSION))

    def test_a_clean_box_says_all_good(self):
        _, out = self.run_doctor(FakeProbe())
        self.assertTrue(out.rstrip().endswith("All good."), out)

    def test_with_pihole_unreachable_the_machine_checks_still_run(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        code, out = self.run_doctor(FakeProbe(free=100 * MB))
        self.assertEqual(code, 1, "the API being down is a real problem")
        self.assertIn("  FIX   Pi-hole API reachable", out)
        self.assertIn("  info  running version %s" % pb.VERSION, out)
        self.assertIn("  WARN  only 100 MB of disk space is free", out)
        self.assertNotIn("anonymous counter", out, "that line needs the state")


if __name__ == "__main__":
    unittest.main()
