#!/usr/bin/env python3
"""Regenerates the screenshots in docs/img from a mock box with demo data. Re-run it whenever the page changes.

    python3 tools/make-screenshots.py [--out DIR]          (needs: pip install playwright && playwright install chromium)

Writes (into docs/img by default):
    panel-en.png  panel-ar.png   the main page on a phone (390x844, 2x), English and Arabic (right to left)
    desktop-en.png               the main page on a desktop (1280 wide, two columns)
    live-en.png                  the live picture card
    box-en.png    box-ar.png     the My box sheet

Nothing here talks to a real box: tests/mock_pihole.py plays Pi-hole, with two children's devices, a day of traffic, two apps blocked,
bedtime set, a newer Sinko on offer and the box reporting a healthy 3 days of uptime. The text on the pictures is the page's own;
only the clock-dependent words ("just now", "3 hours ago") change from one run to the next.
"""
import argparse
import importlib.machinery
import importlib.util
import os
import sys
import time

from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tests"))
import mock_pihole  # noqa: E402

ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
ap.add_argument("--out", default=os.path.join(ROOT, "docs", "img"), help="directory for the pictures (default: docs/img)")
args = ap.parse_args()
os.makedirs(args.out, exist_ok=True)

loader = importlib.machinery.SourceFileLoader("pb", os.path.join(ROOT, "bin", "sinko"))
spec = importlib.util.spec_from_loader("pb", loader)
pb = importlib.util.module_from_spec(spec)
loader.exec_module(pb)


def demo_box():
    """A mock Pi-hole with Sinko set up and a family on it. Returns (server, store, url)."""
    httpd, store = mock_pihole.serve(0, os.path.join(ROOT, "web"))
    url = "http://127.0.0.1:%d/" % httpd.server_port
    catalog = pb.load_catalog(os.path.join(ROOT, "lists"))
    api = pb.Api(url.rstrip("/"), password=mock_pihole.PASSWORD)
    api.login()
    pb.Controller(api, catalog, os.path.join(ROOT, "lists")).setup(run_gravity=False)
    gid = {g["name"]: g["id"] for g in api.groups()}
    for sid in ("youtube", "tiktok", "roblox"):
        api.put_group("pb-svc-" + sid, "", True)
    for mac, name in (("AA:BB:CC:00:00:01", "Sara’s iPad"), ("AA:BB:CC:00:00:02", "Ali’s phone")):
        api.request("POST", "/api/clients", {"client": mac, "comment": name, "groups": pb.kid_groups(gid, catalog)})
    api.logout()
    notes = "https://github.com/iret33/sinko/releases/tag/v3.1.0"
    store.edit_state(lambda st: st.update({
        "schedule": {"enabled": True, "start": "21:30", "end": "06:30", "days": [0, 1, 2, 3, 4]},
        "update": {"latest": "3.1.0", "notes": notes, "checked": int(time.time()) - 3 * 3600, "auto": False, "status": "idle"},
        "telemetry": {"on": True}, "community": {"online": 1234, "at": int(time.time())},
        "setup": {"done": True}}))
    mock_pihole.seed_history(store)
    mock_pihole.start_live(store)
    return httpd, store, url


def sign_in(browser, url, lang, **view):
    ctx = browser.new_context(**dict(dict(locale="en-GB", device_scale_factor=2), **view))
    page = ctx.new_page()
    page.set_default_timeout(20000)
    page.goto(url)
    page.wait_for_selector("#login:not([hidden])")
    if lang == "ar":
        page.click("#login .lang-toggle")
    page.fill("#pw", mock_pihole.PASSWORD)
    page.press("#pw", "Enter")
    page.wait_for_selector(".tile")
    page.locator("#live:not([hidden])").wait_for()
    return ctx, page


def settle(page):
    """Let the live picture draw a few packets, so the pictures show it working."""
    end = time.time() + 8
    while time.time() < end:
        if page.locator(".pk:not([hidden])").count() >= 2:
            break
        time.sleep(0.25)
    time.sleep(0.4)


def note(path):
    print("wrote " + (os.path.relpath(path, ROOT) if path.startswith(ROOT + os.sep) else path))


def save(page, name):
    path = os.path.join(args.out, name)
    page.screenshot(path=path)
    note(path)


httpd, store, url = demo_box()
try:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for lang in ("en", "ar"):
            ctx, page = sign_in(browser, url, lang, viewport={"width": 390, "height": 844})
            settle(page)
            save(page, "panel-%s.png" % lang)
            if lang == "en":
                page.locator("#live .live-card").scroll_into_view_if_needed()
                settle(page)
                page.locator("#live .live-card").screenshot(path=os.path.join(args.out, "live-en.png"))
                note(os.path.join(args.out, "live-en.png"))
            ctx.close()
        ctx, page = sign_in(browser, url, "en", viewport={"width": 1280, "height": 900}, device_scale_factor=1)
        settle(page)
        save(page, "desktop-en.png")
        ctx.close()
        for lang in ("en", "ar"):
            ctx, page = sign_in(browser, url, lang, viewport={"width": 390, "height": 1180})
            page.click(".topbar [data-act=box]")
            page.wait_for_selector("#boxDialog[open]")
            page.wait_for_selector("#boxHealth .box-value:not(:text('…'))")
            time.sleep(0.8)
            save(page, "box-%s.png" % lang)
            ctx.close()
        browser.close()
finally:
    httpd.shutdown()
