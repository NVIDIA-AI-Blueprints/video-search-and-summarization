#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).with_name("check_gpu_device_clamp.py")
SPEC = importlib.util.spec_from_file_location("check_gpu_device_clamp", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
LINT = importlib.util.module_from_spec(SPEC)
# Registered before exec: @dataclass resolves its annotations through
# sys.modules, and an unregistered module makes that lookup fail.
sys.modules[SPEC.name] = LINT
SPEC.loader.exec_module(LINT)

# A deploy script shaped like dev-profile.sh: the clamp arrays, the clamp
# itself, and a call from the middle of process_args.
GOOD_SCRIPT = """#!/bin/bash
DEVICE_ID_KEYS=(
  'LLM_DEVICE_ID'
  'RT_CV_DEVICE_ID'
)

DEVICE_RESERVATION_KEYS=(
  'RESERVED_DEVICE_IDS'
)

function get_deployment_gpu_count() {
  echo 1
}

function clamp_device_ids_to_gpu_count() {
  gpu_count="$(get_deployment_gpu_count)"
  case ",${_out}," in *",0,"*) return ;; esac
}

function process_args() {
  if [[ -n "${profile}" ]]; then
    if [[ "${profile}" == "search" ]]; then
      echo "search only"
    fi
    clamp_device_ids_to_gpu_count "${_profile_env}"
  fi
}
"""


def _write(directory: str, name: str, body: str) -> Path:
    path = Path(directory) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    return path


class TreeTest(unittest.TestCase):
    """The committed tree must satisfy the lint."""

    def test_tree_routes_every_device_index_through_the_clamp(self) -> None:
        self.assertEqual([], LINT.scan([]))

    def test_profiles_are_actually_covered(self) -> None:
        # A lint that silently matches nothing passes forever. The two-GPU
        # developer profiles and the three-GPU warehouse profile are the whole
        # reason this exists, so assert they are in scope.
        report: list[str] = []
        LINT.scan(report)
        text = "\n".join(report)
        for profile in ("dev-profile-alerts", "dev-profile-search", "warehouse-operations"):
            self.assertIn(profile, text, f"{profile} is not in scope: {text}")

    def test_report_states_the_derived_gpu_requirement(self) -> None:
        # Nothing declares how many GPUs a profile needs; it is derived from the
        # highest committed index. Alerts and search assume two, warehouse three.
        report: list[str] = []
        LINT.scan(report)
        text = "\n".join(report)
        self.assertIn("dev-profile-alerts: needs 2 GPU(s)", text)
        self.assertIn("dev-profile-search: needs 2 GPU(s)", text)
        self.assertIn("warehouse-operations: needs 3 GPU(s)", text)
        self.assertIn("dev-profile-base: needs 1 GPU(s)", text)


class ClampKeyCoverageTest(unittest.TestCase):
    def test_unclamped_placement_key_above_device_zero_fails(self) -> None:
        # The regression this guards: a new service placed on device 1 that the
        # clamp does not know about. Every value here is individually valid.
        with tempfile.TemporaryDirectory() as directory:
            _write(directory, "scripts/deploy.sh", GOOD_SCRIPT)
            _write(directory, "profiles/demo/.env", "RT_EMBED_DEVICE_ID='1'\n")
            failures = self._scan(directory)
        self.assertEqual(1, len(failures), failures)
        self.assertIn("RT_EMBED_DEVICE_ID", failures[0])
        self.assertIn("not listed in DEVICE_ID_KEYS", failures[0])

    def test_unclamped_key_on_device_zero_passes(self) -> None:
        # Device 0 exists on every GPU host, so an unclamped key pinned to 0 is
        # not a single-GPU hazard and must not be flagged.
        with tempfile.TemporaryDirectory() as directory:
            _write(directory, "scripts/deploy.sh", GOOD_SCRIPT)
            _write(directory, "profiles/demo/.env", "RT_EMBED_DEVICE_ID='0'\n")
            failures = self._scan(directory)
        self.assertEqual([], failures)

    def test_two_gpu_default_under_a_clamped_key_passes(self) -> None:
        # The point of clamping rather than rewriting defaults: a device-1
        # default is the validated configuration and must stay legal.
        with tempfile.TemporaryDirectory() as directory:
            _write(directory, "scripts/deploy.sh", GOOD_SCRIPT)
            _write(
                directory,
                "profiles/demo/.env",
                "LLM_DEVICE_ID='1'\nRT_CV_DEVICE_ID='0'\nRESERVED_DEVICE_IDS='0'\n",
            )
            failures = self._scan(directory)
        self.assertEqual([], failures)

    def test_interpolated_device_value_must_be_clamped(self) -> None:
        # The index is only known at deploy time, so the key has to be routed
        # through the clamp regardless of what it resolves to.
        with tempfile.TemporaryDirectory() as directory:
            _write(directory, "scripts/deploy.sh", GOOD_SCRIPT)
            _write(directory, "profiles/demo/.env", "RT_EMBED_DEVICE_ID=${SOME_ID:-1}\n")
            failures = self._scan(directory)
        self.assertEqual(1, len(failures), failures)
        self.assertIn("RT_EMBED_DEVICE_ID", failures[0])

    def test_non_numeric_device_index_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            _write(directory, "scripts/deploy.sh", GOOD_SCRIPT)
            _write(directory, "profiles/demo/.env", "LLM_DEVICE_ID='gpu1'\n")
            failures = self._scan(directory)
        self.assertEqual(1, len(failures), failures)
        self.assertIn("is not a device index", failures[0])

    def test_generated_env_is_out_of_scope(self) -> None:
        # generated.env is written by the deploy script from already-clamped
        # values; reading it back would flag the clamp's own output.
        with tempfile.TemporaryDirectory() as directory:
            _write(directory, "scripts/deploy.sh", GOOD_SCRIPT)
            _write(directory, "profiles/demo/generated.env", "RT_EMBED_DEVICE_ID=1\n")
            failures = self._scan(directory)
        self.assertEqual([], failures)

    def test_comments_are_not_flagged(self) -> None:
        # The profiles document the two-GPU layout in prose beside the
        # assignments, so a lint that read comments would fail on the
        # explanation of the rule it enforces.
        with tempfile.TemporaryDirectory() as directory:
            _write(directory, "scripts/deploy.sh", GOOD_SCRIPT)
            _write(
                directory,
                "profiles/demo/.env",
                "# GPU 0: RT-CV. GPU 1: RT_EMBED_DEVICE_ID='1' on 2-GPU hosts.\n"
                "RT_CV_DEVICE_ID='0'\n",
            )
            failures = self._scan(directory)
        self.assertEqual([], failures)

    def _scan(self, directory: str) -> list[str]:
        family = LINT.ProfileFamily(env_root="profiles", script="scripts/deploy.sh")
        original_root = LINT.ROOT
        LINT.ROOT = Path(directory)
        try:
            return LINT.scan_family(family, [])
        finally:
            LINT.ROOT = original_root


class ClampReachabilityTest(unittest.TestCase):
    """The clamp has to run for every profile on every host."""

    def test_missing_clamp_call_fails(self) -> None:
        script = GOOD_SCRIPT.replace(
            '    clamp_device_ids_to_gpu_count "${_profile_env}"\n', ""
        )
        failures = self._scan_script(script)
        self.assertTrue(any("never called" in failure for failure in failures), failures)

    def test_missing_clamp_definition_fails(self) -> None:
        script = GOOD_SCRIPT.replace("clamp_device_ids_to_gpu_count", "some_other_helper")
        failures = self._scan_script(script)
        self.assertTrue(any("is not defined" in failure for failure in failures), failures)

    def test_clamp_gated_on_brev_env_id_fails(self) -> None:
        # The original defect exactly: get_nvidia_smi_gpu_count existed and was
        # called from one BREV_ENV_ID-gated, search-only pre-flight, so nothing
        # covered the general case.
        script = GOOD_SCRIPT.replace(
            '    clamp_device_ids_to_gpu_count "${_profile_env}"\n',
            '    if [[ -n "${BREV_ENV_ID:-}" ]]; then\n'
            '      clamp_device_ids_to_gpu_count "${_profile_env}"\n'
            "    fi\n",
        )
        failures = self._scan_script(script)
        self.assertTrue(
            any("gated on a single profile or environment" in f for f in failures),
            failures,
        )
        self.assertTrue(any("BREV_ENV_ID" in failure for failure in failures), failures)

    def test_clamp_gated_on_one_profile_fails(self) -> None:
        script = GOOD_SCRIPT.replace(
            '    clamp_device_ids_to_gpu_count "${_profile_env}"\n',
            '    if [[ "${profile}" == "search" ]]; then\n'
            '      clamp_device_ids_to_gpu_count "${_profile_env}"\n'
            "    fi\n",
        )
        failures = self._scan_script(script)
        self.assertTrue(
            any("gated on a single profile or environment" in f for f in failures),
            failures,
        )

    def test_a_second_unscoped_call_rescues_a_scoped_one(self) -> None:
        # A profile-specific extra clamp is fine as long as one call still
        # covers everything.
        script = GOOD_SCRIPT.replace(
            '    clamp_device_ids_to_gpu_count "${_profile_env}"\n',
            '    if [[ "${profile}" == "search" ]]; then\n'
            '      clamp_device_ids_to_gpu_count "${_profile_env}"\n'
            "    fi\n"
            '    clamp_device_ids_to_gpu_count "${_profile_env}"\n',
        )
        self.assertEqual([], self._scan_script(script))

    def test_clamp_without_a_gpu_count_source_fails(self) -> None:
        script = GOOD_SCRIPT.replace("get_deployment_gpu_count", "always_two")
        failures = self._scan_script(script)
        self.assertTrue(any("no GPU count" in failure for failure in failures), failures)

    def test_missing_key_arrays_fail(self) -> None:
        script = GOOD_SCRIPT.replace("DEVICE_ID_KEYS=(", "UNUSED_KEYS=(").replace(
            "DEVICE_RESERVATION_KEYS=(", "MORE_UNUSED=("
        )
        failures = self._scan_script(script)
        self.assertTrue(any("no DEVICE_ID_KEYS" in failure for failure in failures), failures)

    def _scan_script(self, script: str) -> list[str]:
        with tempfile.TemporaryDirectory() as directory:
            _write(directory, "scripts/deploy.sh", script)
            _write(directory, "profiles/demo/.env", "LLM_DEVICE_ID='1'\n")
            family = LINT.ProfileFamily(env_root="profiles", script="scripts/deploy.sh")
            original_root = LINT.ROOT
            LINT.ROOT = Path(directory)
            try:
                return LINT.scan_family(family, [])
            finally:
                LINT.ROOT = original_root


class EnclosingConditionsTest(unittest.TestCase):
    """The upward walk must not mistake an already-closed sibling block for an
    enclosing one, or the reachability check reports conditions that do not
    apply to the call."""

    def test_sibling_block_above_the_call_is_not_enclosing(self) -> None:
        lines = [
            "function f() {",
            '  if [[ -n "${a}" ]]; then',
            '    if [[ "${profile}" == "search" ]]; then',
            "      echo sibling",
            "    fi",
            "    target",
            "  fi",
            "}",
        ]
        conditions = LINT.enclosing_conditions(lines, 5)
        self.assertEqual(['if [[ -n "${a}" ]]; then'], conditions)

    def test_enclosing_blocks_are_innermost_first(self) -> None:
        lines = [
            "function f() {",
            '  if [[ -n "${a}" ]]; then',
            '    if [[ -n "${BREV_ENV_ID:-}" ]]; then',
            "      target",
            "    fi",
            "  fi",
            "}",
        ]
        conditions = LINT.enclosing_conditions(lines, 3)
        self.assertEqual(
            ['if [[ -n "${BREV_ENV_ID:-}" ]]; then', 'if [[ -n "${a}" ]]; then'],
            conditions,
        )

    def test_walk_stops_at_the_function_boundary(self) -> None:
        lines = [
            'if [[ -n "${BREV_ENV_ID:-}" ]]; then',
            "  echo outside",
            "fi",
            "function f() {",
            "  target",
            "}",
        ]
        self.assertEqual([], LINT.enclosing_conditions(lines, 4))

    def test_earlier_branches_of_the_same_chain_are_not_reported(self) -> None:
        # dev-profile.sh calls the clamp inside `elif desired_state == "up"`.
        # The chain's `if desired_state == "down"` heads the same construct and
        # is a branch the call cannot be in, so it must not appear as one of the
        # call's conditions.
        lines = [
            "function f() {",
            '  if [[ "${desired_state}" == "down" ]]; then',
            "    echo down",
            '  elif [[ "${desired_state}" == "up" ]]; then',
            "    target",
            "  fi",
            "}",
        ]
        self.assertEqual(
            ['elif [[ "${desired_state}" == "up" ]]; then'],
            LINT.enclosing_conditions(lines, 4),
        )

    def test_single_line_case_does_not_shift_the_depth(self) -> None:
        # The clamp helpers use `case ",${x}," in *",${y},"*) continue ;; esac`
        # on one line; counting it as an opener would misattribute conditions.
        lines = [
            "function f() {",
            '  if [[ -n "${a}" ]]; then',
            '    case ",${out}," in *",0,"*) continue ;; esac',
            "    target",
            "  fi",
            "}",
        ]
        self.assertEqual(['if [[ -n "${a}" ]]; then'], LINT.enclosing_conditions(lines, 3))


if __name__ == "__main__":
    unittest.main()
