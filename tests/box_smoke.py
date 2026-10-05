"""Browser tests of the "My box" sheet, the first-run flows (claim screen, setup list, counter question), the update banner and the
branding, on the real parent page against the mock Pi-hole (tests/mock_pihole.py), in English and Arabic (right to left).

    python3 tests/box_smoke.py [--shots DIR] [--only PART]        (needs: pip install playwright && playwright install chromium)

Parts: structure, update, health, network, help, heartbeat, password, backup, power, counter, about, firstrun, banner, branding, contrast, narrow,
motion, screenshots. The scheduler on the box is played by mock_pihole.SchedulerSim, which handles the request markers the page writes the way the architecture
says (and drops a power request that arrives while an update runs, as bin/sinko does); /pb/box.json is served by the mock (`store.box_info`).
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


FINITE_ANIMATIONS = """() => Promise.all(document.getAnimations().filter(a => { const t = a.effect && a.effect.getComputedTiming(); return t && isFinite(t.endTime); })
    .map(a => a.finished.catch(() => {})))"""


def settle(page):
    """Waits until every animation that ends has ended. On a phone a sheet slides in over 0.22 s at partly see-through opacity: a picture taken
    in that moment shows the page behind the sheet (a capture artefact, not a defect: the sheet is opaque once it has opened)."""
    try:
        page.evaluate(FINITE_ANIMATIONS)
    except Exception:
        pass


def shot(page, name, full=False):
    if args.shots:
        os.makedirs(args.shots, exist_ok=True)
        settle(page)
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
    settle(page)


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


def dialog_overflow(page):
    """How far the My box dialog itself can scroll (it must not: only the scroller inside it does)."""
    return page.evaluate("() => { const d = document.getElementById('boxDialog'); return d.scrollHeight - d.clientHeight; }")


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
        expect(page.is_visible("#boxCounterCard"), "[%s] the counter card is there on a box whose box.json says it has a counter" % lang)
        expect(dialog_overflow(page) <= 1, "[%s] the sheet itself has nothing to scroll (only its inside does), so dragging at the end cannot slide it away: %s px" % (lang, dialog_overflow(page)))
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
        # the end of the sheet: more dragging must not move the sheet, its title or its close button
        page.keyboard.press("Escape")
        open_box(page)
        page.evaluate("() => { const b = document.getElementById('boxBody'); b.scrollTop = b.scrollHeight; }")
        page.mouse.move(195, 500)
        for _ in range(10):
            page.mouse.wheel(0, 500)
            page.wait_for_timeout(30)
        moved = page.evaluate("() => { const d = document.getElementById('boxDialog'); return [d.scrollTop, Math.round(document.querySelector('#boxDialog .sheet-head').getBoundingClientRect().top)]; }")
        expect(moved[0] == 0 and moved[1] >= 0, "[%s] dragging past the end of the sheet leaves its title and close button where they are (scrollTop %s, head top %s)" % (lang, moved[0], moved[1]))
        shot(page, "box-bottom-%s.png" % lang)
        ctx.close()
        # and on the smallest phones
        for width, height in ((320, 568), (568, 320)):
            url, store = make_site()
            ctx, page = open_page(browser, url, lang, viewport={"width": width, "height": height})
            open_box(page)
            expect(dialog_overflow(page) <= 1, "[%s] %dx%d: the sheet itself has nothing to scroll (%s px)" % (lang, width, height, dialog_overflow(page)))
            if (width, height) == (320, 568):
                shot(page, "box-top-320-%s.png" % lang)
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

        # --- an update that fails: what the box says about the previous version decides what the card says (never more than that)
        why = "The installer stopped with an error (exit status 1). The previous version (3.0.0) was put back."
        for rolled, key, error in ((False, "boxFailedNotBack", "Going back to 3.0.0 did not work either (exit status 1): run sudo sinko doctor."),
                                   (None, "boxFailedUnknown", "The update could not be started.")):
            url, store = make_site()
            sim = sim_for(store, outcome="failed", error=error, rolled_back=rolled, run_seconds=1.0)
            offer_update(store)
            ctx, page = open_page(browser, url, lang)
            open_box(page)
            page.click("#boxUpdateBtn")
            expect(eventually(lambda: T(lang, "boxFailedTitle") in live(page), 10), "[%s] a failed update says so (rolledBack %s): %r" % (lang, rolled, live(page)))
            text = box_text(page)
            expect(T(lang, key) in text and T(lang, "boxFailedBack") not in text,
                   "[%s] and does NOT claim the previous version is back when the box did not say so (rolledBack %s)" % (lang, rolled))
            expect("unplug" in T("en", key).lower() and T(lang, key) in text, "[%s] it says what to do instead (rolledBack %s)" % (lang, rolled))
            expect("sudo" not in text and "sinko doctor" not in text and "exit status" not in text, "[%s] and shows no command and no technical text (rolledBack %s): %r" % (lang, rolled, text))
            if rolled is False:
                expect(page.locator("#boxUpdates details").count() == 0, "[%s] a reason that reads like a command is not shown at all, not even collapsed" % lang)
            shot(page, "box-update-failed-%s-%s.png" % ("notback" if rolled is False else "unknown", lang))
            ctx.close()

        url, store = make_site()
        sim = sim_for(store, outcome="failed", error=why, rolled_back=True, run_seconds=1.0)
        offer_update(store)
        ctx, page = open_page(browser, url, lang)
        page.evaluate("() => { window.__before = 1; }")
        open_box(page)
        page.click("#boxUpdateBtn")
        expect(eventually(lambda: T(lang, "boxFailedTitle") in live(page), 10), "[%s] a failed update says so: %r" % (lang, live(page)))
        text = box_text(page)
        expect(T(lang, "boxFailedBack") in text, "[%s] and, because the box said so (rolledBack true), that the previous version is back" % lang)
        expect(T(lang, "boxFailedNotBack") not in text and T(lang, "boxFailedUnknown") not in text, "[%s] and nothing about it not being back" % lang)
        expect(why not in text and "exit status" not in text, "[%s] the technical reason is not part of what the parent reads: %r" % (lang, text))
        det = page.locator("#boxUpdates details.box-details")
        expect(det.count() == 1 and det.get_attribute("open") is None and clean(det.locator("summary").inner_text()) == T(lang, "boxDetailsTitle"),
               "[%s] it waits behind a collapsed 'details for whoever helps you'" % lang)
        det.locator("summary").click()
        reason = page.locator("#boxUpdates details.box-details .box-reason")
        expect(why in reason.inner_text() and reason.get_attribute("dir") == "ltr" and reason.get_attribute("lang") == "en",
               "[%s] opened, it is the English text, left to right, so it cannot jumble with Arabic around it" % lang)
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
        store.edit_state(lambda st: st["update"].update({"at": int(time.time()) - 600}))
        time.sleep(5)
        expect(T(lang, "boxStalled") not in live(page), "[%s] ten minutes of 'running' is slow, not stuck (the box calls a run dead after three minutes with nobody running it)" % lang)
        store.edit_state(lambda st: st["update"].update({"at": int(time.time()) - 20 * 60}))
        expect(eventually(lambda: T(lang, "boxStalled") in live(page), 8), "[%s] a run that has said 'running' for over a quarter of an hour is called stuck" % lang)
        expect(page.locator("#boxUpdateBtn").count() == 1 and "unplug" not in live(page).lower(), "[%s] with a Try again button and no talk of unplugging" % lang)
        ctx.close()

        # --- nobody on the box listens (the scheduler is not running): the request is withdrawn and the parent is told what to do
        url, store = make_site()
        offer_update(store)
        ctx, page = open_page(browser, url, lang)
        page.evaluate("() => { PBBox.timing.startLost = 1500; }")
        open_box(page)
        page.click("#boxUpdateBtn")
        expect(eventually(lambda: T(lang, "boxStarting") in live(page)), "[%s] it starts by saying it is starting" % lang)
        expect(eventually(lambda: store.state()["update"]["request"] is None, 15), "[%s] a request nobody picks up is withdrawn, so it cannot fire later by surprise" % lang)
        expect(eventually(lambda: T(lang, "boxUpdateStuck") in box_text(page)), "[%s] and the card says to unplug the box and plug it back in" % lang)
        expect(eventually(lambda: page.locator("#boxUpdateBtn").count() == 1 and not page.is_disabled("#boxUpdateBtn")) and T(lang, "boxAvailable", v="3.1.0") in live(page),
               "[%s] with the card back at 'available', not a progress bar for ever: %r" % (lang, live(page)))
        shot(page, "box-update-stuck-%s.png" % lang)
        page.click("#boxDialog [data-act=boxClose]")
        expect(eventually(lambda: page.inner_text("#updateBanner") == T(lang, "updateReady", v="3.1.0")), "[%s] and the main page is back to 'ready' instead of 'updating'" % lang)
        ctx.close()
        # the same from the main page alone: a request left by an earlier visit, the sheet never opened
        url, store = make_site()
        offer_update(store)
        store.edit_state(lambda st: st["update"].update({"request": 1700000000000}))
        ctx, page = open_page(browser, url, lang)
        expect(eventually(lambda: page.is_visible("#updateBanner") and page.inner_text("#updateBanner") == T(lang, "updateRunning")), "[%s] the main page first believes the request is being worked on" % lang)
        page.evaluate("() => { PBBox.timing.startLost = 1500; }")
        time.sleep(2.0)
        page.evaluate("() => document.dispatchEvent(new Event('visibilitychange'))")
        expect(eventually(lambda: store.state()["update"]["request"] is None, 10), "[%s] and withdraws it once it has watched it go unanswered, without My box ever being opened" % lang)
        expect(eventually(lambda: page.inner_text("#updateBanner") == T(lang, "updateReady", v="3.1.0")), "[%s] then offers the update again" % lang)
        expect(eventually(lambda: T(lang, "boxUpdateStuck") in clean(page.inner_text("#toast"))), "[%s] and says what to do" % lang)
        ctx.close()
        # a request that IS picked up is left alone, however long the scheduler takes to finish
        url, store = make_site()
        sim = sim_for(store, run_seconds=None)
        offer_update(store)
        ctx, page = open_page(browser, url, lang)
        page.evaluate("() => { PBBox.timing.startLost = 1000; }")
        open_box(page)
        page.click("#boxUpdateBtn")
        eventually(lambda: store.state()["update"]["status"] == "running")
        time.sleep(3.5)
        expect(store.state()["update"]["status"] == "running" and T(lang, "boxUpdateStuck") not in box_text(page), "[%s] an update the scheduler is running is never withdrawn" % lang)
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
        ctx, page = open_page(browser, url, lang, timezone_id="Asia/Bahrain")
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
        expect(r[5][2] == T(lang, "boxClockOk"), "[%s] the box clock agrees with this phone: %r" % (lang, r[5]))
        expect(clean(page.inner_text("#boxClockZone")) == T(lang, "boxClockZoneKnown", z="Asia/Bahrain, UTC+03:00"),
               "[%s] and the card says which time zone bedtime follows (the box's own, from box.json): %r" % (lang, page.inner_text("#boxClockZone")))
        expect("same" not in r[5][2].lower(), "[%s] without claiming the clocks show the same local time" % lang)
        if lang == "en":
            box_now = time.gmtime(time.time() + 3 * 3600)
            wanted = {"%02d:%02d" % (box_now.tm_hour, box_now.tm_min), time.strftime("%H:%M", time.gmtime(time.time() + 3 * 3600 - 60))}
            expect(r[5][1] in wanted, "[en] the clock row shows the time on the box's own wall clock (box.json says +03:00), not this phone's conversion: %r" % r[5][1])
        expect(not ARABIC_INDIC_DIGITS.search(" ".join(x[1] for x in r)), "[%s] Latin digits" % lang)
        shot(page, "box-health-%s.png" % lang)
        ctx.close()

        # the box's time zone against the phone's, universal time, and a box that does not say
        for what, change, ctx_opts, check in (
                ("a phone in another time zone", {}, {"timezone_id": "Asia/Dubai"}, lambda z: T(lang, "boxClockZoneKnown", z="Asia/Bahrain, UTC+03:00") in z and T(lang, "boxClockZoneDiffers") in z),
                ("a box on universal time", {"tz": "UTC", "utcOffset": "+00:00"}, {"timezone_id": "Asia/Bahrain"}, lambda z: z == T(lang, "boxClockZoneUtc")),
                ("a box in London in winter (+00:00 is not universal time)", {"tz": "Europe/London", "utcOffset": "+00:00"}, {"timezone_id": "Asia/Bahrain"},
                 lambda z: T(lang, "boxClockZoneUtc") not in z and "Europe/London" in z),
                ("a box that cannot tell its zone", {"tz": None, "utcOffset": None}, {"timezone_id": "Asia/Bahrain"}, lambda z: z == T(lang, "boxClockZone"))):
            url, store = make_site()
            store.box_info.update(change)
            ctx, page = open_page(browser, url, lang, **ctx_opts)
            open_box(page)
            zone = clean(page.inner_text("#boxClockZone"))
            expect(check(zone), "[%s] %s: %r" % (lang, what, zone))
            if change.get("tz") == "UTC":
                expect(page.get_attribute("#boxClockZone", "class") == "box-warn", "[%s] the universal-time warning is drawn as a warning" % lang)
                expect("ask whoever" not in zone and "timedatectl" not in zone, "[%s] and offers no fix the page cannot give" % lang)
                shot(page, "box-health-utc-%s.png" % lang)
            ctx.close()
        url, store = make_site()
        store.box_info = None
        ctx, page = open_page(browser, url, lang)
        open_box(page)
        expect(page.inner_text("#boxClockZone") == T(lang, "boxClockZone"), "[%s] an older box (no box.json) keeps the general note about the time zone" % lang)
        expect(eventually(lambda: "…" not in page.inner_text("#boxHealth")), "[%s] and the health card works as before" % lang)
        ctx.close()
        errors[:] = [e for e in errors if "status of 404" not in e]        # the missing box.json is the point of this case

        # the box stops answering while the sheet is open: its old readings are not shown as if they were true
        url, store = make_site()
        ctx, page = open_page(browser, url, lang)
        page.evaluate("() => { PBBox.timing.healthDown = 1200; }")
        open_box(page)
        eventually(lambda: "…" not in page.inner_text("#boxHealth"))
        expect(T(lang, "boxFilterOn") in page.inner_text("#boxHealth"), "[%s] (set-up: the card shows 'Filtering: On')" % lang)
        store.outage_until = time.time() + 9
        expect(eventually(lambda: page.is_visible("#boxHealthDown"), 12), "[%s] when the box goes silent the health card says it is not answering" % lang)
        card = clean(page.inner_text("#boxHealth"))
        expect(T(lang, "boxHealthDown") in card and T(lang, "boxFilterOn") not in card and T(lang, "boxUptimeLabel") not in card,
               "[%s] and shows none of the old readings (no 'Filtering: On', no uptime): %r" % (lang, card))
        shot(page, "box-health-down-%s.png" % lang)
        expect("did not work" not in page.inner_text("#toast"), "[%s] with no error shown for it" % lang)
        expect(eventually(lambda: page.is_hidden("#boxHealthDown") and T(lang, "boxFilterOn") in page.inner_text("#boxHealth"), 25),
               "[%s] and the readings are back, read afresh, once the box answers again" % lang)
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
        store.config["dns"]["hosts"] = ["192.168.1.50 family.lan", "192.168.1.60 printer.lan"]
        ctx, page = open_page(browser, url, lang)
        open_box(page)
        rows = lambda: page.evaluate("() => [...document.querySelectorAll('#boxNet .box-addr-row')].map(r => [r.querySelector('.box-label').textContent, r.querySelector('.box-addr-value').textContent])")
        eventually(lambda: len(rows()) == 3)
        r = rows()
        expect(r == [[T(lang, "boxNetIp"), "192.168.1.50"], [T(lang, "boxNetName"), "family.lan:" + port], [T(lang, "boxNetLocal"), "sinko.local:" + port]],
               "[%s] the number the box reports for itself (the page was opened at 127.0.0.1), the name Pi-hole holds for it, and the .local name: %s" % (lang, r))
        expect("printer" not in box_text(page), "[%s] another device's record is not shown as the box" % lang)
        page.click("#boxNet .box-addr-row >> nth=0 >> .box-copy")
        copied = eventually(lambda: page.evaluate("() => navigator.clipboard.readText()") == "192.168.1.50")
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


def part_help(browser):
    """The router sentence: always a number, never a name; and the address rows when the page was opened by the .local name."""
    for lang in ("en", "ar"):
        url, store = make_site()
        port = url.rstrip("/").rsplit(":", 1)[1]
        by_name = "http://sinko.local:%s/" % port                 # the quick-start card tells the parent to open this (see the browser's resolver rules)
        store.config["dns"]["hosts"] = ["192.168.1.50 family.lan"]
        ctx, page = open_page(browser, by_name, lang)
        help_text = lambda: clean(page.evaluate("() => document.getElementById('help').querySelector('p').textContent"))
        expect(eventually(lambda: "192.168.1.50" in help_text()), "[%s] opened as sinko.local: the router help gives the box's number from box.json: %r" % (lang, help_text()))
        expect("sinko.local" not in help_text() and "family.lan" not in help_text() and "{ip}" not in help_text(), "[%s] and puts no name in the router sentence" % lang)
        open_box(page)
        rows = lambda: page.evaluate("() => [...document.querySelectorAll('#boxNet .box-addr-row')].map(r => [r.querySelector('.box-label').textContent, r.querySelector('.box-addr-value').textContent])")
        eventually(lambda: len(rows()) == 3)
        expect(rows() == [[T(lang, "boxNetIp"), "192.168.1.50"], [T(lang, "boxNetName"), "family.lan:" + port], [T(lang, "boxNetLocal"), "sinko.local:" + port]],
               "[%s] and My box shows the number address even when the page was opened by the .local name: %s" % (lang, rows()))
        shot(page, "box-network-by-name-%s.png" % lang)
        ctx.close()

        # the box moved to another network: the next reading of box.json is the new address
        store.box_info["ip"] = "192.168.1.77"
        ctx, page = open_page(browser, by_name, lang)
        expect(eventually(lambda: "192.168.1.77" in help_text()) and "192.168.1.50" not in help_text(), "[%s] a changed address in box.json is the one the help gives" % lang)
        ctx.close()

        # an older box (no box.json): the number the page was opened by, if it is one; otherwise honest words that name no host
        store.box_info = None
        for opened, what in ((by_name, "a name"), ("http://127.0.0.1:%s/" % port, "a loopback number")):
            ctx, page = open_page(browser, opened, lang)
            eventually(lambda: page.is_visible(".tile"))
            expect(help_text() == T(lang, "helpBodyNoIp"), "[%s] no box.json and the page opened by %s: the no-number wording: %r" % (lang, what, help_text()))
            expect("sinko.local" not in help_text() and "127.0.0.1" not in help_text() and "{ip}" not in help_text(), "[%s] with no host name and no loopback number in it" % lang)
            if what == "a name":
                page.evaluate("() => { const h = document.getElementById('help'); h.open = true; h.scrollIntoView({ block: 'center' }); }")
                shot(page, "help-no-number-%s.png" % lang)
            ctx.close()
        # box.json is garbage, or hostile: the page works and believes none of it
        for raw in (b"{not json", b'{"v":1,"ip":"sinko.local","counter":"true","at":"now"}', b'{"v":2,"ip":"192.168.1.50","at":1790000000}', b"[]", b"null"):
            store.box_raw = raw
            ctx, page = open_page(browser, by_name, lang)
            eventually(lambda: page.is_visible(".tile"))
            expect(help_text() == T(lang, "helpBodyNoIp") and page.is_hidden("#banner"), "[%s] box.json %r changes nothing for the worse: no name in the help, no banner" % (lang, raw[:24]))
            open_box(page)
            expect(page.is_hidden("#boxCounterCard"), "[%s] and offers no counter" % lang)
            ctx.close()
        store.box_raw = None
        errors[:] = [e for e in errors if "status of 404" not in e]        # the missing box.json is the point of this part


def part_heartbeat(browser):
    """box.json's `at` is the scheduler's pulse: more than 20 minutes behind the box's own clock means the scheduler is not running."""
    for lang in ("en", "ar"):
        def banner(page):
            return page.is_visible("#banner") and clean(page.inner_text("#banner"))

        def look_again(page):
            page.evaluate("() => document.dispatchEvent(new Event('visibilitychange'))")

        url, store = make_site()
        store.box_info_age = 25 * 60
        ctx, page = open_page(browser, url, lang)
        time.sleep(0.8)
        expect(not banner(page), "[%s] a pulse that is late is not believed at the first look (a corrected clock leaves the file a few seconds behind)" % lang)
        page.evaluate("() => { PBBox.timing.heartbeatGrace = 1500; }")
        time.sleep(2.0)
        look_again(page)
        expect(eventually(lambda: banner(page) == T(lang, "schedulerDown")), "[%s] seen late for a while: the helper-is-not-running banner: %r" % (lang, banner(page)))
        expect(not re_command(banner(page)), "[%s] and it asks for no command" % lang)
        shot(page, "banner-scheduler-down-%s.png" % lang)
        store.box_info_age = 0
        look_again(page)
        expect(eventually(lambda: not banner(page)), "[%s] and it goes away by itself once the box writes the file again" % lang)
        ctx.close()

        # not late: ten minutes is the box's own rhythm; a missing file is an older box; a wrong phone clock does not matter
        for what, setup in (("a file ten minutes old", lambda st: setattr(st, "box_info_age", 10 * 60)),
                            ("a box with no box.json", lambda st: setattr(st, "box_info", None)),
                            ("a box whose own clock is three hours ahead of the phone", lambda st: (setattr(st, "clock_skew", 3 * 3600), setattr(st, "box_info_age", 60)))):
            url, store = make_site()
            setup(store)
            ctx, page = open_page(browser, url, lang)
            page.evaluate("() => { PBBox.timing.heartbeatGrace = 1; }")
            time.sleep(1.2)
            look_again(page)
            time.sleep(1.0)
            expect(not banner(page), "[%s] %s: no banner" % (lang, what))
            ctx.close()
        errors[:] = [e for e in errors if "status of 404" not in e]

        # an update is running: the box does not write the file meanwhile, so a late pulse says nothing
        url, store = make_site()
        sim = sim_for(store, run_seconds=None)
        offer_update(store)
        store.box_info_age = 40 * 60
        ctx, page = open_page(browser, url, lang)
        page.evaluate("() => { PBBox.timing.heartbeatGrace = 1; }")
        open_box(page)
        page.click("#boxUpdateBtn")
        eventually(lambda: store.state()["update"]["status"] == "running")
        time.sleep(1.2)
        page.keyboard.press("Escape")
        time.sleep(1.0)
        look_again(page)
        time.sleep(1.0)
        expect(not banner(page), "[%s] a late pulse during a running update is not 'the helper is not running'" % lang)
        store.edit_state(lambda st: st["update"].update({"status": "ok", "at": int(time.time())}))
        expect(eventually(lambda: (look_again(page), banner(page) == T(lang, "schedulerDown"))[1], 8), "[%s] and once the update is over a pulse that is still late is" % lang)
        ctx.close()


def re_command(text):
    return bool(re.search(r"(?i)\b(sudo|systemctl|ssh|terminal)\b|sinko [a-z]+", text or ""))


def part_password(browser):
    for lang in ("en", "ar"):
        url, store = make_site()
        ctx, page = open_page(browser, url, lang)
        open_box(page)
        err = lambda: page.inner_text("#boxPwForm .form-error")
        page.fill("#boxPwNew", "abc")
        page.click("#boxPwBtn")
        expect(active_id(page) == "boxPwCur", "[%s] the first empty field gets the focus" % lang)
        expect(err() == T(lang, "boxPwNeedCurrent"), "[%s] and an alert says what is missing, for a screen reader too: %r" % (lang, err()))
        expect(page.get_attribute("#boxPwForm .form-error", "role") == "alert", "[%s] (the error line is an alert)" % lang)
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


def make_plain_zip(files):
    """A zip with whatever files are named (no Teleporter tables unless the caller makes them)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
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
        expect(page.get_attribute("#boxRestoreName", "role") == "status" and page.get_attribute("#boxRestoreFile", "aria-hidden") == "true"
               and page.get_attribute("#boxRestorePick", "aria-describedby") == "boxRestoreName",
               "[%s] the name is announced (a status), and a screen reader meets one 'choose file' control, not a second hidden one" % lang)
        page.click("#boxRestoreBtn")
        expect("my-backup.zip" in confirm_text(page), "[%s] the confirmation names the file: %r" % (lang, confirm_text(page)))
        shot(page, "box-restore-confirm-%s.png" % lang)
        page.click("#confirmDialog button[value=no]")
        page.wait_for_selector("#confirmDialog:not([open])", state="attached")
        expect(store.last_import is None, "[%s] cancelling imports nothing" % lang)
        page.click("#boxRestoreBtn")
        confirm_text(page)
        store.gravity_seconds = 2.0
        page.click("#confirmYes")
        expect(eventually(lambda: T(lang, "boxRestoreGravity") in box_text(page), 12),
               "[%s] once the import is in, the page says the box is rebuilding its block lists and how long it takes" % lang)
        expect(page.is_disabled("#boxRestoreBtn"), "[%s] and Restore waits" % lang)
        expect(T(lang, "boxRestoreDone") not in box_text(page), "[%s] and does not say 'ready' yet" % lang)
        shot(page, "box-restore-rebuilding-%s.png" % lang)
        expect(eventually(lambda: T(lang, "boxRestoreDone") in box_text(page), 25), "[%s] restore finishes, and says filtering is ready" % lang)
        expect(store.gravity_runs == 1, "[%s] after one rebuild of the block lists (Pi-hole keys them by the numbers the restored lists have now): %d" % (lang, store.gravity_runs))
        expect(store.last_import["processed"] and store.last_import["import"]["gravity"]["adlist"] is True, "[%s] (the import itself came first)" % lang)
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
        # a real zip with nothing in it that Pi-hole can use (settings only, or somebody's photos): Pi-hole says 200 and processed nothing
        for what, data in (("a settings-only export", make_plain_zip({"etc/pihole/pihole.toml": "[dns]\n"})),
                           ("an unrelated zip", make_plain_zip({"IMG_0001.jpg": "x", "notes.txt": "y"}))):
            store.last_import = None
            page.set_input_files("#boxRestoreFile", {"name": "other.zip", "mimeType": "application/zip", "buffer": data})
            page.click("#boxRestoreBtn")
            confirm_text(page)
            page.click("#confirmYes")
            expect(eventually(lambda: T(lang, "boxRestoreBad") in box_text(page) and not page.is_disabled("#boxRestoreBtn"), 12), "[%s] %s is not called a restore" % (lang, what))
            expect(T(lang, "boxRestoreDone") not in box_text(page) and json.dumps(store.tables(), sort_keys=True) == before,
                   "[%s] and nothing is said to be restored, and nothing changed (%s)" % (lang, what))
            expect(store.gravity_runs == 0, "[%s] and no rebuild of the block lists is started for it" % lang)
            expect(store.last_import and store.last_import["processed"] == [], "[%s] and no safety copy had to be put back (%s)" % (lang, what))
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
        expect(store.gravity_runs == 0, "[%s] and nothing was rebuilt for a file that was refused" % lang)
        ctx.close()
        errors[:] = [e for e in errors if "status of 400" not in e]            # Pi-hole refusing a file is the point of this part

        # the rebuild fails (the box cannot reach the internet): the restore stands, and the page says honestly what is not sure
        url, store = make_site()
        store.gravity_fail = True
        ctx, page = open_page(browser, url, lang)
        open_box(page)
        own = store.export_zip()
        page.set_input_files("#boxRestoreFile", {"name": "same-box.zip", "mimeType": "application/zip", "buffer": own})
        page.click("#boxRestoreBtn")
        confirm_text(page)
        page.click("#confirmYes")
        expect(eventually(lambda: T(lang, "boxRestorePartial") in box_text(page), 20), "[%s] a rebuild that fails is said plainly, not called ready: %r" % (lang, box_text(page)[-200:]))
        expect(T(lang, "boxRestoreDone") not in box_text(page) and T(lang, "boxRestoreFail", e="") not in box_text(page),
               "[%s] and the restore itself is not called a failure" % lang)
        expect(page.locator("#boxBackup .box-msg.is-warn").count() == 1 and not page.is_disabled("#boxRestorePick"), "[%s] drawn as a warning, and the sheet is usable again" % lang)
        shot(page, "box-restore-partial-%s.png" % lang)
        ctx.close()


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
        expect(not page.is_disabled("#boxRestartBtn") and not page.is_disabled("#boxShutdownBtn"), "[%s] and the buttons work again (a parent can try once more)" % lang)
        shot(page, "box-power-stuck-%s.png" % lang)
        page.click("#boxDialog [data-act=boxClose]")
        open_box(page)
        expect(page.inner_text("#boxPowerMsg") == "", "[%s] the 'did not react' message is gone when the sheet is opened again" % lang)
        ctx.close()

        # an update is running (or on its way): Restart and Shut down wait, with a calm note, and never say 'unplug'
        url, store = make_site()
        sim = sim_for(store, run_seconds=None)
        offer_update(store)
        ctx, page = open_page(browser, url, lang)
        open_box(page)
        expect(not page.is_disabled("#boxRestartBtn") and page.inner_text("#boxPowerMsg") == "", "[%s] with no update the buttons are open and say nothing" % lang)
        page.click("#boxUpdateBtn")
        expect(eventually(lambda: page.is_disabled("#boxRestartBtn") and page.is_disabled("#boxShutdownBtn")), "[%s] the moment an update is asked for, both buttons wait" % lang)
        expect(eventually(lambda: clean(page.inner_text("#boxPowerMsg")) == T(lang, "boxPowerWaitUpdate")), "[%s] and a calm note says why: %r" % (lang, page.inner_text("#boxPowerMsg")))
        eventually(lambda: store.state()["update"]["status"] == "running")
        time.sleep(1.5)
        note = clean(page.inner_text("#boxPowerMsg"))
        expect(page.is_disabled("#boxRestartBtn") and note == T(lang, "boxPowerWaitUpdate") and "unplug" not in note.lower(), "[%s] it stays that way while the update runs, with no word about unplugging" % lang)
        shot(page, "box-power-wait-update-%s.png" % lang)
        page.evaluate("() => document.getElementById('boxRestartBtn').click()")
        time.sleep(0.5)
        expect(page.locator("#confirmDialog[open]").count() == 0 and store.state()["power"]["request"] is None and store.power_log == [],
               "[%s] and a press that gets through anyway asks nothing of the box" % lang)
        store.edit_state(lambda st: st["update"].update({"status": "ok", "at": int(time.time())}))
        expect(eventually(lambda: not page.is_disabled("#boxRestartBtn") and page.inner_text("#boxPowerMsg") == "", 8), "[%s] when the update has finished the buttons are open again" % lang)
        ctx.close()
        # an update that another phone started, or one the scheduler has not picked up yet, counts too (this page never saw it begin)
        for what, change in (("running, started elsewhere", {"status": "running", "from": VERSION, "to": "3.1.0", "at": int(time.time())}),
                             ("asked for, not picked up yet", {"request": 1700000000000})):
            url, store = make_site()
            store.edit_state(lambda st: st.setdefault("update", {}).update(change))
            ctx, page = open_page(browser, url, lang)
            open_box(page)
            expect(page.is_disabled("#boxRestartBtn") and page.is_disabled("#boxShutdownBtn") and clean(page.inner_text("#boxPowerMsg")) == T(lang, "boxPowerWaitUpdate"),
                   "[%s] an update %s: the buttons wait" % (lang, what))
            ctx.close()

        # the update starts while a restart request waits: the page takes its request back, and says nothing about unplugging (not even later)
        url, store = make_site()                                                 # nobody takes the request, like a scheduler that is busy
        ctx, page = open_page(browser, url, lang)
        page.evaluate("() => { PBBox.timing.powerStuck = 2500; }")
        open_box(page)
        page.click("#boxRestartBtn")
        confirm_text(page)
        page.click("#confirmYes")
        expect(eventually(lambda: store.state()["power"]["request"] is not None), "[%s] (set-up: the request waits in the state)" % lang)
        store.edit_state(lambda st: st.setdefault("update", {}).update({"status": "running", "from": VERSION, "to": "3.1.0", "at": int(time.time())}))
        store.outage_until = time.time() + 2.5                                   # the installer restarts the box's services
        expect(eventually(lambda: store.state()["power"] == {"request": None, "action": None}, 10), "[%s] the update started: the page takes the request back (the box would only drop it)" % lang)
        time.sleep(3.5)                                                          # longer than the page's wait for an answer, and than the outage
        msg = clean(page.inner_text("#boxPowerMsg"))
        expect(msg == T(lang, "boxPowerWaitUpdate"), "[%s] the note says to wait for the update, not 'the box did not react' or 'restarting': %r" % (lang, msg))
        expect("unplug" not in msg.lower() and page.is_hidden("#login"), "[%s] no instruction to unplug, and no sign-in screen saying the box restarted" % lang)
        store.edit_state(lambda st: st["update"].update({"status": "ok", "at": int(time.time())}))
        expect(eventually(lambda: clean(page.inner_text("#boxPowerMsg")) == T(lang, "boxPowerDropped"), 10), "[%s] afterwards it says the box was updating, did not restart, and to try again" % lang)
        expect(not page.is_disabled("#boxRestartBtn") and store.power_log == [], "[%s] with the buttons open and nothing ever restarted" % lang)
        shot(page, "box-power-dropped-%s.png" % lang)
        ctx.close()

        # the box took the request out of the state while an update ran: that is a drop, not 'restarting'
        url, store = make_site()
        ctx, page = open_page(browser, url, lang)
        open_box(page)
        page.click("#boxShutdownBtn")
        confirm_text(page)
        page.click("#confirmYes")
        eventually(lambda: store.state()["power"]["request"] is not None)
        store.edit_state(lambda st: (st.setdefault("update", {}).update({"status": "running", "from": VERSION, "to": "3.1.0", "at": int(time.time())}),
                                     st["power"].update({"request": None, "action": None})))
        time.sleep(2.0)
        msg = clean(page.inner_text("#boxPowerMsg"))
        expect(msg not in (T(lang, "boxPowerOff"), T(lang, "boxPowerRestarting")), "[%s] a request that vanished while an update runs is not reported as the box shutting down: %r" % (lang, msg))
        ctx.close()

        # closing the sheet takes back a request nobody has answered: it must not wait for the next time the scheduler looks
        url, store = make_site()
        ctx, page = open_page(browser, url, lang)
        open_box(page)
        page.click("#boxRestartBtn")
        confirm_text(page)
        page.click("#confirmYes")
        expect(eventually(lambda: store.state()["power"]["request"] is not None), "[%s] (set-up: nobody answers)" % lang)
        page.click("#boxDialog [data-act=boxClose]")
        expect(eventually(lambda: store.state()["power"] == {"request": None, "action": None}, 6), "[%s] closing the sheet withdraws it, so it cannot fire when the box is next switched on" % lang)
        ctx.close()
        # a request left behind by a phone that went away is withdrawn by the main page after it has watched it for a minute (shortened here)
        url, store = make_site()
        store.edit_state(lambda st: st["power"].update({"request": 1700000000000, "action": "reboot"}))
        ctx, page = open_page(browser, url, lang)
        page.evaluate("() => { PBBox.timing.powerStale = 1500; }")
        time.sleep(2.0)
        expect(eventually(lambda: (page.evaluate("() => document.dispatchEvent(new Event('visibilitychange'))"), store.state()["power"]["request"] is None)[1], 8),
               "[%s] a restart request nobody takes is withdrawn by the main page, without My box ever being opened" % lang)
        ctx.close()

        # the page's picture of the update can be a few seconds old: the write itself refuses
        url, store = make_site()
        ctx, page = open_page(browser, url, lang)
        open_box(page)
        page.evaluate("() => Object.defineProperty(document, 'visibilityState', { get: () => 'hidden', configurable: true })")
        time.sleep(0.6)                                                          # polling stops while the page is hidden: nothing refreshes what it knows
        store.edit_state(lambda st: st.setdefault("update", {}).update({"status": "running", "from": VERSION, "to": "3.1.0", "at": int(time.time())}))
        page.click("#boxRestartBtn")
        confirm_text(page)
        page.click("#confirmYes")
        expect(eventually(lambda: clean(page.inner_text("#boxPowerMsg")) == T(lang, "boxPowerWaitUpdate")), "[%s] a press the page could not know was too late ends in the same calm note: %r" % (lang, page.inner_text("#boxPowerMsg")))
        expect(store.state()["power"]["request"] is None and store.power_log == [], "[%s] and nothing was asked of the box" % lang)
        expect("did not work" not in page.inner_text("#boxToast"), "[%s] and no error toast" % lang)
        ctx.close()


def part_counter(browser):
    for lang in ("en", "ar"):
        url, store = make_site()
        store.edit_state(lambda st: st.update({"community": {"online": 1234, "at": 1800000000}, "telemetry": {"on": False}}))
        ctx, page = open_page(browser, url, lang)
        open_box(page)
        text = box_text(page)
        for key in ("boxCounterLead", "boxCounterSent1", "boxCounterSent2", "boxCounterSent3", "boxCounterKept", "boxCounterForget", "boxCounterNot"):
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
        expect(T(lang, "boxCounterOffToast") in page.inner_text("#boxToast"), "[%s] and says plainly that the box now asks the counter service to forget it: %r" % (lang, page.inner_text("#boxToast")))
        expect(T(lang, "boxCounterForget") in box_text(page), "[%s] (the card says so before the switch is touched, too)" % lang)
        ctx.close()
        # a box with no counter address (or an older box) offers no counter: better silent than a promise nothing keeps
        for what, change in (("box.json says no counter address is set", {"counter": False}), ("an older box with no box.json", None)):
            url, store = make_site()
            if change is None:
                store.box_info = None
            else:
                store.box_info.update(change)
            ctx, page = open_page(browser, url, lang)
            open_box(page)
            expect(page.is_hidden("#boxCounterCard") and page.locator("#boxCounterCard").count() == 1, "[%s] %s: the counter card is not shown" % (lang, what))
            expect(T(lang, "boxCounterLead") not in box_text(page) and T(lang, "boxUpdTitle") in box_text(page), "[%s] and the rest of the sheet is there" % lang)
            ctx.close()
        errors[:] = [e for e in errors if "status of 404" not in e]


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

        # a box with no counter address (or no box.json): three steps, and the list can finish without a question nothing keeps
        for what, change in (("box.json says no counter address is set", {"counter": False}), ("an older box with no box.json", None)):
            url, store = make_site(setup_done=False, kids=False)
            if change is None:
                store.box_info = None
            else:
                store.box_info.update(change)
            store.devices[:] = [store.devices[0]]
            ctx, page = open_page(browser, url, lang)
            page.wait_for_selector("#setup:not([hidden])")
            three = lambda: page.evaluate("() => [...document.querySelectorAll('#setupList .setup-item')].map(i => [i.dataset.item, i.classList.contains('is-done')])")
            expect(three() == [["pw", True], ["router", False], ["child", False]], "[%s] %s: three steps, no counter question: %s" % (lang, what, three()))
            expect(page.inner_text("#setupProgress") == T(lang, "setupProgress", n=1, t=3) and T(lang, "setupCounterText") not in page.inner_text("#setup"),
                   "[%s] and the progress counts three" % lang)
            page.click("#setupList [data-act=openAdd]")
            page.wait_for_selector("#addDialog[open]")
            page.click("#addForm .manual summary")
            page.fill("#manualAddr", "AA:BB:CC:00:00:78")
            page.click("[data-act=addDevice]")
            store.devices.append({"id": 9, "hwaddr": "aa:bb:cc:00:00:05", "macVendor": "", "lastQuery": int(time.time()) - 20, "numQueries": 5,
                                  "ips": [{"ip": "192.168.1.33", "name": "TV"}]})
            page.reload()
            page.wait_for_selector(".tile")
            expect(eventually(lambda: page.is_hidden("#setup")) and eventually(lambda: store.state()["setup"] == {"done": True}),
                   "[%s] the list finishes by itself once the three steps are done, and stays finished" % lang)
            ctx.close()
        errors[:] = [e for e in errors if "status of 404" not in e]

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
    marks = page.evaluate("() => [...document.querySelectorAll('.brand-mark use')].map(u => u.getAttribute('href'))")
    expect(marks == ["#i-sinko", "#i-sinko", "#i-sinko"], "the sign-in card, the claim card and the top bar all draw the Sinko mark (the house with two sound arcs), not the old outline house: %s" % marks)
    expect(page.evaluate("() => !!document.querySelector('symbol#i-sinko path[stroke-linecap=round]')"), "and the symbol has the arcs")
    shot(page, "login-en.png")
    page.click("#login .lang-toggle")
    expect(page.inner_text("#login h1") == "سينكو" and page.inner_text("#login .tagline") == "إنترنت العائلة", "Arabic: the name is سينكو, the tagline إنترنت العائلة")
    expect(page.title() == "سينكو", "and the title follows the language (%r)" % page.title())
    shot(page, "login-ar.png")
    ctx.close()


# WCAG 2.x contrast of every piece of text in the given parts of the page: its computed colour (blended over what is behind it) against the first
# opaque background behind it. Text on a gradient is left out (the hero, the page's own wash): there is no single background to measure. Disabled
# controls, anything see-through (a toast that is not showing) and decorative text that is hidden from screen readers (the app monograms) are exempt.
CONTRAST_JS = """(roots) => {
  const num = (s) => (s.match(/-?[\\d.]+/g) || []).map(Number);
  const parse = (c) => { const n = num(c); return n.length >= 3 ? { r: n[0], g: n[1], b: n[2], a: n.length > 3 ? n[3] : 1 } : null; };
  const over = (f, b) => ({ r: f.r * f.a + b.r * (1 - f.a), g: f.g * f.a + b.g * (1 - f.a), b: f.b * f.a + b.b * (1 - f.a), a: 1 });
  const chan = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
  const lum = (c) => 0.2126 * chan(c.r) + 0.7152 * chan(c.g) + 0.0722 * chan(c.b);
  const ratio = (a, b) => { const x = lum(a), y = lum(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); };
  const background = (el) => {
    const layers = [];
    for (let n = el; n && n.nodeType === 1; n = n.parentElement) {
      const cs = getComputedStyle(n);
      if (cs.backgroundImage && cs.backgroundImage !== 'none') return null;
      const c = parse(cs.backgroundColor);
      if (c && c.a > 0) { layers.push(c); if (c.a >= 1) break; }
    }
    let base = layers.length && layers[layers.length - 1].a >= 1 ? layers.pop() : null;
    if (!base) return null;
    while (layers.length) base = over(layers.pop(), base);
    return base;
  };
  const failures = [], seen = [];
  for (const sel of roots) {
    for (const root of document.querySelectorAll(sel)) {
      const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
      for (let t = walker.nextNode(); t; t = walker.nextNode()) {
        if (!/\\S/.test(t.nodeValue)) continue;
        const el = t.parentElement;
        if (!el || el.closest('script,style,.sr-only,[aria-hidden="true"]') || !el.getClientRects().length) continue;
        const cs = getComputedStyle(el);
        if (cs.visibility === 'hidden' || el.closest('button:disabled,[disabled]')) continue;
        let faded = false;
        for (let n = el; n && n.nodeType === 1; n = n.parentElement) if (parseFloat(getComputedStyle(n).opacity) < 0.99) faded = true;
        if (faded) continue;
        const bg = background(el);
        if (!bg) continue;
        const fg0 = parse(cs.color);
        if (!fg0) continue;
        const fg = over(fg0, bg);
        const px = parseFloat(cs.fontSize), large = px >= 24 || (px >= 18.66 && parseInt(cs.fontWeight, 10) >= 700);
        const r = ratio(fg, bg), need = large ? 3 : 4.5;
        seen.push(1);
        if (r < need) failures.push({ text: t.nodeValue.trim().slice(0, 40), ratio: Math.round(r * 100) / 100, need, cls: (el.className && el.className.baseVal === undefined ? el.className : '') || el.tagName,
          fg: cs.color, bg: 'rgb(' + [bg.r, bg.g, bg.b].map(Math.round).join(',') + ')' });
      }
    }
  }
  return { checked: seen.length, failures };
}"""


def contrast(page, roots, label, minimum=3):
    settle(page)                                   # a sheet that is still sliding in is see-through, and exempt from the measurement below
    got = page.evaluate(CONTRAST_JS, roots)
    expect(got["checked"] >= minimum, "%s: %d pieces of text were measured (so the check is not empty)" % (label, got["checked"]))
    expect(not got["failures"], "%s: all %d pieces of text have at least 4.5:1 (3:1 when large) %s" % (label, got["checked"], got["failures"][:4] if got["failures"] else ""))


def part_contrast(browser):
    """Every component that was added or changed for My box, the first run and the update banner, measured in light and dark, in both languages."""
    main_roots = ["#updateBanner", "#setup", "#banner", "#devices", "#services", ".help", ".foot"]
    for scheme in ("light", "dark"):
        for lang in ("en", "ar"):
            tag = "[%s %s]" % (scheme, lang)
            # --- the main page: update banner, setup list, scheduler banner, a blocked app, a paused device
            url, store = make_site(setup_done=False)
            offer_update(store)
            store.box_info_age = 25 * 60
            ctx, page = open_page(browser, url, lang, color_scheme=scheme)
            page.click("[data-id=youtube]")
            page.wait_for_selector("[data-id=youtube].tile-blocked")
            page.click("[data-act=pause]")
            page.wait_for_selector(".device .tag")
            page.evaluate("() => { PBBox.timing.heartbeatGrace = 1; }")
            eventually(lambda: (page.evaluate("() => document.dispatchEvent(new Event('visibilitychange'))"), page.is_visible("#banner"))[1], 8)
            contrast(page, main_roots, tag + " main page (update banner, setup list, banner, blocked app, paused tag)", 12)
            shot(page, "contrast-main-%s-%s.png" % (scheme, lang), full=True)
            store.edit_state(lambda st: st["update"].update({"status": "failed", "to": "3.1.0", "latest": "3.1.0", "at": int(time.time()), "error": "x", "rolledBack": False}))
            page.evaluate("() => document.dispatchEvent(new Event('visibilitychange'))")
            eventually(lambda: page.is_visible("#updateBanner") and page.get_attribute("#updateBanner", "data-phase") == "failed")
            contrast(page, ["#updateBanner"], tag + " the 'last update did not work' banner", 1)
            ctx.close()

            # --- My box in its different states
            url, store = make_site()
            offer_update(store)
            store.blocking = "disabled"
            store.cpu_temp = 80.0
            store.clock_skew = 3 * 3600
            store.box_info.update({"tz": "UTC", "utcOffset": "+00:00"})
            store.edit_state(lambda st: st.update({"community": {"online": 1234, "at": int(time.time())}, "telemetry": {"on": True}}))
            ctx, page = open_page(browser, url, lang, color_scheme=scheme)
            open_box(page)
            eventually(lambda: "…" not in page.inner_text("#boxHealth"))
            contrast(page, ["#boxDialog"], tag + " My box: update on offer, filtering off, hot box, 3 hours off, universal time, counter online", 25)
            shot(page, "contrast-box-%s-%s.png" % (scheme, lang))
            for rolled in (False, None, True):
                store.edit_state(lambda st: st["update"].update({"status": "failed", "to": "3.1.0", "latest": "3.1.0", "at": int(time.time()) - 5,
                                                                  "error": "The installer stopped with an error (exit status 1).", "rolledBack": rolled}))
                page.keyboard.press("Escape")
                open_box(page)
                if rolled is True:
                    page.click("#boxUpdates details summary")
                contrast(page, ["#boxUpdates"], tag + " a failed update (rolledBack %s)" % rolled, 4)
                shot(page, "contrast-failed-%s-%s-%s.png" % (str(rolled).lower(), scheme, lang))
            store.edit_state(lambda st: st["update"].update({"status": "running", "to": "3.1.0", "at": int(time.time()), "error": None, "rolledBack": None}))
            page.keyboard.press("Escape")
            open_box(page)
            contrast(page, ["#boxUpdates", "#boxPower"], tag + " an update running, with the power buttons waiting", 6)
            shot(page, "contrast-running-%s-%s.png" % (scheme, lang))
            store.edit_state(lambda st: st["update"].update({"status": "idle", "at": 0}))
            page.keyboard.press("Escape")
            open_box(page)
            # an error under the password form, a refused restore, a restore whose rebuild failed, an error toast, the confirm button
            page.fill("#boxPwNew", "abc")
            page.click("#boxPwBtn")
            page.set_input_files("#boxRestoreFile", {"name": "photo.zip", "mimeType": "application/zip", "buffer": b"not a zip"})
            page.click("#boxRestoreBtn")
            confirm_text(page)
            contrast(page, ["#confirmDialog"], tag + " the confirm question and its red button", 2)
            shot(page, "contrast-confirm-%s-%s.png" % (scheme, lang))
            page.click("#confirmYes")
            eventually(lambda: T(lang, "boxRestoreBad") in box_text(page))
            contrast(page, ["#boxPw", "#boxBackup"], tag + " the password error and a refused restore", 6)
            store.gravity_fail = True
            page.set_input_files("#boxRestoreFile", {"name": "same.zip", "mimeType": "application/zip", "buffer": store.export_zip()})
            page.click("#boxRestoreBtn")
            confirm_text(page)
            page.click("#confirmYes")
            eventually(lambda: T(lang, "boxRestorePartial") in box_text(page), 20)
            contrast(page, ["#boxBackup"], tag + " a restore whose rebuild did not finish", 4)
            page.evaluate("""() => { const t = document.getElementById('boxToast'); t.textContent = 'x'; t.className = 'toast toast-error show'; }""")
            page.wait_for_timeout(300)
            contrast(page, ["#boxToast"], tag + " an error toast", 1)
            shot(page, "contrast-toast-%s-%s.png" % (scheme, lang))
            ctx.close()
            errors[:] = [e for e in errors if "status of 400" not in e]

            # --- the claim and sign-in screens
            url, store = make_site(password=False, setup_done=False, kids=False)
            ctx, page = open_page(browser, url, lang, sign_in=False, color_scheme=scheme)
            page.wait_for_selector("#claim:not([hidden])")
            if lang == "ar":
                page.click("#claim .lang-toggle")
            page.fill("#claimPw", "short")
            page.fill("#claimPw2", "short")
            page.click("#claimBtn")
            contrast(page, ["#claim"], tag + " the claim screen with its error", 5)
            shot(page, "contrast-claim-%s-%s.png" % (scheme, lang))
            ctx.close()
            url, store = make_site()
            ctx, page = open_page(browser, url, lang, sign_in=False, color_scheme=scheme)
            page.wait_for_selector("#login:not([hidden])")
            page.evaluate("() => { const n = document.getElementById('loginNote'); n.textContent = 'x'; n.hidden = false; document.getElementById('loginErr').textContent = 'x'; }")
            contrast(page, ["#login"], tag + " the sign-in screen with a note and an error", 4)
            shot(page, "contrast-login-%s-%s.png" % (scheme, lang))
            ctx.close()


def part_narrow(browser):
    """The new screens on the smallest phones (320 and 390 wide), in both languages and in dark mode: nothing sideways, and a picture of each."""
    for width in (320, 390):
        for lang in ("en", "ar"):
            for scheme in ("light", "dark"):
                tag = "[%dpx %s %s]" % (width, lang, scheme)
                url, store = make_site()
                offer_update(store)
                store.config["dns"]["hosts"] = ["192.168.1.50 family.lan"]
                store.box_info.update({"tz": "UTC", "utcOffset": "+00:00"})
                ctx, page = open_page(browser, url, lang, viewport={"width": width, "height": 844}, color_scheme=scheme)

                def sideways():
                    return page.evaluate("() => [document.documentElement.scrollWidth - document.documentElement.clientWidth, document.getElementById('boxBody').scrollWidth - document.getElementById('boxBody').clientWidth]")
                tall = page.evaluate("() => [...document.querySelectorAll('.topbar .brand, .topbar-actions .link')].map(e => Math.round(e.getBoundingClientRect().height)).filter(h => h > 48)")
                expect(not tall and page.evaluate("() => document.documentElement.scrollWidth <= document.documentElement.clientWidth"),
                       "%s the top bar keeps each word of its buttons on one line and nothing scrolls sideways (heights over 48 px: %s)" % (tag, tall))
                shot(page, "narrow-main-%d-%s-%s.png" % (width, scheme, lang))
                open_box(page)
                eventually(lambda: "…" not in page.inner_text("#boxHealth"))
                expect(max(sideways()) <= 1, "%s My box has nothing to scroll sideways %s" % (tag, sideways()))
                shot(page, "narrow-box-top-%d-%s-%s.png" % (width, scheme, lang))
                page.evaluate("() => document.getElementById('boxNet').scrollIntoView()")
                shot(page, "narrow-box-addresses-%d-%s-%s.png" % (width, scheme, lang))
                page.evaluate("() => document.getElementById('boxHealth').scrollIntoView()")
                shot(page, "narrow-box-health-%d-%s-%s.png" % (width, scheme, lang))
                for rolled in (True, False, None):
                    store.edit_state(lambda st: st["update"].update({"status": "failed", "to": "3.1.0", "latest": "3.1.0", "at": int(time.time()) - 5,
                                                                      "error": "The installer stopped with an error (exit status 1).", "rolledBack": rolled}))
                    page.keyboard.press("Escape")
                    open_box(page)
                    if rolled is True:
                        page.click("#boxUpdates details summary")
                    expect(max(sideways()) <= 1, "%s a failed update (rolledBack %s) has nothing to scroll sideways" % (tag, rolled))
                    shot(page, "narrow-failed-%s-%d-%s-%s.png" % (str(rolled).lower(), width, scheme, lang))
                store.edit_state(lambda st: st["update"].update({"status": "running", "to": "3.1.0", "at": int(time.time()), "error": None, "rolledBack": None}))
                page.keyboard.press("Escape")
                open_box(page)
                page.evaluate("() => document.getElementById('boxPower').scrollIntoView()")
                shot(page, "narrow-power-wait-%d-%s-%s.png" % (width, scheme, lang))
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
        want = {"panel-en.png": (780, 1688), "panel-ar.png": (780, 1688), "desktop-en.png": (1280, 900), "box-en.png": (780, 3000), "box-ar.png": (780, 3000)}
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


PARTS = [("structure", part_structure), ("update", part_update), ("health", part_health), ("network", part_network), ("help", part_help),
         ("heartbeat", part_heartbeat), ("password", part_password),
         ("backup", part_backup), ("power", part_power), ("counter", part_counter), ("about", part_about), ("firstrun", part_firstrun),
         ("banner", part_banner), ("branding", part_branding), ("contrast", part_contrast), ("narrow", part_narrow), ("motion", part_motion),
         ("screenshots", part_screenshots)]

with sync_playwright() as p:
    # sinko.local is what the quick-start card tells a parent to open: the browser is told where it is (the mock listens on the loopback address)
    browser = p.chromium.launch(args=["--host-resolver-rules=MAP sinko.local 127.0.0.1"])
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
