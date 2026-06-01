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

"""Runtime helpers for the pluggable VLM-response parser path (Option B).

This module is the *single source of truth* for the shape that pluggable
parsers produce on the wire. Keeping these helpers in :mod:`models` (rather
than inside ``enhance_alert_with_vlm``) buys two architectural wins:

1. ``handlers.direct_media.DirectMediaHandler`` can import them at module
   load time instead of paying a lazy-import penalty on every VLM
   response — no more circular-import smell between orchestrator and
   Mode-3 handler.
2. ``models.base_response_parser`` keeps its narrow contract+loader role;
   merge/error side effects live next door but do not pollute it.

The helpers assume the Option B schema contract:

* Default verification path → emits ``info["reasoning"]`` via
  :class:`models.responses.VLMResponse`.
* Pluggable-parser path → emits ``info["vlm_response"]`` (JSON-serialized
  parser output) with ``info["verdict"] = ""``.

The two schemas are disjoint by construction: a given message either
runs through the default path or the pluggable path, never both.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from models.responses import AlertBridgeResponse, merge_info_with_response


logger = logging.getLogger(__name__)


# Sentinel status strings emitted by the pluggable-parser path. Kept near
# the top of the module so downstream consumers / docs can reference one
# source. The error status is prefix-matched by ``_handle_vlm_error`` /
# retry logic, so changing it is a breaking wire change.
PLUGGABLE_PARSER_OK_STATUS = "OK"
PLUGGABLE_PARSER_ERROR_STATUS = "Pluggable parser failed"


# Error-source bucket keys emitted into ``info["errorSource"]`` so
# downstream consumers can classify failures without substring-matching
# free-form status text. Keep the
# set small and stable — each bucket corresponds to a concrete layer in
# the ingestion pipeline:
#   * ``pluggable_parser`` — custom ``parse()`` raised or returned a
#     non-dict. The VLM produced output but the parser could not map it.
#   * ``vlm_schema``       — the default verification path could not
#     coerce VLM text into :class:`VLMResponse` (e.g. missing verdict
#     tag, JSON decode failure, "not in expected format").
#   * ``vlm_api``          — the VLM endpoint itself failed or produced
#     no output (timeout, connection error, 5xx, 4xx, empty response).
#   * ``media_download``   — Mode-3 media fetch failed before the VLM
#     was even called.
ERROR_SOURCE_PLUGGABLE_PARSER = "pluggable_parser"
ERROR_SOURCE_VLM_SCHEMA = "vlm_schema"
ERROR_SOURCE_VLM_API = "vlm_api"
ERROR_SOURCE_MEDIA_DOWNLOAD = "media_download"


def safe_json_dumps_parser_output(parsed: Dict[str, Any]) -> str:
    """Serialize pluggable-parser output to JSON without crashing at runtime.

    Parser authors are expected to return JSON-serializable dicts, but they
    sometimes return ``datetime`` / ``Decimal`` / ``bytes`` / ``set`` by
    accident. Falling back to ``default=str`` keeps the event shippable
    instead of aborting the whole message with a :class:`TypeError`.
    """
    try:
        return json.dumps(parsed)
    except TypeError:
        logger.warning(
            "Pluggable parser returned non-JSON-serializable values; "
            "falling back to str() coercion. Fix the parser to return "
            "primitives only."
        )
        return json.dumps(parsed, default=str)


def apply_pluggable_parser_output(
    message: Dict[str, Any],
    parsed: Dict[str, Any],
    *,
    video_source: Optional[str],
    latency: Optional[Dict[str, Any]] = None,
    include_latency: bool = False,
) -> None:
    """Merge pluggable parser output into ``message["info"]``.

    Single source of truth for the pluggable-parser output shape — used by
    the VST path, the local-file path, and the direct-media path so all
    three ingestion paths carry the same latency/transport-metadata contract.

    Schema contract (Option B — conditional schema):
        The pluggable-parser path writes the parser JSON to
        ``info["vlm_response"]`` — **not** ``info["reasoning"]``.
        The default verification path is unaffected and keeps emitting
        ``info["reasoning"]``. This preserves zero wire change for
        deployments that never opt into ``vlm.response_parser``.

    Shape produced (conceptual; nvschema alignment stringifies on wire):

        info["vlm_response"] = json.dumps(parsed)
        info["verdict"]      = ""
        info["videoSource"]  = video_source
        info["verificationResponseCode"]   = 200
        info["verificationResponseStatus"] = "OK"
    """
    # Pass an AlertBridgeResponse *without* a VLMResponse so the merge does
    # not emit ``info["reasoning"]``. Transport metadata (video_source,
    # codes) still flows through merge_info_with_response so the
    # map<string,string> stringification and latency logic are applied
    # uniformly.
    merge_info_with_response(
        message,
        AlertBridgeResponse(
            vlm_response=None,
            video_source=video_source,
            verification_response_code=200,
            verification_response_status=PLUGGABLE_PARSER_OK_STATUS,
        ),
        latency=latency,
        include_latency=include_latency,
    )
    info = message.get("info") or {}
    info["vlm_response"] = safe_json_dumps_parser_output(parsed)
    # Pluggable parsers do not emit a binary verdict, so the contract is
    # always ``verdict=""`` (nvschema alignment None→"" stringification). We
    # overwrite unconditionally so stale verdicts from retries or upstream
    # pollution do not leak through.
    info["verdict"] = ""
    message["info"] = info


def apply_pluggable_parser_error(
    message: Dict[str, Any],
    error: Exception,
    *,
    video_source: Optional[str],
    latency: Optional[Dict[str, Any]] = None,
    include_latency: bool = False,
) -> None:
    """Emit an explicit error event when a pluggable parser ``parse()`` raises.

    Distinct from VLM response-schema failures so operators can tell whether
    the VLM produced bad output or the custom parser itself crashed. The
    structured ``info["errorSource"]`` field is set to
    :data:`ERROR_SOURCE_PLUGGABLE_PARSER` so downstream consumers can
    classify without substring-matching the status text.

    Schema contract (Option B): on pluggable-parser failure we
    do **not** emit ``info["vlm_response"]`` (there is no valid parser
    output to serialize). We also do **not** emit ``info["reasoning"]``,
    because the pluggable path owns the ``vlm_response`` slot and the
    default-path reasoning did not run.
    """
    logger.warning(
        "Pluggable parser failed",
        extra={
            "id": message.get("id"),
            "sensorId": message.get("sensorId"),
            "error_type": type(error).__name__,
            "error": str(error),
        },
    )
    merge_info_with_response(
        message,
        AlertBridgeResponse(
            vlm_response=None,
            video_source=video_source,
            verification_response_code=500,
            verification_response_status=(
                f"{PLUGGABLE_PARSER_ERROR_STATUS}: {type(error).__name__}: {error}"
            ),
            verdict="verification-failed",
            error_source=ERROR_SOURCE_PLUGGABLE_PARSER,
        ),
        latency=latency,
        include_latency=include_latency,
    )


__all__ = [
    "PLUGGABLE_PARSER_OK_STATUS",
    "PLUGGABLE_PARSER_ERROR_STATUS",
    "ERROR_SOURCE_PLUGGABLE_PARSER",
    "ERROR_SOURCE_VLM_SCHEMA",
    "ERROR_SOURCE_VLM_API",
    "ERROR_SOURCE_MEDIA_DOWNLOAD",
    "safe_json_dumps_parser_output",
    "apply_pluggable_parser_output",
    "apply_pluggable_parser_error",
]
