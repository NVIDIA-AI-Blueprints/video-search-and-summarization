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

"""Tests for ``eval.tracking.hota._timing.time`` decorator.

The decorator is a no-op when the module-level ``DO_TIMING`` flag is
``False`` (the default and the steady state under tests). When toggled
on, it accumulates per-method elapsed time into ``timer_dict`` and
prints per-call rows to stdout, with formatting that branches on the
first argument name (``"self"`` for methods, ``"test"`` for test
hooks, anything else for top-level functions). Coverage exercises all
five formatting branches plus the summary print emitted when the
``Evaluator.evaluate`` sentinel runs.
"""

import pytest

from spatialai_data_utils.eval.tracking.hota import _timing


@pytest.fixture(autouse=True)
def _isolate_timer_state():
    """``_timing`` keeps mutable module-level state (``DO_TIMING``,
    ``DISPLAY_LESS_PROGRESS``, ``timer_dict``, ``counter``) that the
    real TrackEval Evaluator may flip on permanently
    (``evaluate.py`` does ``_timing.DO_TIMING = True`` /
    ``_timing.DISPLAY_LESS_PROGRESS = True`` and never resets them).

    Snapshot at setup so we can restore at teardown, but also
    **force-reset** the flags to a known-good clean state at the
    start of each test — otherwise a previous-test side effect could
    silently take the ``self``-branch's ``if DISPLAY_LESS_PROGRESS:
    return result`` short-circuit and never record into ``timer_dict``."""
    saved_do_timing = _timing.DO_TIMING
    saved_less_progress = _timing.DISPLAY_LESS_PROGRESS
    saved_timer_dict = _timing.timer_dict.copy()
    saved_counter = _timing.counter
    # Force a clean baseline so previous TrackEval runs don't leak in.
    _timing.DO_TIMING = False
    _timing.DISPLAY_LESS_PROGRESS = False
    _timing.timer_dict.clear()
    _timing.counter = 0
    yield
    _timing.DO_TIMING = saved_do_timing
    _timing.DISPLAY_LESS_PROGRESS = saved_less_progress
    _timing.timer_dict.clear()
    _timing.timer_dict.update(saved_timer_dict)
    _timing.counter = saved_counter


def test_decorator_is_noop_when_do_timing_disabled(capsys):
    """With ``DO_TIMING = False`` the decorator returns the wrapped
    function's value verbatim and prints nothing."""
    _timing.DO_TIMING = False

    @_timing.time
    def add(a, b):
        return a + b

    assert add(2, 3) == 5
    assert capsys.readouterr().out == ""


def test_decorator_records_time_for_top_level_function(capsys):
    """First positional arg is not ``self`` -> top-level branch:
    prints ``N name() ttsec`` and records cumulative time in
    ``timer_dict[function_name]``."""
    _timing.DO_TIMING = True
    _timing.timer_dict.clear()

    @_timing.time
    def my_func(tracker, seq, cls):
        return f"{tracker}-{seq}-{cls}"

    result = my_func("trk", "seqA", "person")
    out = capsys.readouterr().out
    assert result == "trk-seqA-person"
    # Cumulative entry uses bare function name (not Class.method form).
    assert "my_func" in _timing.timer_dict
    # Argument-of-interest printer emits (tracker, seq, cls) values.
    assert "(trk, seqA, person)" in out


def test_decorator_records_time_for_instance_method(capsys):
    """First positional arg is ``self`` -> method branch:
    ``timer_dict`` key is ``ClassName.method_name`` and indented row
    is printed."""
    _timing.DO_TIMING = True
    _timing.timer_dict.clear()

    class Doer:
        @_timing.time
        def do_thing(self, tracker):
            return tracker

    out = Doer().do_thing("trk-1")
    assert out == "trk-1"
    assert "Doer.do_thing" in _timing.timer_dict
    captured = capsys.readouterr().out
    # Method rows are indented with 4 leading spaces.
    assert captured.startswith(" " * 4) or "    Doer.do_thing" in captured


def test_decorator_skips_timing_for_self_method_when_display_less_progress(capsys):
    """``DISPLAY_LESS_PROGRESS = True`` short-circuits the method
    branch — no entry recorded, no row printed."""
    _timing.DO_TIMING = True
    _timing.DISPLAY_LESS_PROGRESS = True
    _timing.timer_dict.clear()

    class Doer:
        @_timing.time
        def do_thing(self):
            return "done"

    assert Doer().do_thing() == "done"
    # No timer entry, no stdout.
    assert "Doer.do_thing" not in _timing.timer_dict
    assert capsys.readouterr().out == ""


def test_decorator_skips_print_for_test_argument_branch(capsys):
    """If the wrapped function's first arg is named ``test`` (special
    pytest-discovery sentinel) the decorator records timing but does
    not print a row."""
    _timing.DO_TIMING = True
    _timing.timer_dict.clear()

    @_timing.time
    def my_helper(test):
        return test * 2

    assert my_helper(3) == 6
    # Time is still recorded (function name was added to the dict)...
    assert "my_helper" in _timing.timer_dict
    # ...but no row was printed.
    assert capsys.readouterr().out == ""


def test_evaluator_evaluate_method_triggers_summary_print(capsys):
    """The sentinel ``Evaluator.evaluate`` triggers the timing-summary
    block instead of a per-call row."""
    _timing.DO_TIMING = True
    _timing.timer_dict.clear()
    _timing.timer_dict["pre_existing"] = 0.123  # ensure something to summarise

    class Evaluator:
        @_timing.time
        def evaluate(self):
            return "ok"

    Evaluator().evaluate()
    out = capsys.readouterr().out
    assert "Timing analysis:" in out
    assert "pre_existing" in out


def test_decorator_accumulates_time_across_multiple_calls():
    """Repeated invocations sum into the same ``timer_dict`` slot."""
    _timing.DO_TIMING = True
    _timing.timer_dict.clear()

    @_timing.time
    def f(x):
        return x

    f(1)
    f(2)
    f(3)
    # We don't pin the exact value (clock-dependent) but it must be
    # positive and the slot must exist.
    assert "f" in _timing.timer_dict
    assert _timing.timer_dict["f"] > 0
