"""Browser tests of the live picture on the real parent page, against the mock Pi-hole (tests/mock_pihole.py).

    python3 tests/live_smoke.py [--shots DIR]      (needs: pip install playwright && playwright install chromium)

Covers: the numbers match Pi-hole's own, packets come from real queries and never show a domain name, in-progress queries
are not drawn early, the request load stays small, the tour's examples never touch a number, the detail sheet, Arabic
(right to left), reduced motion, privacy levels, and the "rules may not apply" states.
"""
import argparse
import importlib.machinery
import importlib.util
import os
import re
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

loader = importlib.machinery.SourceFileLoader("pb", os.path.join(ROOT, "bin", "pihole-bahrain"))
spec = importlib.util.spec_from_loader("pb", loader)
pb = importlib.util.module_from_spec(spec)
loader.exec_module(pb)
CATALOG = pb.load_catalog(os.path.join(ROOT, "lists"))

failures = []
errors = []
servers = []


def expect(cond, msg):
    print(("ok    " if cond else "FAIL  ") + msg)
    if not cond:
        failures.append(msg)


def make_site(live=False, blocked=("youtube", "tiktok"), privacy=0, history=True):
    """A fresh mock box with two children (Sara's iPad is online, Ali's phone idle), some apps blocked, a day of history."""
    httpd, store = mock_pihole.serve(0, os.path.join(ROOT, "web"))
    servers.append(httpd)
    api = pb.Api("http://127.0.0.1:%d" % httpd.server_port, password=mock_pihole.PASSWORD)
    api.login()
    pb.Controller(api, CATALOG, os.path.join(ROOT, "lists")).setup(run_gravity=False)
    gid = {g["name"]: g["id"] for g in api.groups()}
    for sid in blocked:
        api.put_group("pb-svc-" + sid, "", True)
    for mac, name in (("AA:BB:CC:00:00:01", "Sara's iPad"), ("AA:BB:CC:00:00:02", "Ali's phone")):
        api.request("POST", "/api/clients", {"client": mac, "comment": name, "groups": pb.kid_groups(gid, CATALOG)})
    api.logout()
    if history:
        mock_pihole.seed_history(store)
    if live:
        mock_pihole.start_live(store)
    store.privacy = privacy
    return "http://127.0.0.1:%d/" % httpd.server_port, store


def open_page(browser, url, **opts):
    ctx = browser.new_context(**dict(dict(viewport={"width": 390, "height": 844}, locale="en-GB"), **opts))
    page = ctx.new_page()
    page.set_default_timeout(20000)
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" and "401" not in m.text else None)
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(url)
    page.wait_for_selector("#login:not([hidden])")
    page.fill("#pw", "test")
    page.press("#pw", "Enter")
    page.wait_for_selector(".tile")
    page.locator("#live:not([hidden])").wait_for()
    return ctx, page


def shot(page, name, full=False):
    if args.shots:
        os.makedirs(args.shots, exist_ok=True)
        page.screenshot(path=os.path.join(args.shots, name), full_page=full)


def packets(page):
    return page.evaluate("() => [...document.querySelectorAll('.pk:not([hidden])')].map(p => ({text: p.textContent, cls: p.className}))")


def number(text):
    text = text.replace(",", "").replace("٬", "").strip()
    m = re.match(r"^([\d.]+)\s*([KM]?)", text)
    if not m:
        return None
    return float(m.group(1)) * {"": 1, "K": 1e3, "M": 1e6}[m.group(2)]


def wait_until(fn, seconds, step=0.25):
    end = time.time() + seconds
    while time.time() < end:
        got = fn()
        if got:
            return got
        time.sleep(step)
    return None


DOMAINLIKE = re.compile(r"[a-z0-9-]+\.[a-z]{2,}", re.I)
KID1, KID2 = "192.168.1.21", "192.168.1.22"

with sync_playwright() as p:
    browser = p.chromium.launch()

    # ------------------------------------------------------------------ A: busy home, real numbers, a gentle load
    url, store = make_site(live=True)
    reqs = []
    ctx, page = open_page(browser, url)
    page.on("request", lambda r: reqs.append(r.url) if "/api/" in r.url else None)
    expect("rules are working on both devices" in page.inner_text("#liveTitle"), "headline says the rules are working: %r" % page.inner_text("#liveTitle"))
    expect("Apps blocked for children: 2" in page.inner_text("#liveSub"), "and how many apps are blocked: %r" % page.inner_text("#liveSub"))
    names = page.locator(".node-dev .node-name").all_inner_texts()
    expect(names == ["Sara's iPad", "Ali's phone", "Everyone else"], "two children and the rest of the home are drawn: %s" % names)
    expect(page.locator("path.wire-base").count() == 6, "six wires exist (3 device slots, rest of home, box to internet, the direct line)")
    states = page.locator(".node-dev .node-state-text").all_inner_texts()
    expect(states[0] == "Online now" and states[1] == "Quiet", "Sara's iPad is online and Ali's phone idle: %s" % states)

    time.sleep(5)
    want_total = sum(1 for q in store.query_log if q["time"] >= time.time() - 86400)
    want_blocked = sum(1 for q in store.query_log if q["time"] >= time.time() - 86400 and q["status"] in mock_pihole.BLOCKED)
    got_total, got_blocked = number(page.inner_text("#statChecked")), number(page.inner_text("#statStopped"))
    expect(got_total is not None and abs(got_total - want_total) <= max(1, 0.02 * want_total) + 40,
           "'Checked' matches Pi-hole's total (page %s, box %s)" % (got_total, want_total))
    expect(got_blocked is not None and abs(got_blocked - want_blocked) <= 0.02 * want_blocked + 40,
           "'Stopped' matches Pi-hole's blocked count (page %s, box %s)" % (got_blocked, want_blocked))
    share = page.inner_text("#statShare")
    expect(share.endswith("%") and number(share[:-1]) is not None, "the share tile is a percentage: %r" % share)
    expect("last 24 hours" in page.inner_text("#liveScope") and "Whole home" in page.inner_text("#liveScope"), "the scope says what the numbers cover")

    seen, kinds, peak = set(), set(), 0
    end = time.time() + 14
    while time.time() < end:
        pks = packets(page)
        peak = max(peak, len(pks))
        for k in pks:
            seen.add(k["text"].strip())
            kinds.update(c for c in k["cls"].split() if c.startswith("is-"))
        time.sleep(0.2)
    expect(len(seen) > 0, "packets travel along the wires (saw %d different labels)" % len(seen))
    expect("is-blocked" in kinds and "is-allowed" in kinds, "both blocked and allowed requests are drawn (%s)" % sorted(kinds))
    expect("is-direct" in kinds, "after a yes, a dot runs along the dotted line that goes around the box")
    expect(not any(DOMAINLIKE.search(s) for s in seen), "no packet ever shows a domain name: %s" % sorted(seen)[:8])
    expect(peak <= 8, "never more than 8 packets at once (peak %d)" % peak)
    shot(page, "live-phone.png", full=False)

    q_urls = [u for u in reqs if "/api/queries" in u]
    s_urls = [u for u in reqs if "/api/stats/summary" in u]
    expect(len(q_urls) <= 20 / 3.5 + 4, "the query log is read about every 3.5 s (%d reads in about 20 s)" % len(q_urls))
    expect(len(s_urls) <= 4, "the totals are read about every 15 s (%d reads)" % len(s_urls))
    expect(all("length=" in u and int(re.search(r"length=(\d+)", u).group(1)) <= 60 for u in q_urls), "each read asks for at most 60 rows")
    expect(len(q_urls) >= 3 and all("from=" in u for u in q_urls[1:]), "reads after the first only ask for newer rows (%s)" % [u.split("?")[1] for u in q_urls[:3]])
    expect(not any("/api/history" in u or "top_domains" in u for u in reqs), "history and top lists are fetched only when the detail sheet opens")

    # a dialog open: nothing is polled, nothing moves
    page.click("[data-act=openAdd]")
    page.locator("#addDialog[open]").wait_for()
    before = len([u for u in reqs if "/api/queries" in u])
    time.sleep(9)
    after = len([u for u in reqs if "/api/queries" in u])
    expect(after == before, "while a dialog is open the picture stops polling (%d -> %d)" % (before, after))
    expect(not packets(page), "and no packet is left hanging on screen")
    page.keyboard.press("Escape")
    ctx.close()

    # ------------------------------------------------------------------ B: exact events (no background traffic)
    url, store = make_site(live=False)
    ctx, page = open_page(browser, url)
    time.sleep(4.5)                                     # the query log is primed: older queries are never replayed
    expect(not packets(page), "nothing is drawn for queries that were already in the log")
    base = number(page.inner_text("#statChecked"))
    base_blocked = number(page.inner_text("#statStopped"))
    for _ in range(3):
        store.add_query("www.youtube.com", "GRAVITY", KID1, "Sara-iPad")
    store.add_query("www.apple.com", "FORWARDED", "192.168.1.9", "parents-phone")
    inprog = store.add_query("api.tiktok.com", "IN_PROGRESS", KID2, "ali-galaxy")
    voice = {"text": ""}

    def blocked_packet():
        for k in packets(page):
            if "is-blocked" in k["cls"] and "YouTube" in k["text"]:
                return k
        return None
    pk = wait_until(blocked_packet, 9)
    wait_until(lambda: page.inner_text("#boxSub").startswith("No"), 4, 0.1)
    voice["text"] = page.inner_text("#boxSub")
    expect(pk is not None, "a blocked YouTube request from Sara's iPad is drawn as a stopped packet (%s)" % (pk and pk["text"]))
    expect(pk is not None and "✕" in page.evaluate("() => getComputedStyle(document.querySelector('.pk.is-blocked .pk-label'), '::before').content"),
           "stopped packets carry a cross, not only a colour")
    expect("No" in voice["text"] and "YouTube" in voice["text"] and "Sara's iPad" in voice["text"], "the family box says no, naming the app and the device: %r" % voice["text"])
    time.sleep(1.5)
    page.click("#liveRecentTitle")                       # the list is closed until opened (open by default only with reduced motion)
    recent = page.locator("#liveRecentList .recent-item").all_inner_texts()
    expect(any("Stopped YouTube" in r and "Sara's iPad" in r and "×3" in r for r in recent), "the text list collapses repeats: %s" % recent)
    expect(not any("tiktok" in r.lower() for r in recent), "an in-progress query is not shown before it is final")
    total = number(page.inner_text("#statChecked"))
    expect(total == base + 4, "the 'Checked' number counted the 4 finished queries (%s -> %s)" % (base, total))
    expect(number(page.inner_text("#statStopped")) == base_blocked + 3, "and 'Stopped' counted the 3 blocked ones")
    store.query_log[-1]["status"] = "FORWARDED"          # the in-progress query finishes
    time.sleep(5)
    recent = page.locator("#liveRecentList .recent-item").all_inner_texts()
    expect(any("Allowed TikTok" in r for r in recent), "it shows up once, when it is final: %s" % recent)
    expect(not any("Allowed" in r and "Ali's phone" in r for r in recent), "a device is named for stops only, never for allowed lookups: %s" % recent)
    expect(number(page.inner_text("#statChecked")) == base + 5, "and is counted once")

    # tour: examples never change a number
    n0 = (page.inner_text("#statChecked"), page.inner_text("#statStopped"), page.inner_text("#statShare"))
    page.click("[data-act=tour]")
    page.locator("#liveTour:not([hidden])").wait_for()
    expect("Step 1 of 5" in page.inner_text("#tourCount"), "the tour starts at step 1: %r" % page.inner_text("#tourCount"))
    texts = []
    for step in range(4):
        page.click("#tourNext")
        time.sleep(0.4)
        texts.append(page.inner_text("#tourStep"))
        if step in (1, 2):
            ex = wait_until(lambda: [k for k in packets(page) if "is-example" in k["cls"]], 4)
            expect(bool(ex) and "Example" in ex[0]["text"], "step %d plays an example packet labelled Example: %s" % (step + 2, ex))
    expect(len(set(texts)) == 4, "each step has its own words")
    expect(page.locator(".node-dev .node-name").all_inner_texts() == ["Example phone", "Everyone else"], "examples come from an example phone, never from a real child: %s" % page.locator(".node-dev .node-name").all_inner_texts())
    shot(page, "live-tour.png")
    expect((page.inner_text("#statChecked"), page.inner_text("#statStopped"), page.inner_text("#statShare")) == n0, "the tour changed no number")
    page.click("#tourNext")                                # Done
    expect(page.locator("#liveTour").is_hidden(), "finishing the tour closes it")
    expect(page.evaluate("() => document.activeElement && document.activeElement.id") == "tourBtn", "and puts focus back on the button that opened it")
    expect(page.locator(".node-dev .node-name").all_inner_texts() == ["Sara's iPad", "Ali's phone", "Everyone else"], "and the real devices come back")

    # the picture can be folded away; the choice is remembered, and nothing is read while it is closed
    reqs_b = []
    page.on("request", lambda r: reqs_b.append(r.url) if "/api/queries" in r.url else None)
    page.click("#liveToggle")
    expect(page.locator("#stage").is_hidden() and page.get_attribute("#liveToggle", "aria-expanded") == "false", "the toggle folds the picture away")
    expect(page.locator("#statChecked").is_visible(), "the numbers stay")
    expect(page.evaluate("() => localStorage.getItem('pb.live')") == "closed", "the choice is remembered")
    time.sleep(1)
    n_before = len(reqs_b)
    store.add_query("www.youtube.com", "GRAVITY", KID1, "Sara-iPad")
    time.sleep(8)
    expect(len(reqs_b) == n_before, "while folded the query log is not read (%d reads)" % (len(reqs_b) - n_before))
    page.click("#liveToggle")
    expect(page.locator("#stage").is_visible(), "unfolding shows it again")
    time.sleep(5.5)
    expect(not [k for k in packets(page) if "YouTube" in k["text"]], "and nothing from while it was folded is replayed")

    # detail sheet
    page.click("[data-act=detail]")
    page.locator("#liveSheet[open]").wait_for()
    page.locator(".hours .hour").first.wait_for()
    expect(page.locator(".hours .hour").count() == 24, "the busy-hours chart has 24 bars")
    expect(page.locator(".how-item").count() == 4, "four plain steps explain how it works")
    tops = page.locator(".top-item .top-name").all_inner_texts()
    expect(len(tops) >= 1 and not any(DOMAINLIKE.search(t) for t in tops), "the most-stopped list names apps, never domains: %s" % tops)
    expect("Phone book" in page.inner_text("#sheetBody") or "phone book" in page.inner_text("#sheetBody"), "technical words are explained in plain ones")
    facts = page.locator(".fact-value").all_inner_texts()
    expect(len(facts) >= 4 and all(f.strip() for f in facts), "the curious numbers are filled in: %s" % facts)
    time.sleep(0.6)
    shot(page, "live-detail.png")
    page.keyboard.press("Escape")
    page.locator("#liveSheet:not([open])").wait_for(state="attached")
    ctx.close()

    # ------------------------------------------------------------------ C: right to left
    url, store = make_site(live=False)
    ctx, page = open_page(browser, url, locale="ar-BH")
    expect(page.evaluate("() => document.documentElement.dir") == "rtl", "the page is right to left in Arabic")
    expect("القواعد تعمل" in page.inner_text("#liveTitle"), "the headline is in Arabic: %r" % page.inner_text("#liveTitle"))
    x = page.evaluate("() => [...document.querySelectorAll('.node-dev')].map(n => Math.round(n.getBoundingClientRect().left))")
    expect(x == sorted(x, reverse=True), "the first child is drawn on the right: %s" % x)
    d = page.evaluate("() => document.querySelector('path.wire-base').getAttribute('d')")
    expect(d.startswith("M"), "wires follow the mirrored nodes")
    time.sleep(4.5)
    store.add_query("www.youtube.com", "GRAVITY", KID1, "Sara-iPad")
    pk = wait_until(lambda: [k for k in packets(page) if "is-blocked" in k["cls"]], 9)
    expect(bool(pk), "an Arabic page draws stopped packets too: %s" % pk)
    shot(page, "live-ar.png")
    page.click("[data-act=tour]")
    expect("الخطوة 1 من 5" in page.inner_text("#tourCount"), "the tour is translated")
    ctx.close()

    # ------------------------------------------------------------------ D: reduced motion: no movement, everything still counted and listed
    url, store = make_site(live=False)
    ctx, page = open_page(browser, url, reduced_motion="reduce")
    time.sleep(4.5)
    store.add_query("www.youtube.com", "GRAVITY", KID1, "Sara-iPad")
    store.add_query("www.netflix.com", "FORWARDED", KID2, "ali-galaxy")
    seen_pk = False
    for _ in range(30):
        seen_pk = seen_pk or bool(packets(page))
        time.sleep(0.25)
    expect(not seen_pk, "with reduced motion nothing moves")
    expect(page.evaluate("() => document.getElementById('liveRecent').open"), "and the text list is open by default")
    recent = page.locator("#liveRecentList .recent-item").all_inner_texts()
    expect(any("Stopped YouTube" in r for r in recent) and any("Allowed Netflix" in r for r in recent), "it tells what happened: %s" % recent)
    expect(page.evaluate("() => getComputedStyle(document.querySelector('.live-pill .dot')).animationName") == "none", "no animation runs")
    shot(page, "live-reduced.png")
    ctx.close()

    # ------------------------------------------------------------------ E: privacy levels
    url, store = make_site(live=False, privacy=3)
    ctx, page = open_page(browser, url)
    time.sleep(5)
    expect("Totals only" in page.inner_text("#livePillText"), "privacy level 3 says totals only: %r" % page.inner_text("#livePillText"))
    expect("privacy" in page.inner_text("#liveNote").lower(), "and explains why: %r" % page.inner_text("#liveNote"))
    store.add_query("www.youtube.com", "GRAVITY", KID1, "Sara-iPad")
    time.sleep(5)
    expect(not packets(page), "no packets are drawn")
    expect(number(page.inner_text("#statChecked")) is not None, "the totals are still shown")
    ctx.close()

    url, store = make_site(live=False, privacy=2)
    ctx, page = open_page(browser, url)
    time.sleep(4.5)
    store.add_query("hidden", "GRAVITY", "0.0.0.0", "hidden")
    store.add_query("hidden", "FORWARDED", "0.0.0.0", "hidden")
    pk = wait_until(lambda: packets(page), 9)
    expect(bool(pk) and not DOMAINLIKE.search(pk[0]["text"]), "hidden queries are drawn without a name: %s" % pk)
    time.sleep(1)
    expect(page.locator(".node-rest .node-name").inner_text() == "Whole home", "when clients are hidden, the rest of the home becomes 'Whole home'")
    expect(not [r for r in page.locator("#liveRecentList .recent-item").all_inner_texts() if "hidden" in r.lower()], "the text list never shows the hidden marker")
    ctx.close()

    # ------------------------------------------------------------------ F: a device that may not follow the rules
    url, store = make_site(live=False)
    api = pb.Api(url.rstrip("/"), password=mock_pihole.PASSWORD)
    api.login()
    api.request("POST", "/api/clients", {"client": "192.168.1.0/24", "comment": "", "groups": [0]})
    api.logout()
    store.devices[1]["lastQuery"] = 0                  # Ali's phone has never asked
    ctx, page = open_page(browser, url)
    title = page.inner_text("#liveTitle")
    expect("need a look" in title or "may not" in title, "the headline asks for a look: %r" % title)
    expect(page.locator(".live-icon.tone-warn").count() == 1, "and turns amber")
    expect(page.locator(".node-dev.st-overridden").count() >= 1, "the overridden device is marked")
    shot(page, "live-warn.png")
    page.locator(".node-dev.st-overridden").first.click()
    expect("overrides" in page.inner_text("#liveCaption"), "tapping it explains why in plain words: %r" % page.inner_text("#liveCaption"))
    ctx.close()

    # the box cannot tell when devices were online (privacy level, or the device list failed): say "rules set", never "not seen"
    url, store = make_site(live=False)
    store.devices = []
    ctx, page = open_page(browser, url)
    states = page.locator(".node-dev .node-state-text").all_inner_texts()
    expect(states[:2] == ["Rules set", "Rules set"], "without device activity the nodes say 'Rules set': %s" % states)
    expect("not seen" not in page.inner_text("#stage").lower() and "need a look" not in page.inner_text("#liveTitle"), "and nothing claims a device was not seen")
    expect("rules are set for both devices" in page.inner_text("#liveTitle"), "the headline says the rules are set: %r" % page.inner_text("#liveTitle"))
    ctx.close()


    # ------------------------------------------------------------------ H: labels tell the truth (reasons, not guesses)
    url, store = make_site(live=False, blocked=("youtube",))
    ctx, page = open_page(browser, url)
    time.sleep(4.5)
    store.add_query("hidden", "FORWARDED", KID1, "Sara-iPad")                      # one odd row must not switch the names off for good
    store.add_query("logs.netflix.com", "GRAVITY", KID1, "Sara-iPad")                # an ad list stops a tracker on an ALLOWED app's domain
    store.add_query("pixel.facebook.com", "GRAVITY", KID1, "Sara-iPad")
    store.add_query("www.youtube.com", "GRAVITY", KID1, "Sara-iPad")                 # YouTube really is blocked
    store.add_query("www.youtube.com", "GRAVITY", KID1, "Sara-iPad", when=time.time() + 3600)   # a row from the future (the box clock stepped back)
    time.sleep(6)
    page.click("#liveRecentTitle")
    recent = page.locator("#liveRecentList .recent-item").all_inner_texts()
    expect(any("Stopped YouTube" in r for r in recent), "a stop is credited to YouTube, which is blocked: %s" % recent)
    expect(any("Stopped An unwanted site" in r and "\u00d72" in r for r in recent), "trackers on Netflix and Facebook (allowed) are 'an unwanted site': %s" % recent)
    expect(not any("Stopped Netflix" in r or "Stopped Facebook" in r for r in recent), "never 'Stopped Netflix' while Netflix is allowed")
    expect("privacy" not in page.inner_text("#liveRecentList").lower(), "one row with a hidden name does not switch the list to the privacy note")
    store.add_query("www.youtube.com", "GRAVITY", KID1, "Sara-iPad")
    pk = wait_until(lambda: [k for k in packets(page) if "is-blocked" in k["cls"]], 9)
    expect(bool(pk), "a row stamped in the future does not freeze the picture: later queries still arrive")
    # bedtime / internet off: everything is stopped, and that is the reason shown
    apiw = pb.Api(url.rstrip("/"), password=mock_pihole.PASSWORD)
    apiw.login()
    apiw.put_group("pb-offline", "", True)
    apiw.logout()
    page.reload()
    page.locator("#live:not([hidden])").wait_for()
    time.sleep(4.5)
    store.add_query("time.android.com", "REGEX", KID1, "Sara-iPad")
    time.sleep(6)
    page.click("#liveRecentTitle") if not page.evaluate("() => document.getElementById('liveRecent').open") else None
    recent = page.locator("#liveRecentList .recent-item").all_inner_texts()
    expect(any(r.startswith("Internet off") and "Sara's iPad" in r for r in recent), "while the internet is off, a stop says so (not 'an unwanted site'): %s" % recent)
    expect("Internet is off for children" in page.inner_text("#liveTitle"), "and the headline says it: %r" % page.inner_text("#liveTitle"))
    ctx.close()

    # ------------------------------------------------------------------ I: Pi-hole's blocking is switched off
    url, store = make_site(live=False)
    store.blocking = "disabled"
    ctx, page = open_page(browser, url)
    time.sleep(1.5)
    expect("Blocking is switched off" in page.inner_text("#liveTitle"), "a green headline is never shown while blocking is off: %r" % page.inner_text("#liveTitle"))
    expect(page.locator(".live-icon.tone-warn").count() == 1 and "Advanced settings" in page.inner_text("#liveSub"), "it is amber and says where to fix it")
    expect("blocking is off" in page.inner_text("#boxSub"), "and the box says so too: %r" % page.inner_text("#boxSub"))
    store.blocking = "enabled"
    time.sleep(17)
    expect("rules are working" in page.inner_text("#liveTitle"), "it recovers by itself: %r" % page.inner_text("#liveTitle"))
    ctx.close()

    # ------------------------------------------------------------------ J: quiet for hours is not "working"
    url, store = make_site(live=False)
    store.devices[1]["lastQuery"] = int(time.time()) - 20 * 3600
    ctx, page = open_page(browser, url)
    expect("set for both devices" in page.inner_text("#liveTitle"), "one device silent for 20 hours: the headline only says the rules are set: %r" % page.inner_text("#liveTitle"))
    page.locator(".node-dev").nth(1).click()
    expect("cannot apply" in page.inner_text("#liveCaption"), "and its explanation does not claim it is connected: %r" % page.inner_text("#liveCaption"))
    expect(page.inner_text("#liveSay").strip() != "", "a tapped explanation is also announced to screen readers")
    ctx.close()

    # ------------------------------------------------------------------ K: four children: "online" belongs to one device, not to the folded node
    url, store = make_site(live=False)
    apik = pb.Api(url.rstrip("/"), password=mock_pihole.PASSWORD)
    apik.login()
    gid = {g["name"]: g["id"] for g in apik.groups()}
    for mac, name in (("AA:BB:CC:00:00:03", "Noor"), ("AA:BB:CC:00:00:04", "Omar")):
        apik.request("POST", "/api/clients", {"client": mac, "comment": name, "groups": pb.kid_groups(gid, CATALOG)})
    apik.logout()
    store.devices.append({"id": 10, "hwaddr": "aa:bb:cc:00:00:03", "macVendor": "", "lastQuery": int(time.time()) - 3 * 86400, "numQueries": 5, "ips": [{"ip": "192.168.1.23", "name": "noor"}]})
    store.devices.append({"id": 11, "hwaddr": "aa:bb:cc:00:00:04", "macVendor": "", "lastQuery": int(time.time()) - 20, "numQueries": 5, "ips": [{"ip": "192.168.1.24", "name": "omar"}]})
    ctx, page = open_page(browser, url)
    time.sleep(4.5)
    names = page.locator(".node-dev .node-name").all_inner_texts()
    expect(names == ["Sara's iPad", "Ali's phone", "2 more", "Everyone else"], "four children: two nodes and '2 more': %s" % names)
    expect("Noor is not using the box" in page.inner_text("#liveTitle"), "the one that has not been seen is named: %r" % page.inner_text("#liveTitle"))
    store.add_query("www.apple.com", "FORWARDED", "192.168.1.24", "omar")           # Omar is online; Noor is not
    time.sleep(6)
    expect("Noor is not using the box" in page.inner_text("#liveTitle"), "another device's traffic does not hide Noor's warning: %r" % page.inner_text("#liveTitle"))
    expect(page.locator(".node-dev").nth(2).inner_text().count("Not seen in 24 hours") == 1, "the folded node still shows the worst state")
    shot(page, "live-four.png")
    ctx.close()

    # ------------------------------------------------------------------ L: privacy level 1 and the detail sheet
    url, store = make_site(live=False, privacy=1)
    ctx, page = open_page(browser, url)
    page.click("[data-act=detail]")
    page.locator("#liveSheet[open]").wait_for()
    page.locator(".hours .hour").first.wait_for()
    time.sleep(0.5)
    top = page.inner_text("#detailTop")
    expect("Nothing has been stopped yet" not in top and "privacy" in top.lower(), "at privacy level 1 the sheet says why there is no list, not 'nothing stopped': %r" % top)
    ctx.close()

    # ------------------------------------------------------------------ M: sign out and in again: a clean stage, no ghosts
    url, store = make_site(live=False)
    ctx, page = open_page(browser, url)
    time.sleep(2)
    page.click(".topbar [data-act=logout]")
    page.locator("#login:not([hidden])").wait_for()
    errors_before = len(errors)
    page.fill("#pw", "test")
    page.press("#pw", "Enter")
    page.wait_for_selector(".tile")
    page.locator("#live:not([hidden])").wait_for()
    time.sleep(1.5)
    expect(page.locator("g.wire").count() == 6 and page.locator(".pk").count() == 8, "after signing out and in there is one set of wires and packets (%d wires, %d packets)" % (page.locator("g.wire").count(), page.locator(".pk").count()))
    expect(len(errors) == errors_before, "and no errors from the old stage's timers")
    ctx.close()

    # ------------------------------------------------------------------ N: keyboard and touch in the tour, folding
    url, store = make_site(live=False)
    ctx, page = open_page(browser, url)
    page.click("[data-act=tour]")
    page.locator("#liveTour:not([hidden])").wait_for()
    page.click("#tourNext")
    page.focus("#tourBack")
    page.keyboard.press("Enter")
    expect("Step 1 of 5" in page.inner_text("#tourCount"), "Back returns to step 1")
    expect(page.evaluate("() => document.activeElement && document.activeElement.id") == "tourNext", "and focus moves to Next instead of falling to the page")
    page.dblclick("#tourNext")
    time.sleep(0.3)
    expect("Step 3 of 5" in page.inner_text("#tourCount"), "a double tap on Next goes two steps forward: %r" % page.inner_text("#tourCount"))
    page.locator(".node-box").click()
    expect("family box is a small computer" in page.inner_text("#liveCaption"), "tapping a node during the tour explains it: %r" % page.inner_text("#liveCaption"))
    page.click("#tourEnd")
    page.click("#liveToggle")
    expect(page.locator("#livePill").is_hidden(), "a folded card shows no 'live' pill")
    page.click("[data-act=stat][data-which=checked]")
    expect(page.locator("#stage").is_visible() and "asked the box" in page.inner_text("#liveCaption"), "tapping a number while folded opens the card and explains it")
    ctx.close()

    # ------------------------------------------------------------------ O: the feed stops: the pill and the screen-reader line say so
    url, store = make_site(live=False)
    ctx, page = open_page(browser, url)
    time.sleep(1)
    page.route("**/api/queries*", lambda route: route.abort())
    got = wait_until(lambda: "Not updating" in page.inner_text("#livePillText"), 30)
    expect(bool(got), "when the query log cannot be read the pill says 'Not updating'")
    expect("Not updating" in page.inner_text("#liveAria"), "and so does the screen-reader line: %r" % page.inner_text("#liveAria"))
    page.unroute("**/api/queries*")
    got = wait_until(lambda: "Live" in page.inner_text("#livePillText"), 45)
    expect(bool(got), "it recovers by itself")
    errors[:] = [e for e in errors if "ERR_FAILED" not in e]            # the requests this scenario aborted on purpose
    ctx.close()

    # no children yet
    httpd, store = mock_pihole.serve(0, os.path.join(ROOT, "web"))
    servers.append(httpd)
    api = pb.Api("http://127.0.0.1:%d" % httpd.server_port, password=mock_pihole.PASSWORD)
    api.login()
    pb.Controller(api, CATALOG, os.path.join(ROOT, "lists")).setup(run_gravity=False)
    api.logout()
    mock_pihole.seed_history(store)
    ctx, page = open_page(browser, "http://127.0.0.1:%d/" % httpd.server_port)
    expect("No children" in page.inner_text("#liveTitle"), "with no children the picture invites you to add a device: %r" % page.inner_text("#liveTitle"))
    expect(page.locator(".node-add").count() == 1, "and offers the add button as the first node")
    page.click(".node-add")
    expect(page.locator("#addDialog[open]").count() == 1, "which opens the add-device sheet")
    ctx.close()

    # ------------------------------------------------------------------ G: desktop
    url, store = make_site(live=True)
    ctx, page = open_page(browser, url, viewport={"width": 1280, "height": 900})
    time.sleep(6)
    box = page.evaluate("() => { const a = document.querySelector('.col-main').getBoundingClientRect(), b = document.querySelector('.col-side').getBoundingClientRect(); return [a.left, b.left, a.width]; }")
    expect(box[1] > box[0] + box[2] - 1, "on a wide screen the picture and the controls sit side by side")
    shot(page, "live-desktop.png")
    ctx.close()

    browser.close()

for s in servers:
    s.shutdown()
expect(not errors, "no console errors %s" % (errors or ""))
print("\n%d failure(s)" % len(failures))
sys.exit(1 if failures else 0)
