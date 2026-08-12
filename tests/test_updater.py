import os
import tempfile
import unittest
from pathlib import Path

from local_proxy import updater
from local_proxy.updater import UpdateError


class FakeResponse:
    def __init__(self, *, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class FakeClient:
    def __init__(self, response):
        self._response = response

    def get(self, url, headers=None, timeout=None):
        return self._response


def release_payload(tag, *, asset="CodexLocalProxy-win-x64.exe", with_sha=True):
    assets = [{"name": asset, "browser_download_url": f"https://example/{asset}"}]
    if with_sha:
        assets.append(
            {"name": f"{asset}.sha256", "browser_download_url": f"https://example/{asset}.sha256"}
        )
    return {"tag_name": tag, "assets": assets, "html_url": "https://example/release", "body": "notes"}


class VersionTests(unittest.TestCase):
    def test_parses_and_compares_versions(self):
        self.assertEqual(updater.parse_version("v0.7.1"), (0, 7, 1))
        self.assertEqual(updater.parse_version("0.7.1"), (0, 7, 1))
        self.assertTrue(updater.is_newer("0.7.2", "0.7.1"))
        self.assertTrue(updater.is_newer("v1.0.0", "0.9.9"))
        self.assertFalse(updater.is_newer("0.7.1", "0.7.1"))
        self.assertFalse(updater.is_newer("0.7.0", "0.7.1"))

    def test_rejects_unparseable_version(self):
        with self.assertRaisesRegex(UpdateError, "版本号"):
            updater.parse_version("release")


class ParseReleaseTests(unittest.TestCase):
    def test_flags_update_when_newer_with_assets(self):
        info = updater.parse_release(
            release_payload("v0.7.2"), "0.7.1", asset_name="CodexLocalProxy-win-x64.exe"
        )
        self.assertTrue(info.has_update)
        self.assertEqual(info.latest_version, "0.7.2")
        self.assertTrue(info.asset_url.endswith("CodexLocalProxy-win-x64.exe"))
        self.assertTrue(info.sha256_url.endswith(".sha256"))

    def test_no_update_when_same_version(self):
        info = updater.parse_release(
            release_payload("v0.7.1"), "0.7.1", asset_name="CodexLocalProxy-win-x64.exe"
        )
        self.assertFalse(info.has_update)

    def test_no_update_when_asset_missing(self):
        payload = release_payload("v0.7.2", asset="OtherName.exe")
        info = updater.parse_release(
            payload, "0.7.1", asset_name="CodexLocalProxy-win-x64.exe"
        )
        self.assertFalse(info.has_update)
        self.assertIsNone(info.asset_url)

    def test_missing_tag_raises(self):
        with self.assertRaisesRegex(UpdateError, "tag_name"):
            updater.parse_release({"assets": []}, "0.7.1")


class FetchTests(unittest.TestCase):
    def test_check_for_update_uses_injected_client(self):
        client = FakeClient(FakeResponse(payload=release_payload("v0.8.0")))
        info = updater.check_for_update(
            "0.7.1", client=client, asset_name="CodexLocalProxy-win-x64.exe"
        )
        self.assertTrue(info.has_update)
        self.assertEqual(info.latest_version, "0.8.0")

    def test_non_200_raises(self):
        client = FakeClient(FakeResponse(status_code=404))
        with self.assertRaisesRegex(UpdateError, "HTTP 404"):
            updater.check_for_update("0.7.1", client=client)


class Sha256Tests(unittest.TestCase):
    def test_parses_hash_document_forms(self):
        digest = "a" * 64
        self.assertEqual(updater.parse_sha256_document(digest), digest)
        self.assertEqual(updater.parse_sha256_document(f"{digest}  file.exe\n"), digest)

    def test_rejects_bad_hash_document(self):
        with self.assertRaisesRegex(UpdateError, "SHA-256"):
            updater.parse_sha256_document("nothex")

    def test_verifies_file_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "blob"
            path.write_bytes(b"hello")
            import hashlib

            expected = hashlib.sha256(b"hello").hexdigest()
            self.assertEqual(updater.verify_file_sha256(path, expected), expected)
            with self.assertRaisesRegex(UpdateError, "校验失败"):
                updater.verify_file_sha256(path, "b" * 64)


class HelperTests(unittest.TestCase):
    def test_launch_update_helper_builds_finalize_command(self):
        from local_proxy import application

        captured = {}

        def fake_spawn(command, working_directory):
            captured["command"] = command
            captured["cwd"] = working_directory

        original = application._spawn_detached
        application._spawn_detached = fake_spawn
        try:
            application.launch_update_helper(Path("/tmp/new/CodexLocalProxy.exe"))
        finally:
            application._spawn_detached = original
        command = captured["command"]
        self.assertIn("--finalize-update", command)
        self.assertIn("--target", command)
        self.assertIn("--wait-pid", command)
        self.assertEqual(command[0], str(Path("/tmp/new/CodexLocalProxy.exe")))

    def test_process_alive_detects_current_process(self):
        from local_proxy import application

        self.assertTrue(application._process_alive(os.getpid()))


if __name__ == "__main__":
    unittest.main()
