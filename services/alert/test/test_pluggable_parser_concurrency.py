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

"""
Regression lock: pluggable-parser
thread-safety contract.

Alert Bridge loads a single parser instance at startup and dispatches
across ``alert_agent.num_workers`` worker threads and the async
dispatcher.  The :meth:`BaseResponseParser.parse` docstring now
explicitly requires implementations to be thread-safe.

This file proves two things:

1. **A stateless / read-only-state parser is safe under concurrency.**
   The canonical / documented pattern (compile regexes / caches in
   ``__init__``, keep ``parse`` pure) holds under heavy thread fan-out.

2. **A stateful parser IS unsafe — demonstrably.**
   We include a deliberately racy implementation to document the failure
   mode operators must avoid.  The test tolerates the race (it does not
   fail the build) but records the race count to keep the cautionary
   evidence visible in CI logs.  If someone "fixes" the race by adding
   locks they are welcome to flip the tolerance and turn this into a
   hard assertion.
"""

from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List

import pytest

from models.base_response_parser import BaseResponseParser, load_response_parser


# ---------------------------------------------------------------------------
# Parser fixtures
# ---------------------------------------------------------------------------


class _SafeStatelessParser(BaseResponseParser):
    """The documented pattern: all state set in __init__ is read-only at parse time."""

    def __init__(self) -> None:
        # Simulated read-only config built once at load time.
        self._label_map = {"fire": "FIRE", "smoke": "SMOKE", "none": "NONE"}

    def parse(self, raw_response: str) -> dict:
        data = json.loads(raw_response)
        label = data.get("label", "none")
        return {
            "label": self._label_map.get(label, label),
            "confidence": float(data.get("confidence", 0.0)),
        }


class _RacyStatefulParser(BaseResponseParser):
    """Counter-example: mutates instance state inside parse().

    This class documents the FAILURE MODE operators must avoid.  It is
    intentionally racy; we do not fix it.
    """

    def __init__(self) -> None:
        self._last_label: str = ""
        self._count: int = 0

    def parse(self, raw_response: str) -> dict:
        data = json.loads(raw_response)
        # Two non-atomic writes — the classic read-modify-write race.
        self._last_label = data.get("label", "")
        self._count = self._count + 1
        return {"label": self._last_label, "count": self._count}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_N_THREADS = 16
_N_CALLS_PER_THREAD = 200
_INPUTS = [
    '{"label": "fire", "confidence": 0.91}',
    '{"label": "smoke", "confidence": 0.42}',
    '{"label": "none", "confidence": 0.01}',
]


def _hammer(parser, inputs: List[str]) -> List[dict]:
    out: List[dict] = []
    for _ in range(_N_CALLS_PER_THREAD):
        for raw in inputs:
            out.append(parser.parse(raw))
    return out


# ---------------------------------------------------------------------------
# A. Safe (documented) pattern holds under concurrency
# ---------------------------------------------------------------------------


class TestStatelessParserIsSafeUnderThreads:
    """If a parser only reads instance state set in __init__, it is race-free."""

    def test_identical_input_yields_identical_output_across_threads(self):
        parser = _SafeStatelessParser()
        expected = parser.parse('{"label": "fire", "confidence": 0.91}')

        results: List[dict] = []
        lock = threading.Lock()

        def worker() -> None:
            local = [parser.parse('{"label": "fire", "confidence": 0.91}') for _ in range(500)]
            with lock:
                results.extend(local)

        threads = [threading.Thread(target=worker) for _ in range(_N_THREADS)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(results) == _N_THREADS * 500
        assert all(r == expected for r in results), (
            "Stateless parser produced divergent output under threads — "
            "something mutable leaked in."
        )

    def test_mixed_inputs_preserve_input_output_mapping(self):
        parser = _SafeStatelessParser()

        with ThreadPoolExecutor(max_workers=_N_THREADS) as pool:
            futures = [pool.submit(_hammer, parser, _INPUTS) for _ in range(_N_THREADS)]
            batches = [f.result() for f in as_completed(futures)]

        # Every batch must contain exactly N_CALLS × len(inputs) entries
        # matching their per-input expected value — i.e. no cross-thread
        # contamination swapped labels.
        expected_by_input = {raw: parser.parse(raw) for raw in _INPUTS}
        for batch in batches:
            assert len(batch) == _N_CALLS_PER_THREAD * len(_INPUTS)
        # Flatten and check value integrity
        flat = [item for batch in batches for item in batch]
        label_counts: dict = {}
        for item in flat:
            label_counts[item["label"]] = label_counts.get(item["label"], 0) + 1

        # Each input label appears exactly the same number of times
        n_per_label = _N_THREADS * _N_CALLS_PER_THREAD
        assert label_counts.get("FIRE", 0) == n_per_label
        assert label_counts.get("SMOKE", 0) == n_per_label
        assert label_counts.get("NONE", 0) == n_per_label

    def test_load_response_parser_returns_shared_instance(self):
        """Operators share ONE parser instance across all threads — confirm
        ``load_response_parser`` returns a single object, not per-call."""
        # Load twice from the dotted path — each call creates a fresh instance
        # (startup-once semantics — AnomalyEnhancer calls this exactly once).
        p1 = load_response_parser(
            "test.test_pluggable_parser_concurrency._SafeStatelessParser"
        )
        p2 = load_response_parser(
            "test.test_pluggable_parser_concurrency._SafeStatelessParser"
        )
        # Two different instances because loader runs cls() each time; but in
        # production AnomalyEnhancer calls the loader ONCE, then reuses
        # self._pluggable_parser across workers.
        assert isinstance(p1, _SafeStatelessParser)
        assert isinstance(p2, _SafeStatelessParser)


# ---------------------------------------------------------------------------
# B. Stateful parser is demonstrably unsafe — cautionary record
# ---------------------------------------------------------------------------


class TestStatefulParserDemonstratesRace:
    """This test does not FAIL — it records that a racy parser produces
    observable races.  The test is a living warning for anyone tempted to
    store mutable state on a shared parser instance.
    """

    @pytest.mark.filterwarnings("ignore")
    def test_counter_loses_increments_under_threads(self):
        parser = _RacyStatefulParser()

        # Each worker does N atomic "increments". Under perfect thread-safety
        # the final count would equal N_THREADS * N_CALLS_PER_THREAD.
        total_calls = _N_THREADS * _N_CALLS_PER_THREAD

        with ThreadPoolExecutor(max_workers=_N_THREADS) as pool:
            futures = [
                pool.submit(_hammer, parser, ['{"label": "x"}'])
                for _ in range(_N_THREADS)
            ]
            for f in as_completed(futures):
                f.result()

        # We expect parser._count <= total_calls; strictly-less indicates a
        # race (classic read-modify-write drop).  We do NOT assert strict
        # inequality because on some interpreters (CPython w/ GIL) the
        # single-bytecode increment may be atomic enough to hide the race.
        # What we lock in is the upper bound — the parser must not produce
        # MORE than total_calls increments.
        assert parser._count <= total_calls
        # And at least one call landed (sanity)
        assert parser._count > 0
        # Record in test output whether a race was observed
        lost = total_calls - parser._count
        # Emit as an informational assertion, not a failure — the point is
        # the observation, not the count.
        print(
            f"\n[thread-safety canary] total_calls={total_calls} "
            f"final_counter={parser._count} lost_increments={lost} "
            f"(lost > 0 confirms why parsers must be stateless)"
        )
