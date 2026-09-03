import unittest


class ResultDetailTests(unittest.TestCase):
    def test_compact_detail_keeps_only_first_note_and_truncates_long_text(self) -> None:
        from probe_codex_gui import compact_result_detail

        self.assertEqual(
            compact_result_detail("鉴权失败：API Key 无效；Codex 出现重连"),
            "鉴权失败：API Key 无效",
        )
        self.assertEqual(len(compact_result_detail("很长" * 30)), 48)
        self.assertTrue(compact_result_detail("很长" * 30).endswith("…"))


class AttemptProgressTests(unittest.TestCase):
    def test_fast_defaults_and_remaining_timeout_text(self) -> None:
        from probe_codex_gui import (
            DEFAULT_ATTEMPTS,
            DEFAULT_TIMEOUT_SECONDS,
            format_attempt_progress,
        )

        self.assertEqual(DEFAULT_ATTEMPTS, 1)
        self.assertEqual(DEFAULT_TIMEOUT_SECONDS, 90)
        self.assertEqual(
            format_attempt_progress("Provider Alpha", "gpt-5.5", 1, 1, 12, 90),
            "Provider Alpha | gpt-5.5 | 第 1/1 次尝试 | 已用 12s，剩余 78s",
        )
        self.assertEqual(
            format_attempt_progress("Provider Alpha", "gpt-5.5", 1, 1, 91, 90),
            "Provider Alpha | gpt-5.5 | 第 1/1 次尝试 | 已达到 90s 超时，正在终止子进程...",
        )


if __name__ == "__main__":
    unittest.main()
