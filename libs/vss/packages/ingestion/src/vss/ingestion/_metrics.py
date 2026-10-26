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

"""Bounded final chunk snapshots; no provider payload or source data retained."""

from __future__ import annotations

from typing import Any

from vss.observability import metric_scope, record
from vss.observability.context import current_scope, fail_current
from vss.observability.contracts import Availability, Status


class ChunkMetrics:
    def __init__(self, *, embedding: bool = False) -> None:
        self.scope = current_scope()
        self.embedding = embedding
        self.failed = False
        self._chunks: dict[
            str, tuple[int, bool, tuple[tuple[str, float | None, Availability], ...]]
        ] = {}

    def observe(self, key: str, index: int, payload: dict[str, Any]) -> None:
        if self.scope is None:
            return
        collector = self.scope.collector()
        if collector is None:
            return
        identity = collector.identity(key)
        if identity in self._chunks:
            # A cumulative update must preserve its first-seen provisional
            # index until successful final ordering explicitly reindexes it.
            index = self._chunks[identity][0]
        if len(self._chunks) >= 2048 and identity not in self._chunks:
            collector.invalidate()
            return
        entries = []
        for metric, field, divisor in (
            ("input_tokens", "input_tokens", 1),
            ("output_tokens", "output_tokens", 1),
            ("provider.queue", "queue_time_s", 1),
            ("provider.decode", "decode_latency_ms", 1000),
            (
                "provider.infer",
                "inference_latency_ms" if self.embedding else "vlm_latency_ms",
                1000,
            ),
            ("provider.chunk", "chunk_latency_ms", 1000),
            ("provider.frames", "frame_count", 1),
        ):
            raw = payload.get(field)
            availability: Availability = (
                "not_applicable"
                if self.embedding and metric in {"input_tokens", "output_tokens"}
                else "not_reported"
            )
            value = None
            if raw is not None and availability != "not_applicable":
                if type(raw) not in {int, float} or not 0 <= raw <= 2**53 - 1:
                    collector.invalidate()
                    availability = "collection_error"
                else:
                    value = raw / divisor
                    availability = "measured"
            entries.append((metric, value, availability))
        self._chunks[identity] = (
            index,
            payload.get("error") is not None,
            tuple(entries),
        )

    def finish(self, *, complete: bool, model: str | None) -> None:
        if self.scope is None:
            return
        status: Status = "ok" if complete else "error"
        if not self._chunks or any(failed for _, failed, _ in self._chunks.values()):
            status = "error"
            self.failed = True
        with metric_scope(model=model):
            for index, failed, entries in self._chunks.values():
                with metric_scope(chunk_idx=index):
                    chunk_status: Status = "error" if failed or not complete else "ok"
                    record(
                        "ingestion.chunk", 1, library="ingestion", status=chunk_status
                    )
                    record(
                        "ingestion.inference_retries",
                        None,
                        library="ingestion",
                        availability="unsupported",
                        status=chunk_status,
                    )
                    for metric, value, availability in entries:
                        record(
                            "ingestion." + metric,
                            value,
                            library="ingestion",
                            origin="provider",
                            availability=availability,
                            status=chunk_status,
                        )
            record(
                "ingestion.chunks",
                len(self._chunks),
                library="ingestion",
                status=status,
            )
            for metric in ("input_tokens", "output_tokens"):
                values = [
                    value
                    for _, _, entries in self._chunks.values()
                    for name, value, availability in entries
                    if name == metric and availability == "measured"
                ]
                known = len(values) == len(self._chunks) and bool(values)
                record(
                    "ingestion.total_" + metric,
                    sum(v for v in values if v is not None) if known else None,
                    library="ingestion",
                    origin="provider",
                    status=status,
                    availability="measured"
                    if known
                    else "not_applicable"
                    if self.embedding
                    else "not_reported",
                )
        if self.failed:
            fail_current()

    def reindex(self, key: str, index: int) -> None:
        collector = self.scope.collector() if self.scope else None
        if collector is not None:
            identity = collector.identity(key)
            if identity in self._chunks:
                _, failed, entries = self._chunks[identity]
                self._chunks[identity] = (index, failed, entries)
