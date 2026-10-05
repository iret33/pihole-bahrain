"""/pb/box.json: what the page may know about the box without signing in, written by the box program as root. Its
content, how it is written (atomically, mode 644, only when it changed or is ten minutes old), what happens when there
is nowhere to write it, and the commands that print or write it."""
import contextlib
import datetime as dt
import importlib.machinery
import importlib.util
import io
import json
import os
import stat
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

loader = importlib.machinery.SourceFileLoader("sinko_cli", os.path.join(ROOT, "bin", "sinko"))
spec = importlib.util.spec_from_loader("sinko_cli", loader)
pb = importlib.util.module_from_spec(spec)
loader.exec_module(pb)

KEYS = ["v", "version", "ip", "tz", "utcOffset", "counter", "mdns", "at"]


class Box(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name
        self.webroot = os.path.join(self.tmp, "www")
        self.pbdir = os.path.join(self.webroot, "pb")
        self.zoneinfo = os.path.join(self.tmp, "zoneinfo", "Asia")
        env = mock.patch.dict(os.environ, {
            "SINKO_WEBROOT": self.webroot, "SINKO_API_URL": "http://127.0.0.1:1",
            "SINKO_LOCALTIME": os.path.join(self.tmp, "localtime"), "SINKO_TIMEZONE_FILE": os.path.join(self.tmp, "timezone"),
            "SINKO_STATE_DIR": os.path.join(self.tmp, "state")})
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("SINKO_TELEMETRY_URL", None)
        for patch in (mock.patch.object(pb, "default_route_ipv4", lambda: "192.168.1.50"),
                      mock.patch.object(pb, "unit_active", lambda name: name == "avahi-daemon")):
            patch.start()
            self.addCleanup(patch.stop)
        self.set_zone("Asia/Bahrain")

    def set_zone(self, name):
        path = os.path.join(self.tmp, "localtime")
        if os.path.lexists(path):
            os.unlink(path)
        os.symlink("/usr/share/zoneinfo/" + name, path)

    def make_pb(self):
        os.makedirs(self.pbdir, exist_ok=True)

    def read(self):
        with open(os.path.join(self.pbdir, "box.json")) as fh:
            return json.load(fh)

    def path(self):
        return os.path.join(self.pbdir, "box.json")


class ContentTests(Box):
    def test_the_content_is_exactly_what_the_contract_says(self):
        info = pb.box_info({"SINKO_TELEMETRY_URL": "https://counter.example"}, when=1790000000)
        self.assertEqual(list(info), KEYS)
        self.assertEqual(info["v"], 1)
        self.assertEqual(info["version"], pb.VERSION)
        self.assertEqual(info["ip"], "192.168.1.50")
        self.assertEqual(info["tz"], "Asia/Bahrain")
        self.assertRegex(info["utcOffset"], r"^[+-]\d\d:\d\d$")
        self.assertIs(info["counter"], True)
        self.assertIs(info["mdns"], True)
        self.assertEqual(info["at"], 1790000000)
        self.assertIsInstance(info["at"], int)

    def test_nothing_in_it_identifies_the_family_or_the_box(self):
        conf = {"SINKO_HOSTNAME": "smiths.lan", "SINKO_IP": "10.9.9.9", "SINKO_TELEMETRY_URL": "https://counter.example/secret-path"}
        text = json.dumps(pb.box_info(conf))
        for secret in ("smiths", "10.9.9.9", "secret-path", "counter.example"):
            self.assertNotIn(secret, text)
        self.assertLessEqual(len(text), 300)

    def test_counter_means_an_address_is_configured_and_acceptable(self):
        self.assertIs(pb.box_info({})["counter"], False)
        self.assertIs(pb.box_info({"SINKO_TELEMETRY_URL": "http://counter.example"})["counter"], False, "plain http is not accepted")
        self.assertIs(pb.box_info({"SINKO_TELEMETRY_URL": "https://counter.example"})["counter"], True)

    def test_mdns_means_avahi_answers(self):
        self.assertIs(pb.box_info({})["mdns"], True)
        with mock.patch.object(pb, "unit_active", lambda name: False):
            self.assertIs(pb.box_info({})["mdns"], False)
        self.assertIs(pb.box_info({}, mdns=False)["mdns"], False)

    def test_the_address_is_the_default_route_one_and_null_when_there_is_none_or_it_is_no_lan_address(self):
        self.assertEqual(pb.box_info({})["ip"], "192.168.1.50")
        self.assertIsNone(pb.box_info({}, ip=None)["ip"])
        for odd in ("127.0.0.1", "169.254.1.2", "::1", "not an address", "", "0.0.0.0"):
            self.assertIsNone(pb.box_info({}, ip=odd)["ip"], odd)
        with mock.patch.object(pb, "default_route_ipv4", side_effect=OSError("no route")):
            self.assertIsNone(pb.box_info({})["ip"])
        self.assertEqual(pb.box_info({}, ip="10.0.0.7")["ip"], "10.0.0.7")

    def test_the_utc_offset_text(self):
        for hours, minutes, want in ((3, 0, "+03:00"), (0, 0, "+00:00"), (-4, -30, "-04:30"), (5, 45, "+05:45"), (-12, 0, "-12:00"),
                                     (14, 0, "+14:00")):
            tz = dt.timezone(dt.timedelta(hours=hours, minutes=minutes))
            self.assertEqual(pb.utc_offset_text(dt.datetime(2026, 1, 1, tzinfo=tz)), want)
        self.assertRegex(pb.utc_offset_text(), r"^[+-]\d\d:\d\d$")

    def test_the_time_zone_comes_from_the_localtime_link_then_from_etc_timezone(self):
        self.assertEqual(pb.timezone_name(), "Asia/Bahrain")
        self.set_zone("America/Argentina/Buenos_Aires")
        self.assertEqual(pb.timezone_name(), "America/Argentina/Buenos_Aires")
        os.unlink(os.path.join(self.tmp, "localtime"))
        self.assertIsNone(pb.timezone_name())
        with open(os.path.join(self.tmp, "timezone"), "w") as fh:
            fh.write("Europe/London\n")
        self.assertEqual(pb.timezone_name(), "Europe/London")
        self.set_zone("Asia/Dubai")
        self.assertEqual(pb.timezone_name(), "Asia/Dubai", "the link first")

    def test_a_time_zone_that_is_not_a_plausible_name_is_not_used(self):
        for bad in ("../../etc/passwd", "Asia/../x", "has space", "", "a" * 70, "x;rm", "Zone\u00e9"):
            with open(os.path.join(self.tmp, "timezone"), "w", encoding="utf-8") as fh:
                fh.write(bad + "\n")
            if os.path.lexists(os.path.join(self.tmp, "localtime")):
                os.unlink(os.path.join(self.tmp, "localtime"))
            self.assertIsNone(pb.timezone_name(), repr(bad))
        os.symlink("/usr/share/zoneinfo/../../etc/passwd", os.path.join(self.tmp, "localtime"))
        self.assertIsNone(pb.timezone_name())

    def test_a_box_without_any_time_zone_information_has_null(self):
        os.unlink(os.path.join(self.tmp, "localtime"))
        self.assertIsNone(pb.box_info({})["tz"])


class WriteTests(Box):
    def test_without_a_page_folder_there_is_nothing_to_write_and_nothing_is_made(self):
        self.assertEqual(pb.write_box_info({}), "nowhere")
        self.assertFalse(os.path.exists(self.webroot), "not even the web root: the installer swaps the folder by renaming")
        os.makedirs(self.webroot)
        self.assertEqual(pb.write_box_info({}), "nowhere")
        self.assertEqual(os.listdir(self.webroot), [])
        self.assertIsNone(pb.box_info_path())

    def test_a_first_write_is_atomic_compact_and_world_readable(self):
        self.make_pb()
        self.assertEqual(pb.write_box_info({}, when=1790000000), "written")
        self.assertEqual(stat.S_IMODE(os.stat(self.path()).st_mode), 0o644)
        with open(self.path()) as fh:
            text = fh.read()
        self.assertEqual(text, json.dumps(json.loads(text), separators=(",", ":")), "compact, the way the contract shows it")
        self.assertEqual(list(json.loads(text)), KEYS)
        self.assertEqual(os.listdir(self.pbdir), ["box.json"], "no temporary file is left")

    def test_the_file_is_rewritten_only_when_something_changed_or_it_is_ten_minutes_old(self):
        self.make_pb()
        self.assertEqual(pb.write_box_info({}, when=1000), "written")
        inode = os.stat(self.path()).st_ino
        mtime = os.stat(self.path()).st_mtime_ns
        self.assertEqual(pb.write_box_info({}, when=1000 + 5), "unchanged", "only `at` differs")
        self.assertEqual(pb.write_box_info({}, when=1000 + 599), "unchanged")
        self.assertEqual((os.stat(self.path()).st_ino, os.stat(self.path()).st_mtime_ns), (inode, mtime))
        self.assertEqual(self.read()["at"], 1000)
        self.assertEqual(pb.write_box_info({}, when=1000 + 600), "written", "ten minutes: the heartbeat")
        self.assertEqual(self.read()["at"], 1600)

    def test_a_change_of_content_is_written_at_once(self):
        self.make_pb()
        pb.write_box_info({}, when=1000)
        self.assertEqual(pb.write_box_info({}, ip="10.0.0.9", when=1001), "written")
        self.assertEqual(self.read()["ip"], "10.0.0.9")
        self.assertEqual(pb.write_box_info({"SINKO_TELEMETRY_URL": "https://c.example"}, ip="10.0.0.9", when=1002), "written")
        self.assertIs(self.read()["counter"], True)
        self.assertEqual(pb.write_box_info({"SINKO_TELEMETRY_URL": "https://c.example"}, ip="10.0.0.9", mdns=False, when=1003), "written")
        self.assertIs(self.read()["mdns"], False)

    def test_a_file_from_the_future_is_not_fresh_because_the_clock_was_wrong_when_it_was_written(self):
        self.make_pb()
        pb.write_box_info({}, when=5000)
        self.assertEqual(pb.write_box_info({}, when=1000), "written")
        self.assertEqual(self.read()["at"], 1000)

    def test_force_writes_even_when_nothing_changed(self):
        self.make_pb()
        pb.write_box_info({}, when=1000)
        self.assertEqual(pb.write_box_info({}, when=1001, force=True), "written")

    def test_a_garbled_or_foreign_file_is_replaced(self):
        self.make_pb()
        for text in ("", "{", "[]", "null", '{"at": "soon"}', '{"v": 2, "at": 1000}'):
            with open(self.path(), "w") as fh:
                fh.write(text)
            self.assertEqual(pb.write_box_info({}, when=1000), "written", text)
            self.assertEqual(self.read()["v"], 1)

    def test_a_write_that_fails_raises_oserror_and_leaves_the_old_file_alone(self):
        self.make_pb()
        pb.write_box_info({}, when=1000)
        with open(self.path()) as fh:
            before = fh.read()
        with mock.patch.object(pb, "atomic_write", side_effect=OSError(28, "No space left on device")):
            with self.assertRaises(OSError):
                pb.write_box_info({}, ip="10.0.0.9", when=2000)
        with open(self.path()) as fh:
            self.assertEqual(fh.read(), before)

    def test_the_callers_that_must_never_fail_swallow_everything(self):
        self.make_pb()
        for error in (OSError("full"), RuntimeError("bug"), ValueError("x")):
            with mock.patch.object(pb, "write_box_info", side_effect=error):
                pb.refresh_box_info()                               # does not raise
        pb.refresh_box_info(when=1000)
        self.assertEqual(self.read()["at"], 1000)

    def test_the_web_root_comes_from_the_environment_or_from_pihole_ftl(self):
        self.make_pb()
        self.assertEqual(pb.box_info_path(), self.path())
        with mock.patch.dict(os.environ):
            os.environ.pop("SINKO_WEBROOT")
            other = os.path.join(self.tmp, "other")
            os.makedirs(os.path.join(other, "pb"))
            with mock.patch.object(pb, "ftl_config", lambda key: other):
                self.assertEqual(pb.box_info_path(), os.path.join(other, "pb", "box.json"))
            with mock.patch.object(pb, "ftl_config", side_effect=FileNotFoundError("pihole-FTL")):
                self.assertEqual(pb.webroot(), "/var/www/html")


class CommandTests(Box):
    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = pb.main(list(args))
        return code, out.getvalue(), err.getvalue()

    def test_box_info_prints_what_would_be_written_and_writes_nothing(self):
        self.make_pb()
        code, out, _ = self.run_cli("box-info")
        self.assertEqual(code, 0)
        info = json.loads(out)
        self.assertEqual(list(info), KEYS)
        self.assertFalse(os.path.exists(self.path()))

    def test_box_info_write_writes_it_and_says_so(self):
        self.make_pb()
        code, out, _ = self.run_cli("box-info", "--write")
        self.assertEqual(code, 0)
        self.assertIn("box.json written", out)
        self.assertEqual(self.read()["version"], pb.VERSION)
        self.assertEqual(stat.S_IMODE(os.stat(self.path()).st_mode), 0o644)

    def test_the_installer_calls_it_after_every_page_swap_so_it_writes_even_a_fresh_file(self):
        self.make_pb()
        self.run_cli("box-info", "--write")
        first = os.stat(self.path()).st_mtime_ns
        self.run_cli("box-info", "--write")
        self.assertEqual(self.read()["v"], 1)
        self.assertGreaterEqual(os.stat(self.path()).st_mtime_ns, first)

    def test_with_no_page_folder_it_is_a_message_and_success_not_an_error(self):
        code, out, err = self.run_cli("box-info", "--write")
        self.assertEqual(code, 0)
        self.assertIn("no page folder", out)
        self.assertEqual(err, "")

    def test_a_failing_write_is_an_error_with_a_short_message(self):
        self.make_pb()
        with mock.patch.object(pb, "atomic_write", side_effect=OSError(13, "Permission denied")):
            code, _, err = self.run_cli("box-info", "--write")
        self.assertEqual(code, 1)
        self.assertIn("could not write box.json", err)

    def test_configure_refreshes_the_file_with_the_new_address(self):
        self.make_pb()
        with mock.patch.object(pb, "apply_pihole_settings") as apply:
            code, out, _ = self.run_cli("configure", "--ip", "10.1.2.3", "--hostname", "family.lan")
        self.assertEqual(code, 0)
        apply.assert_called_once()
        self.assertEqual(self.read()["ip"], "10.1.2.3")

    def test_configure_without_an_address_uses_the_route_and_never_fails_because_of_it(self):
        self.make_pb()
        with mock.patch.object(pb, "apply_pihole_settings"):
            self.assertEqual(self.run_cli("configure")[0], 0)
            self.assertEqual(self.read()["ip"], "192.168.1.50")
            os.unlink(self.path())
            os.rmdir(self.pbdir)                                   # no page folder: still fine
            self.assertEqual(self.run_cli("configure", "--hostname", "x.lan")[0], 0)

    def test_uninstalling_does_not_write_the_file(self):
        self.make_pb()
        with mock.patch.object(pb, "apply_pihole_settings"):
            self.run_cli("configure", "--remove-hostname", "--disable-web")
        self.assertFalse(os.path.exists(self.path()))

    def test_a_new_address_found_by_the_address_watch_is_written_too(self):
        self.make_pb()
        path = os.path.join(self.tmp, "config")
        with open(path, "w") as fh:
            fh.write("SINKO_HOSTNAME=family.lan\nSINKO_IP=192.168.1.5\n")
        os.chmod(path, 0o644)
        with mock.patch.object(pb, "apply_pihole_settings"):
            pb.apply_new_address("192.168.1.77", {"SINKO_HOSTNAME": "family.lan"}, path)
        self.assertEqual(self.read()["ip"], "192.168.1.77")
        with open(path) as fh:
            self.assertIn("SINKO_IP=192.168.1.77", fh.read())

    def test_a_failure_to_write_the_file_never_breaks_the_address_change(self):
        self.make_pb()
        path = os.path.join(self.tmp, "config")
        with open(path, "w") as fh:
            fh.write("SINKO_IP=192.168.1.5\n")
        with mock.patch.object(pb, "apply_pihole_settings"), mock.patch.object(pb, "write_config_value"), \
                mock.patch.object(pb, "atomic_write", side_effect=OSError("disk full")):
            pb.apply_new_address("192.168.1.77", {}, path)                   # does not raise


if __name__ == "__main__":
    unittest.main()
