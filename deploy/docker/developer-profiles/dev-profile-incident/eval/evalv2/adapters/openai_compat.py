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

"""OpenAI-compatible chat-completions adapter (Switchyard, the NVIDIA API catalog, a vLLM or NIM endpoint).

Each endpoint names the environment variables that hold its base URL (with ``/v1``) and its key; the key is
read at call time, sent only as a bearer header, and never stored: the request artifact is the JSON body alone.
Failures are classified exactly as V1 classifies them (``core.retry.classify_http_status``), and a 2xx that is
not a completion is ``invalid_response``, as in V1 and the console. Any failed attempt that spanned a host suspend
is ``host_suspended``, whatever it failed with, as in V1's ``rate_limited()``.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import httpx

from evalv2.adapters.base import AdapterConfigError, CallContext, CallResult, Capabilities, stored_request
from evalv2.core.budget import BudgetExceeded
from evalv2.core.instrument import Stopwatch, Usage
from evalv2.core.retry import Failure, classify_http_status, run_with_retry


@dataclass(frozen=True)
class Endpoint:
    name: str
    base_url_env: str
    api_key_env: str
    default_base_url: str | None = None
    timeout_s: float = 300.0  # contracts/inference.json timeout_seconds for the allowlisted VLMs


ENDPOINTS: dict[str, Endpoint] = {
    # The same variables V1's eval_vlm_client reads; the base URL includes /v1.
    "switchyard": Endpoint("switchyard", "VLM_GATEWAY_BASE_URL", "VLM_GATEWAY_API_KEY"),
    "nvidia-api-catalog": Endpoint("nvidia-api-catalog", "NVIDIA_API_BASE_URL", "NVIDIA_API_KEY",
                                   default_base_url="https://integrate.api.nvidia.com/v1"),
    "local-openai": Endpoint("local-openai", "EVALV2_LOCAL_BASE_URL", "EVALV2_LOCAL_API_KEY"),
}


def _retry_after(response: httpx.Response) -> float | None:
    try:
        return float(response.headers["Retry-After"])
    except (KeyError, ValueError):
        return None


class OpenAICompatAdapter:
    def __init__(self, id: str, model: str, endpoint: Endpoint, capabilities: Capabilities, *,
                 transport: httpx.BaseTransport | None = None, environ: Mapping[str, str] | None = None) -> None:
        self.id, self.model, self.endpoint, self.capabilities = id, model, endpoint, capabilities
        self._transport = transport
        self._environ = environ if environ is not None else os.environ

    def _credentials(self) -> tuple[str, str]:
        base_url = self._environ.get(self.endpoint.base_url_env) or self.endpoint.default_base_url
        api_key = self._environ.get(self.endpoint.api_key_env, "")
        if not base_url:
            raise AdapterConfigError(f"endpoint {self.endpoint.name}: set {self.endpoint.base_url_env}")
        if not api_key:
            raise AdapterConfigError(f"endpoint {self.endpoint.name}: set {self.endpoint.api_key_env}")
        return base_url.rstrip("/"), api_key

    def complete(self, request: Mapping[str, Any], ctx: CallContext) -> CallResult:
        if request.get("model") != self.model:
            raise ValueError(f"request names model {request.get('model')!r}, adapter serves {self.model!r}")
        base_url, api_key = self._credentials()  # before anything is charged or sent
        request_ref = ctx.store.put("request", stored_request(request))
        attempts: list[dict[str, Any]] = []
        last: dict[str, Any] = {}

        def attempt(number: int) -> tuple[Any, Failure | None]:
            try:
                reservation = ctx.ledger.reserve(ctx.budget_ledger, subject=ctx.subject, task=ctx.task,
                                                 tokens_bound=ctx.tokens_bound)
            except BudgetExceeded as exc:
                attempts.append({"attempt": number, "failure": "budget_exceeded", "detail": str(exc)})
                return None, Failure("budget_exceeded", str(exc))
            watch = Stopwatch(waited_ms=(attempts[-1].get("next_wait_ms", 0.0) if attempts else 0.0))
            failure: Failure | None = None
            body: Any = None
            status: int | None = None
            with watch, httpx.Client(transport=self._transport, timeout=self.endpoint.timeout_s) as client:
                try:
                    response = client.post(f"{base_url}/chat/completions", json=dict(request),
                                           headers={"Authorization": f"Bearer {api_key}"})
                    status = response.status_code
                    if status >= 400:
                        failure = Failure(classify_http_status(status), response.text[:500], status,
                                          _retry_after(response))
                    else:
                        try:
                            body = response.json()
                        except ValueError:
                            failure = Failure("invalid_response", "response body is not JSON", status)
                except httpx.TimeoutException as exc:
                    failure = Failure("timeout", str(exc))
                except httpx.TransportError as exc:
                    failure = Failure("transport_error", str(exc))
            if failure is None and not (isinstance(body, dict) and body.get("choices")):
                failure = Failure("invalid_response", f"no completion in the response: {str(body)[:200]}", status)
            if watch.timing and watch.timing.host_suspended and failure is not None:
                # V1 relabels every failed attempt that spanned a suspend, an HTTP error or a non-completion too.
                failure = Failure("host_suspended", failure.detail, failure.http_status)
            usage = Usage.from_openai(body.get("usage") if isinstance(body, dict) else None)
            ctx.ledger.settle(reservation, tokens_used=usage.total_tokens)
            response_ref = ctx.store.put("response", body) if isinstance(body, dict) else None
            entry = {"attempt": number, "status": status, "failure": failure.kind if failure else None,
                     "timing": watch.timing.to_dict() if watch.timing else None,
                     "response_ref": str(response_ref) if response_ref else None}
            attempts.append(entry)
            last.update(body=body, usage=usage, timing=watch.timing, response_ref=response_ref)
            return body, failure

        def sleep(seconds: float) -> None:
            if attempts:
                attempts[-1]["next_wait_ms"] = round(seconds * 1000.0, 3)
            ctx.sleep(seconds)

        log = run_with_retry(attempt, ctx.policy, sleep=sleep)
        failure = log.final.failure
        body = last.get("body")
        choice = body["choices"][0] if failure is None else {}
        message = choice.get("message") or {}
        return CallResult(request_ref=request_ref, response_ref=last.get("response_ref"),
                          text=message.get("content") if failure is None else None,
                          finish_reason=choice.get("finish_reason"), usage=last.get("usage") or Usage(),
                          timing=last.get("timing"), failure=failure, attempts=tuple(attempts))
