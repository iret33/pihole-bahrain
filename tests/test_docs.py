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


if __name__ == "__main__":
    unittest.main()
