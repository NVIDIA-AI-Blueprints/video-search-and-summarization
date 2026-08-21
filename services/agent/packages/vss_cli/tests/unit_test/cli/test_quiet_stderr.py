# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""A successful command writes nothing to stderr.

stderr is where the CLI puts diagnostics, and a harness is told to treat a
message there as something to look at. Anything printed unconditionally --
startup chatter, an optional file that was not found -- trains the caller to
ignore the channel that matters.
"""

from __future__ import annotations

import subprocess
import sys


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", "from vss_cli import main; main()", *args],
        capture_output=True,
        text=True,
        check=False,
    )


def test_a_successful_invocation_writes_nothing_to_stderr() -> None:
    result = _run("--version")

    assert result.returncode == 0
    assert result.stdout.strip()
    assert result.stderr == "", f"unexpected stderr: {result.stderr!r}"


def test_help_is_also_quiet() -> None:
    result = _run("vios", "--help")

    assert result.returncode == 0
    assert result.stderr == "", f"unexpected stderr: {result.stderr!r}"


def test_sitecustomize_does_not_announce_a_missing_optional_pointer(caplog) -> None:
    """The pointer file is optional; its absence is the ordinary case."""
    import importlib
    import logging

    sitecustomize = importlib.import_module("sitecustomize")

    with caplog.at_level(logging.INFO, logger="sitecustomize"):
        sitecustomize._auto_load_env_files()

    assert not [r for r in caplog.records if ".env_file not found" in r.getMessage()]


def test_help_states_what_a_command_needs() -> None:
    """Learning a command needs Elasticsearch by running it is poor documentation."""
    from vss_cli.group import requires_note

    assert requires_note(frozenset({"elasticsearch", "rt_embed"})) == (
        "\n\nRequires: elasticsearch, rt_embed (see `vss configure show`)."
    )
    assert requires_note(frozenset()) == ""


def test_a_vios_command_advertises_its_backend() -> None:
    result = _run("vios", "list", "--help")

    assert result.returncode == 0
    assert "Requires: vst" in result.stdout
