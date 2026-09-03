import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

from local_proxy.version import resolve_app_version


class AppVersionTests(unittest.TestCase):
    def test_source_checkout_uses_latest_reachable_release_tag(self) -> None:
        calls = []

        def run(command, **options):
            calls.append((command, options))
            return subprocess.CompletedProcess(command, 0, stdout="v0.13.2\n")

        version = resolve_app_version(
            "0.1.7",
            frozen=False,
            repository=Path("checkout"),
            runner=run,
        )

        self.assertEqual(version, "0.13.2")
        self.assertEqual(calls[0][0][-1], "HEAD")
        self.assertEqual(calls[0][1]["cwd"], Path("checkout"))
        self.assertTrue(calls[0][1]["check"])
        self.assertEqual(calls[0][1]["timeout"], 2)

    def test_windows_source_version_lookup_suppresses_console_window(self) -> None:
        calls = []

        def run(command, **options):
            calls.append((command, options))
            return subprocess.CompletedProcess(command, 0, stdout="v0.13.2\n")

        with mock.patch.object(sys, "platform", "win32"):
            version = resolve_app_version(
                "0.1.7",
                frozen=False,
                repository=Path("checkout"),
                runner=run,
            )

        self.assertEqual(version, "0.13.2")
        self.assertEqual(
            calls[0][1]["creationflags"],
            getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

    def test_packaged_runtime_keeps_injected_version(self) -> None:
        def fail_if_called(*args, **kwargs):
            self.fail("packaged runtime must not invoke git")

        self.assertEqual(
            resolve_app_version("0.13.2", frozen=True, runner=fail_if_called),
            "0.13.2",
        )

    def test_invalid_tag_falls_back_to_injected_version(self) -> None:
        def run(command, **options):
            return subprocess.CompletedProcess(command, 0, stdout="v0.13\n")

        self.assertEqual(
            resolve_app_version("0.1.7", frozen=False, runner=run),
            "0.1.7",
        )

    def test_git_failure_falls_back_to_injected_version(self) -> None:
        def run(command, **options):
            raise subprocess.CalledProcessError(128, command)

        self.assertEqual(
            resolve_app_version("0.1.7", frozen=False, runner=run),
            "0.1.7",
        )


if __name__ == "__main__":
    unittest.main()
