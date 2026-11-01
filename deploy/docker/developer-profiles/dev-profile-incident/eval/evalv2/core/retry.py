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

"""Failure taxonomy and retry policies, with V1's semantics.

The policies mirror V1's ``eval_vlm_client.P1_RETRY_POLICIES`` exactly; ``tests/v2/test_v2_retry.py`` asserts
it, so V2 never drifts from the rules V1's results were produced under. A model output, valid or not, is never
retried: only infrastructure failures are.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

FAILURE_KINDS = (
    "rate_limited",      # HTTP 429
    "timeout",           # client timeout, or the gateway's 504/522/524
    "transport_error",   # connection reset, DNS failure, refused
    "host_suspended",    # the attempt spanned a host suspend (wall clock ran ahead of the monotonic clock)
    "http_error",        # any other HTTP error status
    "invalid_output",    # the model answered, but the output failed validation (a model miss, never retried)
    "unsupported",       # the subject cannot do the task (reported as unsupported, never scored)
    "budget_exceeded",   # a cap would have been passed; the attempt was not sent
    "not_sent",          # the run stopped before the attempt
)
INFRASTRUCTURE_FAILURES = frozenset({"rate_limited", "timeout", "transport_error", "host_suspended"})


@dataclass(frozen=True)
class Failure:
    kind: str
    detail: str = ""
    http_status: int | None = None
    retry_after_s: float | None = None

    def __post_init__(self) -> None:
        if self.kind not in FAILURE_KINDS:
            raise ValueError(f"unknown failure kind {self.kind!r}")

    @property
    def infrastructure(self) -> bool:
        return self.kind in INFRASTRUCTURE_FAILURES

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "detail": self.detail, "http_status": self.http_status,
                "retry_after_s": self.retry_after_s}


def classify_http_status(status: int) -> str:
    """V1's mapping: 429 is rate limiting, 504/522/524 are timeouts (the gateway gave up), the rest HTTP errors."""
    if status == 429:
        return "rate_limited"
    if status in (504, 522, 524):
        return "timeout"
    return "http_error"


@dataclass(frozen=True)
class RetryPolicy:
    id: str
    retry_on: tuple[str, ...]
    backoff_s: tuple[float, ...]
    inter_call_delay_s: float = 20.0
    cooldown_s: float = 60.0
    honor_retry_after: bool = True
    max_rate_limit_retries: int = 2

    @property
    def max_retries(self) -> int:
        return self.max_rate_limit_retries if self.retry_on else 0

    def should_retry(self, failure: Failure | None, retries_done: int) -> bool:
        return failure is not None and failure.kind in self.retry_on and retries_done < self.max_retries

    def wait_s(self, failure: Failure, retries_done: int) -> float:
        """V1's rule: ``backoff_s[i]`` before retry ``i`` (the last value repeats), at least Retry-After on a 429."""
        wait = self.backoff_s[min(retries_done, len(self.backoff_s) - 1)]
        if failure.kind == "rate_limited" and self.honor_retry_after and failure.retry_after_s is not None:
            wait = max(wait, failure.retry_after_s)
        return wait


POLICIES: dict[str, RetryPolicy] = {
    "retry-v1": RetryPolicy("retry-v1", retry_on=("rate_limited",), backoff_s=(60.0, 60.0)),
    "retry-v2": RetryPolicy("retry-v2", retry_on=("rate_limited", "timeout", "transport_error", "host_suspended"),
                            backoff_s=(60.0, 180.0)),
    "none": RetryPolicy("none", retry_on=(), backoff_s=(0.0,), max_rate_limit_retries=0),
}


@dataclass
class Attempt:
    number: int
    failure: Failure | None
    result: Any = None
    waited_s: float = 0.0


@dataclass
class AttemptLog:
    attempts: list[Attempt] = field(default_factory=list)

    @property
    def final(self) -> Attempt:
        return self.attempts[-1]

    @property
    def succeeded(self) -> bool:
        return bool(self.attempts) and self.final.failure is None


def run_with_retry(call: Callable[[int], tuple[Any, Failure | None]], policy: RetryPolicy, *,
                   sleep: Callable[[float], None] = time.sleep) -> AttemptLog:
    """Call ``call(attempt_number)`` until it succeeds, fails finally, or the policy runs out of retries.

    ``call`` returns ``(result, failure)``; a ``None`` failure is success. Waiting between attempts is recorded
    on the attempt that follows it and is never part of any attempt's processing time.
    """
    log = AttemptLog()
    waited = 0.0
    while True:
        result, failure = call(len(log.attempts) + 1)
        log.attempts.append(Attempt(len(log.attempts) + 1, failure, result, waited))
        retries_done = len(log.attempts) - 1
        if not policy.should_retry(failure, retries_done):
            return log
        waited = policy.wait_s(failure, retries_done)  # type: ignore[arg-type]
        sleep(waited)
