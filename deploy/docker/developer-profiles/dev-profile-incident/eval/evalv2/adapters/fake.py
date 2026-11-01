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

"""A scripted adapter for hermetic tests and plumbing runs: no network, same accounting as a real adapter.

Each scripted step is either a completion (text, finish_reason, usage) or a ``Failure``. It charges the budget,
applies the retry policy and stores artifacts exactly like ``OpenAICompatAdapter``, so a plumbing run exercises
the whole pipeline without a model call. Results from it are never reported as results.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from evalv2.adapters.base import CallContext, CallResult, Capabilities
from evalv2.core.budget import BudgetExceeded
from evalv2.core.instrument import Stopwatch, Usage
from evalv2.core.retry import Failure, run_with_retry


@dataclass
class Scripted:
    text: str | None = None
    finish_reason: str = "stop"
    usage: Mapping[str, Any] | None = None
    failure: Failure | None = None


@dataclass
class FakeAdapter:
    id: str
    model: str
    script: Sequence[Scripted]
    capabilities: Capabilities = field(default_factory=lambda: Capabilities(video_input=True, json_schema=True))
    calls: int = 0

    def complete(self, request: Mapping[str, Any], ctx: CallContext) -> CallResult:
        if request.get("model") != self.model:
            raise ValueError(f"request names model {request.get('model')!r}, adapter serves {self.model!r}")
        request_ref = ctx.store.put("request", dict(request))
        attempts: list[dict[str, Any]] = []
        last: dict[str, Any] = {}

        def attempt(number: int) -> tuple[Any, Failure | None]:
            try:
                reservation = ctx.ledger.reserve(ctx.budget_ledger, subject=ctx.subject, task=ctx.task,
                                                 tokens_bound=ctx.tokens_bound)
            except BudgetExceeded as exc:
                attempts.append({"attempt": number, "failure": "budget_exceeded"})
                return None, Failure("budget_exceeded", str(exc))
            step = self.script[min(self.calls, len(self.script) - 1)]
            self.calls += 1
            with Stopwatch() as watch:
                body = None if step.failure else {
                    "choices": [{"message": {"role": "assistant", "content": step.text},
                                 "finish_reason": step.finish_reason}],
                    "usage": dict(step.usage or {})}
            usage = Usage.from_openai(body["usage"] if body else None)
            ctx.ledger.settle(reservation, tokens_used=usage.total_tokens)
            response_ref = ctx.store.put("response", body) if body else None
            attempts.append({"attempt": number, "failure": step.failure.kind if step.failure else None,
                             "response_ref": str(response_ref) if response_ref else None})
            last.update(body=body, usage=usage, timing=watch.timing, response_ref=response_ref)
            return body, step.failure

        log = run_with_retry(attempt, ctx.policy, sleep=ctx.sleep)
        failure = log.final.failure
        body = last.get("body") or {}
        choice = (body.get("choices") or [{}])[0]
        return CallResult(request_ref=request_ref, response_ref=last.get("response_ref"),
                          text=(choice.get("message") or {}).get("content") if failure is None else None,
                          finish_reason=choice.get("finish_reason"), usage=last.get("usage") or Usage(),
                          timing=last.get("timing"), failure=failure, attempts=tuple(attempts))
