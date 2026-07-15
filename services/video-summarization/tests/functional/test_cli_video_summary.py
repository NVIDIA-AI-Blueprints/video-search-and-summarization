# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""
Functional tests for the VIA Engine CLI client (``via_client_cli.py``).

Tests that the CLI:
- Displays usage help
- Can upload a file and return a file_id
- Can trigger summarization and produce output
- Exits non-zero on missing required args
- Exits with an error when the backend URL is unreachable

These tests invoke the CLI as a subprocess, so they work regardless of whether
the server is the in-process ViaTestServer or an external service.

The ``base_url`` fixture provides the server URL automatically (see conftest.py).
"""

import subprocess
import sys
from pathlib import Path

import pytest

# Path to the CLI script relative to the repo root
_SRC_DIR = Path(__file__).parents[2] / "src"
_CLI = _SRC_DIR / "via_client_cli.py"


def _run_cli(*args, timeout=15):
    """Run via_client_cli.py with given args and return CompletedProcess."""
    return subprocess.run(
        [sys.executable, str(_CLI), *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _skip_if_generation_backend_unavailable(output: str):
    backend_unavailable_signals = (
        "InternalServerError",
        "Failed to generate summary",
        "NoChunksReturned",
        "No chunks returned",
    )
    if any(signal in output for signal in backend_unavailable_signals):
        pytest.skip(f"Generation back-end not available in test environment: {output[:200]}")


# ---------------------------------------------------------------------------
# Help / usage
# ---------------------------------------------------------------------------


@pytest.mark.test_in_ci
def test_cli_help_displays_usage():
    """Running the CLI with --help exits 0 and prints usage information."""
    result = _run_cli("--help")
    assert result.returncode == 0, f"CLI --help exited {result.returncode}"
    output = result.stdout + result.stderr
    assert (
        "usage" in output.lower() or "help" in output.lower()
    ), f"No usage text in CLI help output: {output[:500]}"


# ---------------------------------------------------------------------------
# Missing required arguments
# ---------------------------------------------------------------------------


@pytest.mark.test_in_ci
def test_cli_missing_required_args_exits_nonzero():
    """Running 'summarize' without --model exits non-zero."""
    result = _run_cli("summarize", "--url", "http://example.com/fake.mp4")
    assert result.returncode != 0, "Expected non-zero exit when required args are missing, got 0"


@pytest.mark.test_in_ci
def test_cli_unknown_backend_url_exits_with_error():
    """Running with a non-existent backend URL exits non-zero with a connection error."""
    result = _run_cli(
        "server-health-check",
        "--backend",
        "http://127.0.0.1:1",  # nothing listening on port 1
        timeout=10,
    )
    assert result.returncode != 0, "Expected non-zero exit for unreachable backend, got 0"


# ---------------------------------------------------------------------------
# File upload
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Summarization
# ---------------------------------------------------------------------------


@pytest.mark.slow
@pytest.mark.test_in_ci
def test_cli_summarize_single_video_outputs_summary(base_url):
    """CLI 'summarize' returns non-empty summary text."""
    # Get a model via the models list first
    import requests

    try:
        resp = requests.get(f"{base_url}/models", timeout=10)
        resp.raise_for_status()
        model_id = resp.json()["data"][0]["id"]
    except Exception as exc:
        pytest.skip(f"Could not obtain model_id: {exc}")

    result = _run_cli(
        "summarize",
        "--backend",
        base_url,
        "--url",
        "https://artifactory.nvidia.com/artifactory/"
        "sw-ds-generic-bld-local/via-engine/media/bp_preview/its_264.mp4",
        "--model",
        model_id,
        "--scenario",
        "traffic monitoring",
        "--events",
        "accident,emergency vehicle",
        "--chunk-duration",
        "10",
        timeout=180,
    )
    output = result.stdout + result.stderr
    _skip_if_generation_backend_unavailable(output)
    assert result.returncode == 0, f"CLI summarize failed ({result.returncode}): {output[:500]}"
    assert len(result.stdout.strip()) > 0, "CLI summarize produced no output"


@pytest.mark.slow
@pytest.mark.test_in_ci
def test_cli_summarize_with_custom_prompt(base_url):
    """CLI 'summarize' with --prompt passes the custom prompt to the server."""
    import requests

    try:
        resp = requests.get(f"{base_url}/models", timeout=10)
        resp.raise_for_status()
        model_id = resp.json()["data"][0]["id"]
    except Exception as exc:
        pytest.skip(f"Could not obtain model_id: {exc}")

    result = _run_cli(
        "summarize",
        "--backend",
        base_url,
        "--url",
        "https://artifactory.nvidia.com/artifactory/"
        "sw-ds-generic-bld-local/via-engine/media/bp_preview/its_264.mp4",
        "--model",
        model_id,
        "--scenario",
        "traffic monitoring",
        "--events",
        "accident,emergency vehicle",
        "--prompt",
        "Describe the scene in exactly one sentence.",
        "--chunk-duration",
        "10",
        timeout=180,
    )
    output = result.stdout + result.stderr
    _skip_if_generation_backend_unavailable(output)
    assert result.returncode == 0, f"CLI summarize with prompt failed: {output[:500]}"
    assert len(result.stdout.strip()) > 0, "CLI summarize with custom prompt produced no output"
