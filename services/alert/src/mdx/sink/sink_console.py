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

"""Console sink for the Alert event bridge.

Development and debugging aid selected with ``event_bridge.sinkType: console``.
Requires no broker, which makes it the quickest way to inspect what the bridge
would publish. Not intended for production: output is not durable and nothing
downstream can consume it.
"""

import json
import logging
from typing import Any, Callable, List, Optional

from mdx.sink.console_render import parse_redact_paths, redact
from mdx.sink.sink_base import SinkBase
from mdx.stream_message import StreamMessage


class ConsoleSink(SinkBase):
    """Sink that renders messages to the log instead of a message broker."""

    def __init__(self, config: dict):
        super().__init__(config)
        self.logger = logging.getLogger(self.__class__.__name__)

        section = (config.get('event_bridge') or {}).get('console_sink') or {}
        self.pretty = bool(section.get('pretty', True))
        self.max_chars = int(section.get('max_chars', 0))
        self.redact_paths = parse_redact_paths(section.get('redact'))

        self.logger.warning(
            "Console sink selected: intended for local development. Event bridge "
            "output is logged only, is not durable, and payloads are written to "
            "the log in full, so whoever can read these logs can read the "
            "payload contents%s",
            f". Masking {self.redact_paths}" if self.redact_paths else
            ". Redaction is off: set event_bridge.console_sink.redact to mask fields",
        )

    def _render(self, payload: Any) -> str:
        try:
            if isinstance(payload, (bytes, bytearray)):
                payload = payload.decode('utf-8', errors='replace')
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except ValueError:
                    # Not JSON, so there are no field paths to mask. Truncation
                    # is the only protection left for an opaque payload.
                    return self._truncate(payload)
            payload = redact(payload, self.redact_paths)
            text = json.dumps(payload, indent=2 if self.pretty else None, default=str)
        except Exception as exc:
            text = f"<unrenderable payload: {exc}>"
        return self._truncate(text)

    def _truncate(self, text: str) -> str:
        if self.max_chars and len(text) > self.max_chars:
            return f"{text[: self.max_chars]}... [truncated {len(text) - self.max_chars} chars]"
        return text

    def _emit(self, label: str, identifier: Any, payload: Any) -> None:
        self.logger.info("[console-sink] %s %s\n%s", label, identifier, self._render(payload))

    def write(self, messages: List[StreamMessage]) -> None:
        for message in messages or []:
            self._emit("anomaly", message.id, message.data)

    def write_msg(self, messages: List[bytes]) -> None:
        for index, payload in enumerate(messages or []):
            self._emit("raw", index, payload)

    def write_incidents(self, messages: List[StreamMessage]) -> None:
        for message in messages or []:
            self._emit("incident", message.id, message.data)

    def write_data(self, data: List[dict], message_transform_func: Optional[Callable] = None) -> None:
        for item in data or []:
            self._emit("anomaly", item.get('id'), item)

    def write_incident_data(self, data: List[dict], message_transform_func: Optional[Callable] = None) -> None:
        for item in data or []:
            self._emit("incident", item.get('id'), item)

    def close(self) -> None:
        """No resources to release."""
        return None
