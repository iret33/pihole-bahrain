"""The release archive is what every box downloads and installs, so its shape is tested like code."""
import hashlib
import os
import re
import subprocess
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


if __name__ == "__main__":
    unittest.main()
