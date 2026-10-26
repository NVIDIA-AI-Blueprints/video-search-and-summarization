# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Regression checks for the split namespace workspace, independent of M8."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from vss.summarization import OpenAISummaryGenerator

ROOT = Path(__file__).resolve().parents[1]


def test_namespace_coverage_counts_unimported_components(tmp_path):
    report = tmp_path / "coverage.json"
    code = """
import sys
from coverage import Coverage
c = Coverage(data_file=sys.argv[1] + ".data")
c.start()
import vss.storage
c.stop()
c.save()
c.json_report(outfile=sys.argv[1])
"""
    subprocess.run(
        [sys.executable, "-c", code, str(report)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    files = {
        path.replace("\\", "/") for path in json.loads(report.read_text())["files"]
    }
    for prefix in (
        "src/vss/video_processing/",
        "packages/ingestion/src/vss/",
        "packages/summarization/src/vss/",
    ):
        assert any(path.startswith(prefix) for path in files), prefix


def test_missing_openai_recommends_summarization_distribution(monkeypatch):
    monkeypatch.setitem(sys.modules, "openai", None)
    with pytest.raises(RuntimeError) as error:
        OpenAISummaryGenerator(
            model="fixture",
            base_url="http://unused.invalid/v1",
            api_key="fixture",
        )
    assert 'pip install "vss-summarization[openai]"' in str(error.value)


def test_wheel_check_rejects_stale_duplicate_distributions(tmp_path, monkeypatch):
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "wheel_check", ROOT / ".github/scripts/check_wheel.py"
    )
    check = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(check)
    dist = tmp_path / "dist"
    dist.mkdir()
    for name in (
        "vss-0.1.0",
        "vss-0.2.0",
        "vss_ingestion-0.1.0",
        "vss_summarization-0.1.0",
    ):
        (dist / (name + "-py3-none-any.whl")).touch()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["check_wheel.py"])
    with pytest.raises(RuntimeError, match="three workspace wheels"):
        check.main()
