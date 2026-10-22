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

"""Workspace packaging and coverage configuration regression tests."""

from __future__ import annotations

import json
import subprocess
import sys
import tomllib
from pathlib import Path


def test_coverage_source_lists_every_namespace_package_portion(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    config = tomllib.loads((root / "pyproject.toml").read_text())
    sources = config["tool"]["coverage"]["run"]["source"]

    expected = {
        "src/vss",
        "packages/ingestion/src/vss",
        "packages/summarization/src/vss",
    }
    assert expected <= set(sources)
    assert all((root / source).is_dir() for source in sources)

    script = """
import json
import sys
from coverage import Coverage
from pathlib import Path

root = Path.cwd()
coverage = Coverage(config_file="pyproject.toml", data_file=sys.argv[1])
coverage.start()
import vss.storage
coverage.stop()
coverage.save()
files = [
    Path(filename).resolve().relative_to(root).as_posix()
    for filename in coverage.get_data().measured_files()
]
print(json.dumps(files))
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path / ".coverage")],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    )
    measured = set(json.loads(result.stdout))
    assert {
        "src/vss/video_processing/execution.py",
        "packages/ingestion/src/vss/ingestion/backend.py",
        "packages/summarization/src/vss/summarization/service.py",
    } <= measured
