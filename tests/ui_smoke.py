"""Headless-browser smoke test of the parent page against a fresh mock Pi-hole.

    python3 tests/ui_smoke.py [--shots DIR]

Requires: pip install playwright && playwright install chromium
"""
import argparse
import importlib.machinery
import importlib.util
import os
import sys
import time

from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import mock_pihole  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--shots", default=None)
args = ap.parse_args()

httpd, store = mock_pihole.serve(0, os.path.join(ROOT, "web"))
loader = importlib.machinery.SourceFileLoader("pb", os.path.join(ROOT, "bin", "sinko"))
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

    # --- another Pi-hole client row that overrides a child's MAC row (FTL prefers IP/subnet rows)
    catalog = pb.load_catalog(os.path.join(ROOT, "lists"))

    def pb_api(method, path, body=None):
        api.login()
        try:
            return api.request(method, path, body)
        finally:
            api.logout()

    pb_api("POST", "/api/clients", {"client": "192.168.1.0/24", "comment": "", "groups": [0]})
    page.fill("#pw", "test")
    page.click("#loginBtn")
    page.wait_for_selector(".tile")
    page.click("[data-act=openAdd]")
    page.wait_for_selector("#picker .pick")
    page.click("#picker .pick >> nth=0")                         # Sara-iPad, 192.168.1.21
    page.click("[data-act=addDevice]")
    page.locator(".device-warn").wait_for()
    expect("192.168.1.0/24" in page.inner_text(".device-warn"), "a subnet row that overrides the child is called out")
    expect("overrides it" in page.inner_text("#toast"), "adding such a device warns right away")
    shot(page, "09-shadowed.png", full=True)
    page.click(".topbar [data-act=lang]")
    expect("لوحة Pi-hole" in page.inner_text(".device-warn"), "the warning is translated")
    page.click(".topbar [data-act=lang]")
    pb_api("DELETE", "/api/clients/" + api.q("192.168.1.0/24"))
    page.reload()
    page.wait_for_selector(".tile")
    expect(page.locator(".device-warn").count() == 0, "no warning once the overriding row is gone")

    store.devices.append({"id": 40, "hwaddr": "aa:bb:cc:00:00:04", "macVendor": "", "lastQuery": int(time.time()) - 20,
                          "numQueries": 9, "ips": [{"ip": "fe80::4", "name": ""}, {"ip": "2001:db8::4", "name": ""}]})
    gid = {g["name"]: g["id"] for g in pb_api("GET", "/api/groups")["groups"]}
    pb_api("POST", "/api/clients", {"client": "AA:BB:CC:00:00:04", "comment": "Tablet", "groups": pb.kid_groups(gid, catalog)})
    pb_api("POST", "/api/clients", {"client": "2001:db8::/32", "comment": "", "groups": [0]})
    page.reload()
    page.wait_for_selector(".tile")
    expect(page.locator(".device-warn").count() == 1, "an IPv6 subnet row is detected too")

    # --- IPv6-only network-table entries are not offered; typing an IPv6 address asks first
    now_ts = int(time.time())
    store.devices.append({"id": 41, "hwaddr": "ip-fd00::1234", "macVendor": "", "lastQuery": now_ts - 5,
                          "numQueries": 3, "ips": [{"ip": "fd00::1234", "name": ""}]})
    store.devices.append({"id": 42, "hwaddr": "ip-fd00::2", "macVendor": "", "lastQuery": now_ts - 6,
                          "numQueries": 3, "ips": [{"ip": "fd00::2", "name": ""}, {"ip": "192.168.1.88", "name": ""}]})
    page.reload()
    page.wait_for_selector(".tile")
    page.click("[data-act=openAdd]")
    page.wait_for_selector("#picker .pick")
    expect(page.locator("#picker [data-addr*=fd00]").count() == 0, "an IPv6-only entry is not offered in the picker")
    expect(page.locator("#picker [data-addr='192.168.1.88']").count() == 1, "an entry with an IPv4 address is offered by it")
    page.click("#addForm .manual summary")
    page.fill("#manualAddr", "fd00::5")
    page.click("[data-act=addDevice]")
    page.locator("#confirmDialog[open]").wait_for()
    expect("IPv6" in page.inner_text("#confirmText"), "typing an IPv6 address asks for confirmation")
    page.click("#confirmDialog button[value=no]")
    page.wait_for_timeout(300)
    expect("fd00::5" not in [c["client"] for c in store.clients], "cancelling adds nothing")
    page.click("[data-act=addDevice]")
    page.locator("#confirmDialog[open]").wait_for()
    page.click("#confirmYes")
    settle(page)
    expect("fd00::5" in [c["client"] for c in store.clients], "confirming adds the device")

    # --- the phone's clock is wrong by two hours: timers must still follow the BOX's clock
    skew = browser.new_page(viewport={"width": 390, "height": 844})
    skew.clock.set_fixed_time(int(time.time()) + 2 * 3600)
    skew.goto(args.url)
    skew.fill("#pw", "test")
    skew.press("#pw", "Enter")
    skew.wait_for_selector(".tile")
    skew.click("[data-act=timer][data-mode=free]")
    skew.click("#timerChips .chip >> nth=1")
    skew.click("[data-act=startTimer]")
    skew.locator("#heroClock:not([hidden])").wait_for()
    import json as _json
    state = next(g for g in store.groups if g["name"] == "pb-state")
    until = _json.loads(state["comment"])["timer"]["until"]
    expect(abs(until - (time.time() + 3600)) < 30, "a timer started on a phone with a wrong clock ends an hour from now on the box (off by %ds)" % (until - time.time() - 3600))
    clock = skew.inner_text("#heroClock")
    expect(clock.startswith("59:") or clock.startswith("1:00:"), "and its countdown shows about an hour, not three (%s)" % clock)
    skew.click("#heroBtn")
    skew.close()

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
