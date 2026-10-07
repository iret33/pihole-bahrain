"""The optional anonymous counter: what is sent (and nothing else), when it is sent (only after a yes, only with an
address configured), how the answer is stored, and the `sinko telemetry` command."""
import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
import stat
import tempfile
import time
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
RELEASE_TELEMETRY_URL = pb.TELEMETRY_URL          # what this release ships; the tests below run with it blanked


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
        builtin = mock.patch.object(pb, "TELEMETRY_URL", "")  # the release's built-in counter address must not leak into a test
        builtin.start()
        self.addCleanup(builtin.stop)

    def model(self, text):
        path = os.path.join(self.tmp, "model")
        with open(path, "wb") as fh:
            fh.write(text)
        return path


class DefaultsTests(Plain):
    def test_the_built_in_endpoint_is_empty_or_an_https_address_the_program_accepts_as_it_is(self):
        builtin = RELEASE_TELEMETRY_URL
        if builtin:
            self.assertTrue(builtin.startswith("https://"), builtin)
            with mock.patch.object(pb, "TELEMETRY_URL", builtin):
                self.assertEqual(pb.telemetry_url({}), builtin, "the release's counter address would be refused")

    def test_with_no_address_anywhere_nothing_can_be_sent_by_accident(self):
        self.assertEqual(pb.TELEMETRY_URL, "")                      # blanked by setUp
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


class CounterFixture(unittest.TestCase):
    """A state folder and a fake counter (tests/fake_release.py: it keeps what it was told and can be made to fail)."""

    def setUp(self):
        self.httpd, self.counter = fake_release.serve_counter()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name
        env = mock.patch.dict(os.environ, {"SINKO_STATE_DIR": os.path.join(self.tmp, "state")})
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("SINKO_TELEMETRY_URL", None)
        builtin = mock.patch.object(pb, "TELEMETRY_URL", "")  # the release's built-in counter address must not leak into a test
        builtin.start()
        self.addCleanup(builtin.stop)

    def state_file(self, name):
        return os.path.join(self.tmp, "state", name)

    def files(self):
        try:
            return sorted(n for n in os.listdir(os.path.join(self.tmp, "state")) if not n.startswith("."))
        except FileNotFoundError:
            return []


class SendForgetTests(CounterFixture):
    IDENT = "0123456789abcdef0123456789abcdef"

    def test_it_posts_exactly_the_id_to_v1_forget_and_the_counter_deletes_it(self):
        self.counter.known.add(self.IDENT)
        self.assertIs(pb.send_forget(self.counter.url, self.IDENT), True)
        path, headers, body = self.counter.requests[0]
        self.assertEqual((path, body), ("/v1/forget", b'{"id":"%s"}' % self.IDENT.encode()))
        self.assertEqual(headers["Content-Type"], "application/json")
        self.assertEqual(headers["User-Agent"], "sinko/" + pb.VERSION)
        self.assertNotIn("Cookie", headers)
        self.assertLessEqual(len(body), 512)
        self.assertEqual((self.counter.forgotten, self.counter.known), ([self.IDENT], set()))

    def test_an_id_the_counter_never_knew_is_forgotten_just_the_same(self):
        self.assertIs(pb.send_forget(self.counter.url, self.IDENT), True)

    def test_anything_but_a_confirmation_is_an_error_to_try_again(self):
        for status, body in ((500, b"{}"), (404, b"{}"), (200, b'{"forgotten": false}'), (200, b"{}"), (200, b"[]"),
                             (200, b"<html>captive portal</html>"), (200, b'{"forgotten": "yes"}'), (200, b"x" * 9000)):
            self.counter.forget_body = body if status == 200 else None
            self.counter.fail_forget = 1 if status == 500 else 0
            url = self.counter.url + ("/nowhere" if status == 404 else "")
            with self.assertRaises(pb.TelemetryError, msg=(status, body[:20])):
                pb.send_forget(url, self.IDENT)

    def test_an_id_that_is_not_32_lowercase_hex_digits_is_never_sent(self):
        for bad in ("", "x", "0123456789ABCDEF0123456789ABCDEF", "0123456789abcdef0123456789abcde", self.IDENT + "0",
                    self.IDENT + "\n", None, 5, "../../etc/passwd"):
            with self.assertRaises(pb.TelemetryError, msg=repr(bad)):
                pb.send_forget(self.counter.url, bad)
        self.assertEqual(self.counter.requests, [])

    def test_an_unreachable_counter_is_a_telemetry_error_and_the_id_is_not_in_it(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        with self.assertRaises(pb.TelemetryError) as caught:
            pb.send_forget(self.counter.url, self.IDENT)
        self.assertNotIn(self.IDENT, str(caught.exception))

    def test_the_transport_is_the_pings_one_so_a_redirect_to_plain_http_is_refused(self):
        site_httpd, site = fake_release.serve()
        self.addCleanup(site_httpd.server_close)
        self.addCleanup(site_httpd.shutdown)
        site.routes["/v1/forget"] = (302, b"", {"Location": "http://evil.example/v1/forget"})
        with self.assertRaises(pb.TelemetryError):
            pb.send_forget(site.base.replace("/releases", ""), self.IDENT)


class ForgetCounterIdTests(CounterFixture):
    """What the box does to the id and its markers when the counter has to forget it."""

    def sent(self):
        ident = pb.install_id()
        pb.note_counter_sent()
        self.counter.known.add(ident)
        return ident

    def test_nothing_to_forget_when_there_is_no_id(self):
        self.assertEqual(pb.forget_counter_id(self.counter.url), ("none", ""))
        self.assertEqual((self.counter.requests, self.files()), ([], []))

    def test_an_id_that_never_left_the_box_is_deleted_and_nothing_is_sent(self):
        pb.install_id()                                   # `sinko telemetry payload` makes one without sending it
        self.assertEqual(pb.forget_counter_id(self.counter.url), ("none", ""))
        self.assertEqual((self.counter.requests, self.files()), ([], []))

    def test_an_id_that_was_sent_is_forgotten_by_the_counter_and_then_deleted_here(self):
        ident = self.sent()
        self.assertEqual(pb.forget_counter_id(self.counter.url), ("forgotten", ""))
        self.assertEqual((self.counter.forgotten, self.counter.known), ([ident], set()))
        self.assertEqual(self.files(), [], "the id and both markers are gone")
        self.assertIsNone(pb.install_id(create=False))

    def test_when_the_counter_cannot_be_reached_the_id_stays_with_a_marker(self):
        ident = self.sent()
        self.counter.fail_forget = 1
        what, detail = pb.forget_counter_id(self.counter.url)
        self.assertEqual(what, "pending")
        self.assertIn("HTTP 500", detail)
        self.assertNotIn(ident, detail)
        self.assertEqual(self.files(), ["counter-sent", "forget-pending", "install-id"])
        self.assertTrue(pb.forget_pending())
        self.assertEqual(pb.install_id(create=False), ident, "the same id: it is what has to be forgotten")
        self.assertEqual(pb.forget_counter_id(self.counter.url), ("forgotten", ""))
        self.assertEqual(self.files(), [])

    def test_without_a_counter_address_nobody_can_be_asked_and_the_marker_waits_for_one(self):
        ident = self.sent()
        self.assertEqual(pb.forget_counter_id(""), ("no-address", ""))
        self.assertEqual(self.files(), ["counter-sent", "forget-pending", "install-id"])
        self.assertEqual(self.counter.requests, [], "nothing is ever sent without an address")
        self.assertEqual(pb.forget_counter_id(self.counter.url), ("forgotten", ""))
        self.assertEqual(self.counter.forgotten, [ident])

    def test_a_marker_without_an_id_is_just_cleaned_up(self):
        pb.set_forget_pending()
        self.assertEqual(pb.forget_counter_id(self.counter.url), ("none", ""))
        self.assertEqual(self.files(), [])

    def test_the_forget_request_is_the_only_thing_sent(self):
        self.sent()
        pb.forget_counter_id(self.counter.url)
        self.assertEqual([r[0] for r in self.counter.requests], ["/v1/forget"])

    def test_the_markers_are_private(self):
        self.sent()
        self.counter.fail_forget = 1
        pb.forget_counter_id(self.counter.url)
        for name in ("install-id", "counter-sent", "forget-pending"):
            self.assertEqual(stat.S_IMODE(os.stat(self.state_file(name)).st_mode), 0o600, name)


class ForgetSchedulingTests(tm.Fixture):
    """The scheduler makes the counter forget the box when the answer is no, and asks again until it has."""
    URL = "https://counter.example"

    def setUp(self):
        super().setUp()
        with open(self.config_path, "a") as fh:
            fh.write("SINKO_TELEMETRY_URL=%s\n" % self.URL)

    def answer(self, value):
        self.set_state(lambda s: s["telemetry"].update(on=value))

    def sent_id(self):
        ident = pb.install_id()
        pb.note_counter_sent()
        return ident

    def files_left(self):
        try:
            return sorted(n for n in os.listdir(self.state_dir) if n in ("install-id", "counter-sent", "forget-pending"))
        except FileNotFoundError:
            return []

    def test_a_no_after_pings_makes_the_counter_forget_at_once_and_the_id_goes(self):
        ident = self.sent_id()
        self.answer(True)
        self.advance(301)
        self.settle()
        self.assertEqual(len(self.ping_calls), 1)
        self.answer(False)
        self.advance(15)
        self.settle()
        self.assertEqual(self.forget_calls, [(self.URL, ident)])
        self.assertIsNone(pb.install_id(create=False))
        self.assertEqual(self.files_left(), [])
        self.advance(12 * 3600)
        self.settle()
        self.assertEqual((len(self.ping_calls), len(self.forget_calls)), (1, 1), "nothing more is ever sent")

    def test_a_no_with_an_id_that_never_left_the_box_sends_nothing_at_all(self):
        pb.install_id()
        self.answer(False)
        self.advance(15)
        self.settle()
        self.assertEqual(self.forget_calls, [])
        self.assertIsNone(pb.install_id(create=False), "but the id is deleted")

    def test_not_decided_is_not_a_no_and_forgets_nothing(self):
        self.sent_id()
        self.answer(None)
        self.advance(7 * 3600)
        self.settle()
        self.assertEqual((self.forget_calls, self.ping_calls), ([], []))
        self.assertIsNotNone(pb.install_id(create=False))

    def test_a_failed_forget_keeps_the_id_and_a_marker_and_is_asked_again_every_ping_interval(self):
        ident = self.sent_id()
        self.forget_answer = pb.TelemetryError("the counter could not be reached (timed out)")
        self.answer(False)
        with self.assertLogs("sinko", level="WARNING") as logs:
            self.advance(15)
            self.settle()
            self.assertEqual(len(self.forget_calls), 1)
            self.assertTrue(pb.forget_pending())
            self.assertEqual(pb.install_id(create=False), ident)
            self.advance(6 * 3600 - 100)
            self.settle()
            self.assertEqual(len(self.forget_calls), 1, "not before the ping interval")
            self.advance(200)
            self.settle()
            self.assertEqual(len(self.forget_calls), 2)
        self.assertEqual(len(logs.records), 2, "once per attempt")
        self.assertNotIn(ident, "\n".join(r.getMessage() for r in logs.records))
        self.forget_answer = True
        self.advance(7 * 3600)
        self.settle()
        self.assertEqual(len(self.forget_calls), 3)
        self.assertEqual(self.files_left(), [])

    def test_while_a_forget_is_pending_no_ping_goes_out_even_after_a_yes(self):
        self.sent_id()
        self.forget_answer = pb.TelemetryError("down")
        self.answer(False)
        with self.assertLogs("sinko", level="WARNING"):
            self.advance(15)
            self.settle()
            self.answer(True)
            self.advance(400)
            self.settle()
        self.assertEqual(self.ping_calls, [], "the answer is yes again, but the old id has not been forgotten yet")
        self.forget_answer = True
        self.advance(7 * 3600)
        self.settle()
        self.advance(15)
        self.settle()
        self.assertEqual(len(self.ping_calls), 1, "and when it has, the pings go on")

    def test_a_ping_that_is_on_its_way_lands_before_the_forget_is_asked(self):
        ident = self.sent_id()
        self.answer(True)
        self.advance(301)
        self.pass_()                                       # the ping was sent; its answer is collected on the next pass
        self.answer(False)
        self.forget_answer = True
        self.pass_()
        self.pass_()
        self.assertEqual([c[1] for c in self.forget_calls], [ident])
        self.assertEqual(len(self.ping_calls), 1)

    def test_without_an_address_the_marker_waits_and_nothing_is_sent(self):
        self.sent_id()
        with open(self.config_path, "w") as fh:
            fh.write(tm.CONFIG)
        self.answer(False)
        self.advance(7 * 3600)
        self.settle()
        self.assertEqual(self.forget_calls, [])
        self.assertTrue(pb.forget_pending())
        with open(self.config_path, "a") as fh:
            fh.write("SINKO_TELEMETRY_URL=%s\n" % self.URL)
        self.advance(7 * 3600)
        self.settle()
        self.assertEqual(len(self.forget_calls), 1)

    def test_a_no_still_removes_the_community_number(self):
        self.set_state(lambda s: s.update(community={"online": 7, "at": 1.0}))
        self.answer(False)
        self.settle()
        self.assertIsNone(self.state()["community"])

    def test_the_id_never_reaches_the_log_on_the_forget_path_either(self):
        ident = self.sent_id()
        self.forget_answer = pb.TelemetryError("the counter answered HTTP 500")
        self.answer(False)
        with self.assertLogs("sinko", level="DEBUG") as logs:
            self.advance(15)
            self.settle()
        self.assertNotIn(ident, "\n".join(r.getMessage() for r in logs.records))


class EndToEndCounterTests(tm.Fixture):
    """The scheduler's real counter code against the fake counter: yes, a few pings, no, and what the counter holds."""

    def setUp(self):
        super().setUp()
        self.cs_httpd, self.cs = fake_release.serve_counter()
        self.addCleanup(self.cs_httpd.server_close)
        self.addCleanup(self.cs_httpd.shutdown)
        with open(self.config_path, "a") as fh:
            fh.write("SINKO_TELEMETRY_URL=%s\n" % self.cs.url)
        env = mock.patch.dict(os.environ, {"SINKO_DT_MODEL": os.path.join(self.tmp, "none")})
        env.start()
        self.addCleanup(env.stop)
        self.real = pb.Maintenance(self.api, config_path=self.config_path, monotonic=lambda: self.mono,
                                   job_factory=tm.InlineJob, clock_ok=lambda: True, check=lambda c: None,
                                   start_runner=lambda: True, power=lambda a: None, default_ip=lambda: None,
                                   apply_address=lambda *a: None, box_info=lambda c, **k: "unchanged", heal=lambda: None,
                                   recover=lambda: {"ok": True, "detail": ""}, catalog=lambda: self.catalog,
                                   jitter=lambda lo, hi: 0, ping=pb.send_ping, forget=pb.send_forget, body=pb.telemetry_body)

    def run_real(self, seconds):
        self.advance(seconds)
        self.real.run(self.now)
        self.real.run(self.now)

    def test_yes_then_no_leaves_nothing_at_the_counter_and_nothing_on_the_box(self):
        self.set_state(lambda s: s["telemetry"].update(on=True))
        self.run_real(301)
        self.assertEqual(len(self.cs.pings), 1)
        first = self.cs.pings[0]
        self.assertEqual(self.cs.known, {first})
        self.assertEqual(self.state()["community"]["online"], 1)
        self.set_state(lambda s: s["telemetry"].update(on=False))
        self.run_real(15)
        self.assertEqual((self.cs.forgotten, self.cs.known), ([first], set()))
        self.assertIsNone(self.state()["community"])
        self.assertIsNone(pb.install_id(create=False))
        self.run_real(24 * 3600)
        self.assertEqual(len(self.cs.requests), 2, "one ping and one forget, nothing since")
        self.set_state(lambda s: s["telemetry"].update(on=True))
        self.run_real(24 * 3600)
        self.assertEqual(len(self.cs.pings), 2)
        self.assertNotEqual(self.cs.pings[1], first, "a yes again is a new random number")

    def test_a_counter_that_fails_a_few_times_is_asked_again_until_it_confirms(self):
        self.set_state(lambda s: s["telemetry"].update(on=True))
        self.run_real(301)
        self.cs.fail_forget = 2
        self.set_state(lambda s: s["telemetry"].update(on=False))
        with self.assertLogs("sinko", level="WARNING"):
            self.run_real(15)
            self.run_real(6 * 3600)
        self.assertEqual(self.cs.forgotten, [])
        self.assertIsNotNone(pb.install_id(create=False))
        self.run_real(6 * 3600)
        self.assertEqual(len(self.cs.forgotten), 1, "the third try is the first that works")
        self.assertEqual(self.cs.known, set())
        self.assertIsNone(pb.install_id(create=False))

    def test_a_ping_that_failed_half_way_still_counts_as_sent(self):
        self.cs.fail_ping = 1
        self.set_state(lambda s: s["telemetry"].update(on=True))
        with self.assertLogs("sinko", level="WARNING"):
            self.run_real(301)
        self.assertTrue(pb.counter_sent(), "the request left, whatever came back")
        self.set_state(lambda s: s["telemetry"].update(on=False))
        self.run_real(15)
        self.assertEqual(len(self.cs.forgotten), 1)


class CommandTests(unittest.TestCase):
    """`sinko telemetry ...` and the install-time answer that `sinko setup` copies into the state."""

    def setUp(self):
        self.httpd, self.store = fake_release.serve_pihole()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name
        self.api = pb.Api("http://127.0.0.1:%d" % self.httpd.server_port, password=mock_pihole.PASSWORD)
        self.api.login()
        self.catalog = pb.load_catalog(LISTS)
        self.ctl = pb.Controller(self.api, self.catalog, "https://lists.example/l")
        with open(os.path.join(self.tmp, "pw"), "w") as fh:
            fh.write(mock_pihole.PASSWORD)
        self.conf = {}
        env = mock.patch.dict(os.environ, {"SINKO_API_URL": "http://127.0.0.1:%d" % self.httpd.server_port,
                                           "SINKO_STATE_DIR": os.path.join(self.tmp, "state"),
                                           "SINKO_DT_MODEL": os.path.join(self.tmp, "none")})
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("SINKO_TELEMETRY_URL", None)
        builtin = mock.patch.object(pb, "TELEMETRY_URL", "")  # the release's built-in counter address must not leak into a test
        builtin.start()
        self.addCleanup(builtin.stop)
        for patch in (mock.patch.object(pb, "CLI_PW_FILE", os.path.join(self.tmp, "pw")),
                      mock.patch.object(pb, "read_config", lambda *a: dict(self.conf)),
                      mock.patch.object(pb, "load_catalog", lambda *a: self.catalog)):
            patch.start()
            self.addCleanup(patch.stop)

    def setup_pihole(self):
        self.ctl.setup(run_gravity=False)

    def state(self):
        return pb.parse_state(next(g for g in self.store.groups if g["name"] == "pb-state")["comment"])

    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = pb.main(list(args))
        return code, out.getvalue(), err.getvalue()

    def test_on_and_off_record_the_answer_in_the_state_and_say_what_it_means(self):
        self.setup_pihole()
        self.conf = {"SINKO_TELEMETRY_URL": "https://counter.example"}
        code, out, _ = self.run_cli("telemetry", "on")
        self.assertEqual(code, 0)
        self.assertIs(self.state()["telemetry"]["on"], True)
        self.assertIn("Anonymous counter: on", out)
        self.assertIn("sinko telemetry payload", out)
        self.assertIs(self.run_cli("telemetry", "off")[0], 0)
        self.assertIs(self.state()["telemetry"]["on"], False)

    def test_on_without_a_counter_address_says_that_nothing_is_sent(self):
        self.setup_pihole()
        code, out, _ = self.run_cli("telemetry", "on")
        self.assertEqual(code, 0)
        self.assertIs(self.state()["telemetry"]["on"], True)
        self.assertIn("nothing is sent", out)

    def test_off_removes_the_community_number(self):
        self.setup_pihole()
        state = self.state()
        state["telemetry"]["on"] = True
        state["community"] = {"online": 12, "at": 1.0}
        self.ctl.write_state(state)
        self.run_cli("telemetry", "off")
        self.assertIsNone(self.state()["community"])

    def test_on_and_off_leave_the_rest_of_the_state_alone(self):
        self.setup_pihole()
        state = self.state()
        state["schedule"]["enabled"] = True
        state["update"]["auto"] = True
        state["setup"]["done"] = True
        self.ctl.write_state(state)
        self.run_cli("telemetry", "on")
        after = self.state()
        self.assertTrue(after["schedule"]["enabled"] and after["update"]["auto"] and after["setup"]["done"])

    def test_status_reports_the_answer_and_never_the_id(self):
        self.setup_pihole()
        secret = pb.install_id()
        for action, shown in (("status", "not decided yet"), ("on", "on"), ("off", "off")):
            code, out, err = self.run_cli("telemetry", action)
            self.assertEqual(code, 0)
            self.assertIn("Anonymous counter: " + shown, out)
            self.assertNotIn(secret, out + err)
        state = self.state()
        state["telemetry"]["on"] = True
        state["community"] = {"online": 12, "at": 1.0}
        self.ctl.write_state(state)
        _, out, _ = self.run_cli("telemetry", "status")
        self.assertIn("Boxes online at the last answer: 12", out)
        self.assertNotIn(secret, out)

    def test_status_does_not_change_anything(self):
        self.setup_pihole()
        writes = self.store.writes
        self.run_cli("telemetry", "status")
        self.assertEqual(self.store.writes, writes)

    def test_payload_prints_exactly_what_would_be_sent_including_the_id(self):
        code, out, err = self.run_cli("telemetry", "payload")
        self.assertEqual(code, 0)
        self.assertEqual(out, pb.telemetry_body() + "\n")
        self.assertEqual(json.loads(out)["id"], pb.install_id(create=False))
        self.assertEqual(err, "")

    def test_payload_needs_no_pihole(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.assertEqual(self.run_cli("telemetry", "payload")[0], 0)

    def test_reset_id_makes_a_new_id_and_does_not_print_it(self):
        old = pb.install_id()
        code, out, err = self.run_cli("telemetry", "reset-id")
        self.assertEqual(code, 0)
        new = pb.install_id(create=False)
        self.assertNotEqual(new, old)
        self.assertNotIn(new, out + err)
        self.assertNotIn(old, out + err)

    def test_before_setup_has_made_the_state_the_command_says_so(self):
        code, _, err = self.run_cli("telemetry", "on")
        self.assertEqual(code, 1)
        self.assertIn("sinko setup", err)

    def test_pihole_being_down_is_a_message_not_a_traceback(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        code, _, err = self.run_cli("telemetry", "status")
        self.assertEqual(code, 2)
        self.assertIn("Pi-hole could not be reached", err)

    def test_setup_copies_the_install_time_answer_when_the_state_has_none(self):
        for value, want in (("1", True), ("0", False), ("true", True), ("No", False)):
            self.store.groups[:] = [g for g in self.store.groups if g["name"] == "Default"]
            self.conf = {"SINKO_TELEMETRY": value}
            code, _, _ = self.run_cli("setup", "--no-gravity")
            self.assertEqual(code, 0)
            self.assertIs(self.state()["telemetry"]["on"], want, value)

    def test_setup_leaves_the_state_alone_when_nothing_was_decided_at_install_time(self):
        for conf in ({}, {"SINKO_TELEMETRY": ""}, {"SINKO_TELEMETRY": "maybe"}):
            self.store.groups[:] = [g for g in self.store.groups if g["name"] == "Default"]
            self.conf = conf
            self.run_cli("setup", "--no-gravity")
            self.assertIsNone(self.state()["telemetry"]["on"], conf)

    def test_setup_never_overrides_the_parents_answer(self):
        self.setup_pihole()
        state = self.state()
        state["telemetry"]["on"] = False
        self.ctl.write_state(state)
        self.conf = {"SINKO_TELEMETRY": "1"}
        self.run_cli("setup", "--no-gravity")
        self.assertIs(self.state()["telemetry"]["on"], False, "an answer given on the page beats the one from the installer")
        writes = self.store.writes
        self.run_cli("setup", "--no-gravity")
        self.assertEqual(self.store.writes, writes, "and a second setup rewrites nothing")

    def counter(self):
        httpd, counter = fake_release.serve_counter()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        return counter

    def test_off_makes_the_counter_forget_the_box_and_says_so(self):
        counter = self.counter()
        self.setup_pihole()
        self.conf = {"SINKO_TELEMETRY_URL": counter.url}
        ident = pb.install_id()
        pb.note_counter_sent()
        counter.known.add(ident)
        code, out, err = self.run_cli("telemetry", "off")
        self.assertEqual(code, 0)
        self.assertIs(self.state()["telemetry"]["on"], False)
        self.assertEqual((counter.forgotten, counter.known), ([ident], set()))
        self.assertIn("asked to forget this box's number, and did", out)
        self.assertIsNone(pb.install_id(create=False))
        self.assertNotIn(ident, out + err)

    def test_off_when_the_counter_cannot_be_reached_says_it_keeps_trying(self):
        counter = self.counter()
        self.setup_pihole()
        self.conf = {"SINKO_TELEMETRY_URL": counter.url}
        ident = pb.install_id()
        pb.note_counter_sent()
        counter.fail_forget = 1
        code, out, err = self.run_cli("telemetry", "off")
        self.assertEqual(code, 0, "the answer was recorded")
        self.assertIs(self.state()["telemetry"]["on"], False)
        self.assertIn("could not be reached", out)
        self.assertIn("keeps trying", out)
        self.assertTrue(pb.forget_pending())
        self.assertEqual(pb.install_id(create=False), ident)
        self.assertNotIn(ident, out + err)

    def test_off_for_a_number_that_never_left_the_box_deletes_it_and_sends_nothing(self):
        counter = self.counter()
        self.setup_pihole()
        self.conf = {"SINKO_TELEMETRY_URL": counter.url}
        pb.install_id()
        code, out, _ = self.run_cli("telemetry", "off")
        self.assertEqual(code, 0)
        self.assertEqual(counter.requests, [])
        self.assertIn("never left the box", out)
        self.assertIsNone(pb.install_id(create=False))

    def test_off_without_a_counter_address_says_nothing_could_be_asked(self):
        self.setup_pihole()
        pb.install_id()
        pb.note_counter_sent()
        code, out, _ = self.run_cli("telemetry", "off")
        self.assertEqual(code, 0)
        self.assertIn("No counter address is set up", out)
        self.assertTrue(pb.forget_pending())

    def test_reset_id_makes_the_counter_forget_the_old_one_and_then_makes_a_new_one(self):
        counter = self.counter()
        self.conf = {"SINKO_TELEMETRY_URL": counter.url}
        old = pb.install_id()
        pb.note_counter_sent()
        counter.known.add(old)
        code, out, err = self.run_cli("telemetry", "reset-id")
        self.assertEqual(code, 0)
        new = pb.install_id(create=False)
        self.assertEqual((counter.forgotten, counter.known), ([old], set()))
        self.assertNotEqual(new, old)
        self.assertRegex(new, r"^[0-9a-f]{32}$")
        self.assertFalse(pb.counter_sent() or pb.forget_pending(), "the new id has not been sent anywhere")
        self.assertIn("by the counter", out)
        for secret in (old, new):
            self.assertNotIn(secret, out + err)

    def test_reset_id_keeps_the_old_id_when_the_counter_cannot_confirm(self):
        counter = self.counter()
        self.conf = {"SINKO_TELEMETRY_URL": counter.url}
        old = pb.install_id()
        pb.note_counter_sent()
        counter.fail_forget = 1
        code, out, err = self.run_cli("telemetry", "reset-id")
        self.assertEqual(code, 1)
        self.assertEqual(pb.install_id(create=False), old, "it still has to be forgotten")
        self.assertTrue(pb.forget_pending())
        self.assertIn("not replaced", err)
        self.assertNotIn(old, out + err)

    def test_reset_id_for_an_id_that_was_sent_but_with_no_address_any_more_replaces_it_and_says_so(self):
        old = pb.install_id()
        pb.note_counter_sent()
        code, out, _ = self.run_cli("telemetry", "reset-id")
        self.assertEqual(code, 0)
        self.assertNotEqual(pb.install_id(create=False), old)
        self.assertIn("no counter address is set up", out)
        self.assertFalse(pb.forget_pending())

    def test_setup_makes_the_runtime_folder(self):
        self.run_cli("setup", "--no-gravity")
        state_dir = os.path.join(self.tmp, "state")
        self.assertEqual(stat.S_IMODE(os.stat(state_dir).st_mode), 0o700)
        self.assertTrue(os.path.isdir(os.path.join(state_dir, "cache")))


if __name__ == "__main__":
    unittest.main()
