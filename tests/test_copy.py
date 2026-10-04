"""The live picture's words (LIVE_STR in web/app.js): complete in both languages, and plain for parents.

The spec for the picture bans technical words from the default view: DNS, query, request, upstream, gravity, resolver, packet,
latency, Pi-hole, cache. They may appear only in the "How it works" sheet (keys lvDetailTitle ... lvTopEmpty), each explained.
"""
import json
import os
import re
import subprocess
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
def read(name):
    with open(os.path.join(ROOT, "web", name), encoding="utf-8") as f:
        return f.read()


APP = read("app.js")

BANNED = re.compile(r"\b(dns|queries|query|requests?|upstream|gravity|resolver|packets?|latency|pi-?hole|cache)\b", re.I)
# keys that belong to the detail sheet, where the real words are explained
DETAIL = re.compile(r"^lv(DetailTitle|HowTitle|How\d+T?|NumbersTitle|Hours|TopTitle|Fact\w+|CannotTitle|Cannot\d|WordsTitle|Word\d+T?|Loading|Unavailable|Other|TopEmpty)$")


def live_strings():
    start = APP.index("var LIVE_STR = {")
    end = APP.index("Object.keys(LIVE_STR.en)")
    code = APP[start:end].replace("var LIVE_STR =", "const LIVE_STR =") + "\nconsole.log(JSON.stringify(LIVE_STR));"
    out = subprocess.run(["node", "-e", code], capture_output=True, text=True, check=True).stdout
    return json.loads(out)


class LiveCopy(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s = live_strings()

    def test_same_keys_in_both_languages(self):
        en, ar = set(self.s["en"]), set(self.s["ar"])
        self.assertEqual(en - ar, set(), "missing in Arabic")
        self.assertEqual(ar - en, set(), "missing in English")

    def test_every_key_the_picture_uses_exists(self):
        src = read("pb-picture.js")
        used = set(re.findall(r"\bt\('(lv\w+)'", src)) | set(re.findall(r"'(lv\w+)'", src))
        dynamic = {"lvEx_" + k for k in ("active", "quiet", "paused", "offline", "overridden", "unreachable", "unknown")}
        missing = {k for k in used if k not in self.s["en"] and k not in ("lvEx_",)} - {"lvEx_"}
        self.assertEqual(missing, set(), "used by pb-picture.js but not defined")
        for k in dynamic:
            self.assertIn(k, self.s["en"])
        for step in range(1, 6):
            self.assertIn("lvTour%d" % step, self.s["en"])

    def test_placeholders_match(self):
        for key, en in self.s["en"].items():
            ar = self.s["ar"][key]
            self.assertEqual(sorted(re.findall(r"\{(\w+)\}", en)), sorted(re.findall(r"\{(\w+)\}", ar)), key)

    def test_nothing_empty_and_arabic_is_arabic(self):
        for lang in ("en", "ar"):
            for key, text in self.s[lang].items():
                self.assertTrue(text.strip(), "%s/%s is empty" % (lang, key))
        latin_only = [k for k, v in self.s["ar"].items() if not re.search(r"[؀-ۿ]", v) and k not in ("lvWord1T", "lvWord2T")]
        self.assertEqual(latin_only, [], "Arabic strings with no Arabic in them")

    def test_default_view_has_no_jargon(self):
        for lang in ("en",):
            for key, text in self.s[lang].items():
                if DETAIL.match(key):
                    continue
                self.assertIsNone(BANNED.search(text), "%s uses a technical word: %r" % (key, text))

    def test_the_box_has_one_name_in_arabic(self):
        for key, text in self.s["ar"].items():
            self.assertNotIn("جهاز العائلة", text, "%s: the box is صندوق العائلة, a device is جهاز" % key)

    def test_no_overclaiming(self):
        for lang in ("en", "ar"):
            for key, text in self.s[lang].items():
                self.assertNotRegex(text, r"(?i)\b(protected|secure|safe from)\b|محمي|مؤمَّن", "%s overclaims" % key)
        self.assertNotIn("Everything is allowed", self.s["en"]["lvFreeSub"])


if __name__ == "__main__":
    unittest.main()
