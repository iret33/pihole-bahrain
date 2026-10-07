"""The box program's runtime data: version, legacy config keys, /var/lib/sinko helpers (atomic writes, request
markers, the counter id, the update result, the run lock)."""
import importlib.machinery
import importlib.util
import json
import os
import stat
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

loader = importlib.machinery.SourceFileLoader("sinko_cli", os.path.join(ROOT, "bin", "sinko"))
spec = importlib.util.spec_from_loader("sinko_cli", loader)
pb = importlib.util.module_from_spec(spec)
loader.exec_module(pb)


def mode_of(path):
    return stat.S_IMODE(os.stat(path).st_mode)


class TempState(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = os.path.join(tmp.name, "var-lib-sinko")
        patcher = mock.patch.dict(os.environ, {"SINKO_STATE_DIR": self.dir})
        patcher.start()
        self.addCleanup(patcher.stop)


class VersionTests(unittest.TestCase):
    def test_version_file_and_constant_agree_and_are_semver(self):
        with open(os.path.join(ROOT, "VERSION"), encoding="utf-8") as fh:
            text = fh.read().strip()
        self.assertEqual(text, pb.VERSION)
        self.assertRegex(text, pb.SEMVER_RE.pattern)
        self.assertEqual(pb.VERSION, "3.0.1")


class LegacyConfigTests(unittest.TestCase):
    def read(self, text):
        with tempfile.NamedTemporaryFile("w", delete=False) as fh:
            fh.write(text)
        self.addCleanup(os.remove, fh.name)
        return pb.read_config(fh.name)

    def test_a_legacy_key_fills_in_for_a_missing_new_key(self):
        conf = self.read("PB_HOSTNAME=family.lan\nPB_IP=192.168.1.5\nPB_REF=master\n")
        self.assertEqual(conf["SINKO_HOSTNAME"], "family.lan")
        self.assertEqual(conf["SINKO_IP"], "192.168.1.5")
        self.assertEqual(conf["SINKO_REF"], "master")

    def test_the_new_key_wins_whatever_the_order(self):
        for text in ("SINKO_IP=10.0.0.2\nPB_IP=192.168.1.5\n", "PB_IP=192.168.1.5\nSINKO_IP=10.0.0.2\n"):
            self.assertEqual(self.read(text)["SINKO_IP"], "10.0.0.2", text)

    def test_an_empty_new_value_is_an_answer_and_is_kept(self):
        self.assertEqual(self.read("SINKO_HOSTNAME=''\nPB_HOSTNAME=family.lan\n")["SINKO_HOSTNAME"], "")

    def test_a_config_without_legacy_keys_is_unchanged(self):
        self.assertEqual(self.read("SINKO_HOSTNAME=a.lan\n"), {"SINKO_HOSTNAME": "a.lan"})

    def test_lists_base_uses_the_legacy_key(self):
        conf = self.read("PB_LISTS_BASE='https://cdn.example/lists/'\n")
        with mock.patch.dict(os.environ):
            os.environ.pop("SINKO_LISTS_BASE", None)
            self.assertEqual(pb.lists_base(conf), "https://cdn.example/lists")


class StateDirTests(TempState):
    def test_default_location(self):
        with mock.patch.dict(os.environ):
            os.environ.pop("SINKO_STATE_DIR")
            self.assertEqual(pb.state_dir(), "/var/lib/sinko")

    def test_directory_is_created_private_with_a_private_cache(self):
        path = pb.ensure_state_dir("cache")
        self.assertEqual(path, os.path.join(self.dir, "cache"))
        self.assertEqual(mode_of(self.dir), 0o700)
        self.assertEqual(mode_of(path), 0o700)

    def test_looser_rights_on_an_existing_directory_are_tightened(self):
        os.makedirs(self.dir, mode=0o755)
        os.chmod(self.dir, 0o755)
        pb.ensure_state_dir()
        self.assertEqual(mode_of(self.dir), 0o700)

    def test_ensure_is_idempotent(self):
        pb.ensure_state_dir("cache")
        pb.atomic_write(pb.state_path("x"), "keep")
        pb.ensure_state_dir("cache")
        with open(pb.state_path("x")) as fh:
            self.assertEqual(fh.read(), "keep")


class AtomicWriteTests(TempState):
    def test_writes_text_and_bytes_private_and_leaves_no_temp_file(self):
        pb.ensure_state_dir()
        pb.atomic_write(pb.state_path("a"), "héllo")
        pb.atomic_write(pb.state_path("b"), b"\x00\x01")
        with open(pb.state_path("a"), encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "héllo")
        with open(pb.state_path("b"), "rb") as fh:
            self.assertEqual(fh.read(), b"\x00\x01")
        self.assertEqual(mode_of(pb.state_path("a")), 0o600)
        self.assertEqual(sorted(os.listdir(self.dir)), ["a", "b"])

    def test_replaces_an_existing_file(self):
        pb.ensure_state_dir()
        pb.atomic_write(pb.state_path("a"), "one")
        pb.atomic_write(pb.state_path("a"), "two")
        with open(pb.state_path("a")) as fh:
            self.assertEqual(fh.read(), "two")

    def test_a_failure_keeps_the_old_content_and_cleans_up(self):
        pb.ensure_state_dir()
        pb.atomic_write(pb.state_path("a"), "old")
        with mock.patch.object(pb.os, "replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                pb.atomic_write(pb.state_path("a"), "new")
        with open(pb.state_path("a")) as fh:
            self.assertEqual(fh.read(), "old")
        self.assertEqual(os.listdir(self.dir), ["a"])

    def test_read_json_file_never_raises(self):
        pb.ensure_state_dir()
        self.assertEqual(pb.read_json_file(pb.state_path("nope"), "dflt"), "dflt")
        pb.atomic_write(pb.state_path("bad"), "{not json")
        self.assertIsNone(pb.read_json_file(pb.state_path("bad")))
        pb.atomic_write(pb.state_path("ok"), '{"a": 1}')
        self.assertEqual(pb.read_json_file(pb.state_path("ok")), {"a": 1})


class HandledMarkerTests(TempState):
    def test_nothing_is_handled_on_a_new_box(self):
        self.assertEqual(pb.load_handled(), {})

    def test_roundtrip_keeps_each_kind_apart(self):
        pb.set_handled("update", 1700000000123)
        pb.set_handled("power", "abc")
        pb.set_handled("check", 5)
        self.assertEqual(pb.load_handled(), {"update": 1700000000123, "power": "abc", "check": 5})
        self.assertEqual(mode_of(pb.state_path("handled.json")), 0o600)

    def test_forgetting_a_marker_restores_the_previous_situation(self):
        pb.set_handled("power", 7)
        pb.set_handled("power", None)
        self.assertEqual(pb.load_handled(), {})

    def test_a_damaged_or_foreign_file_is_ignored(self):
        pb.ensure_state_dir()
        for text in ("{oops", "[]", '{"update": true, "power": "", "other": 1, "check": [1]}'):
            pb.atomic_write(pb.state_path("handled.json"), text)
            self.assertEqual(pb.load_handled(), {}, text)

    def test_unknown_keys_are_dropped_when_rewritten(self):
        pb.ensure_state_dir()
        pb.atomic_write(pb.state_path("handled.json"), '{"update": 3, "evil": 9}')
        pb.set_handled("check", 4)
        with open(pb.state_path("handled.json")) as fh:
            self.assertEqual(json.load(fh), {"check": 4, "update": 3})


class InstallIdTests(TempState):
    def test_lookup_without_creating(self):
        self.assertIsNone(pb.install_id(create=False))
        self.assertFalse(os.path.exists(pb.state_path("install-id")))

    def test_created_lazily_as_32_hex_chars_and_then_stable(self):
        first = pb.install_id()
        self.assertRegex(first, r"^[0-9a-f]{32}$")
        self.assertEqual(pb.install_id(), first)
        self.assertEqual(pb.install_id(create=False), first)
        self.assertEqual(mode_of(pb.state_path("install-id")), 0o600)

    def test_a_damaged_id_is_replaced_by_a_valid_one(self):
        pb.ensure_state_dir()
        pb.atomic_write(pb.state_path("install-id"), "not-an-id\n")
        value = pb.install_id()
        self.assertRegex(value, r"^[0-9a-f]{32}$")

    def test_reset_makes_a_different_id(self):
        first = pb.install_id()
        self.assertNotEqual(pb.reset_install_id(), first)
        self.assertNotEqual(pb.install_id(), first)


class UpdateResultTests(TempState):
    def test_roundtrip(self):
        pb.write_update_result("ok", "3.0.0", "3.1.0", None, 1700000000.5)
        self.assertEqual(pb.read_update_result(),
                         {"status": "ok", "from": "3.0.0", "to": "3.1.0", "error": None, "at": 1700000000.5,
                          "rolledBack": None, "transient": False})
        pb.clear_update_result()
        self.assertIsNone(pb.read_update_result())
        pb.clear_update_result()                         # clearing twice is fine

    def test_garbage_is_rejected_or_cleaned(self):
        pb.ensure_state_dir()
        path = pb.state_path("update-result.json")
        for text in ("nope", "[]", '{"status": "maybe", "at": 1}', '{"status": "ok"}', '{"status": "ok", "at": -1}'):
            pb.atomic_write(path, text)
            self.assertIsNone(pb.read_update_result(), text)
        pb.atomic_write(path, json.dumps({"status": "failed", "from": "x", "to": "3.1.0",
                                          "error": "e" * 500, "at": 12}))
        got = pb.read_update_result()
        self.assertEqual((got["from"], got["to"], len(got["error"])), (None, "3.1.0", 200))


class RunLockTests(TempState):
    def test_only_one_holder_at_a_time(self):
        first, second = pb.RunLock(), pb.RunLock()
        self.assertFalse(pb.RunLock.is_held())
        self.assertTrue(first.acquire())
        self.assertTrue(pb.RunLock.is_held())
        self.assertFalse(second.acquire())
        first.release()
        self.assertFalse(pb.RunLock.is_held())
        self.assertTrue(second.acquire())
        second.release()

    def test_context_manager_releases(self):
        lock = pb.RunLock()
        self.assertTrue(lock.acquire())
        with lock:
            self.assertTrue(pb.RunLock.is_held())
        self.assertFalse(pb.RunLock.is_held())

    def test_the_lock_is_not_inherited_by_child_processes(self):
        import subprocess
        import sys
        # The installer, which the runner starts, outlives nothing: but a child that kept the descriptor would keep
        # the lock after the runner is gone.
        lock = pb.RunLock()
        self.assertTrue(lock.acquire())
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            lock.release()
            self.assertFalse(pb.RunLock.is_held())
        finally:
            child.kill()
            child.wait()


if __name__ == "__main__":
    unittest.main()
