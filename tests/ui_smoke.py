"""Headless-browser smoke test of the parent page against a fresh mock Pi-hole.

    python3 tests/ui_smoke.py [--shots DIR]

Requires: pip install playwright && playwright install chromium
"""
import argparse
import importlib.machinery
import importlib.util
import os
import sys

from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import mock_pihole  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--shots", default=None)
args = ap.parse_args()

httpd, store = mock_pihole.serve(0, os.path.join(ROOT, "web"))
loader = importlib.machinery.SourceFileLoader("pb", os.path.join(ROOT, "bin", "pihole-bahrain"))
spec = importlib.util.spec_from_loader("pb", loader)
pb = importlib.util.module_from_spec(spec)
loader.exec_module(pb)
api = pb.Api("http://127.0.0.1:%d" % httpd.server_port, password=mock_pihole.PASSWORD)
api.login()
pb.Controller(api, pb.load_catalog(os.path.join(ROOT, "lists")), os.path.join(ROOT, "lists")).setup(run_gravity=False)
api.logout()
args.url = "http://127.0.0.1:%d/" % httpd.server_port
errors = []
failures = []


def shot(page, name, full=False):
    if args.shots:
        os.makedirs(args.shots, exist_ok=True)
        page.screenshot(path=os.path.join(args.shots, name), full_page=full)


def expect(cond, msg):
    print(("ok    " if cond else "FAIL  ") + msg)
    if not cond:
        failures.append(msg)


def pressed(page, sid):
    return page.get_attribute("[data-id=%s]" % sid, "aria-pressed") == "true"


def settle(page):
    # The page's CSP forbids eval, so wait with selectors instead of JS predicates.
    page.wait_for_timeout(100)
    page.locator("body:not(.is-busy)").wait_for()
    page.wait_for_timeout(250)


with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 390, "height": 844}, device_scale_factor=2)
    # 401s are expected (auth probe before sign-in, wrong-password check).
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" and "401" not in m.text else None)
    page.on("pageerror", lambda e: errors.append(str(e)))

    page.goto(args.url)
    page.wait_for_selector("#login:not([hidden])")
    shot(page, "01-login.png")
    page.fill("#pw", "wrong")
    page.click("#loginBtn")
    page.locator("#loginErr:not(:empty)").wait_for()
    expect("not right" in page.inner_text("#loginErr"), "wrong password is rejected")

    page.fill("#pw", "test")
    page.click("#loginBtn")
    page.wait_for_selector("#app:not([hidden])")
    page.wait_for_selector(".tile")
    expect(page.inner_text("#heroTitle") == "Children are online", "hero shows online state")
    expect(page.locator(".tile").count() == 19, "19 service tiles")
    expect(page.inner_text("#heroBtn") == "Add device", "first run: hero asks to add a device")
    shot(page, "02-home.png", full=True)

    page.click("[data-act=openAdd]")
    page.wait_for_selector("#picker .pick")
    expect("This device" in page.inner_text("#picker"), "picker marks the current device")
    shot(page, "03-add.png")
    page.click("[data-act=addDevice]")
    expect("Pick a device" in page.inner_text("#addErr"), "add without selection explains what to do")
    page.click("#addForm .manual summary")
    page.fill("#manualAddr", "not-an-address")
    page.click("[data-act=addDevice]")
    expect("MAC address" in page.inner_text("#addErr"), "invalid manual address is rejected")
    page.fill("#manualAddr", "")
    page.click("#picker .pick >> nth=0")
    page.fill("#devName", "Sara's iPad")
    page.click("[data-act=addDevice]")
    settle(page)
    expect("Sara's iPad" in page.inner_text("#devices"), "device added from picker")

    page.click("[data-id=youtube]")
    settle(page)
    expect(pressed(page, "youtube"), "YouTube blocked by tap")

    page.click("[data-act=homework]")
    settle(page)
    expect(pressed(page, "tiktok") and not pressed(page, "whatsapp"), "homework blocks TikTok, keeps WhatsApp")

    page.click("[data-act=timer][data-mode=free]")
    page.click("#timerChips .chip >> nth=1")
    shot(page, "04-timer-sheet.png")
    page.click("[data-act=startTimer]")
    settle(page)
    expect(page.inner_text("#heroKicker") == "Free time", "free-time timer running")
    expect(not pressed(page, "tiktok"), "free time allows TikTok")
    expect(page.is_disabled("[data-id=tiktok]"), "tiles locked while timer runs")
    clock = page.inner_text("#heroClock")
    expect(clock.startswith("59:") or clock.startswith("1:00:"), "countdown shows ~1 hour (%s)" % clock)
    shot(page, "05-timer.png", full=True)

    page.click("#heroBtn")
    settle(page)
    expect(page.inner_text("#heroTitle") == "Children are online", "timer ended")
    expect(pressed(page, "tiktok") and pressed(page, "youtube") and not pressed(page, "whatsapp"),
           "rules from before the timer restored")

    page.click("#heroBtn")
    settle(page)
    expect(page.inner_text("#heroTitle") == "Children\u2019s internet is off", "internet off")
    shot(page, "06-off.png")
    page.click("#heroBtn")
    settle(page)

    page.click("[data-act=timer][data-mode=block]")
    page.fill("#timerMinutes", "2")
    page.click("[data-act=startTimer]")
    expect("5 to 720" in page.inner_text("#toast"), "too-short timer rejected")
    page.fill("#timerMinutes", "45")
    page.click("[data-act=startTimer]")
    settle(page)
    expect(page.inner_text("#heroKicker") == "Offline break", "offline break running")
    page.click("#heroBtn")
    settle(page)

    page.click("[data-act=pause]")
    settle(page)
    expect("Paused" in page.inner_text("#devices"), "device paused")
    page.click("[data-act=pause]")
    settle(page)

    page.check("#bedOn")
    page.fill("#bedStart", "21:30")
    page.click("[data-act=saveBed]")
    settle(page)
    page.reload()
    page.wait_for_selector(".tile")
    expect(page.input_value("#bedStart") == "21:30" and page.is_checked("#bedOn"), "bedtime saved and session kept")

    page.click(".topbar [data-act=lang]")
    expect(page.get_attribute("html", "dir") == "rtl", "Arabic switches to right-to-left")
    expect(page.inner_text("#heroTitle") == "الأطفال متصلون بالإنترنت", "Arabic hero text")
    shot(page, "07-arabic.png", full=True)
    page.click("#bedDays label >> nth=0")
    page.click(".topbar [data-act=lang]")

    page.click("[data-act=remove]")
    page.click("#confirmYes")
    settle(page)
    expect("No devices yet" in page.inner_text("#devices"), "device removed")

    page.click(".topbar [data-act=logout]")
    page.wait_for_selector("#login:not([hidden])")
    expect(True, "signed out")

    dark = browser.new_page(viewport={"width": 1280, "height": 900}, color_scheme="dark")
    dark.goto(args.url)
    dark.fill("#pw", "test")
    dark.press("#pw", "Enter")
    dark.wait_for_selector(".tile")
    shot(dark, "08-desktop-dark.png", full=True)
    browser.close()

expect(not errors, "no console errors %s" % (errors or ""))
print("\n%d failure(s)" % len(failures))
sys.exit(1 if failures else 0)
