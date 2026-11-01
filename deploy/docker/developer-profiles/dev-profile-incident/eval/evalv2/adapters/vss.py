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

"""Track B adapter: the production pipeline on a local experiment agent, one ``/analyze`` call per run.

The agent runs P1, grounding, the zoomed second look and (if the profile asks) object tracking itself, so its calls
are not ours to send one by one. For each request built by :mod:`evalv2.requests.vss` the adapter:

- holds every ledger's worst case in the budget log *before* sending, and sends nothing when a cap could be passed.
  The hold covers the call while it is in flight (up to 15 minutes), so a run killed mid-call resumes from counts
  that include it, and two requests in flight can never both pass a cap that covers one;
- stores the request (the signed URL without its signature) and the reply as artifacts;
- releases the hold to what the agent actually sent, stage by stage, from the reply's run provenance (P3). An
  attempt that reported no usage is charged at the profile's token bound; a failure that carries no provenance (a
  timeout, a 5xx, a dropped connection) keeps the worst case, so the ledger never undercounts. A refused connection
  or a refused request sent nothing and costs nothing;
- pins the agent: its commit, dirty flag, analysis flags and contract version must stay the same for every run and
  across a resume (pass the manifest's ``pinned`` back in). The profile's expected flags must hold, and no stage
  outside the profile may have sent a call; either problem stops the run after the calls are charged;
- checks the object tracker's detector cap the reply reports (``object_tracking.detector.cap``, the agent's
  ``INCIDENT_OBJECT_TRACKING_MAX_DETECTOR_CALLS``): a cap above the profile's worst case, an unreported cap after
  tracker calls, or a cap that changes during the run stops the run after the calls are charged, never silently;
- stops the run on a refusal (400, 403, 409, 501): the request or the agent is misconfigured, and resending cannot
  help.

It runs only against a loopback agent (``EVALV2_VSS_AGENT_URL``, default the experiment agent's
``http://127.0.0.1:8765``), never the shared VM agent. Executing is a live, approval-gated run; J1 is tested with
a fake agent only.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import httpx

from evalv2.adapters.base import AdapterConfigError, CallContext, Capabilities, PipelineResult, redact_url
from evalv2.core.budget import Hold

AGENT_URL_ENV = "EVALV2_VSS_AGENT_URL"
DEFAULT_AGENT_URL = "http://127.0.0.1:8765"  # experiments/agent/start-experiment-agent.sh
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
REFUSAL_STATUSES = frozenset({400, 403, 409, 501})


class AgentDriftError(RuntimeError):
    """A reply from an agent whose commit, flags or contract differ from the run's pinned agent."""


class ProfileMismatchError(RuntimeError):
    """The agent ran with flags, or called stages, that the profile does not declare."""


class AgentRefusedError(RuntimeError):
    """The agent refused the request as configured (400, 403, 409 or 501)."""


@dataclass
class VssAgentAdapter:
    id: str
    model: str
    base_url: str = DEFAULT_AGENT_URL
    capabilities: Capabilities = field(default_factory=Capabilities)
    timeout_seconds: float = 900.0
    post: Callable[..., Any] = httpx.post
    pinned: dict[str, Any] | None = None
    clock: Callable[[], float] = time.monotonic

    @staticmethod
    def _worst_case(accounting: Mapping[str, Any]) -> dict[str, dict[str, int]]:
        return {ledger: {"attempts": int(attempts), "tokens": int(tokens)}
                for ledger, (attempts, tokens) in accounting["worst_case"].items()}

    @staticmethod
    def _release(ctx: CallContext, hold: Hold, charged: Mapping[str, Mapping[str, int]], source: str) -> None:
        ctx.ledger.release(hold, {ledger: (row["attempts"], row["tokens"]) for ledger, row in charged.items()},
                           source=source)

    @staticmethod
    def _measured(provenance: Mapping[str, Any], accounting: Mapping[str, Any]
                  ) -> tuple[dict[str, dict[str, int]], list[str]]:
        """What the agent reported sending, per ledger, and the stages outside the profile that sent calls."""
        charged: dict[str, dict[str, int]] = {}
        undeclared = []
        for stage, meter in sorted((provenance.get("stages") or {}).items()):
            attempts = int(meter.get("calls") or 0)
            if not attempts:
                continue
            ledger = accounting["stage_ledgers"].get(stage)
            if ledger is None or stage not in accounting["stages"]:
                undeclared.append(stage)
            ledger = ledger or "inference"
            tokens = (int(meter.get("total_tokens") or 0)
                      + int(meter.get("calls_without_usage") or 0) * int(accounting["token_bounds"][ledger]))
            row = charged.setdefault(ledger, {"attempts": 0, "tokens": 0})
            row["attempts"] += attempts
            row["tokens"] += tokens
        return charged, undeclared

    def _check_object_tracking_cap(self, payload: Mapping[str, Any], provenance: Mapping[str, Any],
                                   accounting: Mapping[str, Any]) -> None:
        """The tracker's detector cap must be reported, at most the profile's worst case, and the same every run."""
        if "object_tracking" not in accounting["worst_case"]:
            return
        assumed = int(accounting["worst_case"]["object_tracking"][0])
        summary = payload.get("object_tracking") if isinstance(payload.get("object_tracking"), Mapping) else {}
        detector = summary.get("detector") if isinstance(summary.get("detector"), Mapping) else {}
        calls = int(((provenance.get("stages") or {}).get("object_tracking") or {}).get("calls") or 0)
        cap = detector.get("cap")
        if cap is None:
            if calls:
                raise ProfileMismatchError(f"the agent sent {calls} object-tracking detector calls but reported no "
                                           "detector cap (object_tracking.detector.cap), so the worst case is unknown")
            return
        if int(cap) > assumed:
            raise ProfileMismatchError(f"the agent's object-tracking detector cap is {cap}, above the profile's worst "
                                       f"case of {assumed}: start the agent with "
                                       f"INCIDENT_OBJECT_TRACKING_MAX_DETECTOR_CALLS <= {assumed}")
        pinned = (self.pinned or {}).get("object_tracking_cap")
        if pinned is None:
            if self.pinned is not None:
                self.pinned["object_tracking_cap"] = int(cap)
        elif int(cap) != pinned:
            raise AgentDriftError(f"the agent's object-tracking detector cap changed during the run: pinned {pinned}, "
                                  f"now {cap}")

    def _pin(self, provenance: Mapping[str, Any], accounting: Mapping[str, Any]) -> None:
        identity = {"agent": dict(provenance.get("agent") or {}), "flags": dict(provenance.get("flags") or {}),
                    "contract_version": provenance.get("contract_version")}
        wrong = {name: identity["flags"].get(name) for name, value in accounting["expected_flags"].items()
                 if identity["flags"].get(name) != value}
        if wrong:
            raise ProfileMismatchError(f"profile {accounting['profile']['id']} expects "
                                       f"{accounting['expected_flags']}; the agent runs {wrong}")
        if self.pinned is None:
            self.pinned = identity
        elif {key: self.pinned.get(key) for key in identity} != identity:
            raise AgentDriftError(f"the agent changed during the run: pinned {self.pinned}, now {identity}")

    def analyze(self, incident_id: str, request: Mapping[str, Any], ctx: CallContext) -> PipelineResult:
        body, accounting = dict(request["body"]), request["accounting"]
        if body.get("model") != self.model:
            raise ValueError(f"subject {self.id} runs {self.model!r}, but the request names {body.get('model')!r}")
        request_ref = str(ctx.store.put("vss-request", {
            "incident_id": incident_id, "profile": accounting["profile"],
            "body": {**body, "video_url": redact_url(str(body.get("video_url", "")))}}))
        # Charged before sending and kept until the reply says otherwise: a run killed mid-call still counts it.
        hold = ctx.ledger.hold(accounting["worst_case"], subject=ctx.subject, task=ctx.task,
                               source=f"in flight: /analyze {incident_id} ({request_ref})")
        url = f"{self.base_url.rstrip('/')}/api/v1/incidents/{incident_id}/analyze"
        started = self.clock()
        try:
            response = self.post(url, json=body, timeout=self.timeout_seconds)
        except httpx.ConnectError as exc:  # the agent never received the request: nothing was sent upstream
            self._release(ctx, hold, {}, "refused connection: nothing sent")
            return PipelineResult(0, "request_failed", {}, request_ref, None, None, (self.clock() - started) * 1000,
                                  error=f"{type(exc).__name__}: {exc}")
        except httpx.HTTPError as exc:  # the agent may have sent calls before the reply was lost
            charged = self._worst_case(accounting)
            self._release(ctx, hold, charged, f"no reply ({type(exc).__name__}): worst case")
            return PipelineResult(0, "request_failed", {}, request_ref, None, None, (self.clock() - started) * 1000,
                                  charged, error=f"{type(exc).__name__}: {exc}")
        elapsed_ms = (self.clock() - started) * 1000
        try:
            payload = response.json()
        except ValueError:
            payload = {"error": response.text[:500]}
        if not isinstance(payload, dict):
            payload = {"error": str(payload)[:500]}
        response_ref = str(ctx.store.put("vss-response", {"status": response.status_code, "body": payload}))
        status = response.status_code
        if status in REFUSAL_STATUSES:
            self._release(ctx, hold, {}, f"agent refused the request ({status}): nothing sent")
            raise AgentRefusedError(f"the agent refused the request ({status}): {payload.get('detail', payload)}")
        detail = payload.get("detail") if status == 422 else None
        provenance = payload.get("provenance") if status == 200 else (
            detail.get("provenance") if isinstance(detail, dict) else None)
        if provenance:
            charged, undeclared = self._measured(provenance, accounting)
            self._release(ctx, hold, charged, f"agent provenance ({status})")
            if undeclared:  # charged first, so the ledger is right although the run stops here
                raise ProfileMismatchError(f"profile {accounting['profile']['id']} declares {accounting['stages']}, "
                                           f"but the agent called {undeclared}")
            self._pin(provenance, accounting)
            self._check_object_tracking_cap(payload, provenance, accounting)
        elif status in (200, 422):
            self._release(ctx, hold, self._worst_case(accounting), f"agent {status} without provenance: worst case")
            raise ProfileMismatchError(f"the agent answered {status} without run provenance (it predates P3)")
        else:
            charged = self._worst_case(accounting)
            self._release(ctx, hold, charged, f"agent {status}: worst case")
        outcome = "answered" if status == 200 else "contract_rejected" if status == 422 else "request_failed"
        error = None if outcome != "request_failed" else str(payload.get("detail") or payload.get("error"))[:500]
        return PipelineResult(status, outcome, payload, request_ref, response_ref, provenance, elapsed_ms, charged,
                              error)


def build_vss_adapter(subject: Any, *, environ: Mapping[str, str] | None = None,
                      capabilities: Capabilities | None = None, **kwargs: Any) -> VssAgentAdapter:
    """A Track B adapter from a run-config subject (``adapter: vss``, endpoint ``experiment-agent`` or none)."""
    environ = os.environ if environ is None else environ
    if subject.endpoint not in (None, "experiment-agent"):
        raise AdapterConfigError(f"subject {subject.id}: Track B runs only on the local experiment agent "
                                 f"(endpoint 'experiment-agent'), not {subject.endpoint!r}")
    base_url = environ.get(AGENT_URL_ENV) or DEFAULT_AGENT_URL
    if urlsplit(base_url).hostname not in LOOPBACK_HOSTS:
        raise AdapterConfigError(f"{AGENT_URL_ENV}={base_url!r}: Track B runs only on a loopback experiment agent, "
                                 "never on the shared VM agent")
    return VssAgentAdapter(subject.id, subject.model, base_url, capabilities or Capabilities(), **kwargs)


__all__ = ["AgentDriftError", "AgentRefusedError", "ProfileMismatchError", "VssAgentAdapter", "build_vss_adapter",
           "redact_url"]
