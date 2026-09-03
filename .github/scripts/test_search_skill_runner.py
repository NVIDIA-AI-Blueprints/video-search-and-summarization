# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for the deterministic archive-search skill runner."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
RUNNER = REPO_ROOT / "skills/operations/vss-search-archive/scripts/run_search.sh"


class SearchSkillRunnerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.vss_root = self.root / "checkout"
        agent = self.vss_root / "services" / "agent"
        agent.mkdir(parents=True)
        (agent / "pyproject.toml").write_text("[project]\nname='fixture'\n")

        self.bin_dir = self.root / "bin"
        self.bin_dir.mkdir()
        fake_uv = self.bin_dir / "uv"
        fake_uv.write_text(
            """#!/usr/bin/env python3
import os
import sys

args = sys.argv[1:]
try:
    command = args[args.index("vss") + 1:]
except ValueError:
    sys.exit(90)
if command == ["search", "run", "--help"]:
    sys.exit(0)
if command[:2] == ["configure", "--base-url"]:
    sys.exit(0)
if command == ["vios", "list"]:
    print(os.environ["FAKE_VIOS_LIST"])
    sys.exit(0)
if command[:2] == ["search", "run"]:
    print(os.environ["FAKE_SEARCH_OUTPUT"])
    sys.exit(int(os.environ.get("FAKE_SEARCH_EXIT", "0")))
sys.exit(91)
"""
        )
        fake_uv.chmod(0o755)

        self.receipt = self.root / "agent-capabilities.json"
        self.receipt.write_text(
            json.dumps({"ui_artifacts": {"version": "1.0"}}), encoding="utf-8"
        )
        self.environment = {
            **os.environ,
            "HOME": str(self.root),
            "PATH": f"{self.bin_dir}:{os.environ['PATH']}",
            "VSS_REPO_ROOT": str(self.vss_root),
            "VSS_ORIGIN": "https://vss.example.test",
            "VSS_CAPABILITY_RECEIPT": str(self.receipt),
            "FAKE_VIOS_LIST": '{"count":0,"sensors":[]}',
            "FAKE_SEARCH_OUTPUT": "",
        }

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def run_runner(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(RUNNER), *arguments],
            text=True,
            capture_output=True,
            env=self.environment,
            check=False,
        )

    def test_lists_sources(self) -> None:
        result = self.run_runner("--list-sources")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"count": 0, "sensors": []})

    def test_refuses_to_broaden_source_scoped_search(self) -> None:
        result = self.run_runner(
            "--source-scoped", "true", "--", "embed", "--query", "person"
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("refusing an unrestricted search", result.stderr)

    def test_accepts_equals_form_video_source(self) -> None:
        self.set_valid_search_output()

        result = self.run_runner(
            "--source-scoped",
            "true",
            "--",
            "embed",
            "--video-source=sensor-1",
            "--query",
            "person",
            "--raw",
        )

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_emits_validated_cli_pair_and_ui_artifact(self) -> None:
        search_json, marker = self.set_valid_search_output()

        result = self.run_runner(
            "--source-scoped",
            "false",
            "--",
            "embed",
            "--query",
            "person",
            "--raw",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        lines = result.stdout.splitlines()
        self.assertEqual(json.loads(lines[0]), search_json)
        self.assertEqual(json.loads(lines[1]), marker)
        self.assertTrue(lines[2].startswith("<vss-ui-artifact>"))
        envelope = json.loads(
            lines[2].removeprefix("<vss-ui-artifact>").removesuffix(
                "</vss-ui-artifact>"
            )
        )
        self.assertEqual(envelope["version"], "1.0")
        self.assertEqual(envelope["kind"], "vss.search.results")
        self.assertEqual(envelope["payload"], search_json)

    def test_rejects_mismatched_completion_marker(self) -> None:
        search_json, marker = self.set_valid_search_output()
        marker["job_id"] = "different-job"
        self.environment["FAKE_SEARCH_OUTPUT"] = "\n".join(
            (json.dumps(search_json), json.dumps(marker))
        )

        result = self.run_runner(
            "--source-scoped", "false", "--", "embed", "--query", "person"
        )

        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertIn("completion marker did not validate", result.stderr)

    def test_rejects_extra_output_documents(self) -> None:
        search_json, marker = self.set_valid_search_output()
        self.environment["FAKE_SEARCH_OUTPUT"] = "\n".join(
            (json.dumps(search_json), json.dumps(marker), '{"unexpected":true}')
        )

        result = self.run_runner(
            "--source-scoped", "false", "--", "embed", "--query", "person"
        )

        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertIn("one result body and one completion marker", result.stderr)

    def set_valid_search_output(self) -> tuple[dict[str, object], dict[str, object]]:
        search_json: dict[str, object] = {
            "data": [],
            "job_id": "search-fixture",
        }
        marker: dict[str, object] = {
            "event": "vss_job_completed",
            "group": "search",
            "job_id": "search-fixture",
            "status": "completed",
            "exit_hint": 0,
        }
        self.environment["FAKE_SEARCH_OUTPUT"] = "\n".join(
            (json.dumps(search_json), json.dumps(marker))
        )
        return search_json, marker


if __name__ == "__main__":
    unittest.main()
