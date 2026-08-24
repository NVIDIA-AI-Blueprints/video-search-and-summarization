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


def test_configure_check_reports_which_groups_the_deployment_can_serve() -> None:
    """`show` says what you have, `--help` says what a command needs.

    Neither is useful alone, and doing the join by hand is the thing an
    operator should not have to do.
    """
    from vss_cli import config as config_mod
    from vss_cli.configure import _command_availability

    deployment = config_mod.Deployment(
        base_url="https://vss.test",
        services={"vst": config_mod.Service(url="https://vss.test/vst")},
        written_at="2026-08-20T00:00:00+00:00",
    )

    rows = {name: (ok, detail) for name, ok, detail in _command_availability(deployment)}

    assert rows["vios"][0] is True
    assert rows["search"][0] is False
    assert "elasticsearch" in rows["search"][1]


def test_stderr_stays_quiet_when_an_env_file_is_present(tmp_path, monkeypatch) -> None:
    """The configured case is the common one; it was still printing two INFO lines."""
    import importlib
    import logging

    sitecustomize = importlib.import_module("sitecustomize")
    env = tmp_path / "real.env"
    env.write_text("EXAMPLE=1\n")
    pointer = tmp_path / ".env_file"
    pointer.write_text(str(env))
    monkeypatch.setattr(sitecustomize, "__file__", str(tmp_path / "pkg" / "sitecustomize.py"))
    # Stub the loader so this exercises the branch it is named for. The CLI's
    # own runtime has no python-dotenv -- it arrives with the agent stack -- so
    # without this the module takes the "not installed" path and warns, and the
    # test passes or fails on which environment happened to run it.
    monkeypatch.setattr(sitecustomize, "load_dotenv", lambda *_a, **_k: None)

    records: list[logging.LogRecord] = []
    handler = logging.Handler()
    handler.emit = records.append  # type: ignore[method-assign]
    logging.getLogger("sitecustomize").addHandler(handler)
    try:
        sitecustomize._auto_load_env_files()
    finally:
        logging.getLogger("sitecustomize").removeHandler(handler)

    assert [r.getMessage() for r in records if r.levelno >= logging.INFO] == []


def test_configure_check_prints_its_block_on_one_stream() -> None:
    """A header on stderr and rows on stdout is a block that survives neither redirect."""
    import inspect

    from vss_cli import configure as configure_mod

    src = inspect.getsource(configure_mod.check.callback)
    header = next(line for line in src.splitlines() if '"commands:"' in line)
    assert "err=True" not in header, header
