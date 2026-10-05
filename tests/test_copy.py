"""The page's words: complete in both languages, with the same placeholders, and plain for parents.

Three groups of strings are checked, all read out of the source by tests/pbstrings.py:

* the live picture (LIVE_STR in web/app.js): the spec bans technical words from the default view: DNS, query, request, upstream,
  gravity, resolver, packet, latency, Pi-hole, cache. They may appear only in the "How it works" sheet (keys lvDetailTitle ...
  lvTopEmpty), each explained;
* the first run (FIRST_STR in web/app.js): the claim screen, the setup list, the update banner;
* the My box sheet (BOX_STR in web/pb-box.js).

The last two follow the same rule with no "How it works" sheet to hide behind: no technical word anywhere. The unavoidable exceptions
are listed here, one by one, with the reason.
"""
import re
import unittest

import pbstrings

APP = pbstrings.read("app.js")
BOX_JS = pbstrings.read("pb-box.js")
INDEX = pbstrings.read("index.html")

BANNED = re.compile(r"\b(dns|queries|query|requests?|upstream|gravity|resolver|packets?|latency|pi-?hole|cache)\b", re.I)
# keys that belong to the detail sheet, where the real words are explained
DETAIL = re.compile(r"^lv(DetailTitle|HowTitle|How\d+T?|NumbersTitle|Hours|TopTitle|Fact\w+|CannotTitle|Cannot\d|WordsTitle|Word\d+T?|Loading|Unavailable|Other|TopEmpty)$")

# More words a parent should never have to read on the first-run and My box screens: addresses and protocols, programs, file formats,
# commands. (The licence's name, GPL-3.0-or-later, and the example "Orange Pi Zero 3" are not on the list: they are names.)
JARGON = re.compile(r"\b(ip|mac|dhcp|ssh|sudo|systemctl|api|json|hash|teleporter|mdns|systemd|daemon|kernel|checksum|zip|sid|command|terminal|"
                    r"firmware|reboot|localhost|https?|url)\b", re.I)

# The unavoidable exceptions to BANNED, by key. Anything else that names Pi-hole is a bug.
BANNED_EXCEPTIONS = {
    "boxAboutTrademark": "the trademark notice has to name Pi-hole (and the company that owns the name)",
    "boxAboutPihole": "About says which Pi-hole version the box runs (what a seller or a helper checks against the program's own report)",
}

OVERCLAIM = r"(?i)\b(protected|secure|safe from)\b|محمي|مؤمَّن"
LATIN_ONLY_OK = {"lvWord1T", "lvWord2T"}          # 'Pi-hole' and 'DNS' as headings of the explained words

ARABIC = re.compile(r"[؀-ۿ]")
INDIC_DIGITS = re.compile("[٠-٩۰-۹]")


def t_calls(source):
    """The text between the parentheses of every t(...) call in the source (balanced, so a ternary inside is included)."""
    out = []
    for m in re.finditer(r"(?<![\w.])t\(", source):
        depth, i = 1, m.end()
        while i < len(source) and depth:
            depth += {"(": 1, ")": -1}.get(source[i], 0)
            i += 1
        out.append(source[m.end():i - 1])
    return out


def keys_used(source):
    """Keys the source asks for: the quoted names inside t(...) (also in a ternary) and data-i18n="key"."""
    used = set(re.findall(r'data-i18n="(\w+)"', source))
    for call in t_calls(source):
        # a quoted name at the start of an argument, or after ? or : (a ternary choosing between keys)
        used |= set(re.findall(r"""(?:^|[?:]\s*)['"]([A-Za-z]\w*)['"]""", call.strip()))
    return used


def quoted(source):
    """Every quoted word in the source: a string key chosen in one place and passed to t() somewhere else is among them."""
    return set(re.findall(r"""['"](\w+)['"]""", source))


class Copy:
    """Rules every group of strings has to follow; subclasses name the group in `strings`."""
    strings = None
    banned_outside = (BANNED,)

    def table(self):
        return type(self).strings

    def test_same_keys_in_both_languages(self):
        s = self.table()
        en, ar = set(s["en"]), set(s["ar"])
        self.assertEqual(en - ar, set(), "missing in Arabic")
        self.assertEqual(ar - en, set(), "missing in English")

    def test_placeholders_match(self):
        s = self.table()
        for key, en in s["en"].items():
            self.assertEqual(sorted(re.findall(r"\{(\w+)\}", en)), sorted(re.findall(r"\{(\w+)\}", s["ar"][key])), key)

    def test_nothing_empty_arabic_is_arabic_and_digits_are_latin(self):
        s = self.table()
        for lang in ("en", "ar"):
            for key, text in s[lang].items():
                self.assertTrue(text.strip(), "%s/%s is empty" % (lang, key))
        latin_only = [k for k, v in s["ar"].items() if not ARABIC.search(v) and k not in LATIN_ONLY_OK]
        self.assertEqual(latin_only, [], "Arabic strings with no Arabic in them")
        for key, text in s["ar"].items():
            self.assertIsNone(INDIC_DIGITS.search(text), "%s: the page writes numbers with Latin digits, also in Arabic" % key)

    def test_the_box_has_one_name_in_arabic(self):
        for key, text in self.table()["ar"].items():
            self.assertNotIn("جهاز العائلة", text, "%s: the box is صندوق العائلة, a device is جهاز" % key)

    def test_no_overclaiming(self):
        for lang in ("en", "ar"):
            for key, text in self.table()[lang].items():
                self.assertNotRegex(text, OVERCLAIM, "%s overclaims" % key)


class LiveCopy(Copy, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.strings = pbstrings.live_strings()
        cls.s = cls.strings

    def test_every_key_the_picture_uses_exists(self):
        src = pbstrings.read("pb-picture.js")
        used = set(re.findall(r"\bt\('(lv\w+)'", src)) | set(re.findall(r"'(lv\w+)'", src))
        dynamic = {"lvEx_" + k for k in ("active", "quiet", "paused", "offline", "overridden", "unreachable", "unknown")}
        missing = {k for k in used if k not in self.s["en"] and k not in ("lvEx_",)} - {"lvEx_"}
        self.assertEqual(missing, set(), "used by pb-picture.js but not defined")
        for k in dynamic:
            self.assertIn(k, self.s["en"])
        for step in range(1, 6):
            self.assertIn("lvTour%d" % step, self.s["en"])

    def test_default_view_has_no_jargon(self):
        for key, text in self.s["en"].items():
            if DETAIL.match(key):
                continue
            self.assertIsNone(BANNED.search(text), "%s uses a technical word: %r" % (key, text))

    def test_no_overclaiming_in_the_free_time_line(self):
        self.assertNotIn("Everything is allowed", self.s["en"]["lvFreeSub"])


class FirstRunCopy(Copy, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.strings = pbstrings.first_strings()

    def test_no_technical_words(self):
        for key, text in self.table()["en"].items():
            self.assertIsNone(BANNED.search(text), "%s uses a technical word: %r" % (key, text))
            self.assertIsNone(JARGON.search(text), "%s uses jargon: %r" % (key, text))

    def test_every_string_is_used(self):
        used = quoted(APP) | keys_used(INDEX)
        self.assertEqual(set(self.table()["en"]) - used, set(), "defined but never used")

    def test_the_names(self):
        # One tagline per language everywhere (README, site, social preview, page): the brand tool's constants are the source.
        s = self.table()
        brand = pbstrings.brand()
        self.assertEqual(s["en"]["tagline"], brand["TAGLINE_EN"])
        self.assertEqual(s["ar"]["tagline"], brand["TAGLINE_AR"])
        self.assertEqual(brand["TAGLINE_EN"], "Calm internet for the family")
        self.assertEqual(brand["TAGLINE_AR"], "إنترنت هادئ للعائلة")

    def test_the_login_and_claim_screens_say_the_same_as_the_string_table(self):
        # index.html carries the English text before the script runs; it must be the table's own words, and no old name may be left.
        found = re.findall(r'data-i18n="tagline">([^<]*)</p>', INDEX)
        self.assertEqual(len(found), 2, "the sign-in and the claim screen")
        for text in found:
            self.assertEqual(text, pbstrings.brand()["TAGLINE_EN"])
        for old in ("Family internet", "إنترنت العائلة"):
            self.assertNotIn(old, INDEX + APP + BOX_JS, "the old tagline is gone")


class BoxCopy(Copy, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.strings = pbstrings.box_strings()

    def test_no_technical_words_except_the_listed_ones(self):
        for key, text in self.table()["en"].items():
            if key in BANNED_EXCEPTIONS:
                continue
            self.assertIsNone(BANNED.search(text), "%s uses a technical word: %r" % (key, text))
            self.assertIsNone(JARGON.search(text), "%s uses jargon: %r" % (key, text))

    def test_the_exceptions_are_real_and_the_only_places_that_name_pi_hole(self):
        s = self.table()
        for key in BANNED_EXCEPTIONS:
            self.assertIn(key, s["en"])
            self.assertTrue(BANNED.search(s["en"][key]), "%s no longer needs its exception: remove it from BANNED_EXCEPTIONS" % key)
        naming = {k for k, v in s["en"].items() if re.search(r"pi-?hole", v, re.I)} | {k for k, v in s["ar"].items() if re.search(r"pi-?hole", v, re.I)}
        self.assertEqual(naming, set(BANNED_EXCEPTIONS), "Pi-hole is named only where the trademark notice needs it")

    def test_the_trademark_notice_says_what_the_project_requires(self):
        s = self.table()
        self.assertEqual(s["en"]["boxAboutTrademark"], "Pi-hole is a trademark of Pi-hole LLC. Sinko is independent software that works with it.")
        self.assertIn("Pi-hole LLC", s["ar"]["boxAboutTrademark"])

    def test_every_string_is_defined_and_used(self):
        s = self.table()
        # a key is used when its name appears in quotes anywhere (the string tables spell their keys without quotes)
        used = quoted(BOX_JS) | quoted(APP) | keys_used(INDEX)
        defined = set(s["en"])
        asked = keys_used(BOX_JS)
        app = pbstrings.app_strings()
        # what the sheet asks for must exist, in the sheet's own table or the page's (sign-in and error wording)
        self.assertEqual({k for k in asked if k not in defined and k not in app["en"]}, set(), "used by pb-box.js but not defined")
        for k in asked - defined:
            self.assertIn(k, app["ar"], "%s is missing in Arabic" % k)
        self.assertEqual(defined - used, set(), "defined but never used")

    def test_the_counter_description_is_precise(self):
        en = self.table()["en"]
        text = " ".join(en[k] for k in en if k.startswith("boxCounter"))
        for must in ("random code", "Sinko version", "kind of box", "country", "does not store the internet address", "never sent",
                     "names of children", "passwords", "language", "time zone", "Optional", "stays off until you switch it on"):
            self.assertIn(must.lower(), text.lower(), "the counter text must say: %s" % must)
        ar = " ".join(self.table()["ar"][k] for k in self.table()["ar"] if k.startswith("boxCounter"))
        for must in ("رمز عشوائي", "الدولة", "عنوان الإنترنت", "كلمات المرور", "اللغة", "المنطقة الزمنية", "اختياري"):
            self.assertIn(must, ar, "the Arabic counter text must say: %s" % must)

    def test_the_online_line_has_its_own_words_for_two_and_never_reads_one_of_one(self):
        s = self.table()
        for lang in ("en", "ar"):
            self.assertIn("{n}", s[lang]["boxCounterOnline"], lang)
            self.assertNotIn("{n}", s[lang]["boxCounterOnlineTwo"], "%s: exactly two is said without a number" % lang)
        # the Arabic used to stack two 'من' in a row around the number
        self.assertNotRegex(s["ar"]["boxCounterOnline"], r"من\s*\{n\}\s*من")
        self.assertRegex(s["en"]["boxCounterOnlineTwo"], r"(?i)one other")
        self.assertIn("متصلان", s["ar"]["boxCounterOnlineTwo"], "the dual: two boxes are متصلان")

    def test_about_names_the_pihole_version_with_the_version_in_it(self):
        s = self.table()
        for lang in ("en", "ar"):
            self.assertIn("{v}", s[lang]["boxAboutPihole"], lang)
            self.assertRegex(s[lang]["boxAboutPihole"], r"Pi-hole")
        self.assertEqual(s["en"]["boxAboutPihole"], "Pi-hole {v}")

    def test_a_backup_is_said_to_be_private_and_a_restore_to_leave_the_password_alone(self):
        en = self.table()["en"]
        self.assertIn("parent password", en["boxBackupPrivate"])
        self.assertRegex(en["boxBackupPrivate"], r"(?i)private")
        self.assertRegex(en["boxRestoreHint"], r"(?i)password .* stay")

    def test_power_confirmations_say_the_childrens_internet_stops(self):
        s = self.table()
        for key in ("boxRestartAsk", "boxShutdownAsk"):
            self.assertIn("children", s["en"][key])
            self.assertIn("الأطفال", s["ar"][key])

    def test_failure_text_says_the_previous_version_is_back_only_where_the_box_said_so(self):
        s = self.table()
        # boxFailedBack is shown for update.rolledBack === true only; the Arabic says "version" the way the rest of the sheet does (الإصدار).
        self.assertIn("previous version is back", s["en"]["boxFailedBack"])
        self.assertIn("الإصدار السابق", s["ar"]["boxFailedBack"])
        for key in ("boxFailedNotBack", "boxFailedUnknown"):
            self.assertNotIn("previous version is back", s["en"][key], key + " must not claim a recovery")
            self.assertNotIn("عاد الإصدار", s["ar"][key], key)
            self.assertIn("unplug the box, plug it back in", s["en"][key].lower(), key)
            self.assertIn("ask whoever set up the box", s["en"][key], key)
            self.assertIn("افصل الصندوق", s["ar"][key], key)
            self.assertIn("من أعدّ الصندوق", s["ar"][key], key)

    def test_the_technical_reason_is_never_part_of_the_parents_sentence(self):
        s = self.table()
        self.assertNotIn("boxFailedReason", s["en"], "update.error is English and technical: it goes in the collapsed details, not into a sentence")
        for lang in ("en", "ar"):
            for key, text in s[lang].items():
                if key.startswith("boxFailed"):
                    self.assertNotIn("{e}", text, "%s/%s prints the reason" % (lang, key))
        self.assertEqual(s["en"]["boxDetailsTitle"], "Details for whoever helps you")

    def test_nothing_said_while_an_update_runs_tells_a_parent_to_unplug_anything(self):
        s = self.table()
        for key in ("boxPowerWaitUpdate", "boxPowerDropped", "boxRunningNote", "boxStalled"):
            self.assertNotRegex(s["en"][key], r"(?i)unplug|plug it back", key)
            self.assertNotIn("افصل", s["ar"][key], key)
        self.assertIn("plugged in", s["en"]["boxRunningNote"])
        self.assertIn("update", s["en"]["boxPowerWaitUpdate"].lower())
        self.assertIn("never switched off in the middle", s["en"]["boxPowerWaitUpdate"])

    def test_switching_the_counter_off_says_the_box_asks_the_service_to_forget_it(self):
        s = self.table()
        for key in ("boxCounterForget", "boxCounterOffToast"):
            self.assertRegex(s["en"][key], r"(?i)asks? the counter service to forget", key)
            self.assertIn("خدمة العدّاد أن تنسى", s["ar"][key], key)
        first = pbstrings.first_strings()
        self.assertRegex(first["en"]["setupCounterText"], r"(?i)ask(s)? the counter service to forget")
        self.assertIn("تنسى", first["ar"]["setupCounterText"])

    def test_the_counter_card_says_what_the_counter_keeps_the_way_the_privacy_page_does(self):
        s = self.table()
        self.assertIn("first and last heard", s["en"]["boxCounterKept"])
        self.assertIn("أول رسالة وآخر رسالة", s["ar"]["boxCounterKept"])
        self.assertIn("the addresses of your devices", s["en"]["boxCounterNot"])
        self.assertIn("عناوين أجهزتك", s["ar"]["boxCounterNot"])
        self.assertIn("country", pbstrings.first_strings()["en"]["setupCounterText"])

    def test_the_time_zone_notes_are_honest_and_offer_no_fix_the_page_cannot_give(self):
        s = self.table()
        for lang in ("en", "ar"):
            for key in ("boxClockZoneKnown", "boxClockZoneUtc", "boxClockZoneDiffers"):
                self.assertNotRegex(s[lang][key], r"(?i)timedatectl|sudo|systemctl", key)
        self.assertIn("{z}", s["en"]["boxClockZoneKnown"])
        self.assertIn("UTC", s["en"]["boxClockZoneUtc"])
        self.assertIn("UTC", s["ar"]["boxClockZoneUtc"])
        self.assertNotRegex(s["en"]["boxClockZoneUtc"], r"(?i)ask whoever|change the time zone|set the time zone")

    def test_the_address_rows_say_what_each_is_for(self):
        en = self.table()["en"]
        self.assertEqual(en["boxNetIp"], "Address for your router")
        self.assertEqual(len({en["boxNetIp"], en["boxNetName"], en["boxNetLocal"]}), 3)
        self.assertNotIn("Easy name", en.values())

    def test_the_arabic_sheet_calls_a_version_a_version_and_a_backup_a_backup(self):
        ar = self.table()["ar"]
        for key, text in ar.items():
            self.assertNotIn("النسخة محدّثة", text, key)
            self.assertNotIn("النسخة السابقة", text, key)
            self.assertNotIn("تم تنزيل النسخة.", text, key)
        self.assertIn("الاحتياطية", ar["boxBackupDone"])
        self.assertNotEqual(ar["boxAboutTitle"], "حول")


class NoCommandsForParents(unittest.TestCase):
    """The page is for people who cannot SSH into the box: nothing it says may ask for a command."""

    def test_the_page_messages_do_not_ask_for_a_command(self):
        s = pbstrings.app_strings()
        for lang in ("en", "ar"):
            for key in ("schedulerDown", "notInstalled"):
                text = s[lang][key]
                self.assertNotRegex(text, r"(?i)\b(sudo|systemctl|run:|ssh|terminal)\b", "%s/%s asks for a command" % (lang, key))
                self.assertNotIn("sinko ", text.lower().replace("سينكو", ""), "%s/%s names a command" % (lang, key))

    def test_the_router_help_never_puts_a_name_where_a_number_is_needed(self):
        s = pbstrings.app_strings()
        for lang in ("en", "ar"):
            self.assertIn("{ip}", s[lang]["helpBody"])
            no_number = s[lang]["helpBodyNoIp"]
            self.assertNotIn("{ip}", no_number)
            self.assertNotRegex(no_number, r"(?i)\.local\b|\.lan\b|family\.|sinko\.", "%s: no host name in the router sentence" % lang)
            self.assertNotRegex(no_number, r"\d+\.\d+\.\d+\.\d+", "%s: no example number: a parent could type it into the router" % lang)
        self.assertIn("number address", s["en"]["helpBodyNoIp"])
        self.assertIn("العنوان الرقمي", s["ar"]["helpBodyNoIp"])
        self.assertEqual(sorted(s["en"]), sorted(s["ar"]))

    def test_the_remedy_for_a_stuck_box_is_unplugging_it(self):
        s = pbstrings.app_strings()
        self.assertIn("Unplug the box", s["en"]["schedulerDown"])
        self.assertIn("افصل الصندوق", s["ar"]["schedulerDown"])

    def test_the_app_name(self):
        s = pbstrings.app_strings()
        self.assertEqual(s["en"]["appName"], "Sinko")
        self.assertEqual(s["ar"]["appName"], "سينكو")


if __name__ == "__main__":
    unittest.main()
