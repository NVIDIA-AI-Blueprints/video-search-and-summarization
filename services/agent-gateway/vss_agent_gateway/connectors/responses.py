# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""OpenAI Responses protocol connector used by any compatible agent harness."""

from __future__ import annotations

import hashlib
import json
import threading
import urllib.error
import urllib.request
from collections import OrderedDict
from collections.abc import Iterator
from dataclasses import dataclass
from threading import Event

from ..artifacts import (
    ARTIFACT_CLOSE,
    ARTIFACT_OPEN,
    parse_artifact,
    strip_artifact_envelopes,
)
from ..config import GatewayConfig
from ..contract import ConnectorEvent, CreateRunRequest, Message, transcript_digest
from ..json_codec import strict_json_loads
from ..sse import iter_sse
from .base import Connector, ConnectorError

PUBLISH_ARTIFACT_TOOL = "vss_ui_publish_artifact"
MAX_CLIENT_TOOL_ROUNDS = 4
SUPPORTED_ARTIFACT_KINDS = frozenset({"vss.search.results", "vss.alert.incidents"})
ARTIFACT_TRANSPORT_INSTRUCTIONS = """VSS UI artifact transport contract:
- If this turn successfully produces a validated VSS search result or alert-incident result and the vss_ui_publish_artifact tool is available, you must call that tool exactly once with the exact version, kind, and payload required by the loaded VSS skill. Human-readable prose or a table is not a substitute.
- Do not call the publisher for ordinary chat, failed operations, unvalidated data, alert-rule inventory, or alert-rule mutation.
- When the publisher is unavailable, follow the loaded VSS skill's vss-ui-artifact envelope fallback.
- After a successful publisher result, finish the human-facing response without repeating its machine-readable payload."""
PUBLISH_ARTIFACT_DEFINITION: dict[str, object] = {
    "type": "function",
    "name": PUBLISH_ARTIFACT_TOOL,
    "description": (
        "Publish a validated VSS search-result or alert-incident payload to the "
        "VSS UI. Use this exactly once when an installed VSS skill requires a UI "
        "artifact; do not use it for ordinary chat or unvalidated data."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "version": {"type": "string", "enum": ["1.0"]},
            "kind": {
                "type": "string",
                "enum": sorted(SUPPORTED_ARTIFACT_KINDS),
            },
            "payload": {"type": "object"},
        },
        "required": ["version", "kind", "payload"],
        "additionalProperties": False,
    },
}


@dataclass(frozen=True, slots=True)
class _ThreadState:
    previous_response_id: str
    transcript: tuple[Message, ...]
    transcript_digest: str
    transcript_chars: int


class ResponsesConnector(Connector):
    """Translate a standard Responses SSE stream without naming the harness."""

    def __init__(self, config: GatewayConfig) -> None:
        self._config = config
        self._endpoint = f"{config.backend_url}{config.backend_path}"
        self._lock = threading.RLock()
        self._thread_state: OrderedDict[str, _ThreadState] = OrderedDict()
        self._thread_state_chars = 0
        self._active_responses: dict[str, object] = {}

    @property
    def protocol(self) -> str:
        return "responses"

    def _session_key(self, thread_id: str) -> str:
        digest = hashlib.sha256(f"vss-ui:{thread_id}".encode()).hexdigest()[:40]
        return f"vss-ui:{digest}"

    def _select_input(
        self,
        request: CreateRunRequest,
    ) -> tuple[tuple[Message, ...], str | None, tuple[Message, ...]]:
        """Continue only when the browser transcript still matches our saved chain."""

        with self._lock:
            state = self._thread_state.get(request.thread_id)
            if state is not None:
                self._thread_state.move_to_end(request.thread_id)
        prefix = request.history_prefix()
        if (
            state is not None
            and state.previous_response_id
            and (
                not request.history
                or transcript_digest(prefix) == state.transcript_digest
            )
        ):
            return (
                request.input,
                state.previous_response_id,
                state.transcript + request.input,
            )

        selected = request.full_transcript()
        return selected, None, selected

    @property
    def _artifact_publisher_enabled(self) -> bool:
        return self._config.vss_capabilities is not None

    def _request_instructions(self, requested: str | None) -> str | None:
        if not self._artifact_publisher_enabled:
            return requested
        if not requested:
            return ARTIFACT_TRANSPORT_INSTRUCTIONS
        return f"{requested}\n\n{ARTIFACT_TRANSPORT_INSTRUCTIONS}"

    def _request_payload(
        self,
        request: CreateRunRequest,
    ) -> tuple[dict[str, object], tuple[Message, ...]]:
        selected, previous_response_id, transcript = self._select_input(request)
        payload: dict[str, object] = {
            "model": self._config.backend_model,
            "input": [message.to_responses_item() for message in selected],
            "stream": True,
            "store": True,
        }
        if self._artifact_publisher_enabled:
            # This is a Responses-protocol capability, not a harness adapter.
            # Servers that implement client tools expose it to their agent; a
            # server that owns all tools can ignore it and publish the same
            # artifact through a streamed tool result or final-text envelope.
            payload["tools"] = [PUBLISH_ARTIFACT_DEFINITION]
        if previous_response_id:
            payload["previous_response_id"] = previous_response_id
        instructions = self._request_instructions(request.instructions)
        if instructions:
            payload["instructions"] = instructions
        if self._config.backend_session_field:
            payload[self._config.backend_session_field] = self._session_key(
                request.thread_id
            )
        return payload, transcript

    def _client_tool_payload(
        self,
        request: CreateRunRequest,
        previous_response_id: str,
        outputs: list[dict[str, object]],
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "model": self._config.backend_model,
            "input": outputs,
            "previous_response_id": previous_response_id,
            "stream": True,
            "store": True,
        }
        if self._artifact_publisher_enabled:
            payload["tools"] = [PUBLISH_ARTIFACT_DEFINITION]
        instructions = self._request_instructions(request.instructions)
        if instructions:
            payload["instructions"] = instructions
        if self._config.backend_session_field:
            payload[self._config.backend_session_field] = self._session_key(
                request.thread_id
            )
        return payload

    def _encoded_request(
        self,
        payload: dict[str, object],
        *,
        thread_id: str,
    ) -> urllib.request.Request:
        headers = {
            "Accept": "text/event-stream",
            "Content-Type": "application/json",
            "User-Agent": "vss-agent-gateway/1.0",
            **self._config.backend_headers,
        }
        if self._config.backend_token:
            headers["Authorization"] = f"Bearer {self._config.backend_token}"
        if self._config.backend_session_header:
            headers[self._config.backend_session_header] = self._session_key(thread_id)
        encoded = json.dumps(
            payload, ensure_ascii=False, separators=(",", ":")
        ).encode()
        return urllib.request.Request(
            self._endpoint, data=encoded, headers=headers, method="POST"
        )

    def _http_request(
        self, request: CreateRunRequest
    ) -> tuple[urllib.request.Request, tuple[Message, ...]]:
        payload, transcript = self._request_payload(request)
        return self._encoded_request(payload, thread_id=request.thread_id), transcript

    @staticmethod
    def _error_message(payload: object, fallback: str) -> str:
        if isinstance(payload, dict):
            error = payload.get("error")
            if isinstance(error, dict) and isinstance(error.get("message"), str):
                return error["message"]
            response = payload.get("response")
            if isinstance(response, dict):
                nested_error = response.get("error")
                if isinstance(nested_error, dict) and isinstance(
                    nested_error.get("message"), str
                ):
                    return nested_error["message"]
        return fallback

    @staticmethod
    def _response_id(payload: object) -> str | None:
        if not isinstance(payload, dict):
            return None
        response = payload.get("response")
        if isinstance(response, dict) and isinstance(response.get("id"), str):
            return response["id"]
        if isinstance(payload.get("id"), str) and str(payload["id"]).startswith(
            "resp_"
        ):
            return str(payload["id"])
        return None

    @staticmethod
    def _function_call(payload: object) -> dict[str, object] | None:
        if not isinstance(payload, dict):
            return None
        item = payload.get("item")
        if not isinstance(item, dict):
            return None
        if item.get("type") != "function_call":
            return None
        return item

    @staticmethod
    def _function_call_output(payload: object) -> dict[str, object] | None:
        if not isinstance(payload, dict):
            return None
        item = payload.get("item")
        if not isinstance(item, dict) or item.get("type") != "function_call_output":
            return None
        return item

    @staticmethod
    def _publish_artifact_result(
        call_id: str,
        arguments: str,
    ) -> tuple[ConnectorEvent, dict[str, object]]:
        artifact = parse_artifact(arguments)
        if artifact is None or artifact.kind not in SUPPORTED_ARTIFACT_KINDS:
            message = (
                "artifact arguments must be a valid version 1.0 VSS search "
                "or alert artifact"
            )
            result = json.dumps(
                {"ok": False, "error": message},
                separators=(",", ":"),
            )
            return (
                ConnectorEvent(
                    "tool.failed",
                    {
                        "tool_call_id": call_id,
                        "name": PUBLISH_ARTIFACT_TOOL,
                        "arguments": arguments,
                        "error": message,
                    },
                ),
                {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": result,
                },
            )

        payload = json.dumps(
            {
                "version": "1.0",
                "kind": artifact.kind,
                "payload": artifact.payload,
            },
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        result = json.dumps(
            {
                "ok": True,
                "artifact_id": artifact.artifact_id,
                "message": "Published to the VSS UI; finish the human-facing answer.",
            },
            separators=(",", ":"),
        )
        return (
            ConnectorEvent(
                "tool.completed",
                {
                    "tool_call_id": call_id,
                    "name": PUBLISH_ARTIFACT_TOOL,
                    "arguments": arguments,
                    # The service's connector-neutral parser validates, emits,
                    # deduplicates, and strips this transport envelope.
                    "output": f"{ARTIFACT_OPEN}{payload}{ARTIFACT_CLOSE}",
                },
            ),
            {
                "type": "function_call_output",
                "call_id": call_id,
                "output": result,
            },
        )

    def _record_state(
        self,
        request: CreateRunRequest,
        response_id: str,
        transcript: tuple[Message, ...],
        output_text: str,
    ) -> None:
        completed_transcript = transcript + (
            Message(role="assistant", content=strip_artifact_envelopes(output_text)),
        )
        state = _ThreadState(
            previous_response_id=response_id,
            transcript=completed_transcript,
            transcript_digest=transcript_digest(completed_transcript),
            transcript_chars=sum(
                len(message.content) for message in completed_transcript
            ),
        )
        with self._lock:
            previous = self._thread_state.pop(request.thread_id, None)
            if previous is not None:
                self._thread_state_chars -= previous.transcript_chars
            if state.transcript_chars > self._config.max_thread_state_chars:
                return
            self._thread_state[request.thread_id] = state
            self._thread_state_chars += state.transcript_chars
            while (
                len(self._thread_state) > self._config.max_runs
                or self._thread_state_chars > self._config.max_thread_state_chars
            ):
                _, evicted = self._thread_state.popitem(last=False)
                self._thread_state_chars -= evicted.transcript_chars

    def run(
        self,
        request: CreateRunRequest,
        *,
        run_id: str,
        cancel_event: Event,
    ) -> Iterator[ConnectorEvent]:
        upstream_request, transcript = self._http_request(request)
        response_id: str | None = None
        output_parts: list[str] = []
        retained_output_chars = sum(len(message.content) for message in transcript)
        retain_thread_state = (
            retained_output_chars <= self._config.max_thread_state_chars
        )
        tool_names: dict[str, str] = {}
        tool_arguments: dict[str, str] = {}
        canonical_tool_ids: dict[str, str] = {}
        client_tool_rounds = 0

        def retain_output(delta: str) -> None:
            nonlocal retained_output_chars, retain_thread_state
            if not retain_thread_state:
                return
            retained_output_chars += len(delta)
            if retained_output_chars > self._config.max_thread_state_chars:
                output_parts.clear()
                retain_thread_state = False
                return
            output_parts.append(delta)

        while True:
            completed = False
            round_response_id: str | None = None
            client_tool_outputs: list[dict[str, object]] = []
            try:
                response = urllib.request.urlopen(
                    upstream_request,
                    timeout=self._config.request_timeout_seconds,
                )
            except urllib.error.HTTPError as error:
                body = error.read(64_000).decode("utf-8", errors="replace")
                try:
                    parsed_error = strict_json_loads(body)
                except ValueError:
                    parsed_error = {}
                message = self._error_message(
                    parsed_error, f"backend returned HTTP {error.code}"
                )
                raise ConnectorError(
                    message, code="backend_http_error", retryable=error.code >= 500
                ) from error
            except urllib.error.URLError as error:
                raise ConnectorError(
                    "backend is unreachable",
                    code="backend_unreachable",
                    retryable=True,
                ) from error

            with response:
                with self._lock:
                    self._active_responses[run_id] = response
                try:
                    content_type = response.headers.get("Content-Type", "")
                    if "text/event-stream" not in content_type.lower():
                        body = response.read(5_000_000)
                        try:
                            payload = strict_json_loads(body)
                        except ValueError as error:
                            raise ConnectorError(
                                "backend returned a non-SSE response",
                                code="invalid_backend_response",
                            ) from error
                        round_response_id = self._response_id(payload)
                        output = (
                            payload.get("output_text")
                            if isinstance(payload, dict)
                            else None
                        )
                        if isinstance(output, str) and output:
                            retain_output(output)
                            yield ConnectorEvent("message.delta", {"delta": output})
                        completed = True
                    else:
                        for frame in iter_sse(response):
                            if cancel_event.is_set():
                                return
                            if frame.data.strip() == "[DONE]":
                                break
                            try:
                                payload = strict_json_loads(frame.data)
                            except ValueError as error:
                                raise ConnectorError(
                                    "backend emitted invalid SSE JSON",
                                    code="invalid_backend_event",
                                ) from error
                            event_type = frame.event
                            if (
                                not event_type
                                and isinstance(payload, dict)
                                and isinstance(payload.get("type"), str)
                            ):
                                event_type = payload["type"]
                            event_type = event_type or ""

                            round_response_id = (
                                self._response_id(payload) or round_response_id
                            )
                            if event_type == "response.output_text.delta":
                                delta = (
                                    payload.get("delta")
                                    if isinstance(payload, dict)
                                    else None
                                )
                                if isinstance(delta, str) and delta:
                                    retain_output(delta)
                                    yield ConnectorEvent(
                                        "message.delta", {"delta": delta}
                                    )
                            elif event_type == "response.output_item.added":
                                item = self._function_call(payload)
                                if item is not None:
                                    item_id = str(
                                        item.get("id") or item.get("call_id") or ""
                                    )
                                    call_id = str(item.get("call_id") or item_id)
                                    name = str(item.get("name") or "tool")
                                    for identifier in {item_id, call_id} - {""}:
                                        tool_names[identifier] = name
                                        canonical_tool_ids[identifier] = call_id
                                    tool_arguments.setdefault(call_id, "")
                                    yield ConnectorEvent(
                                        "tool.started",
                                        {"tool_call_id": call_id, "name": name},
                                    )
                            elif event_type == "response.function_call_arguments.delta":
                                if isinstance(payload, dict):
                                    identifier = str(
                                        payload.get("item_id")
                                        or payload.get("call_id")
                                        or ""
                                    )
                                    delta = payload.get("delta")
                                    if identifier and isinstance(delta, str):
                                        call_id = canonical_tool_ids.get(
                                            identifier, identifier
                                        )
                                        tool_arguments[call_id] = (
                                            tool_arguments.get(call_id, "") + delta
                                        )
                                        yield ConnectorEvent(
                                            "tool.arguments.delta",
                                            {
                                                "tool_call_id": call_id,
                                                "name": tool_names.get(
                                                    identifier,
                                                    tool_names.get(call_id, "tool"),
                                                ),
                                                "delta": delta,
                                            },
                                        )
                            elif event_type == "response.output_item.done":
                                item = self._function_call(payload)
                                if item is not None:
                                    item_id = str(
                                        item.get("id") or item.get("call_id") or ""
                                    )
                                    call_id = str(item.get("call_id") or item_id)
                                    arguments = item.get("arguments")
                                    if not isinstance(arguments, str):
                                        arguments = tool_arguments.get(call_id, "")
                                    status = str(item.get("status") or "")
                                    name = str(
                                        item.get("name")
                                        or tool_names.get(item_id, "tool")
                                    )
                                    if name == PUBLISH_ARTIFACT_TOOL:
                                        yield ConnectorEvent(
                                            "tool.requested",
                                            {
                                                "tool_call_id": call_id,
                                                "name": name,
                                                "arguments": arguments,
                                            },
                                        )
                                        event, function_output = (
                                            self._publish_artifact_result(
                                                call_id, arguments
                                            )
                                        )
                                        yield event
                                        client_tool_outputs.append(function_output)
                                    else:
                                        yield ConnectorEvent(
                                            "tool.completed"
                                            if status == "completed"
                                            else "tool.requested",
                                            {
                                                "tool_call_id": call_id,
                                                "name": name,
                                                "arguments": arguments,
                                            },
                                        )
                                output_item = self._function_call_output(payload)
                                if output_item is not None:
                                    call_id = str(
                                        output_item.get("call_id")
                                        or output_item.get("id")
                                        or "tool"
                                    )
                                    yield ConnectorEvent(
                                        "tool.completed",
                                        {
                                            "tool_call_id": call_id,
                                            "name": tool_names.get(call_id, "tool"),
                                            "output": output_item.get("output"),
                                        },
                                    )
                            elif event_type == "response.completed":
                                completed = True
                            elif event_type in {"response.failed", "error"}:
                                message = self._error_message(
                                    payload, "backend run failed"
                                )
                                raise ConnectorError(message, code="backend_run_failed")
                except (OSError, ValueError) as error:
                    if cancel_event.is_set():
                        return
                    raise ConnectorError(
                        "backend stream ended unexpectedly",
                        code="backend_stream_error",
                        retryable=True,
                    ) from error
                finally:
                    with self._lock:
                        self._active_responses.pop(run_id, None)

            if cancel_event.is_set():
                return
            if not completed:
                raise ConnectorError(
                    "backend stream ended before response.completed",
                    code="incomplete_backend_stream",
                    retryable=True,
                )
            response_id = round_response_id or response_id
            if not client_tool_outputs:
                break
            if not round_response_id:
                raise ConnectorError(
                    "backend requested a client tool without a response id",
                    code="invalid_backend_response",
                )
            client_tool_rounds += 1
            if client_tool_rounds > MAX_CLIENT_TOOL_ROUNDS:
                raise ConnectorError(
                    "backend exceeded the VSS UI publisher tool round limit",
                    code="client_tool_round_limit",
                )
            payload = self._client_tool_payload(
                request,
                round_response_id,
                client_tool_outputs,
            )
            upstream_request = self._encoded_request(
                payload,
                thread_id=request.thread_id,
            )

        if response_id and retain_thread_state:
            self._record_state(request, response_id, transcript, "".join(output_parts))

    def cancel(self, run_id: str) -> None:
        with self._lock:
            response = self._active_responses.get(run_id)
        close = getattr(response, "close", None)
        if callable(close):
            close()
