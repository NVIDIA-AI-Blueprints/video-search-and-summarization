# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""CLI video selection controls the same matrix in run and standalone validation."""

from contextlib import redirect_stdout
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import run
import validate


class SelectionPrecedenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config = Path(self.tmp.name) / "config.yml"
        self.config.write_text(
            "sweep:\n"
            "  profile: custom\n"
            "  video_classes: [50MB, 500MB]\n"
            "  videos: [configured.mp4]\n"
            "  concurrencies: [1, 5, 10]\n"
        )

    def assert_selection(self, flags, classes, videos, concurrencies):
        for entry in (run, validate):
            with self.subTest(entry=entry.__name__):
                with (
                    patch.object(
                        entry, "validate",
                        return_value=validate.ValidationResult(errors=["stop before endpoint calls"]),
                    ) as check,
                    redirect_stdout(io.StringIO()),
                ):
                    self.assertEqual(entry.main(["--config", str(self.config), *flags]), 2)
                check.assert_called_once()
                self.assertEqual(check.call_args.kwargs["classes"], classes)
                self.assertEqual(check.call_args.kwargs["user_videos"], videos)
                self.assertEqual(check.call_args.kwargs["concurrencies"], concurrencies)

    def test_cli_video_replaces_config_classes_without_corpus(self):
        self.assert_selection(["--video", "selected.mp4"], ["custom"], ["selected.mp4"], [1, 5, 10])

    def test_explicit_custom_profile_keeps_config_concurrencies_but_not_classes(self):
        self.assert_selection(
            ["--profile", "custom", "--video=first.mp4", "--video", "second.mkv", "--video-class-name", "mine"],
            ["mine"], ["first.mp4", "second.mkv"], [1, 5, 10],
        )

    def test_cli_video_does_not_sweep_config_corpus_when_corpus_exists(self):
        self.config.write_text("corpus: .\n" + self.config.read_text())
        self.assert_selection(["--video", "selected.mp4"], ["custom"], ["selected.mp4"], [1, 5, 10])

    def test_explicit_classes_can_be_combined_with_cli_video(self):
        self.assert_selection(
            ["--corpus", self.tmp.name, "--video", "selected.mp4", "--video-class=2GB", "--concurrency", "2"],
            ["2GB", "custom"], ["selected.mp4"], [2],
        )

    def test_config_can_intentionally_combine_classes_and_videos(self):
        self.assert_selection(
            ["--corpus", self.tmp.name], ["50MB", "500MB", "custom"], ["configured.mp4"], [1, 5, 10],
        )

    def test_named_profile_still_replaces_config_matrix(self):
        self.assert_selection(
            ["--profile", "smoke", "--video", "selected.mp4"], ["custom"], ["selected.mp4"], [1],
        )
        self.assert_selection(["--profile", "smoke", "--corpus", self.tmp.name], ["50MB"], None, [1])

    def test_legacy_set_remains_rejected_instead_of_restoring_config_classes(self):
        args = run.parse_args(["--config", str(self.config), "--video", "selected.mp4", "--set", "video_class=2GB"])
        classes, _ = validate.resolve_matrix(
            args.profile, args.classes, args.concurrencies, user_videos=args.videos,
        )
        errors, accepted = validate.check_overrides(validate._parse_set(args.overrides))
        self.assertEqual(classes, ["custom"])
        self.assertEqual(accepted, [])
        self.assertEqual(len(errors), 1)
        self.assertIn("--set video_class=... is not applied", errors[0])


if __name__ == "__main__":
    unittest.main()
