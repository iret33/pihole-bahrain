"""The page's strings, read out of the source files, for the copy test and the browser tests.

web/app.js keeps the live picture's words in `var LIVE_STR = {...}` and the first-run words in `var FIRST_STR = {...}`; web/pb-box.js
exports the My box sheet's as `strings`. Each is evaluated by node on its own literal, so nothing here needs a browser.
"""
import ast
import json
import os
import re
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(name):
    with open(os.path.join(ROOT, "web", name), encoding="utf-8") as f:
        return f.read()


def _literal(source, name, end_marker):
    start = source.index("var %s = {" % name)
    end = source.index(end_marker, start)
    code = source[start:end].replace("var %s =" % name, "const %s =" % name) + "\nconsole.log(JSON.stringify(%s));" % name
    return json.loads(subprocess.run(["node", "-e", code], capture_output=True, text=True, check=True).stdout)


def live_strings():
    return _literal(read("app.js"), "LIVE_STR", "Object.keys(LIVE_STR.en)")


def first_strings():
    return _literal(read("app.js"), "FIRST_STR", "Object.keys(FIRST_STR.en)")


def box_strings():
    code = "console.log(JSON.stringify(require(%s).strings))" % json.dumps(os.path.join(ROOT, "web", "pb-box.js"))
    return json.loads(subprocess.run(["node", "-e", code], capture_output=True, text=True, check=True).stdout)


def app_strings():
    """The page's own STR table up to the point where the others are merged in (the sign-in, devices, bedtime words ...)."""
    src = read("app.js")
    start = src.index("var STR = {")
    end = src.index("var LIVE_STR = {", start)
    code = src[start:end].replace("var STR =", "const STR =") + "\nconsole.log(JSON.stringify(STR));"
    return json.loads(subprocess.run(["node", "-e", code], capture_output=True, text=True, check=True).stdout)


def brand():
    """The names and taglines the brand tool (tools/make-brand.py) draws: NAME_EN, NAME_AR, TAGLINE_EN, TAGLINE_AR. Read from its source, so
    the page, the README, the site and the pictures can be compared with the one place that decides them (the tool needs an imaging library
    this does not)."""
    with open(os.path.join(ROOT, "tools", "make-brand.py"), encoding="utf-8") as f:
        source = f.read()
    out = {}
    for name in ("NAME_EN", "NAME_AR", "TAGLINE_EN", "TAGLINE_AR"):
        m = re.search(r"^%s = (\"[^\"\n]+\")$" % name, source, re.M)
        if not m:
            raise ValueError("tools/make-brand.py no longer defines %s as a plain string" % name)
        out[name] = ast.literal_eval(m.group(1))
    return out


def fmt(text, **values):
    for k, v in values.items():
        text = text.replace("{%s}" % k, str(v))
    return text
