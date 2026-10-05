"""The update engine: release check, download, verification, safe unpacking, install, self-check, rollback, and the
`sinko update|rollback|selfcheck` commands. Network access goes to a local HTTP server (tests/fake_release.py); the
installer and the installed program are small fakes inside the fake release, and they really run."""
import contextlib
import http.client
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
import time
import unittest
import urllib.error
import urllib.request
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

# A start page like the real one: it loads its own files from /pb/ (and links to Pi-hole's admin, which is not ours).
PAGE = ('<!doctype html><link rel="stylesheet" href="/pb/style.css"><script src="/pb/pb-core.js" defer></script>'
        '<script src="/pb/app.js" defer></script><a href="/admin/">advanced</a>')


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
        conf = {"SINKO_RELEASE_BASE": "https://elsewhere.example/rel", "SINKO_RELEASE_API": "https://elsewhere.example/api",
                "SINKO_REF": "v3.0.1"}
        s = pb.release_settings(conf)
        self.assertEqual((s["base"], s["api"], s["ref"]), ("http://127.0.0.1:1/rel", "https://elsewhere.example/api", "v3.0.1"))

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

    def test_the_version_that_is_put_back_is_told_the_ref_the_box_had_not_the_pin_that_failed(self):
        self.publish("3.2.0", latest=False, install_ok=False)
        self.site.add_release("3.0.0")
        result = self.updater().update(ref="v3.2.0")
        self.assertEqual((result["status"], result["rolledBack"]), ("failed", True))
        self.assertEqual([l.rsplit("ref=", 1)[1] for l in self.installs()], ["v3.2.0", "latest"])
        os.unlink(self.log_path)
        self.updater(conf=self.site.conf(SINKO_REF="v3.0.0")).update(ref="v3.2.0")
        self.assertEqual([l.rsplit("ref=", 1)[1] for l in self.installs()], ["v3.2.0", "v3.0.0"], "a pinned box goes back to its pin")

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

    def test_a_rollback_that_fails_as_well_says_so_and_is_not_reported_as_back(self):
        self.publish("3.1.0", install_ok=False)
        self.site.add_release("3.0.0", install_ok=False)
        result = self.updater().update()
        self.assertEqual(result["status"], "failed")
        self.assertIn("did not work either", result["error"])
        self.assertIs(result["rolledBack"], False)
        self.assertFalse(result["transient"])

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
        self.assertEqual([c for c in run.call_args_list if c[0][0][0] == "git"], [], "git was never started")
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


class OutcomeTests(Box):
    """What a run says about itself: `rolledBack` (the page tells the parent from it whether the old version is back),
    `transient` (the scheduler's retry rule), an error text that is never a command, and what is flushed when."""

    def raw_result(self):
        with open(pb.state_path("update-result.json")) as fh:
            return json.load(fh)

    def test_a_success_says_nothing_failed(self):
        self.publish("3.1.0")
        self.site.add_release("3.0.0")
        result = self.updater().update()
        self.assertEqual((result["rolledBack"], result["transient"]), (None, False))
        self.assertEqual(self.raw_result()["rolledBack"], None)

    def test_the_previous_version_back_and_passing_its_check_is_the_only_true(self):
        self.publish("3.1.0", install_ok=False)
        self.site.add_release("3.0.0")
        result = self.updater().update()
        self.assertEqual((result["status"], result["rolledBack"], result["transient"]), ("failed", True, False))
        self.assertIs(self.raw_result()["rolledBack"], True)

    def test_a_new_version_that_fails_its_check_with_the_old_one_passing_is_true_too(self):
        self.publish("3.1.0", selfcheck_ok=False)
        self.site.add_release("3.0.0")
        self.assertIs(self.updater().update()["rolledBack"], True)

    def test_an_old_version_that_is_put_back_but_fails_its_own_check_is_not_true(self):
        self.publish("3.1.0", install_ok=False)
        self.site.add_release("3.0.0", selfcheck_ok=False)
        result = self.updater().update()
        self.assertIs(result["rolledBack"], False)
        self.assertIn("did not work either", result["error"])

    def test_with_nothing_to_go_back_to_it_is_false(self):
        self.publish("3.1.0", install_ok=False)                 # no copy of 3.0.0 on the fake GitHub
        self.assertIs(self.updater().update()["rolledBack"], False)

    def test_a_failure_before_anything_was_installed_is_true_only_when_the_box_passes_its_check(self):
        self.publish("3.1.0", sha=("0" * 64 + "  sinko.tar.gz\n").encode())
        checks = []

        def passes():
            checks.append(1)
            return True, ""
        result = self.updater(selfcheck=passes).update()
        self.assertEqual((result["status"], result["rolledBack"]), ("failed", True))
        self.assertEqual(checks, [1], "the box was looked at, not assumed to be fine")
        self.assertEqual(self.installs(), [])
        self.assertIs(self.updater(selfcheck=lambda: (False, "page is broken")).update()["rolledBack"], False)

        def explodes():
            raise RuntimeError("boom")
        self.assertIs(self.updater(selfcheck=explodes).update()["rolledBack"], False)

    def test_a_download_that_fails_is_transient_and_a_bad_release_is_not(self):
        self.site.set_api({}, status=500)
        result = self.updater().update(ref="v3.1.0")             # no such release on the fake GitHub: HTTP 404
        self.assertEqual((result["status"], result["transient"]), ("failed", True))
        self.assertEqual(self.raw_result()["transient"], True)
        self.publish("3.2.0", sha=("0" * 64 + "  sinko.tar.gz\n").encode())
        self.assertFalse(self.updater().update()["transient"], "a checksum that does not match: the release is the trouble")
        self.publish("3.3.0", install_ok=False)
        self.site.add_release("3.0.0")
        self.assertFalse(self.updater().update()["transient"], "an installer that fails: the release is the trouble")

    def test_a_full_disk_is_transient(self):
        self.publish("3.1.0")
        with mock.patch.object(pb.shutil, "disk_usage", return_value=mock.Mock(free=1024)):
            result = self.updater().update()
        self.assertEqual((result["status"], result["transient"]), ("failed", True))
        self.assertIn("not enough free space", result["error"])

    def test_an_unexpected_exception_after_the_install_is_false_and_before_it_is_checked(self):
        self.publish("3.1.0")
        self.site.add_release("3.0.0")
        with mock.patch.object(pb, "prune_cache", side_effect=RuntimeError("boom")), self.assertLogs("sinko", level="ERROR"):
            result = self.updater().update()
        self.assertEqual((result["status"], result["rolledBack"], result["transient"]), ("failed", False, False))
        self.assertIn("stopped unexpectedly (RuntimeError)", result["error"])
        with mock.patch.object(self.updater().__class__, "_resolve", side_effect=KeyError("x")), self.assertLogs("sinko", level="ERROR"):
            result = self.updater().update()
        self.assertEqual((result["status"], result["rolledBack"]), ("failed", True), "nothing was installed, and the box passes")

    def test_the_reason_is_never_a_command_or_a_hint_to_type_something(self):
        reasons = []
        self.publish("3.1.0", install_ok=False)                           # rolled back
        self.site.add_release("3.0.0")
        reasons.append(self.updater().update()["error"])
        reasons.append(self.updater().update()["error"])                  # again: the cache now holds 3.0.0
        self.publish("3.1.1", install_ok=False)
        self.site.add_release("3.0.0", install_ok=False)                   # rollback fails too
        shutil.rmtree(pb.cache_dir(), ignore_errors=True)
        reasons.append(self.updater().update()["error"])
        self.publish("3.1.2", install_ok=False)
        self.site.routes.pop("/releases/download/v3.0.0/sinko.tar.gz")      # nothing to go back to
        self.site.routes.pop("/releases/download/v3.0.0/sinko.tar.gz.sha256")
        shutil.rmtree(pb.cache_dir(), ignore_errors=True)
        reasons.append(self.updater().update()["error"])
        self.publish("3.1.3", selfcheck_ok=False)
        reasons.append(self.updater().update()["error"])
        self.publish("3.1.4", sha=("0" * 64 + "  sinko.tar.gz\n").encode())
        reasons.append(self.updater().update()["error"])
        reasons.append(self.updater().update(ref="v9.9.9")["error"])
        self.publish("3.1.5", tarball=b"not a tarball")
        reasons.append(self.updater().update()["error"])
        for reason in reasons:
            self.assertTrue(reason and len(reason) <= 200, reason)
            for banned in ("sudo", "sinko ", "run ", "systemctl", "Run:"):
                self.assertNotIn(banned, reason, reason)

    def test_the_cli_adds_the_hint_for_the_person_at_the_keyboard_but_not_the_stored_reason(self):
        self.publish("3.1.0", install_ok=False)
        self.site.add_release("3.0.0", install_ok=False)
        code, _, err = CommandTests.run_cli(self, "update", "--yes", "--from-panel")
        self.assertEqual(code, 1)
        self.assertIn("sudo sinko doctor", err)
        self.assertNotIn("doctor", pb.read_update_result()["error"])

    def test_everything_is_flushed_before_the_installer_starts_and_again_before_the_check(self):
        events = []
        self.publish("3.1.0")
        self.site.add_release("3.0.0")

        def installer(src, ref):
            events.append("install")
            return 0

        def check():
            events.append("check")
            return True, ""
        with mock.patch.object(pb, "sync_disks", lambda: events.append("sync")):
            self.updater(installer=installer, selfcheck=check).update()
        self.assertEqual(events, ["sync", "install", "sync", "check"])

    def test_a_downloaded_copy_is_flushed_and_its_folder_entry_too_before_it_is_relied_on(self):
        synced = []
        self.publish("3.1.0")
        self.site.add_release("3.0.0")
        with mock.patch.object(pb, "fsync_path", side_effect=synced.append), \
                mock.patch.object(pb, "_fsync_dir", side_effect=lambda d: synced.append(("dir", d))):
            self.updater(installer=lambda s, r: 0, selfcheck=lambda: (True, "")).update()
        files = [x for x in synced if isinstance(x, str)]
        self.assertEqual(len(files), 2, synced)                   # the rollback copy and the new release, flushed as files
        self.assertTrue(all(os.path.dirname(f) == pb.cache_dir() for f in files))
        self.assertEqual([x for x in synced if x == ("dir", pb.cache_dir())], [("dir", pb.cache_dir())] * 2,
                         "and their names in the folder, so that the rename survives a power cut")

    def test_a_scheduler_side_failure_notes_whether_the_next_try_may_come_soon(self):
        result = {"status": "failed", "to": "3.1.0", "at": 1234.5, "transient": True}
        pb.note_failure(result)
        self.assertTrue(pb.failure_is_transient({"at": 1234.5, "to": "3.1.0"}))
        self.assertFalse(pb.failure_is_transient({"at": 1234.5, "to": "3.2.0"}), "another release")
        self.assertFalse(pb.failure_is_transient({"at": 9999.0, "to": "3.1.0"}), "another run")
        pb.note_failure(dict(result, transient=False))
        self.assertFalse(pb.failure_is_transient({"at": 1234.5, "to": "3.1.0"}))
        pb.atomic_write(pb.state_path("update-failure.json"), "garbage")
        self.assertFalse(pb.failure_is_transient({"at": 1234.5, "to": "3.1.0"}))


MB = 1024 * 1024


class DiskSpaceTests(Box):
    """The installer asks for more than 200 MB for an update or a rollback and more than 1 GB for a first install; the
    engine asks for the first of those BEFORE it touches anything, and says so plainly. Between the two (it used to ask
    for 100 MB) a box downloaded, failed in the installer's first line, failed again in the rollback's, and reported a box
    it had never touched as one that could not be put back."""

    def free(self, *amounts):
        """shutil.disk_usage that answers with these free sizes (MB), the last one for every later question."""
        answers = list(amounts)
        return mock.patch.object(pb.shutil, "disk_usage",
                                 side_effect=lambda path: mock.Mock(free=(answers.pop(0) if len(answers) > 1 else answers[0]) * MB))

    def test_the_numbers_agree(self):
        self.assertEqual(pb.MIN_FREE_BYTES, 200 * MB, "what install.sh asks of an update or a rollback")
        self.assertEqual(pb.DISK_WARN_BYTES, 300 * MB, "the doctor warns before the engine refuses")
        self.assertGreater(pb.DISK_WARN_BYTES, pb.MIN_FREE_BYTES)

    def test_between_100_and_200_mb_it_stops_before_anything_is_downloaded_and_the_box_is_said_to_be_untouched(self):
        self.publish("3.1.0")
        self.site.add_release("3.0.0")
        checks = []
        with self.free(150):
            result = self.updater(selfcheck=lambda: checks.append(1) or (True, "")).update()
        self.assertEqual((result["status"], result["from"], result["rolledBack"], result["transient"]),
                         ("failed", "3.0.0", True, True))
        self.assertIn("not enough free space", result["error"])
        self.assertIn("150 MB are free and more than 200 MB are needed", result["error"])
        self.assertIn("Nothing on the box was changed", result["error"])
        self.assertLessEqual(len(result["error"]), 200)
        self.assertNotIn("sudo", result["error"])
        self.assertEqual(checks, [1], "the box as it is was checked, which is what makes rolledBack true")
        self.assertEqual(self.site.count("sinko.tar.gz"), 0, "nothing was downloaded")
        self.assertEqual(self.installs(), [], "and no installer was started, so there was nothing to roll back")
        self.assertEqual(self.installed(), "3.0.0")
        self.assertEqual(pb.read_update_result(), result)

    def test_rolled_back_comes_from_the_check_not_from_the_reason(self):
        self.publish("3.1.0")
        with self.free(150):
            result = self.updater(selfcheck=lambda: (False, "page is broken")).update()
        self.assertEqual((result["status"], result["rolledBack"], result["transient"]), ("failed", False, True))

    def test_200_mb_exactly_is_not_enough_and_a_little_more_is(self):
        self.publish("3.1.0")
        self.site.add_release("3.0.0")
        with self.free(200):
            self.assertEqual(self.updater().update()["status"], "failed")
        self.assertEqual(self.installs(), [])
        with mock.patch.object(pb.shutil, "disk_usage", return_value=mock.Mock(free=200 * MB + 1)):
            result = self.updater().update()
        self.assertEqual((result["status"], result["to"]), ("ok", "3.1.0"))

    def test_with_room_for_the_installer_the_update_goes_on(self):
        self.publish("3.1.0")
        self.site.add_release("3.0.0")
        with self.free(500):
            result = self.updater().update()
        self.assertEqual((result["status"], result["to"], result["error"]), ("ok", "3.1.0", None))
        self.assertEqual(len(self.installs()), 1)

    def test_room_that_the_downloads_themselves_used_up_is_noticed_before_the_installer_starts(self):
        # 500 MB at the start, 150 MB when the release has been downloaded, unpacked and stored (and the rollback copy too).
        self.publish("3.1.0")
        self.site.add_release("3.0.0")
        with self.free(500, 150):
            result = self.updater().update()
        self.assertEqual((result["status"], result["rolledBack"], result["transient"]), ("failed", True, True))
        self.assertIn("150 MB are free", result["error"])
        self.assertEqual(self.installs(), [], "the installer was never started: it would have refused, and so would the rollback")
        self.assertEqual(self.installed(), "3.0.0")
        self.assertNotIn("3.1.0", pb.cached_versions(), "a release that was not installed is no rollback target")
        self.assertEqual(self.leftovers(), [])

    def test_the_branch_path_checks_again_too(self):
        # (the same check, after the clone: git writes a whole checkout before the installer starts)
        installer_calls = []
        with mock.patch.object(pb.Updater, "_fetch_branch", return_value=(os.path.join(self.app, "src"), "3.1.0")), \
                self.free(500, 150):
            result = self.updater(installer=lambda s, r: installer_calls.append(1) or 0,
                                  selfcheck=lambda: (True, "")).update(ref="master")
        self.assertEqual((result["status"], result["rolledBack"]), ("failed", True))
        self.assertEqual(installer_calls, [])

    def test_a_rollback_by_hand_asks_for_the_same_room_and_changes_nothing_without_it(self):
        self.publish("3.1.0")
        self.site.add_release("3.0.0")
        self.updater().update()
        self.assertEqual(self.installed(), "3.1.0")
        installs = list(self.installs())
        with mock.patch.object(pb, "VERSION", "3.1.0"), self.free(150):
            result = self.updater().rollback()
        self.assertEqual(result["status"], "failed")
        self.assertIn("not enough free space", result["error"])
        self.assertEqual(self.installs(), installs)
        self.assertEqual(self.installed(), "3.1.0")
        with mock.patch.object(pb, "VERSION", "3.1.0"), self.free(500):
            self.assertEqual(self.updater().rollback()["status"], "ok")
        self.assertEqual(self.installed(), "3.0.0")

    def test_the_page_and_the_scheduler_see_a_failure_that_is_neither_alarming_nor_final(self):
        # transient: tried again within the hour, not after a week; rolledBack true: the page does not tell anybody to unplug the box.
        self.publish("3.1.0")
        with self.free(150):
            result = self.updater().update()
        self.assertIs(result["transient"], True)
        self.assertIs(result["rolledBack"], True)
        self.assertIsNone(result["to"], "it stopped before it knew which release (the scheduler keeps the one it asked for)")


class UpdateSourceTests(unittest.TestCase):
    """Where releases may come from: https, or this machine. The program and the page are replaced with what comes."""

    def setUp(self):
        env = mock.patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        for key in ENV_TO_CLEAR:
            os.environ.pop(key, None)

    def test_web_address(self):
        for good in ("https://github.com/a/b/releases", "https://api.github.com/repos/a/b/releases/latest",
                     "http://127.0.0.1:8080/releases", "http://localhost/x", "http://[::1]:9/x", "https://example.org:8443/r"):
            self.assertTrue(pb.web_address(good), good)
        for bad in ("http://github.com/a/b/releases", "http://192.168.1.5/releases", "http://127.0.0.1.evil.example/x",
                    "ftp://example.org/x", "file:///etc/passwd", "https://user:pw@example.org/x", "https://user@example.org/x",
                    "https://example.org/x#frag", "https:///nohost", "example.org/x", "", None, 5, "https://exa mple.org/x",
                    "https://example.org/x\\nHost: evil", "javascript:alert(1)", "https://example.org:notaport/"):
            self.assertFalse(pb.web_address(bad), repr(bad))

    def test_plain_http_to_another_machine_is_refused_in_the_settings_and_in_the_environment(self):
        for key in ("SINKO_RELEASE_BASE", "SINKO_RELEASE_API"):
            with self.assertRaises(pb.UpdateError) as caught:
                pb.release_settings({key: "http://mirror.lan/sinko"})
            self.assertIn("https://", str(caught.exception))
            os.environ[key] = "http://mirror.lan/sinko"
            with self.assertRaises(pb.UpdateError):
                pb.release_settings({})
            os.environ.pop(key)
        self.assertEqual(pb.release_settings({"SINKO_RELEASE_BASE": "https://mirror.example/sinko/"})["base"],
                         "https://mirror.example/sinko")
        self.assertEqual(pb.release_settings({"SINKO_RELEASE_BASE": "http://127.0.0.1:8080/r"})["base"], "http://127.0.0.1:8080/r")

    def test_a_base_with_a_query_is_refused_because_paths_are_added_to_it(self):
        with self.assertRaises(pb.UpdateError):
            pb.release_settings({"SINKO_RELEASE_BASE": "https://mirror.example/r?token=1"})
        pb.release_settings({"SINKO_RELEASE_API": "https://mirror.example/api?per_page=1"})

    def test_a_check_and_an_update_with_such_a_setting_never_touch_the_network(self):
        calls = []
        conf = {"SINKO_RELEASE_BASE": "http://mirror.lan/r", "SINKO_RELEASE_API": "http://mirror.lan/a"}
        with self.assertRaises(pb.UpdateError):
            pb.check_for_update(conf, fetch=lambda *a, **k: calls.append(a))
        self.assertEqual(calls, [])

    def test_the_fetch_function_itself_refuses_other_schemes(self):
        for url in ("file:///etc/passwd", "ftp://example.org/x", "http://mirror.lan/x"):
            with self.assertRaises(pb.UpdateError):
                pb.http_fetch(url, 100)

    def test_a_redirect_to_plain_http_or_another_scheme_is_refused_whatever_it_started_from(self):
        handler = pb._SafeRedirect()
        for old, new in (("https://github.com/x", "http://evil.example/x"), ("http://127.0.0.1:1/x", "http://evil.example/x"),
                         ("https://github.com/x", "file:///etc/passwd"), ("https://github.com/x", "ftp://evil.example/x")):
            request = urllib.request.Request(old)
            with self.assertRaises(urllib.error.HTTPError, msg=new):
                handler.redirect_request(request, None, 302, "Found", {}, new)
        request = urllib.request.Request("https://github.com/x")
        self.assertIsNotNone(handler.redirect_request(request, None, 302, "Found", {}, "https://objects.githubusercontent.com/y"))
        local = urllib.request.Request("http://127.0.0.1:1/x")
        self.assertIsNotNone(handler.redirect_request(local, None, 302, "Found", {}, "http://127.0.0.1:1/y"))


class RunLockHardeningTests(unittest.TestCase):
    """The updater's lock is in the state folder (root only), not in a folder every user can write to."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.state = os.path.join(tmp.name, "state")
        env = mock.patch.dict(os.environ, {"SINKO_STATE_DIR": self.state})
        env.start()
        self.addCleanup(env.stop)

    def test_the_lock_lives_in_the_private_state_folder_and_never_in_run_lock(self):
        lock = pb.RunLock()
        self.assertTrue(lock.acquire())
        self.addCleanup(lock.release)
        self.assertTrue(os.path.isfile(os.path.join(self.state, "lock")))
        self.assertEqual(stat.S_IMODE(os.stat(self.state).st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.join(self.state, "lock")).st_mode), 0o600)

    def test_a_link_in_place_of_the_lock_file_is_not_followed(self):
        pb.ensure_state_dir()
        target = os.path.join(os.path.dirname(self.state), "victim")
        with open(target, "w") as fh:
            fh.write("precious")
        os.symlink(target, os.path.join(self.state, "lock"))
        with self.assertRaises(OSError):
            pb.RunLock().acquire()
        with open(target) as fh:
            self.assertEqual(fh.read(), "precious")
        self.assertFalse(pb.RunLock.is_held(), "a lock that cannot be opened is not held by a runner")

    def test_a_folder_in_place_of_the_lock_file_is_refused(self):
        pb.ensure_state_dir()
        os.mkdir(os.path.join(self.state, "lock"))
        with self.assertRaises(OSError):
            pb.RunLock().acquire()

    def test_a_state_folder_that_belongs_to_somebody_else_is_refused(self):
        os.makedirs(self.state)
        real = os.geteuid()
        with mock.patch.object(pb.os, "geteuid", return_value=real + 1):
            with self.assertRaises(OSError):
                pb.ensure_state_dir()

    def test_an_update_cannot_start_when_the_lock_cannot_be_used_and_says_so(self):
        pb.ensure_state_dir()
        os.mkdir(os.path.join(self.state, "lock"))
        updater = pb.Updater(conf={}, out=lambda *_: None)
        with self.assertRaises(pb.UpdateError) as caught:
            updater.update()
        self.assertNotIsInstance(caught.exception, pb.UpdateLocked)
        self.assertEqual(updater.rollback()["status"], "failed")

    def test_the_lock_is_still_exclusive(self):
        first, second = pb.RunLock(), pb.RunLock()
        self.assertTrue(first.acquire())
        self.assertFalse(second.acquire())
        self.assertTrue(pb.RunLock.is_held())
        first.release()
        self.assertTrue(second.acquire())
        second.release()
        self.assertFalse(pb.RunLock.is_held())


class CommandTests(Box):
    """`sinko update`, `rollback`, `selfcheck` through argparse, the way a person or the scheduler runs them."""

    def run_cli(self, *args, stdin_tty=False):
        out, err = io.StringIO(), io.StringIO()
        stdin = mock.Mock()
        stdin.isatty.return_value = stdin_tty
        # main() refuses to run for anyone but root unless SINKO_API_URL is set (the test hook): CI runs the tests as an
        # ordinary user, so these commands get that hook too. Nothing in them talks to Pi-hole at that address.
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), mock.patch.object(sys, "stdin", stdin), \
                mock.patch.dict(os.environ, {"SINKO_RELEASE_BASE": self.site.base, "SINKO_RELEASE_API": self.site.api,
                                             "SINKO_API_URL": "http://127.0.0.1:1"}):
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

    def test_check_on_a_pinned_box_says_it_is_pinned_and_not_that_it_has_the_newest_version(self):
        self.publish("3.1.0")                                     # a newer release exists, but the box is pinned to its own
        with mock.patch.object(pb, "read_config", lambda *a: {"SINKO_REF": "v3.0.0"}):
            code, out, _ = self.run_cli("update", "--check")
        self.assertEqual(code, 0)
        self.assertNotIn("newest version", out)
        self.assertIn("pinned to v3.0.0", out)
        self.assertIn("sudo sinko update --ref latest", out)

    def test_check_with_an_explicit_older_release_does_not_claim_a_pin(self):
        code, out, _ = self.run_cli("update", "--check", "--ref", "v3.0.0")
        self.assertEqual(code, 0)
        self.assertIn("v3.0.0 is not newer", out)
        self.assertNotIn("pinned", out)

    def config_file(self, text):
        path = os.path.join(self.tmp, "config")
        with open(path, "w") as fh:
            fh.write(text)
        os.chmod(path, 0o644)
        return path

    def test_updating_to_a_chosen_release_says_the_box_is_pinned_and_how_to_follow_releases_again(self):
        path = self.config_file("SINKO_HOSTNAME=family.lan\nSINKO_REF=latest\n")
        self.publish("3.1.0", latest=False)
        self.site.add_release("3.0.0")
        with mock.patch.object(pb, "CONFIG_FILE", path):
            code, out, _ = self.run_cli("update", "--yes", "--ref", "v3.1.0")
        self.assertEqual(code, 0)
        self.assertIn("pinned to v3.1.0", out)
        self.assertIn("sudo sinko update --ref latest", out)
        with open(path) as fh:
            self.assertIn("SINKO_REF=v3.1.0\n", fh.read(), "saved: by the installer in real life, here by the command")

    def test_going_back_to_the_newest_release_unpins_even_when_it_is_already_installed(self):
        path = self.config_file("SINKO_REF=v3.0.0\nSINKO_IP=192.168.1.5\n")
        self.publish("3.0.0")
        with mock.patch.object(pb, "CONFIG_FILE", path):
            code, out, _ = self.run_cli("update", "--yes", "--ref", "latest")
        self.assertEqual(code, 0)
        self.assertEqual(self.installs(), [], "nothing needed installing")
        self.assertIn("follows the newest release again", out)
        with open(path) as fh:
            self.assertEqual(fh.read(), "SINKO_REF=latest\nSINKO_IP=192.168.1.5\n", "and nothing else in the file changed")

    def test_a_branch_says_it_is_never_offered_releases_and_how_to_leave_it(self):
        path = self.config_file("SINKO_REF=latest\n")
        with mock.patch.object(pb, "CONFIG_FILE", path):
            with mock.patch.object(pb.Updater, "update", return_value={"status": "ok", "error": None}):
                code, out, _ = self.run_cli("update", "--yes", "--ref", "feature/x")
        self.assertEqual(code, 0)
        self.assertIn("development branch 'feature/x'", out)
        self.assertIn("--ref latest --force", out)
        with open(path) as fh:
            self.assertEqual(fh.read(), "SINKO_REF=feature/x\n")

    def test_an_update_without_a_ref_never_changes_the_pin(self):
        path = self.config_file("SINKO_REF=v3.0.0\n")
        self.publish("3.0.0")
        with mock.patch.object(pb, "CONFIG_FILE", path):
            code, out, _ = self.run_cli("update", "--yes")
        self.assertEqual(code, 0)
        self.assertNotIn("pinned", out)
        with open(path) as fh:
            self.assertEqual(fh.read(), "SINKO_REF=v3.0.0\n")

    def test_a_failed_update_does_not_change_the_pin(self):
        path = self.config_file("SINKO_REF=latest\n")
        self.publish("3.1.0", install_ok=False)
        self.site.add_release("3.0.0")
        with mock.patch.object(pb, "CONFIG_FILE", path):
            code, out, _ = self.run_cli("update", "--yes", "--ref", "v3.1.0")
        self.assertEqual(code, 1)
        self.assertNotIn("pinned", out)

    # The fake installer saves the ref it is given in $SINKO_CONFIG_FILE before it can fail, as the real one does (write_settings
    # runs before the setup, the units and the self-check): so these runs show what is left in the settings.
    def failing_pin_run(self, config_text, *args, old_installs=True, **build):
        path = self.config_file(config_text)
        self.publish("3.1.0", latest=False, install_ok=False, **build)
        if old_installs:
            self.site.add_release("3.0.0")
        with mock.patch.object(pb, "CONFIG_FILE", path), mock.patch.dict(os.environ, {"SINKO_CONFIG_FILE": path}):
            code, out, err = self.run_cli("update", "--yes", *args)
        with open(path) as fh:
            return code, out, err, fh.read()

    def refs_given_to_the_installer(self):
        return [line.rsplit("ref=", 1)[1] for line in self.installs()]

    def test_a_failed_update_to_a_chosen_release_leaves_the_pin_as_it_was_after_the_rollback_installer_ran(self):
        code, out, err, config = self.failing_pin_run("SINKO_HOSTNAME=family.lan\nSINKO_REF=latest\n", "--ref", "v3.1.0")
        self.assertEqual(code, 1)
        self.assertEqual(self.refs_given_to_the_installer(), ["v3.1.0", "latest"],
                         "the version that is put back is told the ref the box had, not the one that failed")
        self.assertEqual(self.installed(), "3.0.0")
        self.assertIn("SINKO_REF=latest\n", config)
        self.assertNotIn("v3.1.0", config)
        self.assertIn("SINKO_HOSTNAME=family.lan\n", config, "nothing else in the file changed")
        self.assertIn("choice of v3.1.0 was not kept", err)
        self.assertIn("still follows the newest release", err)
        self.assertNotIn("pinned", out)

    def test_a_pinned_box_that_fails_to_move_to_another_release_stays_pinned_to_its_own(self):
        code, out, err, config = self.failing_pin_run("SINKO_REF=v3.0.0\n", "--ref", "v3.1.0")
        self.assertEqual(code, 1)
        self.assertEqual(self.refs_given_to_the_installer(), ["v3.1.0", "v3.0.0"])
        self.assertIn("SINKO_REF=v3.0.0\n", config)
        self.assertIn("still follows release v3.0.0", err)

    def test_without_a_copy_to_go_back_to_the_settings_are_put_back_by_the_command(self):
        # The new release's installer wrote its ref and died, and no installer ran after it: only keep_pin can undo that.
        code, out, err, config = self.failing_pin_run("SINKO_REF=latest\nSINKO_IP=192.168.1.5\n", "--ref", "v3.1.0",
                                                      old_installs=False)
        self.assertEqual(code, 1)
        self.assertEqual(self.refs_given_to_the_installer(), ["v3.1.0"], "no rollback installer ran")
        self.assertEqual(sorted(config.split()), ["SINKO_IP=192.168.1.5", "SINKO_REF=latest"])
        self.assertIn("still follows the newest release", err)

    def test_a_rollback_that_fails_too_leaves_the_pin_as_it_was_as_well(self):
        path = self.config_file("SINKO_REF=latest\n")
        self.publish("3.1.0", latest=False, install_ok=False)
        self.site.add_release("3.0.0", install_ok=False)
        with mock.patch.object(pb, "CONFIG_FILE", path), mock.patch.dict(os.environ, {"SINKO_CONFIG_FILE": path}):
            code, _, err = self.run_cli("update", "--yes", "--ref", "v3.1.0")
        self.assertEqual(code, 1)
        self.assertIn("did not work either", err)
        with open(path) as fh:
            self.assertEqual(fh.read().strip(), "SINKO_REF=latest")

    def test_a_missing_ref_line_means_the_newest_release_and_is_put_back_as_such(self):
        code, out, err, config = self.failing_pin_run("SINKO_HOSTNAME=family.lan\n", "--ref", "v3.1.0", old_installs=False)
        self.assertEqual(code, 1)
        self.assertIn("SINKO_REF=latest\n", config)
        self.assertNotIn("v3.1.0", config)

    def test_a_failure_before_the_installer_changes_nothing_in_the_settings_and_says_what_the_box_follows(self):
        path = self.config_file("SINKO_REF=latest\n")
        self.publish("3.1.0", latest=False, sha=("0" * 64 + "  sinko.tar.gz\n").encode())
        with mock.patch.object(pb, "CONFIG_FILE", path), mock.patch.dict(os.environ, {"SINKO_CONFIG_FILE": path}):
            code, _, err = self.run_cli("update", "--yes", "--ref", "v3.1.0")
        self.assertEqual(code, 1)
        self.assertEqual(self.installs(), [])
        with open(path) as fh:
            self.assertEqual(fh.read(), "SINKO_REF=latest\n")
        self.assertIn("still follows the newest release", err)

    def test_a_failed_update_without_a_ref_says_nothing_about_a_pin(self):
        path = self.config_file("SINKO_REF=latest\n")
        self.publish("3.1.0", install_ok=False)
        self.site.add_release("3.0.0")
        with mock.patch.object(pb, "CONFIG_FILE", path), mock.patch.dict(os.environ, {"SINKO_CONFIG_FILE": path}):
            code, _, err = self.run_cli("update", "--yes")
        self.assertEqual(code, 1)
        self.assertNotIn("choice of", err)
        self.assertEqual(self.refs_given_to_the_installer(), ["latest", "latest"])

    def test_a_branch_that_fails_leaves_a_release_pin_alone(self):
        path = self.config_file("SINKO_REF=v3.0.0\n")
        with mock.patch.object(pb, "CONFIG_FILE", path), \
                mock.patch.object(pb.Updater, "update", return_value={"status": "failed", "error": "git could not fetch x"}):
            code, _, err = self.run_cli("update", "--yes", "--ref", "feature/x")
        self.assertEqual(code, 1)
        with open(path) as fh:
            self.assertEqual(fh.read(), "SINKO_REF=v3.0.0\n")
        self.assertIn("choice of feature/x was not kept", err)
        self.assertIn("still follows release v3.0.0", err)

    def test_the_pin_that_cannot_be_put_back_is_said_not_raised(self):
        missing = os.path.join(self.tmp, "no-such-folder", "config")
        said = io.StringIO()
        with contextlib.redirect_stderr(said):
            pb.keep_pin("latest", "v3.1.0", missing)
        # (read_config of a missing file says "latest" = nothing to put back, so only the explanation is printed)
        self.assertIn("still follows the newest release", said.getvalue())
        path = self.config_file("SINKO_REF=v3.1.0\n")
        os.chmod(path, 0o444)
        with mock.patch.object(pb, "write_config_value", side_effect=OSError("read-only file system")):
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                pb.keep_pin("latest", "v3.1.0", path)
        self.assertIn("could not put the setting back to latest", err.getvalue())

    def test_a_missing_settings_file_is_said_not_a_traceback(self):
        missing = os.path.join(self.tmp, "no-config")
        self.publish("3.0.0")
        with mock.patch.object(pb, "CONFIG_FILE", missing):
            code, out, err = self.run_cli("update", "--yes", "--ref", "v3.0.0")
        self.assertEqual(code, 0)
        self.assertIn("could not save the choice of v3.0.0", err)

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
        self.httpd, self.store = fake_release.serve_pihole()
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
        self.httpd, self.store = fake_release.serve_pihole()
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
        for name, text in (("index.html", PAGE), ("pb/app.js", "x"), ("pb/pb-core.js", "x"), ("pb/style.css", "x"),
                           ("pb/services.json", "{}"), ("pb/version.txt", pb.VERSION + "\n")):
            with open(os.path.join(self.web, name), "w") as fh:
                fh.write(text)
        self.scheduler = ("ok", "the scheduler service has run for 300 s and keeps finishing its passes")
        with open(os.path.join(self.tmp, "pw"), "w") as fh:
            fh.write(mock_pihole.PASSWORD)
        env = mock.patch.dict(os.environ, {"SINKO_API_URL": "http://127.0.0.1:%d" % self.httpd.server_port,
                                           "SINKO_WEBROOT": self.web})
        env.start()
        self.addCleanup(env.stop)
        for patch in (mock.patch.object(pb, "CLI_PW_FILE", os.path.join(self.tmp, "pw")),
                      mock.patch.object(pb, "scheduler_status", lambda *a, **k: self.scheduler),
                      mock.patch.object(pb, "APP_DIR", os.path.join(self.tmp, "no-app"))):
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
        self.scheduler = ("stopped", "the scheduler service is not running")
        code, lines = self.run_check()
        self.assertEqual(code, 1)
        self.assertTrue(any(l.startswith("  FAIL  the scheduler service is not running") for l in lines), lines)

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

    def test_a_page_that_lacks_a_script_it_loads_fails(self):
        # A release packed without pb-core.js used to pass: the page then throws at load and shows nothing.
        os.unlink(os.path.join(self.web, "pb", "pb-core.js"))
        code, lines = self.run_check()
        self.assertEqual(code, 1)
        self.assertTrue(any("page files installed" in l and "pb/pb-core.js" in l and l.startswith("  FAIL") for l in lines), lines)

    def test_a_scheduler_that_is_only_starting_does_not_pass(self):
        self.scheduler = ("starting", "the scheduler service started 2 s ago")
        code, lines = self.run_check()
        self.assertEqual(code, 1)
        self.assertTrue(any(l.startswith("  FAIL  the scheduler service started 2 s ago") for l in lines), lines)

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
        answers = iter([("starting", "started 1 s ago"), ("idle", "no pass yet"), self.scheduler])
        sleeps = []
        with mock.patch.object(pb, "scheduler_status", side_effect=lambda *a, **k: next(answers)):
            code, lines = self.run_check(wait=60, sleep=sleeps.append, clock=iter([0, 1, 2, 3, 4, 5]).__next__)
        self.assertEqual(code, 0, lines)
        self.assertEqual(sleeps, [3, 3])

    def test_waiting_gives_up_when_the_time_is_up(self):
        sleeps = []
        self.scheduler = ("stopped", "the scheduler service is not running")
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
        run = os.path.join(self.tmp, "run")
        with mock.patch.object(pb, "APP_DIR", app), mock.patch.dict(os.environ, {"SINKO_RUN_DIR": run}):
            pb.write_heartbeat(5, pid=4242)                          # what the running scheduler leaves in /run/sinko
            env = dict(os.environ, SINKO_APP_DIR=app, PATH=self.fake_systemctl() + os.pathsep + os.environ["PATH"],
                       SINKO_CLI_PW_FILE=os.path.join(self.tmp, "pw"))
            done = subprocess.run([sys.executable, os.path.join(app, "bin", "sinko"), "selfcheck"], env=env,
                                  capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(done.stdout.count("  ok    "), 6, done.stdout)

    def fake_systemctl(self, pid=4242, up_seconds=100):
        """A systemctl that answers `show` for a scheduler that has been up for `up_seconds`, as pid `pid`."""
        folder = os.path.join(self.tmp, "fakebin")
        os.makedirs(folder, exist_ok=True)
        entered = int((time.monotonic() - up_seconds) * 1e6)
        with open(os.path.join(folder, "systemctl"), "w") as fh:
            fh.write("#!/bin/sh\nif [ \"$1\" = show ]; then\n  echo ActiveState=active\n"
                     "  echo ActiveEnterTimestampMonotonic=%d\n  echo MainPID=%d\n  echo NRestarts=0\nfi\nexit 0\n" % (entered, pid))
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


class TransportErrorTests(unittest.TestCase):
    """Pi-hole answers with a body shorter than its Content-Length (FTL restarting, a connection closed half way): urllib
    raises http.client.IncompleteRead, which is neither an OSError nor a ValueError. The scheduler survives it; so must
    the self-check (or a good update is rolled back for it), the wait for Pi-hole, and every command."""

    def setUp(self):
        self.httpd, self.calls = fake_release.serve_truncated()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name
        os.makedirs(os.path.join(self.tmp, "www", "pb"))
        with open(os.path.join(self.tmp, "pw"), "w") as fh:
            fh.write(mock_pihole.PASSWORD)
        env = mock.patch.dict(os.environ, {"SINKO_API_URL": self.httpd.url, "SINKO_WEBROOT": os.path.join(self.tmp, "www"),
                                           "SINKO_STATE_DIR": os.path.join(self.tmp, "state")})
        env.start()
        self.addCleanup(env.stop)
        for patch in (mock.patch.object(pb, "CLI_PW_FILE", os.path.join(self.tmp, "pw")),
                      mock.patch.object(pb, "APP_DIR", os.path.join(self.tmp, "no-app"))):
            patch.start()
            self.addCleanup(patch.stop)
        self.catalog = pb.load_catalog(LISTS)

    def test_this_is_the_error_and_it_is_in_the_class_the_scheduler_survives(self):
        with self.assertRaises(http.client.IncompleteRead) as caught:
            pb.Api(self.httpd.url)._raw("GET", "/api/auth")
        self.assertNotIsInstance(caught.exception, (OSError, ValueError))
        self.assertIsInstance(caught.exception, pb.TRANSPORT_ERRORS)

    def test_a_round_of_the_selfcheck_reports_it_as_a_failed_check_and_does_not_raise(self):
        results = pb.selfcheck_round(self.catalog, scheduler=lambda: (True, "this is the scheduler"))
        self.assertFalse(results[0][0])
        self.assertIn("Pi-hole API reachable", results[0][1])
        self.assertIn("IncompleteRead", results[0][1])
        self.assertFalse(all(ok for ok, _ in results))
        self.assertEqual(len(results), 6, "every check still ran and said something")

    def test_the_command_exits_with_a_failure_not_a_traceback_and_waiting_goes_on_until_the_time_is_up(self):
        lines, slept = [], []
        now = [0.0]
        code = pb.run_selfcheck(wait=10, out=lines.append, sleep=lambda s: (slept.append(s), now.__setitem__(0, now[0] + s)),
                                clock=lambda: now[0], catalog=self.catalog)
        self.assertEqual(code, 1)
        self.assertGreaterEqual(len(slept), 2, "it tried again instead of giving up at the first cut answer")
        self.assertTrue(any("FAIL" in l and "Pi-hole API reachable" in l for l in lines), lines)

    def test_waiting_for_pihole_survives_it_and_ends_with_the_ordinary_error(self):
        started = time.monotonic()
        with self.assertRaises(pb.ApiError) as caught:
            pb.Api(self.httpd.url).wait_ready(timeout=0.5)
        self.assertIn("did not come up", str(caught.exception))
        self.assertGreater(time.monotonic() - started, 0.4)
        self.assertGreaterEqual(len(self.calls), 1)

    def test_waiting_for_pihole_ends_as_soon_as_it_answers_properly(self):
        httpd, calls = fake_release.serve_truncated(good_after=2)
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        with mock.patch.object(pb.time, "sleep", lambda s: None):
            pb.Api(httpd.url).wait_ready(timeout=30)
        self.assertEqual(len(calls), 3, "two cut answers, then the good one")

    def cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = pb.main(list(args))
        return code, out.getvalue(), err.getvalue()

    def test_a_command_says_that_pihole_did_not_answer_properly_instead_of_a_traceback(self):
        for args in (("status",), ("use-mac",), ("telemetry", "on"), ("selfcheck",), ("remove",)):
            code, out, err = self.cli(*args)
            self.assertIn(code, (1, 2), args)
            self.assertNotIn("Traceback", out + err, args)
        code, out, err = self.cli("status")
        self.assertEqual(code, 2)
        self.assertIn("did not answer properly", err)
        self.assertIn("IncompleteRead", err)

    def test_the_doctor_reports_it_as_something_to_fix_and_goes_on_to_the_other_checks(self):
        lines = []
        real_report = pb.Report
        with mock.patch.object(pb, "Report", lambda: real_report(out=lines.append)), \
                mock.patch.object(pb.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "", "")), \
                mock.patch.object(pb, "ftl_config", return_value="true"), \
                mock.patch.object(pb, "scheduler_status", return_value=("ok", "the scheduler service has run for 300 s")):
            code = pb.cmd_doctor(None)
        self.assertEqual(code, 1)
        self.assertTrue(any(l.lstrip().startswith("FIX") and "Pi-hole API reachable" in l and "IncompleteRead" in l
                            for l in lines), lines)
        self.assertTrue(any("running version" in l for l in lines), "and the checks that need no Pi-hole still ran")

    def test_the_notes_of_a_rollback_by_hand_survive_it_as_well(self):
        with self.assertLogs("sinko", level="WARNING"):
            pb.note_rollback("3.1.0", "3.0.0")


class SchedulerStatusTests(unittest.TestCase):
    """What "the scheduler works" means for the self-check and the doctor: a process that has stayed up longer than
    one crash cycle and has finished passes. systemd is replaced by what `systemctl show` would print."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.run_dir = os.path.join(tmp.name, "run")
        env = mock.patch.dict(os.environ, {"SINKO_RUN_DIR": self.run_dir})
        env.start()
        self.addCleanup(env.stop)

    def show(self, state="active", entered=None, pid=4242, restarts=0, code=0):
        lines = ["ActiveState=" + state, "ActiveEnterTimestampMonotonic=%d" % (0 if entered is None else entered * 1e6),
                 "MainPID=%d" % pid, "NRestarts=%d" % restarts]
        return mock.patch.object(pb.subprocess, "run", return_value=subprocess.CompletedProcess([], code, "\n".join(lines) + "\n", ""))

    def test_the_unit_state_is_read_from_systemctl_show_on_the_machines_monotonic_clock(self):
        with self.show(entered=900.0, pid=77, restarts=3) as run:
            info = pb.unit_state("sinko", mono=lambda: 1000.0)
        self.assertEqual(info, {"active": True, "age": 100.0, "pid": 77, "restarts": 3})
        command = run.call_args[0][0]
        self.assertEqual(command[:2], ["systemctl", "show"])
        self.assertEqual(command[-1], "sinko")
        self.assertLessEqual(run.call_args[1]["timeout"], 10)

    def test_a_unit_that_is_not_active_has_no_age(self):
        for state in ("inactive", "activating", "failed", "deactivating", ""):
            with self.show(state=state, entered=900.0):
                info = pb.unit_state("sinko", mono=lambda: 1000.0)
            self.assertFalse(info["active"], state)
            self.assertIsNone(info["age"], state)

    def test_when_systemd_cannot_be_asked_there_is_no_answer(self):
        for error in (FileNotFoundError("systemctl"), subprocess.TimeoutExpired("systemctl", 10), PermissionError()):
            with mock.patch.object(pb.subprocess, "run", side_effect=error):
                self.assertIsNone(pb.unit_state("sinko"))
                self.assertEqual(pb.scheduler_status()[0], "stopped")
                self.assertFalse(pb.unit_active_for("sinko", 1))
        with self.show(code=1):
            self.assertIsNone(pb.unit_state("sinko"))

    def test_odd_property_values_do_not_raise(self):
        junk = subprocess.CompletedProcess([], 0, "ActiveState=active\nActiveEnterTimestampMonotonic=soon\nMainPID=\nNRestarts=x\nnoise\n", "")
        with mock.patch.object(pb.subprocess, "run", return_value=junk):
            info = pb.unit_state("sinko")
        self.assertEqual((info["active"], info["age"], info["pid"], info["restarts"]), (True, None, 0, 0))

    def test_active_for_means_active_without_a_break(self):
        with self.show(entered=990.0), mock.patch.object(pb.time, "monotonic", lambda: 1000.0):
            self.assertFalse(pb.unit_active_for("sinko", 15))
            self.assertTrue(pb.unit_active_for("sinko", 10))
        with self.show(state="activating", entered=100.0), mock.patch.object(pb.time, "monotonic", lambda: 1000.0):
            self.assertFalse(pb.unit_active_for("sinko", 1))

    def status(self, now, beat=None, **kw):
        info = {"active": True, "age": 100.0, "pid": 4242, "restarts": 0}
        info.update(kw)
        with mock.patch.object(pb, "unit_state", return_value=info), mock.patch.object(pb, "read_heartbeat", return_value=beat):
            return pb.scheduler_status(mono=lambda: now)

    def test_a_scheduler_that_stays_up_and_keeps_finishing_passes_is_ok(self):
        beat = {"pid": 4242, "version": pb.VERSION, "ticks": 7, "mono": 995.0}
        self.assertEqual(self.status(1000.0, beat)[0], "ok")

    def test_not_active_is_stopped(self):
        self.assertEqual(self.status(1000.0, None, active=False, age=None)[0], "stopped")

    def test_a_scheduler_that_has_just_started_is_starting_not_ok(self):
        self.assertEqual(self.status(1000.0, None, age=3.0)[0], "starting")

    def test_a_scheduler_that_restarts_again_and_again_is_crashing_even_in_its_active_second(self):
        beat = {"pid": 4242, "version": pb.VERSION, "ticks": 9, "mono": 999.0}
        self.assertEqual(self.status(1000.0, beat, age=30.0, restarts=5)[0], "crashing")
        self.assertEqual(self.status(1000.0, beat, age=300.0, restarts=5)[0], "ok", "long ago: it has been stable since")

    def test_up_but_no_finished_pass_of_this_process_is_idle(self):
        fresh = {"pid": 4242, "version": pb.VERSION, "ticks": 7, "mono": 995.0}
        for beat, why in ((None, "no heartbeat"), (dict(fresh, pid=1), "another process"),
                          (dict(fresh, version="2.9.0"), "another version"), (dict(fresh, ticks=1), "one pass only"),
                          (dict(fresh, mono=900.0), "stale"), (dict(fresh, mono=1100.0), "from the future")):
            state, text = self.status(1000.0, beat)
            self.assertEqual(state, "idle", why)
            self.assertTrue(text)

    def test_a_crash_loop_is_never_ok_however_the_check_lands(self):
        """The model from the review: active for 1.5 s of every 12 s (RestartSec 10 plus the run), started at any phase."""
        for phase in range(24):
            verdicts = set()
            for step in range(0, 91, 3):                          # a round every 3 s for 90 s, as `selfcheck --wait 90`
                t = phase * 0.5 + step
                cycle, into = divmod(t, 12.0)
                active = into < 1.5
                info = {"active": active, "age": into if active else None, "pid": 1000 + int(cycle), "restarts": int(cycle)}
                beat = {"pid": info["pid"], "version": pb.VERSION, "ticks": 1, "mono": t - into + 1.0}
                with mock.patch.object(pb, "unit_state", return_value=info), mock.patch.object(pb, "read_heartbeat", return_value=beat):
                    verdicts.add(pb.scheduler_status(mono=lambda: t)[0])
            self.assertNotIn("ok", verdicts, "phase %s" % phase)

    def test_a_healthy_scheduler_that_was_restarted_a_moment_ago_passes_within_about_twenty_seconds(self):
        for age_at_start in (0, 3, 10, 60):
            passed_at = None
            for step in range(0, 91, 3):
                age = age_at_start + step
                beat = {"pid": 4242, "version": pb.VERSION, "ticks": 1 + int(age // 15), "mono": 1000.0 + step - (age % 15)}
                state, _ = self.status(1000.0 + step, beat, age=float(age))
                if state == "ok":
                    passed_at = step
                    break
            self.assertIsNotNone(passed_at, age_at_start)
            self.assertLessEqual(passed_at, 21, age_at_start)

    def test_the_heartbeat_round_trips_and_is_private(self):
        pb.write_heartbeat(3, pid=4242)
        beat = pb.read_heartbeat()
        self.assertEqual((beat["pid"], beat["version"], beat["ticks"]), (4242, pb.VERSION, 3))
        self.assertLess(abs(beat["mono"] - time.monotonic()), 5)
        self.assertEqual(stat.S_IMODE(os.stat(self.run_dir).st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.join(self.run_dir, "heartbeat.json")).st_mode), 0o600)

    def test_a_missing_or_garbled_heartbeat_is_none(self):
        self.assertIsNone(pb.read_heartbeat())
        os.makedirs(self.run_dir)
        path = os.path.join(self.run_dir, "heartbeat.json")
        for text in ("", "{", "[]", "null", json.dumps({"pid": "x", "ticks": 1, "mono": 1.0, "version": "3.0.0"}),
                     json.dumps({"pid": 5, "ticks": -1, "mono": 1.0, "version": "3.0.0"}),
                     json.dumps({"pid": 5, "ticks": 1, "mono": True, "version": "3.0.0"}),
                     json.dumps({"pid": 5, "ticks": 1, "mono": 1.0, "version": 3}),
                     json.dumps({"pid": True, "ticks": 1, "mono": 1.0, "version": "3.0.0"})):
            with open(path, "w") as fh:
                fh.write(text)
            self.assertIsNone(pb.read_heartbeat(), text)

    def test_a_heartbeat_that_cannot_be_written_raises_oserror_for_the_loop_to_log(self):
        with mock.patch.dict(os.environ, {"SINKO_RUN_DIR": "/proc/sinko-cannot-exist"}):
            with self.assertRaises(OSError):
                pb.write_heartbeat(1)


class PageFilesTests(unittest.TestCase):
    """The start page is a set of files that must all be there: one missing script means a page that shows nothing."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = os.path.join(tmp.name, "www")
        os.makedirs(os.path.join(self.root, "pb"))

    def put(self, name, text="x"):
        path = os.path.join(self.root, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            fh.write(text)

    def complete(self):
        self.put("index.html", PAGE)
        for name in ("app.js", "pb-core.js", "style.css", "version.txt", "services.json"):
            self.put("pb/" + name)

    def test_the_references_in_index_html_are_found(self):
        html = ('<link rel="icon" href="/pb/icon.svg"><script src="/pb/pb-core.js" defer></script><script src=\'/pb/app.js\'></script>'
                '<a href="/admin/">x</a><img src="/pb/fonts/a.woff2?v=3"><script src="/pb/../etc/passwd"></script>'
                '<link href = "/pb/style.css#x"><script src="https://cdn.example/pb/evil.js"></script>')
        self.assertEqual(pb.referenced_page_files(html), ["icon.svg", "pb-core.js", "app.js", "fonts/a.woff2", "style.css"])

    def test_a_complete_page_has_no_problems(self):
        self.complete()
        self.assertEqual(pb.page_file_problems(self.root), [])

    def test_a_file_that_index_html_loads_but_the_box_lacks_is_a_problem(self):
        self.complete()
        os.unlink(os.path.join(self.root, "pb", "pb-core.js"))
        self.assertEqual(pb.page_file_problems(self.root), ["pb/pb-core.js"])

    def test_the_files_the_page_cannot_start_without_are_always_required(self):
        self.put("index.html", "<html></html>")
        self.assertEqual(pb.page_file_problems(self.root), ["pb/app.js", "pb/version.txt", "pb/services.json"])
        self.assertEqual(pb.page_file_problems(os.path.join(self.root, "nowhere")),
                         ["index.html", "pb/app.js", "pb/version.txt", "pb/services.json"])

    def test_an_empty_file_is_a_problem_too(self):
        self.complete()
        self.put("pb/app.js", "")
        self.assertEqual(pb.page_file_problems(self.root), ["pb/app.js (empty)"])

    def test_every_file_of_the_installed_releases_web_folder_must_be_installed(self):
        self.complete()
        src = os.path.join(os.path.dirname(self.root), "src-web")
        for name in ("index.html", "app.js", "pb-core.js", "links.json", "fonts/a.woff2"):
            path = os.path.join(src, name)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as fh:
                fh.write("x")
        self.assertEqual(pb.page_file_problems(self.root, src), ["pb/links.json", "pb/fonts/a.woff2"])
        self.put("pb/links.json")
        self.put("pb/fonts/a.woff2")
        self.assertEqual(pb.page_file_problems(self.root, src), [])

    def src_tree(self, *names):
        src = os.path.join(os.path.dirname(self.root), "src-web")
        for name in names:
            path = os.path.join(src, name)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as fh:
                fh.write("x")
        return src

    def test_only_what_the_installer_copies_is_required_not_everything_below_the_web_folder(self):
        # install_files: web/* (the files, not the folders; index.html to the web root) and web/fonts/*. A release that adds
        # web/img/a.png would otherwise make every update to it fail its check and be rolled back, for a file nobody copies.
        self.complete()
        src = self.src_tree("index.html", "app.js", "img/a.png", "img/deeper/b.png", "docs/readme.txt", "fonts/sub/c.woff2",
                            ".DS_Store", "fonts/.hidden", ".github/x.yml")
        self.assertEqual(pb.page_file_problems(self.root, src), [])

    def test_the_files_the_installer_does_copy_are_required(self):
        self.complete()
        src = self.src_tree("index.html", "app.js", "style.css", "links.json", "fonts/a.woff2", "fonts/OFL.txt")
        self.assertEqual(pb.page_file_problems(self.root, src), ["pb/links.json", "pb/fonts/OFL.txt", "pb/fonts/a.woff2"])
        self.put("pb/links.json")
        self.put("pb/fonts/a.woff2")
        self.put("pb/fonts/OFL.txt", "")
        self.assertEqual(pb.page_file_problems(self.root, src), ["pb/fonts/OFL.txt (empty)"])

    def test_a_missing_fonts_folder_in_the_source_is_not_a_problem_of_the_check(self):
        self.complete()
        self.assertEqual(pb.page_file_problems(self.root, self.src_tree("app.js")), [])

    def test_the_real_web_folder_holds_only_what_the_installer_copies(self):
        # The rule of page_file_problems is the installer's. If this fails, a file was added to web/ below a folder other
        # than fonts/: install.sh would not copy it. Teach install_files (and then this check) about the new folder.
        web = os.path.join(ROOT, "web")
        for here, dirs, names in os.walk(web):
            rel = os.path.relpath(here, web)
            self.assertIn(rel, (".", "fonts"), "web/%s has files the installer does not copy" % rel)
            if rel == "fonts":
                self.assertEqual(dirs, [], "web/fonts/ holds a folder: `install web/fonts/*` would fail on it")
        installed = os.path.join(os.path.dirname(self.root), "installed")
        os.makedirs(os.path.join(installed, "pb", "fonts"))
        for name in os.listdir(web):                       # what install_files puts where
            path = os.path.join(web, name)
            if os.path.isfile(path) and not name.startswith("."):
                shutil.copy(path, os.path.join(installed, name if name == "index.html" else os.path.join("pb", name)))
        for name in os.listdir(os.path.join(web, "fonts")):
            shutil.copy(os.path.join(web, "fonts", name), os.path.join(installed, "pb", "fonts", name))
        for name in ("version.txt", "services.json", "domains.json"):
            with open(os.path.join(installed, "pb", name), "w") as fh:
                fh.write("x")
        self.assertEqual(pb.page_file_problems(installed, web), [])

    def test_the_source_tree_counts_only_when_it_is_the_installed_version(self):
        app = os.path.join(os.path.dirname(self.root), "app")
        os.makedirs(os.path.join(app, "src", "web"))
        with mock.patch.object(pb, "APP_DIR", app):
            self.assertIsNone(pb.installed_source_web(), "no VERSION file")
            with open(os.path.join(app, "src", "VERSION"), "w") as fh:
                fh.write("2.9.0\n")
            self.assertIsNone(pb.installed_source_web(), "another version's tree")
            with open(os.path.join(app, "src", "VERSION"), "w") as fh:
                fh.write(pb.VERSION + "\n")
            self.assertEqual(pb.installed_source_web(), os.path.join(app, "src", "web"))


if __name__ == "__main__":
    unittest.main()
