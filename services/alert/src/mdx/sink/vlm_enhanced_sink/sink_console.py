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

"""Console sink for VLM-enhanced Alert and Incident results.

Selected with ``vlm_enhanced_sink.type: console``. Needs neither a broker nor
Elasticsearch, so it is the fastest way to see verdicts while developing
locally. Output is not durable and nothing downstream can consume it.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from .sink_base import VLMEnhancedSink


class VLMEnhancedConsoleSink(VLMEnhancedSink):
    """Renders VLM-verified events to the log instead of a datastore."""

    def __init__(
        self,
        pretty: bool = True,
        max_chars: int = 0,
        category_mapping: Optional[Dict[str, str]] = None,
        alert_config_store: Any = None,
    ) -> None:
        super().__init__(
            alert_config_store=alert_config_store,
            category_mapping=category_mapping,
        )
        self._pretty = pretty
        self._max_chars = max_chars
        self._logger.warning(
            "Console VLM enhanced sink selected: verdicts are logged only and are not persisted"
        )

    @classmethod
    def from_config(
        cls,
        config: Dict[str, Any],
        category_mapping: Optional[Dict[str, str]] = None,
        alert_config_store: Any = None,
    ) -> "VLMEnhancedConsoleSink":
        sink_root = config.get("vlm_enhanced_sink", {}) or {}
        console_cfg = sink_root.get("console") or {}
        return cls(
            pretty=bool(console_cfg.get("pretty", True)),
            max_chars=int(console_cfg.get("max_chars", 0)),
            category_mapping=category_mapping,
            alert_config_store=alert_config_store,
        )

    def _store_success(
        self,
        event_kind: str,
        document: Dict[str, Any],
        raw_vlm_response: Any,
        user_prompt: str,
    ) -> None:
        self._emit(event_kind, "verdict", document)

    def _store_error(
        self,
        event_kind: str,
        document: Dict[str, Any],
        error_payload: Dict[str, Any],
    ) -> None:
        self._emit(event_kind, "error", document)

    def _emit(self, event_kind: str, outcome: str, document: Dict[str, Any]) -> None:
        if 'category' in document:
            original_category = document['category']
            resolved = self._resolve_output_category(original_category)
            if resolved and resolved != original_category:
                document['category'] = resolved

        self._logger.info(
            "[console-sink] vlm-enhanced %s %s id=%s\n%s",
            event_kind,
            outcome,
            document.get("id"),
            self._render(document),
        )

    def _render(self, document: Dict[str, Any]) -> str:
        try:
            text = json.dumps(document, indent=2 if self._pretty else None, default=str)
        except Exception as exc:
            return f"<unrenderable document: {exc}>"
        if self._max_chars and len(text) > self._max_chars:
            return f"{text[: self._max_chars]}... [truncated {len(text) - self._max_chars} chars]"
        return text
