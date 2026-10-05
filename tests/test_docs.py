"""The documents make promises to parents, sellers and maintainers. Most of them can only be checked by reading the
code, but a few can be kept by test: the sentences that were once wrong must not come back, the privacy statement says
the hard thing in its first lines, every shell command is marked "own install" where a ready-made box has no shell, and
the links between the documents lead somewhere."""
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Customer-facing and maintainer-facing text that this project's documentation owner writes.
DOCS = [
    "docs/faq.md", "docs/privacy.md", "docs/selling.md", "docs/troubleshooting.md", "docs/updating.md",
    "docs/product-image.md", "docs/quick-start-card.md", "docs/hardware-test-checklist.md",
    "docs/maintainers/releasing.md", "SECURITY.md", "CONTRIBUTING.md", "NOTICE", "COPYING.md", "TRADEMARK.md",
    "telemetry/README.md", "site/README.md", "site/strings.js", "site/index.html", ".github/ISSUE_TEMPLATE/bug_report.yml",
    "README.md", "docs/install.md", "docs/how-it-works.md", "docs/img/README.md", "docs/maintainers/architecture.md",
]


def read(path):
    with open(os.path.join(ROOT, path), encoding="utf-8") as fh:
        return fh.read()


def flat(text):
    """Line breaks in the source are not part of a sentence."""
    return " ".join(text.split())


def english(text):
    """privacy.md has both languages in one file: the English part is everything before the Arabic anchor."""
    return text.split('<a id="arabic"></a>')[0]


def arabic(text):
    return text.split('<a id="arabic"></a>')[1]


class WhatMustNotBeSaidAgain(unittest.TestCase):
    # Each of these was written once and is false: what a family looks up does leave the box (Pi-hole passes it to its
    # upstream DNS service), a ready-made box has no login, and the installer sets 30 days only on a Pi-hole it installs.
    BANNED = [
        r"nothing leaves the box",
        r"never leaves? your box",
        r"what never leaves",
        r"stays on the box in your home",
        r"sends nothing about children, devices, websites",
        r"installer keeps 30 days",
        r"Asking for your record to be deleted",
        r"DEFAULT_TELEMETRY_URL",
        r"output is safe to share",
        r"nothing else is collected",
        r"continue to offer the newest release",
        r"enable it for the session",
        r"cached updates",
        r"filesystem grows to fill",
        r"إنترنت هادئ لعائلتك",
        r"You are one of N",                    # the page never said this; it says "This box is one of N ...", and nothing below two boxes
        r"أنت ضمن",
        r"(?<!about )once a day, and whenever you tap",     # the check also runs after every start and again after a failed one
        r"مرة كل يوم، وكلما ضغطت",
        r"never retries a version that failed for a week",   # a failure of the network or the disk is tried again the same night
        r"the release API and the two files",    # an update also refreshes the lists and fetches guard.txt: see TheUpdateTrafficIsDescribedCompletely
    ]

    def test_the_old_false_sentences_are_gone(self):
        for path in DOCS:
            for number, line in enumerate(read(path).splitlines(), 1):
                if re.search(r"do \*\*not\*\* write|do not write", line, re.I):
                    continue                                    # selling.md quotes what a seller must not write
                for pattern in self.BANNED:
                    self.assertIsNone(re.search(pattern, line, re.I), "%s:%d says %r: %s" % (path, number, pattern, line.strip()))


class ThePrivacyStatementSaysTheHardThingFirst(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = read("docs/privacy.md")

    def test_the_first_paragraph_names_the_upstream_service_and_what_it_sees(self):
        first = flat(english(self.text).split("###")[0])
        self.assertIn("upstream DNS service", first)
        self.assertIn("Cloudflare for Families", first)
        self.assertIn("names of those sites", first)
        self.assertIn("internet address", first)

    def test_the_arabic_summary_says_it_too(self):
        first = flat(arabic(self.text).split("###")[0])
        for needle in ("DNS", "Cloudflare for Families", "عنوان إنترنت"):
            self.assertIn(needle, first)

    def test_both_languages_have_the_lookups_section_and_it_is_linked(self):
        for part, anchor in ((english(self.text), "lookups"), (arabic(self.text), "lookups-ar")):
            self.assertIn('<a id="%s"></a>' % anchor, part)
            self.assertIn("](#%s)" % anchor, part)

    def test_the_lookups_section_is_precise_about_defaults_and_retention(self):
        section = english(self.text).split('<a id="lookups"></a>')[1].split("### What Sinko never sends")[0]
        for needle in ("1.1.1.3", "1.0.0.3", "was already installed keeps", "30 days", "91 days", "unencrypted", "never receives"):
            self.assertIn(needle, section)

    def test_the_heading_is_about_what_sinko_sends_not_about_the_box(self):
        self.assertIn("### What Sinko never sends to the project", self.text)
        self.assertIn("### ما لا يرسله سينكو إلى المشروع أبدًا", self.text)

    def test_switching_off_deletes_the_record_and_needs_no_shell(self):
        eng, ara = english(self.text), arabic(self.text)
        self.assertIn("forget this code", eng)
        self.assertIn("انسَ هذا الرمز", ara)
        for part in (eng, ara):
            self.assertIn("6", part)                            # asks again about every 6 hours
        self.assertIn("This is the way on a ready-made box", eng)
        self.assertIn("وهذه هي الطريقة على الصندوق الجاهز", ara)

    def test_the_counter_is_asked_in_four_places(self):
        self.assertIn("one of four places", english(self.text))
        self.assertIn("في أربعة أماكن", arabic(self.text))

    def test_the_arabic_heading_for_how_long_reads_naturally(self):
        self.assertIn("**مدة الاحتفاظ.**", self.text)
        self.assertNotIn("كم تُحفظ", self.text)


class EveryShellCommandIsMarkedForAnOwnInstall(unittest.TestCase):
    CUSTOMER_FACING = ["docs/faq.md", "docs/privacy.md", "docs/troubleshooting.md", "docs/updating.md"]

    def test_the_top_of_each_says_that_sudo_commands_are_for_an_own_install_and_a_ready_made_box_has_no_login(self):
        for path in self.CUSTOMER_FACING:
            top = flat("\n".join(read(path).splitlines()[:28])).lower()
            self.assertIn("sudo", top, path)
            self.assertTrue("own install" in top or "built their own box" in top, path)
            self.assertIn("ready-made box", top, path)
            self.assertTrue("no login" in top or "has no login" in top or "cannot be logged in" in top, path)

    def test_troubleshooting_has_a_section_for_the_ready_made_box(self):
        text = read("docs/troubleshooting.md")
        self.assertIn("## Ready-made box (no login)", text)
        section = text.split("## Ready-made box (no login)")[1].split("\n## ")[0]
        for needle in ("power cable", "router", "Re-flash", "Backup", "seller"):
            self.assertIn(needle, section)
        self.assertNotIn("sudo ", section.replace("sudo` ", ""))     # the section is about what needs no shell


class UpdatingTellsTheTruthAboutPinsAndEndings(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = read("docs/updating.md")

    def test_ref_pins_and_how_to_follow_releases_again(self):
        for needle in ("--ref v3.0.1", "pins the box", "SINKO_REF", "--ref latest", "--force"):
            self.assertIn(needle, self.text)

    def test_three_endings_and_when_the_previous_version_is_back(self):
        for needle in ("It failed and the previous version is back", "could not be put back", "about three"):
            self.assertIn(needle, self.text)

    def test_pihole_on_a_ready_made_box_is_updated_by_reflashing(self):
        self.assertIn("updated by re-flashing a newer Sinko image", self.text)

    def test_the_checksum_is_not_presented_as_authentication(self):
        self.assertIn("integrity", read("SECURITY.md"))
        self.assertIn("not against someone who controls", self.text)


class TheImageGuideMatchesTheSealAndThePolicy(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = read("docs/product-image.md")

    def test_what_the_seal_does_and_does_not(self):
        for needle in ("--no-zerofill", "/var/log.hdd", "Wi-Fi", "locks the root password and turns off SSH password logins last",
                       "/var/lib/sinko/cache", "no login", "answer **no**", "unset HISTFILE"):
            self.assertIn(needle, self.text)

    def test_the_acceptance_check_does_not_rely_on_the_default_password(self):
        self.assertIn("throw-away root password", self.text)
        self.assertIn("do **not** include `password`", self.text)

    def test_pihole_is_updated_by_a_new_image_and_the_policy_is_rebuild_per_release(self):
        self.assertIn("Pi-hole on a ready-made box is not updated by anything except a newer", self.text)
        self.assertIn("Rebuild the golden unit for every Sinko release", self.text)
        self.assertIn("rebuild the image for every Sinko release", read("docs/selling.md"))

    def test_the_card_does_not_promise_the_whole_card_is_used(self):
        self.assertIn("not \"uses the whole card\"", self.text)
        self.assertNotIn("the filesystem grows", read("docs/selling.md"))


class TheSellersPrivacySentenceIsTrue(unittest.TestCase):
    def test_section_5_names_the_upstream_service_in_both_languages(self):
        section = read("docs/selling.md").split("## 5. Privacy promise to buyers")[1].split("## 6.")[0]
        self.assertIn("upstream DNS service", section)
        self.assertIn("Cloudflare for Families", section)
        self.assertIn("خدمة DNS خارجية", section)
        self.assertIn("deletes the box's record", section)
        self.assertIn("Do **not** write", section)


class TheCardKeepsTheLicenceOffer(unittest.TestCase):
    def test_the_three_year_offer_is_printed_in_both_languages_and_the_fields_are_named(self):
        card = read("docs/quick-start-card.md")
        self.assertEqual(card.count("at least three years"), 1)
        self.assertEqual(card.count("ثلاث سنوات على الأقل"), 1)
        self.assertNotIn("three `[...]` fields", card)
        self.assertIn("[YOUR PRODUCT NAME]", card)
        self.assertIn("[اسم منتجك]", card)


class TheBugTemplateDoesNotCallDoctorHarmless(unittest.TestCase):
    def test_it_warns_that_the_issue_is_public_and_what_doctor_can_contain(self):
        text = read(".github/ISSUE_TEMPLATE/bug_report.yml") + read("CONTRIBUTING.md") + read("docs/troubleshooting.md")
        self.assertIn("This issue is public", text)
        self.assertIn("children's names", text)
        self.assertIn("MAC or IP", text)


class TheCounterReadmeCoversForgetAndTheAbuseLimit(unittest.TestCase):
    def test_forget_is_documented_and_workers_dev_has_no_rate_limit(self):
        text = read("telemetry/README.md")
        for needle in ("/v1/forget", "{\"forgotten\": true}", "whether or not the id was known", "custom domain", "workers.dev", "zone"):
            self.assertIn(needle, text)


class TheLinksBetweenTheDocumentsLeadSomewhere(unittest.TestCase):
    LINK = re.compile(r"\]\(([^)\s#]+)(#[^)\s]*)?\)")

    def test_relative_links_point_at_files_that_exist(self):
        for path in [p for p in DOCS if p.endswith(".md")]:
            base = os.path.dirname(os.path.join(ROOT, path))
            for match in self.LINK.finditer(read(path)):
                target = match.group(1)
                if re.match(r"[a-z]+:", target):
                    continue
                self.assertTrue(os.path.exists(os.path.normpath(os.path.join(base, target))), "%s links to %s, which is not there" % (path, target))

    def test_links_inside_privacy_md_point_at_anchors_that_exist(self):
        text = read("docs/privacy.md")
        ids = set(re.findall(r'<a id="([^"]+)"', text))
        for anchor in re.findall(r"\]\(#([^)\s]+)\)", text):
            self.assertIn(anchor, ids, "docs/privacy.md links to #%s, which does not exist" % anchor)

    def test_the_selling_guide_names_the_sections_the_card_and_the_image_guide_point_to(self):
        selling = read("docs/selling.md")
        for heading in ("## 2. The software you are shipping", "## 4. Hardware and the shop", "## 5. Privacy promise to buyers",
                        "## 7. Keeping the boxes you sold safe"):
            self.assertIn(heading, selling)


class TheLicenceFilesAgree(unittest.TestCase):
    def test_the_plex_year_matches_the_font_licence_and_the_wordmark_credit_is_there(self):
        notice = read("NOTICE")
        first = read("web/fonts/OFL.txt").splitlines()[0]
        year = re.search(r"Copyright (\d{4}) IBM Corp", first).group(1)
        self.assertIn("Copyright %s IBM Corp" % year, notice)
        self.assertNotIn("(C) 2017 IBM", notice)
        for needle in ("docs/img/wordmark-en.svg", "docs/img/wordmark-ar.svg", "docs/img/social-preview.png"):
            self.assertIn(needle, notice)

    def test_lists_are_cc0_everywhere_including_the_catalogue(self):
        for path in ("NOTICE", "COPYING.md", "CONTRIBUTING.md"):
            self.assertIn("services.json", read(path), path)

    def test_the_marks_are_named_as_not_gpl_where_the_gpl_is_stated(self):
        for path in ("NOTICE", "COPYING.md"):
            text = read(path)
            self.assertIn("web/icon.svg", text, path)
            self.assertIn("docs/img", text, path)
        self.assertRegex(read("NOTICE"), r"Copyright \(C\) 2026 The Sinko contributors")

    def test_the_lawyer_note_is_not_in_the_published_policy(self):
        self.assertNotIn("lawyer", read("TRADEMARK.md").lower())
        self.assertIn("lawyer", read("docs/maintainers/releasing.md").lower())


class TheSecurityPolicyAdmitsWhoIsTrusted(unittest.TestCase):
    def test_it_says_what_the_checksum_is_and_is_not_and_that_nothing_is_signed(self):
        text = read("SECURITY.md")
        for needle in ("integrity, not origin", "Whoever controls this GitHub repository controls every box", "no signed releases",
                       "SINKO_REPO_SLUG", "Block lists are outside this chain", "tag ruleset", "no login of any kind"):
            self.assertIn(needle, text)


# --------------------------------------------------------------------------- what the code does, read from the code
def constant(path, name):
    """The integer that `NAME = <arithmetic>` gives in a source file. Only digits and * + - ( ) are evaluated."""
    match = re.search(r"(?<!\w)%s\s*=\s*([0-9][0-9 *+\-()_]*)" % re.escape(name), read(path))
    if not match:
        raise AssertionError("%s no longer defines %s as a plain number" % (path, name))
    return eval(match.group(1).strip(), {"__builtins__": {}}, {})       # noqa: S307 (digits and arithmetic signs only, from our own source)


def slug(heading):
    """The anchor GitHub makes of a heading: the text without its markup, lower case, punctuation gone, spaces to hyphens."""
    text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", heading)
    text = re.sub(r"[`*_~]|<[^>]+>", "", text)
    return re.sub(r"[^\w\- ]", "", text.strip().lower()).replace(" ", "-")


def anchors_of(path):
    text = read(path)
    found = set()
    in_code = False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            in_code = not in_code
        if not in_code and re.match(r"#{1,6}\s", line):
            found.add(slug(re.sub(r"^#+\s+", "", line)))
    found.update(re.findall(r'<a id="([^"]+)"', text))
    return found


class TheUpdateTrafficIsDescribedCompletely(unittest.TestCase):
    """privacy.md, the FAQ, the update guide and the hardware checklist give numbers; the numbers are the code's."""

    @classmethod
    def setUpClass(cls):
        cls.privacy = flat(read("docs/privacy.md"))
        cls.eng = flat(english(read("docs/privacy.md")))
        cls.ara = flat(arabic(read("docs/privacy.md")))
        cls.faq = flat(read("docs/faq.md"))
        cls.updating = flat(read("docs/updating.md"))
        cls.checklist = flat(read("docs/hardware-test-checklist.md"))

    def test_the_numbers_in_the_text_are_the_numbers_in_the_program(self):
        # If one of these changes, the sentences below (and their Arabic) are wrong: change them in the same commit.
        self.assertEqual(constant("bin/sinko", "CHECK_FIRST"), 120)              # "about two minutes after the box starts"
        self.assertEqual(constant("bin/sinko", "CHECK_EVERY"), 24 * 3600)        # "about once a day"
        self.assertEqual(constant("bin/sinko", "CHECK_RETRY"), 1800)             # "half an hour to an hour": this plus up to as much
        self.assertEqual(constant("bin/sinko", "TRANSIENT_RETRY"), 1800)         # "after half an hour, the same night"
        self.assertEqual(constant("bin/sinko", "FAILED_BLOCKS_AUTO"), 7 * 24 * 3600)   # "left alone for a week"
        self.assertEqual(constant("bin/sinko", "MIN_RUN_GAP"), 300)              # "never less than five minutes apart"
        self.assertEqual(constant("bin/sinko", "RUNNER_GRACE"), 180)             # "about three minutes"
        self.assertEqual(constant("bin/sinko", "REPAIR_AFTER"), 600)             # "ten minutes of difference"
        self.assertEqual(constant("bin/sinko", "REPAIR_EVERY"), 3600)            # "at most once an hour"
        self.assertEqual(constant("bin/sinko", "MIN_FREE_BYTES"), 200 * 1024 * 1024)   # "more than 200 MB"
        self.assertEqual(constant("bin/sinko", "PING_EVERY"), 6 * 3600)          # "about every 6 hours"
        self.assertEqual(constant("install.sh", "MIN_FREE_FIRST_MB"), 1024)      # "more than 1 GB for a first installation"
        self.assertEqual(constant("install.sh", "MIN_FREE_UPDATE_MB"), 200)
        self.assertEqual(constant("bin/sinko", "CLOCK_HOLD_UPTIME"), 600)        # "at most about ten minutes"
        window = re.search(r"AUTO_WINDOW = \((\d+), (\d+)\)", read("bin/sinko"))
        self.assertEqual((int(window.group(1)), int(window.group(2))), (3, 5))   # "between 03:00 and 05:00"

    def test_the_check_is_described_with_its_start_its_day_and_its_retry_in_both_languages(self):
        for needle in ("About two minutes after the box starts, then about once a day", "half an hour to an hour",
                       "after a power cut and after an update", "does not ask which version is the latest"):
            self.assertIn(needle, self.eng)
        for needle in ("بعد دقيقتين تقريبًا من تشغيل الصندوق", "مرة كل يوم تقريبًا", "بعد نصف ساعة إلى ساعة",
                       "بعد انقطاع الكهرباء وبعد كل تحديث", "فلا يسأل عن أحدث إصدار"):
            self.assertIn(needle, self.ara)

    def test_an_update_is_said_to_refresh_the_lists_and_fetch_the_doctor_s_file_in_both_languages(self):
        for needle in ("runs Sinko's installer again", "refreshes the block lists", "raw.githubusercontent.com", "lists/guard.txt",
                       "the same check as `sudo sinko doctor`", "after every update", "github.com"):
            self.assertIn(needle, self.eng)
        for needle in ("برنامج تثبيت سينكو", "raw.githubusercontent.com", "lists/guard.txt", "الفحص نفسه الذي يجريه الأمر",
                       "بعد كل تحديث", "github.com"):
            self.assertIn(needle, self.ara)

    def test_a_ready_made_box_is_not_said_to_have_all_of_it_done_without_the_update_requests(self):
        # The old text said a ready-made box has all of this done already and only your own box runs doctor.
        self.assertNotIn("On your own box, `sudo sinko doctor` also fetches", self.eng)
        self.assertNotIn("وعلى صندوقك الذي بنيته بنفسك يجلب الأمر `sudo sinko doctor` أيضًا", self.ara)
        self.assertIn("ready-made box therefore makes that small request after every update", self.eng)

    def test_who_sees_what_is_said_and_the_two_kinds_of_request_are_told_apart(self):
        self.assertIn("Who sees what", self.eng)
        self.assertIn("من يرى ماذا", self.ara)
        self.assertIn("do not name Sinko or its version", self.eng)
        self.assertIn("لا تذكر سينكو ولا إصداره", self.ara)

    def test_the_faq_names_the_check_the_list_refresh_and_the_small_file(self):
        for needle in ("about two minutes after the box starts and then about", "a refresh of the block lists",
                       "one small public file", "`sudo sinko doctor` does the same by hand"):
            self.assertIn(needle, self.faq)

    def test_the_retry_rule_is_the_one_in_the_program(self):
        for needle in ("only because the download could not be made (the internet) or the card has too little room",
                       "later in the same night", "left alone for a week", "never less than five minutes apart"):
            self.assertIn(needle, self.updating)

    def test_the_checklist_lists_what_an_update_really_sends(self):
        for needle in ("`sinko.tar.gz.sha256`", "one request for each block list", "`lists/guard.txt` (the doctor check)",
                       "about two minutes after the scheduler restarts"):
            self.assertIn(needle, self.checklist)
        self.assertNotIn("the release API and the two files", self.checklist)

    def test_the_counter_line_is_described_as_the_page_says_it(self):
        self.assertIn("This box is one of N Sinko boxes online", self.eng)
        self.assertIn("only from two boxes up", self.eng)
        self.assertIn("ابتداءً من صندوقين اثنين فقط", self.ara)
        self.assertNotIn("You are one of N", self.privacy)
        self.assertNotIn("أنت ضمن", self.privacy)
        # ... and that is what the page does (the strings exist, and nothing is shown below two boxes)
        box = read("web/pb-box.js")
        self.assertIn("boxCounterOnlineTwo", box)
        self.assertRegex(box, r"n < 2\) return null")


class AnUpdateThatWasCutShortIsDescribedHonestly(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.updating = flat(read("docs/updating.md"))
        cls.trouble = flat(read("docs/troubleshooting.md"))
        cls.checklist = flat(read("docs/hardware-test-checklist.md"))

    def test_the_guide_says_what_a_cut_can_leave_and_what_the_box_and_the_parent_do(self):
        self.assertIn("## If the power fails during an update", read("docs/updating.md"))
        for needle in ("No file is left half-written", "different versions for ten minutes", "from the copy it keeps of it",
                       "at most once an hour", "leave the box on for about a quarter of an hour", "re-flash",
                       "`sudo sinko repair`", "is not repaired by the box as a whole", "Try again"):
            self.assertIn(needle, self.updating)

    def test_it_does_not_promise_a_button_that_the_page_may_not_show(self):
        # The failed card has "Try again" only while a newer version is waiting (web/pb-box.js: `if (view.latest)`).
        self.assertIn("while a newer version is waiting", self.updating)
        self.assertRegex(read("web/pb-box.js"), r"if \(view\.latest\) kids\.push\(updateButton\('boxTryAgain'\)\)")
        self.assertNotIn("and try *Update now* again", self.updating)
        self.assertNotIn("and try *Update now* again", self.trouble)

    def test_the_repair_the_text_describes_exists_in_the_program(self):
        program = read("bin/sinko")
        self.assertIn('sub.add_parser("repair"', program)
        self.assertIn("def cmd_repair", program)
        self.assertIn("sinko repair", read("docs/install.md"))
        self.assertIn("sinko repair", self.trouble)

    def test_the_troubleshooting_page_tells_a_parent_to_wait_a_quarter_of_an_hour(self):
        self.assertIn("Leave the box on for about a quarter of an hour", self.trouble)

    def test_the_printed_card_and_the_sellers_guide_pass_the_same_advice_on_in_both_languages(self):
        card = flat(read("docs/quick-start-card.md"))
        self.assertIn("If the power failed while the box was updating, leave it plugged in for a quarter of an hour", card)
        self.assertIn("وإن انقطعت الكهرباء أثناء تحديث الصندوق فاتركه موصولًا ربع ساعة", card)
        self.assertIn("usually puts itself right", card)               # "usually": a cut after the page swap is not repaired by the box
        self.assertIn("usually puts itself right within a quarter of an hour", flat(read("docs/selling.md")))

    def test_the_checklist_tests_a_cut_at_the_two_moments_and_names_what_it_cannot_promise(self):
        for needle in ("A cut between the program and the page", "A cut after the page swap", "/var/lib/sinko/repair.json",
                       "does not cover this case"):
            self.assertIn(needle, self.checklist)


class TheInstallCommandIsOneForm(unittest.TestCase):
    """The hardened command (https only, no redirect out of https) is the only one written anywhere a person copies it from."""
    SOURCES = ["README.md", "docs/install.md", "docs/product-image.md", "docs/maintainers/releasing.md",
               "docs/maintainers/architecture.md", "docs/quick-start-card.md", "docs/selling.md", "docs/faq.md",
               "docs/troubleshooting.md", "site/index.html", "install.sh"]

    def test_every_install_one_liner_carries_both_flags(self):
        seen = 0
        for path in self.SOURCES:
            for number, line in enumerate(read(path).splitlines(), 1):
                if "releases/latest/download/install.sh" in line and "| sudo bash" in line:
                    seen += 1
                    for flag in ("--proto '=https'", "--proto-redir '=https'"):
                        self.assertIn(flag, line, "%s:%d writes the install command without %s" % (path, number, flag))
        self.assertGreaterEqual(seen, 6)            # README, install guide, image guide, releasing, architecture, the site, the installer

    def test_the_site_builds_the_same_command(self):
        self.assertRegex(read("site/lib.js"), r"curl --proto '=https' --proto-redir '=https' -fsSL https://github\.com/")

    def test_the_printed_card_asks_the_customer_to_type_nothing(self):
        self.assertNotIn("curl", read("docs/quick-start-card.md"))


class TheReadmeHasAnInstallSectionAndTheLinksToItResolve(unittest.TestCase):
    def test_the_readme_has_a_heading_named_install_with_the_command_and_a_pointer_to_the_guide(self):
        text = read("README.md")
        self.assertRegex(text, r"(?m)^## Install$")
        section = text.split("\n## Install\n")[1].split("\n## ")[0]
        self.assertIn("releases/latest/download/install.sh | sudo bash", section)
        self.assertIn("docs/install.md", section)

    def test_every_link_with_an_anchor_points_at_a_heading_that_exists(self):
        link = re.compile(r"\]\(([^)\s#]*)#([^)\s]+)\)")
        for path in [p for p in DOCS if p.endswith(".md")]:
            text = read(path)
            base = os.path.dirname(path)
            for target, anchor in link.findall(text):
                if re.match(r"[a-z]+:", target):
                    continue
                destination = path if target == "" else os.path.normpath(os.path.join(base, target))
                if not destination.endswith(".md"):
                    continue
                self.assertIn(anchor, anchors_of(destination), "%s links to %s#%s, which has no such heading" % (path, destination, anchor))

    def test_the_site_links_to_a_heading_the_readme_has(self):
        self.assertIn("install", anchors_of("README.md"))
        self.assertIn("two-ways-to-get-sinko", anchors_of("README.md"))      # docs/install.md links to it

    def test_the_slugger_agrees_with_githubs_for_the_headings_used(self):
        self.assertEqual(slug("Two ways to get Sinko"), "two-ways-to-get-sinko")
        self.assertEqual(slug("4. Hardware and the shop"), "4-hardware-and-the-shop")
        self.assertEqual(slug("Ready-made box (no login)"), "ready-made-box-no-login")
        self.assertEqual(slug("Install"), "install")


class TheBuyWordingAgreesWithTheSite(unittest.TestCase):
    """While the site has no shop link ("Not on sale yet"), nothing else may say that a box can be bought, and when it has one,
    nothing may still say it cannot."""

    def test_the_words_follow_the_shop_link(self):
        buy = re.search(r'buyUrl:\s*"([^"]*)"', read("site/config.js")).group(1)
        for path in ("README.md", "docs/install.md", "docs/faq.md"):
            said = "not on sale yet" in flat(read(path)).lower()
            self.assertEqual(said, not buy, "%s %s 'not on sale yet' although buyUrl is %r" % (path, "says" if said else "does not say", buy))
        self.assertIn("readySoon: 'Not on sale yet.", read("site/strings.js"))      # the site's own words, shown while buyUrl is empty

    def test_the_readme_does_not_send_a_buyer_to_a_place_that_does_not_say_where(self):
        text = flat(read("README.md"))
        self.assertNotIn("See the [project website](https://iret33.github.io/sinko/) for where to buy.", text)


class TheTaglineIsOne(unittest.TestCase):
    OLD = ("Family internet", "إنترنت العائلة", "إنترنت هادئ لعائلتك")

    def test_the_readme_the_site_the_page_and_the_image_notes_say_what_the_brand_tool_draws(self):
        import sys
        sys.path.insert(0, os.path.join(ROOT, "tests"))
        import pbstrings
        brand = pbstrings.brand()
        self.assertEqual(brand["TAGLINE_EN"], "Calm internet for the family")
        self.assertEqual(brand["TAGLINE_AR"], "إنترنت هادئ للعائلة")
        readme = read("README.md")
        img = read("docs/img/README.md")
        strings = read("site/strings.js")
        for text, where in ((readme, "README.md"), (img, "docs/img/README.md")):
            self.assertIn(brand["TAGLINE_EN"], text, where)
            self.assertIn(brand["TAGLINE_AR"], text, where)
        self.assertIn("heroTagline: '%s'" % brand["TAGLINE_EN"], strings)
        self.assertIn("heroTagline: '%s'" % brand["TAGLINE_AR"], strings)
        self.assertIn("docTitle: 'Sinko · %s'" % brand["TAGLINE_EN"], strings)
        self.assertIn("docTitle: 'سينكو · %s'" % brand["TAGLINE_AR"], strings)
        self.assertIn(brand["TAGLINE_EN"], read("docs/maintainers/releasing.md"))     # the repository description suggested there
        page = read("web/app.js")
        self.assertIn("tagline: '%s'" % brand["TAGLINE_EN"], page)
        self.assertIn("tagline: '%s'" % brand["TAGLINE_AR"], page)

    def test_no_document_still_uses_a_retired_tagline(self):
        for path in ("README.md", "docs/img/README.md", "docs/faq.md", "docs/privacy.md", "docs/install.md", "docs/selling.md",
                     "docs/maintainers/releasing.md", "site/strings.js", "site/index.html", "site/README.md"):
            text = read(path)
            for old in self.OLD:
                self.assertNotIn(old, text, "%s still says %r" % (path, old))


class ThePasswordRouteIsDescribedExactly(unittest.TestCase):
    def test_the_install_guide_says_which_pihole_gets_which_route_and_where_the_password_is_visible(self):
        text = flat(read("docs/install.md"))
        for needle in ("has no password yet", "through its API", "`pihole setpassword`", "for a moment it is in that program's arguments",
                       "process list", "*Change password*"):
            self.assertIn(needle, text)

    def test_what_the_guide_says_is_what_the_installer_does(self):
        script = read("install.sh")
        self.assertIn("pihole setpassword", script)
        self.assertIn("never on a command line", script)                      # the route that does exist: the API, for a Pi-hole with no password
        self.assertIn("has NO password", script)
        # the Python that talks to the API signs in with nothing: it only asks whether a session is needed at all
        python = script.split("SET_PASSWORD_PY <<'PYEOF'")[1].split("\nPYEOF")[0]
        self.assertNotIn("cli_pw", python)
        # the guide must not call the API route the only one
        self.assertNotIn("hands it to Pi-hole through Pi-hole's API |", read("docs/install.md"))


class TheWatchdogAndShutDownAreDescribedAsTheSealDoesIt(unittest.TestCase):
    def test_the_image_guide_and_the_checklist_say_only_what_is_known(self):
        guide = flat(read("docs/product-image.md"))
        for needle in ("*Shut down* disarms the watchdog first", "systemd 257", "which is read in its source and not measured",
                       "A *Restart* keeps the watchdog armed at 15 seconds", "only the real board shows"):
            self.assertIn(needle, guide)
        checklist = flat(read("docs/hardware-test-checklist.md"))
        for needle in ("**Shut down and the watchdog.**", "**Restart with a backlog on the card.**", "CONFIG_WATCHDOG_NOWAYOUT"):
            self.assertIn(needle, checklist)
        self.assertNotIn("(the hardware watchdog does not bring it back)", checklist)

    def test_the_seal_still_writes_the_two_fifteen_second_values_the_text_names(self):
        seal = read("tools/seal.sh")
        self.assertIn("RuntimeWatchdogSec=15", seal)
        self.assertIn("RebootWatchdogSec=15", seal)

    def test_the_seal_refuses_what_the_guide_says_it_refuses(self):
        seal = read("tools/seal.sh")
        for needle in ("unattended-upgrades is not installed", "no time service", "can log in on the console", "this unit follows"):
            self.assertIn(needle, seal)
        guide = flat(read("docs/product-image.md"))
        for needle in ("`unattended-upgrades` is not installed or not switched on", "no time service is on",
                       "pinned to a version or follows a branch", "an account other than root can log in on the console"):
            self.assertIn(needle, guide)


class ThePiholeVersionInAboutIsReal(unittest.TestCase):
    def test_the_documents_that_promise_it_name_a_page_that_shows_it(self):
        box = read("web/pb-box.js")
        self.assertIn("boxAboutPihole", box)
        self.assertIn("/api/info/version", box)
        for path in ("docs/updating.md", "docs/faq.md", "docs/selling.md", "docs/product-image.md"):
            self.assertRegex(flat(read(path)), r"\*About\* (shows|says) (which )?Pi-hole version", path)
        self.assertIn("/api/info/version", read("docs/maintainers/architecture.md"))


class TheReleasingGuideSaysWhatWorksAfterWhich(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = read("docs/maintainers/releasing.md")
        cls.section = cls.text.split("## Before you publish")[1].split("\n## ")[0]

    def test_the_section_is_there_and_names_every_badge_and_link_of_the_readme(self):
        self.assertIn("## Before you publish", self.text)
        readme = read("README.md")
        for name in re.findall(r'<img alt="([^"]+)" src="https://(?:img\.shields\.io/github|github\.com/iret33)', readme):
            self.assertIn(name, self.section, "the README badge %r is not in the list" % name)
        for needle in ("Website", "Licence", "boxes online", "web/links.json", "site/config.js"):
            self.assertIn(needle, self.section)

    def test_the_steps_come_in_the_order_that_works(self):
        steps = re.findall(r"(?m)^(\d)\. \*\*([^*]+)\*\*", self.section)
        names = [name for _, name in steps]
        self.assertEqual([n for n, _ in steps], [str(i) for i in range(1, len(steps) + 1)])
        order = ["Rename the repository", "Merge this tree", "Let CI run", "Switch Pages on", "Deploy the counter", "If there is a shop", "Release", "Walk every link"]
        positions = [next(i for i, n in enumerate(names) if n.startswith(o)) for o in order]
        self.assertEqual(positions, sorted(positions))

    def test_it_says_which_step_each_thing_needs(self):
        for needle in ("1 (rename)", "2 (master holds the tree under the new name)", "(it stays empty until one CI run exists on `master`)",
                       "4 (Pages)", "5 (counter deployed)", "(the first release with its files;", "already (it is a static image)",
                       "Settings → Pages → Build and deployment → Source = *GitHub Actions*", "Social preview"):
            self.assertIn(needle, flat(self.section))

    def test_the_readme_badges_still_point_at_the_names_the_guide_uses(self):
        readme = read("README.md")
        self.assertIn("img.shields.io/github/downloads/iret33/sinko/total", readme)
        self.assertIn("img.shields.io/github/v/release/iret33/sinko", readme)
        self.assertIn("actions/workflows/ci.yml/badge.svg", readme)
        self.assertIn("https://iret33.github.io/sinko/", readme)


class TheChecklistHasTheItemsOnlyARealBoxCanAnswer(unittest.TestCase):
    def test_every_item_this_round_found_is_on_the_list(self):
        text = flat(read("docs/hardware-test-checklist.md"))
        for needle in ("**The restore window.**", "**The self-check on the slowest board.**", "**systemd-run and the reboot.**",
                       "**Shut down and the watchdog.**", "**The clock after a power cut.**", "**Each box has its own HTTPS key.**",
                       "**A cut between the program and the page.**", "**A failed night is tried again the same night.**",
                       "**The SSH read-back.**", "**The zero-fill on a real card**", "A system that speaks another language",
                       "Not enough room:", "`?v=<version>`"):
            self.assertIn(needle, text)


if __name__ == "__main__":
    unittest.main()
