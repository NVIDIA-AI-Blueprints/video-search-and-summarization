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

"""Tests for :mod:`service_adapters.cluster_service`.

Uses an in-memory ``FakeCollection`` that mirrors the subset of
``pymilvus.Collection`` the helpers depend on (``delete``, ``upsert``,
``query_iterator``) so scenarios run without Milvus.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import numpy as np
import pytest

from cluster_manager.types import Archive, EmbeddingRecord
from service_adapters.cluster_service import (
    delete_filtered_from_main,
    relabel_main_clusters,
    relabel_retired_main_clusters,
    write_db_snapshots,
)


# ---------------------------------------------------------------------------
# Fake Milvus Collection
# ---------------------------------------------------------------------------


class _FakeIterator:
    def __init__(self, rows: List[dict], batch_size: int) -> None:
        self._rows = rows
        self._batch_size = batch_size
        self._closed = False
        self._i = 0

    def next(self) -> List[dict]:
        if self._closed or self._i >= len(self._rows):
            return []
        out = self._rows[self._i : self._i + self._batch_size]
        self._i += len(out)
        return out

    def close(self) -> None:
        self._closed = True


class FakeCollection:
    def __init__(self, rows: Optional[List[dict]] = None) -> None:
        self.rows: Dict[int, dict] = {}
        for r in rows or []:
            self.rows[int(r["entry_id"])] = dict(r)
        self.delete_calls: List[str] = []
        self.upsert_calls: List[dict] = []

    def query_iterator(self, batch_size: int, expr: str, output_fields):
        filtered = []
        for row in self.rows.values():
            filtered.append({k: row[k] for k in output_fields if k in row})
        return _FakeIterator(filtered, batch_size)

    def query(self, expr: str, output_fields=None):
        m = re.match(r"^object_id\s*==\s*'([^']*)'$", expr.strip())
        if not m:
            raise ValueError(f"FakeCollection.query unsupported expr: {expr!r}")
        target = m.group(1)
        fields = output_fields or ["entry_id", "object_id"]
        return [{k: r[k] for k in fields if k in r} for r in self.rows.values() if str(r.get("object_id")) == target]

    def delete(self, expr: str) -> None:
        self.delete_calls.append(expr)
        m = re.match(r"^entry_id\s+in\s+\[(.*)\]$", expr.strip())
        if not m:
            raise ValueError(f"unsupported delete expr: {expr!r}")
        for eid in (int(x) for x in re.findall(r"-?\d+", m.group(1))):
            self.rows.pop(eid, None)

    def upsert(self, data, partial_update: bool = False) -> None:
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


def _rec(entry_id: int, obj_id: int, sensor: str, frame: int) -> EmbeddingRecord:
    return EmbeddingRecord(
        entry_id=entry_id,
        object_id=obj_id,
        sensor_id=sensor,
        frame_id=frame,
        vector=np.zeros(4, dtype=np.float32),
    )


# ---------------------------------------------------------------------------
# relabel_main_clusters
# ---------------------------------------------------------------------------


def _archive_with_reps(reps: Dict[int, int]) -> Archive:
    """Build a real Archive with the given cluster_id -> rep_object_id mapping."""
    arch = Archive()
    for cid, rep in reps.items():
        arch.rep_object_id[cid] = rep
    return arch


class TestRelabelMainClusters:
    def test_empty_map_is_noop(self) -> None:
        coll = FakeCollection([_row_dict(10, "8")])
        archive = _archive_with_reps({})
        n = relabel_main_clusters(
            coll,
            [_rec(10, 8, "cam1", 100)],
            {},
            archive,
            batch_size=100,
        )
        assert n == 0
        assert coll.upsert_calls == []

    def test_single_cluster_rewrite(self) -> None:
        # cluster 5 has rep tracker_id 555; relabel writes "555" + is_clustered=True
        coll = FakeCollection([_row_dict(10, "8"), _row_dict(11, "8")])
        embs = [_rec(10, 8, "cam1", 100), _rec(11, 8, "cam1", 101)]
        archive = _archive_with_reps({5: 555})
        n = relabel_main_clusters(
            coll,
            embs,
            {10: 5, 11: 5},
            archive,
            batch_size=100,
        )
        assert n == 2
        assert coll.rows[10]["object_id"] == "555"
        assert coll.rows[11]["object_id"] == "555"
        assert coll.rows[10]["is_clustered"] is True
        assert coll.rows[11]["is_clustered"] is True
        # Partial upsert leaves untouched columns alone: the vector on
        # the row stays as whatever was there before relabel, not
        # overwritten from the embeddings list.
        assert coll.rows[10]["vector"] == [0.0, 1.0, 0.0]

    def test_payload_is_partial_update(self) -> None:
        """Wire payload: each upsert row carries only the three fields
        that change (entry_id PK + object_id + is_clustered), with
        partial_update=True. Milvus >= 2.6.2 merges them into the
        existing row without disturbing sensor_id/frame_id/vector.
        """
        coll = FakeCollection([_row_dict(10, "8"), _row_dict(11, "8")])
        embs = [_rec(10, 8, "cam1", 100), _rec(11, 8, "cam1", 101)]
        archive = _archive_with_reps({5: 555})
        relabel_main_clusters(coll, embs, {10: 5, 11: 5}, archive, batch_size=100)
        assert len(coll.upsert_calls) == 1
        assert coll.upsert_calls[0]["partial_update"] is True
        for row in coll.upsert_calls[0]["rows"]:
            assert set(row.keys()) == {
                "entry_id",
                "object_id",
                "is_clustered",
            }
            assert row["object_id"] == "555"
            assert row["is_clustered"] is True

    def test_entry_id_preserved(self) -> None:
        coll = FakeCollection([_row_dict(42, "8")])
        archive = _archive_with_reps({3: 333})
        n = relabel_main_clusters(
            coll,
            [_rec(42, 8, "cam1", 100)],
            {42: 3},
            archive,
            batch_size=100,
        )
        assert n == 1
        assert 42 in coll.rows
        assert coll.rows[42]["object_id"] == "333"
        assert coll.rows[42]["is_clustered"] is True

    def test_entry_id_not_in_embeddings_skipped(self) -> None:
        coll = FakeCollection([_row_dict(10, "8")])
        archive = _archive_with_reps({5: 555, 7: 777})
        n = relabel_main_clusters(
            coll,
            [_rec(10, 8, "cam1", 100)],
            {10: 5, 99: 7},  # 99 has no embedding
            archive,
            batch_size=100,
        )
        assert n == 1
        assert coll.rows[10]["object_id"] == "555"

    def test_batching_respected(self) -> None:
        # 5 entries with batch_size=2 -> 3 upsert calls (2, 2, 1).
        embs = [_rec(i, 8, "cam1", i) for i in range(5)]
        coll = FakeCollection([_row_dict(i, "8") for i in range(5)])
        archive = _archive_with_reps({5: 555})
        n = relabel_main_clusters(
            coll,
            embs,
            {i: 5 for i in range(5)},
            archive,
            batch_size=2,
        )
        assert n == 5
        assert [c["n"] for c in coll.upsert_calls] == [2, 2, 1]


# ---------------------------------------------------------------------------
# delete_filtered_from_main
# ---------------------------------------------------------------------------


class TestDeleteFilteredFromMain:
    def test_empty_is_noop(self) -> None:
        coll = FakeCollection([_row_dict(10, "8")])
        n = delete_filtered_from_main(coll, [], batch_size=100)
        assert n == 0
        assert coll.delete_calls == []
        assert 10 in coll.rows

    def test_deletes_requested_ids(self) -> None:
        coll = FakeCollection(
            [
                _row_dict(10, "8"),
                _row_dict(11, "8"),
                _row_dict(12, "9"),
            ]
        )
        n = delete_filtered_from_main(coll, [10, 12], batch_size=100)
        assert n == 2
        assert 10 not in coll.rows
        assert 12 not in coll.rows
        assert 11 in coll.rows  # untouched

    def test_batching(self) -> None:
        coll = FakeCollection([_row_dict(i, "8") for i in range(6)])
        n = delete_filtered_from_main(coll, list(range(6)), batch_size=2)
        assert n == 6
        assert len(coll.delete_calls) == 3
        assert coll.rows == {}


# ---------------------------------------------------------------------------
# relabel_retired_main_clusters
# ---------------------------------------------------------------------------


def _clustered_row(entry_id: int, object_id: str, **kw: Any) -> dict:
    """Main-collection row as it looks AFTER its batch's relabel pass:
    ``is_clustered=True`` and ``object_id`` set to the cluster rep."""
    return {
        "entry_id": entry_id,
        "object_id": object_id,
        "sensor_id": kw.get("sensor_id", "cam1"),
        "frame_id": kw.get("frame_id", 100 + entry_id),
        "is_clustered": True,
        "vector": kw.get("vector", [0.0, 1.0, 0.0]),
    }


class TestRelabelRetiredMainClusters:
    def test_empty_map_is_noop(self) -> None:
        coll = FakeCollection([_clustered_row(10, "700")])
        n = relabel_retired_main_clusters(coll, {}, batch_size=100)
        assert n == 0
        assert coll.upsert_calls == []
        assert coll.rows[10]["object_id"] == "700"

    def test_simple_merge_rewrites_rows(self) -> None:
        # Cluster with rep "700" merges into cluster "300". Prior-batch
        # rows under "700" must move to "300"; rows already under "300"
        # are untouched.
        coll = FakeCollection(
            [
                _clustered_row(10, "700"),
                _clustered_row(11, "700"),
                _clustered_row(12, "300"),
            ]
        )
        n = relabel_retired_main_clusters(
            coll,
            {"700": "300"},
            batch_size=100,
        )
        assert n == 2
        assert coll.rows[10]["object_id"] == "300"
        assert coll.rows[11]["object_id"] == "300"
        assert coll.rows[12]["object_id"] == "300"

    def test_is_clustered_left_alone(self) -> None:
        """Partial upsert should not flip ``is_clustered`` off — these
        rows were already clustered in a prior batch and stay clustered
        under the surviving rep."""
        coll = FakeCollection([_clustered_row(10, "700")])
        relabel_retired_main_clusters(
            coll,
            {"700": "300"},
            batch_size=100,
        )
        assert coll.rows[10]["is_clustered"] is True

    def test_full_row_payload(self) -> None:
        """Full-row upsert (no ``partial_update``) because Milvus 2.6.15
        rejects partial upserts against sealed segments where the
        retired prior-batch rows live. Caller must source every
        unchanged column from a prior query so a single round-trip per
        retired key suffices.
        """
        coll = FakeCollection([_clustered_row(10, "700"), _clustered_row(11, "700")])
        relabel_retired_main_clusters(
            coll,
            {"700": "300"},
            batch_size=100,
        )
        assert len(coll.upsert_calls) == 1
        assert coll.upsert_calls[0]["partial_update"] is False
        for row in coll.upsert_calls[0]["rows"]:
            assert set(row.keys()) == {
                "entry_id",
                "object_id",
                "sensor_id",
                "frame_id",
                "is_clustered",
                "vector",
            }
            assert row["object_id"] == "300"
            assert row["is_clustered"] is True

    def test_retired_key_with_no_rows_is_skipped(self) -> None:
        coll = FakeCollection([_clustered_row(1, "100")])
        n = relabel_retired_main_clusters(
            coll,
            {"999": "100"},
            batch_size=100,
        )
        assert n == 0
        assert coll.upsert_calls == []

    def test_retired_equals_surviving_is_skipped(self) -> None:
        coll = FakeCollection([_clustered_row(1, "100")])
        n = relabel_retired_main_clusters(
            coll,
            {"100": "100"},
            batch_size=100,
        )
        assert n == 0
        assert coll.upsert_calls == []

    def test_batch_size_chunks_upsert(self) -> None:
        coll = FakeCollection([_clustered_row(eid, "700") for eid in range(10, 15)])
        relabel_retired_main_clusters(
            coll,
            {"700": "300"},
            batch_size=2,
        )
        # 5 rows / batch_size 2 -> 3 upsert calls (2, 2, 1).
        assert [c["n"] for c in coll.upsert_calls] == [2, 2, 1]

    def test_multi_key_independent(self) -> None:
        coll = FakeCollection(
            [
                _clustered_row(10, "700"),
                _clustered_row(20, "800"),
            ]
        )
        n = relabel_retired_main_clusters(
            coll,
            {"700": "300", "800": "400"},
            batch_size=100,
        )
        assert n == 2
        assert coll.rows[10]["object_id"] == "300"
        assert coll.rows[20]["object_id"] == "400"

    def test_upsert_failure_skips_to_next_key(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Per-key try/except: a failure on one retired key is logged
        and the function moves on to the next."""
        coll = FakeCollection([_clustered_row(10, "700"), _clustered_row(20, "800")])
        orig_upsert = coll.upsert

        def flaky_upsert(data, partial_update: bool = False):
            target = data[0]["object_id"]
            if target == "300":
                raise RuntimeError("milvus flake on upsert")
            orig_upsert(data, partial_update=partial_update)

        coll.upsert = flaky_upsert  # type: ignore[assignment]

        logger = logging.getLogger("test.cluster_service.retired_upsert_fail")
        with caplog.at_level(logging.ERROR, logger=logger.name):
            n = relabel_retired_main_clusters(
                coll,
                {"700": "300", "800": "400"},
                batch_size=100,
                logger=logger,
            )

        assert n == 1
        assert coll.rows[10]["object_id"] == "700"  # unchanged
        assert coll.rows[20]["object_id"] == "400"  # relabeled
        assert any("upsert failed" in r.getMessage() for r in caplog.records)

    def test_query_failure_skips_to_next_key(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        coll = FakeCollection([_clustered_row(20, "800")])
        orig_query = coll.query

        def flaky_query(expr, output_fields=None):
            if "700" in expr:
                raise RuntimeError("query flake")
            return orig_query(expr, output_fields=output_fields)

        coll.query = flaky_query  # type: ignore[assignment]
        logger = logging.getLogger("test.cluster_service.retired_query_fail")
        with caplog.at_level(logging.ERROR, logger=logger.name):
            n = relabel_retired_main_clusters(
                coll,
                {"700": "300", "800": "400"},
                batch_size=100,
                logger=logger,
            )
        assert n == 1
        assert coll.rows[20]["object_id"] == "400"
        assert any("failed to query main" in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------------------
# write_db_snapshots
# ---------------------------------------------------------------------------


class TestWriteDbSnapshots:
    def test_main_only(self, tmp_path: Path) -> None:
        main = FakeCollection([_row_dict(10, "c3"), _row_dict(11, "c3")])
        paths = write_db_snapshots(
            batch_idx=0,
            output_dir=tmp_path,
            main_collection=main,
            sections=("main",),
            stage="post_clustering",
        )
        assert set(paths) == {"main"}
        out = Path(paths["main"])
        assert out.name == "main_db_post_clustering.json"
        assert out.parent.name == "batch_000"
        data = json.loads(out.read_text())
        assert {row["entry_id"] for row in data} == {10, 11}

    def test_compressed_section(self, tmp_path: Path) -> None:
        compressed = FakeCollection(
            [
                {
                    "entry_id": 1,
                    "object_id": "c3",
                    "sensor_id": "cam1",
                    "frame_id": 100,
                    "weight": 5,
                    "vector": [0.0, 0.0],
                },
            ]
        )
        paths = write_db_snapshots(
            batch_idx=0,
            output_dir=tmp_path,
            compressed_collection=compressed,
            sections=("compressed",),
        )
        data = json.loads(Path(paths["compressed"]).read_text())
        assert data[0]["weight"] == 5

    def test_tracking_section(self, tmp_path: Path) -> None:
        db = {
            "c5": {
                "cam1": [
                    SimpleNamespace(
                        object_id="c5",
                        stream_id="cam1",
                        frame_id=100,
                        leftX=0.0,
                        topY=0.0,
                        rightX=1.0,
                        bottomY=1.0,
                    )
                ]
            }
        }
        paths = write_db_snapshots(
            batch_idx=0,
            output_dir=tmp_path,
            tracking_db=db,
            tracking_lock=threading.Lock(),
            sections=("tracking",),
        )
        data = json.loads(Path(paths["tracking"]).read_text())
        assert "c5" in data
        assert data["c5"]["cam1"][0]["frame_id"] == 100

    def test_missing_handle_skips_section(self, tmp_path: Path) -> None:
        # Request all three sections but only provide main_collection.
        main = FakeCollection([_row_dict(10, "c3")])
        paths = write_db_snapshots(
            batch_idx=0,
            output_dir=tmp_path,
            main_collection=main,
            sections=("main", "compressed", "tracking"),
        )
        assert set(paths) == {"main"}

    def test_stage_suffix(self, tmp_path: Path) -> None:
        main = FakeCollection()
        paths = write_db_snapshots(
            batch_idx=0,
            output_dir=tmp_path,
            main_collection=main,
            sections=("main",),
            stage="initial",
        )
        assert Path(paths["main"]).name == "main_db_initial.json"

    def test_no_stage_means_no_suffix(self, tmp_path: Path) -> None:
        main = FakeCollection()
        paths = write_db_snapshots(
            batch_idx=7,
            output_dir=tmp_path,
            main_collection=main,
            sections=("main",),
            stage="",
        )
        assert Path(paths["main"]).name == "main_db.json"
        assert Path(paths["main"]).parent.name == "batch_007"

    def test_query_failure_logs_and_skips(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        class BrokenCollection:
            def query_iterator(self, *a, **kw):
                raise RuntimeError("milvus flake")

        logger = logging.getLogger("test.cluster_service.snapshot_fail")
        with caplog.at_level(logging.ERROR, logger=logger.name):
            paths = write_db_snapshots(
                batch_idx=0,
                output_dir=tmp_path,
                main_collection=BrokenCollection(),
                sections=("main",),
                stage="initial",
                logger=logger,
            )
        assert paths == {}  # main section silently skipped
        assert any("main DB dump failed" in r.getMessage() for r in caplog.records)

    def test_iterator_closed_even_on_error(self, tmp_path: Path) -> None:
        class BadIter:
            def __init__(self) -> None:
                self.closed = False

            def next(self):
                raise RuntimeError("next blew up")

            def close(self):
                self.closed = True

        bad = BadIter()

        class Coll:
            def query_iterator(self, **kw):
                return bad

        paths = write_db_snapshots(
            batch_idx=0,
            output_dir=tmp_path,
            main_collection=Coll(),
            sections=("main",),
        )
        assert paths == {}
        assert bad.closed  # finally-clause in _query_iterator_to_list


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _row_dict(entry_id: int, object_id: str, **kw: Any) -> dict:
    return {
        "entry_id": entry_id,
        "object_id": object_id,
        "sensor_id": kw.get("sensor_id", "cam1"),
        "frame_id": kw.get("frame_id", 100 + entry_id),
        "vector": kw.get("vector", [0.0, 1.0, 0.0]),
    }
