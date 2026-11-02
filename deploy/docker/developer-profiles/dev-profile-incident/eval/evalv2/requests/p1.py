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
"""The P1 (incident extraction) request, built from ``contracts/`` exactly as V1 and the console build it.

The body is the video (a signed URL) first, then the prompt text, with the contract schema enforced through a
strict ``response_format`` and the P1 settings read from ``contracts/inference.json``. The default prompt is the
production prompt; an experiment prompt is named by its registry id and served only if its SHA-256 still
matches (``prompt_registry.prompt_text``). ``tests/v2/test_v2_adapters.py`` pins the body to V1's
``eval_vlm_client.p1_request_payload``.
"""

from __future__ import annotations

import json
import re
from typing import Any

import contract
import prompt_registry
from evalv2.core.hashing import sha256_bytes, sha256_json


def p1_settings() -> dict[str, Any]:
    return dict(json.loads(contract.INFERENCE_PATH.read_text())["p1"])


def base_prompt_id() -> str:
    """The registry id of the current contract's production prompt (``incident-contract-v4`` -> ``p1-v4-base``)."""
    version = contract.contract_version()
    match = re.fullmatch(r"incident-contract-v(\d+)", version)
    if not match:
        raise ValueError(f"unrecognised contract version {version!r}")
    return f"p1-v{match.group(1)}-base"


def prompt_text(prompt_id: str | None) -> str:
    return contract.extraction_prompt() if prompt_id is None else prompt_registry.prompt_text(prompt_id)


def build_p1_request(model: str, video_url: str, *, prompt_id: str | None = None) -> dict[str, Any]:
    content = [{"type": "video_url", "video_url": {"url": video_url}},
               {"type": "text", "text": prompt_text(prompt_id)}]
    return {"model": model, "messages": [{"role": "user", "content": content}], **p1_settings(),
            "response_format": contract.response_format()}


def p1_provenance(prompt_id: str | None = None) -> dict[str, Any]:
    """What a P1 record pins: prompt id and SHA-256, contract version, settings and schema hashes."""
    return {"stage": "p1", "prompt_id": prompt_id or base_prompt_id(),
            "prompt_sha256": sha256_bytes(prompt_text(prompt_id).encode()),
            "contract_version": contract.contract_version(), "settings_sha256": sha256_json(p1_settings()),
            "response_format_sha256": sha256_json(contract.response_format())}
