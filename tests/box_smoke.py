"""Browser tests of the "My box" sheet, the first-run flows (claim screen, setup list, counter question), the update banner and the
branding, on the real parent page against the mock Pi-hole (tests/mock_pihole.py), in English and Arabic (right to left).

    python3 tests/box_smoke.py [--shots DIR] [--only PART]        (needs: pip install playwright && playwright install chromium)

Parts: structure, update, health, network, password, backup, power, counter, about, firstrun, banner, branding, motion, screenshots. The scheduler on
the box is played by mock_pihole.SchedulerSim, which handles the request markers the page writes the way the architecture says.
"""
import argparse
import importlib.machinery
import importlib.util
import io
import json
import os
import re
import sys
import tempfile
import time
import zipfile

from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
WEB = os.path.join(ROOT, "web")
sys.path.insert(0, HERE)
import mock_pihole  # noqa: E402
import pbstrings  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--shots", default=None)
ap.add_argument("--only", default=None, help="run one part (see above)")
args = ap.parse_args()

loader = importlib.machinery.SourceFileLoader("pb", os.path.join(ROOT, "bin", "sinko"))
spec = importlib.util.spec_from_loader("pb", loader)
pb = importlib.util.module_from_spec(spec)
loader.exec_module(pb)
CATALOG = pb.load_catalog(os.path.join(ROOT, "lists"))

BOX = pbstrings.box_strings()          # {en: {...}, ar: {...}} the My box sheet's words
FIRST = pbstrings.first_strings()      # the claim screen, setup list, update banner
APP = pbstrings.app_strings()          # sign-in, devices, bedtime
with open(os.path.join(ROOT, "VERSION"), encoding="utf-8") as fh:
    VERSION = fh.read().strip()
NOTES = "https://github.com/iret33/sinko/releases/tag/v3.1.0"
ARABIC_INDIC_DIGITS = re.compile("[٠-٩۰-۹]")

failures = []
errors = []
servers = []


def expect(cond, msg):
    print(("ok    " if cond else "FAIL  ") + msg)
    if not cond:
        failures.append(msg)


def T(lang, key, **values):
    """One of the page's words in a language, with {placeholders} filled in."""
    for table in (BOX, FIRST, APP):
        if key in table[lang]:
            return pbstrings.fmt(table[lang][key], **values)
    raise KeyError(key)


def eventually(fn, seconds=8.0, step=0.12):
    """The first truthy answer of fn() within `seconds` (exceptions count as 'not yet'), else None."""
    end = time.time() + seconds
    while time.time() < end:
        try:
            got = fn()
        except Exception:
            got = None
        if got:
            return got
        time.sleep(step)
    return None


def shot(page, name, full=False):
    if args.shots:
        os.makedirs(args.shots, exist_ok=True)
        page.screenshot(path=os.path.join(args.shots, name), full_page=full)


def make_site(installed=True, password=True, setup_done=True, kids=True):
    """A fresh mock box. Sinko is installed with one child's device (Sara's iPad), the setup list already dismissed."""
    httpd, store = mock_pihole.serve(0, WEB)
    servers.append(httpd)
    url = "http://127.0.0.1:%d/" % httpd.server_port
    if installed:
        api = pb.Api(url.rstrip("/"), password=mock_pihole.PASSWORD)
        api.login()
        pb.Controller(api, CATALOG, os.path.join(ROOT, "lists")).setup(run_gravity=False)
        if kids:
            gid = {g["name"]: g["id"] for g in api.groups()}
            api.request("POST", "/api/clients", {"client": "AA:BB:CC:00:00:01", "comment": "Sara's iPad", "groups": pb.kid_groups(gid, CATALOG)})
        api.logout()
        if setup_done:
            store.edit_state(lambda st: st.setdefault("setup", {}).update({"done": True}))
    if not password:
        store.set_password("")
        store.password_changes = 0
    return url, store


def offer_update(store, latest="3.1.0", checked_ago=3 * 3600):
    store.edit_state(lambda st: st.setdefault("update", {}).update(
        {"latest": latest, "notes": NOTES if latest == "3.1.0" else None, "checked": int(time.time()) - checked_ago}))


def open_page(browser, url, lang="en", sign_in=True, **opts):
    ctx = browser.new_context(**dict(dict(viewport={"width": 390, "height": 844}, locale="en-GB", device_scale_factor=2,
                                          permissions=["clipboard-read", "clipboard-write"]), **opts))
    page = ctx.new_page()
    page.set_default_timeout(6000)
    # 401 (before sign-in) and 429 are expected; so is "no answer at all" while the mock plays a box that restarts its services.
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" and not re.search(r"401|429|ERR_EMPTY_RESPONSE|ERR_CONNECTION", m.text) else None)
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(url)
    if sign_in:
        page.wait_for_selector("#login:not([hidden])")
        if lang == "ar":
            page.click("#login .lang-toggle")
        page.fill("#pw", mock_pihole.PASSWORD)
        page.press("#pw", "Enter")
        page.wait_for_selector(".tile")
    return ctx, page


def open_box(page):
    page.click(".topbar [data-act=box]")
    page.wait_for_selector("#boxDialog[open]")
    page.wait_for_selector("#boxUpdLive .box-status")


ISOLATES = re.compile("[\u2066-\u2069]")      # the invisible marks that keep a version or a number whole inside Arabic text


def clean(text):
    return ISOLATES.sub("", text)


def live(page):
    try:
        return clean(page.inner_text("#boxUpdLive"))
    except Exception:                                  # the page is reloading
        return ""


def box_text(page):
    try:
        return clean(page.inner_text("#boxBody"))
    except Exception:
        return ""


def active_id(page):
    return page.evaluate("() => document.activeElement && (document.activeElement.id || document.activeElement.getAttribute('data-act') || document.activeElement.tagName)")


def confirm_text(page):
    page.wait_for_selector("#confirmDialog[open]")
    return page.inner_text("#confirmText")


def sim_for(store, **kw):
    return mock_pihole.SchedulerSim(store, **dict(dict(version=VERSION, latest="3.1.0", notes=NOTES, run_seconds=2.0), **kw))


def reloaded(page, marker="__before"):
    """True once the page has been reloaded since page.evaluate set `marker` on window."""
    return page.evaluate("m => window[m] === undefined", marker)


# ------------------------------------------------------------------------------------------------------------------ parts
def part_structure(browser):
    for lang in ("en", "ar"):
        url, store = make_site()
        offer_update(store)
        ctx, page = open_page(browser, url, lang)
        expect(page.is_visible(".topbar [data-act=box]") and page.is_visible(".foot [data-act=box]"),
               "[%s] My box is a visible button in the top bar and a link in the footer" % lang)
        expect(page.inner_text(".topbar [data-act=box]").strip() == T(lang, "boxButton"), "[%s] the button says %r" % (lang, T(lang, "boxButton")))
        page.click(".foot [data-act=box]")
        page.wait_for_selector("#boxDialog[open]")
        expect(page.inner_text("#boxTitle") == T(lang, "boxTitle"), "[%s] opened from the footer link" % lang)
        expect(active_id(page) == "boxTitle", "[%s] focus moves to the sheet's title, so a screen reader starts there (got %s)" % (lang, active_id(page)))
        page.keyboard.press("Escape")
        page.wait_for_selector("#boxDialog:not([open])", state="attached")
        expect(page.evaluate("() => document.activeElement.getAttribute('data-act')") == "box" and page.evaluate("() => document.activeElement.closest('.foot') !== null"),
               "[%s] closing returns focus to the link that opened it" % lang)
        open_box(page)
        ids = page.evaluate("() => [...document.querySelectorAll('#boxBody > section')].map(s => s.id)")
        expect(ids == ["boxUpdates", "boxHealth", "boxNet", "boxPw", "boxBackup", "boxPower", "boxCounterCard", "boxAbout"],
               "[%s] the sheet has its eight cards in order: %s" % (lang, ids))
        titles = page.evaluate("() => [...document.querySelectorAll('#boxBody > section > h3')].map(h => h.textContent)")
        expect(all(titles), "[%s] every card has a heading" % lang)
        expect(page.get_attribute("#boxDialog", "aria-labelledby") == "boxTitle", "[%s] the dialog is named by its title" % lang)
        expect(page.get_attribute("#boxUpdLive", "aria-live") == "polite", "[%s] the update status is a polite live region" % lang)
        # touch targets: 44 px
        small = page.evaluate("""() => [...document.querySelectorAll('#boxDialog button, #boxDialog a, #boxDialog input:not(.sr-only), #boxDialog label.box-switch-row')]
            .filter(e => e.offsetParent !== null).map(e => { const r = e.getBoundingClientRect(); return [e.id || e.textContent.trim().slice(0, 20), Math.round(r.width), Math.round(r.height)]; })
            .filter(x => x[2] < 44 || x[1] < 44)""")
        small = [s for s in small if s[0] and not s[0].startswith("boxRestoreFile")]
        expect(not small, "[%s] every button, link and switch in the sheet is at least 44 px: %s" % (lang, small))
        text = box_text(page)
        expect(not ARABIC_INDIC_DIGITS.search(text), "[%s] digits are Latin digits" % lang)
        if lang == "ar":
            expect(page.get_attribute("html", "dir") == "rtl" and page.get_attribute("html", "lang") == "ar", "[ar] the sheet is right to left")
            flip = page.evaluate("() => { const s = document.querySelector('#boxBody .box-link svg'); return s ? getComputedStyle(s).transform : 'none'; }")
            expect("-1" in flip, "[ar] a direction-bearing icon is mirrored: %s" % flip)
        # a keyboard user can reach everything: Tab stays inside the open sheet
        seen = set()
        for _ in range(60):
            page.keyboard.press("Tab")
            # a modal dialog lets Tab pass to the browser's own controls (then document.body is active) but never to the page behind it
            seen.add(page.evaluate("() => document.activeElement === document.body ? 'browser' : (document.activeElement.closest('dialog') ? document.activeElement.closest('dialog').id : 'page behind')"))
        expect(seen <= {"boxDialog", "browser"} and "boxDialog" in seen, "[%s] Tab never reaches the page behind the open sheet: %s" % (lang, seen))
        outline = page.evaluate("() => { const s = getComputedStyle(document.activeElement); return [s.outlineStyle, parseFloat(s.outlineWidth)]; }")
        expect(outline[0] != "none" and outline[1] >= 2, "[%s] the focused control has a visible outline: %s" % (lang, outline))
        shot(page, "box-top-%s.png" % lang)
        ctx.close()


def part_update(browser):
    for lang in ("en", "ar"):
        # --- an update that works, with the box going silent for a while on the way
        url, store = make_site()
        sim = sim_for(store, outage=2.5, run_seconds=4.0)
        offer_update(store)
        ctx, page = open_page(browser, url, lang)
        page.evaluate("() => { window.__before = 1; PBBox.timing.longDown = 600000; }")
        open_box(page)
        expect(T(lang, "boxAvailable", v="3.1.0") in live(page), "[%s] a newer version is offered: %r" % (lang, live(page)))
        link = page.locator("#boxUpdates a.box-link")
        expect(link.get_attribute("href") == NOTES and link.get_attribute("target") == "_blank" and "noopener" in link.get_attribute("rel"),
               "[%s] release notes open the link the scheduler gave, in a new tab, safely" % lang)
        shot(page, "box-update-available-%s.png" % lang)
        page.click("#boxUpdateBtn")
        expect(active_id(page) == "boxUpdLive", "[%s] focus moves to the status when the button is gone (got %s)" % (lang, active_id(page)))
        expect(live(page) in (T(lang, "boxStarting"), T(lang, "boxRunning", v="3.1.0")), "[%s] it says at once that the update is starting: %r" % (lang, live(page)))
        expect(page.locator("#boxUpdates .box-bar[role=progressbar]").count() == 1 and page.locator("#boxUpdateBtn").count() == 0,
               "[%s] a calm progress bar replaces the buttons" % lang)
        expect(eventually(lambda: sim.log and sim.log[0] == ("update", "3.1.0") and store.state()["update"]["request"] is None),
               "[%s] the scheduler took the request marker (%s)" % (lang, sim.log))
        waited = eventually(lambda: page.locator("#boxUpdates .is-waiting").count() == 1, 6)
        expect(waited, "[%s] while the box restarts its services the page says it is waiting, calmly" % lang)
        expect(page.is_hidden("#login") and "did not work" not in page.inner_text("#toast"), "[%s] no sign-in screen and no error while the box is silent" % lang)
        shot(page, "box-update-waiting-%s.png" % lang)
        ok = eventually(lambda: T(lang, "boxRunning", v="3.1.0") in live(page), 8)
        expect(ok, "[%s] once the box answers again it says it is updating to 3.1.0: %r" % (lang, live(page)))
        shot(page, "box-update-running-%s.png" % lang)
        expect(eventually(lambda: store.version == "3.1.0", 12), "[%s] the update finishes" % lang)
        expect(eventually(lambda: reloaded(page), 12), "[%s] the page reloads itself because the box now has a newer version" % lang)
        page.wait_for_selector(".tile")
        expect(page.is_hidden("#login"), "[%s] and the parent is still signed in" % lang)
        expect(page.inner_text("#version") == "v3.1.0", "[%s] the footer shows v3.1.0 (%r)" % (lang, page.inner_text("#version")))
        expect(eventually(lambda: T(lang, "boxUpdatedToast", v="3.1.0") in clean(page.inner_text("#toast"))), "[%s] a toast says what happened: %r" % (lang, page.inner_text("#toast")))
        expect(store.state()["update"]["status"] == "ok", "[%s] the state says ok" % lang)
        open_box(page)
        expect("3.1.0" in box_text(page) and T(lang, "boxAvailable", v="3.1.0") not in box_text(page), "[%s] after the reload nothing is on offer any more" % lang)
        shot(page, "box-update-done-%s.png" % lang)
        ctx.close()

        # --- an update that fails: the reason, and the previous version is back
        url, store = make_site()
        sim = sim_for(store, outcome="failed", error="The download did not match its checksum.", run_seconds=1.0)
        offer_update(store)
        ctx, page = open_page(browser, url, lang)
        page.evaluate("() => { window.__before = 1; }")
        open_box(page)
        page.click("#boxUpdateBtn")
        expect(eventually(lambda: T(lang, "boxFailedTitle") in live(page), 10), "[%s] a failed update says so: %r" % (lang, live(page)))
        text = box_text(page)
        expect(T(lang, "boxFailedBack") in text, "[%s] and that the previous version is back" % lang)
        expect("The download did not match its checksum." in page.inner_text("#boxUpdates .box-reason"), "[%s] and why" % lang)
        expect(page.locator("#boxUpdateBtn").count() == 1 and page.locator("#boxCheckBtn").count() == 1, "[%s] and it can be tried again" % lang)
        expect(not reloaded(page), "[%s] no reload: the page is still the old version, which is the version that is installed" % lang)
        shot(page, "box-update-failed-%s.png" % lang)
        page.click("#boxDialog [data-act=boxClose]")
        expect(eventually(lambda: page.is_visible("#updateBanner") and page.inner_text("#updateBanner") == T(lang, "updateFailed")),
               "[%s] the banner on the main page says the last update failed" % lang)
        sim.outcome = "ok"
        page.click("#updateBanner")
        page.wait_for_selector("#boxUpdateBtn")
        page.click("#boxUpdateBtn")
        expect(eventually(lambda: store.version == "3.1.0", 10), "[%s] trying again works" % lang)
        ctx.close()

        # --- the box forgets the session while it updates (Pi-hole restarted)
        url, store = make_site()
        sim = sim_for(store, run_seconds=30.0)
        offer_update(store)
        ctx, page = open_page(browser, url, lang)
        open_box(page)
        page.click("#boxUpdateBtn")
        eventually(lambda: T(lang, "boxRunning", v="3.1.0") in live(page))
        store.sessions.clear()
        expect(eventually(lambda: page.locator("#boxSignInBtn").count() == 1, 8), "[%s] a lost session during an update is explained, not a silent bounce" % lang)
        expect(page.is_hidden("#login") and T(lang, "boxSignedOut") in box_text(page), "[%s] with a button to sign in again" % lang)
        page.click("#boxSignInBtn")
        page.wait_for_selector("#login:not([hidden])")
        expect(page.evaluate("() => !document.getElementById('boxDialog').open"), "[%s] the sheet is closed on the sign-in screen" % lang)
        ctx.close()

        # --- the scheduler picks the request up late, or never finishes
        url, store = make_site()
        sim = sim_for(store, run_seconds=None)
        offer_update(store)
        ctx, page = open_page(browser, url, lang)
        page.evaluate("() => { PBBox.timing.startLate = 1; }")
        open_box(page)
        page.click("#boxUpdateBtn")
        expect(eventually(lambda: T(lang, "boxRunningNote") in box_text(page)), "[%s] the calm note explains the wait" % lang)
        eventually(lambda: store.state()["update"]["status"] == "running")
        store.edit_state(lambda st: st["update"].update({"at": int(time.time()) - 3600}))
        expect(eventually(lambda: T(lang, "boxStalled") in live(page), 8), "[%s] a run that has said 'running' for over half an hour is called stuck" % lang)
        ctx.close()

        # --- check again: finds a newer version, or cannot reach the internet
        url, store = make_site()
        sim = sim_for(store, run_seconds=1.0)
        store.edit_state(lambda st: st.setdefault("update", {}).update({"checked": int(time.time()) - 3 * 3600}))
        ctx, page = open_page(browser, url, lang)
        page.evaluate("() => { PBBox.timing.checkOffline = 1500; }")
        open_box(page)
        expect(T(lang, "boxInstalled", v=VERSION) in box_text(page), "[%s] the installed version is shown" % lang)
        expect("3 " in live(page) or "٣" in live(page) or "3" in live(page), "[%s] 'up to date, checked 3 hours ago': %r" % (lang, live(page)))
        expect(not ARABIC_INDIC_DIGITS.search(live(page)), "[%s] Latin digits in the time" % lang)
        shot(page, "box-update-current-%s.png" % lang)
        page.click("#boxCheckBtn")
        expect(eventually(lambda: T(lang, "boxAvailable", v="3.1.0") in live(page), 8), "[%s] check again finds 3.1.0" % lang)
        ctx.close()
        url, store = make_site()
        sim = sim_for(store, offline=True, latest="3.1.0")
        ctx, page = open_page(browser, url, lang)
        page.evaluate("() => { PBBox.timing.checkOffline = 1500; }")
        open_box(page)
        page.click("#boxCheckBtn")
        expect(eventually(lambda: page.get_attribute("#boxCheckBtn", "disabled") is not None, 3), "[%s] the button shows it is checking" % lang)
        expect(eventually(lambda: T(lang, "boxCheckOffline") in box_text(page), 12), "[%s] an offline box says it could not reach the internet" % lang)
        ctx.close()

        # --- the automatic switch writes only its own field
        url, store = make_site()
        store.edit_state(lambda st: st.update({"telemetry": {"on": True}, "community": {"online": 5, "at": 1800000000}}))
        ctx, page = open_page(browser, url, lang)
        open_box(page)
        page.click("#boxUpdates label.box-switch-row")
        expect(eventually(lambda: store.state().get("update", {}).get("auto") is True), "[%s] 'update automatically at night' is saved" % lang)
        st = store.state()
        expect(st["telemetry"] == {"on": True} and st["community"]["online"] == 5 and st["setup"] == {"done": True}, "[%s] and nothing else in the state changed" % lang)
        page.click("#boxUpdates label.box-switch-row")
        expect(eventually(lambda: store.state()["update"]["auto"] is False), "[%s] and switching it off is saved too" % lang)
        ctx.close()

        # --- an update finished behind the page's back: opening the sheet reloads it
        url, store = make_site()
        ctx, page = open_page(browser, url, lang)
        page.evaluate("() => { window.__before = 1; }")
        store.version = "3.1.0"
        store.edit_state(lambda st: st.setdefault("update", {}).update({"status": "ok", "from": VERSION, "to": "3.1.0", "at": int(time.time()) - 30, "checked": int(time.time())}))
        page.click(".topbar [data-act=box]")
        expect(eventually(lambda: reloaded(page), 10), "[%s] a page older than the box reloads when My box is opened (an automatic update ran at night)" % lang)
        ctx.close()


def part_health(browser):
    for lang in ("en", "ar"):
        url, store = make_site()
        ctx, page = open_page(browser, url, lang)
        open_box(page)
        rows = lambda: page.evaluate("() => [...document.querySelectorAll('#boxHealth .box-row')].map(r => [r.querySelector('.box-label').textContent, r.querySelector('.box-value').textContent, r.querySelector('.box-note').textContent])")
        eventually(lambda: "…" not in [r[1] for r in rows()][0])
        r = rows()
        expect(r[0][1] == T(lang, "boxFilterOn"), "[%s] filtering is on: %r" % (lang, r[0]))
        expect(("3" in r[1][1]) and ("4" in r[1][1]), "[%s] uptime in words: %r" % (lang, r[1][1]))
        expect(r[2][1].startswith(T(lang, "boxTempCool", n="").strip().rstrip("، ,")) or "47" in r[2][1], "[%s] temperature in plain words: %r" % (lang, r[2][1]))
        expect("47" in r[2][1] and "°C" in r[2][1], "[%s] with the number: %r" % (lang, r[2][1]))
        expect("38" in r[3][1], "[%s] memory: %r" % (lang, r[3][1]))
        expect("Sara-iPad" in r[4][1], "[%s] the last device seen: %r" % (lang, r[4][1]))
        expect(r[5][2] == T(lang, "boxClockOk"), "[%s] the box clock matches this phone: %r" % (lang, r[5]))
        expect(not ARABIC_INDIC_DIGITS.search(" ".join(x[1] for x in r)), "[%s] Latin digits" % lang)
        shot(page, "box-health-%s.png" % lang)
        ctx.close()

        # filtering off, hot box, no sensor, a wrong clock, and one reading that fails
        url, store = make_site()
        store.blocking = "disabled"
        store.cpu_temp = 80.0
        store.clock_skew = 3 * 3600
        store.info_fail = {"system"}
        ctx, page = open_page(browser, url, lang)
        open_box(page)
        eventually(lambda: rows()[0][1] != "…")
        r = rows()
        expect(r[0][1] == T(lang, "boxFilterOff") and r[0][2] == T(lang, "boxFilterOffNote"), "[%s] switched-off filtering is said plainly with what it means: %r" % (lang, r[0]))
        expect("80" in r[2][1] and r[2][2] == T(lang, "boxTempHotNote"), "[%s] a hot box says to move it: %r" % (lang, r[2]))
        expect(r[1][1] == T(lang, "boxNotAvailable") and r[3][1] == T(lang, "boxNotAvailable"), "[%s] a reading that failed says 'not available' and the rest still shows: %r %r" % (lang, r[1], r[3]))
        expect(r[5][2] != T(lang, "boxClockOk") and "3" in r[5][2], "[%s] a clock that is 3 hours off is called out: %r" % (lang, r[5][2]))
        store.cpu_temp = None
        page.keyboard.press("Escape")
        open_box(page)
        eventually(lambda: rows()[2][1] == T(lang, "boxTempNone"))
        expect(rows()[2][1] == T(lang, "boxTempNone"), "[%s] a board with no sensor says so: %r" % (lang, rows()[2][1]))
        shot(page, "box-health-warnings-%s.png" % lang)
        ctx.close()
        errors[:] = [e for e in errors if "status of 500" not in e]            # the mock was told to fail a reading, on purpose


def part_network(browser):
    for lang in ("en", "ar"):
        url, store = make_site()
        port = url.rstrip("/").rsplit(":", 1)[1]
        store.config["dns"]["hosts"] = ["127.0.0.1 family.lan", "192.168.1.60 printer.lan"]
        ctx, page = open_page(browser, url, lang)
        open_box(page)
        rows = lambda: page.evaluate("() => [...document.querySelectorAll('#boxNet .box-addr-row')].map(r => [r.querySelector('.box-label').textContent, r.querySelector('.box-addr-value').textContent])")
        eventually(lambda: len(rows()) == 3)
        r = rows()
        expect(r == [[T(lang, "boxNetIp"), "127.0.0.1"], [T(lang, "boxNetName"), "family.lan:" + port], [T(lang, "boxNetLocal"), "sinko.local:" + port]],
               "[%s] number address, the name Pi-hole holds for it, and the .local name: %s" % (lang, r))
        expect("printer" not in box_text(page), "[%s] another device's record is not shown as the box" % lang)
        page.click("#boxNet .box-addr-row >> nth=0 >> .box-copy")
        copied = eventually(lambda: page.evaluate("() => navigator.clipboard.readText()") == "127.0.0.1")
        expect(copied, "[%s] the copy button puts the number on the clipboard" % lang)
        expect(T(lang, "boxCopied") in page.inner_text("#boxNet .box-addr-row >> nth=0"), "[%s] and says so" % lang)
        shot(page, "box-network-%s.png" % lang)
        ctx.close()
    # plain http on a home network is not a secure context: no clipboard API, so the fallback must work
    url, store = make_site()
    ctx, page = open_page(browser, url, "en")
    page.evaluate("() => { Object.defineProperty(navigator, 'clipboard', { value: undefined, configurable: true }); }")
    open_box(page)
    eventually(lambda: page.locator("#boxNet .box-copy").count() >= 1)
    page.click("#boxNet .box-copy >> nth=0")
    expect(eventually(lambda: "Copied" in page.inner_text("#boxNet .box-copy >> nth=0")), "copying still works without the clipboard API (an insecure page)")
    ctx.close()


def part_password(browser):
    for lang in ("en", "ar"):
        url, store = make_site()
        ctx, page = open_page(browser, url, lang)
        open_box(page)
        err = lambda: page.inner_text("#boxPwForm .form-error")
        page.fill("#boxPwNew", "abc")
        page.click("#boxPwBtn")
        expect(active_id(page) == "boxPwCur", "[%s] the first empty field gets the focus" % lang)
        page.fill("#boxPwCur", mock_pihole.PASSWORD)
        page.click("#boxPwBtn")
        expect(err() == T(lang, "boxPwShort"), "[%s] a short new password is refused: %r" % (lang, err()))
        page.fill("#boxPwNew", "new-secret-1")
        page.fill("#boxPwNew2", "new-secret-2")
        page.click("#boxPwBtn")
        expect(err() == T(lang, "boxPwMismatch"), "[%s] two different new passwords are refused: %r" % (lang, err()))
        page.fill("#boxPwNew2", "new-secret-1")
        page.fill("#boxPwCur", "wrong-password")
        page.click("#boxPwBtn")
        expect(eventually(lambda: err() == T(lang, "boxPwWrong")), "[%s] a wrong current password is said plainly: %r" % (lang, err()))
        expect(store.password_changes == 0 and store.deleted_sessions, "[%s] and nothing changed (the check's own session was deleted: %s)" % (lang, store.deleted_sessions))
        store.pwhash = mock_pihole.mock_hash("long-enough-pw")           # the mock's usual password is shorter than the new rule allows
        page.fill("#boxPwCur", "long-enough-pw")
        page.fill("#boxPwNew", "long-enough-pw")
        page.fill("#boxPwNew2", "long-enough-pw")
        page.click("#boxPwBtn")
        expect(err() == T(lang, "boxPwSame"), "[%s] the same password again is refused: %r" % (lang, err()))
        store.pwhash = mock_pihole.mock_hash(mock_pihole.PASSWORD)
        shot(page, "box-password-error-%s.png" % lang)
        # a second box where the check meets two-factor sign-in and the seats are full
        page.fill("#boxPwCur", mock_pihole.PASSWORD)
        page.fill("#boxPwNew", "new-secret-1")
        page.fill("#boxPwNew2", "new-secret-1")
        store.max_sessions = 1
        page.click("#boxPwBtn")
        expect(eventually(lambda: err() == T(lang, "boxPwTooMany")), "[%s] no free session for the check: %r" % (lang, err()))
        store.max_sessions = None
        store.totp_code = "123456"
        page.click("#boxPwBtn")
        expect(eventually(lambda: page.is_visible("#boxPwCode")), "[%s] with two-factor sign-in on, the code is asked for" % lang)
        page.click("#boxPwBtn")
        expect(err() == T(lang, "boxPwNeedCode"), "[%s] and it is required: %r" % (lang, err()))
        page.fill("#boxPwCode", "123456")
        mine = list(store.sessions)
        page.click("#boxPwBtn")
        page.wait_for_selector("#login:not([hidden])")
        expect(page.inner_text("#loginNote") == T(lang, "loginPwChanged"), "[%s] after the change the sign-in screen says so: %r" % (lang, page.inner_text("#loginNote")))
        expect(store.password_changes == 1 and not store.sessions, "[%s] the password changed once and every session ended" % lang)
        expect(len(store.deleted_sessions) >= 2 and store.deleted_sessions[-1] not in mine, "[%s] the check's session was deleted, not the page's own" % lang)
        expect(page.evaluate("() => !document.getElementById('boxDialog').open") and page.input_value("#pw") == "", "[%s] the sheet is closed and no password is left in a field" % lang)
        shot(page, "box-password-done-%s.png" % lang)
        store.totp_code = None
        page.fill("#pw", mock_pihole.PASSWORD)
        page.click("#loginBtn")
        expect(eventually(lambda: "not right" in page.inner_text("#loginErr") or T(lang, "wrongPassword") in page.inner_text("#loginErr")), "[%s] the old password no longer works" % lang)
        page.fill("#pw", "new-secret-1")
        page.click("#loginBtn")
        page.wait_for_selector(".tile")
        expect(True, "[%s] the new password signs in" % lang)
        page.click(".topbar [data-act=box]")
        page.wait_for_selector("#boxDialog[open]")
        expect(page.input_value("#boxPwCur") == "" and page.input_value("#boxPwNew") == "", "[%s] the password fields start empty each time" % lang)
        ctx.close()


def make_backup_zip(tables=None, pwhash=None):
    """A Teleporter archive made by hand (the mock's own format)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("etc/pihole/pihole.toml", '[webserver.api]\n  pwhash = "%s"\n' % (pwhash or "$MOCK$other"))
        for name in mock_pihole.GRAVITY_TABLES:
            z.writestr(name + ".json", json.dumps((tables or {}).get(name, [])))
    return buf.getvalue()


def part_backup(browser):
    for lang in ("en", "ar"):
        url, store = make_site()
        ctx, page = open_page(browser, url, lang)
        open_box(page)
        expect(T(lang, "boxBackupPrivate") in box_text(page), "[%s] the backup card says the file must be kept private (it holds the password)" % lang)
        with page.expect_download() as dl:
            page.click("#boxBackupBtn")
        d = dl.value
        expect(re.fullmatch(r"sinko-backup-\d{4}-\d{2}-\d{2}\.zip", d.suggested_filename) is not None, "[%s] the backup is a dated file: %s" % (lang, d.suggested_filename))
        data = open(d.path(), "rb").read()
        z = zipfile.ZipFile(io.BytesIO(data))
        expect("etc/pihole/pihole.toml" in z.namelist() and "client.json" in z.namelist(), "[%s] it is the Teleporter archive: %s" % (lang, sorted(z.namelist())[:4]))
        expect(T(lang, "boxBackupDone") in box_text(page) and store.exports == 1, "[%s] and the page says so" % lang)
        shot(page, "box-backup-%s.png" % lang)

        # --- restore: only children's devices, apps and rules come back; the password, settings and the box's own state stay
        store.edit_state(lambda st: st.update({"power": {"request": 1700000000002, "action": "poweroff"}}))      # a restart pending when the backup was made
        store.edit_state(lambda st: st.setdefault("update", {}).update({"status": "running", "request": 1700000000000, "from": "2.2.0", "to": "3.0.0", "at": 1700000000}))
        old_zip = store.export_zip()
        store.edit_state(lambda st: (st.update({"power": {"request": None, "action": None}}), st["update"].update(
            {"status": "ok", "request": None, "from": "3.0.0", "to": "3.1.0", "at": 1800000100, "auto": True, "latest": None, "notes": None})))
        store.edit_state(lambda st: st.update({"telemetry": {"on": False}, "community": {"online": 3, "at": 1800000000}}))
        gid = store.gid("pb-kids")
        store.clients.append({"id": 900, "client": "AA:BB:CC:00:00:09", "comment": "Ali's phone", "groups": [0, gid], "name": None})
        store.pwhash = mock_pihole.mock_hash("changed-since-the-backup")
        store.sessions.add(page.evaluate("() => sessionStorage.getItem('pb.sid')"))
        page.reload()
        page.wait_for_selector(".tile")
        expect("Ali's phone" in page.inner_text("#devices"), "[%s] (set-up: the box now has a second child's device)" % lang)
        open_box(page)
        expect(page.is_disabled("#boxRestoreBtn"), "[%s] Restore waits for a file" % lang)
        page.set_input_files("#boxRestoreFile", {"name": "my-backup.zip", "mimeType": "application/zip", "buffer": old_zip})
        expect(page.inner_text("#boxRestoreName") == "my-backup.zip" and not page.is_disabled("#boxRestoreBtn"), "[%s] the chosen file is named" % lang)
        page.click("#boxRestoreBtn")
        expect("my-backup.zip" in confirm_text(page), "[%s] the confirmation names the file: %r" % (lang, confirm_text(page)))
        shot(page, "box-restore-confirm-%s.png" % lang)
        page.click("#confirmDialog button[value=no]")
        page.wait_for_selector("#confirmDialog:not([open])", state="attached")
        expect(store.last_import is None, "[%s] cancelling imports nothing" % lang)
        page.click("#boxRestoreBtn")
        confirm_text(page)
        page.click("#confirmYes")
        expect(eventually(lambda: T(lang, "boxRestoreDone") in box_text(page), 12), "[%s] restore finishes" % lang)
        li = store.last_import
        expect(li and li["import"] == {"config": False, "dhcp_leases": False, "gravity": {t: True for t in mock_pihole.GRAVITY_TABLES}},
               "[%s] only the seven gravity tables were asked for (config and DHCP leases explicitly off): %s" % (lang, li and li["import"]))
        expect(li and "etc/pihole/pihole.toml" not in li["processed"], "[%s] the configuration was not touched" % lang)
        expect(store.pwhash == mock_pihole.mock_hash("changed-since-the-backup") and store.password_changes == 0, "[%s] the password stayed as it is now" % lang)
        expect([c["comment"] for c in store.clients] == ["Sara's iPad"], "[%s] the second child's device is gone, as in the backup: %s" % (lang, [c["comment"] for c in store.clients]))
        st = store.state()
        expect(st["power"] == {"request": None, "action": None}, "[%s] a restart that was pending in the backup does not happen" % lang)
        expect(st["update"]["status"] == "ok" and st["update"]["auto"] is True and st["update"]["request"] is None and st["update"]["to"] == "3.1.0",
               "[%s] the update fields are today's, not the backup's 'running': %s" % (lang, st["update"]))
        expect(st["telemetry"] == {"on": False} and st["community"]["online"] == 3, "[%s] the counter answer is today's" % lang)
        expect("Ali" not in page.inner_text("#devices") and "Sara" in page.inner_text("#devices"), "[%s] the page behind shows the restored devices" % lang)
        shot(page, "box-restore-done-%s.png" % lang)
        ctx.close()

        # --- a file that is not a Sinko backup: refused, nothing changed
        url, store = make_site()
        ctx, page = open_page(browser, url, lang)
        open_box(page)
        before = json.dumps(store.tables(), sort_keys=True)
        page.set_input_files("#boxRestoreFile", {"name": "photo.zip", "mimeType": "application/zip", "buffer": b"this is not a zip file"})
        page.click("#boxRestoreBtn")
        confirm_text(page)
        page.click("#confirmYes")
        expect(eventually(lambda: T(lang, "boxRestoreBad") in box_text(page)), "[%s] a file that is not a backup is refused plainly" % lang)
        expect(json.dumps(store.tables(), sort_keys=True) == before, "[%s] and nothing changed" % lang)
        # a real archive that has no Sinko in it: Pi-hole accepts it, so the page puts everything back
        only_default = make_backup_zip({"group": [{"id": 0, "name": "Default", "enabled": 1, "description": "The default group"}]})
        page.set_input_files("#boxRestoreFile", {"name": "other-box.zip", "mimeType": "application/zip", "buffer": only_default})
        page.click("#boxRestoreBtn")
        confirm_text(page)
        page.click("#confirmYes")
        expect(eventually(lambda: store.last_import and store.last_import["processed"] and any("group" in p for p in store.last_import["processed"]), 8), "[%s] (the foreign archive was imported)" % lang)
        expect(eventually(lambda: T(lang, "boxRestoreBad") in box_text(page), 15), "[%s] a Pi-hole backup without Sinko is refused too" % lang)
        expect(eventually(lambda: json.dumps(store.tables(), sort_keys=True) == before, 10), "[%s] and the box is exactly as it was: parental controls were not broken" % lang)
        expect("pb-kids" in [g["name"] for g in store.groups], "[%s] Sinko's groups are back" % lang)
        ctx.close()
        errors[:] = [e for e in errors if "status of 400" not in e]            # Pi-hole refusing a file is the point of this part


def part_power(browser):
    for lang in ("en", "ar"):
        url, store = make_site()
        sim = sim_for(store, power_outage=2.0)
        ctx, page = open_page(browser, url, lang)
        open_box(page)
        page.click("#boxRestartBtn")
        text = confirm_text(page)
        expect(text == T(lang, "boxRestartAsk"), "[%s] the restart confirmation says the internet stops for the children: %r" % (lang, text))
        shot(page, "box-power-confirm-%s.png" % lang)
        page.click("#confirmDialog button[value=no]")
        page.wait_for_selector("#confirmDialog:not([open])", state="attached")
        time.sleep(0.4)
        expect(store.power_log == [] and store.state()["power"]["request"] is None, "[%s] cancelling asks for nothing" % lang)
        page.click("#boxRestartBtn")
        confirm_text(page)
        page.click("#confirmYes")
        expect(eventually(lambda: store.power_log == ["reboot"]), "[%s] the request reached the scheduler: %s" % (lang, store.power_log))
        expect(eventually(lambda: page.inner_text("#boxPowerMsg") == T(lang, "boxPowerRestarting")), "[%s] the page says the box is restarting: %r" % (lang, page.inner_text("#boxPowerMsg")))
        expect(page.is_disabled("#boxRestartBtn") and page.is_disabled("#boxShutdownBtn"), "[%s] both buttons wait" % lang)
        shot(page, "box-power-restarting-%s.png" % lang)
        page.wait_for_selector("#login:not([hidden])", timeout=20000)
        expect(page.inner_text("#loginNote") == T(lang, "boxBackNote"), "[%s] when the box is back the sign-in screen says it restarted: %r" % (lang, page.inner_text("#loginNote")))
        ctx.close()

        url, store = make_site()
        sim = sim_for(store, power_outage=60.0)
        ctx, page = open_page(browser, url, lang)
        open_box(page)
        page.click("#boxShutdownBtn")
        expect(confirm_text(page) == T(lang, "boxShutdownAsk"), "[%s] the shut-down confirmation says the internet stays off until the box is unplugged and plugged in" % lang)
        page.click("#confirmYes")
        expect(eventually(lambda: store.power_log == ["poweroff"]) and eventually(lambda: page.inner_text("#boxPowerMsg") == T(lang, "boxPowerOff")),
               "[%s] shut down: the page says when to unplug and how to start it again" % lang)
        shot(page, "box-power-off-%s.png" % lang)
        ctx.close()

        # nobody on the box listens (the scheduler is not running): the page says so and withdraws the request
        url, store = make_site()
        ctx, page = open_page(browser, url, lang)
        page.evaluate("() => { PBBox.timing.powerStuck = 1500; }")
        open_box(page)
        page.click("#boxRestartBtn")
        confirm_text(page)
        page.click("#confirmYes")
        expect(eventually(lambda: page.inner_text("#boxPowerMsg") == T(lang, "boxPowerStuck"), 10), "[%s] a box that does not react is told to be unplugged and plugged in: %r" % (lang, page.inner_text("#boxPowerMsg")))
        expect(eventually(lambda: store.state()["power"] == {"request": None, "action": None}), "[%s] and the request is withdrawn, so it cannot fire later by surprise" % lang)
        ctx.close()


def part_counter(browser):
    for lang in ("en", "ar"):
        url, store = make_site()
        store.edit_state(lambda st: st.update({"community": {"online": 1234, "at": 1800000000}, "telemetry": {"on": False}}))
        ctx, page = open_page(browser, url, lang)
        open_box(page)
        text = box_text(page)
        for key in ("boxCounterLead", "boxCounterSent1", "boxCounterSent2", "boxCounterSent3", "boxCounterKept", "boxCounterNot"):
            expect(T(lang, key) in text, "[%s] the card says %s" % (lang, key))
        expect(not page.is_checked("#boxCounter") and page.is_hidden("#boxCounterOnline"), "[%s] the counter is off until the parent says yes, and nothing about others is shown" % lang)
        page.click("#boxCounterCard label.box-switch-row")
        expect(eventually(lambda: store.state()["telemetry"] == {"on": True}), "[%s] switching it on records the answer" % lang)
        expect(eventually(lambda: page.is_visible("#boxCounterOnline")) and "1,234" in page.inner_text("#boxCounterOnline"), "[%s] 'you are one of N boxes online': %r" % (lang, page.inner_text("#boxCounterOnline")))
        expect(page.inner_text("#boxCounterOnline") == T(lang, "boxCounterOnline", n="1,234"), "[%s] with the number written with Latin digits" % lang)
        expect(T(lang, "boxCounterOnToast") in page.inner_text("#boxToast"), "[%s] a thank-you shows inside the sheet (a toast outside it would be hidden)" % lang)
        shot(page, "box-counter-%s.png" % lang)
        page.click("#boxCounterCard label.box-switch-row")
        expect(eventually(lambda: store.state()["telemetry"] == {"on": False}) and eventually(lambda: page.is_hidden("#boxCounterOnline")), "[%s] switching it off records false and hides the number" % lang)
        ctx.close()


def part_about(browser):
    for lang in ("en", "ar"):
        url, store = make_site()
        ctx, page = open_page(browser, url, lang)
        open_box(page)
        eventually(lambda: page.locator("#boxAbout a").count() >= 5)
        text = clean(page.inner_text("#boxAbout"))
        expect(T(lang, "boxAboutVersion", v=VERSION) in text, "[%s] the version: %r" % (lang, page.inner_text("#boxAboutVersion")))
        expect("GPL-3.0-or-later" in text, "[%s] the licence is named" % lang)
        expect(T(lang, "boxAboutTrademark") in text, "[%s] the trademark sentence is there" % lang)
        links = page.evaluate("() => [...document.querySelectorAll('#boxAbout a')].map(a => [a.getAttribute('href'), a.getAttribute('target'), a.getAttribute('rel')])")
        want = json.load(open(os.path.join(WEB, "links.json")))
        expect([l[0] for l in links] == [want[k] for k in ("home", "issues", "releases", "privacy", "license")], "[%s] the links come from links.json and empty ones are left out: %s" % (lang, [l[0] for l in links]))
        expect(all(l[1] == "_blank" and "noopener" in l[2] for l in links), "[%s] they open in a new tab, safely" % lang)
        shot(page, "box-about-%s.png" % lang)
        ctx.close()


def part_firstrun(browser):
    for lang in ("en", "ar"):
        # --- the claim screen: no password yet, and Sinko is installed
        url, store = make_site(password=False, setup_done=False, kids=False)
        ctx, page = open_page(browser, url, lang, sign_in=False)
        page.wait_for_selector("#claim:not([hidden])")
        if lang == "ar":
            page.click("#claim .lang-toggle")
        expect(page.is_hidden("#login") and page.is_hidden("#app"), "[%s] a box with no password offers to choose one, not a sign-in" % lang)
        expect(page.inner_text("#claim h1") == T(lang, "claimTitle") and T(lang, "claimLead") in page.inner_text("#claim"), "[%s] welcome text" % lang)
        expect(page.inner_text("#claim .tagline") == T(lang, "tagline"), "[%s] with the tagline" % lang)
        shot(page, "claim-%s.png" % lang)
        page.fill("#claimPw", "short")
        page.fill("#claimPw2", "short")
        page.click("#claimBtn")
        expect(page.inner_text("#claimErr") == T(lang, "claimShort"), "[%s] a short password is refused: %r" % (lang, page.inner_text("#claimErr")))
        page.fill("#claimPw", "family-first-1")
        page.fill("#claimPw2", "family-first-2")
        page.click("#claimBtn")
        expect(page.inner_text("#claimErr") == T(lang, "claimMismatch"), "[%s] two different passwords are refused" % lang)
        expect(store.password_changes == 0, "[%s] nothing was set yet" % lang)
        page.fill("#claimPw2", "family-first-1")
        page.click("#claimBtn")
        page.wait_for_selector("#app:not([hidden])")
        expect(store.password_changes == 1 and store.check_password("family-first-1"), "[%s] the password was set" % lang)
        expect(eventually(lambda: bool(page.evaluate("() => sessionStorage.getItem('pb.sid')"))), "[%s] and the parent is signed in with it, no second sign-in" % lang)
        expect(page.is_hidden("#claim") and page.is_visible("#setup"), "[%s] then the setup list is shown" % lang)
        expect(page.input_value("#claimPw") == "", "[%s] no password is left in the form" % lang)
        ctx.close()

        # --- a box with no password and no Sinko on it is not claimed here
        url, store = make_site(installed=False, password=False)
        ctx, page = open_page(browser, url, lang, sign_in=False)
        page.wait_for_selector("#app:not([hidden])")
        expect(page.is_hidden("#claim"), "[%s] no claim screen while Sinko is not installed" % lang)
        expect(eventually(lambda: page.is_visible("#banner")), "[%s] the page explains that setup is not finished" % lang)
        banner = page.inner_text("#banner")
        expect("sudo" not in banner and "systemctl" not in banner and "run:" not in banner and "نفّذ" not in banner, "[%s] without asking a parent to type a command: %r" % (lang, banner))
        ctx.close()

        # --- the setup list
        url, store = make_site(setup_done=False, kids=False)
        store.devices[:] = [store.devices[0]]                 # only the parent's own phone has asked the box so far
        ctx, page = open_page(browser, url, lang)
        page.wait_for_selector("#setup:not([hidden])")
        items = lambda: page.evaluate("() => [...document.querySelectorAll('#setupList .setup-item')].map(i => [i.dataset.item, i.classList.contains('is-done')])")
        expect(items() == [["pw", True], ["router", False], ["child", False], ["counter", False]], "[%s] four steps, the password done: %s" % (lang, items()))
        expect(page.inner_text("#setupProgress") == T(lang, "setupProgress", n=1, t=4), "[%s] progress: %r" % (lang, page.inner_text("#setupProgress")))
        expect(not ARABIC_INDIC_DIGITS.search(page.inner_text("#setup")), "[%s] Latin digits" % lang)
        shot(page, "setup-list-%s.png" % lang)
        expect(T(lang, "setupCounterText") in page.inner_text("#setup"), "[%s] the counter question is asked in plain words" % lang)
        page.click("[data-act=counterNo]")
        expect(eventually(lambda: store.state()["telemetry"] == {"on": False}), "[%s] 'Not now' records false" % lang)
        expect(eventually(lambda: items()[3][1]), "[%s] and the step is ticked" % lang)
        expect(T(lang, "setupCounterDone") in page.inner_text("#setup"), "[%s] with its answered wording" % lang)
        page.click("[data-act=setupHelp]")
        expect(page.evaluate("() => document.getElementById('help').open"), "[%s] 'show setup help' opens the router help" % lang)
        page.click("#setupList [data-act=openAdd]")
        page.wait_for_selector("#addDialog[open]")
        page.click("#addForm .manual summary")
        page.fill("#manualAddr", "AA:BB:CC:00:00:77")
        page.fill("#devName", "Tablet")
        page.click("[data-act=addDevice]")
        expect(eventually(lambda: items()[2][1]), "[%s] adding the first child's device ticks that step" % lang)
        store.devices.append({"id": 9, "hwaddr": "aa:bb:cc:00:00:05", "macVendor": "", "lastQuery": int(time.time()) - 20, "numQueries": 5,
                              "ips": [{"ip": "192.168.1.33", "name": "TV"}]})
        page.reload()
        page.wait_for_selector(".tile")
        expect(eventually(lambda: page.is_hidden("#setup")), "[%s] when every step is done the list goes away by itself" % lang)
        expect(eventually(lambda: store.state()["setup"] == {"done": True}), "[%s] and is remembered, so it does not come back" % lang)
        ctx.close()

        # dismiss writes setup.done
        url, store = make_site(setup_done=False, kids=False)
        store.devices[:] = [store.devices[0]]
        ctx, page = open_page(browser, url, lang)
        page.wait_for_selector("#setup:not([hidden])")
        page.click("[data-act=setupHide]")
        expect(eventually(lambda: page.is_hidden("#setup")) and eventually(lambda: store.state()["setup"] == {"done": True}), "[%s] 'hide this list' dismisses it for good" % lang)
        page.reload()
        page.wait_for_selector(".tile")
        expect(page.is_hidden("#setup"), "[%s] it stays hidden after a reload" % lang)
        ctx.close()

        # the router step counts other devices, never the parent's own phone or the box itself
        url, store = make_site(setup_done=False, kids=False)
        store.devices[:] = [store.devices[0], {"id": 8, "hwaddr": "ip-127.0.0.1", "macVendor": "", "lastQuery": int(time.time()) - 5, "numQueries": 4, "ips": [{"ip": "127.0.0.1", "name": "box"}]}]
        ctx, page = open_page(browser, url, lang)
        page.wait_for_selector("#setup:not([hidden])")
        expect(page.evaluate("() => document.querySelector('#setupList [data-item=router]').classList.contains('is-done')") is False,
               "[%s] the router step is not ticked by the parent's phone or the box itself" % lang)
        ctx.close()


def part_banner(browser):
    for lang in ("en", "ar"):
        url, store = make_site()
        offer_update(store)
        ctx, page = open_page(browser, url, lang)
        page.wait_for_selector("#updateBanner:not([hidden])")
        expect(page.inner_text("#updateBanner") == T(lang, "updateReady", v="3.1.0"), "[%s] the banner: %r" % (lang, page.inner_text("#updateBanner")))
        shot(page, "banner-%s.png" % lang)
        page.click("#updateBanner")
        page.wait_for_selector("#boxDialog[open]")
        expect(T(lang, "boxAvailable", v="3.1.0") in live(page), "[%s] it opens My box where the update is" % lang)
        ctx.close()
        # nothing on offer, or a 'latest' that is not a version: no banner
        for latest in (None, "1.0.0", VERSION):
            url, store = make_site()
            if latest:
                offer_update(store, latest)
            ctx, page = open_page(browser, url, lang)
            time.sleep(0.4)
            expect(page.is_hidden("#updateBanner"), "[%s] no banner for latest=%r" % (lang, latest))
            ctx.close()
        url, store = make_site()
        store.edit_state(lambda st: st.setdefault("update", {}).update({"latest": "9.9.9; rm -rf /", "notes": "javascript:alert(1)"}))
        ctx, page = open_page(browser, url, lang)
        time.sleep(0.4)
        expect(page.is_hidden("#updateBanner"), "[%s] a 'latest' that is not a version is ignored (the page never shows what it cannot trust)" % lang)
        open_box(page)
        expect(page.locator("#boxUpdates a").count() == 0, "[%s] and no link is made from a notes value the parser did not accept" % lang)
        ctx.close()


def part_branding(browser):
    url, store = make_site()
    ctx, page = open_page(browser, url, "en", sign_in=False)
    page.wait_for_selector("#login:not([hidden])")
    expect(page.title() == "Sinko", "the title is Sinko (%r)" % page.title())
    expect(page.inner_text("#login h1") == "Sinko" and page.inner_text("#login .tagline") == "Family internet", "the login screen: name and tagline")
    icon = page.evaluate("() => { const l = document.querySelector('link[rel=icon]'); return l && [l.getAttribute('href'), l.getAttribute('type')]; }")
    touch = page.evaluate("() => { const l = document.querySelector('link[rel=apple-touch-icon]'); return l && l.getAttribute('href'); }")
    expect(icon == ["/pb/icon.svg", "image/svg+xml"], "the page links its icon: %s" % (icon,))
    expect(touch == "/pb/apple-touch-icon.png", "and the iPhone icon: %s" % (touch,))
    expect(page.get_attribute("meta[name=generator]", "content") == "sinko", "the generator marker stays 'sinko' (the installer looks for it)")
    shot(page, "login-en.png")
    page.click("#login .lang-toggle")
    expect(page.inner_text("#login h1") == "سينكو" and page.inner_text("#login .tagline") == "إنترنت العائلة", "Arabic: the name is سينكو, the tagline إنترنت العائلة")
    expect(page.title() == "سينكو", "and the title follows the language (%r)" % page.title())
    shot(page, "login-ar.png")
    ctx.close()


def part_motion(browser):
    url, store = make_site()
    sim_for(store, run_seconds=None)
    offer_update(store)
    ctx, page = open_page(browser, url, "en", reduced_motion="reduce")
    open_box(page)
    page.click("#boxUpdateBtn")
    eventually(lambda: page.locator(".box-bar").count() == 1)
    anim = page.evaluate("() => [getComputedStyle(document.querySelector('.box-bar i')).animationName, getComputedStyle(document.querySelector('.box-status svg')).animationName]")
    expect(anim == ["none", "none"], "with reduced motion nothing in the progress state moves: %s" % anim)
    ctx.close()
    ctx, page = open_page(browser, url, "en")
    open_box(page)
    eventually(lambda: page.locator(".box-bar").count() == 1)
    anim = page.evaluate("() => getComputedStyle(document.querySelector('.box-bar i')).animationName")
    expect(anim != "none", "and without it the bar does move (%s)" % anim)
    ctx.close()


def part_screenshots(browser):
    """tools/make-screenshots.py writes the documentation pictures and can be run again over its own output."""
    import struct
    import subprocess
    with tempfile.TemporaryDirectory() as out:
        for name in ("panel-en.png", "box-ar.png"):                 # pictures from an earlier run: they are simply replaced
            with open(os.path.join(out, name), "wb") as fh:
                fh.write(b"old")
        done = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "make-screenshots.py"), "--out", out], capture_output=True, text=True, timeout=240)
        expect(done.returncode == 0, "make-screenshots.py succeeds, over the pictures of an earlier run %s" % (done.stderr[-300:] if done.returncode else ""))
        want = {"panel-en.png": (780, 1688), "panel-ar.png": (780, 1688), "desktop-en.png": (1280, 900), "box-en.png": (780, 2360), "box-ar.png": (780, 2360)}
        for name, size in want.items():
            path = os.path.join(out, name)
            with open(path, "rb") as fh:
                head = fh.read(24)
            got = struct.unpack(">II", head[16:24]) if head[:8] == b"\x89PNG\r\n\x1a\n" else None
            expect(got == size, "%s is a PNG of %s (got %s)" % (name, size, got))
        with open(os.path.join(out, "live-en.png"), "rb") as fh:
            head = fh.read(24)
        w, h = struct.unpack(">II", head[16:24])
        expect(head[:8] == b"\x89PNG\r\n\x1a\n" and w >= 700 and h > 1200, "live-en.png is the live picture card (%dx%d)" % (w, h))
        expect(sorted(os.listdir(out)) == sorted(list(want) + ["live-en.png"]), "and nothing else is written: %s" % sorted(os.listdir(out)))


PARTS = [("structure", part_structure), ("update", part_update), ("health", part_health), ("network", part_network), ("password", part_password),
         ("backup", part_backup), ("power", part_power), ("counter", part_counter), ("about", part_about), ("firstrun", part_firstrun),
         ("banner", part_banner), ("branding", part_branding), ("motion", part_motion), ("screenshots", part_screenshots)]

with sync_playwright() as p:
    browser = p.chromium.launch()
    for name, fn in PARTS:
        if args.only and args.only != name:
            continue
        print("--- " + name)
        started = time.time()
        try:
            fn(browser)
        except Exception as exc:                     # a crashed part is a failure, and the other parts still run
            import traceback
            traceback.print_exc()
            expect(False, "part %s crashed: %s" % (name, exc))
        print("    (%.1f s)" % (time.time() - started))
    browser.close()

expect(not errors, "no console errors %s" % (errors or ""))
for httpd in servers:
    httpd.shutdown()
print("\n%d failure(s)" % len(failures))
sys.exit(1 if failures else 0)
