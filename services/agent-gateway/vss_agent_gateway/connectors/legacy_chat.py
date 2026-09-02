# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Compatibility connector for the existing VSS OpenAI-shaped chat stream."""

from __future__ import annotations

import hashlib
import json
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from threading import Event

from ..config import GatewayConfig
from ..contract import ConnectorEvent, CreateRunRequest
from .base import Connector, ConnectorError


class LegacyChatConnector(Connector):
    def __init__(self, config: GatewayConfig) -> None:
        self._config = config
        self._endpoint = f"{config.backend_url}{config.backend_path}"
        self._lock = threading.RLock()
        self._active_responses: dict[str, object] = {}

    @property
    def protocol(self) -> str:
        return "legacy-chat"

    @staticmethod
    def _session_key(thread_id: str) -> str:
        return f"vss-ui-{hashlib.sha256(thread_id.encode()).hexdigest()[:40]}"

    @staticmethod
    def _step_event(payload: object) -> ConnectorEvent | None:
        if not isinstance(payload, dict):
            return None
        status = str(payload.get("status") or "in_progress").lower()
        data: dict[str, object] = {
            "tool_call_id": str(payload.get("id") or "tool"),
            "name": str(payload.get("name") or "Agent step"),
            "payload": payload.get("payload"),
        }
        if status in {"complete", "completed", "success", "succeeded"}:
            return ConnectorEvent("tool.completed", data)
        if status in {"error", "failed", "failure"}:
            data["error"] = payload.get("error")
            return ConnectorEvent("tool.failed", data)
        return ConnectorEvent("tool.started", data)

    @staticmethod
    def _content(payload: object) -> str | None:
        if not isinstance(payload, dict):
            return None
        choices = payload.get("choices")
        if (
            not isinstance(choices, list)
            or not choices
            or not isinstance(choices[0], dict)
        ):
            return None
        choice = choices[0]
        delta = choice.get("delta")
        message = choice.get("message")
        if isinstance(delta, dict) and isinstance(delta.get("content"), str):
            return delta["content"]
        if isinstance(message, dict) and isinstance(message.get("content"), str):
            return message["content"]
        return None

    def run(
        self,
        request: CreateRunRequest,
        *,
        run_id: str,
        cancel_event: Event,
    ) -> Iterator[ConnectorEvent]:
        messages = request.full_transcript()
        payload = {
            "model": self._config.backend_model,
            "messages": [message.to_chat_message() for message in messages],
            "stream": True,
        }
        headers = {
            "Accept": "text/event-stream",
            "Content-Type": "application/json",
            "Conversation-Id": self._session_key(request.thread_id),
            "User-Agent": "vss-agent-gateway/1.0",
            **self._config.backend_headers,
        }
        if self._config.backend_token:
            headers["Authorization"] = f"Bearer {self._config.backend_token}"
        upstream_request = urllib.request.Request(
            self._endpoint,
            data=json.dumps(
                payload, ensure_ascii=False, separators=(",", ":")
            ).encode(),
            headers=headers,
            method="POST",
        )

        try:
            response = urllib.request.urlopen(
                upstream_request,
                timeout=self._config.request_timeout_seconds,
            )
        except urllib.error.HTTPError as error:
            error.read(64_000)
            raise ConnectorError(
                f"backend returned HTTP {error.code}",
                code="backend_http_error",
                retryable=error.code >= 500,
            ) from error
        except urllib.error.URLError as error:
            raise ConnectorError(
                "backend is unreachable", code="backend_unreachable", retryable=True
            ) from error

        done = False
        with response:
            with self._lock:
                self._active_responses[run_id] = response
            try:
                for raw_line in response:
                    if cancel_event.is_set():
                        return
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not line:
                        continue
                    if line.startswith("intermediate_data:"):
                        try:
                            intermediate = json.loads(line.partition(":")[2].strip())
                        except json.JSONDecodeError:
                            continue
                        event = self._step_event(intermediate)
                        if event:
                            yield event
                        continue
                    if not line.startswith("data:"):
                        continue
                    data = line.partition(":")[2].strip()
                    if data == "[DONE]":
                        done = True
                        break
                    try:
                        parsed = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    content = self._content(parsed)
                    if content:
                        yield ConnectorEvent("message.delta", {"delta": content})
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

        if not done and not cancel_event.is_set():
            raise ConnectorError(
                "backend stream ended before [DONE]",
                code="incomplete_backend_stream",
                retryable=True,
            )

    def cancel(self, run_id: str) -> None:
        with self._lock:
            response = self._active_responses.get(run_id)
        close = getattr(response, "close", None)
        if callable(close):
            close()
