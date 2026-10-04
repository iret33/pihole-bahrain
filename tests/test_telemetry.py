"""The optional anonymous counter: what is sent (and nothing else), when it is sent (only after a yes, only with an
address configured), how the answer is stored, and the `sinko telemetry` command."""
import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
import re
import stat
import tempfile
import time
import unittest
from unittest import mock

import fake_release
import test_maintenance as tm

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
LISTS = os.path.join(ROOT, "lists")

loader = importlib.machinery.SourceFileLoader("sinko_cli", os.path.join(ROOT, "bin", "sinko"))
spec = importlib.util.spec_from_loader("sinko_cli", loader)
pb = importlib.util.module_from_spec(spec)
loader.exec_module(pb)


class Plain(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name
        env = mock.patch.dict(os.environ, {"SINKO_STATE_DIR": os.path.join(self.tmp, "state")})
        env.start()
        self.addCleanup(env.stop)
        for key in ("SINKO_TELEMETRY_URL", "SINKO_DT_MODEL"):
            os.environ.pop(key, None)

    def model(self, text):
        path = os.path.join(self.tmp, "model")
        with open(path, "wb") as fh:
            fh.write(text)
        return path


class DefaultsTests(Plain):
    def test_the_default_endpoint_is_empty_so_nothing_can_be_sent_by_accident(self):
        self.assertEqual(pb.TELEMETRY_URL, "")
        self.assertEqual(pb.telemetry_url({}), "")


class AddressTests(Plain):
    def test_the_environment_beats_the_settings_beats_the_built_in_default(self):
        conf = {"SINKO_TELEMETRY_URL": "https://counter.example/"}
        self.assertEqual(pb.telemetry_url(conf), "https://counter.example")
        os.environ["SINKO_TELEMETRY_URL"] = "https://other.example/base/"
        self.assertEqual(pb.telemetry_url(conf), "https://other.example/base")
        with mock.patch.object(pb, "TELEMETRY_URL", "https://built-in.example"):
            os.environ.pop("SINKO_TELEMETRY_URL")
            self.assertEqual(pb.telemetry_url({}), "https://built-in.example")

    def test_an_address_that_would_expose_the_id_or_is_not_one_is_not_used(self):
        for bad in ("http://counter.example", "ftp://counter.example", "file:///etc/passwd", "counter.example",
                    "https://", "https://user:pw@counter.example", "https://counter.example/?x=1", "https://counter.example/#f",
                    "https://counter.example:notaport", "javascript:alert(1)", "   ", "http://127.0.0.1.evil.example"):
            self.assertEqual(pb.telemetry_url({"SINKO_TELEMETRY_URL": bad}), "", bad)

    def test_plain_http_is_only_accepted_for_this_machine(self):
        for good in ("http://127.0.0.1:8080", "http://localhost:9", "http://[::1]:9"):
            self.assertEqual(pb.telemetry_url({"SINKO_TELEMETRY_URL": good}), good)


class HardwareTests(Plain):
    def test_models(self):
        for text, want in ((b"Orange Pi Zero 3\x00", "orangepi-zero3"), (b"OrangePi Zero3\x00", "orangepi-zero3"),
                           (b"orange-pi zero 3", "orangepi-zero3"), (b"Raspberry Pi 4 Model B Rev 1.4\x00", "raspberrypi"),
                           (b"Orange Pi Zero 2W\x00", "other"), (b"Some Board\x00", "other"), (b"", "other"),
                           (b"\xff\xfe\x00", "other")):
            self.assertEqual(pb.hardware_kind(self.model(text), machine="aarch64"), want, text)

    def test_without_a_device_tree_the_cpu_decides(self):
        missing = os.path.join(self.tmp, "none")
        for machine, want in (("x86_64", "x86"), ("AMD64", "x86"), ("i686", "x86"), ("aarch64", "other"), ("armv7l", "other")):
            self.assertEqual(pb.hardware_kind(missing, machine=machine), want, machine)

    def test_the_device_tree_path_can_be_overridden_and_the_answer_is_one_of_four(self):
        os.environ["SINKO_DT_MODEL"] = self.model(b"Orange Pi Zero 3\x00")
        self.assertEqual(pb.hardware_kind(), "orangepi-zero3")
        self.assertIn(pb.hardware_kind(os.path.join(self.tmp, "none")), ("orangepi-zero3", "raspberrypi", "x86", "other"))


class PayloadTests(Plain):
    def test_the_payload_is_exactly_id_version_and_hardware(self):
        os.environ["SINKO_DT_MODEL"] = self.model(b"Orange Pi Zero 3\x00")
        body = pb.telemetry_body()
        data = json.loads(body)
        self.assertEqual(list(data), ["id", "v", "hw"])
        self.assertRegex(data["id"], r"^[0-9a-f]{32}$")
        self.assertEqual((data["v"], data["hw"]), (pb.VERSION, "orangepi-zero3"))
        self.assertEqual(body, json.dumps(data, separators=(",", ":")), "compact, the same bytes every time")
        self.assertLessEqual(len(body.encode()), 512)

    def test_the_id_is_stable_between_pings_and_a_reset_gives_a_new_one(self):
        first = json.loads(pb.telemetry_body())["id"]
        self.assertEqual(json.loads(pb.telemetry_body())["id"], first)
        pb.reset_install_id()
        self.assertNotEqual(json.loads(pb.telemetry_body())["id"], first)

    def test_nothing_that_identifies_the_family_can_be_in_it(self):
        conf = os.path.join(self.tmp, "config")
        with open(conf, "w") as fh:
            fh.write("SINKO_HOSTNAME=smiths.lan\nSINKO_IP=192.168.7.7\n")
        body = pb.telemetry_body()
        for secret in ("smiths", "192.168", pb.__file__, os.environ.get("TZ", "Asia/Bahrain")):
            self.assertNotIn(secret, body)
        self.assertEqual(set(json.loads(body)), {"id", "v", "hw"})

    def test_the_id_comes_from_random_bytes_not_from_the_machine(self):
        with mock.patch.object(pb.secrets, "token_hex", return_value="ab" * 16) as token:
            self.assertEqual(json.loads(pb.telemetry_body())["id"], "ab" * 16)
        token.assert_called_once_with(16)


class SendPingTests(unittest.TestCase):
    def setUp(self):
        self.httpd, self.site = fake_release.serve()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        self.url = self.site.base.replace("/releases", "")

    def answer(self, payload, status=200):
        self.site.routes["/v1/ping"] = (status, payload if isinstance(payload, bytes) else json.dumps(payload).encode(),
                                        {"Content-Type": "application/json"})

    def test_it_posts_the_body_as_json_to_v1_ping_and_returns_the_numbers(self):
        self.answer({"online": 12, "total": 340})
        self.assertEqual(pb.send_ping(self.url, '{"id":"x"}'), {"online": 12, "total": 340})
        path, headers, body = self.site.posts[0]
        self.assertEqual((path, body), ("/v1/ping", b'{"id":"x"}'))
        self.assertEqual(headers["Content-Type"], "application/json")
        self.assertEqual(headers["User-Agent"], "sinko/" + pb.VERSION)
        self.assertNotIn("Cookie", headers)

    def test_numbers_that_make_no_sense_are_not_used(self):
        for payload in ({"online": -1, "total": 5}, {"online": 6, "total": 5}, {"online": "5", "total": 9}, {"online": True, "total": 9},
                        {"online": 5.5, "total": 9}, {"online": 1, "total": 10 ** 9}, {"online": 1}, [], "x", None, {}):
            self.answer(payload)
            self.assertIsNone(pb.send_ping(self.url, "{}"), payload)

    def test_an_answer_that_is_not_json_or_far_too_big_is_not_used(self):
        self.answer(b"<html>captive portal</html>")
        self.assertIsNone(pb.send_ping(self.url, "{}"))
        self.answer(b'{"online": 1, "total": 2, "pad": "' + b"x" * 10000 + b'"}')
        self.assertIsNone(pb.send_ping(self.url, "{}"))

    def test_every_way_the_counter_can_be_unreachable_is_one_error_type(self):
        self.answer({}, status=500)
        with self.assertRaises(pb.TelemetryError):
            pb.send_ping(self.url, "{}")
        self.answer({}, status=404)
        with self.assertRaises(pb.TelemetryError):
            pb.send_ping(self.url, "{}")
        self.site.routes["/v1/ping"] = lambda h: time.sleep(2)
        started = time.monotonic()
        with self.assertRaises(pb.TelemetryError):
            pb.send_ping(self.url, "{}", timeout=0.3)
        self.assertLess(time.monotonic() - started, 1.8)
        self.httpd.shutdown()
        self.httpd.server_close()
        with self.assertRaises(pb.TelemetryError):
            pb.send_ping(self.url, "{}")

    def test_the_timeout_is_ten_seconds(self):
        self.assertEqual(pb.PING_TIMEOUT, 10)


class PingSchedulingTests(tm.Fixture):
    URL = "https://counter.example"

    def setUp(self):
        super().setUp()
        with open(self.config_path, "a") as fh:
            fh.write("SINKO_TELEMETRY_URL=%s\n" % self.URL)
        self.ids = []

    def yes(self):
        self.set_state(lambda s: s["telemetry"].update(on=True))

    def test_nothing_is_ever_sent_without_a_yes(self):
        for answer in (None, False):
            self.set_state(lambda s: s["telemetry"].update(on=answer))
            self.advance(7 * 3600)
            self.settle()
        self.assertEqual(self.ping_calls, [])
        self.assertFalse(os.path.exists(pb.state_path("install-id")), "not even the id is made before a yes")

    def test_a_yes_without_a_configured_address_sends_nothing(self):
        with open(self.config_path, "w") as fh:
            fh.write(tm.CONFIG)
        self.yes()
        self.advance(7 * 3600)
        self.settle()
        self.assertEqual(self.ping_calls, [])
        self.assertIsNone(self.state()["community"])

    def test_an_address_in_the_environment_counts_as_configured(self):
        with open(self.config_path, "w") as fh:
            fh.write(tm.CONFIG)
        self.yes()
        self.advance(301)
        with mock.patch.dict(os.environ, {"SINKO_TELEMETRY_URL": self.URL}):
            self.settle()
        self.assertEqual(len(self.ping_calls), 1)

    def test_an_unacceptable_address_sends_nothing(self):
        with open(self.config_path, "w") as fh:
            fh.write(tm.CONFIG + "SINKO_TELEMETRY_URL=http://counter.example\n")
        self.yes()
        self.advance(7 * 3600)
        self.settle()
        self.assertEqual(self.ping_calls, [])

    def test_the_first_ping_is_five_minutes_after_start_and_carries_the_payload(self):
        self.yes()
        self.advance(200)
        self.settle()
        self.assertEqual(self.ping_calls, [])
        self.advance(101)
        self.settle()
        self.assertEqual(self.ping_calls, [(self.URL, "BODY")])

    def test_then_every_six_hours_give_or_take_half_an_hour(self):
        self.yes()
        self.advance(301)
        self.settle()
        self.assertEqual(self.jitter_calls, [(-1800, 1800)])
        self.advance(6 * 3600 - 10)
        self.settle()
        self.assertEqual(len(self.ping_calls), 1)
        self.advance(20)
        self.settle()
        self.assertEqual(len(self.ping_calls), 2)

    def test_the_jitter_moves_the_next_ping_by_at_most_half_an_hour(self):
        for jitter, sooner, later in ((-1800, 5 * 3600 + 1799, 5 * 3600 + 1801), (1800, 6 * 3600 + 1799, 6 * 3600 + 1801)):
            self.m.jitter = lambda low, high, j=jitter: j
            self.m._next_ping = self.mono
            self.yes()
            self.settle()
            count = len(self.ping_calls)
            self.advance(sooner)
            self.settle()
            self.assertEqual(len(self.ping_calls), count, "not yet at %d s" % sooner)
            self.advance(later - sooner)
            self.settle()
            self.assertEqual(len(self.ping_calls), count + 1, "due at %d s" % later)

    def test_the_answer_is_stored_as_the_community_number(self):
        self.yes()
        self.advance(301)
        self.settle()
        self.assertEqual(self.state()["community"], {"online": 5, "at": self.now.timestamp()})

    def test_a_number_that_makes_no_sense_is_not_stored(self):
        self.ping_answer = None
        self.yes()
        self.advance(301)
        self.settle()
        self.assertEqual(len(self.ping_calls), 1)
        self.assertIsNone(self.state()["community"])

    def test_a_failed_ping_is_retried_in_half_an_hour_and_logged_once(self):
        self.ping_answer = pb.TelemetryError("the counter could not be reached")
        self.yes()
        self.advance(301)
        with self.assertLogs("sinko", level="WARNING") as logs:
            self.settle()
            self.assertEqual(len(logs.records), 1)
            self.advance(1700)
            self.settle()
            self.assertEqual(len(self.ping_calls), 1, "not before half an hour")
            self.assertEqual(len(logs.records), 1)
            self.advance(200)
            self.settle()
        self.assertEqual(len(self.ping_calls), 2)
        self.assertEqual(len(logs.records), 2, "one line per attempt, an attempt every half hour")
        self.assertIsNone(self.state()["community"])

    def test_switching_it_off_removes_the_number_and_stops_the_pings(self):
        self.yes()
        self.advance(301)
        self.settle()
        self.assertIsNotNone(self.state()["community"])
        self.set_state(lambda s: s["telemetry"].update(on=False))
        self.settle()
        self.assertIsNone(self.state()["community"])
        self.advance(12 * 3600)
        self.settle()
        self.assertEqual(len(self.ping_calls), 1)

    def test_a_no_that_arrives_while_the_ping_is_on_its_way_wins(self):
        self.yes()
        self.advance(301)
        self.pass_()                                       # the ping is sent; its answer is collected on the next pass
        self.set_state(lambda s: s["telemetry"].update(on=False))
        self.pass_()
        self.assertIsNone(self.state()["community"])
        self.assertIs(self.state()["telemetry"]["on"], False)

    def test_no_ping_while_the_clock_cannot_be_trusted(self):
        self.clock_trusted = False
        self.yes()
        self.advance(7 * 3600)
        self.settle()
        self.assertEqual(self.ping_calls, [])
        self.clock_trusted = True
        self.settle()
        self.assertEqual(len(self.ping_calls), 1)

    def test_an_unchanged_state_is_not_rewritten_by_the_counter_job_when_it_is_off(self):
        self.advance(121)
        self.settle()                                      # the first update check records its answer once
        writes = self.store.writes
        for _ in range(5):
            self.advance(3600)
            self.settle()
        self.assertEqual(self.store.writes, writes)

    def test_the_id_never_reaches_the_log(self):
        self.ping_answer = pb.TelemetryError("the counter could not be reached (timed out)")
        secret = pb.install_id()
        self.yes()
        self.advance(301)
        with self.assertLogs("sinko", level="DEBUG") as logs:
            self.settle()
        self.assertNotIn(secret, "\n".join(r.getMessage() for r in logs.records))


if __name__ == "__main__":
    unittest.main()
