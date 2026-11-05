# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Fail-closed version lookup and benchmark ordering, with no live uploads."""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError
from urllib.parse import urlunsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import check_compatibility as compatibility
from config import ConfigError, load_config, resolve_defaults
import run
import validate


DEPLOYMENT = {
    "base_url": "http://fixture.invalid",
    "services": {
        "vst": {"url": "http://fixture.invalid/vst"},
        "elasticsearch": {"url": "http://fixture.invalid/elasticsearch"},
    },
}
EVIDENCE = {
    "status": "compatible",
    "skill_version": "v3.3.0",
    "deployed_vss_version": "3.3.0+tree.fixture",
    "requires_vss": ">=3.3.0,<3.4.0",
    "version_url": "http://fixture.invalid/api/v1/version",
    "checked_at_utc": "2026-09-28T00:00:00+00:00",
}


def response(payload, status=200):
    result = io.BytesIO(json.dumps(payload).encode())
    result.status = status
    return result


class CompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.repo = Path(__file__).resolve().parents[4]
        self.shared, self.checker_path = compatibility.load_shared_checker(self.repo)
        _, self.requirement, self.skill_version = self.shared.requirement_from_skill(compatibility.SKILL_PATH)

    def test_real_api_contract_patch_acceptance_and_no_es_auth(self):
        for version in ("3.3.0", "3.3.1", "3.3.99", "3.3.0+tree.abc", "3.3.1-dev.12+tree.abc"):
            with (
                self.subTest(version=version),
                patch.dict("os.environ", {"VSS_AUTH_TOKEN": "es-only-token"}),
                patch("check_compatibility.urlopen", return_value=response({"service": "vss", "version": version})) as request,
            ):
                result = compatibility.check_compatibility(DEPLOYMENT, timeout_sec=7, vss_repo=self.repo)
                self.assertEqual(result["deployed_vss_version"], version)
                self.assertEqual(result["status"], "compatible")
                self.assertEqual(result["requires_vss"], ">=3.3.0,<3.4.0")
                self.assertEqual(result["skill_version"], self.skill_version)
                self.assertEqual(result["skill_file"], str(compatibility.SKILL_PATH))
                self.assertEqual(result["checker_file"], str(self.checker_path))
                sent = request.call_args.args[0]
                self.assertEqual(sent.full_url, "http://fixture.invalid/api/v1/version")
                self.assertEqual(sent.get_method(), "GET")
                self.assertIsNone(sent.get_header("Authorization"))
                self.assertEqual(request.call_args.kwargs["timeout"], 7)
                request.assert_called_once()

    def test_shared_checker_owns_requirement_parsing_and_comparison(self):
        with (
            patch("check_compatibility.load_shared_checker", return_value=(self.shared, self.checker_path)),
            patch.object(self.shared, "requirement_from_skill", wraps=self.shared.requirement_from_skill) as metadata,
            patch.object(self.shared, "satisfies", wraps=self.shared.satisfies) as compare,
            patch("check_compatibility.urlopen", return_value=response({"service": "vss", "version": "3.3.1"})),
        ):
            compatibility.check_compatibility(DEPLOYMENT, vss_repo=self.repo)
        metadata.assert_called_once_with(compatibility.SKILL_PATH)
        compare.assert_called_once_with("3.3.1", self.shared.parse_requirement(self.requirement))

    def test_wrapper_and_documented_shared_skill_command_agree(self):
        for version, expected in [("3.3.1", 0), ("3.4.0", 3), ("03.3.0", 1)]:
            with (
                self.subTest(version=version),
                patch.object(sys, "argv", [str(self.checker_path), DEPLOYMENT["base_url"], "--skill", str(compatibility.SKILL_PATH)]),
                patch.object(self.shared, "urlopen", return_value=response({"service": "vss", "version": version})),
                patch("check_compatibility.urlopen", return_value=response({"service": "vss", "version": version})),
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()),
            ):
                self.assertEqual(self.shared.main(), expected)
                self.assertEqual(compatibility.main([
                    "--no-config", "--vss-repo", str(self.repo), "--base-url", DEPLOYMENT["base_url"],
                ]), expected)

    def test_incompatible_release_is_distinct_from_indeterminate(self):
        for version in ("3.2.1", "3.4.0", "4.0.0"):
            with (
                self.subTest(version=version),
                patch("check_compatibility.urlopen", return_value=response({"service": "vss", "version": version})),
                self.assertRaisesRegex(compatibility.CompatibilityError, "incompatible") as raised,
            ):
                compatibility.check_compatibility(DEPLOYMENT, vss_repo=self.repo)
            self.assertEqual(raised.exception.exit_code, 3)

    def test_failed_lookups_never_return_compatibility_or_retry(self):
        errors = [HTTPError("http://fixture.invalid", c, "failure", {}, None) for c in (401, 403, 404, 503)]
        errors.extend([URLError("offline"), TimeoutError("deadline"), OSError("connection reset")])
        for error in errors:
            with self.subTest(error=error), patch("check_compatibility.urlopen", side_effect=error) as request:
                with self.assertRaises(compatibility.CompatibilityError) as raised:
                    compatibility.check_compatibility(DEPLOYMENT, vss_repo=self.repo)
                self.assertEqual(raised.exception.exit_code, 1)
                request.assert_called_once()

    def test_payload_shape_and_semver_are_strict(self):
        payloads = [[], {}, {"service": "vss", "version": None}, {"service": "vst", "version": "3.3.0"}]
        payloads.extend({"service": "vss", "version": v} for v in ("03.3.0", "v3.3.0", "3.3", "3.3.0-01", "3.3.0-."))
        for payload in payloads:
            with self.subTest(payload=payload), patch("check_compatibility.urlopen", return_value=response(payload)):
                with self.assertRaises(compatibility.CompatibilityError) as raised:
                    compatibility.check_compatibility(DEPLOYMENT, vss_repo=self.repo)
                self.assertEqual(raised.exception.exit_code, 1)
        bad_json = io.BytesIO(b"not JSON")
        bad_json.status = 200
        with (patch("check_compatibility.urlopen", return_value=bad_json),
              self.assertRaisesRegex(compatibility.CompatibilityError, "invalid JSON")):
            compatibility.check_compatibility(DEPLOYMENT, vss_repo=self.repo)

    def test_invalid_timeout_and_secret_bearing_url_fail_before_request(self):
        # Build synthetic userinfo without a credential-shaped URL literal for secret scanners.
        credential_url = urlunsplit(("http", "user:secret@fixture.invalid", "/version", "", ""))
        with patch("check_compatibility.urlopen") as request:
            for timeout in (0, -1, float("nan"), float("inf"), None, "ten", True):
                with self.subTest(timeout=timeout), self.assertRaises(compatibility.CompatibilityError):
                    compatibility.check_compatibility(DEPLOYMENT, timeout_sec=timeout, vss_repo=self.repo)
            for url in (
                credential_url, "http://fixture.invalid/version?token=secret",
                "http://fixture.invalid/version#fragment", "file:///version", "http://fixture.invalid:bad/version",
            ):
                with self.subTest(url=url), self.assertRaises(compatibility.CompatibilityError):
                    compatibility.check_compatibility(DEPLOYMENT, version_url=url, vss_repo=self.repo)
            request.assert_not_called()

    def test_missing_or_invalid_skill_frontmatter_fails_before_request(self):
        with tempfile.TemporaryDirectory() as d:
            skill = Path(d) / "SKILL.md"
            for text in (
                "[]", "---\nname: other\n---\n", "---\nmetadata:\n  version: '3.3.0'\n---\n",
                "---\nmetadata:\n  version: '3.3.0'\n  requires-vss: '*'\n---\n",
                "---\nmetadata:\n  version: '3.3.0'\n  requires-vss: '>=3.3.0,'\n---\n",
                "---\nmetadata:\n  requires-vss: '>=3.3.0'\n---\n",
            ):
                with self.subTest(text=text), patch("check_compatibility.urlopen") as request:
                    skill.write_text(text)
                    with self.assertRaises(compatibility.CompatibilityError) as raised:
                        compatibility.check_compatibility(DEPLOYMENT, vss_repo=self.repo, skill_path=skill)
                    self.assertEqual(raised.exception.exit_code, 1)
                    request.assert_not_called()

    def test_installed_skill_reads_its_stamp_and_comparison_from_prepared_checkout(self):
        with tempfile.TemporaryDirectory() as d:
            skill = Path(d) / "installed-skill" / "SKILL.md"
            skill.parent.mkdir()
            for version in ("3.3.0-rc0", "3.3.1"):
                skill.write_text(f'---\nmetadata:\n  version: "{version}"\n  requires-vss: ">=3.3.0,<3.4.0"\n---\n')
                with patch("check_compatibility.urlopen", return_value=response({"service": "vss", "version": "3.3.1"})):
                    evidence = compatibility.check_compatibility(DEPLOYMENT, vss_repo=self.repo, skill_path=skill)
                self.assertEqual(evidence["skill_version"], version)
                self.assertEqual(evidence["skill_file"], str(skill))
                self.assertEqual(evidence["checker_file"], str(self.repo / compatibility.SHARED_CHECKER))
            with patch("check_compatibility.urlopen") as request, self.assertRaisesRegex(compatibility.CompatibilityError, "Shared version checker is missing"):
                compatibility.check_compatibility(DEPLOYMENT, vss_repo=Path(d), skill_path=skill)
            request.assert_not_called()

    def test_standalone_modes_keep_cli_discovery_and_explicit_public_route(self):
        for explicit in ([], ["--version-url", "http://fixture.invalid/custom/version"]):
            output = io.StringIO()
            cli_instance = Mock(deployment=Mock(return_value=DEPLOYMENT))
            with (
                patch("check_compatibility.VssCli", return_value=cli_instance) as cli,
                patch("check_compatibility.urlopen", return_value=response({"service": "vss", "version": "3.3.1"})) as request,
                redirect_stdout(output),
            ):
                code = compatibility.main(["--no-config", "--vss-repo", str(self.repo), *explicit])
            self.assertEqual(code, 0)
            url = explicit[-1] if explicit else "http://fixture.invalid/api/v1/version"
            self.assertEqual(json.loads(output.getvalue())["version_url"], url)
            self.assertEqual(request.call_args.args[0].full_url, url)
            if explicit:
                cli.assert_not_called()
            else:
                cli.assert_called_once_with(self.repo, None, executable=None)
                cli_instance.deployment.assert_called_once_with(check_health=False)
        with patch("check_compatibility.urlopen", side_effect=URLError("offline")), redirect_stderr(io.StringIO()):
            self.assertEqual(compatibility.main(["--no-config", "--vss-repo", str(self.repo), "--base-url", "http://fixture.invalid"]), 1)

    def test_config_options_and_flags_resolve_consistently(self):
        with tempfile.TemporaryDirectory() as d:
            config = Path(d) / "config.yml"
            config.write_text(
                "cli:\n  executable: ./tools/vss\ncompatibility:\n  version_url: http://fixture.invalid/version\n  request_timeout_sec: 7\n"
            )
            flat = load_config(config, explicit=True)
            scalars, _ = resolve_defaults(flat, config.parent)
            self.assertEqual(scalars["cli_executable"], str(config.parent / "tools/vss"))
            self.assertEqual(scalars["version_timeout"], 7)
            args = run.parse_args(["--config", str(config), "--version-timeout", "4"])
            self.assertEqual(args.version_timeout, 4)
            self.assertEqual(args.version_url, "http://fixture.invalid/version")
            config.write_text("compatibility:\n  request_timeout_sec: not-a-number\n")
            with self.assertRaises(ConfigError):
                resolve_defaults(load_config(config), config.parent)


class CompatibilityGateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        corpus = self.root / "50MB"
        corpus.mkdir()
        (corpus / "fixture.mp4").write_bytes(b"fixture")
        self.cli = Mock(deployment=Mock(return_value=DEPLOYMENT))
        self.options = {
            "vss_repo": self.root,
            "cli_config_home": None,
            "elasticsearch_url": "",
            "corpus_root": self.root,
            "profile": "smoke",
            "classes": ["50MB"],
            "concurrencies": [1],
            "readiness_poll_interval_sec": 5,
            "readiness_timeout_sec": None,
            "es_request_timeout_sec": 60,
            "results_dir": self.root / "results",
        }

    def test_failed_gate_prevents_ffprobe_even_without_health_probe(self):
        with (
            patch("validate.VssCli", return_value=self.cli),
            patch(
                "validate.check_compatibility", side_effect=compatibility.CompatibilityError("version unknown")
            ) as check,
            patch("validate.probe_class") as probe,
            patch("validate.ffprobe_available") as available,
        ):
            result = validate.validate(**self.options, check_health=False)
        self.assertFalse(result.ok)
        self.assertIn("version unknown", result.report())
        check.assert_called_once()
        self.cli.deployment.assert_called_once_with(check_health=False)
        probe.assert_not_called()
        available.assert_not_called()
        self.cli.call.assert_not_called()

    def test_successful_gate_precedes_corpus_measurement_and_records_evidence(self):
        events = []
        with (
            patch("validate.VssCli", return_value=self.cli),
            patch(
                "validate.check_compatibility", side_effect=lambda *a, **kw: events.append("version") or EVIDENCE.copy()
            ),
            patch("validate.probe_class", side_effect=lambda *a: events.append("ffprobe")),
            patch("validate.ffprobe_available", return_value=True),
        ):
            result = validate.validate(**self.options)
        self.assertTrue(result.ok, result.report())
        self.assertEqual(events, ["version", "ffprobe"])
        self.assertEqual(result.version_compatibility, EVIDENCE)

    def test_direct_run_and_dry_run_cannot_bypass_failed_version_lookup(self):
        for dry in ([], ["--dry-run"]):
            with (
                self.subTest(dry=dry),
                patch("validate.VssCli", return_value=self.cli),
                patch(
                    "validate.check_compatibility", side_effect=compatibility.CompatibilityError("version incompatible", exit_code=3)
                ),
                patch("validate.probe_class") as probe,
                patch("run.load_class") as load,
                patch("run.upload_one") as upload,
                patch("run.EsReadinessMonitor") as es,
                redirect_stdout(io.StringIO()),
            ):
                code = run.main(
                    [
                        "--no-config",
                        "--profile",
                        "smoke",
                        "--corpus",
                        str(self.root),
                        "--results-dir",
                        str(self.root / "results"),
                        *dry,
                    ]
                )
                self.assertEqual(code, 3)
                probe.assert_not_called()
                load.assert_not_called()
                upload.assert_not_called()
                es.assert_not_called()

    def test_noop_set_override_is_rejected_with_native_flag_guidance(self):
        with (
            patch("validate.VssCli", return_value=self.cli),
            patch("validate.check_compatibility") as gate,
            patch("validate.probe_class") as probe,
        ):
            result = validate.validate(**self.options, overrides={"concurrency": "10"})
        self.assertFalse(result.ok)
        self.assertIn("not applied by this runner", result.report())
        self.assertIn("--concurrency", result.report())
        gate.assert_not_called()
        probe.assert_not_called()

    def test_validated_measurements_are_reused_for_corpus_csv(self):
        from corpus import load_class

        spec = {
            "duration_sec": 60,
            "fps": 30,
            "width": 1920,
            "height": 1080,
            "resolution": "1920x1080",
            "codec": "h264",
            "container": "mp4",
        }
        events = []
        with (
            patch("validate.VssCli", return_value=self.cli),
            patch("validate.check_compatibility", return_value=EVIDENCE.copy()),
            patch("validate.probe_video", return_value=spec.copy()) as probe,
            patch("validate.ffprobe_available", return_value=True),
        ):
            result = validate.validate(**self.options, progress=events.append)
        self.assertTrue(result.ok, result.report())
        probe.assert_called_once()
        self.assertEqual([event[:5] for event in events], ["[2/8]", "[3/8]"])
        with patch("corpus.probe_video", side_effect=AssertionError("measured twice")):
            items = load_class(self.root, "50MB", measurements=result.measurements)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].duration_sec, 60)
        self.assertEqual(items[0].bytes, len(b"fixture"))

    def test_standalone_validate_enforces_gate_without_health_check(self):
        with (
            patch("validate.VssCli", return_value=self.cli),
            patch(
                "validate.check_compatibility", side_effect=compatibility.CompatibilityError("version unavailable")
            ) as gate,
            patch("validate.probe_class") as probe,
            redirect_stdout(io.StringIO()),
        ):
            code = validate.main(
                [
                    "--no-config",
                    "--profile",
                    "smoke",
                    "--corpus",
                    str(self.root),
                    "--results-dir",
                    str(self.root / "results"),
                    "--no-health-check",
                ]
            )
        self.assertEqual(code, 1)
        gate.assert_called_once()
        probe.assert_not_called()


if __name__ == "__main__":
    unittest.main()
