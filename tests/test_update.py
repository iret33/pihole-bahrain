"""The update engine: release check, download, verification, safe unpacking, install, self-check, rollback, and the
`sinko update|rollback|selfcheck` commands. Network access goes to a local HTTP server (tests/fake_release.py); the
installer and the installed program are small fakes inside the fake release, and they really run."""
import contextlib
import hashlib
import importlib.machinery
import importlib.util
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import unittest
import urllib.error
from unittest import mock

import fake_release
import mock_pihole

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
LISTS = os.path.join(ROOT, "lists")

loader = importlib.machinery.SourceFileLoader("sinko_cli", os.path.join(ROOT, "bin", "sinko"))
spec = importlib.util.spec_from_loader("sinko_cli", loader)
pb = importlib.util.module_from_spec(spec)
loader.exec_module(pb)

ENV_TO_CLEAR = ("SINKO_RELEASE_BASE", "SINKO_RELEASE_API", "SINKO_REF", "SINKO_REPO", "SINKO_REPO_SLUG",
                "SINKO_WEBROOT", "SINKO_API_URL")


class Box(unittest.TestCase):
    """A box in a temp folder: state dir, app dir with the 3.0.0 fake program installed, and a fake GitHub."""

    def setUp(self):
        self.httpd, self.site = fake_release.serve()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name
        self.state = os.path.join(self.tmp, "state")
        self.app = os.path.join(self.tmp, "app")
        self.log_path = os.path.join(self.tmp, "install.log")
        os.makedirs(os.path.join(self.app, "bin"))
        env = mock.patch.dict(os.environ, {"SINKO_STATE_DIR": self.state, "SINKO_APP_DIR": self.app,
                                           "FAKE_LOG": self.log_path})
        env.start()
        self.addCleanup(env.stop)
        for key in ENV_TO_CLEAR:
            os.environ.pop(key, None)
        patch = mock.patch.object(pb, "APP_DIR", self.app)
        patch.start()
        self.addCleanup(patch.stop)
        files = fake_release.release_files("3.0.0")
        for name in ("bin/sinko", "VERSION"):
            data, mode = files["sinko/" + name]
            with open(os.path.join(self.app, name), "wb") as fh:
                fh.write(data)
            os.chmod(os.path.join(self.app, name), mode)
        self.lines = []

    def updater(self, conf=None, **kw):
        return pb.Updater(conf=self.site.conf() if conf is None else conf, out=self.lines.append, **kw)

    def publish(self, version, latest=True, **build):
        """A release on the fake GitHub; `latest` also makes it what the API and /latest/ answer with."""
        tarball = self.site.add_release(version, **build)
        if latest:
            self.site.set_api(self.site.release_json(version))
            self.site.make_latest(version)
        return tarball

    @staticmethod
    def read(path):
        with open(path) as fh:
            return fh.read().strip()

    def installed(self):
        return self.read(os.path.join(self.app, "VERSION"))

    def installs(self):
        try:
            with open(self.log_path) as fh:
                return fh.read().splitlines()
        except FileNotFoundError:
            return []

    def leftovers(self):
        found = [n for n in os.listdir(self.app) if n.startswith(".stage-")]
        cache = os.path.join(self.state, "cache")
        if os.path.isdir(cache):
            found += [n for n in os.listdir(cache) if n.startswith(".dl-")]
        return found


class VersionTests(unittest.TestCase):
    def test_parse_and_compare_numbers_not_text(self):
        self.assertEqual(pb.parse_version("3.10.0"), (3, 10, 0))
        self.assertTrue(pb.is_newer("3.10.0", "3.9.9"))
        self.assertTrue(pb.is_newer("4.0.0", "3.99.99"))
        self.assertFalse(pb.is_newer("3.0.0", "3.0.0"))
        self.assertFalse(pb.is_newer("2.9.9", "3.0.0"))

    def test_anything_that_is_not_x_y_z_is_not_a_version(self):
        for bad in ("3.1", "3.1.0-rc1", "v3.1.0", "3.1.0.1", "", None, 3, "a.b.c", "3.1.0\n", "-1.0.0"):
            self.assertIsNone(pb.parse_version(bad), repr(bad))
            self.assertFalse(pb.is_newer(bad, "3.0.0"))
            self.assertFalse(pb.is_newer("3.1.0", bad))


class SettingsTests(unittest.TestCase):
    def setUp(self):
        env = mock.patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        for key in ENV_TO_CLEAR:
            os.environ.pop(key, None)

    def test_defaults_point_at_the_projects_github(self):
        s = pb.release_settings({})
        self.assertEqual(s["slug"], "iret33/sinko")
        self.assertEqual(s["base"], "https://github.com/iret33/sinko/releases")
        self.assertEqual(s["api"], "https://api.github.com/repos/iret33/sinko/releases/latest")
        self.assertEqual(s["ref"], "latest")

    def test_the_slug_comes_from_the_repo_url_when_not_given(self):
        for repo in ("https://github.com/acme/box.git", "https://github.com/acme/box", "git@github.com:acme/box.git"):
            self.assertEqual(pb.release_settings({"SINKO_REPO": repo})["slug"], "acme/box", repo)
        self.assertEqual(pb.release_settings({"SINKO_REPO": "https://example.org/x.git"})["slug"], "iret33/sinko")
        self.assertEqual(pb.release_settings({"SINKO_REPO": "https://github.com/a/b.git", "SINKO_REPO_SLUG": "c/d"})["slug"], "c/d")

    def test_the_environment_beats_the_settings_file(self):
        os.environ["SINKO_RELEASE_BASE"] = "http://127.0.0.1:1/rel/"
        conf = {"SINKO_RELEASE_BASE": "http://elsewhere/rel", "SINKO_RELEASE_API": "http://elsewhere/api",
                "SINKO_REF": "v3.0.1"}
        s = pb.release_settings(conf)
        self.assertEqual((s["base"], s["api"], s["ref"]), ("http://127.0.0.1:1/rel", "http://elsewhere/api", "v3.0.1"))

    def test_bad_settings_are_refused_with_a_readable_message(self):
        for conf in ({"SINKO_REPO_SLUG": "not a slug"}, {"SINKO_REPO_SLUG": "a/b/c"}, {"SINKO_REPO_SLUG": "../x"},
                     {"SINKO_RELEASE_BASE": "file:///etc"}, {"SINKO_RELEASE_API": "ftp://x/y"}):
            with self.assertRaises(pb.UpdateError, msg=conf):
                pb.release_settings(conf)

    def test_refs(self):
        self.assertEqual(pb.classify_ref("latest"), ("latest", None))
        self.assertEqual(pb.classify_ref("v3.1.0"), ("tag", "3.1.0"))
        self.assertEqual(pb.classify_ref("master"), ("branch", "master"))
        self.assertEqual(pb.classify_ref("feature/x-1.2"), ("branch", "feature/x-1.2"))
        self.assertEqual(pb.classify_ref("v3.1.0-rc1"), ("branch", "v3.1.0-rc1"))
        for bad in ("", "-x", "--upload-pack=touch /tmp/x", "a b", "a..b", "x;rm", "$(id)", "a" * 200, None, "é"):
            with self.assertRaises(pb.UpdateError, msg=repr(bad)):
                pb.classify_ref(bad)


class CheckTests(Box):
    def check(self, **kw):
        return pb.check_for_update(self.site.conf(), running=kw.pop("running", "3.0.0"))

    def test_a_newer_release_is_reported_with_notes_built_from_the_configured_repo(self):
        self.site.set_api(self.site.release_json("3.1.0"))
        found = self.check()
        self.assertEqual((found["version"], found["tag"]), ("3.1.0", "v3.1.0"))
        self.assertEqual(found["notes"], "https://github.com/iret33/sinko/releases/tag/v3.1.0",
                         "a URL a server sent (here evil.example) is never passed on")
        self.assertRegex(found["notes"], pb.NOTES_RE.pattern)

    def test_the_same_or_an_older_release_is_no_update(self):
        for version in ("3.0.0", "2.9.0"):
            self.site.set_api(self.site.release_json(version))
            self.assertIsNone(self.check(), version)

    def test_drafts_prereleases_and_odd_tags_are_ignored(self):
        for extra in ({"draft": True}, {"prerelease": True}):
            self.site.set_api(self.site.release_json("3.1.0", **extra))
            self.assertIsNone(self.check(), extra)
        for tag in ("v3.1.0-rc1", "3.1.0", "v3.1", "latest", None, 5):
            self.site.set_api({"tag_name": tag, "draft": False, "prerelease": False})
            self.assertIsNone(self.check(), tag)

    def test_a_release_list_gives_the_newest_stable_one(self):
        self.site.set_api([self.site.release_json("3.2.0", prerelease=True), self.site.release_json("3.1.0"),
                           self.site.release_json("3.10.0", draft=True), self.site.release_json("3.0.5"),
                           "junk", None])
        self.assertEqual(self.check()["version"], "3.1.0")
        self.assertIsNone(pb.pick_release([]))
        self.assertIsNone(pb.pick_release("nonsense"))

    def test_every_way_the_network_can_fail_is_an_update_error(self):
        self.site.set_api({}, status=403)                                   # rate limited
        with self.assertRaises(pb.UpdateError):
            self.check()
        self.site.routes["/api/latest"] = (200, b"<html>not json</html>", {})
        with self.assertRaises(pb.UpdateError):
            self.check()
        self.site.routes.pop("/api/latest")                                 # 404
        with self.assertRaises(pb.UpdateError):
            self.check()
        self.httpd.shutdown()
        self.httpd.server_close()                                           # connection refused
        with self.assertRaises(pb.UpdateError):
            self.check()

    def test_a_pinned_release_needs_no_network_and_a_branch_is_never_offered_a_release(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        conf = self.site.conf(SINKO_REF="v3.4.5")
        self.assertEqual(pb.check_for_update(conf)["version"], "3.4.5")
        self.assertIsNone(pb.check_for_update(conf, running="3.4.5"))
        self.assertIsNone(pb.check_for_update(self.site.conf(SINKO_REF="master")))
        self.assertEqual(pb.check_for_update(self.site.conf(SINKO_REF="master"), ref="v3.5.0")["version"], "3.5.0")

    def test_the_request_names_the_program_and_the_check_has_a_timeout(self):
        seen = {}

        def handler(h):
            seen.update(h.headers)
            body = json.dumps(self.site.release_json("3.1.0")).encode()
            h.send_response(200)
            h.send_header("Content-Length", str(len(body)))
            h.end_headers()
            h.wfile.write(body)
        self.site.routes["/api/latest"] = handler
        self.check()
        self.assertEqual(seen["User-Agent"], "sinko/" + pb.VERSION)
        self.assertLessEqual(pb.CHECK_TIMEOUT, 10)


class FetchTests(Box):
    def route(self, path, fn):
        self.site.routes[path] = fn
        return self.site.base.replace("/releases", "") + path

    def test_a_declared_size_over_the_cap_is_refused_before_reading(self):
        url = self.route("/big", (200, b"x" * 100, {}))
        with self.assertRaises(pb.UpdateError):
            pb.http_fetch(url, 50)
        self.assertEqual(pb.http_fetch(url, 100), b"x" * 100)

    def test_a_body_over_the_cap_is_cut_off_while_it_arrives_even_without_a_length(self):
        def handler(h):
            h.send_response(200)
            h.end_headers()
            try:
                h.wfile.write(b"x" * 100000)
            except OSError:
                pass
            h.close_connection = True
        url = self.route("/stream", handler)
        with self.assertRaises(pb.UpdateError):
            pb.http_fetch(url, 5000)

    def test_a_trickle_hits_the_overall_deadline_not_just_the_per_wait_timeout(self):
        def handler(h):
            h.send_response(200)
            h.end_headers()
            try:
                for _ in range(100):
                    h.wfile.write(b"x")
                    h.wfile.flush()
                    time.sleep(0.05)
            except OSError:
                pass
            h.close_connection = True
        url = self.route("/trickle", handler)
        started = time.monotonic()
        with self.assertRaises(pb.UpdateError) as ctx:
            pb.http_fetch(url, 10 ** 6, timeout=5, deadline=0.4)
        self.assertIn("too long", str(ctx.exception))
        self.assertLess(time.monotonic() - started, 3)

    def test_a_server_that_says_nothing_times_out(self):
        url = self.route("/silent", lambda h: time.sleep(3))
        started = time.monotonic()
        with self.assertRaises(pb.UpdateError):
            pb.http_fetch(url, 100, timeout=0.3)
        self.assertLess(time.monotonic() - started, 2.5)

    def test_redirects_are_followed_and_the_body_can_go_to_a_file(self):
        self.site.routes["/old"] = (302, b"", {"Location": "/new"})
        self.site.routes["/new"] = (200, b"hello", {})
        origin = self.site.base.replace("/releases", "")
        dest = os.path.join(self.tmp, "out")
        self.assertIsNone(pb.http_fetch(origin + "/old", 100, dest=dest))
        with open(dest, "rb") as fh:
            self.assertEqual(fh.read(), b"hello")

    def test_a_redirect_from_https_down_to_http_or_to_a_strange_scheme_is_refused(self):
        handler = pb._SafeRedirect()
        req = mock.Mock(full_url="https://github.com/x")
        for new in ("http://evil.example/x", "ftp://evil.example/x", "file:///etc/passwd"):
            with self.assertRaises(urllib.error.HTTPError, msg=new):
                handler.redirect_request(req, None, 302, "Found", {}, new)

    def test_an_error_status_names_the_status_not_a_stack_trace(self):
        origin = self.site.base.replace("/releases", "")
        with self.assertRaises(pb.UpdateError) as ctx:
            pb.http_fetch(origin + "/missing.tar.gz", 100)
        self.assertIn("404", str(ctx.exception))


class ChecksumTests(Box):
    def settings(self):
        return pb.release_settings(self.site.conf())

    def test_sha256_file_forms(self):
        h = "a" * 64
        self.assertEqual(pb.parse_sha256("%s  sinko.tar.gz\n" % h), h)
        self.assertEqual(pb.parse_sha256("%s *sinko.tar.gz" % h.upper()), h)
        self.assertEqual(pb.parse_sha256(h), h)
        for bad in ("", "\n", "zz  sinko.tar.gz", "%s  other.tar.gz" % h, "%s  sinko.tar.gz\n%s  sinko.tar.gz" % (h, h),
                    "%s" % ("a" * 63), "%s  sinko.tar.gz extra" % h):
            with self.assertRaises(pb.UpdateError, msg=repr(bad)):
                pb.parse_sha256(bad)

    def test_a_download_that_matches_is_kept(self):
        data = self.site.add_release("3.1.0")
        dest = os.path.join(self.tmp, "dl")
        pb.download_release(self.settings(), "v3.1.0", dest)
        with open(dest, "rb") as fh:
            self.assertEqual(fh.read(), data)

    def test_a_mismatch_is_refused_and_the_file_is_removed(self):
        self.site.add_release("3.1.0", sha=("0" * 64 + "  sinko.tar.gz\n").encode())
        dest = os.path.join(self.tmp, "dl")
        with self.assertRaises(pb.UpdateError) as ctx:
            pb.download_release(self.settings(), "v3.1.0", dest)
        self.assertIn("checksum", str(ctx.exception))
        self.assertFalse(os.path.exists(dest))

    def test_a_missing_or_garbled_checksum_file_is_refused(self):
        for sha in (False, b"not a checksum\n", b"\xff\xfe\x00"):
            self.site.add_release("3.1.0", sha=sha)
            dest = os.path.join(self.tmp, "dl")
            with self.assertRaises(pb.UpdateError, msg=repr(sha)):
                pb.download_release(self.settings(), "v3.1.0", dest)
            self.assertFalse(os.path.exists(dest))

    def test_a_missing_tarball_leaves_nothing_behind(self):
        dest = os.path.join(self.tmp, "dl")
        with open(dest, "wb"):
            pass
        with self.assertRaises(pb.UpdateError):
            pb.download_release(self.settings(), "v9.9.9", dest)
        self.assertFalse(os.path.exists(dest))

    def test_the_size_cap_applies_to_the_tarball(self):
        self.site.add_release("3.1.0")
        with mock.patch.object(pb, "MAX_DOWNLOAD", 100):
            with self.assertRaises(pb.UpdateError):
                pb.download_release(self.settings(), "v3.1.0", os.path.join(self.tmp, "dl"))
        self.assertEqual(pb.MAX_DOWNLOAD, 20 * 1024 * 1024)

    def test_latest_download_uses_the_latest_path(self):
        self.assertEqual(pb.release_url(self.settings(), None, "sinko.tar.gz"), self.site.base + "/latest/download/sinko.tar.gz")
        self.assertEqual(pb.release_url(self.settings(), "v3.1.0", "x"), self.site.base + "/download/v3.1.0/x")


class UnpackTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name
        self.dest = os.path.join(self.tmp, "stage")
        os.mkdir(self.dest)
        self.sentinel = os.path.join(self.tmp, "outside")

    def unpack(self, data):
        path = os.path.join(self.tmp, "r.tar.gz")
        with open(path, "wb") as fh:
            fh.write(data)
        return pb.unpack_release(path, self.dest)

    def good_members(self, version="3.1.0", **kw):
        members = [fake_release.dir_member("sinko")]
        for name, (data, mode) in sorted(fake_release.release_files(version, **kw).items()):
            members.append(fake_release.file_member(name, data, mode))
        return members

    def bad(self, extra, drop=(), why=""):
        members = [m for m in self.good_members() if m[0].name not in drop] + extra
        with self.assertRaises(pb.UpdateError, msg=why) as ctx:
            self.unpack(fake_release.tar_bytes(members))
        self.assertFalse(os.path.exists(self.sentinel), why + ": something was written outside the folder")
        self.assertRegex(str(ctx.exception), r"^The download is not a valid Sinko release")
        return ctx.exception

    def test_a_good_release_unpacks_with_safe_modes(self):
        self.assertEqual(self.unpack(fake_release.build_release("3.1.0")), "3.1.0")
        top = os.path.join(self.dest, "sinko")
        for name in ("VERSION", "install.sh", "bin/sinko", "lists/services.json", "web/index.html", "LICENSE"):
            self.assertTrue(os.path.isfile(os.path.join(top, name)), name)
        mode = lambda p: stat.S_IMODE(os.stat(os.path.join(top, p)).st_mode)
        self.assertEqual(mode("install.sh"), 0o755)
        self.assertEqual(mode("VERSION"), 0o644)
        self.assertEqual(mode("bin"), 0o755)
        self.assertEqual(mode("."), 0o755)

    def test_setuid_and_group_writable_bits_are_not_kept(self):
        members = self.good_members()
        members.append(fake_release.file_member("sinko/tools-suid", b"x", 0o4777))
        members.append(fake_release.file_member("sinko/loose", b"x", 0o666))
        self.unpack(fake_release.tar_bytes(members))
        top = os.path.join(self.dest, "sinko")
        self.assertEqual(stat.S_IMODE(os.stat(os.path.join(top, "tools-suid")).st_mode), 0o755)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.join(top, "loose")).st_mode), 0o644)

    def test_absolute_and_dotdot_names_are_refused(self):
        self.bad([fake_release.file_member(self.sentinel, b"x")], why="absolute")
        self.bad([fake_release.file_member("/" + self.sentinel, b"x")], why="double slash")
        self.bad([fake_release.file_member("sinko/../../outside", b"x")], why="dotdot")
        self.bad([fake_release.file_member("sinko/a/../../../outside", b"x")], why="deep dotdot")
        self.bad([fake_release.file_member("../outside", b"x")], why="leading dotdot")
        self.bad([fake_release.dir_member("sinko/../../outside")], why="dotdot folder")

    def test_every_kind_of_link_and_special_file_is_refused(self):
        for kind, extra in (("symlink out", {"type": tarfile.SYMTYPE, "linkname": "/etc"}),
                            ("symlink up", {"type": tarfile.SYMTYPE, "linkname": "../.."}),
                            ("symlink inside", {"type": tarfile.SYMTYPE, "linkname": "VERSION"}),
                            ("hard link", {"type": tarfile.LNKTYPE, "linkname": "sinko/VERSION"}),
                            ("hard link out", {"type": tarfile.LNKTYPE, "linkname": "/etc/passwd"}),
                            ("char device", {"type": tarfile.CHRTYPE}), ("block device", {"type": tarfile.BLKTYPE}),
                            ("fifo", {"type": tarfile.FIFOTYPE})):
            info = tarfile.TarInfo("sinko/evil")
            for key, value in extra.items():
                setattr(info, key, value)
            self.bad([(info, None)], why=kind)
            shutil.rmtree(self.dest)
            os.mkdir(self.dest)

    def test_a_file_cannot_be_written_through_an_earlier_link(self):
        link = tarfile.TarInfo("sinko/x")
        link.type = tarfile.SYMTYPE
        link.linkname = os.path.dirname(self.sentinel)
        self.bad([(link, None), fake_release.file_member("sinko/x/outside", b"payload")], why="write through a link")

    def test_everything_must_live_under_the_one_top_folder(self):
        self.bad([fake_release.file_member("other/file", b"x")], why="other folder")
        self.bad([fake_release.file_member("sinkox/file", b"x")], why="similar name")
        self.bad([fake_release.file_member("file", b"x")], why="top level file")

    def test_required_files_must_be_there(self):
        for name in pb.REQUIRED_MEMBERS:
            self.bad([], drop=(name,), why=name)
            shutil.rmtree(self.dest)
            os.mkdir(self.dest)

    def test_a_required_name_that_is_a_folder_does_not_count(self):
        self.bad([fake_release.dir_member("sinko/install.sh")], drop=("sinko/install.sh",), why="folder")

    def test_a_duplicate_name_is_refused(self):
        self.bad([fake_release.file_member("sinko/install.sh", b"#!/bin/sh\necho second\n", 0o755)], why="duplicate")

    def test_the_version_file_must_be_a_version(self):
        for text in (b"latest\n", b"3.1\n", b"", b"3.1.0; rm -rf /\n", b"x" * 500):
            self.bad([fake_release.file_member("sinko/VERSION", text)], drop=("sinko/VERSION",), why=repr(text))
            shutil.rmtree(self.dest)
            os.mkdir(self.dest)

    def test_too_many_files_and_too_much_data_are_refused(self):
        with mock.patch.object(pb, "MAX_MEMBERS", 5):
            self.bad([], why="member count")
        shutil.rmtree(self.dest)
        os.mkdir(self.dest)
        with mock.patch.object(pb, "MAX_UNPACKED", 50):
            self.bad([], why="total size")

    def test_a_tarball_that_inflates_far_beyond_its_size_is_stopped(self):
        # 40 MB of zeros compresses to a few KB: the header says so, and the reader would stop it anyway.
        members = self.good_members() + [fake_release.file_member("sinko/zeros", b"\0" * (40 << 20))]
        data = fake_release.tar_bytes(members)
        self.assertLess(len(data), 200 * 1024)
        with mock.patch.object(pb, "MAX_UNPACKED", 1 << 20):
            with self.assertRaises(pb.UpdateError):
                self.unpack(data)
        reader = pb._CappedReader(io.BytesIO(b"x" * 100), 10)
        with self.assertRaises(pb.UpdateError):
            reader.read(-1)
        self.assertEqual(pb._CappedReader(io.BytesIO(b"x" * 100), 100).read(None), b"x" * 100)

    def test_damaged_input_is_an_update_error_not_a_traceback(self):
        good = fake_release.build_release("3.1.0")
        for label, data in (("truncated", good[:len(good) // 2]), ("not gzip", b"hello world"), ("empty", b""),
                            ("plain tar", fake_release.tar_bytes(self.good_members(), gz=False)),
                            ("corrupt", good[:100] + bytes(b ^ 0xFF for b in good[100:140]) + good[140:])):
            with self.assertRaises(pb.UpdateError, msg=label):
                self.unpack(data)
            shutil.rmtree(self.dest)
            os.mkdir(self.dest)

    def test_the_dot_folder_entry_that_tar_adds_when_archiving_dot_is_harmless(self):
        members = [fake_release.dir_member("./")] + self.good_members()
        self.assertEqual(self.unpack(fake_release.tar_bytes(members)), "3.1.0")

    def test_it_works_on_every_python_the_box_could_have(self):
        script = (
            "import importlib.machinery, importlib.util, os, sys, tarfile\n"
            "loader = importlib.machinery.SourceFileLoader('s', sys.argv[1])\n"
            "m = importlib.util.module_from_spec(importlib.util.spec_from_loader('s', loader)); loader.exec_module(m)\n"
            "assert m.unpack_release(sys.argv[2], sys.argv[3]) == '3.1.0'\n"
            "try:\n"
            "    m.unpack_release(sys.argv[4], sys.argv[5])\n"
            "except m.UpdateError:\n"
            "    print('ok', sys.version_info[:2])\n"
            "else:\n"
            "    raise SystemExit('a hostile tarball was accepted')\n")
        good = os.path.join(self.tmp, "good.tar.gz")
        evil = os.path.join(self.tmp, "evil.tar.gz")
        with open(good, "wb") as fh:
            fh.write(fake_release.build_release("3.1.0"))
        link = tarfile.TarInfo("sinko/x")
        link.type = tarfile.SYMTYPE
        link.linkname = "/etc"
        with open(evil, "wb") as fh:
            fh.write(fake_release.tar_bytes(self.good_members() + [(link, None)]))
        ran = []
        for minor in range(9, 15):
            exe = shutil.which("python3.%d" % minor)
            if not exe:
                continue
            for name in ("good", "evil"):
                shutil.rmtree(os.path.join(self.tmp, name + "-dest"), ignore_errors=True)
                os.mkdir(os.path.join(self.tmp, name + "-dest"))
            done = subprocess.run([exe, "-c", script, os.path.join(ROOT, "bin", "sinko"), good,
                                   os.path.join(self.tmp, "good-dest"), evil, os.path.join(self.tmp, "evil-dest")],
                                  capture_output=True, text=True)
            self.assertEqual(done.returncode, 0, "python3.%d: %s%s" % (minor, done.stdout, done.stderr))
            ran.append(minor)
        self.assertTrue(ran, "no python3.9-3.14 found to test with")


class CacheTests(Box):
    def put(self, *versions):
        os.makedirs(pb.cache_dir(), exist_ok=True)
        for v in versions:
            with open(pb.cache_file(v), "wb") as fh:
                fh.write(b"x")

    def test_versions_are_listed_newest_first_and_only_real_ones(self):
        self.put("3.9.0", "3.10.0", "3.0.0")
        for junk in ("sinko-3.1.tar.gz", "sinko-3.1.0.tar.gz.sha256", "other.tar.gz", ".dl-123.tar.gz"):
            with open(os.path.join(pb.cache_dir(), junk), "wb"):
                pass
        self.assertEqual(pb.cached_versions(), ["3.10.0", "3.9.0", "3.0.0"])

    def test_no_cache_folder_is_no_versions(self):
        self.assertEqual(pb.cached_versions(), [])

    def test_prune_keeps_the_installed_release_and_the_newest_older_one(self):
        self.put("3.2.0", "3.1.0", "3.0.0", "2.9.0")
        pb.prune_cache("3.2.0")
        self.assertEqual(pb.cached_versions(), ["3.2.0", "3.1.0"])

    def test_prune_after_a_rollback_keeps_the_installed_release_not_just_the_newest(self):
        self.put("3.1.0", "3.0.0", "2.9.0")
        pb.prune_cache("3.0.0")                       # a later rollback still has 2.9.0 to go to
        self.assertEqual(pb.cached_versions(), ["3.0.0", "2.9.0"])

    def test_prune_never_drops_the_installed_release_even_when_it_is_the_oldest(self):
        self.put("3.2.0", "3.1.0", "3.0.0")
        pb.prune_cache("3.0.0")
        self.assertEqual(pb.cached_versions(), ["3.2.0", "3.0.0"], "the installed one and the newest other one")

    def test_prune_with_the_installed_release_not_cached_keeps_the_newest_older_one(self):
        self.put("2.9.0", "2.8.0")
        pb.prune_cache("3.0.0")
        self.assertEqual(pb.cached_versions(), ["2.9.0"])


class UpdateFlowTests(Box):
    def test_update_installs_the_new_release_and_stores_both_for_rollback(self):
        self.publish("3.1.0")
        self.site.add_release("3.0.0")                 # the release this box runs: fetched once as the rollback copy
        result = self.updater().update()
        self.assertEqual((result["status"], result["from"], result["to"], result["error"]), ("ok", "3.0.0", "3.1.0", None))
        self.assertEqual(self.installed(), "3.1.0")
        self.assertEqual(self.installs(), ["install 3.1.0 src=%s noninteractive=1 ref=latest" % os.path.join(self.app, "src")])
        self.assertEqual(pb.read_update_result(), result)
        self.assertEqual(pb.cached_versions(), ["3.1.0", "3.0.0"])
        self.assertEqual(self.leftovers(), [])
        src = os.path.join(self.app, "src")
        self.assertEqual(self.read(os.path.join(src, "VERSION")), "3.1.0")
        self.assertTrue(os.access(os.path.join(src, "install.sh"), os.X_OK))
        self.assertFalse(os.path.exists(src + ".old"))
        self.assertFalse(pb.RunLock.is_held(), "the lock is released")
        self.assertTrue(any("now at version 3.1.0" in l for l in self.lines), self.lines)

    def test_the_download_comes_from_the_tag_when_the_version_is_known(self):
        self.publish("3.1.0")
        self.site.add_release("3.0.0")
        self.updater().update()
        self.assertEqual(self.site.count("/releases/download/v3.1.0/sinko.tar.gz"), 2, "tarball and checksum")
        self.assertEqual(self.site.count("/latest/download/"), 0)

    def test_when_the_box_is_up_to_date_nothing_is_downloaded_or_installed(self):
        self.publish("3.0.0")
        result = self.updater().update()
        self.assertEqual((result["status"], result["from"], result["to"]), ("ok", "3.0.0", "3.0.0"))
        self.assertEqual(self.site.count("sinko.tar.gz"), 0)
        self.assertEqual(self.installs(), [])
        self.assertEqual(pb.read_update_result()["status"], "ok")

    def test_no_published_release_is_not_an_error(self):
        self.site.set_api([self.site.release_json("3.5.0", prerelease=True)])
        result = self.updater().update()
        self.assertEqual((result["status"], result["to"]), ("ok", "3.0.0"))
        self.assertTrue(any("No release" in l for l in self.lines))

    def test_force_installs_even_when_it_is_not_newer(self):
        self.publish("3.0.0")
        result = self.updater().update(force=True)
        self.assertEqual((result["status"], result["to"]), ("ok", "3.0.0"))
        self.assertEqual(len(self.installs()), 1)

    def test_a_pinned_release_is_installed_and_the_installer_is_told_the_pin(self):
        self.publish("3.2.0", latest=False)
        self.site.add_release("3.0.0")
        result = self.updater().update(ref="v3.2.0")
        self.assertEqual(result["to"], "3.2.0")
        self.assertIn("ref=v3.2.0", self.installs()[0])
        self.assertEqual(self.site.count("/api/latest"), 0, "a pin needs no metadata")

    def test_an_older_pin_is_refused_unless_forced(self):
        self.site.add_release("2.9.0")
        result = self.updater().update(ref="v2.9.0")
        self.assertEqual((result["status"], result["to"]), ("ok", "3.0.0"))
        self.assertEqual(self.installs(), [])
        self.assertTrue(any("--force" in l for l in self.lines), self.lines)
        result = self.updater().update(ref="v2.9.0", force=True)
        self.assertEqual((result["status"], result["to"]), ("ok", "2.9.0"))
        self.assertEqual(self.installed(), "2.9.0")

    def test_the_installer_failing_puts_the_previous_version_back(self):
        self.publish("3.1.0", install_ok=False)
        self.site.add_release("3.0.0")
        result = self.updater().update()
        self.assertEqual((result["status"], result["from"], result["to"]), ("failed", "3.0.0", "3.1.0"))
        self.assertIn("installer stopped", result["error"])
        self.assertIn("previous version (3.0.0) was put back", result["error"])
        self.assertLessEqual(len(result["error"]), 200)
        self.assertEqual([l.split()[1] for l in self.installs()], ["3.1.0", "3.0.0"])
        self.assertEqual(self.installed(), "3.0.0")
        self.assertEqual(pb.cached_versions(), ["3.0.0"], "a release that failed is not kept")
        self.assertEqual(self.read(os.path.join(self.app, "src", "VERSION")), "3.0.0")
        self.assertEqual(pb.read_update_result(), result)

    def test_a_failing_selfcheck_of_the_new_version_rolls_back_too(self):
        self.publish("3.1.0", selfcheck_ok=False)
        self.site.add_release("3.0.0")
        result = self.updater().update()
        self.assertEqual(result["status"], "failed")
        self.assertIn("did not pass its own check (the fake page is broken)", result["error"])
        self.assertIn("put back", result["error"])
        self.assertEqual([l.split()[1] for l in self.installs()], ["3.1.0", "3.0.0"])
        self.assertEqual(self.installed(), "3.0.0")

    def test_the_selfcheck_that_judges_the_update_is_the_one_that_was_just_installed(self):
        calls = []
        real = pb.run_installed_selfcheck

        def spy():
            with open(os.path.join(self.app, "VERSION")) as fh:
                calls.append(fh.read().strip())
            return real()
        self.publish("3.1.0")
        self.site.add_release("3.0.0")
        self.updater(selfcheck=spy).update()
        self.assertEqual(calls, ["3.1.0"], "the new program is already in place when it is judged")

    def test_without_an_earlier_copy_a_failed_update_says_so(self):
        self.publish("3.1.0", install_ok=False)         # v3.0.0 is not on the fake GitHub: no rollback copy can be fetched
        result = self.updater().update()
        self.assertEqual(result["status"], "failed")
        self.assertIn("no earlier copy", result["error"])
        self.assertEqual([l.split()[1] for l in self.installs()], ["3.1.0"])
        self.assertTrue(any(l.startswith("Note: no copy of the current version") for l in self.lines), self.lines)

    def test_a_rollback_that_fails_as_well_points_to_doctor(self):
        self.publish("3.1.0", install_ok=False)
        self.site.add_release("3.0.0", install_ok=False)
        result = self.updater().update()
        self.assertEqual(result["status"], "failed")
        self.assertIn("did not work either", result["error"])
        self.assertIn("sinko doctor", result["error"])

    def test_a_rollback_copy_already_in_the_cache_is_used_without_downloading(self):
        self.site.add_release("3.0.0")
        os.makedirs(pb.cache_dir(), exist_ok=True)
        shutil.copy(self._write_tarball("3.0.0"), pb.cache_file("3.0.0"))
        self.publish("3.1.0", install_ok=False)
        result = self.updater().update()
        self.assertIn("put back", result["error"])
        self.assertEqual(self.site.count("/releases/download/v3.0.0/"), 0)

    def _write_tarball(self, version):
        path = os.path.join(self.tmp, "t-%s.tar.gz" % version)
        with open(path, "wb") as fh:
            fh.write(fake_release.build_release(version))
        return path

    def test_a_checksum_mismatch_installs_nothing(self):
        self.publish("3.1.0", sha=("0" * 64 + "  sinko.tar.gz\n").encode())
        result = self.updater().update()
        self.assertEqual(result["status"], "failed")
        self.assertIn("checksum", result["error"])
        self.assertEqual(self.installs(), [])
        self.assertEqual(self.installed(), "3.0.0")
        self.assertEqual(pb.cached_versions(), [])
        self.assertEqual(self.leftovers(), [])

    def test_a_missing_checksum_file_installs_nothing(self):
        self.publish("3.1.0", sha=False)
        self.assertEqual(self.updater().update()["status"], "failed")
        self.assertEqual(self.installs(), [])

    def test_a_tarball_that_names_another_version_than_its_tag_is_refused(self):
        self.publish("3.1.0", tarball=fake_release.build_release("3.2.0"))
        result = self.updater().update()
        self.assertEqual(result["status"], "failed")
        self.assertIn("version 3.2.0, not 3.1.0", result["error"])
        self.assertEqual(self.installs(), [])

    def test_a_hostile_tarball_is_refused_and_leaves_no_staging_folder(self):
        link = tarfile.TarInfo("sinko/evil")
        link.type = tarfile.SYMTYPE
        link.linkname = "/etc"
        members = [fake_release.dir_member("sinko")] + [
            fake_release.file_member(n, d, m) for n, (d, m) in fake_release.release_files("3.1.0").items()] + [(link, None)]
        self.publish("3.1.0", tarball=fake_release.tar_bytes(members))
        result = self.updater().update()
        self.assertEqual(result["status"], "failed")
        self.assertIn("not a valid Sinko release", result["error"])
        self.assertEqual(self.installs(), [])
        self.assertEqual(self.leftovers(), [])
        self.assertFalse(os.path.exists(os.path.join(self.app, "src")))

    def test_when_the_metadata_cannot_be_read_the_latest_download_is_used(self):
        self.site.set_api({}, status=403)               # rate limited
        self.site.add_release("3.1.0")
        self.site.make_latest("3.1.0")
        self.site.add_release("3.0.0")
        result = self.updater().update()
        self.assertEqual((result["status"], result["to"]), ("ok", "3.1.0"))
        self.assertGreaterEqual(self.site.count("/releases/latest/download/sinko.tar.gz"), 1)
        self.assertTrue(any("trying the latest download" in l for l in self.lines), self.lines)

    def test_the_latest_download_that_is_not_newer_changes_nothing(self):
        self.site.set_api({}, status=500)
        self.site.add_release("3.0.0")
        self.site.make_latest("3.0.0")
        result = self.updater().update()
        self.assertEqual((result["status"], result["to"]), ("ok", "3.0.0"))
        self.assertEqual(self.installs(), [])
        self.assertEqual(pb.cached_versions(), [])
        self.assertEqual(self.leftovers(), [])

    def test_everything_down_is_a_failed_result_with_a_readable_reason(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        result = self.updater().update()
        self.assertEqual(result["status"], "failed")
        self.assertTrue(result["error"])
        self.assertLessEqual(len(result["error"]), 200)
        self.assertNotIn("Traceback", result["error"])
        self.assertEqual(pb.read_update_result()["status"], "failed")

    def test_two_updates_never_run_at_once(self):
        self.publish("3.1.0")
        pb.write_update_result("ok", "2.0.0", "3.0.0", None, 1.0)
        holder = pb.RunLock()
        self.assertTrue(holder.acquire())
        try:
            with self.assertRaises(pb.UpdateLocked):
                self.updater().update()
            with self.assertRaises(pb.UpdateLocked):
                self.updater().rollback()
        finally:
            holder.release()
        self.assertEqual(pb.read_update_result()["to"], "3.0.0", "the run that holds the lock owns the result file")
        self.assertEqual(self.site.count("/api/latest"), 0)

    def test_the_result_of_an_earlier_run_is_gone_as_soon_as_a_new_run_starts(self):
        pb.write_update_result("ok", "2.0.0", "3.0.0", None, 1.0)
        seen = []

        def installer(src, ref):
            seen.append(pb.read_update_result())
            return 0
        self.publish("3.1.0")
        self.site.add_release("3.0.0")
        self.updater(installer=installer, selfcheck=lambda: (True, "")).update()
        self.assertEqual(seen, [None])

    def test_an_unexpected_crash_is_still_reported_and_the_lock_released(self):
        self.publish("3.1.0")
        with mock.patch.object(pb, "unpack_release", side_effect=ValueError("boom")):
            with self.assertLogs("sinko", level="ERROR"):
                result = self.updater().update()
        self.assertEqual(result["status"], "failed")
        self.assertIn("ValueError", result["error"])
        self.assertNotIn("boom", result["error"])
        self.assertFalse(pb.RunLock.is_held())
        self.assertEqual(pb.read_update_result()["status"], "failed")

    def test_an_installer_that_cannot_start_counts_as_a_failure_and_rolls_back(self):
        calls = []

        def installer(src, ref):
            calls.append(src)
            if len(calls) == 1:
                raise OSError("no bash")
            return 0
        self.publish("3.1.0")
        self.site.add_release("3.0.0")
        with self.assertLogs("sinko", level="ERROR"):
            result = self.updater(installer=installer, selfcheck=lambda: (True, "")).update()
        self.assertIn("could not be started", result["error"])
        self.assertEqual(len(calls), 2)

    def test_declining_the_question_changes_nothing(self):
        self.publish("3.1.0")
        asked = []
        result = self.updater().update(confirm=lambda current, to: asked.append((current, to)) or False)
        self.assertIsNone(result)
        self.assertEqual(asked, [("3.0.0", "3.1.0")])
        self.assertEqual(self.site.count("sinko.tar.gz"), 0)
        self.assertIsNone(pb.read_update_result())
        self.assertFalse(pb.RunLock.is_held())

    def test_too_little_disk_space_stops_before_downloading(self):
        self.publish("3.1.0")
        with mock.patch.object(pb, "MIN_FREE_BYTES", 1 << 60):
            result = self.updater().update()
        self.assertEqual(result["status"], "failed")
        self.assertIn("free space", result["error"])
        self.assertEqual(self.site.count("sinko.tar.gz"), 0)

    def test_leftovers_of_a_cut_off_run_are_cleaned_up(self):
        os.makedirs(os.path.join(self.app, ".stage-old", "sinko"))
        os.makedirs(pb.cache_dir())
        with open(os.path.join(pb.cache_dir(), ".dl-old.tar.gz"), "wb") as fh:
            fh.write(b"half")
        self.publish("3.0.0")
        self.updater().update()
        self.assertEqual(self.leftovers(), [])

    def test_an_update_never_leaves_a_downloaded_file_readable_by_others(self):
        self.publish("3.1.0")
        self.site.add_release("3.0.0")
        self.updater().update()
        self.assertEqual(stat.S_IMODE(os.stat(self.state).st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(os.stat(pb.cache_dir()).st_mode), 0o700)


class RollbackTests(Box):
    def test_rollback_reinstalls_the_newest_older_release_from_the_cache(self):
        self.publish("3.1.0")
        self.site.add_release("3.0.0")
        self.updater().update()
        self.assertEqual(self.installed(), "3.1.0")
        with mock.patch.object(pb, "VERSION", "3.1.0"):
            result = self.updater().rollback()
        self.assertEqual((result["status"], result["from"], result["to"], result["error"]), ("ok", "3.1.0", "3.0.0", None))
        self.assertEqual(self.installed(), "3.0.0")
        self.assertEqual([l.split()[1] for l in self.installs()], ["3.1.0", "3.0.0"])
        self.assertEqual(pb.cached_versions(), ["3.1.0", "3.0.0"], "a rollback keeps both, so the update can be redone")

    def test_rollback_without_an_older_copy_says_so(self):
        result = self.updater().rollback()
        self.assertEqual(result["status"], "failed")
        self.assertIn("No earlier version", result["error"])
        self.assertEqual(self.installs(), [])

    def test_a_damaged_stored_copy_is_not_installed(self):
        os.makedirs(pb.cache_dir())
        with open(pb.cache_file("2.9.0"), "wb") as fh:
            fh.write(b"not a tarball")
        result = self.updater().rollback()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(self.installs(), [])

    def test_a_stored_copy_that_is_another_version_than_its_name_is_not_installed(self):
        os.makedirs(pb.cache_dir())
        shutil.copy(self._tarball("2.8.0"), pb.cache_file("2.9.0"))
        result = self.updater().rollback()
        self.assertEqual(result["status"], "failed")
        self.assertIn("damaged", result["error"])
        self.assertEqual(self.installs(), [])

    def _tarball(self, version):
        path = os.path.join(self.tmp, "t.tar.gz")
        with open(path, "wb") as fh:
            fh.write(fake_release.build_release(version))
        return path


class BranchPathTests(Box):
    """The developer path: `--ref master` fetches the repository with git."""

    def make_repo(self, version="3.1.0-dev"):
        self.repo = os.path.join(self.tmp, "repo")
        os.makedirs(self.repo, exist_ok=True)
        for name, (data, mode) in fake_release.release_files(version.split("-")[0]).items():
            path = os.path.join(self.repo, name.split("/", 1)[1])
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as fh:
                fh.write(data)
            os.chmod(path, mode)
        self.git("init", "-q", "-b", "master")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "x")

    def git(self, *args):
        env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.org",
                   GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.org")
        subprocess.run(["git", "-C", self.repo] + list(args), check=True, env=env, capture_output=True)

    def conf(self):
        return self.site.conf(SINKO_REPO=self.repo, SINKO_REF="master")

    def test_the_first_run_clones_and_installs_from_the_clone(self):
        self.make_repo()
        self.site.add_release("3.0.0")
        result = self.updater(conf=self.conf()).update()
        self.assertEqual((result["status"], result["to"]), ("ok", "3.1.0"))
        self.assertEqual(self.installs(), ["install 3.1.0 src=%s noninteractive=1 ref=master" % os.path.join(self.app, "src")])
        self.assertTrue(os.path.isdir(os.path.join(self.app, "src", ".git")))
        self.assertEqual(self.installed(), "3.1.0")

    def test_a_later_run_fetches_what_changed(self):
        self.make_repo()
        self.site.add_release("3.0.0")
        self.updater(conf=self.conf()).update()
        with open(os.path.join(self.repo, "VERSION"), "w") as fh:
            fh.write("3.2.0\n")
        self.git("commit", "-q", "-am", "next")
        result = self.updater(conf=self.conf()).update()
        self.assertEqual((result["status"], result["to"]), ("ok", "3.2.0"))
        self.assertEqual(self.installed(), "3.2.0")

    def test_a_branch_is_installed_even_when_its_version_number_has_not_changed(self):
        self.make_repo("3.0.0")
        self.site.add_release("3.0.0")
        result = self.updater(conf=self.conf()).update()
        self.assertEqual((result["status"], result["to"]), ("ok", "3.0.0"))
        self.assertEqual(len(self.installs()), 1)

    def test_a_branch_that_does_not_exist_fails_cleanly(self):
        self.make_repo()
        self.site.add_release("3.0.0")
        result = self.updater(conf=self.conf()).update(ref="no-such-branch")
        self.assertEqual(result["status"], "failed")
        self.assertIn("git could not fetch", result["error"])
        self.assertEqual(self.installs(), [])
        self.assertEqual(self.leftovers(), [])

    def test_a_ref_that_looks_like_an_option_never_reaches_git(self):
        self.make_repo()
        with mock.patch.object(pb.subprocess, "run") as run:
            result = self.updater(conf=self.conf()).update(ref="--upload-pack=touch /tmp/pwned")
        self.assertEqual(result["status"], "failed")
        run.assert_not_called()
        result = self.updater(conf=self.site.conf(SINKO_REPO="-oProxyCommand=x", SINKO_REF="master")).update()
        self.assertEqual(result["status"], "failed")

    def test_a_source_without_the_required_files_is_refused(self):
        self.make_repo()
        os.unlink(os.path.join(self.repo, "install.sh"))
        self.git("commit", "-q", "-am", "drop")
        result = self.updater(conf=self.conf()).update()
        self.assertEqual(result["status"], "failed")
        self.assertIn("install.sh", result["error"])
        self.assertEqual(self.installs(), [])

    def test_a_failed_branch_install_rolls_back_to_the_cached_release(self):
        self.make_repo()
        with open(os.path.join(self.repo, "install.sh"), "w") as fh:
            fh.write("#!/bin/sh\nexit 1\n")
        self.git("commit", "-q", "-am", "broken")
        self.site.add_release("3.0.0")
        result = self.updater(conf=self.conf()).update()
        self.assertEqual(result["status"], "failed")
        self.assertIn("put back", result["error"])
        self.assertEqual(self.installed(), "3.0.0")


class CommandTests(Box):
    """`sinko update`, `rollback`, `selfcheck` through argparse, the way a person or the scheduler runs them."""

    def run_cli(self, *args, stdin_tty=False):
        out, err = io.StringIO(), io.StringIO()
        stdin = mock.Mock()
        stdin.isatty.return_value = stdin_tty
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), mock.patch.object(sys, "stdin", stdin), \
                mock.patch.dict(os.environ, {"SINKO_RELEASE_BASE": self.site.base, "SINKO_RELEASE_API": self.site.api}):
            code = pb.main(list(args))
        return code, out.getvalue(), err.getvalue()

    def test_check_says_whether_a_newer_version_exists_and_changes_nothing(self):
        self.publish("3.1.0")
        code, out, _ = self.run_cli("update", "--check")
        self.assertEqual(code, 0)
        self.assertIn("Update available: Sinko 3.1.0 (this box has 3.0.0)", out)
        self.publish("3.0.0")
        code, out, _ = self.run_cli("update", "--check")
        self.assertEqual((code, out.strip()), (0, "Sinko 3.0.0 is the newest version."))
        self.assertEqual(self.site.count("sinko.tar.gz"), 0)
        self.assertEqual(self.installs(), [])
        self.assertIsNone(pb.read_update_result())

    def test_check_when_offline_fails_with_a_message(self):
        self.site.set_api({}, status=403)
        code, out, err = self.run_cli("update", "--check")
        self.assertEqual(code, 1)
        self.assertIn("could not check for updates", err)

    def test_check_on_a_developer_box_explains_there_is_no_release_to_check(self):
        code, out, _ = self.run_cli("update", "--check", "--ref", "master")
        self.assertEqual(code, 0)
        self.assertIn("developer branch 'master'", out)

    def test_without_yes_and_without_a_terminal_it_refuses_instead_of_hanging(self):
        self.publish("3.1.0")
        code, _, err = self.run_cli("update")
        self.assertEqual(code, 1)
        self.assertIn("--yes", err)
        self.assertEqual(self.site.count("sinko.tar.gz"), 0)

    def test_with_a_terminal_it_asks_and_n_means_no(self):
        self.publish("3.1.0")
        with mock.patch("builtins.input", return_value="n"):
            code, out, _ = self.run_cli("update", stdin_tty=True)
        self.assertEqual(code, 1)
        self.assertIn("Nothing was changed", out)
        self.assertEqual(self.installs(), [])
        with mock.patch("builtins.input", side_effect=EOFError):
            self.assertEqual(self.run_cli("update", stdin_tty=True)[0], 1)

    def test_yes_updates_and_the_exit_status_follows_the_result(self):
        self.publish("3.1.0")
        self.site.add_release("3.0.0")
        code, out, _ = self.run_cli("update", "--yes")
        self.assertEqual(code, 0)
        self.assertEqual(self.installed(), "3.1.0")
        self.assertIn("now at version 3.1.0", out)
        self.publish("3.2.0", install_ok=False)
        code, _, err = self.run_cli("update", "--yes", "--force")
        self.assertEqual(code, 1)
        self.assertIn("the update failed", err)

    def test_from_panel_never_asks_and_leaves_the_result_for_the_scheduler(self):
        self.publish("3.1.0")
        self.site.add_release("3.0.0")
        code, _, _ = self.run_cli("update", "--yes", "--from-panel")
        self.assertEqual(code, 0)
        self.assertEqual(pb.read_update_result()["to"], "3.1.0")
        self.publish("3.2.0")
        self.assertEqual(self.run_cli("update", "--from-panel")[0], 0, "--from-panel implies --yes")

    def test_an_update_that_is_already_running_is_reported(self):
        holder = pb.RunLock()
        holder.acquire()
        self.addCleanup(holder.release)
        code, _, err = self.run_cli("update", "--yes")
        self.assertEqual(code, 1)
        self.assertIn("already running", err)

    def test_a_bad_ref_is_refused_with_a_message(self):
        code, _, err = self.run_cli("update", "--yes", "--ref=--upload-pack=x")
        self.assertEqual(code, 1)
        self.assertIn("not a version name", err)

    def test_rollback_command(self):
        code, _, err = self.run_cli("rollback")
        self.assertEqual(code, 1)
        self.assertIn("No earlier version", err)
        os.makedirs(pb.cache_dir(), exist_ok=True)
        with open(pb.cache_file("2.9.0"), "wb") as fh:
            fh.write(fake_release.build_release("2.9.0"))
        with mock.patch.object(pb, "note_rollback") as note:
            code, out, _ = self.run_cli("rollback")
        self.assertEqual(code, 0)
        self.assertIn("back at version 2.9.0", out)
        note.assert_called_once_with("3.0.0", "2.9.0")
        self.assertEqual(self.installed(), "2.9.0")


class RollbackNoteTests(unittest.TestCase):
    """After a rollback by hand the box must not install the same release again the next night."""

    def setUp(self):
        self.httpd, self.store = mock_pihole.serve()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        self.api = pb.Api("http://127.0.0.1:%d" % self.httpd.server_port, password=mock_pihole.PASSWORD)
        self.api.login()
        pb.Controller(self.api, pb.load_catalog(LISTS), "https://lists.example/l").setup(run_gravity=False)
        env = mock.patch.dict(os.environ, {"SINKO_API_URL": "http://127.0.0.1:%d" % self.httpd.server_port})
        env.start()
        self.addCleanup(env.stop)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        with open(os.path.join(tmp.name, "pw"), "w") as fh:
            fh.write(mock_pihole.PASSWORD)
        patch = mock.patch.object(pb, "CLI_PW_FILE", os.path.join(tmp.name, "pw"))
        patch.start()
        self.addCleanup(patch.stop)

    def state(self):
        return pb.parse_state(next(g for g in self.store.groups if g["name"] == "pb-state")["comment"])

    def test_the_undone_release_is_recorded_as_failed_so_auto_update_skips_it(self):
        pb.note_rollback("3.1.0", "3.0.0")
        u = self.state()["update"]
        self.assertEqual((u["status"], u["to"], u["from"]), ("failed", "3.1.0", "3.0.0"))
        self.assertIn("by hand", u["error"])
        self.assertGreater(u["at"], time.time() - 60)

    def test_the_other_parts_of_the_state_are_kept(self):
        state = self.state()
        state["schedule"]["enabled"] = True
        state["update"]["auto"] = True
        pb.Controller(self.api, pb.load_catalog(LISTS)).write_state(state)
        pb.note_rollback("3.1.0", "3.0.0")
        self.assertTrue(self.state()["schedule"]["enabled"])
        self.assertTrue(self.state()["update"]["auto"])

    def test_pihole_being_down_is_not_an_error(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        with self.assertLogs("sinko", level="WARNING"):
            pb.note_rollback("3.1.0", "3.0.0")

    def test_patch_state_writes_only_when_something_changed(self):
        writes = self.store.writes
        self.assertFalse(pb.patch_state(self.api, lambda s: None))
        self.assertEqual(self.store.writes, writes)
        self.assertTrue(pb.patch_state(self.api, lambda s: s["setup"].update(done=True)))
        self.assertEqual(self.store.writes, writes + 1)
        self.assertTrue(self.state()["setup"]["done"])


class SelfcheckTests(unittest.TestCase):
    def setUp(self):
        self.httpd, self.store = mock_pihole.serve()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name
        api = pb.Api("http://127.0.0.1:%d" % self.httpd.server_port, password=mock_pihole.PASSWORD)
        api.login()
        self.catalog = pb.load_catalog(LISTS)
        pb.Controller(api, self.catalog, "https://lists.example/l").setup(run_gravity=False)
        self.web = os.path.join(self.tmp, "www")
        os.makedirs(os.path.join(self.web, "pb"))
        for name, text in (("index.html", "x"), ("pb/app.js", "x"), ("pb/version.txt", pb.VERSION + "\n")):
            with open(os.path.join(self.web, name), "w") as fh:
                fh.write(text)
        with open(os.path.join(self.tmp, "pw"), "w") as fh:
            fh.write(mock_pihole.PASSWORD)
        env = mock.patch.dict(os.environ, {"SINKO_API_URL": "http://127.0.0.1:%d" % self.httpd.server_port,
                                           "SINKO_WEBROOT": self.web})
        env.start()
        self.addCleanup(env.stop)
        for patch in (mock.patch.object(pb, "CLI_PW_FILE", os.path.join(self.tmp, "pw")),
                      mock.patch.object(pb, "unit_active", return_value=True)):
            patch.start()
            self.addCleanup(patch.stop)

    def run_check(self, **kw):
        lines = []
        code = pb.run_selfcheck(out=lines.append, catalog=self.catalog, **kw)
        return code, lines

    def test_a_healthy_box_passes_with_one_line_per_check(self):
        code, lines = self.run_check()
        self.assertEqual(code, 0, lines)
        self.assertEqual(len(lines), 6)
        self.assertTrue(all(l.startswith("  ok    ") for l in lines), lines)

    def test_a_missing_group_fails(self):
        self.store.groups[:] = [g for g in self.store.groups if g["name"] != "pb-svc-youtube"]
        code, lines = self.run_check()
        self.assertEqual(code, 1)
        self.assertTrue(any(l.startswith("  FAIL  all groups present (missing: pb-svc-youtube)") for l in lines), lines)

    def test_a_missing_list_fails(self):
        self.store.lists[:] = [l for l in self.store.lists if l["comment"] != "pb:guard"]
        code, lines = self.run_check()
        self.assertEqual(code, 1)
        self.assertTrue(any("every list registered (missing: guard)" in l and l.startswith("  FAIL") for l in lines), lines)

    def test_the_scheduler_not_running_fails(self):
        with mock.patch.object(pb, "unit_active", return_value=False):
            code, lines = self.run_check()
        self.assertEqual(code, 1)
        self.assertTrue(any(l.startswith("  FAIL  scheduler service") for l in lines), lines)

    def test_missing_page_files_and_a_stale_page_fail(self):
        os.unlink(os.path.join(self.web, "pb", "app.js"))
        code, lines = self.run_check()
        self.assertEqual(code, 1)
        self.assertTrue(any("page files installed" in l and "pb/app.js" in l and l.startswith("  FAIL") for l in lines), lines)
        with open(os.path.join(self.web, "pb", "app.js"), "w") as fh:
            fh.write("x")
        with open(os.path.join(self.web, "pb", "version.txt"), "w") as fh:
            fh.write("2.2.0\n")
        code, lines = self.run_check()
        self.assertEqual(code, 1)
        self.assertTrue(any("same version (page: 2.2.0, program: %s)" % pb.VERSION in l and l.startswith("  FAIL") for l in lines), lines)

    def test_an_unreachable_pihole_fails_every_check_that_needs_it(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        code, lines = self.run_check()
        self.assertEqual(code, 1)
        self.assertEqual(sum(l.startswith("  FAIL") for l in lines), 3, lines)
        self.assertEqual(len(lines), 6)

    def test_a_wrong_cli_password_fails(self):
        with open(os.path.join(self.tmp, "pw"), "w") as fh:
            fh.write("wrong")
        code, lines = self.run_check()
        self.assertEqual(code, 1)
        self.assertTrue(lines[0].startswith("  FAIL  Pi-hole API reachable"), lines)

    def test_waiting_repeats_a_failing_round_until_it_passes(self):
        answers = iter([False, False, True])
        sleeps = []
        with mock.patch.object(pb, "unit_active", side_effect=lambda name: next(answers)):
            code, lines = self.run_check(wait=60, sleep=sleeps.append, clock=iter([0, 1, 2, 3, 4, 5]).__next__)
        self.assertEqual(code, 0, lines)
        self.assertEqual(sleeps, [3, 3])

    def test_waiting_gives_up_when_the_time_is_up(self):
        sleeps = []
        with mock.patch.object(pb, "unit_active", return_value=False):
            code, _ = self.run_check(wait=10, sleep=sleeps.append, clock=iter([0, 4, 8, 12, 16]).__next__)
        self.assertEqual(code, 1)
        self.assertEqual(sleeps, [3, 3])

    def test_the_command_exits_with_the_result(self):
        with contextlib.redirect_stdout(io.StringIO()) as out, mock.patch.object(pb, "load_catalog", return_value=self.catalog):
            code = pb.main(["selfcheck"])
        self.assertEqual(code, 0, out.getvalue())

    def test_the_installed_program_judges_itself_in_a_new_process(self):
        # The same command the update engine runs, against a real program file.
        app = os.path.join(self.tmp, "app")
        os.makedirs(os.path.join(app, "bin"))
        shutil.copy(os.path.join(ROOT, "bin", "sinko"), os.path.join(app, "bin", "sinko"))
        os.symlink(LISTS, os.path.join(app, "lists"))
        with mock.patch.object(pb, "APP_DIR", app):
            env = dict(os.environ, SINKO_APP_DIR=app, PATH=self.fake_systemctl() + os.pathsep + os.environ["PATH"],
                       SINKO_CLI_PW_FILE=os.path.join(self.tmp, "pw"))
            done = subprocess.run([sys.executable, os.path.join(app, "bin", "sinko"), "selfcheck"], env=env,
                                  capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(done.stdout.count("  ok    "), 6, done.stdout)

    def fake_systemctl(self):
        folder = os.path.join(self.tmp, "fakebin")
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "systemctl"), "w") as fh:
            fh.write("#!/bin/sh\nexit 0\n")
        os.chmod(os.path.join(folder, "systemctl"), 0o755)
        return folder

    def test_the_post_install_check_helper_reports_the_first_failure(self):
        app = os.path.join(self.tmp, "app2")
        os.makedirs(os.path.join(app, "bin"))
        with mock.patch.object(pb, "APP_DIR", app):
            self.assertEqual(pb.run_installed_selfcheck(), (False, "the installed program is missing"))
            with open(os.path.join(app, "bin", "sinko"), "w") as fh:
                fh.write("import sys\nprint('  ok    a')\nprint('  FAIL  the page is old')\nsys.exit(1)\n")
            self.assertEqual(pb.run_installed_selfcheck(), (False, "the page is old"))
            with open(os.path.join(app, "bin", "sinko"), "w") as fh:
                fh.write("import sys\nprint('boom', file=sys.stderr)\nsys.exit(3)\n")
            self.assertEqual(pb.run_installed_selfcheck(), (False, "boom"))
            with open(os.path.join(app, "bin", "sinko"), "w") as fh:
                fh.write("pass\n")
            self.assertEqual(pb.run_installed_selfcheck(), (True, ""))


if __name__ == "__main__":
    unittest.main()
