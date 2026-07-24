# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for vss_agents/orchestrator/network_util.py."""

import subprocess
from unittest import mock

from vss_agents.orchestrator import network_util


def test_run_text_command_returns_stdout_on_success():
    result = subprocess.CompletedProcess(["echo"], 0, stdout="  10.0.0.1\n", stderr="")
    with mock.patch.object(network_util.subprocess, "run", return_value=result):
        assert network_util.run_text_command(["echo"]) == "10.0.0.1"


def test_run_text_command_returns_empty_on_failure_or_missing_binary():
    result = subprocess.CompletedProcess(["false"], 1, stdout="nope", stderr="")
    with mock.patch.object(network_util.subprocess, "run", return_value=result):
        assert network_util.run_text_command(["false"]) == ""

    with mock.patch.object(network_util.subprocess, "run", side_effect=FileNotFoundError):
        assert network_util.run_text_command(["missing"]) == ""


def test_detect_internal_ip_parses_src_from_route():
    with mock.patch.object(
        network_util,
        "run_text_command",
        return_value="1.1.1.1 via 10.0.0.1 dev eth0 src 10.0.0.5 uid 1000",
    ):
        assert network_util.detect_internal_ip() == "10.0.0.5"


def test_detect_internal_ip_returns_empty_when_route_unavailable():
    with mock.patch.object(network_util, "run_text_command", return_value=""):
        assert network_util.detect_internal_ip() == ""


def test_detect_external_ip_returns_first_successful_lookup():
    with mock.patch.object(
        network_util,
        "run_text_command",
        side_effect=["", "203.0.113.10"],
    ) as run_text:
        assert network_util.detect_external_ip() == "203.0.113.10"
        assert run_text.call_count == 2


def test_detect_external_ip_returns_empty_when_all_lookups_fail():
    with mock.patch.object(network_util, "run_text_command", return_value=""):
        assert network_util.detect_external_ip() == ""
