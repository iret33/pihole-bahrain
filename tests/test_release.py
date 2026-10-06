"""The release archive is what every box downloads and installs, so its shape is tested like code."""
import hashlib
import os
import re
import subprocess
import sys
import tarfile
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def build(out):
    return subprocess.run(["bash", os.path.join(ROOT, "tools", "build-release.sh"), out],
                          capture_output=True, text=True, check=True, env=dict(os.environ, SOURCE_DATE_EPOCH="1700000000"))


class ReleaseBuild(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.out = os.path.join(cls.tmp.name, "dist")
        build(cls.out)
        cls.tar = os.path.join(cls.out, "sinko.tar.gz")
        with tarfile.open(cls.tar) as tf:
            cls.members = {m.name: m for m in tf.getmembers()}
            cls.version = tf.extractfile("sinko/VERSION").read().decode().strip()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_the_three_assets_exist(self):
        for name in ("sinko.tar.gz", "sinko.tar.gz.sha256", "install.sh"):
            self.assertTrue(os.path.isfile(os.path.join(self.out, name)), name)

    def test_checksum_file_is_in_sha256sum_format_and_correct(self):
        with open(os.path.join(self.out, "sinko.tar.gz.sha256")) as fh:
            line = fh.read()
        digest, name = re.fullmatch(r"([0-9a-f]{64})  (sinko\.tar\.gz)\n", line).groups()
        with open(self.tar, "rb") as fh:
            self.assertEqual(hashlib.sha256(fh.read()).hexdigest(), digest)

    def test_install_sh_is_the_file_in_the_tree(self):
        with open(os.path.join(ROOT, "install.sh"), "rb") as a, open(os.path.join(self.out, "install.sh"), "rb") as b:
            self.assertEqual(a.read(), b.read())

    def test_one_top_level_folder_and_no_unsafe_paths(self):
        for name, m in self.members.items():
            self.assertTrue(name == "sinko" or name.startswith("sinko/"), name)
            self.assertFalse(os.path.isabs(name) or ".." in name.split("/"), name)
            self.assertTrue(m.isfile() or m.isdir(), "%s is a link or device" % name)

    def test_what_a_box_needs_is_inside(self):
        for need in ("bin/sinko", "install.sh", "uninstall.sh", "VERSION", "LICENSE", "NOTICE", "web/index.html",
                     "web/app.js", "lists/services.json", "systemd/sinko.service", "systemd/sinko-lists.timer"):
            self.assertIn("sinko/" + need, self.members)

    def test_what_the_documents_send_a_seller_to_look_for_is_inside(self):
        # docs/selling.md and docs/product-image.md name these paths on the box: they only exist there if the release carries them.
        for need in ("tools/seal.sh", "tools/firstboot.sh", "systemd/sinko-firstboot.service", "systemd/sinko-lists.service",
                     "lists/LICENSE", "web/fonts/OFL.txt", "web/fonts/plex-arabic-arabic-400.woff2"):
            self.assertIn("sinko/" + need, self.members)
        for name in ("tools/seal.sh", "tools/firstboot.sh"):
            self.assertTrue(self.members["sinko/" + name].mode & 0o111, name)

    def test_the_start_page_carries_the_stamp_the_installer_fills_in(self):
        # The installer replaces @VERSION@ with the release's version in the addresses of the page's scripts and styles (a phone keeps
        # files from Pi-hole's web server for an hour), so every file of the page must be asked for with it, and the page must say
        # which release it is. A page without the stamp would run old scripts after an update.
        with tarfile.open(self.tar) as tf:
            page = tf.extractfile("sinko/web/index.html").read().decode("utf-8")
        self.assertIn('<meta name="sinko-version" content="@VERSION@">', page)
        files = re.findall(r'(?:src|href)="/pb/([^"?]+)\?v=@VERSION@"', page)
        self.assertGreaterEqual(len(files), 6)                 # the style sheet and five scripts
        for name in files:
            self.assertIn("sinko/web/" + name, self.members, name)
        self.assertEqual(re.findall(r'(?:src|href)="/pb/(?![^"]*\?v=@VERSION@)[^"]*\.(?:js|css)"', page), [], "an address without the stamp")

    def test_what_a_box_does_not_need_stays_out(self):
        for name in self.members:
            self.assertFalse(re.match(r"sinko/(tests|docs|site|telemetry|\.github)/", name), name)
        self.assertNotIn("sinko/tools/build-release.sh", self.members)

    def test_scripts_are_executable_and_files_are_not_owned_by_a_person(self):
        for name in ("bin/sinko", "install.sh", "uninstall.sh"):
            self.assertTrue(self.members["sinko/" + name].mode & 0o111, name)
        for m in self.members.values():
            self.assertEqual((m.uid, m.gid, m.mtime), (0, 0, 1700000000), m.name)

    def test_version_agrees_everywhere(self):
        with open(os.path.join(ROOT, "VERSION")) as fh:
            self.assertEqual(self.version, fh.read().strip())
        with open(os.path.join(ROOT, "bin", "sinko"), encoding="utf-8") as fh:
            self.assertIn('VERSION = "%s"' % self.version, fh.read())

    def test_build_is_reproducible(self):
        with tempfile.TemporaryDirectory() as again:
            build(os.path.join(again, "dist"))
            with open(self.tar, "rb") as a, open(os.path.join(again, "dist", "sinko.tar.gz"), "rb") as b:
                self.assertEqual(a.read(), b.read())

    def test_the_changelog_has_notes_for_this_version(self):
        out = subprocess.run(["python3", os.path.join(ROOT, "tools", "changelog-section.py"), self.version],
                             capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertTrue(out.stdout.strip())


TOOL = os.path.join(ROOT, "tools", "changelog-section.py")
NOTES = "### Added\n- Something people will notice.\n"


def changelog_section(text, *flags, version="3.0.0"):
    """Runs tools/changelog-section.py on a CHANGELOG with this text. Returns the finished process."""
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "CHANGELOG.md")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return subprocess.run([sys.executable, TOOL, "--changelog", path, *flags, version], capture_output=True, text=True)


class ChangelogGate(unittest.TestCase):
    """A tag must not publish notes whose tagged file still says "Unreleased": the release workflow asks for a date."""

    def test_a_dated_heading_passes_the_gate_and_prints_its_notes(self):
        for heading in ("## [3.0.0] - 2026-10-05", "## [3.0.0] - 2026-02-28"):
            out = changelog_section("# Changelog\n\n%s\n%s\n## [2.2.0] - 2026-10-03\n- older\n" % (heading, NOTES), "--require-date")
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertEqual(out.stdout.strip(), NOTES.strip())

    def test_unreleased_is_refused_when_a_date_is_required(self):
        text = "# Changelog\n\n## [3.0.0] - Unreleased\n%s\n## [2.2.0] - 2026-10-03\n- older\n" % NOTES
        out = changelog_section(text, "--require-date")
        self.assertEqual(out.returncode, 1)
        self.assertEqual(out.stdout, "")
        self.assertIn("Unreleased", out.stderr)
        self.assertIn("YYYY-MM-DD", out.stderr)

    def test_without_the_flag_the_notes_can_still_be_previewed(self):
        out = changelog_section("## [3.0.0] - Unreleased\n%s" % NOTES)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout.strip(), NOTES.strip())

    def test_anything_that_is_not_exactly_a_real_date_is_refused(self):
        for tail in ("", " - ", " - Unreleased", " - unreleased", " - TBD", " - 2026-10", " - 2026-10-5", " - 05-10-2026",
                     " - 2026/10/05", " - 2026-13-01", " - 2026-02-30", " - 0000-00-00", " - 2026-10-05 (rc)",
                     " - 2026-10-05.", " - \u0662\u0660\u0662\u0666-\u0661\u0660-\u0660\u0665"):
            out = changelog_section("## [3.0.0]%s\n%s" % (tail, NOTES), "--require-date")
            self.assertEqual(out.returncode, 1, "accepted the heading %r" % tail)
            self.assertEqual(out.stdout, "")

    def test_only_the_version_being_released_needs_a_date(self):
        text = "## [3.1.0] - Unreleased\n- next\n\n## [3.0.0] - 2026-10-05\n%s" % NOTES
        self.assertEqual(changelog_section(text, "--require-date", version="3.0.0").returncode, 0)
        self.assertEqual(changelog_section(text, "--require-date", version="3.1.0").returncode, 1)

    def test_a_missing_empty_or_doubled_section_is_refused_in_both_modes(self):
        for flags in ((), ("--require-date",)):
            self.assertEqual(changelog_section("## [2.2.0] - 2026-10-03\n- older\n", *flags).returncode, 1)
            self.assertEqual(changelog_section("## [3.0.0] - 2026-10-05\n\n\n## [2.2.0] - 2026-10-03\n- older\n", *flags).returncode, 1)
            both = "## [3.0.0] - 2026-10-05\n%s\n## [3.0.0] - 2026-10-06\n%s" % (NOTES, NOTES)
            out = changelog_section(both, *flags)
            self.assertEqual(out.returncode, 1)
            self.assertIn("2 sections", out.stderr)

    def test_a_version_that_is_not_a_version_and_a_missing_file_are_refused(self):
        self.assertNotEqual(changelog_section(NOTES, version="latest").returncode, 0)
        out = subprocess.run([sys.executable, TOOL, "--changelog", os.path.join(tempfile.gettempdir(), "no-such-changelog.md"), "3.0.0"],
                             capture_output=True, text=True)
        self.assertEqual(out.returncode, 1)

    def test_a_leading_v_is_fine(self):
        out = changelog_section("## [3.0.0] - 2026-10-05\n%s" % NOTES, "--require-date", version="v3.0.0")
        self.assertEqual(out.returncode, 0, out.stderr)


class ReleaseWorkflow(unittest.TestCase):
    """What .github/workflows/release.yml must keep doing; it cannot be run here, so its text is checked."""

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(ROOT, ".github", "workflows", "release.yml"), encoding="utf-8") as fh:
            cls.text = fh.read()

    def test_the_changelog_step_requires_a_date(self):
        self.assertRegex(self.text, r"changelog-section\.py\s+--require-date\b")

    def test_the_gate_is_not_switched_off_anywhere(self):
        self.assertNotIn("--allow", self.text)

    def test_only_github_s_own_actions_run_and_none_is_unpinned_third_party_code_in_the_write_path(self):
        uses = re.findall(r"^\s*(?:-\s*)?uses:\s*(\S+)", self.text, re.M)
        self.assertTrue(uses)
        for ref in uses:
            self.assertTrue(ref.startswith("actions/") or ref.startswith("./"), "%s is not a GitHub-owned action" % ref)
        self.assertNotIn("softprops", self.text)

    def test_the_job_that_can_write_runs_nothing_from_the_repository(self):
        publish = self.text[self.text.index("\n  publish:"):]
        self.assertIn("contents: write", publish)
        self.assertNotIn("actions/checkout", publish)
        self.assertNotIn("tools/", publish)
        self.assertNotIn("contents: write", self.text[:self.text.index("\n  publish:")])

    def test_a_release_published_on_the_website_starts_it_once(self):
        # Pushing the tag is one way; publishing a release on the website (which creates the tag) is the other.
        self.assertRegex(self.text, r"(?m)^  push:\n    tags: \[\"v\[0-9\]\+")
        self.assertRegex(self.text, r"(?m)^  release:\n    types: \[published\]$")
        self.assertRegex(self.text, r"(?m)^concurrency:\n  group: release-\$\{\{ github\.ref_name \}\}\n  cancel-in-progress: true$")
        checks = self.text[self.text.index("\n  checks:"):self.text.index("\n  build:")]
        self.assertIn("startsWith(github.event.release.tag_name, 'v')", checks)

    def publish_script(self):
        """The shell of the Publish step, as the runner gets it (the block under its `run: |`)."""
        block = self.text[self.text.index("- name: Publish"):]
        lines = block.split("\n")[2:]
        body = []
        for line in lines:
            if line.strip() and not line.startswith(" " * 10):
                break
            body.append(line[10:])
        return "\n".join(body)

    def run_publish(self, release_exists):
        """Run the Publish step against a stand-in `gh` that only writes down how it was called."""
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "bin"))
            os.makedirs(os.path.join(tmp, "dist"))
            for name in ("sinko.tar.gz", "sinko.tar.gz.sha256", "install.sh", "release-notes.md"):
                open(os.path.join(tmp, "dist", name), "w").close()
            calls = os.path.join(tmp, "calls")
            with open(os.path.join(tmp, "bin", "gh"), "w") as fh:
                fh.write('#!/bin/sh\necho "$*" >> "%s"\n'
                         'if [ "$1 $2" = "release view" ]; then exit %d; fi\nexit 0\n' % (calls, 0 if release_exists else 1))
            os.chmod(os.path.join(tmp, "bin", "gh"), 0o755)
            env = dict(os.environ, GITHUB_REF_NAME="v3.0.0", PATH=os.path.join(tmp, "bin") + os.pathsep + os.environ["PATH"])
            done = subprocess.run(["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", self.publish_script()],
                                  cwd=tmp, env=env, capture_output=True, text=True)
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
            with open(calls) as fh:
                return [line for line in fh.read().splitlines() if not line.startswith("--version")]

    def test_a_tag_pushed_with_git_creates_the_release_with_the_three_files(self):
        calls = self.run_publish(release_exists=False)
        self.assertTrue(calls[0].startswith("release view v3.0.0"), calls)
        self.assertEqual(len(calls), 2, calls)
        self.assertTrue(calls[1].startswith("release create v3.0.0 dist/sinko.tar.gz dist/sinko.tar.gz.sha256 dist/install.sh"))
        for flag in ("--verify-tag", "--latest", "--notes-file dist/release-notes.md"):
            self.assertIn(flag, calls[1])

    def test_a_release_made_on_the_website_gets_the_files_and_notes_instead_of_failing(self):
        # Making the release on the website creates the tag, which starts the workflow: the release already exists.
        calls = self.run_publish(release_exists=True)
        self.assertEqual(len(calls), 3, calls)
        self.assertTrue(calls[1].startswith("release upload v3.0.0 dist/sinko.tar.gz dist/sinko.tar.gz.sha256 dist/install.sh"))
        self.assertIn("--clobber", calls[1])
        self.assertTrue(calls[2].startswith("release edit v3.0.0"), calls)
        for flag in ("--notes-file dist/release-notes.md", "--draft=false", "--prerelease=false", "--latest"):
            self.assertIn(flag, calls[2])
        self.assertFalse(any(c.startswith("release create") for c in calls), calls)


if __name__ == "__main__":
    unittest.main()
