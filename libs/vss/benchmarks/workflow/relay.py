# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""One-shot, fixed-upstream, loopback-only nonstreaming completion witness.

Each process has one immutable route token and serves exactly one workflow.
Sealing is irreversible. No request/response text or authorization is retained.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import http.client
import json
import math
import re
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from benchmarks.e2e.config import endpoint

LIMIT = 1024**2


def controlled_request(body, cap):
    """Explicit benchmark intervention, applied identically to both clients.

    Preserve application prompts except for the declared no-thinking directive.
    This establishes sent settings, not effective upstream behavior or equal work.
    """
    if "max_completion_tokens" in body or type(cap) is not int or not 1 <= cap <= 4096:
        raise ValueError("controlled_request_cap")
    result = json.loads(json.dumps(body, allow_nan=False))
    messages = result.get("messages")
    if (
        not isinstance(messages, list)
        or not messages
        or any(not isinstance(message, dict) for message in messages)
    ):
        raise ValueError("controlled_request_messages")
    systems = [message for message in messages if message.get("role") == "system"]
    if any(not isinstance(message.get("content"), str) for message in systems):
        raise ValueError("controlled_request_system")
    if systems:
        for message in systems:
            if not message["content"].startswith("/no_think\n"):
                message["content"] = "/no_think\n" + message["content"]
    else:
        messages.insert(0, {"role": "system", "content": "/no_think\n"})
    result.update(max_tokens=cap, temperature=0, top_p=1, seed=1729)
    # Do not silently preserve a contradictory explicit reasoning request.
    if {
        "reasoning_effort",
        "reasoning",
        "chat_template_kwargs",
        "enable_reasoning",
        "enable_thinking",
        "thinking",
        "extra_body",
    }.intersection(result):
        raise ValueError("controlled_request_conflicting_reasoning")
    return result


def request_controls(body):
    """Hash every non-message/model setting, including nested reasoning controls.

    This witnesses the request, not whether the upstream honored a parameter.
    Only allowlisted scalar reasoning labels are published; arbitrary extra
    fields (which may contain sensitive text) are represented only by a hash.
    """
    controls = {
        key: value for key, value in body.items() if key not in {"model", "messages"}
    }
    effort = body.get("reasoning_effort")
    return {
        "schema_version": "requested_generation_controls.1",
        "settings_sha256": hashlib.sha256(
            json.dumps(
                controls, sort_keys=True, separators=(",", ":"), allow_nan=False
            ).encode()
        ).hexdigest(),
        "reasoning_effort": effort
        if isinstance(effort, str)
        and effort in {"none", "minimal", "low", "medium", "high", "xhigh"}
        else "unspecified_or_unrecognized",
        "effective_settings_verified": False,
    }


def request_identity(record):
    """Identify witnessed inputs/settings; missing legacy evidence is not equal.

    Usage, response text and finish reasons are outcomes, not request controls.
    Even a complete identity does not establish upstream parameter compliance.
    """
    controls = record.get("requested_controls")
    if not isinstance(controls, dict) or (
        controls.get("schema_version") != "requested_generation_controls.1"
        or controls.get("effective_settings_verified") is not False
        or not isinstance(record.get("generation"), dict)
    ):
        return None
    hashes = (record.get("messages_sha256"), controls.get("settings_sha256"))
    if any(
        not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value)
        for value in hashes
    ):
        return None
    return (*hashes, record["generation"])


def completion_valid(record, cap_limit=None):
    if (
        record.get("status") != "completed"
        or record.get("http_status") != 200
        or not record.get("model_matches")
        or record.get("finish_reason") != "stop"
        or any(
            record.get("usage", {}).get(name) is None
            for name in ("prompt_tokens", "completion_tokens")
        )
    ):
        return False
    generation = record.get("generation", {})
    caps = [
        generation[name]
        for name in ("max_tokens", "max_completion_tokens")
        if name in generation
    ]
    return (
        len(caps) == 1
        and type(caps[0]) is int
        and 0 < caps[0]
        and (cap_limit is None or caps[0] <= cap_limit)
        and record["usage"]["completion_tokens"] <= caps[0]
    )


class HeaderBudget:
    def __init__(self, inner):
        self.inner, self.remaining = inner, 16384

    def readline(self, size=-1):
        value = self.inner.readline(
            min(size if size >= 0 else 16385, self.remaining + 1)
        )
        self.remaining -= len(value)
        if self.remaining < 0:
            raise http.client.LineTooLong("relay headers")
        return value

    def __getattr__(self, name):
        return getattr(self.inner, name)


class Relay(HTTPServer):
    request_queue_size = 4

    def __init__(
        self,
        upstream,
        model,
        token,
        *,
        port=0,
        deadline_s=60,
        max_records=64,
        generation_cap=None,
        provider_binding=None,
        provider_key=None,
        provider_generation_cap=None,
        workflow_policy=None,
        workflow_ledger=None,
    ):
        if not 0 < deadline_s <= 600 or not 1 <= max_records <= 64:
            raise ValueError("relay bounds")
        # UUID tokens are route identities, not credentials. Never publish them.
        import uuid

        uuid.UUID(token)
        self.upstream, self.model, self.token = upstream, model, token
        self.provider_binding, self.provider_key = provider_binding, provider_key
        self.provider_generation_cap = provider_generation_cap
        self.workflow_policy, self.workflow_ledger = workflow_policy, workflow_ledger
        if (workflow_policy is None) != (workflow_ledger is None):
            raise ValueError("workflow_policy_requires_ledger")
        if workflow_policy is not None and (
            provider_binding is None
            or workflow_policy.role != provider_binding.role
            or provider_generation_cap is None
        ):
            raise ValueError("workflow_provider_selection")
        self.request_limit = (
            LIMIT if workflow_policy is None else workflow_policy.request_limit
        )
        self.provider_requests = self.provider_tokens = 0
        if provider_binding is not None:
            if (
                (provider_binding.role != "llm" and workflow_policy is None)
                or provider_binding.placement != "remote"
                or provider_binding.base_url != upstream
                or provider_binding.model != model
                or not provider_key
                or generation_cap is not None
                or (
                    provider_generation_cap is not None
                    and (
                        provider_binding.request_settings is None
                        or type(provider_generation_cap) is not int
                        or not 1 <= provider_generation_cap <= 2048
                    )
                )
            ):
                raise ValueError("relay_provider_binding")
        elif (
            provider_key
            or provider_generation_cap is not None
            or urlsplit(upstream).hostname
            not in {
                "127.0.0.1",
                "localhost",
                "::1",
            }
        ):
            raise ValueError("remote_relay_requires_binding")
        self.deadline_s, self.max_records = deadline_s, max_records
        if generation_cap is not None and (
            type(generation_cap) is not int or not 1 <= generation_cap <= 4096
        ):
            raise ValueError("generation cap")
        self.generation_cap = generation_cap
        self.records = []
        self.active = self.rejected = self.dropped = 0
        self.sealed = False
        self.admitted = False
        self.started = time.monotonic()
        super().__init__(("127.0.0.1", port), Handler)

    def snapshot(self):
        return {
            "contract": "one_operation_completion_relay.1",
            "generation_policy": "deterministic_no_think.v1"
            if self.generation_cap is not None
            else "declared_provider_controls.v1"
            if self.provider_generation_cap is not None
            else "observe",
            "route_sha256": hashlib.sha256(self.token.encode()).hexdigest(),
            "upstream_sha256": hashlib.sha256(self.upstream.encode()).hexdigest(),
            "provider_binding_sha256": None
            if self.provider_binding is None
            else hashlib.sha256(
                self.provider_binding.model_dump_json().encode()
            ).hexdigest(),
            "provider_requests": self.provider_requests,
            "provider_requested_output_tokens": self.provider_tokens,
            "provider_role": None
            if self.provider_binding is None
            else self.provider_binding.role,
            "workflow_ledger_halted": None
            if self.workflow_ledger is None
            else self.workflow_ledger.halted,
            "sealed": self.sealed,
            "admitted": self.admitted,
            "active": self.active,
            "rejected": self.rejected,
            "dropped": self.dropped,
            "records": list(self.records),
            "complete": self.sealed
            and self.admitted
            and (self.workflow_ledger is None or not self.workflow_ledger.halted)
            and not (self.active or self.rejected or self.dropped)
            and any(r["phase"] == "after_client_admission" for r in self.records)
            and all(r.get("status") == "completed" for r in self.records),
        }

    def admit(self):
        if (
            self.admitted
            or self.sealed
            or self.active
            or self.rejected
            or self.dropped
            or (
                self.workflow_policy is not None
                and (
                    self.workflow_ledger.halted
                    or (self.workflow_policy.role == "vlm" and len(self.records) != 1)
                )
            )
            or not all(completion_valid(r) for r in self.records)
        ):
            self.rejected += 1
            return False
        self.admitted = True
        return True


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def send_error(self, code, message=None, explain=None):
        self.dispatched = True
        self.server.rejected += 1
        self.send(code, b'{"error":"relay_protocol"}')

    def handle_one_request(self):
        self.dispatched = False
        try:
            super().handle_one_request()
        finally:
            # The standard handler swallows idle socket timeouts before dispatch.
            if not self.dispatched:
                self.server.rejected += 1

    def setup(self):
        super().setup()
        self.connection.settimeout(1)
        self.rfile = HeaderBudget(self.rfile)

    def handle(self):
        self.deadline = time.monotonic() + self.server.deadline_s

        def expire():
            try:
                self.connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

        timer = threading.Timer(self.server.deadline_s, expire)
        timer.daemon = True
        timer.start()
        try:
            super().handle()
        finally:
            timer.cancel()
            timer.join(timeout=1)

    def send(self, code, body):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self.dispatched = True
        if self.path.rstrip("/") == "/control/" + self.server.token:
            self.send(200, json.dumps(self.server.snapshot()).encode())
        else:
            self.server.rejected += 1
            self.send(404, b"{}")

    def do_POST(self):
        self.dispatched = True
        server = self.server
        if self.path == "/control/" + server.token + "/arm":
            self.send(
                200 if server.admit() else 409, json.dumps(server.snapshot()).encode()
            )
            return
        if self.path == "/control/" + server.token + "/seal":
            server.sealed = True
            self.send(200, json.dumps(server.snapshot()).encode())
            return
        if self.path != "/" + server.token + "/v1/chat/completions" or server.sealed:
            server.rejected += 1
            if server.workflow_ledger is not None:
                server.workflow_ledger.halted = True
                server.sealed = True
            self.send(400 if server.workflow_policy is not None else 409, b"{}")
            return
        if len(server.records) >= server.max_records:
            server.dropped += 1
            if server.workflow_policy is not None:
                server.sealed = True
                server.workflow_ledger.halted = True
            self.send(400 if server.workflow_policy is not None else 429, b"{}")
            return
        record = {
            "status": "failed",
            "phase": "after_client_admission"
            if server.admitted
            else "before_client_admission",
        }
        server.records.append(record)
        server.active += 1
        started = time.monotonic()
        try:
            lengths = self.headers.get_all("Content-Length", [])
            if (
                len(lengths) != 1
                or not lengths[0].isascii()
                or not lengths[0].isdigit()
                or self.headers.get("Transfer-Encoding")
                or self.headers.get("Content-Encoding", "identity") != "identity"
            ):
                raise ValueError("request_framing")
            length = int(lengths[0])
            if not 0 < length <= server.request_limit:
                raise ValueError("request_limit")
            raw = bytearray()
            while len(raw) < length:
                self.check_deadline()
                block = self.rfile.read1(min(65536, length - len(raw)))
                if not block:
                    raise ValueError("incomplete_request")
                raw.extend(block)
            body = json.loads(raw)
            if body.get("stream") or body.get("model") != server.model:
                raise ValueError("model_or_stream")
            if (
                server.workflow_policy is not None
                and server.workflow_policy.role == "vlm"
            ):
                from benchmarks.workflow.provider_budget import jpeg_witness

                if not server.admitted and len(server.records) != 1:
                    raise ValueError("workflow_vlm_only_one_startup")
                record["frame_delivery"] = jpeg_witness(
                    body,
                    expected=server.workflow_policy.frames_per_request
                    if server.admitted
                    else 0,
                )
            if server.generation_cap is not None:
                record["client_request_sha256"] = hashlib.sha256(raw).hexdigest()
                body = controlled_request(body, server.generation_cap)
                raw = json.dumps(body, separators=(",", ":"), allow_nan=False).encode()
                if len(raw) > LIMIT:
                    raise ValueError("controlled_request_limit")
                record["generation_policy"] = "deterministic_no_think.v1"
            elif server.provider_generation_cap is not None:
                from benchmarks.providers import controlled_provider_request

                record["client_request_sha256"] = hashlib.sha256(raw).hexdigest()
                body = controlled_provider_request(
                    body,
                    server.provider_generation_cap,
                    server.provider_binding.request_settings,
                )
                raw = json.dumps(body, separators=(",", ":"), allow_nan=False).encode()
                record["generation_policy"] = "declared_provider_controls.v1"
            if len(raw) > server.request_limit:
                raise ValueError("controlled_request_limit")
            record["request_sha256"] = hashlib.sha256(raw).hexdigest()
            record["requested_controls"] = request_controls(body)
            record["messages_sha256"] = hashlib.sha256(
                json.dumps(
                    body["messages"], sort_keys=True, separators=(",", ":")
                ).encode()
            ).hexdigest()
            record["generation"] = {
                key: value
                for key in (
                    "max_tokens",
                    "max_completion_tokens",
                    "temperature",
                    "top_p",
                    "seed",
                )
                if type(value := body.get(key)) in (bool, int, float)
                and math.isfinite(value)
                and abs(value) <= 1000000
            }
            record["no_think_directive_present"] = any(
                isinstance(message.get("content"), str)
                and "/no_think" in message["content"]
                for message in body["messages"]
                if isinstance(message, dict)
            )
            headers = {
                "Accept-Encoding": "identity",
                "Content-Type": "application/json",
            }
            if server.provider_binding is not None:
                from benchmarks.providers import complete, request_cap

                cap = request_cap(body)
                policy = server.workflow_policy
                max_requests = 8 if policy is None else policy.max_requests
                max_tokens = 8192 if policy is None else policy.max_requested_tokens
                wall_s = 600 if policy is None else policy.wall_s
                if (
                    server.provider_requests >= max_requests
                    or server.provider_tokens + cap > max_tokens
                    or time.monotonic() - server.started >= wall_s
                ):
                    server.sealed = True
                    raise ValueError("provider_workflow_budget")
                server.provider_requests += 1
                server.provider_tokens += cap
                ordinal = None
                if server.workflow_ledger is not None:
                    ordinal = server.workflow_ledger.debit(
                        cap, role=policy.role, request_sha256=record["request_sha256"]
                    )
                    record["workflow_ledger_ordinal"] = ordinal
                evidence, payload = asyncio.run(
                    complete(
                        server.provider_binding,
                        body,
                        key=server.provider_key,
                        timeout_s=min(
                            90,
                            self.deadline - time.monotonic(),
                            wall_s - (time.monotonic() - server.started),
                            server.workflow_ledger.remaining_s
                            if server.workflow_ledger is not None
                            else 600,
                        ),
                    )
                )
                record["provider_witness"] = evidence
                valid_provider = (
                    payload is not None
                    and evidence.get("finish_reason") == "stop"
                    and evidence.get("reported_output_within_requested_cap") is True
                    and not evidence.get("disabled_reasoning_contradicted", False)
                    and all(
                        evidence.get("usage", {}).get(k) is not None
                        for k in ("prompt_tokens", "completion_tokens")
                    )
                )
                if ordinal is not None:
                    server.workflow_ledger.settle(ordinal, valid=valid_provider)
                    ordinal = None
                if (
                    evidence.get("finish_reason") != "stop"
                    or evidence.get("reported_output_within_requested_cap") is not True
                    or evidence.get("disabled_reasoning_contradicted", False)
                ):
                    server.sealed = True
                if server.workflow_policy is not None and not valid_provider:
                    raise ValueError("workflow_provider_controls_failed")
                if payload is None:
                    server.sealed = True
                    record["http_status"] = evidence.get("http_status")
                    raise ValueError("remote_provider_failed")
                status, output = 200, json.dumps(payload, allow_nan=False).encode()
                if len(output) > LIMIT:
                    raise ValueError("remote_relay_response_limit")
            else:
                if auth := self.headers.get("Authorization"):
                    headers["Authorization"] = auth
                status, output = asyncio.run(self.forward(raw, headers))
            record["http_status"] = status
            payload = json.loads(output)
            choices, usage = payload.get("choices") or [], payload.get("usage") or {}
            reason = choices[0].get("finish_reason") if len(choices) == 1 else None
            record.update(
                status="completed",
                model_matches=payload.get("model")
                == (
                    server.provider_binding.response_model
                    if server.provider_binding is not None
                    else server.model
                ),
                finish_reason=reason
                if reason in {"stop", "length", "content_filter"}
                else "unknown",
                usage={
                    key: value
                    if type(value := usage.get(key)) is int and 0 <= value <= 10000000
                    else None
                    for key in ("prompt_tokens", "completion_tokens")
                },
                response_sha256=hashlib.sha256(output).hexdigest(),
            )
            self.check_deadline()
            self.send(status, output)
        except Exception as error:
            if server.provider_binding is not None:
                # Unknown upstream settlement blocks retries from either client.
                server.sealed = True
            if server.workflow_ledger is not None:
                server.workflow_ledger.halted = True
            record.update(status="failed", failure=type(error).__name__)
            try:
                self.send(
                    400 if server.workflow_policy is not None else 502,
                    b'{"error":"relay_request_failed"}',
                )
            except OSError:
                pass
        finally:
            record["elapsed_s"] = time.monotonic() - started
            server.active -= 1

    async def forward(self, raw, headers):
        async with asyncio.timeout(max(0, self.deadline - time.monotonic())):
            async with httpx.AsyncClient(
                timeout=self.server.deadline_s, trust_env=False, follow_redirects=False
            ) as client:
                async with client.stream(
                    "POST",
                    self.server.upstream + "/chat/completions",
                    content=bytes(raw),
                    headers=headers,
                ) as response:
                    if (
                        response.headers.get("Content-Encoding", "identity")
                        != "identity"
                    ):
                        raise ValueError("response_encoding")
                    output = bytearray()
                    async for block in response.aiter_raw():
                        if len(output) + len(block) > LIMIT:
                            raise ValueError("response_limit")
                        output.extend(block)
                    return response.status_code, bytes(output)

    def check_deadline(self):
        if time.monotonic() >= self.deadline:
            raise TimeoutError("relay_deadline")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upstream-env", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--deadline-s", type=float, default=120)
    parser.add_argument("--generation-cap", type=int)
    parser.add_argument("--provider-binding", type=Path)
    parser.add_argument("--credential-file", type=Path)
    parser.add_argument("--credential-name", default="INFERENCEHUB_TOKEN")
    parser.add_argument("--provider-generation-cap", type=int)
    args = parser.parse_args()
    binding = key = None
    if args.provider_binding is not None:
        from benchmarks.providers import Binding, private_key

        binding = Binding.model_validate_json(args.provider_binding.read_bytes())
        key = private_key(args.credential_file, args.credential_name)
    elif args.credential_file is not None:
        raise ValueError("relay_credential_requires_binding")
    with Relay(
        endpoint(args.upstream_env),
        args.model,
        args.token,
        port=args.port,
        deadline_s=args.deadline_s,
        generation_cap=args.generation_cap,
        provider_binding=binding,
        provider_key=key,
        provider_generation_cap=args.provider_generation_cap,
    ) as server:
        server.serve_forever(poll_interval=0.1)


if __name__ == "__main__":
    main()
