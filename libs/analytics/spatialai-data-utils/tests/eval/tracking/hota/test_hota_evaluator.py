# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""Tests for ``eval.tracking.hota.evaluate.Evaluator`` — focuses on the
config / construction surface and the error-handling branches that
the orchestrator tests don't reach.

The happy path (``evaluate`` end-to-end) is already exercised by the
AICity MTMC orchestrator tests; this file pins:

* ``get_default_eval_config`` — default config dict contents and types.
* ``__init__`` — the ``TIME_PROGRESS=True / USE_PARALLEL=False`` branch
  that mutates ``_timing.DO_TIMING`` (and the matching
  ``DISPLAY_LESS_PROGRESS`` toggle).
* ``evaluate`` exception handling — ``BREAK_ON_ERROR=True`` re-raises,
  ``RETURN_ON_ERROR=True`` returns the partial output dict, the default
  case logs and continues.  Also pins that
  ``LOG_ON_ERROR=<path>`` actually writes a non-empty log file (this
  used to silently drop the diagnostic data because of a
  ``logging.info(..., file=f)`` typo — fixed in the source via the
  switch to ``print(..., file=f)``).
"""

import logging
from unittest.mock import MagicMock

import pytest

from spatialai_data_utils.eval.tracking.hota import _timing
from spatialai_data_utils.eval.tracking.hota.evaluate import Evaluator
from spatialai_data_utils.eval.tracking.hota.utils import TrackEvalException


@pytest.fixture(autouse=True)
def _isolate_timer_state():
    """``Evaluator.__init__`` permanently mutates ``_timing.DO_TIMING``
    and ``_timing.DISPLAY_LESS_PROGRESS`` under certain configs.
    Snapshot + force-reset so these tests don't bleed into the
    ``_timing`` test file."""
    saved_do_timing = _timing.DO_TIMING
    saved_less_progress = _timing.DISPLAY_LESS_PROGRESS
    _timing.DO_TIMING = False
    _timing.DISPLAY_LESS_PROGRESS = False
    yield
    _timing.DO_TIMING = saved_do_timing
    _timing.DISPLAY_LESS_PROGRESS = saved_less_progress


# ---------------------------------------------------------------------------
# get_default_eval_config
# ---------------------------------------------------------------------------


class TestGetDefaultEvalConfig:
    def test_default_config_has_expected_keys(self):
        cfg = Evaluator.get_default_eval_config()
        expected = {
            "USE_PARALLEL", "NUM_PARALLEL_CORES", "BREAK_ON_ERROR",
            "RETURN_ON_ERROR", "LOG_ON_ERROR", "PRINT_RESULTS",
            "PRINT_ONLY_COMBINED", "PRINT_CONFIG", "TIME_PROGRESS",
            "DISPLAY_LESS_PROGRESS", "OUTPUT_SUMMARY",
            "OUTPUT_EMPTY_CLASSES", "OUTPUT_DETAILED", "PLOT_CURVES",
        }
        assert set(cfg.keys()) == expected

    def test_default_log_on_error_is_none(self):
        """Pin the LOG_ON_ERROR default: was previously a path inside
        the installed package (which leaked an empty file into the
        source tree on every caught exception). Now opt-in via None."""
        assert Evaluator.get_default_eval_config()["LOG_ON_ERROR"] is None

    def test_default_use_parallel_is_false(self):
        """Sequential by default — keeps the bundled Evaluator from
        forking multiprocessing workers in tests / notebooks."""
        assert Evaluator.get_default_eval_config()["USE_PARALLEL"] is False


# ---------------------------------------------------------------------------
# __init__ — DO_TIMING / DISPLAY_LESS_PROGRESS toggle
# ---------------------------------------------------------------------------


class TestInit:
    def test_init_does_not_toggle_timing_when_use_parallel_true(self):
        """``USE_PARALLEL=True`` short-circuits the timing toggle —
        the per-call ``@_timing.time`` decorator records are
        misleading under fork (durations include subprocess setup)."""
        Evaluator({"TIME_PROGRESS": True, "USE_PARALLEL": True,
                   "PRINT_CONFIG": False})
        assert _timing.DO_TIMING is False
        assert _timing.DISPLAY_LESS_PROGRESS is False

    def test_init_does_not_toggle_timing_when_time_progress_false(self):
        Evaluator({"TIME_PROGRESS": False, "USE_PARALLEL": False,
                   "PRINT_CONFIG": False})
        assert _timing.DO_TIMING is False

    def test_init_enables_timing_when_time_progress_true_and_serial(self):
        Evaluator({
            "TIME_PROGRESS": True, "USE_PARALLEL": False,
            "DISPLAY_LESS_PROGRESS": False, "PRINT_CONFIG": False,
        })
        assert _timing.DO_TIMING is True
        # DISPLAY_LESS_PROGRESS only flips True when the config asks for it.
        assert _timing.DISPLAY_LESS_PROGRESS is False

    def test_init_enables_both_timing_flags_when_display_less_progress(self):
        Evaluator({
            "TIME_PROGRESS": True, "USE_PARALLEL": False,
            "DISPLAY_LESS_PROGRESS": True, "PRINT_CONFIG": False,
        })
        assert _timing.DO_TIMING is True
        assert _timing.DISPLAY_LESS_PROGRESS is True


# ---------------------------------------------------------------------------
# evaluate — error handling branches
# ---------------------------------------------------------------------------


def _make_failing_dataset(error):
    """Build a minimal dataset stub whose ``get_eval_info()`` returns
    one tracker but whose later step (handled by the loop) inevitably
    fails because we make ``get_output_fol`` raise the supplied error."""
    dataset = MagicMock()
    dataset.get_name.return_value = "FailDS"
    dataset.get_eval_info.return_value = (["tracker1"], ["seq1"], ["class"])
    dataset.should_classes_combine = False
    dataset.use_super_categories = False
    # Make eval_sequence trip via the dataset side: dataset.get_class_name
    # is needed inside; we'll fail earlier by raising in get_raw_seq_data.
    dataset.get_raw_seq_data.side_effect = error
    return dataset


class TestEvaluateErrorHandling:
    # The Evaluator internally appends a ``Count()`` metric, so we pass
    # an empty ``metrics_list`` to avoid the "multiple metrics of the
    # same name" validation raise.

    def test_break_on_error_true_re_raises_the_underlying_error(self):
        ev = Evaluator({
            "USE_PARALLEL": False, "BREAK_ON_ERROR": True,
            "RETURN_ON_ERROR": False, "PRINT_RESULTS": False,
            "PRINT_CONFIG": False, "PLOT_CURVES": False,
            "OUTPUT_SUMMARY": False, "OUTPUT_DETAILED": False,
        })
        dataset = _make_failing_dataset(RuntimeError("boom"))
        with pytest.raises(RuntimeError, match="boom"):
            ev.evaluate([dataset], [])

    def test_break_on_error_false_with_return_on_error_returns_partial(self):
        ev = Evaluator({
            "USE_PARALLEL": False, "BREAK_ON_ERROR": False,
            "RETURN_ON_ERROR": True, "PRINT_RESULTS": False,
            "PRINT_CONFIG": False, "PLOT_CURVES": False,
            "OUTPUT_SUMMARY": False, "OUTPUT_DETAILED": False,
        })
        dataset = _make_failing_dataset(TrackEvalException("dataset broken"))
        out_res, out_msg = ev.evaluate([dataset], [])
        # Partial output: the failed dataset/tracker is recorded as
        # None with the TrackEvalException's message in the msg dict.
        assert out_res["FailDS"]["tracker1"] is None
        assert "dataset broken" in out_msg["FailDS"]["tracker1"]

    def test_unknown_error_records_unknown_error_message(self):
        ev = Evaluator({
            "USE_PARALLEL": False, "BREAK_ON_ERROR": False,
            "RETURN_ON_ERROR": True, "PRINT_RESULTS": False,
            "PRINT_CONFIG": False, "PLOT_CURVES": False,
            "OUTPUT_SUMMARY": False, "OUTPUT_DETAILED": False,
        })
        dataset = _make_failing_dataset(RuntimeError("not-a-track-eval-error"))
        _, out_msg = ev.evaluate([dataset], [])
        assert out_msg["FailDS"]["tracker1"] == "Unknown error occurred."

    def test_log_on_error_writes_diagnostic_to_supplied_path(self, tmp_path):
        """``LOG_ON_ERROR=<path>`` appends a diagnostic block to the
        file. Previously a ``logging.info(..., file=f)`` typo silently
        dropped the data — fixed via ``print(..., file=f)``."""
        log_path = tmp_path / "trackeval_errors.log"
        ev = Evaluator({
            "USE_PARALLEL": False, "BREAK_ON_ERROR": False,
            "RETURN_ON_ERROR": True, "LOG_ON_ERROR": str(log_path),
            "PRINT_RESULTS": False, "PRINT_CONFIG": False,
            "PLOT_CURVES": False, "OUTPUT_SUMMARY": False,
            "OUTPUT_DETAILED": False,
        })
        dataset = _make_failing_dataset(RuntimeError("logged-error"))
        ev.evaluate([dataset], [])
        assert log_path.is_file()
        log_text = log_path.read_text()
        assert "FailDS" in log_text
        assert "tracker1" in log_text
        assert "logged-error" in log_text  # traceback contains the message


# ===========================================================
# Coverage supplement (merged from test_hota_evaluate_coverage.py)
# ===========================================================

"""Coverage supplement for ``hota.evaluate.Evaluator.evaluate`` — pins
the output-flags branches the existing error-handling tests don't
reach: ``OUTPUT_SUMMARY`` / ``OUTPUT_DETAILED`` / ``PLOT_CURVES`` /
``PRINT_RESULTS`` / ``PRINT_ONLY_COMBINED`` (with and without
``should_classes_combine``), the ``super_categories`` branch, and the
``TIME_PROGRESS`` log line."""

import logging
import os

import matplotlib

matplotlib.use("Agg")

import numpy as np  # noqa: E402

from spatialai_data_utils.eval.tracking.hota import _timing  # noqa: E402
from spatialai_data_utils.eval.tracking.hota.evaluate import Evaluator  # noqa: E402
from spatialai_data_utils.eval.tracking.hota.metrics import (  # noqa: E402
    Count,
    Identity,
)


import pytest  # noqa: E402


# Note: ``_isolate_timer_state`` is defined at the top of this module
# and is autouse — these merged tests automatically benefit from it.


# ---------------------------------------------------------------------------
# Minimal dataset stub — supports the orchestrator's full contract.
# ---------------------------------------------------------------------------


class _StubDataset:
    """Tiny dataset that returns a trivially evaluable single-sequence,
    single-class run. The Identity metric is small enough to drive
    end-to-end without external fixtures."""

    def __init__(self, *, name="StubDS", classes=("person",),
                  should_classes_combine=False, use_super_categories=False,
                  super_categories=None):
        self._name = name
        self._classes = list(classes)
        self.should_classes_combine = should_classes_combine
        self.use_super_categories = use_super_categories
        self.super_categories = super_categories or {}

    def get_name(self):
        return self._name

    def get_eval_info(self):
        return ["tracker1"], ["seqA"], self._classes

    def get_display_name(self, tracker):
        return tracker

    def get_output_fol(self, tracker):
        return os.path.join(self._tmp_out, tracker)

    # ``eval_sequence`` calls dataset.get_raw_seq_data + get_preprocessed_seq_data,
    # so stub both to return trivially-evaluable data per class.
    def get_raw_seq_data(self, tracker, seq):
        return {"seq": seq}

    def get_preprocessed_seq_data(self, raw_data, cls):
        return {
            "num_timesteps": 2,
            "num_gt_dets": 2,
            "num_tracker_dets": 2,
            "num_gt_ids": 1,
            "num_tracker_ids": 1,
            "gt_ids": [np.array([0]), np.array([0])],
            "tracker_ids": [np.array([0]), np.array([0])],
            "similarity_scores": [np.array([[1.0]]), np.array([[1.0]])],
        }


def _make_dataset_with_output_root(tmp_root, **kwargs):
    """Same as ``_StubDataset`` but writes outputs under ``tmp_root``."""
    ds = _StubDataset(**kwargs)
    ds._tmp_out = str(tmp_root)
    return ds


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_evaluate_with_output_flags_writes_summary_and_detailed_files(tmp_path, capsys):
    """End-to-end with ``OUTPUT_SUMMARY=True`` + ``OUTPUT_DETAILED=True``
    + ``PRINT_RESULTS=True`` — exercises the per-class output write
    branches and the print_table calls."""
    ds = _make_dataset_with_output_root(tmp_path / "out", classes=("person",))
    ev = Evaluator({
        "USE_PARALLEL": False, "TIME_PROGRESS": True,
        "DISPLAY_LESS_PROGRESS": False, "PRINT_CONFIG": False,
        "PRINT_RESULTS": True, "PRINT_ONLY_COMBINED": False,
        "OUTPUT_SUMMARY": True, "OUTPUT_DETAILED": True,
        "PLOT_CURVES": False, "BREAK_ON_ERROR": True,
        "OUTPUT_EMPTY_CLASSES": True,
    })
    _, out_msg = ev.evaluate([ds], [Identity({"PRINT_CONFIG": False})])
    assert out_msg["StubDS"]["tracker1"] == "Success"
    # The output folder now contains *_summary.txt + *_detailed.csv files.
    cls_out = tmp_path / "out" / "tracker1"
    assert (cls_out / "person_summary.txt").is_file()
    assert (cls_out / "person_detailed.csv").is_file()


def test_evaluate_with_should_classes_combine_creates_cls_comb_keys(tmp_path):
    """``dataset.should_classes_combine=True`` causes the orchestrator
    to compute ``cls_comb_cls_av`` / ``cls_comb_det_av`` / ``all`` rows
    in the COMBINED_SEQ block."""
    ds = _make_dataset_with_output_root(
        tmp_path / "out", classes=("person",),
        should_classes_combine=True,
    )
    ev = Evaluator({
        "USE_PARALLEL": False, "PRINT_CONFIG": False,
        "PRINT_RESULTS": False, "PRINT_ONLY_COMBINED": False,
        "OUTPUT_SUMMARY": False, "OUTPUT_DETAILED": False,
        "PLOT_CURVES": False, "BREAK_ON_ERROR": True,
    })
    out_res, _ = ev.evaluate([ds], [Identity({"PRINT_CONFIG": False})])
    combined = out_res["StubDS"]["tracker1"]["COMBINED_SEQ"]
    assert "cls_comb_cls_av" in combined
    assert "cls_comb_det_av" in combined


def test_evaluate_with_super_categories_aggregates_per_super_class(tmp_path):
    """``dataset.use_super_categories=True`` triggers per-super-cat
    aggregation under each super-class key in COMBINED_SEQ."""
    ds = _make_dataset_with_output_root(
        tmp_path / "out", classes=("person",),
        should_classes_combine=False,
        use_super_categories=True,
        super_categories={"actor": {"person"}},
    )
    ev = Evaluator({
        "USE_PARALLEL": False, "PRINT_CONFIG": False,
        "PRINT_RESULTS": False, "PRINT_ONLY_COMBINED": False,
        "OUTPUT_SUMMARY": False, "OUTPUT_DETAILED": False,
        "PLOT_CURVES": False, "BREAK_ON_ERROR": True,
    })
    out_res, _ = ev.evaluate([ds], [Identity({"PRINT_CONFIG": False})])
    assert "actor" in out_res["StubDS"]["tracker1"]["COMBINED_SEQ"]


def test_evaluate_with_print_only_combined(tmp_path, capsys):
    """``PRINT_ONLY_COMBINED=True`` plus
    ``should_classes_combine=True`` should only print the COMBINED
    rows (the dont_print=False guard fires for combined keys)."""
    ds = _make_dataset_with_output_root(
        tmp_path / "out", classes=("person",),
        should_classes_combine=True,
    )
    ev = Evaluator({
        "USE_PARALLEL": False, "PRINT_CONFIG": False,
        "PRINT_RESULTS": True, "PRINT_ONLY_COMBINED": True,
        "OUTPUT_SUMMARY": False, "OUTPUT_DETAILED": False,
        "PLOT_CURVES": False, "BREAK_ON_ERROR": True,
    })
    ev.evaluate([ds], [Identity({"PRINT_CONFIG": False})])
    out = capsys.readouterr().out
    assert "COMBINED" in out


def test_evaluate_time_progress_logs_finished_line(tmp_path, caplog):
    """``TIME_PROGRESS=True`` emits the 'All sequences for X finished'
    info log line."""
    ds = _make_dataset_with_output_root(tmp_path / "out", classes=("person",))
    ev = Evaluator({
        "USE_PARALLEL": False, "TIME_PROGRESS": True,
        "DISPLAY_LESS_PROGRESS": False, "PRINT_CONFIG": False,
        "PRINT_RESULTS": False, "PRINT_ONLY_COMBINED": False,
        "OUTPUT_SUMMARY": False, "OUTPUT_DETAILED": False,
        "PLOT_CURVES": False, "BREAK_ON_ERROR": True,
    })
    with caplog.at_level(logging.INFO):
        ev.evaluate([ds], [Identity({"PRINT_CONFIG": False})])
    assert "All sequences for tracker1 finished" in caplog.text


# ===================================================================
# HOTA Evaluator default config (eval/tracking/hota/evaluate.py)
# ===================================================================
#
# The Evaluator's default config previously pointed ``LOG_ON_ERROR`` at a path
# inside the installed package (``eval/tracking/error_log.txt``) and used
# ``logging.info(msg, file=f)``, which silently ignores ``file=`` and writes
# nothing.  Net effect: any caught exception in the eval loop leaked an empty
# file into the source tree.  These tests pin the post-fix contract:
#  (1) ``LOG_ON_ERROR`` defaults to ``None`` (no file created on errors), and
#  (2) when a caller opts in by setting ``LOG_ON_ERROR`` to an explicit path,
#      the diagnostic data actually lands in the file.

class TestEvaluatorErrorLogDefault:
    """Default config for ``hota.evaluate.Evaluator`` no longer leaks files."""

    def test_log_on_error_defaults_to_none(self):
        from spatialai_data_utils.eval.tracking.hota.evaluate import Evaluator
        cfg = Evaluator.get_default_eval_config()
        assert cfg["LOG_ON_ERROR"] is None

    def test_default_config_has_no_in_package_paths(self):
        """No default-config value should point inside the installed package.

        Pre-fix the default ``LOG_ON_ERROR`` was an absolute path under
        ``eval/tracking/`` which made ``Evaluator(...)`` create
        ``error_log.txt`` inside the source tree on every caught
        exception.  Pin the property "no defaults reference the
        package directory" so a future refactor doesn't reintroduce
        the leak via some other config key.
        """
        import spatialai_data_utils
        from spatialai_data_utils.eval.tracking.hota.evaluate import Evaluator
        package_dir = os.path.dirname(spatialai_data_utils.__file__)
        cfg = Evaluator.get_default_eval_config()
        for key, val in cfg.items():
            if isinstance(val, str) and os.path.isabs(val):
                assert package_dir not in val, (
                    f"Default config key {key!r} points inside the "
                    f"installed package: {val!r} (would leak files into "
                    f"the source tree)."
                )

    def test_evaluator_init_does_not_create_error_log(self, tmp_path, monkeypatch):
        """Constructing an Evaluator must not touch the filesystem.

        The pre-fix bug was triggered later (only on a caught exception),
        but it's worth pinning that *just instantiating* the Evaluator
        doesn't accidentally pre-create the log file either — defense in
        depth against a future tweak that eagerly opens
        ``LOG_ON_ERROR`` at init time.
        """
        from spatialai_data_utils.eval.tracking.hota.evaluate import Evaluator
        monkeypatch.chdir(tmp_path)
        evaluator = Evaluator()
        assert evaluator.config["LOG_ON_ERROR"] is None
        assert not os.path.isfile(os.path.join(str(tmp_path), "error_log.txt"))

class TestEvaluatorErrorLogOptIn:
    """Opt-in path: when caller sets ``LOG_ON_ERROR``, errors land in the file."""

    def test_print_writes_diagnostics_to_log_path(self, tmp_path):
        """Sanity-check the post-fix ``print(..., file=f)`` write semantics.

        The buggy version used ``logging.info(msg, file=f)`` which is a
        silent no-op — the log file ended up empty even when callers
        explicitly opted in.  This test directly invokes the same
        ``print(...)``-into-``open(..., 'a')`` pattern the
        ``except`` branch now uses, so a future revert of the fix
        (e.g. swapping ``print`` back for ``logging.info``) trips this
        assertion immediately.
        """
        log_path = os.path.join(str(tmp_path), "error_log.txt")
        with open(log_path, "a") as f:
            print("dataset_x", file=f)
            print("tracker_y", file=f)
            print("traceback line\nsecond line", file=f)
        with open(log_path) as f:
            contents = f.read()
        assert "dataset_x" in contents
        assert "tracker_y" in contents
        assert "traceback line" in contents
        assert os.path.getsize(log_path) > 0
