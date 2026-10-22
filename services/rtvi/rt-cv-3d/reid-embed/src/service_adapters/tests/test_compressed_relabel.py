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

"""Tests for :mod:`compressed_relabel`.

Uses a fake ``Collection`` double that mirrors the subset of
the pymilvus ``Collection`` API ``relabel_compressed_clusters``
depends on: ``query`` and ``upsert(..., partial_update=True)``.
"""

from __future__ import annotations

import logging
import re
from typing import Dict, List, Optional

import pytest

from service_adapters.compressed_relabel import relabel_compressed_clusters


class FakeCollection:
    """Minimal in-memory stand-in for pymilvus.Collection."""

    def __init__(self, rows: Optional[List[dict]] = None) -> None:
        self.rows: Dict[int, dict] = {}
        for r in rows or []:
            self.rows[int(r["entry_id"])] = dict(r)
        self.upsert_calls: List[dict] = []
        self.fail_next_upsert: Optional[Exception] = None

    def query(self, expr: str, output_fields=None):
        m = re.match(r"^object_id\s*==\s*'([^']*)'$", expr.strip())
        if not m:
            raise ValueError(f"FakeCollection.query unsupported expr: {expr!r}")
        target = m.group(1)
        fields = output_fields or ["entry_id", "object_id"]
        return [{k: r[k] for k in fields if k in r} for r in self.rows.values() if str(r["object_id"]) == target]

    def upsert(self, data, partial_update: bool = False):
        if self.fail_next_upsert is not None:
            err = self.fail_next_upsert
            self.fail_next_upsert = None
            raise err
        # Milvus 2.5.x doesn't honor partial_update server-side; production
        # sends full rows, so the fake mirrors that contract.
        self.upsert_calls.append(
            {
                "n": len(data),
                "rows": [dict(r) for r in data],
                "partial_update": partial_update,
            }
        )
        for row in data:
            eid = int(row["entry_id"])
            existing = self.rows.setdefault(eid, {})
            for k, v in row.items():
                existing[k] = v


def _row(entry_id: int, object_id: str, **kw) -> dict:
    return {
        "entry_id": entry_id,
        "object_id": object_id,
        "sensor_id": kw.get("sensor_id", "cam1"),
        "frame_id": kw.get("frame_id", 100 + entry_id),
        "weight": kw.get("weight", 1),
        "vector": kw.get("vector", [0.0, 1.0, 0.0]),
        "mean": kw.get("mean", [0.0, 1.0, 0.0]),
    }


# Test-friendly map shape: retired_rep -> surviving_rep (both digit-strings).
# Mirrors what main.py builds from cluster_manager.archive.


class TestRelabelCompressedClusters:
    def test_empty_map_is_noop(self) -> None:
        coll = FakeCollection([_row(1, "100")])
        n = relabel_compressed_clusters(coll, {}, batch_size=100)
        assert n == 0
        assert coll.upsert_calls == []

    def test_simple_merge_rewrites_rows(self) -> None:
        # Cluster with rep "700" merges into cluster "300"
        coll = FakeCollection(
            [
                _row(10, "700"),
                _row(11, "700"),
                _row(12, "300"),
            ]
        )
        n = relabel_compressed_clusters(coll, {"700": "300"}, batch_size=100)
        assert n == 2
        assert coll.rows[10]["object_id"] == "300"
        assert coll.rows[11]["object_id"] == "300"
        assert coll.rows[12]["object_id"] == "300"
        # Vector, weight and cluster moments on rewritten rows preserved
        # through the full-row upsert (the caller re-supplies them from the
        # prev query).
        assert coll.rows[10]["weight"] == 1
        assert coll.rows[10]["vector"] == [0.0, 1.0, 0.0]
        assert coll.rows[10]["mean"] == [0.0, 1.0, 0.0]

    def test_retired_key_with_no_rows_is_skipped(self) -> None:
        coll = FakeCollection([_row(1, "100")])
        n = relabel_compressed_clusters(coll, {"999": "100"}, batch_size=100)
        assert n == 0
        assert coll.upsert_calls == []

    def test_retired_equals_surviving_is_skipped(self) -> None:
        """Edge case: retired and surviving share the same rep (e.g. one
        cluster's rep was already the lower-min-frame member). Nothing
        to relabel; query is skipped entirely.
        """
        coll = FakeCollection([_row(1, "100")])
        n = relabel_compressed_clusters(coll, {"100": "100"}, batch_size=100)
        assert n == 0
        assert coll.upsert_calls == []

    def test_payload_carries_full_row(self) -> None:
        """Wire payload: every upsert row carries the full schema
        because Milvus 2.5.x rejects partial upserts. The caller fetches
        the unchanged columns from the prev query so a round-trip
        suffices.
        """
        coll = FakeCollection([_row(10, "700"), _row(11, "700")])
        relabel_compressed_clusters(coll, {"700": "300"}, batch_size=100)
        assert len(coll.upsert_calls) == 1
        assert coll.upsert_calls[0]["partial_update"] is False
        for row in coll.upsert_calls[0]["rows"]:
            assert set(row.keys()) == {
                "entry_id",
                "object_id",
                "sensor_id",
                "frame_id",
                "weight",
                "vector",
                "mean",
            }
            assert row["object_id"] == "300"

    def test_batch_size_chunks_upsert(self) -> None:
        coll = FakeCollection([_row(eid, "700") for eid in range(10, 15)])
        relabel_compressed_clusters(coll, {"700": "300"}, batch_size=2)
        # 5 rows / batch_size 2 -> 3 upsert calls (2, 2, 1).
        assert [c["n"] for c in coll.upsert_calls] == [2, 2, 1]

    def test_upsert_failure_skips_to_next_key(self, caplog: pytest.LogCaptureFixture) -> None:
        """An upsert failure for one retired key is logged and the
        function moves on to the next.
        """
        coll = FakeCollection([_row(10, "700"), _row(20, "800")])
        orig_upsert = coll.upsert

        def flaky_upsert(data, partial_update: bool = False):
            # Fail when relabeling toward "300", succeed toward "400".
            target = data[0]["object_id"]
            if target == "300":
                raise RuntimeError("milvus flake on upsert")
            orig_upsert(data, partial_update=partial_update)

        coll.upsert = flaky_upsert  # type: ignore[assignment]

        logger = logging.getLogger("test.compressed_relabel.upsert_fail")
        with caplog.at_level(logging.ERROR, logger=logger.name):
            n = relabel_compressed_clusters(
                coll,
                {"700": "300", "800": "400"},
                batch_size=100,
                logger=logger,
            )

        # "800" -> "400" succeeded; "700" -> "300" failed and was skipped.
        assert n == 1
        assert coll.rows[10]["object_id"] == "700"  # unchanged
        assert coll.rows[20]["object_id"] == "400"  # relabeled
        assert any("upsert failed" in r.getMessage() for r in caplog.records)

    def test_query_failure_skips_to_next_key(self, caplog: pytest.LogCaptureFixture) -> None:
        coll = FakeCollection([_row(20, "800")])
        orig_query = coll.query

        def flaky_query(expr, output_fields=None):
            if "700" in expr:
                raise RuntimeError("query flake")
            return orig_query(expr, output_fields=output_fields)

        coll.query = flaky_query  # type: ignore[assignment]
        logger = logging.getLogger("test.compressed_relabel.query_fail")
        with caplog.at_level(logging.ERROR, logger=logger.name):
            n = relabel_compressed_clusters(
                coll,
                {"700": "300", "800": "400"},
                batch_size=100,
                logger=logger,
            )
        assert n == 1
        assert coll.rows[20]["object_id"] == "400"
        assert any("failed to query compressed" in r.getMessage() for r in caplog.records)
