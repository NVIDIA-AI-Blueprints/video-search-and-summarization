# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Search ingestion cannot complete with a missing pipeline or disable it via config."""

from contextlib import redirect_stderr, redirect_stdout
import io
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from completion import EsReadinessMonitor, UploadContext
from es_readiness import EsReadinessConfig
from httpio import JsonResponse
import run
from validate import resolve_matrix
from vss_cli import VssCli


class Clock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def indexed(raw, embed):
    return JsonResponse(200, {"aggregations": {"pipelines": {"buckets": {
        "rt_cv": {"doc_count": raw}, "rt_embed": {"doc_count": embed},
    }}}}, "", 0)


class RequiredPipelineTests(unittest.TestCase):
    def test_missing_pipeline_cannot_confirm(self):
        for raw, embed in ((1785, 0), (0, 12)):
            with self.subTest(raw=raw, embed=embed):
                clock = Clock()
                monitor = EsReadinessMonitor(
                    "http://es.test", 0.5, 4,
                    EsReadinessConfig(timeout_override_sec=5),
                )
                with (
                    patch("es_readiness.request_json", return_value=indexed(raw, embed)),
                    patch("es_readiness.time.monotonic", side_effect=clock.monotonic),
                    patch("es_readiness.time.sleep", side_effect=clock.sleep),
                ):
                    result = monitor.confirm(UploadContext("clip.mp4", "clip", "returned-uuid", {}, 60, 30))
                self.assertFalse(result.confirmed)
                self.assertEqual(result.outcome, "unconfirmed")
                self.assertEqual(result.metrics["expected_frames"], 1785)
                self.assertEqual(result.metrics["expected_chunks"], 12)

    def test_waits_for_raw_after_embed_completes(self):
        monitor = EsReadinessMonitor("http://es.test", 0.5, 4)
        with (
            patch("es_readiness.request_json", side_effect=[indexed(0, 12), indexed(1785, 12)]) as query,
            patch("es_readiness.time.sleep"),
        ):
            result = monitor.confirm(UploadContext("clip.mp4", "clip", "returned-uuid", {}, 60, 30))
        self.assertTrue(result.confirmed)
        self.assertEqual(query.call_count, 2)

    def test_removed_flags_are_rejected_before_validation(self):
        for flag in ("--expect-embed", "--expect-raw"):
            for value in ("false", "true"):
                with self.subTest(flag=flag, value=value), redirect_stderr(io.StringIO()), patch("run.validate") as validate:
                    with self.assertRaises(SystemExit) as error:
                        run.main(["--no-config", flag, value])
                    self.assertEqual(error.exception.code, 2)
                    validate.assert_not_called()

    def test_removed_yaml_keys_are_rejected_before_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "old.yml"
            for key in ("expect_embed", "expect_raw"):
                for value in ("false", "true"):
                    config.write_text(f"es_readiness:\n  {key}: {value}\n")
                    output = io.StringIO()
                    with self.subTest(key=key, value=value), redirect_stdout(output), patch("run.validate") as validate:
                        self.assertEqual(run.main(["--config", str(config)]), 2)
                        validate.assert_not_called()
                    self.assertIn("always requires both Raw and Embed", output.getvalue())

    def test_shipped_defaults_honor_selected_checkout_and_cli_configuration(self):
        config = Path(__file__).resolve().parents[1] / "config.yml"
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory) / "a-different-fork-name"
            cli_home = Path(directory) / "deployment-config"
            with patch.dict(os.environ, {"VSS_REPO_ROOT": str(repo), "VSS_CONFIG_HOME": str(cli_home)}):
                args = run.parse_args(["--config", str(config)])
                cli = VssCli(args.vss_repo, args.cli_config_home, executable=sys.executable)
            self.assertEqual(args.vss_repo, repo)
            self.assertEqual(cli.config_home, str(cli_home))
            self.assertIsNone(args.corpus)
            self.assertEqual(args.elasticsearch_url, "")
            self.assertEqual(resolve_matrix(args.profile, args.classes, args.concurrencies), (["50MB"], [1]))


if __name__ == "__main__":
    unittest.main()
