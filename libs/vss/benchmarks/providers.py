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

"""Bounded provider probes and private bindings, independent of GPU allocation.

These are capability observations, never an effective-control or placement
qualification. Credentials remain on the machine executing the probe. There
are no SDK retries, redirects, environment proxies or caller-supplied auth.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import stat
import time
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import httpx
from pydantic import Field, model_validator

from benchmarks.e2e.config import Strict

REMOTE_HOSTS = frozenset({"inference-api.nvidia.com"})
RESPONSE_LIMIT = 2 * 1024**2


def digest(value):
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def request_cap(body):
    """Only one bounded completion; provider billing is still not guaranteed."""
    caps = [
        body[name] for name in ("max_tokens", "max_completion_tokens") if name in body
    ]
    if len(caps) != 1 or type(caps[0]) is not int or not 1 <= caps[0] <= 2048:
        raise ValueError("probe_token_cap")
    if any(
        type(body[name]) is not int or body[name] != 1
        for name in ("n", "best_of")
        if name in body
    ):
        raise ValueError("probe_multiple_completions")
    return caps[0]


class RequestedSettings(Strict):
    temperature: float = Field(ge=0, le=2)
    top_p: float = Field(gt=0, le=1)
    seed: int
    reasoning_control: Literal["chat_template_disabled", "reasoning_effort_none"]


def controlled_provider_request(body, cap, settings: RequestedSettings):
    """Apply declared model-specific settings identically without prompt edits.

    This is a request intervention, not evidence the provider honored it. An
    unsupported parameter must fail the cell; never silently retry without it.
    """
    value = json.loads(json.dumps(body, allow_nan=False))
    if "max_completion_tokens" in value or {
        "reasoning_effort",
        "reasoning",
        "chat_template_kwargs",
        "enable_reasoning",
        "enable_thinking",
        "thinking",
        "extra_body",
    }.intersection(value):
        raise ValueError("provider_conflicting_generation_controls")
    value.update(
        max_tokens=cap,
        temperature=settings.temperature,
        top_p=settings.top_p,
        seed=settings.seed,
    )
    if settings.reasoning_control == "chat_template_disabled":
        value["chat_template_kwargs"] = {"enable_thinking": False}
    else:
        value["reasoning_effort"] = "none"
    request_cap(value)
    return value


class Binding(Strict):
    """An exact route/model declaration; unknown identity stays unknown."""

    label: str = Field(pattern=r"^[a-z][a-z0-9-]{0,47}$")
    role: Literal["vlm", "llm"]
    placement: Literal["local", "remote"]
    base_url: str = Field(max_length=256)
    model: str = Field(pattern=r"^[A-Za-z0-9_.:/-]{1,128}$")
    # Some providers return a different model ID. Declare it BEFORE inference.
    response_model: str = Field(pattern=r"^[A-Za-z0-9_.:/-]{1,128}$")
    revision: str | None = Field(default=None, min_length=1, max_length=256)
    precision: str | None = Field(default=None, min_length=1, max_length=64)
    engine: str | None = Field(default=None, min_length=1, max_length=128)
    request_settings: RequestedSettings | None = None

    @model_validator(mode="after")
    def route(self):
        url = urlsplit(self.base_url)
        if (
            url.username is not None
            or url.password is not None
            or url.query
            or url.fragment
            or url.path != "/v1"
        ):
            raise ValueError("provider_route")
        if self.placement == "remote":
            if (
                url.scheme != "https"
                or url.hostname not in REMOTE_HOSTS
                or url.netloc != url.hostname
            ):
                raise ValueError("provider_remote_route")
        elif (
            url.scheme != "http"
            or url.hostname != "127.0.0.1"
            or url.port is None
            or url.netloc != f"127.0.0.1:{url.port}"
        ):
            raise ValueError("provider_local_route")
        return self


def private_key(path: Path, name: str) -> str:
    """Read one literal assignment, never evaluate a shell/dotenv program."""
    if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,127}", name):
        raise ValueError("credential_name")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_mode & 0o077
            or info.st_size > 65536
        ):
            raise ValueError("credential_file_admission")
        raw = stream.read(65537)
    if len(raw) > 65536:
        raise ValueError("credential_file_budget")
    values = []
    for line in raw.decode().splitlines():
        key, sep, value = line.strip().removeprefix("export ").partition("=")
        if sep and key.strip() == name:
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            if not re.fullmatch(r"[A-Za-z0-9_./+=:-]{8,8192}", value):
                raise ValueError("credential_literal")
            values.append(value)
    if len(values) != 1:
        raise ValueError("credential_assignment")
    return values[0]


async def complete(binding: Binding, body: dict, *, key: str | None, timeout_s: float):
    """Return sanitized metadata plus successful private payload; one HTTP call.

    A failure halts the session. In particular, a local timeout cannot establish
    that an upstream inference job stopped. Never return upstream error bodies.
    """
    if (binding.placement == "remote") != bool(key):
        raise ValueError("provider_auth_selection")
    if not 0 < timeout_s <= 90:
        raise ValueError("provider_deadline")
    if body.get("model") != binding.model or body.get("stream", False) is not False:
        raise ValueError("provider_model_or_stream")
    if not isinstance(body.get("messages"), list) or not body["messages"]:
        raise ValueError("provider_messages")
    request_cap(body)
    raw = json.dumps(body, allow_nan=False, separators=(",", ":")).encode()
    limit = (16 if binding.role == "vlm" else 1) * 1024**2
    if len(raw) > limit:
        raise ValueError("provider_request_budget")
    headers = {"Content-Type": "application/json", "Accept-Encoding": "identity"}
    if key:
        headers["Authorization"] = "Bearer " + key
    result = dict(
        status="failed",
        binding_sha256=digest(binding.model_dump()),
        request_sha256=hashlib.sha256(raw).hexdigest(),
        messages_sha256=digest(body["messages"]),
        settings_sha256=digest(
            {k: v for k, v in body.items() if k not in {"model", "messages"}}
        ),
        effective_settings_verified=False,
        placement_comparison_qualified=False,
        provider_gpu_metrics="unavailable"
        if binding.placement == "remote"
        else "not_sampled",
        upstream_settlement="unknown",
    )
    started = time.monotonic()
    try:
        async with asyncio.timeout(timeout_s):
            async with httpx.AsyncClient(
                trust_env=False, follow_redirects=False, timeout=timeout_s
            ) as client:
                async with client.stream(
                    "POST",
                    binding.base_url + "/chat/completions",
                    content=raw,
                    headers=headers,
                ) as response:
                    result["http_status"] = response.status_code
                    if response.status_code != 200:
                        result["failure"] = "provider_http_status"
                        return result, None
                    if (
                        response.headers.get("Content-Encoding", "identity")
                        != "identity"
                    ):
                        raise ValueError("provider_response_encoding")
                    output = bytearray()
                    async for block in response.aiter_raw():
                        if len(output) + len(block) > RESPONSE_LIMIT:
                            raise ValueError("provider_response_budget")
                        output.extend(block)
        if key and key.encode() in output:
            raise ValueError("provider_credential_echo")
        payload = json.loads(output)
        if key and key in json.dumps(payload, ensure_ascii=False):
            raise ValueError("provider_decoded_credential_echo")
        returned_model = payload.get("model")
        result["response_model"] = (
            returned_model
            if isinstance(returned_model, str)
            and re.fullmatch(r"[A-Za-z0-9_.:/-]{1,128}", returned_model)
            else None
        )
        choices, usage = payload.get("choices"), payload.get("usage", {})
        if (
            payload.get("model") != binding.response_model
            or not isinstance(choices, list)
            or len(choices) != 1
            or not isinstance(choices[0].get("message", {}).get("content"), str)
        ):
            raise ValueError("provider_completion_contract")
        reason = choices[0].get("finish_reason")
        reasoning_usage = (
            usage.get("completion_tokens_details", {}).get("reasoning_tokens")
            if isinstance(usage.get("completion_tokens_details"), dict)
            else None
        )
        result.update(
            status="completed_probe",
            upstream_settlement="response_received_not_internal_job_audit",
            model_matches=True,
            response_sha256=hashlib.sha256(output).hexdigest(),
            finish_reason=reason
            if reason in {"stop", "length", "content_filter"}
            else "unknown",
            usage={
                name: value
                if type(value := usage.get(name)) is int and 0 <= value <= 10000000
                else None
                for name in ("prompt_tokens", "completion_tokens")
            },
            reasoning_usage=reasoning_usage
            if type(reasoning_usage) is int and 0 <= reasoning_usage <= 10000000
            else None,
        )
        message = choices[0]["message"]
        template = body.get("chat_template_kwargs")
        result["reasoning_disabled_requested"] = (
            body.get("reasoning_effort") == "none"
            or isinstance(template, dict)
            and template.get("enable_thinking") is False
        )
        result["reasoning_text_present"] = any(
            isinstance(message.get(name), str) and bool(message[name].strip())
            for name in ("reasoning_content", "reasoning")
        )
        result["disabled_reasoning_contradicted"] = result[
            "reasoning_disabled_requested"
        ] and (result["reasoning_text_present"] or (result["reasoning_usage"] or 0) > 0)
        result["reported_output_within_requested_cap"] = result["usage"][
            "completion_tokens"
        ] is not None and result["usage"]["completion_tokens"] <= request_cap(body)
        return result, payload
    except Exception as error:
        # Class only: provider exceptions and body text can echo credentials.
        result["failure"] = type(error).__name__
        return result, None
    finally:
        result["elapsed_s"] = time.monotonic() - started


class ProbeSession:
    """A non-resumable manifest-wide quota, including failures and timeouts."""

    def __init__(self, root: Path):
        from benchmarks.common import software_hash

        root.mkdir(mode=0o700, parents=False, exist_ok=False)
        self.root = root
        self.started = time.monotonic()
        self.requests = self.tokens = 0
        self.halted = False
        self.write(
            "intent.json",
            {
                "schema_version": "provider_probe_budget.1",
                "software_sha256": software_hash(Path(__file__).resolve().parents[1]),
                "max_requests": 8,
                "max_requested_output_tokens": 8192,
                "wall_limit_s": 600,
                "per_request_limit_s": 90,
                "retries": 0,
                "billing_or_effective_reasoning_token_cap": False,
                "thresholds_approved": False,
                "acceptance_eligible": False,
            },
        )

    def write(self, name, value):
        fd = os.open(self.root / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, indent=2, allow_nan=False)
            stream.write("\n")

    async def probe(self, binding, body, *, key):
        cap = request_cap(body)
        left = 600 - (time.monotonic() - self.started)
        if self.halted or self.requests >= 8 or self.tokens + cap > 8192 or left <= 0:
            raise ValueError("probe_session_closed")
        index = self.requests
        self.requests += 1
        self.tokens += cap
        # Debit and persist BEFORE the sole HTTP attempt; no recovery/retry path.
        self.write(
            f"{index:02d}-intent.json",
            {
                "binding": binding.model_dump(),
                "request_sha256": digest(body),
                "requested_output_tokens": cap,
                "ordinal": self.requests,
                "cumulative_requested_output_tokens": self.tokens,
            },
        )
        self.halted = True
        result, payload = await complete(
            binding, body, key=key, timeout_s=min(90, left)
        )
        self.write(f"{index:02d}-result.json", result)
        if payload is not None:
            self.write(f"{index:02d}-response-private.json", payload)
        # A truncated or unidentified response is not a capability pass.
        self.halted = not (
            result["status"] == "completed_probe"
            and result.get("finish_reason") == "stop"
            and result.get("reported_output_within_requested_cap") is True
            and not result.get("disabled_reasoning_contradicted", False)
        )
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--credential-file", type=Path)
    parser.add_argument("--credential-name", default="INFERENCEHUB_TOKEN")
    args = parser.parse_args()

    async def run():
        if args.manifest.stat().st_size > 48 * 1024**2:
            raise ValueError("probe_manifest_budget")
        manifest = json.loads(args.manifest.read_bytes())
        if not isinstance(manifest, list) or not 1 <= len(manifest) <= 8:
            raise ValueError("probe_manifest_count")
        bindings = [Binding.model_validate(item["binding"]) for item in manifest]
        if sum(request_cap(item["request"]) for item in manifest) > 8192:
            raise ValueError("probe_manifest_token_budget")
        key = (
            private_key(args.credential_file, args.credential_name)
            if args.credential_file
            else None
        )
        session = ProbeSession(args.root)
        for item, binding in zip(manifest, bindings, strict=True):
            await session.probe(
                binding,
                item["request"],
                key=key if binding.placement == "remote" else None,
            )
            if session.halted:
                break
        return {
            "status": "halted" if session.halted else "completed_probes",
            "requests": session.requests,
            "requested_output_tokens": session.tokens,
            "acceptance_eligible": False,
        }

    try:
        result = asyncio.run(run())
    except Exception as error:
        result = {"status": "failed", "failure": type(error).__name__}
    print(json.dumps(result))
    return 0 if result["status"] == "completed_probes" else 3


if __name__ == "__main__":
    raise SystemExit(main())
