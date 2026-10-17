# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""CLI video selection controls the same matrix in run and standalone validation."""

from contextlib import redirect_stdout
import io
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from config import ConfigError
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
            ["--corpus", self.tmp.name], ["50MB", "500MB", "custom"], [str(self.config.parent / "configured.mp4")], [1, 5, 10],
        )

    def test_named_profile_still_replaces_config_matrix(self):
        self.assert_selection(
            ["--profile", "smoke", "--video", "selected.mp4"], ["custom"], ["selected.mp4"], [1],
        )
        self.assert_selection(["--profile", "smoke", "--corpus", self.tmp.name], ["50MB"], None, [1])

    def test_configured_video_paths_follow_config_directory_in_both_entrypoints(self):
        self.config.write_text(
            "sweep:\n  profile: custom\n  videos: [clips/first.mp4, ../second.mkv]\n  concurrencies: [1]\n"
        )
        expected = [str(self.config.parent / "clips/first.mp4"), str((self.config.parent / "../second.mkv").resolve())]
        args = run.parse_args(["--config", str(self.config)])
        self.assertEqual(args.videos, expected)
        self.assert_selection([], ["custom"], expected, [1])
        # A flag stays caller-relative and replaces the config's resolved list.
        self.assert_selection(["--video", "caller.mp4"], ["custom"], ["caller.mp4"], [1])

    def test_yaml_integer_strings_work_but_fractional_and_boolean_concurrency_fail_before_validation(self):
        self.config.write_text("sweep:\n  profile: custom\n  videos: [clip.mp4]\n  concurrencies: ['1', '5']\n")
        self.assertEqual(run.parse_args(["--config", str(self.config)]).concurrencies, [1, 5])
        self.assert_selection([], ["custom"], [str(self.config.parent / "clip.mp4")], [1, 5])
        for value in ("1.9", "true", "2.0", ".nan", ".inf", "'1.9'"):
            self.config.write_text(f"sweep:\n  profile: custom\n  videos: [clip.mp4]\n  concurrencies: [{value}]\n")
            with self.subTest(value=value):
                with self.assertRaisesRegex(ConfigError, "integers"):
                    run.parse_args(["--config", str(self.config)])
                for entry in (run, validate):
                    with patch.object(entry, "validate") as check, redirect_stdout(io.StringIO()):
                        self.assertEqual(entry.main(["--config", str(self.config)]), 2)
                    check.assert_not_called()

    def test_null_checkout_inherits_discovery_and_explicit_flag_still_wins(self):
        self.config.write_text("cli:\n  repo: null\nsweep:\n  profile: custom\n  videos: [clip.mp4]\n  concurrencies: [1]\n")
        inherited = self.config.parent / "prepared-checkout"
        explicit = self.config.parent / "explicit-checkout"
        with patch.dict(os.environ, {"VSS_REPO_ROOT": str(inherited)}):
            self.assertEqual(run.parse_args(["--config", str(self.config)]).vss_repo, inherited)
            self.assertEqual(run.parse_args(["--config", str(self.config), "--vss-repo", str(explicit)]).vss_repo, explicit)
            for entry in (run, validate):
                with (patch.object(entry, "validate", return_value=validate.ValidationResult(errors=["stop"])) as check,
                      redirect_stdout(io.StringIO())):
                    self.assertEqual(entry.main(["--config", str(self.config)]), 2)
                self.assertEqual(check.call_args.kwargs["vss_repo"], inherited)

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
