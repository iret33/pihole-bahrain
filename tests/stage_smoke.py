"""Browser test of the live picture's drawing engine (web/pb-live.js) on a minimal fixture page, independent of the real
page's layout: wires follow their nodes, packets run their steps in order from a fixed pool, reduced motion and pause move
nothing but still run every callback, and hostile domain names stay plain text.

    python3 tests/stage_smoke.py        (needs: pip install playwright && playwright install chromium)
"""
import os
import sys

from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(os.path.dirname(HERE), "web")

FIXTURE = """<!doctype html><meta charset=utf-8>
<style>
 #host{position:relative;width:600px;height:400px}
 .node{position:absolute;width:100px;height:50px}
 #a{left:20px;top:20px}#b{left:250px;top:175px}#c{left:480px;top:330px}
 .pk{position:absolute;left:0;top:0;display:flex}
 svg.live-wires{position:absolute;inset:0;width:100%;height:100%}
</style>
<div id=host>
 <svg class=live-wires></svg><div class=live-packets></div>
 <div class=node id=a data-node=a>a</div><div class=node id=b data-node=b>b</div><div class=node id=c data-node=c>c</div>
</div>"""

failures = []


def expect(cond, msg):
    print(("ok    " if cond else "FAIL  ") + msg)
    if not cond:
        failures.append(msg)


with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 700, "height": 500})
    page.set_default_timeout(15000)             # a stuck animation fails the test instead of hanging it
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.set_content(FIXTURE)
    page.add_script_tag(path=os.path.join(WEB, "pb-core.js"))
    page.add_script_tag(path=os.path.join(WEB, "pb-live.js"))
    page.evaluate("""() => {
      window.log = [];
      window.stage = new PBLive.Stage(document.getElementById('host'),
        { wires: [{ id: 'ab', from: 'a', to: 'b' }, { id: 'bc', from: 'b', to: 'c' }], poolSize: 3 });
      window.stage.layout();
    }""")

    d = page.evaluate("() => [...document.querySelectorAll('path.wire-base')].map(p => p.getAttribute('d'))")
    expect(len(d) == 2 and all(x.startswith("M") for x in d), "each wire is drawn as a curve between its two nodes (%s)" % d)
    expect(page.evaluate("() => document.querySelector('svg.live-wires').getAttribute('viewBox')") == "0 0 600 400", "the SVG matches the stage size")

    page.evaluate("() => { document.getElementById('a').style.left = '300px'; window.stage.layout(); }")
    d2 = page.evaluate("() => document.querySelector('path.wire-base').getAttribute('d')")
    expect(d2 != d[0], "moving a node and calling layout() redraws its wire")
    page.evaluate("() => { document.getElementById('c').style.display = 'none'; window.stage.layout(); }")
    expect(page.evaluate("() => document.querySelector('[data-wire=bc]').getAttribute('display')") == "none",
           "a wire to a node that is not shown is hidden")
    page.evaluate("() => { document.getElementById('c').style.display = ''; window.stage.layout(); }")

    # a packet runs its legs in order, callbacks fire, and it frees its element afterwards
    page.evaluate("""() => new Promise(res => {
      window.stage.send({ label: 'YouTube', badge: 'YT', color: '#E62117', tone: 'is-allowed',
        route: [{ wire: 'ab', ms: 120 }, { hold: 60 }, { wire: 'bc', ms: 120 }],
        onStep: (i, pk) => { window.log.push('step' + i); if (i === 0) pk.tone('is-blocked'); },
        onDone: () => { window.log.push('done'); res(); } });
    })""")
    expect(page.evaluate("() => window.log.join(',')") == "step0,step1,step2,done", "legs finish in order, then onDone")
    expect(page.evaluate("() => [...document.querySelectorAll('.pk')].every(p => p.hidden)"), "a finished packet is hidden and free again")

    # the pool is fixed: with more packets than elements, the extra ones run their callbacks without being animated
    res = page.evaluate("""() => {
      const out = { animated: 0, instant: 0, maxVisible: 0 };
      for (let i = 0; i < 8; i++) {
        const ok = window.stage.send({ label: 'p' + i, route: [{ wire: 'ab', ms: 400 }], onStep: () => {} });
        ok ? out.animated++ : out.instant++;
        out.maxVisible = Math.max(out.maxVisible, [...document.querySelectorAll('.pk')].filter(p => !p.hidden).length);
      }
      return out;
    }""")
    expect(res["animated"] == 3 and res["instant"] == 5, "only poolSize packets animate at once (%s)" % res)
    expect(res["maxVisible"] <= 3, "never more packets on screen than the pool")
    page.wait_for_timeout(900)
    expect(page.evaluate("() => [...document.querySelectorAll('.pk')].every(p => p.hidden)"), "all of them clean up")

    # hostile text stays text
    page.evaluate("() => window.stage.send({ label: '<img src=x onerror=window.pwned=1>', badge: '<b>', route: [{ wire: 'ab', ms: 100 }] })")
    page.wait_for_timeout(400)
    expect(page.evaluate("() => document.querySelectorAll('.live-packets img, .live-packets b').length") == 0
           and not page.evaluate("() => window.pwned"), "a hostile domain name is shown as plain text")

    # pause: nothing moves, callbacks still run
    page.evaluate("() => { window.log = []; window.stage.pause(); }")
    moved = page.evaluate("() => window.stage.send({ route: [{ wire: 'ab', ms: 50 }, { wire: 'bc', ms: 50 }], onStep: i => window.log.push(i), onDone: () => window.log.push('d') })")
    expect(moved is False and page.evaluate("() => window.log.join(',')") == "0,1,d", "while paused nothing animates but the callbacks run")
    page.evaluate("() => window.stage.resume()")

    # destroy takes the drawing with it: after signing out and in, a new Stage on the same host starts clean
    out = page.evaluate("""() => {
      const host = document.getElementById('host');
      window.stage.destroy();
      const after = [host.querySelectorAll('g.wire').length, host.querySelectorAll('.pk').length];
      const s2 = new PBLive.Stage(host, { wires: [{ id: 'ab', from: 'a', to: 'b' }], poolSize: 2 });
      s2.layout();
      const n = document.createElement('span'); PBLive.tickNumber(n, 1, 5.3, 0, x => x.toFixed(1));
      return { after, again: [host.querySelectorAll('g.wire').length, host.querySelectorAll('.pk').length], exact: n.textContent };
    }""")
    expect(out["after"] == [0, 0], "destroy() removes the wires and packets it drew (%s)" % out["after"])
    expect(out["again"] == [1, 2], "and a new Stage on the same host starts from empty layers (%s)" % out["again"])
    expect(out["exact"] == "5.3", "a number with no animation time shows its exact value")

    # reduced motion: same, from the media query
    page.emulate_media(reduced_motion="reduce")
    page.reload()
    page.set_content(FIXTURE)
    page.add_script_tag(path=os.path.join(WEB, "pb-core.js"))
    page.add_script_tag(path=os.path.join(WEB, "pb-live.js"))
    out = page.evaluate("""() => {
      const s = new PBLive.Stage(document.getElementById('host'), { wires: [{ id: 'ab', from: 'a', to: 'b' }] });
      s.layout(); const log = [];
      const animated = s.send({ route: [{ wire: 'ab', ms: 50 }], onStep: i => log.push(i), onDone: () => log.push('d') });
      const n = document.createElement('span'); PBLive.tickNumber(n, 1, 1000, 500, x => String(x));
      return { animated, log: log.join(','), number: n.textContent };
    }""")
    expect(out["animated"] is False and out["log"] == "0,d", "with prefers-reduced-motion nothing moves but every step still runs")
    expect(out["number"] == "1000", "numbers jump straight to their value")
    expect(not errors, "no console or page errors %s" % (errors or ""))
    browser.close()

print("\n%d failure(s)" % len(failures))
sys.exit(1 if failures else 0)
