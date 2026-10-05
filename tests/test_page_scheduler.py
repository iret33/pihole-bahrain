"""The real parent page and the real scheduler, talking to each other through Pi-hole's pb-state group.

The page (web/) was tested against an imitation of the scheduler (SchedulerSim in tests/mock_pihole.py) and the scheduler
(Controller, Maintenance, Scheduler in bin/sinko) against states written by hand. Nothing tested the protocol BETWEEN the two
real programs (docs/maintainers/architecture.md, "Shared state"). Here Chromium drives the real page against the mock
Pi-hole while the real scheduler code ticks against the same state:

  * the scheduler is `pb.Scheduler.step()`: the real Controller.tick and the real Maintenance.run, logged in to the mock
    through SINKO_API_URL, keeping its files in a temporary SINKO_STATE_DIR. Only what would touch the machine is a
    recording fake, through the hooks Maintenance already has: the update runner, the power command, the address change,
    the repair and the post-crash check. The update check is the REAL check against a fake GitHub, the counter pings and
    forget requests are the REAL ones against the fake counter service, and box.json is written by the REAL writer;
  * time: the scheduler's passes use the real wall clock (the page compares update.at and box.json's `at` with the box's
    clock), while the timers of Maintenance (first check after 2 minutes, first ping after 5 ...) count a clock the test
    moves forward;
  * nobody ticks in the background. A test acts in the page, waits until the page's write has reached Pi-hole, and then
    lets the scheduler run a pass, so each step is deterministic. (The page and the scheduler both read, change and write
    the whole description; Pi-hole has no compare-and-swap, so two writes that truly overlap can lose one. That is a known
    limit of the protocol, not something a deterministic test can show, and no test here pretends otherwise.)

Needs Playwright with Chromium (pip install playwright && playwright install chromium); skipped, saying so, without them.
"""
import contextlib
import datetime as dt
import importlib.machinery
import importlib.util
import itertools
import json
import os
import re
import shutil
import stat
import tempfile
import time
import unittest
from unittest import mock

try:
    from playwright.sync_api import sync_playwright
except ImportError:                                  # the CI "unit" job installs no browser tooling
    raise unittest.SkipTest("Playwright is not installed (pip install playwright && playwright install chromium): "
                            "the test of the real page against the real scheduler needs a real browser")

import fake_release
import mock_pihole
import pbstrings
import test_maintenance as tm

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
WEB = os.path.join(ROOT, "web")
LISTS = os.path.join(ROOT, "lists")

loader = importlib.machinery.SourceFileLoader("sinko_cli_page_scheduler", os.path.join(ROOT, "bin", "sinko"))
spec = importlib.util.spec_from_loader("sinko_cli_page_scheduler", loader)
pb = importlib.util.module_from_spec(spec)
loader.exec_module(pb)

CATALOG = pb.load_catalog(LISTS)
VERSION = pb.VERSION
NEW = "3.1.0"
NOTES = "https://github.com/iret33/sinko/releases/tag/v3.1.0"
BOX_IP = "192.168.1.50"
ISOLATES = re.compile("[\u2066-\u2069]")             # the invisible marks that keep a version or a number whole inside Arabic text
STRINGS = {}


def T(lang, key, **values):
    """One of the page's words in a language, with {placeholders} filled in."""
    if not STRINGS:
        STRINGS.update(box=pbstrings.box_strings(), first=pbstrings.first_strings(), app=pbstrings.app_strings())
    for table in STRINGS.values():
        if key in table[lang]:
            return pbstrings.fmt(table[lang][key], **values)
    raise KeyError(key)


def clean(text):
    return ISOLATES.sub("", text)


class Box:
    """One box: the mock Pi-hole serving the real page, a fake GitHub, a fake counter service, and the real scheduler.

    Everything is released by the test case's cleanups. `tick()` is one or more passes of the real scheduler loop.
    """

    def __init__(self, case, offer=None, counter=False, setup_done=True, kids=True, ready=True):
        self.case = case
        self.stack = contextlib.ExitStack()
        case.addCleanup(self.close)
        self.tmp = tempfile.mkdtemp(prefix="sinko-page-scheduler-")
        self.stack.callback(shutil.rmtree, self.tmp, True)
        self.webroot = os.path.join(self.tmp, "www")
        os.makedirs(os.path.join(self.webroot, "pb"))
        self.config_path = os.path.join(self.tmp, "config")
        pw_file = os.path.join(self.tmp, "cli_pw")
        zone_file = os.path.join(self.tmp, "timezone")
        with open(pw_file, "w") as fh:
            fh.write(mock_pihole.PASSWORD)
        with open(zone_file, "w") as fh:
            fh.write("Asia/Bahrain\n")

        # --- the box's Pi-hole (serving the real page from web/), GitHub and the counter service
        self.httpd, self.store = mock_pihole.serve(0, WEB)
        self.stack.callback(self.httpd.server_close)
        self.stack.callback(self.httpd.shutdown)
        self.url = "http://127.0.0.1:%d/" % self.httpd.server_port
        self.github_httpd, self.github = fake_release.serve()
        self.stack.callback(self.github_httpd.server_close)
        self.stack.callback(self.github_httpd.shutdown)
        self.github.set_api(self.github.release_json(offer or VERSION))
        self.counter_httpd, self.counter = fake_release.serve_counter()
        self.stack.callback(self.counter_httpd.server_close)
        self.stack.callback(self.counter_httpd.shutdown)
        self.store.box_info = None                       # no imitation of box.json: the page gets the file the real writer wrote, or none

        # --- the settings and the environment of the scheduler
        with open(self.config_path, "w") as fh:
            fh.write("SINKO_HOSTNAME=family.lan\nSINKO_IP=%s\n" % BOX_IP)
            fh.write("".join("%s=%s\n" % item for item in self.github.conf().items()))
        env = {"SINKO_API_URL": self.url.rstrip("/"), "SINKO_STATE_DIR": os.path.join(self.tmp, "state"),
               "SINKO_RUN_DIR": os.path.join(self.tmp, "run"), "SINKO_WEBROOT": self.webroot,
               "SINKO_LOCALTIME": os.path.join(self.tmp, "no-such-link"), "SINKO_TIMEZONE_FILE": zone_file}
        if counter:
            env["SINKO_TELEMETRY_URL"] = self.counter.url
        self.stack.enter_context(mock.patch.dict(os.environ, env))
        for key in ("SINKO_RELEASE_BASE", "SINKO_RELEASE_API", "SINKO_REF", "SINKO_REPO", "SINKO_REPO_SLUG") + (() if counter else ("SINKO_TELEMETRY_URL",)):
            os.environ.pop(key, None)
        self.stack.enter_context(mock.patch.object(pb, "CLI_PW_FILE", pw_file))
        self.stack.enter_context(mock.patch.object(pb, "load_catalog", lambda *a, **k: CATALOG))
        # Whatever would touch this machine, or a network that is not the fakes above, fails the test if it is ever reached.
        self.reached = []
        for name in ("run_power", "start_update_runner", "apply_new_address", "default_route_ipv4", "run_self_heal", "recover_check",
                     "ftl_config", "ftl_set_config"):
            self.stack.enter_context(mock.patch.object(pb, name, side_effect=lambda *a, _n=name, **k: self.reached.append(_n)))
        self.stack.callback(lambda: case.assertEqual(self.reached, [], "a test reached the real machine (systemctl, FTL, a reboot)"))

        # --- Sinko is installed in Pi-hole, with one child's device (what `sinko setup` leaves)
        api = pb.Api(self.url.rstrip("/"), password=mock_pihole.PASSWORD)
        api.login()
        pb.Controller(api, CATALOG, LISTS).setup(run_gravity=False)
        if kids:
            gid = {g["name"]: g["id"] for g in api.groups()}
            api.request("POST", "/api/clients", {"client": "AA:BB:CC:00:00:01", "comment": "Sara's iPad", "groups": pb.kid_groups(gid, CATALOG)})
        api.logout()
        if setup_done:
            self.store.edit_state(lambda st: st.setdefault("setup", {}).update({"done": True}))

        # --- what the scheduler wrote to the state, what the fakes were asked to do
        self.sched_writes = []
        real_put = pb.Api.put_group

        def spy(api_self, name, comment, enabled, new_name=None):
            if name == pb.G_STATE:
                self.sched_writes.append(comment)
            return real_put(api_self, name, comment, enabled, new_name)
        self.stack.enter_context(mock.patch.object(pb.Api, "put_group", spy))
        self.runner_calls, self.power_calls, self.address_calls, self.heal_calls = [], [], [], []
        self.run_lock = None

        # --- the real scheduler
        self.mono = [1000.0]
        self.maintenance = pb.Maintenance(
            None, config_path=self.config_path, monotonic=lambda: self.mono[0], job_factory=tm.InlineJob, clock_ok=lambda: True,
            start_runner=self.fake_runner, power=self.fake_power, default_ip=lambda: BOX_IP, apply_address=self.fake_address,
            jitter=lambda low, high: 0, box_info=self.write_box_info, heal=self.fake_heal,
            recover=lambda: {"ok": True, "detail": ""}, catalog=lambda: CATALOG)
        self.maintenance.AUTO_WINDOW = (25, 26)         # the night's automatic update is not what is tested here, and no hour of the day may decide it
        self.scheduler = pb.Scheduler(clock=pb.ClockGuard(uptime=lambda: None, synchronized=lambda: None), maintenance=self.maintenance,
                                      gate=pb.LogGate(monotonic=lambda: self.mono[0]), repair=lambda: [], monotonic=lambda: self.mono[0])
        self.stack.callback(self.scheduler.close)
        self.stack.callback(self.release_lock)
        if ready:
            self.tick(2, advance=121)                    # the first check (2 minutes after start) and the first box.json are done

    def close(self):
        """Stops everything (also done by the test case's cleanup; calling it twice is fine)."""
        self.stack.close()

    # ----- the fakes: they record what they were asked, and what the files said at that moment -----
    def seen(self):
        return {"state": self.store.state(), "handled": pb.load_handled()}

    def fake_runner(self):
        self.runner_calls.append(self.seen())
        self.run_lock = pb.RunLock()                     # like the real runner, it holds the update lock for as long as it runs
        self.case.assertTrue(self.run_lock.acquire())
        return True

    def fake_power(self, action):
        self.power_calls.append(dict(self.seen(), action=action))

    def fake_address(self, ip, conf, path):
        self.address_calls.append(ip)

    def fake_heal(self):
        self.heal_calls.append(1)

    def release_lock(self):
        if self.run_lock is not None:
            self.run_lock.release()
            self.run_lock = None

    def box_clock(self):
        return time.time() + self.store.clock_skew

    def write_box_info(self, conf, **kw):
        """The REAL box.json writer, on the box's clock, with the two things that would run a program on this machine given."""
        return pb.write_box_info(conf, ip=BOX_IP, root=self.webroot, mdns=True, when=self.box_clock(), **kw)

    # ----- the real scheduler -----
    def tick(self, count=2, advance=0):
        """`count` passes of the real scheduler loop (a job's answer is collected on the pass after the one that started it).
        `advance` seconds go by on the clock Maintenance's timers count first."""
        self.mono[0] += advance
        for _ in range(count):
            self.scheduler.step()
        self.publish_box_json()

    def publish_box_json(self):
        """The mock serves /pb/box.json from memory: hand it the file the real writer left in the page's folder."""
        try:
            with open(os.path.join(self.webroot, "pb", "box.json"), "rb") as fh:
                self.store.box_raw = fh.read()
        except OSError:
            pass

    def box_json(self):
        with open(os.path.join(self.webroot, "pb", "box.json"), encoding="utf-8") as fh:
            return json.load(fh)

    def state(self):
        """The shared state as the scheduler's parser reads it."""
        return pb.parse_state(self.store.state_group()["comment"])

    def finish_update(self, status, error=None, rolled_back=None):
        """What the update runner does when it ends: the new files are in place (ok), its result is written, the lock is let go."""
        if status == "ok":
            self.store.version = NEW                     # /pb/version.txt now says so
        pb.write_update_result(status, VERSION, NEW, error, time.time(), rolled_back=rolled_back)
        self.release_lock()


class PageAndSchedulerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        try:
            if not os.path.exists(cls.playwright.chromium.executable_path):
                raise unittest.SkipTest("Chromium is not installed for Playwright (python3 -m playwright install chromium): "
                                        "the test of the real page against the real scheduler needs it")
            # sinko.local is what the quick-start card tells a parent to open: the browser is told where it is (the mock listens on the loopback)
            cls.browser = cls.playwright.chromium.launch(args=["--host-resolver-rules=MAP sinko.local 127.0.0.1"])
        except BaseException:
            cls.playwright.stop()
            raise

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def setUp(self):
        self.errors = []
        self.page_writes = []              # the comment of every PUT to pb-state the page sent, in order
        self.pages = []

    def wait(self, fn, seconds=10.0, step=0.1):
        """The first truthy answer of fn() within `seconds` (an exception counts as 'not yet'), else None. Between two tries the
        browser's events (a request the page sent, a route) are handled: Playwright's sync API only does that while it is called."""
        end = time.time() + seconds
        while time.time() < end:
            try:
                got = fn()
            except Exception:
                got = None
            if got:
                return got
            try:
                self.pages[-1].wait_for_timeout(step * 1000)
            except Exception:
                time.sleep(step)
        return None

    # ----- the page -----
    def open(self, box, lang="en", sign_in=True, host=None, clock_offset=0, **options):
        """A phone with the page open and the parent signed in. `clock_offset`: the phone's own clock is this many milliseconds off."""
        ctx = self.browser.new_context(**dict(dict(viewport={"width": 390, "height": 844}, locale="en-GB"), **options))
        self.addCleanup(ctx.close)
        if clock_offset:
            ctx.add_init_script("(() => { const real = Date.now; Date.now = () => real.call(Date) + %d; })();" % clock_offset)
        page = ctx.new_page()
        page.set_default_timeout(8000)
        # 401 (before sign-in) and 429 are expected; so is "no answer at all" while a box restarts its services.
        page.on("console", lambda m: self.errors.append(m.text) if m.type == "error" and not re.search(r"401|429|ERR_EMPTY_RESPONSE|ERR_CONNECTION", m.text) else None)
        page.on("pageerror", lambda e: self.errors.append(str(e)))
        page.on("request", lambda r: self.page_writes.append(json.loads(r.post_data)["comment"])
                if r.method == "PUT" and r.url.endswith("/api/groups/pb-state") else None)
        page.goto(box.url if host is None else "http://%s:%d/" % (host, box.httpd.server_port))
        if sign_in:
            page.wait_for_selector("#login:not([hidden])")
            if lang == "ar":
                page.click("#login .lang-toggle")
            page.fill("#pw", mock_pihole.PASSWORD)
            page.press("#pw", "Enter")
            page.wait_for_selector(".tile")
        self.pages.append(page)
        return page

    @staticmethod
    def open_box(page):
        page.click(".topbar [data-act=box]")
        page.wait_for_selector("#boxDialog[open]")
        page.wait_for_selector("#boxUpdLive .box-status")

    @staticmethod
    def live(page):
        try:
            return clean(page.inner_text("#boxUpdLive"))
        except Exception:                                # the page is reloading
            return ""

    @staticmethod
    def box_text(page):
        try:
            return clean(page.inner_text("#boxBody"))
        except Exception:
            return ""

    @staticmethod
    def reloaded(page):
        """True once the page has been reloaded since page.evaluate set `__before` on window."""
        return page.evaluate("() => window.__before === undefined")

    def finish(self, box, page=None):
        """What every test ends with. Whatever the page wrote is kept as it is by the scheduler's parser, whatever the scheduler wrote
        is kept as it is by the page's parser (the two normalise the same description), and the page logged no error."""
        for raw in self.page_writes:
            self.assertEqual(json.loads(raw), pb.parse_state(raw), "the scheduler's parser changes what the page wrote")
        page = page or (self.pages[-1] if self.pages else None)
        if page is not None and box.sched_writes:
            kept = page.evaluate("raws => raws.map(raw => PBCore.parseState(raw))", box.sched_writes)
            for raw, got in zip(box.sched_writes, kept):
                self.assertEqual(json.loads(raw), got, "the page's parser changes what the scheduler wrote")
        self.assertEqual(self.errors, [], "the page logged errors")
        self.assertEqual((box.address_calls, box.heal_calls), ([], []), "and the scheduler never took the page's doings for damage to repair")

    # ----------------------------------------------------------------------------------------------------------------
    # (1) "Update now" in My box
    # ----------------------------------------------------------------------------------------------------------------
    def press_update_now(self, box, page, lang, watch=True):
        """The parent opens My box and presses "Update now": what the page writes, and what the real scheduler does with it.
        `watch`: also wait until the page says that it is updating (the page looks at the state every few seconds)."""
        page.evaluate("() => { window.__before = 1; PBBox.timing.longDown = 600000; }")
        self.open_box(page)
        self.assertIn(T(lang, "boxAvailable", v=NEW), self.live(page), "the scheduler's own check put the offer there")
        link = page.locator("#boxUpdates a.box-link")
        self.assertEqual(link.get_attribute("href"), NOTES, "the release notes link is the one the scheduler built")
        page.click("#boxUpdateBtn")
        self.assertEqual(self.live(page), T(lang, "boxStarting"), "the page says at once that the update is starting")
        marker = self.wait(lambda: box.store.state()["update"]["request"])
        self.assertTrue(marker, "the page asked for the update")
        raw = box.store.state_group()["comment"]
        self.assertEqual(pb.parse_state(raw)["update"]["request"], marker, "the real parser accepts the page's marker as it is")
        self.assertEqual(box.runner_calls, [], "nothing happens until the scheduler looks")
        box.tick(1)
        self.assertEqual(len(box.runner_calls), 1, "the scheduler started the runner")
        before = box.runner_calls[0]
        self.assertEqual(before["handled"].get("update"), marker, "the marker was stored before the runner started")
        self.assertIsNone(before["state"]["update"]["request"], "and the request was already cleared")
        self.assertEqual(before["state"]["update"]["status"], "running", "and the state already said running")
        u = box.state()["update"]
        self.assertEqual((u["status"], u["from"], u["to"], u["request"], u["error"]), ("running", VERSION, NEW, None, None))
        self.assertEqual(pb.load_handled(), {"update": marker})
        box.tick(3)
        box.tick(2, advance=30)
        self.assertEqual(len(box.runner_calls), 1, "and it starts the runner once, not on every pass")
        if watch:
            self.assertTrue(self.wait(lambda: T(lang, "boxRunning", v=NEW) in self.live(page)), "the page says it is updating to 3.1.0: %r" % self.live(page))
            self.assertEqual(page.locator("#boxUpdates .box-bar[role=progressbar]").count(), 1, "with a calm progress bar")
        return marker

    def test_update_now_installs_the_new_version_and_the_page_reloads(self):
        self.update_now_ok("en")

    def update_now_ok(self, lang):
        box = Box(self, offer=NEW)
        page = self.open(box, lang)
        self.press_update_now(box, page, lang)
        box.finish_update("ok")
        self.assertTrue(os.path.exists(pb.state_path("update-result.json")), "the runner left its result")
        box.tick(2)
        u = box.state()["update"]
        self.assertEqual((u["status"], u["from"], u["to"], u["error"], u["rolledBack"], u["latest"], u["notes"]),
                         ("ok", VERSION, NEW, None, None, None, None), "the reconcile moved the runner's result into the state")
        self.assertFalse(os.path.exists(pb.state_path("update-result.json")), "and the file is gone")
        self.assertEqual(len(box.runner_calls), 1)
        # the page: the state says ok, the box now serves a newer version: it says so, then reloads itself
        page.wait_for_function("t => document.getElementById('boxUpdates').innerText.includes(t)", arg=T(lang, "boxReloading"), polling="raf")
        self.assertIn(T(lang, "boxUpdated", v=NEW), self.live(page))
        self.assertTrue(self.wait(lambda: self.reloaded(page)), "the page reloads itself because the box now has a newer version")
        page.wait_for_selector(".tile")
        self.assertTrue(page.is_hidden("#login"), "and the parent is still signed in")
        self.assertEqual(page.inner_text("#version"), "v" + NEW)
        self.assertTrue(self.wait(lambda: T(lang, "boxUpdatedToast", v=NEW) in clean(page.inner_text("#toast"))),
                        "a toast says what happened: %r" % page.inner_text("#toast"))
        self.open_box(page)
        self.assertNotIn(T(lang, "boxAvailable", v=NEW), self.box_text(page), "after the reload nothing is on offer any more")
        box.tick(3, advance=60)
        self.assertEqual((len(box.runner_calls), box.state()["update"]["status"]), (1, "ok"))
        self.finish(box, page)

    def failed_update(self, lang, rolled_back):
        box = Box(self, offer=NEW)
        page = self.open(box, lang)
        self.press_update_now(box, page, lang, watch=False)
        if rolled_back:
            error = "The installer stopped with an error (exit status 1). The previous version (%s) was put back." % VERSION
        else:
            error = "The installer stopped with an error (exit status 1). Going back to %s did not work either (exit status 1)." % VERSION
        box.finish_update("failed", error, rolled_back)
        box.tick(2)
        u = box.state()["update"]
        self.assertEqual((u["status"], u["from"], u["to"], u["error"], u["rolledBack"], u["latest"]),
                         ("failed", VERSION, NEW, error, rolled_back, NEW), "the reconcile moved the failure into the state, the release stays on offer")
        self.assertTrue(pb.failure_is_transient(u) is False and os.path.exists(pb.state_path("update-failure.json")), "and noted it for its own retry rule")
        self.assertTrue(self.wait(lambda: T(lang, "boxFailedTitle") in self.live(page)), "a failed update says so: %r" % self.live(page))
        text = self.box_text(page)
        details = page.locator("#boxUpdates details.box-details")
        self.assertEqual(details.count(), 1, "the technical reason waits behind a collapsed 'details for whoever helps you'")
        details.locator("summary").click()
        reason = page.locator("#boxUpdates details.box-details .box-reason")
        self.assertEqual((reason.inner_text(), reason.get_attribute("dir"), reason.get_attribute("lang")), (error, "ltr", "en"),
                         "opened, it is the runner's English text, left to right")
        if rolled_back:
            self.assertIn(T(lang, "boxFailedBack"), text, "the box said the previous version is back, so the page says it")
            self.assertNotIn(T(lang, "boxFailedNotBack"), text)
        else:
            self.assertIn(T(lang, "boxFailedNotBack"), text, "the previous version is not back, and the page says what to do instead")
            self.assertNotIn(T(lang, "boxFailedBack"), text, "and never claims that it is")
            self.assertIn("unplug", T("en", "boxFailedNotBack").lower())
        self.assertNotIn("sudo", text)
        self.assertNotIn("exit status", text, "nothing technical is part of what the parent reads")
        self.assertEqual((page.locator("#boxUpdateBtn").count(), page.locator("#boxCheckBtn").count()), (1, 1), "and it can be tried again")
        self.assertFalse(self.reloaded(page), "no reload: the page is the version that is installed")
        box.tick(3, advance=60)
        self.assertEqual((len(box.runner_calls), box.state()["update"]["status"]), (1, "failed"), "the scheduler does not try again by itself the same hour")
        self.finish(box, page)

    def test_a_failed_update_that_put_the_previous_version_back_is_told_that_way(self):
        self.failed_update("en", True)

    def test_a_failed_update_that_could_not_go_back_is_not_called_recovered(self):
        self.failed_update("en", False)

    def test_update_now_in_arabic(self):
        self.update_now_ok("ar")

    # ----------------------------------------------------------------------------------------------------------------
    # (2) "Check again"
    # ----------------------------------------------------------------------------------------------------------------
    def test_check_again_is_consumed_once(self):
        box = Box(self, offer=VERSION)                   # the newest release on GitHub is the one installed: the first check found nothing
        page = self.open(box)
        self.assertEqual(box.github.count("/api/latest"), 1, "the scheduler's own first check, two minutes after it started")
        before = box.state()["update"]
        self.assertEqual((before["latest"], before["checked"] > 0), (None, True))
        self.open_box(page)
        self.assertEqual(self.live(page), T("en", "boxUpToDate", t=T("en", "boxJustNow")))
        box.github.set_api(box.github.release_json(NEW))                  # a release has come out since
        page.click("#boxCheckBtn")
        self.assertEqual((page.inner_text("#boxCheckBtn"), page.is_disabled("#boxCheckBtn")), (T("en", "boxChecking"), True), "the button shows that it is checking")
        marker = self.wait(lambda: box.store.state()["update"]["checkRequest"])
        self.assertTrue(marker, "the page asked")
        raw = box.store.state_group()["comment"]
        self.assertEqual(pb.parse_state(raw)["update"]["checkRequest"], marker, "the real parser accepts the page's marker as it is")
        self.assertEqual(box.github.count("/api/latest"), 1, "GitHub is not asked until the scheduler looks")
        box.tick(2)
        self.assertEqual(box.github.count("/api/latest"), 2, "the scheduler checked, with the real check, once")
        after = box.state()["update"]
        self.assertEqual((after["checkRequest"], after["latest"], after["notes"]), (None, NEW, NOTES), "and answered in the state")
        self.assertGreater(after["checked"], before["checked"], "the page can see that the box looked")
        self.assertEqual(pb.load_handled(), {"check": marker})
        for _ in range(12):                                               # three minutes of passes
            box.tick(1, advance=15)
        self.assertEqual(box.github.count("/api/latest"), 2, "the request is consumed once, not on every pass")
        self.assertTrue(self.wait(lambda: T("en", "boxAvailable", v=NEW) in self.live(page)), "the page shows what the check found: %r" % self.live(page))
        self.assertEqual((page.inner_text("#boxCheckBtn"), page.is_disabled("#boxCheckBtn")), (T("en", "boxCheckAgain"), False))
        # a second copy of the same marker (a slow write, a restored backup) is cleared and is not a second check
        box.store.edit_state(lambda st: st["update"].update(checkRequest=marker))
        box.tick(2)
        self.assertEqual((box.github.count("/api/latest"), box.state()["update"]["checkRequest"]), (2, None))
        self.finish(box, page)

    def test_a_phone_with_a_wrong_clock_still_has_its_requests_obeyed(self):
        """The page writes the time of a request on the BOX's clock (from the Date header of its answers), because the scheduler drops
        a request whose time is far from its own clock (a restored backup's old requests): a phone that is hours off is no problem."""
        box = Box(self, offer=NEW)

        def check():
            return "#boxCheckBtn", lambda s: s["update"]["checkRequest"], lambda: box.github.count("/api/latest") == 2

        def restart():
            return "#boxRestartBtn", lambda s: s["power"]["request"], lambda: [c["action"] for c in box.power_calls] == ["reboot"]

        def update():
            return "#boxUpdateBtn", lambda s: s["update"]["request"], lambda: len(box.runner_calls) == 1
        for what, hours, ask in (("a phone 2 hours behind asks the box to check", -2, check), ("a phone 3 hours ahead asks it to restart", 3, restart),
                                 ("a phone a day behind asks it to update", -24, update)):
            with self.subTest(what):
                button, read, obeyed = ask()
                page = self.open(box, clock_offset=hours * 3600 * 1000)
                self.assertGreater(abs(page.evaluate("() => Date.now()") / 1000.0 - time.time()), 3600, "(set-up: the phone's clock is wrong)")
                self.open_box(page)
                if button == "#boxRestartBtn":
                    self.ask_to_power_off(page, button)
                else:
                    page.click(button)
                marker = self.wait(lambda: read(box.store.state()))
                self.assertTrue(marker, "the page asked")
                self.assertLess(abs(marker / 1000.0 - time.time()), 60, "with the time on the box's clock, not the phone's")
                box.tick(2)
                self.assertTrue(obeyed(), "and the scheduler obeyed it")
                self.assertEqual(read(box.state()), None, "and took it out of the state")
        self.finish(box)

    # ----------------------------------------------------------------------------------------------------------------
    # (3) restart and shut down
    # ----------------------------------------------------------------------------------------------------------------
    @staticmethod
    def ask_to_power_off(page, button):
        page.click(button)
        page.wait_for_selector("#confirmDialog[open]")
        page.click("#confirmYes")

    @staticmethod
    def power_note(page):
        return clean(page.inner_text("#boxPowerMsg"))

    def test_a_restart_asked_for_while_the_box_is_idle_reaches_systemctl_once(self):
        box = Box(self)
        page = self.open(box)
        self.open_box(page)
        self.assertEqual((page.is_disabled("#boxRestartBtn"), self.power_note(page)), (False, ""), "with no update the buttons are open")
        self.ask_to_power_off(page, "#boxRestartBtn")
        marker = self.wait(lambda: box.store.state()["power"]["request"])
        self.assertTrue(marker, "the page asked")
        raw = box.store.state_group()["comment"]
        self.assertEqual(pb.parse_state(raw)["power"], {"request": marker, "action": "reboot"}, "the real parser accepts the page's request as it is")
        self.assertEqual(box.power_calls, [], "nothing is done until the scheduler looks")
        box.tick(1)
        self.assertEqual([call["action"] for call in box.power_calls], ["reboot"], "the scheduler restarted the box")
        call = box.power_calls[0]
        self.assertEqual(call["handled"].get("power"), marker, "the marker was stored BEFORE the restart")
        self.assertEqual(call["state"]["power"], {"request": None, "action": None}, "and cleared in the state before it too")
        for _ in range(6):
            box.tick(2, advance=15)
        self.assertEqual(len(box.power_calls), 1, "once, not on every pass")
        self.assertTrue(self.wait(lambda: self.power_note(page) == T("en", "boxPowerRestarting")), "the page says the box is restarting: %r" % self.power_note(page))
        self.assertTrue(page.is_disabled("#boxRestartBtn") and page.is_disabled("#boxShutdownBtn"), "and both buttons wait")
        # another phone shuts the box down
        other = self.open(box)
        self.open_box(other)
        self.ask_to_power_off(other, "#boxShutdownBtn")
        second = self.wait(lambda: box.store.state()["power"]["request"])
        self.assertTrue(second and second != marker)
        self.assertEqual(pb.parse_state(box.store.state_group()["comment"])["power"], {"request": second, "action": "poweroff"})
        box.tick(1)
        self.assertEqual([c["action"] for c in box.power_calls], ["reboot", "poweroff"], "a new request is a new request")
        self.assertEqual(box.power_calls[1]["handled"].get("power"), second)
        box.tick(3, advance=30)
        self.assertEqual(len(box.power_calls), 2)
        self.assertTrue(self.wait(lambda: self.power_note(other) == T("en", "boxPowerOff")), "and that page says when to unplug and how to start again")
        self.finish(box, other)

    def test_a_restart_that_arrives_while_an_update_runs_is_dropped_and_the_page_shows_the_calm_note(self):
        box = Box(self, offer=NEW)
        phone = self.open(box)                           # the phone that asks for a restart
        self.open_box(phone)
        self.ask_to_power_off(phone, "#boxRestartBtn")
        asked = self.wait(lambda: box.store.state()["power"]["request"])
        self.assertTrue(asked, "the restart waits in the state")
        parent = self.open(box)                          # the phone that presses "Update now" before the scheduler has looked
        self.open_box(parent)
        parent.click("#boxUpdateBtn")
        self.assertTrue(self.wait(lambda: box.store.state()["update"]["request"]), "both requests are waiting when the scheduler looks")
        with self.assertLogs("sinko", level="INFO") as logs:
            box.tick(1)
        messages = [r.getMessage() for r in logs.records]
        self.assertIn("update requested by the page", messages)
        self.assertIn("power request ignored: an update is running", messages)
        self.assertEqual(box.power_calls, [], "the box is never switched off in the middle of an installation")
        self.assertEqual(len(box.runner_calls), 1)
        self.assertEqual(box.state()["power"], {"request": None, "action": None}, "the request was taken out of the state, not kept for later")
        self.assertEqual(pb.load_handled().get("power"), asked, "and stored as handled, so it never fires")
        box.tick(3, advance=30)
        self.assertEqual(box.power_calls, [])
        # the phone that asked: the calm note, the buttons wait, and not a word about unplugging
        note = T("en", "boxPowerWaitUpdate")
        self.assertTrue(self.wait(lambda: self.power_note(phone) == note), "the page says to wait for the update: %r" % self.power_note(phone))
        self.assertTrue(page_disabled(phone))
        self.assertNotIn("unplug", self.power_note(phone).lower())
        # the other phone: the buttons wait too, and pressing one anyway (a click that gets through) asks nothing of the box
        self.assertTrue(self.wait(lambda: self.power_note(parent) == note) and page_disabled(parent))
        self.pages[-1].wait_for_timeout(200)
        writes = len(self.page_writes)
        parent.evaluate("() => document.getElementById('boxRestartBtn').click()")
        parent.wait_for_timeout(500)
        self.assertEqual(parent.locator("#confirmDialog[open]").count(), 0, "no question is asked")
        self.assertEqual(len(self.page_writes), writes, "and the page does not even write a request")
        self.assertEqual((box.store.state()["power"], box.power_calls), ({"request": None, "action": None}, []))
        # the update ends (the runner could not install it): the page says that the box did not restart, and the buttons are open again
        box.finish_update("failed", "The installer stopped with an error (exit status 1). The previous version (%s) was put back." % VERSION, True)
        box.tick(2)
        self.assertTrue(self.wait(lambda: self.power_note(phone) == T("en", "boxPowerDropped")), "afterwards: %r" % self.power_note(phone))
        self.assertFalse(page_disabled(phone))
        self.assertEqual(box.power_calls, [], "and nothing was ever restarted")
        self.finish(box, parent)

    # ----------------------------------------------------------------------------------------------------------------
    # (4) the counter question
    # ----------------------------------------------------------------------------------------------------------------
    def test_the_counter_question_yes_then_no_ends_with_the_counter_forgetting_the_id(self):
        box = Box(self, counter=True, setup_done=False)
        counter = box.counter
        self.assertIs(box.box_json()["counter"], True, "the box's own writer says a counter address is set")
        page = self.open(box)
        page.wait_for_selector("#setup:not([hidden])")
        self.assertEqual(page.locator("#setupList [data-item=counter]").count(), 1, "the checklist asks the counter question")
        page.click("[data-act=counterYes]")
        self.assertTrue(self.wait(lambda: box.store.state()["telemetry"] == {"on": True}), "the answer is stored")
        self.assertEqual(counter.requests, [], "nothing has been sent")
        box.tick(2, advance=301)                         # the first ping is five minutes after the start
        ident = pb.install_id(create=False)
        self.assertRegex(ident, r"^[0-9a-f]{32}$")
        self.assertEqual(counter.pings, [ident], "the counter was pinged once, with the id the box made")
        path, headers, body = counter.requests[0]
        self.assertEqual((path, sorted(json.loads(body)), json.loads(body)["v"]), ("/v1/ping", ["hw", "id", "v"], VERSION))
        self.assertTrue(os.path.exists(pb.state_path("counter-sent")), "and it noted that the id has left the box")
        self.assertEqual(box.state()["community"]["online"], 1, "the answer is in the state")
        self.assertTrue(self.wait(lambda: box.store.state()["setup"] == {"done": True}), "(the page also ticked the whole list off, which survived the scheduler's writes)")
        self.open_box(page)
        self.assertTrue(self.wait(lambda: page.is_visible("#boxCounterOnline") and page.inner_text("#boxCounterOnline") == T("en", "boxCounterOnline", n="1")),
                        "the page says that this is one of 1 boxes online")
        self.assertTrue(page.is_checked("#boxCounter"))
        page.click("#boxCounterCard label.box-switch-row")              # No
        self.assertTrue(self.wait(lambda: box.store.state()["telemetry"] == {"on": False}), "the page stores the No")
        box.tick(2)
        self.assertEqual(counter.forgotten, [ident], "the scheduler's forget request reached the counter")
        self.assertEqual(json.loads(counter.requests[1][2]), {"id": ident})
        self.assertEqual(counter.known, set(), "which deleted the id")
        for name in ("install-id", "counter-sent", "forget-pending"):
            self.assertFalse(os.path.exists(pb.state_path(name)), name + " is gone from the box too")
        st = box.state()
        self.assertEqual((st["telemetry"], st["community"]), ({"on": False}, None), "the No is kept and the old number is gone")
        self.assertTrue(self.wait(lambda: page.is_hidden("#boxCounterOnline") and not page.is_checked("#boxCounter")))
        box.tick(3, advance=7 * 3600)
        box.tick(3, advance=7 * 3600)
        self.assertEqual((len(counter.requests), counter.pings, counter.forgotten), (2, [ident], [ident]), "and nothing more is ever sent")
        self.assertEqual(st["setup"], {"done": True})
        self.finish(box, page)

    def test_the_counter_is_not_offered_when_box_json_says_there_is_no_counter(self):
        box = Box(self, setup_done=False, kids=False)    # no counter address anywhere
        self.assertIs(box.box_json()["counter"], False, "the box's own writer says so")
        page = self.open(box)
        page.wait_for_selector("#setup:not([hidden])")
        items = page.evaluate("() => [...document.querySelectorAll('#setupList .setup-item')].map(i => i.dataset.item)")
        self.assertEqual(items, ["pw", "router", "child"], "the checklist has no counter question")
        self.assertNotIn(T("en", "setupCounterText"), page.inner_text("#setup"))
        self.open_box(page)
        self.assertEqual((page.locator("#boxCounterCard").count(), page.is_hidden("#boxCounterCard")), (1, True), "and My box has no counter card")
        self.assertNotIn(T("en", "boxCounterLead"), self.box_text(page))
        self.assertIn(T("en", "boxUpdTitle"), self.box_text(page), "while the rest of the sheet is there")
        self.finish(box, page)

    # ----------------------------------------------------------------------------------------------------------------
    # (6) box.json
    # ----------------------------------------------------------------------------------------------------------------
    def test_box_json_as_the_scheduler_writes_it_is_what_the_page_reads(self):
        box = Box(self, counter=True)
        path = os.path.join(box.webroot, "pb", "box.json")
        info = box.box_json()
        self.assertEqual(sorted(info), ["at", "counter", "ip", "mdns", "tz", "utcOffset", "v", "version"])
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o644, "readable by the web server and everybody on the network")
        self.assertEqual((info["v"], info["version"], info["ip"], info["tz"], info["counter"], info["mdns"]),
                         (1, VERSION, BOX_IP, "Asia/Bahrain", True, True))
        self.assertRegex(info["utcOffset"], r"^[+-]\d\d:\d\d$")
        self.assertLess(abs(info["at"] - time.time()), 60)
        page = self.open(box, host="sinko.local")        # opened by the name the quick-start card gives, which a router cannot use
        self.assertEqual(page.evaluate("raw => PBBox.pure.parseBoxInfo(raw)", info),
                         {"version": VERSION, "ip": BOX_IP, "tz": "Asia/Bahrain", "utcOffset": info["utcOffset"], "counter": True, "mdns": True, "at": info["at"]},
                         "the page keeps every field of what the writer wrote")
        help_text = clean(page.evaluate("() => document.getElementById('helpBody').textContent"))
        self.assertEqual(help_text, T("en", "helpBody", ip=BOX_IP), "the router help gives the number from box.json")
        self.assertNotIn("sinko.local", help_text)
        self.open_box(page)
        self.assertTrue(self.wait(lambda: page.locator("#boxNet .box-addr-value").count() >= 2))
        self.assertEqual(page.locator("#boxNet .box-addr-value").first.inner_text(), BOX_IP, "My box lists the number first")
        self.assertTrue(page.is_visible("#boxCounterCard"), "the counter card is there, because box.json says there is a counter")
        zone = T("en", "boxClockZoneKnown", z="Asia/Bahrain, UTC" + info["utcOffset"])
        self.assertIn(zone, clean(page.inner_text("#boxClockZone")), "and the card says which time zone bedtime follows")
        self.finish(box, page)

    def look_again(self, page):
        page.evaluate("() => document.dispatchEvent(new Event('visibilitychange'))")

    @staticmethod
    def banner(page):
        return page.is_visible("#banner") and clean(page.inner_text("#banner"))

    def test_a_box_json_that_is_25_minutes_old_by_the_box_clock_says_the_scheduler_is_not_running(self):
        box = Box(self)
        page = self.open(box)
        page.evaluate("() => { PBBox.timing.heartbeatGrace = 1500; }")
        self.look_again(page)
        page.wait_for_timeout(1800)
        self.look_again(page)
        page.wait_for_timeout(300)
        self.assertFalse(self.banner(page), "a scheduler that just wrote the file is running")
        # nobody ticks any more, and the box's own clock goes on: the file is 25 minutes old by it
        box.store.clock_skew = 25 * 60
        self.look_again(page)
        page.wait_for_timeout(1800)
        self.assertFalse(self.banner(page), "a late pulse is not believed at the first look (a corrected clock leaves the file a few seconds behind)")
        self.look_again(page)
        self.assertTrue(self.wait(lambda: self.banner(page) == T("en", "schedulerDown")), "seen late for a while: the timer-is-not-running banner: %r" % self.banner(page))
        self.assertNotRegex(self.banner(page), r"(?i)\b(sudo|systemctl|ssh|terminal)\b|sinko [a-z]+", "and it asks for no command")
        # the scheduler is running again: its next pass writes box.json afresh, by the box's clock
        box.tick(2, advance=301)
        self.look_again(page)
        self.assertTrue(self.wait(lambda: not self.banner(page), seconds=5), "the banner goes away by itself once the box writes the file again")
        self.finish(box, page)

    def test_the_scheduler_writes_box_json_often_enough_for_the_pages_twenty_minute_limit(self):
        box = Box(self)
        page = self.open(box)
        limit = page.evaluate("() => PBBox.pure.HEARTBEAT_LATE_SEC")
        last, writes, worst = box.box_json()["at"], 0, 0
        for _ in range(160):                             # 40 minutes of passes, fifteen seconds apart, by the box's clock
            box.store.clock_skew += 15
            box.tick(1, advance=15)
            at = box.box_json()["at"]
            writes += at != last
            last = at
            worst = max(worst, box.box_clock() - at)
        self.assertGreaterEqual(writes, 3, "the file is rewritten every ten minutes or so even when nothing changed")
        self.assertLess(worst, limit, "and its age never reaches what the page calls silence: %d s of %d s" % (worst, limit))
        self.assertFalse(page.evaluate("([at, now]) => PBBox.pure.heartbeatLate({ at: at }, now)", [last, last + worst]), "(the page's own function agrees)")
        self.assertTrue(page.evaluate("([at, now]) => PBBox.pure.heartbeatLate({ at: at }, now)", [last, last + limit + 60]))
        self.finish(box, page)


    # ----------------------------------------------------------------------------------------------------------------
    # (5) both sides write the same description
    # ----------------------------------------------------------------------------------------------------------------
    def test_neither_side_loses_a_field_the_other_wrote_over_many_interleaved_writes_and_passes(self):
        box = Box(self, counter=True, setup_done=False, kids=False)       # the checklist stays (no child yet) and the counter can be answered
        page = self.open(box)
        now = dt.datetime.now()
        bed = ((now - dt.timedelta(hours=2)).strftime("%H:%M"), (now + dt.timedelta(hours=2)).strftime("%H:%M"))     # bedtime is on right now
        model = {"auto": False, "setup": False, "bed": False, "telemetry": None, "latest": None}
        answers = itertools.cycle([NEW, "3.2.0", VERSION, NEW])          # what GitHub says at each round: the scheduler's own writes keep changing

        def dialog_open():
            return page.evaluate("() => document.getElementById('boxDialog').open")

        def main_page():
            if dialog_open():
                page.keyboard.press("Escape")
                page.wait_for_selector("#boxDialog:not([open])", state="attached")

        def sheet():
            if not dialog_open():
                self.open_box(page)

        def stored(read, want, what):
            self.assertTrue(self.wait(lambda: read(box.store.state()) == want), "the page stored " + what)

        def hide_setup():
            main_page()
            page.click("[data-act=setupHide]")
            stored(lambda s: s["setup"]["done"], True, "that the list is done")
            model["setup"] = True

        def bedtime(on):
            main_page()
            page.set_checked("#bedOn", on)
            if on:
                page.fill("#bedStart", bed[0])
                page.fill("#bedEnd", bed[1])
            page.click("[data-act=saveBed]")
            stored(lambda s: s["schedule"]["enabled"], on, "bedtime %s" % ("on" if on else "off"))
            model["bed"] = on

        def automatic(on):
            sheet()
            if page.is_checked("#boxAuto") != on:
                page.click("#boxUpdates label.box-switch-row")
            stored(lambda s: s["update"]["auto"], on, "the automatic switch")
            model["auto"] = on

        def counter(on):
            sheet()
            if page.is_checked("#boxCounter") != on:
                page.click("#boxCounterCard label.box-switch-row")
            stored(lambda s: s["telemetry"], {"on": on}, "the counter answer")
            model["telemetry"] = on

        def check_again():
            sheet()
            self.assertTrue(self.wait(lambda: not page.is_disabled("#boxCheckBtn")))
            page.click("#boxCheckBtn")
            stored(lambda s: s["update"]["checkRequest"] is not None, True, "a request to check")

        def scheduler_round(label):
            """A day passes on the scheduler's timers: it checks for a release (GitHub's answer changes every time), pings or forgets the
            counter, writes box.json, and applies bedtime: every one of them reads, changes and writes the shared state."""
            answer = next(answers)
            box.github.set_api(box.github.release_json(answer))
            box.tick(3, advance=25 * 3600)
            model["latest"] = None if answer == VERSION else answer
            st = box.state()
            where = "after %s" % label
            self.assertEqual((st["update"]["auto"], st["setup"]["done"], st["schedule"]["enabled"], st["telemetry"]["on"], st["update"]["latest"]),
                             (model["auto"], model["setup"], model["bed"], model["telemetry"], model["latest"]), where)
            self.assertEqual(st["scheduleActive"], model["bed"], "the scheduler's own field " + where)
            if model["bed"]:
                self.assertEqual((st["schedule"]["start"], st["schedule"]["end"]), bed, where)
            self.assertEqual(st["community"] is not None, model["telemetry"] is True, "the scheduler's number about the counter " + where)
            self.assertEqual((st["update"]["request"], st["update"]["checkRequest"], st["power"]["request"]), (None, None, None), where)

        script = [("the list is hidden", hide_setup), ("bedtime on", lambda: bedtime(True)), ("the switch on", lambda: automatic(True)),
                  ("counter yes", lambda: counter(True)), ("a check", check_again), ("the switch off", lambda: automatic(False)),
                  ("bedtime off", lambda: bedtime(False)), ("counter no", lambda: counter(False)), ("bedtime on again", lambda: bedtime(True)),
                  ("the switch on again", lambda: automatic(True)), ("counter yes again", lambda: counter(True)), ("a second check", check_again),
                  ("bedtime off again", lambda: bedtime(False)), ("counter no again", lambda: counter(False)), ("the switch off again", lambda: automatic(False))]
        for label, act in script:
            act()
            scheduler_round(label)
        # (these two guard the test itself: a scheduler that stopped writing, or a page that did not, would leave nothing interleaved to check)
        self.assertGreaterEqual(len(self.page_writes), len(script), "the page wrote at least once for every step")
        self.assertGreaterEqual(len(box.sched_writes), len(script) * 3 // 2, "and the scheduler wrote half as often again in between")
        self.assertEqual(len(box.counter.forgotten), 2, "(the counter was told to forget the box each time the answer became no)")
        # what the page makes of it all: reloaded, it shows exactly the merged state
        main_page()
        page.reload()
        page.wait_for_selector(".tile")
        self.assertEqual((page.is_checked("#bedOn"), page.is_hidden("#setup")), (False, True))
        self.open_box(page)
        self.assertEqual((page.is_checked("#boxAuto"), page.is_checked("#boxCounter")), (False, False))
        offered = T("en", "boxAvailable", v=model["latest"]) if model["latest"] else None
        self.assertTrue(offered in self.live(page) if offered else "Up to date" in self.live(page), "and what the scheduler last found: %r" % self.live(page))
        self.finish(box, page)

    # ----------------------------------------------------------------------------------------------------------------
    # (7) restoring a backup
    # ----------------------------------------------------------------------------------------------------------------
    def restore_backup(self, box, page, old_zip, when_the_import_is_in=None):
        """The parent restores a backup in My box. `when_the_import_is_in` runs once, at the moment the scheduler could look: Pi-hole holds
        the backup's description of pb-state and the page has not yet put back what belongs to this box (it asks for the state next)."""
        fired = []

        def route(handler, request):
            if request.method == "GET" and box.store.last_import is not None and not fired and when_the_import_is_in:
                fired.append(1)
                when_the_import_is_in()
            handler.continue_()
        page.route("**/api/groups/pb-state", route)
        self.open_box(page)
        page.set_input_files("#boxRestoreFile", {"name": "backup.zip", "mimeType": "application/zip", "buffer": old_zip})
        page.click("#boxRestoreBtn")
        page.wait_for_selector("#confirmDialog[open]")
        page.click("#confirmYes")
        self.assertTrue(self.wait(lambda: T("en", "boxRestoreDone") in self.box_text(page), seconds=20), "the restore finished: %r" % self.box_text(page)[-200:])
        page.unroute("**/api/groups/pb-state")
        if when_the_import_is_in:
            self.assertEqual(fired, [1], "the scheduler did look in the middle of the restore")

    def test_restoring_a_backup_with_old_requests_in_its_state_makes_the_scheduler_act_on_none_of_them(self):
        old = {"update": 1700000000001, "check": 1700000000002, "power": 1700000000003}
        for what, handled, in_the_middle in (
                ("a backup of another box (none of its requests was ever handled here), and the scheduler looks in the middle of the restore", {}, True),
                ("the same, and the scheduler looks when the restore is over", {}, False),
                ("this box's own backup: its requests were handled long ago, and the scheduler looks in the middle of the restore", old, True),
                ("this box's own backup, but later requests of every kind were handled since, and the scheduler looks in the middle of the restore",
                 {kind: marker + 5000 for kind, marker in old.items()}, True)):
            with self.subTest(what):
                box = Box(self, offer=NEW)
                page = self.open(box)
                for kind, marker in handled.items():
                    pb.set_handled(kind, marker)
                # the backup was made while a check, an update and a restart were waiting for the scheduler
                box.store.edit_state(lambda st: (st["update"].update(request=old["update"], checkRequest=old["check"]),
                                                 st["power"].update(request=old["power"], action="reboot")))
                box.store.clients.append({"id": 900, "client": "AA:BB:CC:00:00:09", "comment": "Ali's phone", "groups": [0, box.store.gid("pb-kids")], "name": None})
                old_zip = box.store.export_zip()
                box.store.clients.pop()
                box.store.edit_state(lambda st: (st["update"].update(request=None, checkRequest=None), st["power"].update(request=None, action=None)))
                checks = box.github.count("/api/latest")
                known_handled = dict(handled)
                self.restore_backup(box, page, old_zip, (lambda: box.tick(2)) if in_the_middle else None)
                self.assertIsNotNone(box.store.last_import, "the backup was imported")
                self.assertIn("Ali's phone", [c["comment"] for c in box.store.clients], "(and its devices came back)")
                box.tick(3)
                box.tick(3, advance=60)
                self.assertEqual(box.runner_calls, [], "no update was started")
                self.assertEqual(box.power_calls, [], "no restart or shut-down was done")
                self.assertEqual(box.github.count("/api/latest"), checks, "no check was made for it")
                self.assertEqual(pb.load_handled(), known_handled, "and the scheduler took none of the old markers")
                st = box.state()
                self.assertEqual((st["update"]["request"], st["update"]["checkRequest"], st["power"]), (None, None, {"request": None, "action": None}),
                                 "the state holds none of the backup's requests")
                self.assertEqual((st["update"]["status"], st["update"]["latest"], st["setup"]), ("idle", NEW, {"done": True}), "the rest of it is today's")
                self.finish(box, page)
                page.context.close()
                box.close()


def page_disabled(page):
    return page.is_disabled("#boxRestartBtn") and page.is_disabled("#boxShutdownBtn")


if __name__ == "__main__":
    unittest.main()
