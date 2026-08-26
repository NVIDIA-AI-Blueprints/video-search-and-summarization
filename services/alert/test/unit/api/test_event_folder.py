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

"""Unit tests for the realtime event folder and its durable store.

Uses a small stateful Elasticsearch double rather than a mock: the properties
worth testing here — that a second fold changes nothing, that a boundary shift
removes the document it absorbed — are about accumulated state, and a
call-assertion mock cannot express them.
"""

from datetime import datetime, timedelta, timezone

import time

import pytest

from realtime.services.event_folder import RealtimeEventFolder
from realtime.services.event_store import EventStoreUnavailable, RealtimeEventStore
from realtime.services.incident_service import IncidentService


BASE = datetime(2026, 3, 1, 12, 0, 0, tzinfo=timezone.utc)


def iso(moment):
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def chunk(idx, sensor="cam-1", category="alert", offset_s=0, span_s=30):
    start = BASE + timedelta(seconds=offset_s)
    end = start + timedelta(seconds=span_s)
    return {
        "_id": f"{sensor}-{category}-{idx}",
        "Id": f"{sensor}-{category}-{idx}",
        "sensorId": sensor,
        "category": category,
        "timestamp": iso(start),
        "end": iso(end),
        "info": {
            "chunkIdx": idx,
            "verdict": "confirmed",
            "requestId": "req-1",
            "reasoning": f"chunk {idx}",
        },
    }


class FakeIndices:
    def __init__(self, client):
        self._client = client

    def exists(self, index):
        return index in self._client.indices_set

    def create(self, index, mappings=None):
        if index in self._client.indices_set:
            raise RuntimeError("resource_already_exists_exception")
        self._client.indices_set.add(index)
        self._client.mappings[index] = mappings


# Elasticsearch refuses metadata fields inside _source; the real client raises
# document_parsing_exception. The double has to refuse them too, or code that
# leaks one passes here and fails in production.
_ES_META = {"_id", "_index", "_seq_no", "_primary_term", "_version", "_score"}


class NotFoundError(Exception):
    """Shaped like ``elasticsearch.NotFoundError``: named, and carrying a 404.

    A bare ``KeyError`` here let code that cannot tell "no such document" from
    "the cluster is unreachable" pass, and the two demand opposite answers.
    """

    def __init__(self, doc_id):
        super().__init__(f"no such document: {doc_id}")
        self.status_code = 404


def _epoch_millis(iso_value):
    """Date sort values come back from Elasticsearch as epoch milliseconds."""
    if not iso_value:
        return 0
    text = str(iso_value).replace("Z", "+00:00")
    try:
        return int(datetime.fromisoformat(text).timestamp() * 1000)
    except ValueError:
        return 0


class FakeClient:
    """A deliberately strict Elasticsearch double.

    Strict where being generous would hide a defect: it rejects reserved fields
    in a document body, honours the ``sort`` it is given rather than assuming
    one, evaluates ``must_not``, reports per-item bulk errors, returns date sort
    values as epoch milliseconds, pages composite aggregations, raises a
    404-shaped error for a document or an index that is not there, and refuses
    outright any range bound or bool clause it does not model.
    """

    def __init__(self):
        self.docs = {}
        self.seq = {}
        self.indices_set = set()
        self.mappings = {}
        self.indices = FakeIndices(self)
        self.raw = []
        # A doc id to fail, or "*" for every write. Without the wildcard a test
        # that means "the whole batch failed" silently exercises the success
        # path instead, and asserts nothing.
        self.reject_next_bulk_item = None

    def _index_docs(self, index):
        return self.docs.setdefault(index, {})

    # -- search --------------------------------------------------------
    def search(self, index, query=None, size=10, sort=None, aggregations=None,
               track_total_hits=False, **kwargs):
        # A search against an index nobody has created is an error unless the
        # caller opted out of it. Returning an empty page here regardless would
        # hide the difference between "no events" and "no index", which is the
        # state of every deployment until its first fold completes.
        if (
            str(index).startswith("ab-")
            and index not in self.indices_set
            and not kwargs.get("ignore_unavailable")
        ):
            raise NotFoundError(index)
        if aggregations:
            return self._composite(query, aggregations)
        if index.startswith("mdx-"):
            return self._search_raw(query, size, sort)
        return self._search_events(index, query, size, sort, kwargs.get("search_after"))

    def _search_raw(self, query, size, sort):
        hits = [d for d in self.raw if self._matches(d, query)]
        hits.sort(key=lambda d: d["timestamp"], reverse=self._is_desc(sort, "timestamp"))
        page = hits[:size]
        return {
            "hits": {
                "total": {"value": len(hits)},
                "hits": [{"_id": d["_id"], "_source": dict(d)} for d in page],
            }
        }

    def _search_events(self, index, query, size, sort, search_after):
        # Reads carry the version the document was at, which is what the folder
        # fences its writes on.
        rows = [
            (doc_id, src) for doc_id, src in self._index_docs(index).items()
            if self._matches(src, query)
        ]
        keys = self._sort_keys(sort)
        rows.sort(key=lambda kv: self._sort_value(kv[1], keys),
                  reverse=self._is_desc(sort, keys[0] if keys else None))
        matched = len(rows)
        if search_after:
            # Compared on the same values the rows were sorted on, not on their
            # string renderings. Comparing text here while sorting numerically
            # agreed only because every fixture timestamp happens to be the
            # same number of digits — so a cursor defect at a digit-count
            # boundary, or any change to the sort key's type, would pass.
            anchor = self._coerce_anchor(search_after, keys)
            descending = self._is_desc(sort, keys[0] if keys else None)
            rows = [
                kv for kv in rows
                if (self._sort_value(kv[1], keys) < anchor) == descending
                and self._sort_value(kv[1], keys) != anchor
            ]
        page = rows[:size]
        return {
            "hits": {
                "total": {"value": matched},
                "hits": [
                    {"_id": doc_id, "_source": dict(src),
                     "_seq_no": self.seq.get((index, doc_id), 0),
                     "_primary_term": 1,
                     "sort": list(self._sort_value(src, keys))}
                    for doc_id, src in page
                ],
            }
        }

    @staticmethod
    def _coerce_anchor(search_after, keys):
        """Read a ``search_after`` value back into the type its field sorts as.

        Elasticsearch accepts the epoch-millis long it handed out, whether the
        caller sends it as a number or as its string form.
        """
        out = []
        for key, value in zip(keys, search_after):
            if key in ("timestamp", "end", "createdAt"):
                try:
                    out.append(int(value))
                except (TypeError, ValueError):
                    raise AssertionError(
                        f"search_after value {value!r} is not a valid sort value "
                        f"for the date field {key!r}; Elasticsearch answers 400"
                    ) from None
            else:
                out.append(str(value))
        return tuple(out)

    @staticmethod
    def _sort_keys(sort):
        keys = []
        for entry in sort or []:
            if isinstance(entry, dict):
                keys.extend(entry.keys())
            else:
                keys.append(str(entry).split(":")[0])
        return keys or ["timestamp"]

    @staticmethod
    def _is_desc(sort, field):
        for entry in sort or []:
            if isinstance(entry, dict) and field in entry:
                return entry[field].get("order", "asc") == "desc"
            if isinstance(entry, str) and entry.startswith(str(field)):
                return entry.endswith(":desc")
        return False

    @staticmethod
    def _sort_value(src, keys):
        out = []
        for key in keys:
            value = src.get(key)
            # ``createdAt`` belongs here: it is a ``date`` in the mapping and it
            # is the field the cursor now sorts on, so modelling it as a raw
            # string would let the double agree with a broken round trip.
            out.append(
                _epoch_millis(value)
                if key in ("timestamp", "end", "createdAt")
                else (value or "")
            )
        return tuple(out)

    def _composite(self, query, aggregations):
        composite = aggregations["groups"]["composite"]
        page_size = composite.get("size", 10)
        after = composite.get("after")
        seen = []
        for doc in self.raw:
            if not self._matches(doc, query):
                continue
            key = (doc["sensorId"], doc["category"])
            if key not in seen:
                seen.append(key)
        seen.sort()
        if after:
            anchor = (after["sensorId"], after["category"])
            seen = [k for k in seen if k > anchor]
        page = seen[:page_size]
        # Elasticsearch returns after_key on every non-empty page, including
        # the last; the caller is expected to make one more request to learn it
        # is done.
        after_key = None
        if page:
            after_key = {"sensorId": page[-1][0], "category": page[-1][1]}
        return {
            "aggregations": {
                "groups": {
                    "buckets": [{"key": {"sensorId": s, "category": c}} for s, c in page],
                    "after_key": after_key,
                }
            }
        }

    # -- query evaluation ---------------------------------------------
    def _matches(self, doc, query):
        if not query:
            return True
        return self._bool(doc, query.get("bool", {}) or {})

    def _bool(self, doc, node):
        unsupported = set(node) - {"must", "must_not", "filter", "should"}
        if unsupported:
            raise AssertionError(f"double does not model bool clauses {sorted(unsupported)}")
        # ``filter`` scores nothing but selects identically to ``must``; a double
        # that ignored it would let every filtered query match everything.
        for clause in node.get("must", []) + node.get("filter", []):
            if not self._clause(doc, clause):
                return False
        for clause in node.get("must_not", []):
            if self._clause(doc, clause):
                return False
        return True

    def _clause(self, doc, clause):
        if "bool" in clause:
            return self._bool(doc, clause["bool"])
        if "term" in clause:
            field, value = next(iter(clause["term"].items()))
            return self._field(doc, field) == value
        if "terms" in clause:
            field, values = next(iter(clause["terms"].items()))
            return self._field(doc, field) in values
        if "exists" in clause:
            return self._field(doc, clause["exists"]["field"]) is not None
        if "range" in clause:
            field, bounds = next(iter(clause["range"].items()))
            value = self._field(doc, field)
            if value is None:
                return False
            if "gte" in bounds and value < bounds["gte"]:
                return False
            if "gt" in bounds and value <= bounds["gt"]:
                return False
            if "lte" in bounds and value > bounds["lte"]:
                return False
            if "lt" in bounds and value >= bounds["lt"]:
                return False
            if not set(bounds) <= {"gte", "gt", "lte", "lt"}:
                raise AssertionError(f"double does not model range bounds {sorted(bounds)}")
            return True
        return True

    @staticmethod
    def _field(doc, field):
        current = doc
        for part in field.replace(".keyword", "").split("."):
            current = current.get(part) if isinstance(current, dict) else None
            if current is None:
                return None
        return current

    # -- writes --------------------------------------------------------
    def _version_conflict(self, meta):
        """Whether a fenced action would be refused, as the real client would.

        Modelled rather than ignored: the conditional write is the mechanism
        that stops a stalled cycle applying its batch on top of a newer fold,
        and a double that applies every write regardless would let that
        mechanism be deleted without a single test noticing.
        """
        if "if_seq_no" not in meta:
            return False
        key = (meta["_index"], meta["_id"])
        return self.seq.get(key, 0) != meta["if_seq_no"]

    def bulk(self, operations, refresh=False):
        items, errors = [], False
        i = 0
        while i < len(operations):
            action = operations[i]
            kind = "index" if "index" in action else (
                "create" if "create" in action else "delete"
            )
            if kind in ("index", "create"):
                meta, document = action[kind], operations[i + 1]
                stored = self._index_docs(meta["_index"])
                reserved = _ES_META & set(document)
                if reserved:
                    errors = True
                    items.append({kind: {
                        "_id": meta["_id"],
                        "error": {
                            "type": "document_parsing_exception",
                            "reason": (
                                f"Field [{sorted(reserved)[0]}] is a metadata field and "
                                "cannot be added inside a document"
                            ),
                        },
                    }})
                elif self.reject_next_bulk_item in ("*", meta["_id"]):
                    errors = True
                    items.append({kind: {"_id": meta["_id"], "error": {"type": "rejected"}}})
                elif kind == "create" and meta["_id"] in stored:
                    errors = True
                    items.append({kind: {
                        "_id": meta["_id"],
                        "error": {"type": "version_conflict_engine_exception"},
                    }})
                elif self._version_conflict(meta):
                    errors = True
                    items.append({kind: {
                        "_id": meta["_id"],
                        "error": {"type": "version_conflict_engine_exception"},
                    }})
                else:
                    stored[meta["_id"]] = document
                    key = (meta["_index"], meta["_id"])
                    self.seq[key] = self.seq.get(key, 0) + 1
                    items.append({kind: {"_id": meta["_id"], "status": 200}})
                i += 2
            else:
                meta = action["delete"]
                if self._version_conflict(meta):
                    errors = True
                    items.append({"delete": {
                        "_id": meta["_id"],
                        "error": {"type": "version_conflict_engine_exception"},
                    }})
                else:
                    existed = meta["_id"] in self._index_docs(meta["_index"])
                    self._index_docs(meta["_index"]).pop(meta["_id"], None)
                    items.append({"delete": {
                        "_id": meta["_id"],
                        "result": "deleted" if existed else "not_found",
                    }})
                i += 1
        return {"errors": errors, "items": items}

    def get(self, index, id):
        stored = self._index_docs(index)
        if id not in stored:
            raise NotFoundError(id)
        return {
            "_source": stored[id],
            "_seq_no": self.seq.get((index, id), 0),
            "_primary_term": 1,
        }

    def index(self, index, id, document, op_type=None, if_seq_no=None, if_primary_term=None):
        reserved = _ES_META & set(document)
        if reserved:
            raise RuntimeError(f"document_parsing_exception: {sorted(reserved)[0]}")
        stored = self._index_docs(index)
        if op_type == "create" and id in stored:
            raise RuntimeError("version_conflict_engine_exception")
        if if_seq_no is not None and self.seq.get((index, id), 0) != if_seq_no:
            raise RuntimeError("version_conflict_engine_exception")
        stored[id] = document
        self.seq[(index, id)] = self.seq.get((index, id), 0) + 1
        return {"_seq_no": self.seq[(index, id)], "_primary_term": 1}

    def delete(self, index, id, if_seq_no=None, if_primary_term=None):
        stored = self._index_docs(index)
        if if_seq_no is not None and self.seq.get((index, id), 0) != if_seq_no:
            raise RuntimeError("version_conflict_engine_exception")
        stored.pop(id, None)


class FakeEsClient:
    def __init__(self):
        self.client = FakeClient()


@pytest.fixture
def es():
    return FakeEsClient()


@pytest.fixture
def folder(es):
    service = IncidentService(
        es_client=es,
        index_base="mdx-vlm-incidents",
        consolidation={
            "max_inter_alert_gap_seconds": 60,
            "max_event_duration_seconds": 300,
            "representative": "latest",
        },
    )
    store = RealtimeEventStore(es, collection="alert-realtime-events")
    return RealtimeEventFolder(
        service, store, es, "mdx-vlm-incidents-*",
        fold_interval_seconds=30, fold_window_seconds=600,
    )


def always_recheck_the_lease(folder):
    """Force the folder to revalidate its lease at every opportunity.

    Renewal is driven by the lease clock, not by the group loop, so a test that
    merely makes ``renew_lock`` fail proves nothing on its own: the folder is
    entitled to keep working on a lease it still holds, and a unit test runs
    far too fast for one to lapse. This makes every check due, which is the
    state a real cycle reaches whenever it runs longer than a third of the TTL.
    """
    folder._renew_due = lambda **_kwargs: True


def stored_events(es):
    docs = es.client.docs.get("ab-alert-realtime-events", {})
    return {k: v for k, v in docs.items() if not v.get("_docKind")}


class TestFoldCycle:
    def test_contiguous_chunks_become_one_event(self, es, folder):
        es.client.raw = [chunk(0, offset_s=0), chunk(1, offset_s=30)]

        result = folder.run_once(now=BASE + timedelta(seconds=120))

        assert result is not None
        assert result.groups == 1
        assert result.chunks == 2
        events = stored_events(es)
        assert len(events) == 1
        event = next(iter(events.values()))
        assert event["chunk_ids"] == ["cam-1-alert-0", "cam-1-alert-1"]
        assert len(event["chunk_meta"]) == 2
        assert all("reasoning" not in meta for meta in event["chunk_meta"])

    def test_gap_beyond_bound_splits_into_two_events(self, es, folder):
        es.client.raw = [chunk(0, offset_s=0), chunk(1, offset_s=300)]

        folder.run_once(now=BASE + timedelta(seconds=400))

        assert len(stored_events(es)) == 2

    def test_second_fold_over_unchanged_evidence_changes_nothing(self, es, folder):
        es.client.raw = [chunk(0, offset_s=0), chunk(1, offset_s=30)]
        folder.run_once(now=BASE + timedelta(seconds=120))
        first = {k: dict(v) for k, v in stored_events(es).items()}

        folder.run_once(now=BASE + timedelta(seconds=121))
        second = stored_events(es)

        assert set(first) == set(second)
        for event_id, before in first.items():
            after = second[event_id]
            assert before["chunk_ids"] == after["chunk_ids"]
            assert before["timestamp"] == after["timestamp"]
            assert before["end"] == after["end"]

    def test_two_groups_are_folded_independently(self, es, folder):
        es.client.raw = [
            chunk(0, sensor="cam-1", offset_s=0),
            chunk(0, sensor="cam-2", offset_s=0),
        ]

        result = folder.run_once(now=BASE + timedelta(seconds=120))

        assert result.groups == 2
        assert len(stored_events(es)) == 2

    def test_non_confirmed_evidence_is_not_folded(self, es, folder):
        rejected = chunk(0, offset_s=0)
        rejected["info"]["verdict"] = "rejected"
        es.client.raw = [rejected]

        result = folder.run_once(now=BASE + timedelta(seconds=120))

        assert result.groups == 0
        assert stored_events(es) == {}

    def test_verifier_documents_are_not_folded(self, es, folder):
        verifier = chunk(0, offset_s=0)
        del verifier["info"]["chunkIdx"]
        es.client.raw = [verifier]

        result = folder.run_once(now=BASE + timedelta(seconds=120))

        assert result.groups == 0
        assert stored_events(es) == {}


class TestReviewRegressions:
    """Each of these reproduces a defect found in review. They fail on the code
    as it was before, and are the reason to keep the strict double."""

    def test_stored_document_carries_no_elasticsearch_metadata_field(self, es, folder):
        es.client.raw = [chunk(0, offset_s=0)]

        folder.run_once(now=BASE + timedelta(seconds=120))

        events = stored_events(es)
        assert events, "nothing was written; the bulk body was rejected"
        for event in events.values():
            assert "_id" not in event
            assert "_index" not in event
            assert event["Id"], "the application-visible identifier must survive"

    def test_a_late_chunk_that_splits_an_event_loses_no_evidence(self, es):
        service = IncidentService(
            es_client=es, index_base="mdx-vlm-incidents",
            consolidation={
                "max_inter_alert_gap_seconds": 60,
                "max_event_duration_seconds": 300,
                "representative": "latest",
            },
        )
        store = RealtimeEventStore(es)
        folder = RealtimeEventFolder(
            service, store, es, "mdx-vlm-incidents-*", fold_window_seconds=1200,
        )
        es.client.raw = [chunk(i, offset_s=i * 30) for i in range(1, 11)]
        folder.run_once(now=BASE + timedelta(seconds=700))

        # Evidence predating the event arrives, moving the start back. The
        # duration cap now fires one chunk earlier, so one event becomes two.
        es.client.raw.insert(0, chunk(0, offset_s=0))
        folder.run_once(now=BASE + timedelta(seconds=710))

        events = stored_events(es)
        members = {c for e in events.values() for c in e["chunk_ids"]}
        expected = {f"cam-1-alert-{i}" for i in range(11)}
        assert members == expected, f"evidence lost: {sorted(expected - members)}"
        assert len({e["Id"] for e in events.values()}) == len(events), \
            "two events were stamped with the same id"

    def test_a_failed_write_is_not_counted_and_blocks_the_delete(self, es, folder):
        es.client.raw = [chunk(0, offset_s=0), chunk(1, offset_s=30)]
        folder.run_once(now=BASE + timedelta(seconds=120))
        before = dict(stored_events(es))
        assert len(before) == 1
        surviving_id = next(iter(before))

        # The next write fails. Nothing may be deleted on the strength of it.
        es.client.raw.insert(0, chunk(9, offset_s=-30))
        es.client.reject_next_bulk_item = "*"
        result = folder.run_once(now=BASE + timedelta(seconds=130))
        es.client.reject_next_bulk_item = None

        assert result.failed >= 1
        assert surviving_id in stored_events(es), \
            "the old event was deleted although its replacement never landed"


class TestRetentionAndLock:
    def test_events_past_retention_are_removed(self, es, folder):
        es.client.raw = [chunk(0, offset_s=0)]
        folder.run_once(now=BASE + timedelta(seconds=400))
        assert len(stored_events(es)) == 1

        # A cycle long after the retention boundary sees the event expire.
        result = folder.run_once(now=BASE + timedelta(days=8))

        assert result.purged == 1
        assert stored_events(es) == {}

    def test_events_inside_retention_are_kept(self, es, folder):
        es.client.raw = [chunk(0, offset_s=0)]
        folder.run_once(now=BASE + timedelta(seconds=400))

        folder.run_once(now=BASE + timedelta(days=3))

        assert len(stored_events(es)) == 1

    def test_lock_is_renewed_across_a_cycle(self, es, folder):
        es.client.raw = [chunk(0, sensor="cam-1"), chunk(0, sensor="cam-2")]

        folder.run_once(now=BASE + timedelta(seconds=400))

        # Two groups, so the lease was renewed at least twice; the version the
        # folder holds must still be the one in the store.
        assert folder._lock_version is None, "the lease must be released at the end"

    def test_a_lost_lease_abandons_the_cycle(self, es, folder):
        es.client.raw = [chunk(0, sensor="cam-1"), chunk(0, sensor="cam-2")]
        original = folder._store.renew_lock

        def lose_it(owner, version, ttl_seconds):
            return None

        folder._store.renew_lock = lose_it
        always_recheck_the_lease(folder)
        try:
            result = folder.run_once(now=BASE + timedelta(seconds=400))
        finally:
            folder._store.renew_lock = original

        assert result.groups >= 1
        assert stored_events(es) == {}, "no write may follow a lost lease"


class TestUncoveredPaths:
    """Paths a mutation pass showed the suite was not exercising."""

    def test_a_late_bridging_chunk_merges_two_events_and_removes_the_absorbed_one(self, es, folder):
        # Two events separated by a silence longer than the gap bound.
        es.client.raw = [chunk(0, offset_s=0), chunk(5, offset_s=150)]
        folder.run_once(now=BASE + timedelta(seconds=400))
        assert len(stored_events(es)) == 2
        before = set(stored_events(es))

        # Evidence arrives that bridges the silence, so the two are one event.
        es.client.raw.insert(1, chunk(2, offset_s=60))
        es.client.raw.insert(2, chunk(3, offset_s=90))
        es.client.raw.insert(3, chunk(4, offset_s=120))
        result = folder.run_once(now=BASE + timedelta(seconds=410))

        after = set(stored_events(es))
        assert len(after) == 1, "the bridged events should have merged"
        assert result.superseded == 1, "the absorbed document must be deleted"
        assert len(before - after) == 1

    def test_a_truncated_group_is_skipped_entirely(self, es, folder, monkeypatch):
        import realtime.services.event_folder as module
        monkeypatch.setattr(module, "_GROUP_FETCH_CAP", 2)
        es.client.raw = [chunk(i, offset_s=i * 30) for i in range(5)]

        result = folder.run_once(now=BASE + timedelta(seconds=400))

        assert result.truncated_groups, "the truncation was not reported"
        assert stored_events(es) == {}, (
            "an event assembled from a partial read must not be written"
        )
        assert result.superseded == 0

    def test_group_enumeration_pages_beyond_one_composite_page(self, es, folder, monkeypatch):
        import realtime.services.event_folder as module
        monkeypatch.setattr(module, "_GROUP_PAGE_SIZE", 2)
        es.client.raw = [chunk(0, sensor=f"cam-{i}") for i in range(5)]

        result = folder.run_once(now=BASE + timedelta(seconds=400))

        assert result.groups == 5, f"only {result.groups} of 5 groups were walked"
        assert len(stored_events(es)) == 5

    def test_a_lost_lease_stops_before_retention_and_freshness(self, es, folder):
        es.client.raw = [chunk(0, sensor="cam-1"), chunk(0, sensor="cam-2")]
        calls = {"purge": 0, "record": 0}
        folder._store.purge_older_than = lambda *a, **k: calls.__setitem__("purge", calls["purge"] + 1) or 0
        folder._store.record_fold = lambda *a, **k: calls.__setitem__("record", calls["record"] + 1)
        folder._store.renew_lock = lambda *a, **k: None
        always_recheck_the_lease(folder)

        result = folder.run_once(now=BASE + timedelta(seconds=400))

        assert result.aborted is True
        assert calls == {"purge": 0, "record": 0}, (
            "an abandoned cycle must not delete or publish itself as complete"
        )

    def test_release_hands_the_lock_to_a_different_owner(self, es, folder):
        es.client.raw = [chunk(0, offset_s=0)]
        folder.run_once(now=BASE + timedelta(seconds=400))

        other = RealtimeEventStore(es)
        assert other.acquire_lock("a-different-instance", ttl_seconds=300) is not None

    def test_upsert_reports_only_what_landed(self, es):
        store = RealtimeEventStore(es)
        good = {"Id": "evt-good", "timestamp": iso(BASE), "end": iso(BASE)}
        bad = {"Id": "evt-bad", "timestamp": iso(BASE), "end": iso(BASE)}
        es.client.reject_next_bulk_item = "evt-bad"

        written, failed = store.upsert([good, bad])

        assert written == ["evt-good"]
        assert failed == ["evt-bad"]
        assert not set(written) & set(failed), (
            "an id reported as written must not also be reported as failed"
        )

    def test_a_stale_release_cannot_drop_a_successors_lock(self, es):
        store = RealtimeEventStore(es)
        stale = store.acquire_lock("first", ttl_seconds=300)
        # The lease expires and a successor takes it over.
        es.client.docs[store.index]["_fold_lock"]["expiresAt"] = 0
        assert store.acquire_lock("second", ttl_seconds=300) is not None

        # The original holder now finishes and releases with the version it
        # remembers. Deleting on that version must be refused.
        store.release_lock("first", stale)

        assert store.acquire_lock("third", ttl_seconds=300) is None, (
            "the successor's lock was deleted by a stale release"
        )

    def test_an_event_older_than_the_window_is_not_rewritten(self, es, folder):
        """The lookback exists to read events whole, not to re-write settled ones."""
        es.client.raw = [chunk(i, offset_s=i * 30) for i in range(4)]
        folder.run_once(now=BASE + timedelta(seconds=400))
        settled = {k: dict(v) for k, v in stored_events(es).items()}
        assert settled

        # Far enough on that the event sits inside the lookback margin but
        # outside the window. It must be left exactly as it is.
        folder.run_once(now=BASE + timedelta(seconds=800))

        for event_id, before in settled.items():
            after = stored_events(es).get(event_id)
            assert after is not None, f"{event_id} was removed once out of window"
            assert after["chunk_ids"] == before["chunk_ids"]
            assert after["updatedAt"] == before["updatedAt"], (
                "a settled event outside the window must not be rewritten"
            )

    def test_an_old_event_is_left_alone_while_its_group_stays_active(self, es, folder):
        """The lookback reads history; it does not license rewriting it.

        The group has fresh evidence, so it is walked every cycle, and the
        lookback pulls its older chunks back in. Those older events were settled
        by an earlier cycle and may sit against the fetch boundary, where their
        first chunk cannot be proven present — so they must not be rewritten.
        """
        es.client.raw = [chunk(i, offset_s=i * 30) for i in range(4)]
        folder.run_once(now=BASE + timedelta(seconds=400))
        settled = {k: dict(v) for k, v in stored_events(es).items()}
        assert len(settled) == 1

        # Fresh evidence keeps the group active far later.
        es.client.raw.append(chunk(20, offset_s=700))
        es.client.raw.append(chunk(21, offset_s=730))
        folder.run_once(now=BASE + timedelta(seconds=800))

        for event_id, before in settled.items():
            after = stored_events(es).get(event_id)
            assert after is not None, f"{event_id} was deleted by a later cycle"
            assert after["updatedAt"] == before["updatedAt"], (
                "an event outside the window was rewritten from a lookback read"
            )

    def test_upsert_refuses_a_batch_with_a_repeated_id(self, es):
        store = RealtimeEventStore(es)
        duplicate = {"Id": "evt-same", "timestamp": iso(BASE), "end": iso(BASE)}

        with pytest.raises(ValueError, match="duplicate event id"):
            store.upsert([duplicate, dict(duplicate)])


class TestConstructorValidation:
    def _folder(self, es, **consolidation):
        service = IncidentService(
            es_client=es, index_base="mdx-vlm-incidents",
            consolidation={
                "max_inter_alert_gap_seconds": 60,
                "max_event_duration_seconds": 300,
                "representative": "latest",
                **consolidation,
            },
        )
        return service

    def test_an_unbounded_event_duration_is_refused(self, es):
        service = self._folder(es, max_event_duration_seconds=None)

        with pytest.raises(ValueError, match="max_event_duration_seconds"):
            RealtimeEventFolder(
                service, RealtimeEventStore(es), es, "mdx-vlm-incidents-*",
                fold_window_seconds=600,
            )

    def test_a_window_that_cannot_contain_an_event_is_refused(self, es):
        service = self._folder(es)

        with pytest.raises(ValueError, match="fold_window_seconds"):
            RealtimeEventFolder(
                service, RealtimeEventStore(es), es, "mdx-vlm-incidents-*",
                fold_window_seconds=300,
            )


class TestEquivalenceWithComputedView:
    """The persisted event must match what the retrieval path would compute."""

    def test_persisted_event_matches_consolidate_output(self, es, folder):
        chunks = [chunk(0, offset_s=0), chunk(1, offset_s=30), chunk(2, offset_s=60)]
        es.client.raw = list(chunks)

        folder.run_once(now=BASE + timedelta(seconds=200))
        persisted = next(iter(stored_events(es).values()))

        computed = folder._svc.consolidate([dict(c) for c in chunks])
        assert len(computed) == 1
        expected = computed[0]

        for field in ("Id", "timestamp", "end", "chunk_ids", "sensorId", "category"):
            assert persisted[field] == expected[field], f"{field} diverged"
        assert persisted["info"]["chunkCount"] == expected["info"]["chunkCount"]
        assert persisted["chunk_meta"] == expected["chunk_meta"]


class TestCycleDiscipline:
    def test_cycle_is_skipped_while_another_is_running(self, es, folder):
        es.client.raw = [chunk(0, offset_s=0)]
        folder._cycle_lock.acquire()
        try:
            assert folder.run_once(now=BASE + timedelta(seconds=120)) is None
        finally:
            folder._cycle_lock.release()

    def test_cycle_is_skipped_while_another_instance_holds_the_lock(self, es, folder):
        es.client.raw = [chunk(0, offset_s=0)]
        folder._store.acquire_lock("someone-else", ttl_seconds=300)

        assert folder.run_once(now=BASE + timedelta(seconds=120)) is None
        assert stored_events(es) == {}

    def test_lock_is_released_so_the_next_cycle_runs(self, es, folder):
        es.client.raw = [chunk(0, offset_s=0)]
        folder.run_once(now=BASE + timedelta(seconds=120))

        assert folder.run_once(now=BASE + timedelta(seconds=150)) is not None

    def test_last_completed_at_is_published_for_freshness(self, es, folder):
        assert folder.last_completed_at is None
        es.client.raw = [chunk(0, offset_s=0)]
        folder.run_once(now=BASE + timedelta(seconds=120))
        assert folder.last_completed_at is not None


class TestStoreLock:
    def test_second_owner_is_refused_while_the_lock_is_live(self, es):
        store = RealtimeEventStore(es)
        assert store.acquire_lock("a", ttl_seconds=300) is not None
        assert store.acquire_lock("b", ttl_seconds=300) is None

    def test_expired_lock_can_be_taken_over(self, es):
        store = RealtimeEventStore(es)
        store.acquire_lock("a", ttl_seconds=300)
        # A holder that died without releasing leaves the document behind; the
        # TTL is what lets the next cycle proceed rather than wedging forever.
        es.client.docs[store.index]["_fold_lock"]["expiresAt"] = 0

        assert store.acquire_lock("b", ttl_seconds=300) is not None

    def test_release_without_a_version_is_a_no_op(self, es):
        store = RealtimeEventStore(es)
        store.acquire_lock("a", ttl_seconds=300)

        # A caller that lost track of its version has nothing safe to delete on,
        # so the lease is left to expire rather than removed blind.
        store.release_lock("b", None)

        assert store.acquire_lock("c", ttl_seconds=300) is None

    def test_internal_documents_are_excluded_from_event_reads(self, es):
        store = RealtimeEventStore(es)
        # The folder ensures the index before it reads; this mirrors that, and
        # the double now refuses a read against an index nobody created.
        store.ensure_index()
        store.acquire_lock("a", ttl_seconds=300)
        store.record_fold(completed_at=1.0, duration=0.1, events=0)

        events, complete = store.events_in_window(
            "2026-01-01T00:00:00Z", "2027-01-01T00:00:00Z"
        )

        assert events == []
        assert complete is True


class TestSupersessionGuards:
    """Each guard in ``_superseded`` is the only thing standing between a
    failed write and a destroyed durable record. Every one of these fails with
    its guard removed, and all of them passed without these tests."""

    def test_a_failed_merge_write_destroys_nothing(self, es, folder):
        """Three events merge into one; the merged write is rejected.

        Without the ``covered`` guard the three originals are deleted anyway,
        and the evidence exists nowhere afterwards.
        """
        es.client.raw = [chunk(i, offset_s=i * 120) for i in range(3)]
        folder.run_once(now=BASE + timedelta(seconds=400))
        before = {
            event_id: tuple(e["chunk_ids"]) for event_id, e in stored_events(es).items()
        }
        assert len(before) == 3, "the three chunks should start as three events"
        surviving_chunks = {c for members in before.values() for c in members}

        # Bridging chunks turn the three into one, and that one write fails.
        es.client.raw += [chunk(4, offset_s=60), chunk(5, offset_s=180)]
        es.client.reject_next_bulk_item = "*"
        result = folder.run_once(now=BASE + timedelta(seconds=420))
        es.client.reject_next_bulk_item = None
        assert result.failed >= 1 and result.events == 0, (
            "the premise of this test is that the replacement write failed"
        )

        after = stored_events(es)
        still_there = {c for e in after.values() for c in e["chunk_ids"]}
        assert surviving_chunks <= still_there, (
            "evidence disappeared: the merged replacement was never written, so "
            f"deleting what it absorbed lost {sorted(surviving_chunks - still_there)}"
        )

    def test_an_event_whose_evidence_was_not_re_read_is_left_alone(self, es, folder):
        """A stored event whose chunk has since lost its confirmed verdict.

        Its members are no longer in the fetched set, so this cycle recomputed
        nothing about it and its absence from the result means nothing. Without
        the ``issubset(fetched_chunk_ids)`` guard it is deleted on that silence.
        """
        es.client.raw = [chunk(0, offset_s=0), chunk(1, offset_s=200)]
        folder.run_once(now=BASE + timedelta(seconds=400))
        assert len(stored_events(es)) == 2

        # The first chunk's verdict is withdrawn, so it is no longer fetched.
        es.client.raw[0]["info"]["verdict"] = "not_confirmed"
        folder.run_once(now=BASE + timedelta(seconds=420))

        remaining = {c for e in stored_events(es).values() for c in e["chunk_ids"]}
        assert "cam-1-alert-0" in remaining, (
            "an event was deleted because its evidence was not re-read, which is "
            "silence rather than a decision"
        )

    def test_a_late_bridge_across_the_window_boundary_leaves_no_duplicate(self, es, folder):
        """The case the removed window guard created.

        The fetch reaches further back than the window, so an event ending
        behind the window can be absorbed by one ending inside it. Refusing to
        supersede it there stranded a duplicate that no later cycle could
        reclaim, because the window only ever moves forward.
        """
        es.client.raw = [
            chunk(0, offset_s=0), chunk(1, offset_s=100), chunk(2, offset_s=150),
        ]
        folder.run_once(now=BASE + timedelta(seconds=300))
        assert len(stored_events(es)) == 2

        # A bridging chunk arrives once the first event sits behind the window.
        es.client.raw.append(chunk(3, offset_s=60))
        folder.run_once(now=BASE + timedelta(seconds=650))

        owners = {}
        for event_id, event in stored_events(es).items():
            for chunk_id in event["chunk_ids"]:
                owners.setdefault(chunk_id, []).append(event_id)
        duplicated = {c: ids for c, ids in owners.items() if len(ids) > 1}
        assert not duplicated, f"the same evidence is reported by two events: {duplicated}"


class TestPartialReadsAreNotCleanCycles:
    """A cycle that could not see everything must not report that it did."""

    def test_incomplete_group_enumeration_abandons_the_cycle(self, es, folder, monkeypatch):
        es.client.raw = [chunk(i, sensor=f"cam-{i}", offset_s=0) for i in range(5)]
        monkeypatch.setattr(
            "realtime.services.event_folder._GROUP_PAGE_SIZE", 2, raising=False
        )
        real_search = es.client.search
        calls = {"n": 0}

        def fail_on_second_page(**kwargs):
            if kwargs.get("aggregations"):
                calls["n"] += 1
                if calls["n"] == 2:
                    raise RuntimeError("search_phase_execution_exception")
            return real_search(**kwargs)

        es.client.search = fail_on_second_page
        result = folder.run_once(now=BASE + timedelta(seconds=120))
        es.client.search = real_search

        assert result is not None and result.aborted, (
            "part of the fleet was never walked, so the cycle is not complete"
        )
        assert folder.last_completed_at is None, (
            "a partial cycle published itself as a completed fold; a caller "
            "reading fold_lag_seconds would see fresh data that is not there"
        )

    def test_a_group_whose_evidence_cannot_be_read_is_skipped_not_folded_to_nothing(
        self, es, folder,
    ):
        es.client.raw = [chunk(0, offset_s=0), chunk(1, sensor="cam-2", offset_s=0)]
        folder.run_once(now=BASE + timedelta(seconds=120))
        before = dict(stored_events(es))
        assert len(before) == 2

        real_search = es.client.search

        def fail_for_cam_1(**kwargs):
            query = str(kwargs.get("query"))
            if "cam-1" in query and not kwargs.get("aggregations"):
                raise RuntimeError("circuit_breaking_exception")
            return real_search(**kwargs)

        es.client.search = fail_for_cam_1
        result = folder.run_once(now=BASE + timedelta(seconds=140))
        es.client.search = real_search

        assert result.truncated_groups, (
            "an unreadable group was folded to nothing and reported as clean"
        )
        assert stored_events(es).keys() == before.keys(), (
            "a group that could not be read had its events treated as gone"
        )

    def test_a_lost_lease_leaves_the_stored_events_alone(self, es, folder):
        es.client.raw = [chunk(0, offset_s=0)]
        folder.run_once(now=BASE + timedelta(seconds=120))
        before = dict(stored_events(es))

        folder._store.renew_lock = lambda *a, **k: None
        always_recheck_the_lease(folder)
        result = folder.run_once(now=BASE + timedelta(seconds=140))

        assert result.aborted
        assert stored_events(es) == before


class TestLockLifecycle:
    def test_an_abandoned_cycle_does_not_strand_the_lease(self, es, folder):
        """Every exit from a cycle has to release what it took.

        The lease outlives a failed cycle by three intervals, so a second
        instance is locked out for that long on every failure — which, if the
        cluster is unhealthy, means every cycle.
        """
        folder._store.events_in_window = lambda *a, **k: ([], False)

        result = folder.run_once(now=BASE + timedelta(seconds=120))

        assert result.aborted
        second = RealtimeEventStore(es, collection="alert-realtime-events")
        assert second.acquire_lock("a-different-instance", ttl_seconds=90) is not None, (
            "the abandoned cycle kept its lease"
        )

    def test_two_processes_on_one_host_cannot_share_a_lease(self, es, folder):
        """The owner must identify the *process*, not the machine.

        A host-derived identity was tried so a restart could reclaim its own
        lease at once. It made the two co-located processes of a rolling
        restart indistinguishable to the lock, so the newcomer took a live
        lease from the outgoing one mid-cycle — the failure the lock exists to
        prevent. Waiting out a TTL after a restart is the cheaper of the two.
        """
        store = RealtimeEventStore(es, collection="alert-realtime-events")
        store.ensure_index()
        second = RealtimeEventFolder(
            folder._svc, store, es, "mdx-vlm-incidents-*",
            fold_interval_seconds=30, fold_window_seconds=600,
        )
        assert second._owner != folder._owner

        first_lease = store.acquire_lock(folder._owner, ttl_seconds=90)

        assert first_lease is not None
        assert store.acquire_lock(second._owner, ttl_seconds=90) is None, (
            "a second process on the same host took a lease the first still holds"
        )
        assert store.renew_lock(folder._owner, first_lease, ttl_seconds=90) is not None, (
            "the original holder lost its lease to a co-located process"
        )

    def test_an_expired_lease_is_taken_over_after_a_restart(self, es, folder):
        """The cost of the fix above, pinned so it stays bounded: a new process
        waits for the lease to lapse, and then does get it."""
        store = RealtimeEventStore(es, collection="alert-realtime-events")
        store.ensure_index()
        store.acquire_lock("a-process-that-has-since-died", ttl_seconds=90)
        # The previous process was killed, so nothing released the lease; the
        # TTL lapsing is the only thing that frees it.
        es.client.docs[store.index]["_fold_lock"]["expiresAt"] = 0

        assert store.acquire_lock(folder._owner, ttl_seconds=90) is not None


class TestFreshness:
    def test_a_store_that_has_never_folded_reports_no_lag(self, es):
        store = RealtimeEventStore(es)
        store.ensure_index()

        assert store.fold_lag_seconds() is None

    def test_a_freshness_read_that_failed_is_not_reported_as_never_folded(self, es):
        store = RealtimeEventStore(es)
        store.ensure_index()

        def blow_up(**_kwargs):
            raise RuntimeError("cluster is red")

        es.client.get = blow_up
        with pytest.raises(EventStoreUnavailable):
            store.fold_lag_seconds()

    def test_freshness_is_published_only_by_a_complete_cycle(self, es, folder):
        es.client.raw = [chunk(0, offset_s=0)]
        folder._store.events_in_window = lambda *a, **k: ([], False)

        folder.run_once(now=BASE + timedelta(seconds=120))

        assert folder._store.fold_lag_seconds() is None


class TestIdentityStability:
    def test_the_ordering_key_is_inherited_rather_than_recomputed(self, es, folder):
        """``createdAt`` is what the cursor pages on, so it must not move.

        Evidence predating an event pulls its ``timestamp`` earlier; a
        ``createdAt`` recomputed from it would slide the event behind a cursor
        already issued and have a caller see the same row twice.
        """
        es.client.raw = [chunk(1, offset_s=100)]
        folder.run_once(now=BASE + timedelta(seconds=200))
        first = next(iter(stored_events(es).values()))
        created_before = first["createdAt"]
        assert created_before == first["timestamp"]

        es.client.raw.append(chunk(0, offset_s=60))
        folder.run_once(now=BASE + timedelta(seconds=220))

        after = next(iter(stored_events(es).values()))
        assert after["timestamp"] < created_before, "the event did not actually move"
        assert after["createdAt"] == created_before, (
            "createdAt followed timestamp backwards, which breaks the cursor"
        )

    def test_two_events_never_share_an_id(self, es, folder):
        """A split hands one event the id its sibling derives from its own
        first chunk. Applied in one batch, the first one's evidence vanishes."""
        es.client.raw = [chunk(i, offset_s=i * 30) for i in range(6)]
        folder.run_once(now=BASE + timedelta(seconds=300))

        # A gap opens the group into two events whose derived ids collide.
        es.client.raw = [chunk(i, offset_s=i * 30) for i in range(3)] + [
            chunk(i, offset_s=i * 30 + 400) for i in range(3, 6)
        ]
        folder.run_once(now=BASE + timedelta(seconds=800))

        events = stored_events(es)
        ids = [e["Id"] for e in events.values()]
        assert len(ids) == len(set(ids)), f"two events share an id: {ids}"


class TestConfigurationIsRejectedNotDegraded:
    def _folder(self, es, **kwargs):
        service = IncidentService(
            es_client=es, index_base="mdx-vlm-incidents",
            consolidation={
                "max_inter_alert_gap_seconds": kwargs.pop("gap", 60),
                "max_event_duration_seconds": kwargs.pop("cap", 300),
                "representative": "latest",
            },
        )
        return RealtimeEventFolder(
            service, RealtimeEventStore(es), es, "mdx-vlm-incidents-*",
            fold_interval_seconds=30, **kwargs,
        )

    def test_a_window_exactly_equal_to_the_floor_is_refused(self, es):
        """Equal is not enough: an event ending at the window start began
        exactly at the fetch boundary, so it is read with nothing to spare."""
        with pytest.raises(ValueError):
            self._folder(es, cap=300, gap=60, fold_window_seconds=360)

    def test_a_window_one_second_above_the_floor_is_accepted(self, es):
        assert self._folder(es, cap=300, gap=60, fold_window_seconds=361) is not None

    def test_the_entrypoint_treats_a_bad_window_as_fatal(self):
        """Swallowed, this leaves a deployment that starts healthy, passes
        readiness, and never folds anything."""
        from realtime.services.event_folder import validate_fold_bounds

        with pytest.raises(ValueError):
            validate_fold_bounds(360, 300, 60)
        with pytest.raises(ValueError):
            validate_fold_bounds(600, None, 60)
        assert validate_fold_bounds(600, 300, 60) == 360


class TestRetentionTargets:
    def test_the_reaper_never_removes_the_lock_or_the_state_document(self, es, folder):
        es.client.raw = [chunk(0, offset_s=0)]
        folder.run_once(now=BASE + timedelta(seconds=120))
        store = folder._store
        store.record_fold(completed_at=time.time(), duration=0.1, events=1)
        store.acquire_lock("someone", ttl_seconds=90)

        store.purge_older_than(iso(BASE + timedelta(days=3650)))

        docs = es.client.docs.get("ab-alert-realtime-events", {})
        assert "_fold_lock" in docs, "the reaper deleted the lock it runs under"
        assert "_fold_state" in docs, "the reaper deleted the freshness record"

    def test_internal_documents_are_excluded_by_kind_not_by_luck(self, es):
        """The reaper's exclusion has to hold on its own terms.

        Today the lock and state documents survive a purge only because neither
        carries an ``end``, so the range filter misses them — the exclusion is
        never what saves them. Give one an ``end`` and the guard is the only
        thing left, which is the situation any future field addition creates.
        """
        store = RealtimeEventStore(es)
        store.ensure_index()
        es.client.docs.setdefault("ab-alert-realtime-events", {})["_fold_state"] = {
            "_docKind": "state",
            "completedAt": 0.0,
            "end": iso(BASE - timedelta(days=365)),
        }

        store.purge_older_than(iso(BASE))

        assert "_fold_state" in es.client.docs["ab-alert-realtime-events"], (
            "the reaper deleted its own freshness record once that record had "
            "a field the range filter could match"
        )

    def test_retention_is_measured_from_the_end_of_an_event(self, es):
        """An event that started long ago but is still running must survive.

        Measured from ``timestamp`` instead, a long condition is purged out
        from under the consumer watching it.
        """
        store = RealtimeEventStore(es)
        store.ensure_index()
        store.upsert([{
            "Id": "evt-long", "sensorId": "cam-1", "category": "alert",
            "timestamp": iso(BASE - timedelta(days=30)),
            "end": iso(BASE),
            "createdAt": iso(BASE - timedelta(days=30)),
        }])

        store.purge_older_than(iso(BASE - timedelta(days=7)))

        assert "evt-long" in es.client.docs["ab-alert-realtime-events"]


class TestIndexCreation:
    def test_a_failed_index_creation_is_not_reported_as_usable(self, es):
        store = RealtimeEventStore(es)

        def blow_up(**_kwargs):
            raise RuntimeError("cluster_block_exception")

        es.client.indices.create = blow_up
        assert store.ensure_index() is False

    def test_a_cycle_does_not_run_against_an_unusable_index(self, es, folder):
        folder._store.ensure_index = lambda: False

        assert folder.run_once(now=BASE + timedelta(seconds=120)) is None


class TestTheFetchBoundary:
    """The window start cuts through events; the fetch margin is what keeps
    them whole. These fail if the margin or the enumeration range is narrowed
    back to the window."""

    def test_an_event_straddling_the_window_start_is_not_eroded(self, es, folder):
        """Read from the window start instead of behind it, and the earlier
        half of a straddling event is invisible — so the event is rewritten
        from what is left and its first chunk is destroyed."""
        es.client.raw = [chunk(0, offset_s=290), chunk(1, offset_s=310)]
        folder.run_once(now=BASE + timedelta(seconds=400))
        first = next(iter(stored_events(es).values()))
        assert len(first["chunk_ids"]) == 2

        # window_start is now BASE+300, between the two chunks.
        folder.run_once(now=BASE + timedelta(seconds=900))

        events = stored_events(es)
        assert len(events) == 1, f"the event was split or duplicated: {list(events)}"
        assert len(next(iter(events.values()))["chunk_ids"]) == 2, (
            "the half of the event behind the window start was dropped"
        )

    def test_a_group_whose_evidence_all_predates_the_window_is_still_folded(
        self, es, folder,
    ):
        """Its chunks start before the window, but the event they form ends
        inside it — so this group owns work in this cycle and enumerating on
        the narrower range skips it entirely."""
        es.client.raw = [chunk(0, offset_s=280, span_s=60)]
        folder.run_once(now=BASE + timedelta(seconds=400))
        assert len(next(iter(stored_events(es).values()))["chunk_ids"]) == 1

        # Late evidence, also entirely before the window start of BASE+300.
        es.client.raw.append(chunk(1, offset_s=240, span_s=30))
        folder.run_once(now=BASE + timedelta(seconds=900))

        merged = next(iter(stored_events(es).values()))
        assert len(merged["chunk_ids"]) == 2, (
            "the group was never enumerated, so its late evidence joined nothing"
        )


class TestDerivedIdCollisions:
    def test_two_events_in_one_batch_never_share_an_id(self, es, folder):
        """Two events of a group derive different ids because they start at
        different chunks — unless two chunks share a timestamp, which nothing
        forbids. Applied in one bulk request the second would overwrite the
        first and take its evidence with it."""
        from realtime.services.event_folder import _dedupe_derived_ids

        candidates = [
            {"Id": "evt-same", "chunk_ids": ["c1"]},
            {"Id": "evt-same", "chunk_ids": ["c2"]},
        ]

        out = _dedupe_derived_ids(candidates)

        ids = [e["Id"] for e in out]
        assert ids[0] == "evt-same", "the first event should keep its derived id"
        assert len(set(ids)) == 2, "two events went out under one id"

    def test_the_remint_is_a_function_of_the_evidence(self, es, folder):
        """Re-minting must not reintroduce order dependence: the same pair in
        the same order always yields the same pair of ids."""
        from realtime.services.event_folder import _dedupe_derived_ids

        def run():
            return [e["Id"] for e in _dedupe_derived_ids([
                {"Id": "evt-same", "chunk_ids": ["c1"]},
                {"Id": "evt-same", "chunk_ids": ["c2"]},
            ])]

        assert run() == run()


class TestPersistenceConfigIsValidated:
    """Values that cannot mean what they say are refused, not interpreted."""

    def _cfg(self, **overrides):
        persistence = {
            "fold_interval_seconds": 30, "fold_window_seconds": 600,
            "retention_days": 7,
        }
        persistence.update(overrides)
        return persistence, {
            "max_event_duration_seconds": 300, "max_inter_alert_gap_seconds": 60,
        }

    def test_the_shipped_defaults_are_accepted(self):
        from realtime.services.event_folder import validate_persistence_config

        assert validate_persistence_config(*self._cfg()) == 600

    def test_zero_retention_is_refused_rather_than_read_as_disabled(self):
        """``retention_days: 0`` read as falsey disables the reaper silently,
        so representative VLM text is kept for ever — the opposite of the
        intent anyone writing 0 could have had."""
        from realtime.services.event_folder import validate_persistence_config

        with pytest.raises(ValueError):
            validate_persistence_config(*self._cfg(retention_days=0))

    def test_negative_retention_is_refused_rather_than_purging_everything(self):
        """A negative value puts the cutoff in the future, so the first cycle
        deletes the entire store."""
        from realtime.services.event_folder import validate_persistence_config

        with pytest.raises(ValueError):
            validate_persistence_config(*self._cfg(retention_days=-1))

    def test_retention_cannot_be_switched_off(self, es):
        """There is no configuration that keeps persisted events for ever.

        Each carries a clone of the representative chunk's model text and is
        served by an endpoint with no authorization boundary, so unbounded
        retention would be an unbounded exposure. Offering it would need the
        model text stripped first, which is a different change.
        """
        from realtime.services.event_folder import validate_persistence_config

        with pytest.raises(ValueError):
            validate_persistence_config(*self._cfg(retention_days=None))

    def test_a_non_numeric_bound_fails_at_startup_not_in_the_background(self):
        from realtime.services.event_folder import validate_persistence_config

        with pytest.raises(ValueError):
            validate_persistence_config(*self._cfg(fold_interval_seconds="thirty"))

    def test_a_zero_interval_is_refused(self):
        from realtime.services.event_folder import validate_persistence_config

        with pytest.raises(ValueError):
            validate_persistence_config(*self._cfg(fold_interval_seconds=0))

    def test_a_negative_retention_would_otherwise_empty_the_store(self, es):
        """Shown rather than argued: the guard above is what stands between a
        typo and the whole store."""
        store = RealtimeEventStore(es)
        store.ensure_index()
        store.upsert([{
            "Id": "evt-1", "sensorId": "cam-1", "category": "alert",
            "timestamp": iso(BASE), "end": iso(BASE), "createdAt": iso(BASE),
        }])

        removed = store.purge_older_than(iso(BASE + timedelta(days=1)))

        assert removed == 1, (
            "a cutoff in the future purges everything, which is what a negative "
            "retention_days produces"
        )


class TestTheLeaseCoversTheWrites:
    def test_a_lease_lost_after_reading_stops_before_writing(self, es, folder):
        """Renewing only before the read leaves the writes unprotected.

        Reading and consolidating a large group can outlast the lease on its
        own, and the writes are precisely what a second instance must not be
        racing.
        """
        es.client.raw = [chunk(0, offset_s=0)]
        calls = {"n": 0}
        real_renew = folder._store.renew_lock

        def lose_it_on_the_second_check(*args, **kwargs):
            calls["n"] += 1
            # The first check, before the fetch, succeeds; the second — the one
            # guarding the writes — does not.
            if calls["n"] == 1:
                return real_renew(*args, **kwargs)
            return None

        folder._store.renew_lock = lose_it_on_the_second_check
        always_recheck_the_lease(folder)
        result = folder.run_once(now=BASE + timedelta(seconds=120))

        assert calls["n"] >= 2, "the lease is not revalidated before the writes"
        assert result.aborted
        assert not stored_events(es), "an event was written without holding the lease"


class TestFreshnessIsPublishedOrTheCycleIsNot:
    def test_a_cycle_whose_freshness_failed_does_not_claim_to_be_fresh(self, es, folder):
        """Otherwise the endpoint says no cycle has ever run while the process
        metric says one just finished — the ambiguity this value removes."""
        es.client.raw = [chunk(0, offset_s=0)]
        # Failed at the Elasticsearch call, not by stubbing the method: the
        # point at issue is whether ``record_fold`` reports its own failure,
        # and a stub would assume the answer.
        real_index = es.client.index

        def fail_the_state_write(index, id, document, **kwargs):
            if id == "_fold_state":
                raise RuntimeError("cluster_block_exception")
            return real_index(index, id, document, **kwargs)

        es.client.index = fail_the_state_write
        result = folder.run_once(now=BASE + timedelta(seconds=120))
        es.client.index = real_index

        assert result.freshness_unpublished
        assert folder.last_completed_at is None
        # The work is not disowned along with the announcement of it. Reporting
        # the cycle as aborted would delete the record of writes that are in the
        # index, leaving the counters permanently short against it.
        assert result.aborted is False
        assert result.events == 1
        assert stored_events(es), "the events themselves should still have landed"


class TestBoundsAreFinite:
    def test_a_nan_bound_is_refused(self):
        from realtime.services.event_folder import validate_persistence_config

        with pytest.raises(ValueError):
            validate_persistence_config(
                {"fold_window_seconds": float("nan")},
                {"max_event_duration_seconds": 300, "max_inter_alert_gap_seconds": 60},
            )

    def test_an_infinite_bound_is_refused(self):
        """A window of infinity passes ``> floor`` and then makes every fetch
        unbounded — the scan the per-group strategy exists to avoid."""
        from realtime.services.event_folder import validate_persistence_config

        with pytest.raises(ValueError):
            validate_persistence_config(
                {"fold_window_seconds": float("inf")},
                {"max_event_duration_seconds": 300, "max_inter_alert_gap_seconds": 60},
            )

    def test_an_absent_duration_cap_uses_the_same_default_as_everything_else(self):
        """Absent and explicitly null are different answers.

        Every other consumer of this key defaults it to 300. An absent key
        taking the whole instance down — the folder runs on the leader, and a
        child exiting ends the instance — would make the strictest reader of a
        shared config the one that decides whether the service starts.
        """
        from realtime.services.event_folder import validate_persistence_config

        assert validate_persistence_config(
            {"fold_window_seconds": 600}, {"max_inter_alert_gap_seconds": 60},
        ) == 600

    def test_an_explicitly_null_duration_cap_is_still_refused(self):
        from realtime.services.event_folder import validate_persistence_config

        with pytest.raises(ValueError):
            validate_persistence_config(
                {"fold_window_seconds": 600},
                {"max_event_duration_seconds": None, "max_inter_alert_gap_seconds": 60},
            )

    def test_the_message_does_not_offer_a_way_to_switch_retention_off(self):
        """An operator following this text must not reach an outcome the
        feature does not support. An earlier version said "set it to null to
        disable", which was both unsupported and — since unset applies the
        default — the opposite of what unset does."""
        from realtime.services.event_folder import validate_persistence_config

        with pytest.raises(ValueError) as caught:
            validate_persistence_config(
                {"fold_window_seconds": 600, "retention_days": 0},
                {"max_event_duration_seconds": 300, "max_inter_alert_gap_seconds": 60},
            )
        message = str(caught.value)
        assert "null" not in message
        assert "no value that switches it off" in message


class TestSupersessionSkipsMemberlessDocuments:
    def test_a_stored_document_with_no_members_is_never_dropped(self, es, folder):
        """An empty set is a subset of everything.

        Without the guard, a malformed document with no ``chunk_ids`` passes
        the coverage test vacuously and is deleted on evidence that says
        nothing about it — the same "silence is not a decision" rule the rest
        of this function is built on.
        """
        es.client.raw = [chunk(0, offset_s=0)]
        folder.run_once(now=BASE + timedelta(seconds=120))
        store = folder._store
        es.client.docs[store.index]["evt-malformed"] = {
            "Id": "evt-malformed", "sensorId": "cam-1", "category": "alert",
            "timestamp": iso(BASE), "end": iso(BASE + timedelta(seconds=30)),
            "createdAt": iso(BASE), "chunk_ids": [],
        }

        folder.run_once(now=BASE + timedelta(seconds=140))

        assert "evt-malformed" in es.client.docs[store.index]


class TestLeaseRenewalCadence:
    def test_renewal_becomes_due_before_the_lease_could_lapse(self, es, folder):
        """Between renewals the folder trusts its own lease clock, so the
        cadence is what makes that trust sound."""
        import time as _time

        ttl = folder._interval * 3
        folder._renewed_at = _time.monotonic()

        assert folder._renew_due(now_monotonic=_time.monotonic()) is False
        assert folder._renew_due(now_monotonic=_time.monotonic() + ttl / 4) is False
        assert folder._renew_due(now_monotonic=_time.monotonic() + ttl / 2) is True, (
            "the lease is never renewed, so a cycle longer than the TTL keeps "
            "writing on a lease it no longer holds"
        )
        assert folder._renew_due(now_monotonic=_time.monotonic() + ttl) is True

    def test_a_long_cycle_renews_rather_than_running_out(self, es, folder):
        es.client.raw = [chunk(i, sensor=f"cam-{i}", offset_s=0) for i in range(4)]
        renewals = {"n": 0}
        real_renew = folder._store.renew_lock

        def counted(*args, **kwargs):
            renewals["n"] += 1
            return real_renew(*args, **kwargs)

        folder._store.renew_lock = counted
        folder._renew_due = lambda **_kwargs: True
        folder.run_once(now=BASE + timedelta(seconds=120))

        assert renewals["n"] >= 4, "a cycle that outlasts its lease must renew it"


class TestWritesAreFenced:
    """The lock says a cycle was entitled to start. Only the conditional write
    says it is still entitled to land."""

    def test_a_stalled_cycle_cannot_overwrite_a_newer_fold(self, es, folder):
        """The lease is not a fencing token, so the write carries one.

        A cycle that stalls inside ``bulk`` past its TTL, while a second writer
        takes the lease and folds the same window, would otherwise have its
        stale batch applied on top — resurrecting superseded events and undoing
        the newer fold.
        """
        es.client.raw = [chunk(0, offset_s=0)]
        folder.run_once(now=BASE + timedelta(seconds=120))
        store = folder._store
        stale, complete = store.events_in_window(iso(BASE - timedelta(hours=1)),
                                                 iso(BASE + timedelta(hours=1)))
        assert complete and stale
        stale_versions = {
            str(e["Id"]): (e["_seq_no"], e["_primary_term"]) for e in stale
        }

        # A second writer moves the document while the first is "stalled".
        newer = dict(stale[0])
        newer["chunk_ids"] = ["written-by-the-other-writer"]
        store.upsert([newer], stale_versions)

        # The stalled batch finally lands, still carrying the old version.
        resurrected = dict(stale[0])
        resurrected["chunk_ids"] = ["the-stale-batch"]
        written, failed = store.upsert([resurrected], stale_versions)

        assert written == [] and failed, "a stale write was applied over a newer fold"
        surviving = next(iter(stored_events(es).values()))
        assert surviving["chunk_ids"] == ["written-by-the-other-writer"]

    def test_a_stalled_cycle_cannot_delete_what_a_newer_fold_rewrote(self, es, folder):
        es.client.raw = [chunk(0, offset_s=0)]
        folder.run_once(now=BASE + timedelta(seconds=120))
        store = folder._store
        stored, _ = store.events_in_window(iso(BASE - timedelta(hours=1)),
                                           iso(BASE + timedelta(hours=1)))
        versions = {str(e["Id"]): (e["_seq_no"], e["_primary_term"]) for e in stored}

        moved = dict(stored[0])
        moved["chunk_ids"] = ["written-by-the-other-writer"]
        store.upsert([moved], versions)

        removed = store.delete([str(stored[0]["Id"])], versions)

        assert removed == 0, "a stale delete destroyed a newer record"
        assert stored_events(es), "the newer fold's event was deleted"

    def test_an_event_nobody_read_is_created_not_blindly_overwritten(self, es, folder):
        """With no version to fence on, the write must assert the document is
        absent — otherwise a concurrent creation is silently clobbered."""
        store = RealtimeEventStore(es)
        store.ensure_index()
        event = {
            "Id": "evt-1", "sensorId": "cam-1", "category": "alert",
            "timestamp": iso(BASE), "end": iso(BASE), "createdAt": iso(BASE),
            "chunk_ids": ["a"],
        }
        assert store.upsert([event])[0] == ["evt-1"]

        rival = dict(event, chunk_ids=["b"])
        written, failed = store.upsert([rival])

        assert written == [] and failed == ["evt-1"]
        assert stored_events(es)["evt-1"]["chunk_ids"] == ["a"]

    def test_retention_deletes_without_a_fence(self, es):
        """Retention selects on age, so two writers reading the same ages
        choose the same documents; fencing it would only make the reaper fail
        against its own concurrent folds."""
        store = RealtimeEventStore(es)
        store.ensure_index()
        store.upsert([{
            "Id": "evt-old", "sensorId": "cam-1", "category": "alert",
            "timestamp": iso(BASE - timedelta(days=30)),
            "end": iso(BASE - timedelta(days=30)), "createdAt": iso(BASE),
        }])

        assert store.purge_older_than(iso(BASE)) == 1


class TestSchedulerAccountsForOverruns:
    def test_a_cycle_that_overruns_its_interval_reports_the_skipped_ticks(self, es, folder):
        """A slow cycle must register as a skip, not quietly delay the next.

        Sleeping a whole interval after each cycle would space them by interval
        plus duration, so the fold falling behind the fleet — the thing worth
        alerting on — would never be visible.
        """
        import threading, time as _time

        folder._interval = 0.05
        cycles = {"n": 0}

        def slow_cycle(*_args, **_kwargs):
            cycles["n"] += 1
            if cycles["n"] == 1:
                _time.sleep(0.3)     # six intervals
            if cycles["n"] >= 2:
                folder._stop.set()
            return None

        folder.run_once = slow_cycle
        warnings = []
        waits = []
        real_wait = folder._stop.wait

        def record_wait(timeout=None):
            waits.append(timeout)
            return real_wait(timeout)

        folder._stop.wait = record_wait
        import logging as _logging

        class Capture(_logging.Handler):
            def emit(self, record):
                if record.levelno >= _logging.WARNING:
                    warnings.append(record.getMessage())

        logger_obj = _logging.getLogger("realtime.services.event_folder")
        handler = Capture()
        logger_obj.addHandler(handler)
        try:
            thread = threading.Thread(target=folder._run, daemon=True)
            thread.start()
            thread.join(timeout=5)
        finally:
            logger_obj.removeHandler(handler)

        assert any("overran" in w for w in warnings), (
            f"an overrunning cycle was absorbed silently; warnings: {warnings}"
        )
        # Waiting a *fixed* interval after every cycle is what turns a cadence
        # into "interval plus however long the cycle took". Against deadlines,
        # the wait after an overrun is whatever is left of the next slot —
        # strictly less than a full interval, and the difference is the drift.
        assert waits[1] < folder._interval, (
            f"the scheduler waited {waits[1]}s — a full interval — after a "
            f"cycle that had already overrun, so the schedule drifts by the "
            f"duration of every slow cycle"
        )


class TestConsolidationConfigFailsAtStartup:
    def test_an_invalid_representative_strategy_is_refused_before_the_thread(self):
        """Left to ``IncidentService``'s constructor, this raises on the
        supervisor thread — killing the thread and nothing else, so the
        instance stays healthy and folds nothing for ever."""
        from realtime.services.event_folder import validate_persistence_config

        with pytest.raises(ValueError):
            validate_persistence_config(
                {"fold_window_seconds": 600, "retention_days": 7},
                {
                    "max_event_duration_seconds": 300,
                    "max_inter_alert_gap_seconds": 60,
                    "representative": "whatever-i-like",
                },
            )

    def test_an_out_of_range_gap_is_refused_before_the_thread(self):
        from realtime.services.event_folder import validate_persistence_config

        with pytest.raises(ValueError):
            validate_persistence_config(
                {"fold_window_seconds": 600, "retention_days": 7},
                {"max_event_duration_seconds": 300, "max_inter_alert_gap_seconds": 99999},
            )


class TestNullBoundsAreRefusedNotLeaked:
    """``null`` is the value most likely to be typed by hand, and a bare
    ``float(None)`` raises ``TypeError`` — which neither caller catches. The
    pipeline would disable folding silently; the read path would answer 500."""

    def _cfg(self, persistence=None, consolidation=None):
        p = {"fold_interval_seconds": 30, "fold_window_seconds": 600, "retention_days": 7}
        c = {"max_event_duration_seconds": 300, "max_inter_alert_gap_seconds": 60}
        p.update(persistence or {})
        c.update(consolidation or {})
        return p, c

    @pytest.mark.parametrize("key", [
        "fold_window_seconds", "fold_interval_seconds", "retention_days",
    ])
    def test_a_null_persistence_bound_raises_value_error(self, key):
        from realtime.services.event_folder import validate_persistence_config

        with pytest.raises(ValueError):
            validate_persistence_config(*self._cfg(persistence={key: None}))

    def test_a_null_inter_alert_gap_raises_value_error(self):
        from realtime.services.event_folder import validate_persistence_config

        with pytest.raises(ValueError):
            validate_persistence_config(
                *self._cfg(consolidation={"max_inter_alert_gap_seconds": None})
            )

class TestLockRegressionsArePinned:
    """The two behaviours the previous round changed, each with a test, so a
    refactor cannot undo either silently."""

    def test_the_same_owner_string_cannot_take_a_live_lease(self, es):
        """The same-owner bypass was removed because two live processes can
        present one identifier; restoring it must fail here."""
        store = RealtimeEventStore(es)
        store.ensure_index()
        assert store.acquire_lock("folder-shared-name", ttl_seconds=90) is not None

        assert store.acquire_lock("folder-shared-name", ttl_seconds=90) is None, (
            "a holder's own identifier was accepted as grounds to re-take a "
            "live lease, which is indistinguishable from a second process "
            "stealing it"
        )

    def test_renewal_of_a_lease_someone_else_took_is_refused(self, es, folder):
        """Renewal is a conditional write. Unconditional, a folder that has
        already lost its lease reclaims it on the next tick and both instances
        fold and delete concurrently, with no abort ever logged."""
        store = folder._store
        store.ensure_index()
        mine = store.acquire_lock(folder._owner, ttl_seconds=90)
        assert mine is not None

        # Someone else takes over once the lease lapses.
        es.client.docs[store.index]["_fold_lock"]["expiresAt"] = 0
        assert store.acquire_lock("a-different-instance", ttl_seconds=90) is not None

        assert store.renew_lock(folder._owner, mine, ttl_seconds=90) is None, (
            "a folder renewed a lease that another instance now holds"
        )

    def test_a_cycle_that_lost_its_lease_deletes_nothing(self, es, folder):
        es.client.raw = [chunk(0, offset_s=0), chunk(1, offset_s=200)]
        folder.run_once(now=BASE + timedelta(seconds=400))
        before = dict(stored_events(es))
        deletes = {"n": 0}
        real_delete = folder._store.delete
        folder._store.delete = lambda *a, **k: deletes.__setitem__("n", deletes["n"] + 1) or 0
        folder._store.renew_lock = lambda *a, **k: None
        always_recheck_the_lease(folder)

        result = folder.run_once(now=BASE + timedelta(seconds=420))

        assert result.aborted
        assert deletes["n"] == 0
        assert stored_events(es) == before


class TestSuccessionRanking:
    def test_the_strongest_claim_inherits_the_ordering_key(self, es, folder):
        """Which recomputed event succeeds a stored one decides whose
        ``createdAt`` is inherited. The rule is "most members shared";
        reversed, the key migrates to the event with least to do with it."""
        stored = [{
            "Id": "evt-A", "chunk_ids": ["c1", "c2", "c3"],
            "createdAt": "2026-03-01T11:00:00.000Z",
        }]
        candidates = [
            {"Id": "evt-weak", "chunk_ids": ["c3"], "timestamp": "2026-03-01T12:00:00.000Z"},
            {"Id": "evt-strong", "chunk_ids": ["c1", "c2"], "timestamp": "2026-03-01T12:00:00.000Z"},
        ]

        events = folder._reconcile_ids(candidates, stored)

        assert events[1]["createdAt"] == "2026-03-01T11:00:00.000Z", (
            "the ordering key was inherited from the wrong predecessor"
        )
        assert events[0]["createdAt"] == "2026-03-01T12:00:00.000Z"


class TestChunkMetaContract:
    def _events(self, es, chunks):
        service = IncidentService(
            es_client=es, index_base="mdx-vlm-incidents",
            consolidation={
                "max_inter_alert_gap_seconds": 60,
                "max_event_duration_seconds": 300,
                "representative": "latest",
            },
        )
        return service.consolidate(chunks)

    def test_members_are_listed_in_chronological_order(self, es):
        """It is the traceability record that outlives the raw evidence, so its
        order is part of the contract rather than an accident of the scan."""
        out = self._events(es, [
            chunk(2, offset_s=60), chunk(0, offset_s=0), chunk(1, offset_s=30),
        ])

        stamps = [m["timestamp"] for m in out[0]["chunk_meta"]]
        assert stamps == sorted(stamps)

    def test_a_member_with_no_end_falls_back_rather_than_carrying_none(self, es):
        """The raw copy is gone by the time anyone reads this, so a null here
        cannot be repaired from anywhere."""
        one = chunk(0, offset_s=0)
        one.pop("end")
        out = self._events(es, [one])

        assert out[0]["chunk_meta"][0]["end"] is not None


class TestBoundaryHelpers:
    def test_an_event_ending_exactly_on_the_window_start_is_kept(self):
        """The comparison exists for this case and nothing else."""
        from realtime.services.event_folder import _at_or_after

        assert _at_or_after(iso(BASE), BASE) is True
        assert _at_or_after(iso(BASE - timedelta(milliseconds=1)), BASE) is False

    def test_an_instant_keeps_its_milliseconds(self):
        """Truncating to whole seconds would move a boundary comparison by up
        to a second, which is the resolution these comparisons work at."""
        from realtime.services.event_folder import _iso

        assert _iso(BASE + timedelta(milliseconds=250)).endswith(".250Z")


class TestRenewalIsBounded:
    def test_every_renewal_advances_the_lease_clock(self, es, folder):
        """Asserted directly rather than inferred from a renewal count.

        A renewal that does not reset the clock leaves the next check due at
        once, so every group renews — the per-group write hotspot the lease
        clock replaced, a thousand synchronous writes to one document at five
        hundred groups. Renewal counts for the two variants differ by one on a
        realistic fixture, which is not a difference a test can rest on; this
        is.
        """
        es.client.raw = [chunk(i, sensor=f"cam-{i}", offset_s=0) for i in range(4)]
        folder._renew_due = lambda **_kwargs: True
        observed = []
        real_renew = folder._store.renew_lock

        def watch(*args, **kwargs):
            observed.append(folder._renewed_at)
            return real_renew(*args, **kwargs)

        folder._store.renew_lock = watch
        folder.run_once(now=BASE + timedelta(seconds=120))

        assert len(observed) >= 2, "too few renewals to check the clock moved"
        assert len(set(observed)) == len(observed), (
            "two renewals saw the same lease clock, so at least one did not "
            "reset it and every later check falls due immediately"
        )


class TestArrivalOrderInvariantIdentity:
    """The property the spec asks for, checked as the spec states it: the same
    evidence set delivered in a different order must yield the same identity."""

    def _fold(self, arrivals):
        """Fold a list of (chunk-list, now-offset) rounds into a fresh store."""
        es = FakeEsClient()
        service = IncidentService(
            es_client=es, index_base="mdx-vlm-incidents",
            consolidation={
                "max_inter_alert_gap_seconds": 60,
                "max_event_duration_seconds": 300,
                "representative": "latest",
            },
        )
        store = RealtimeEventStore(es, collection="alert-realtime-events")
        folder = RealtimeEventFolder(
            service, store, es, "mdx-vlm-incidents-*",
            fold_interval_seconds=30, fold_window_seconds=600,
        )
        for chunks, elapsed in arrivals:
            es.client.raw = list(chunks)
            folder.run_once(now=BASE + timedelta(seconds=elapsed))
        return es, store, folder

    def test_the_same_evidence_in_a_different_order_yields_the_same_identity(self):
        late = chunk(0, offset_s=60)
        early_first = [chunk(1, offset_s=100), chunk(2, offset_s=150)]
        whole = [late] + early_first

        # One deployment sees the later evidence, folds, then receives the
        # earlier chunk. Another receives everything before its first fold.
        es_a, _, _ = self._fold([(early_first, 200), (whole, 220)])
        es_b, _, _ = self._fold([(whole, 220)])

        ids_a = sorted(e["Id"] for e in stored_events(es_a).values())
        ids_b = sorted(e["Id"] for e in stored_events(es_b).values())

        assert ids_a == ids_b, (
            "identity depends on the order evidence arrived in, so two "
            "deployments fed the same set disagree about what the event is"
        )

    def test_the_stored_identity_matches_the_computed_view(self):
        """This service's two surfaces must not disagree about what an event
        is. Freezing the id made them: the store remembered while
        ``?consolidate=true`` re-derived."""
        whole = [chunk(0, offset_s=60), chunk(1, offset_s=100)]
        es, _, folder = self._fold([([whole[1]], 200), (whole, 220)])

        stored = sorted(e["Id"] for e in stored_events(es).values())
        computed = sorted(e["Id"] for e in folder._svc.consolidate(list(es.client.raw)))

        assert stored == computed

    def test_repeated_folding_of_unchanged_evidence_is_stable(self):
        """The other half of the same requirement: while the evidence does not
        change, neither may the identity."""
        whole = [chunk(0, offset_s=0), chunk(1, offset_s=30)]
        es, _, folder = self._fold([(whole, 120), (whole, 150), (whole, 180)])

        assert len(stored_events(es)) == 1


class TestAliasesCarryOldReferences:
    def test_an_alias_is_not_written_for_a_write_that_failed(self, es, folder):
        """An alias pointing at a document that was never created resolves to
        nothing, which is worse than one that still resolves to the old id."""
        es.client.raw = [chunk(1, offset_s=100)]
        folder.run_once(now=BASE + timedelta(seconds=200))
        original_id = next(iter(stored_events(es)))

        es.client.raw.insert(0, chunk(0, offset_s=60))
        # Only the event write fails, not everything: rejecting the whole batch
        # would fail the alias write too, and the test would pass without the
        # guard it is meant to be checking.
        doomed = folder._svc.consolidate(list(es.client.raw))[0]["Id"]
        es.client.reject_next_bulk_item = doomed
        result = folder.run_once(now=BASE + timedelta(seconds=220))
        es.client.reject_next_bulk_item = None

        assert result.failed >= 1, "the premise is that the event write failed"
        assert result.aliases == 0
        docs = es.client.docs[folder._store.index]
        assert not any(v.get("_docKind") == "alias" for v in docs.values()), (
            "an alias was written pointing at an event that never landed, so "
            "the reference it carries resolves to nothing"
        )
        assert original_id in docs, "the caller's reference was orphaned"

    def test_an_alias_is_never_reaped_later_than_its_target(self, es):
        """The ordering is the point, so the cutoff has to fall between them.

        An alias stamped with the time it was minted outlives an event that
        ended long ago by the whole retention period, leaving a reference that
        resolves to a document retention has already removed. Purging with a
        far-future cutoff removes both and proves nothing.
        """
        store = RealtimeEventStore(es)
        store.ensure_index()
        long_ago = BASE - timedelta(days=10)
        store.upsert([{
            "Id": "evt-new", "sensorId": "cam-1", "category": "alert",
            "timestamp": iso(long_ago), "end": iso(long_ago), "createdAt": iso(long_ago),
        }])
        store.write_aliases([("evt-old", "evt-new", iso(long_ago))])
        docs = es.client.docs[store.index]
        assert "_alias-evt-old" in docs and "evt-new" in docs

        # A cutoff the event falls behind but a "minted now" alias would not.
        store.purge_older_than(iso(BASE - timedelta(days=7)))

        docs = es.client.docs[store.index]
        assert "evt-new" not in docs, "the event should have been reaped"
        assert "_alias-evt-old" not in docs, (
            "the alias outlived its target, so the reference it carries now "
            "resolves to a document that is gone"
        )

    def test_the_alias_a_fold_writes_carries_the_events_end(self, es, folder):
        es.client.raw = [chunk(1, offset_s=100)]
        folder.run_once(now=BASE + timedelta(seconds=200))
        es.client.raw.insert(0, chunk(0, offset_s=60))
        folder.run_once(now=BASE + timedelta(seconds=220))

        docs = es.client.docs[folder._store.index]
        alias = next(v for v in docs.values() if v.get("_docKind") == "alias")
        target = docs[alias["to"]]
        assert alias["end"] == target["end"], (
            "the alias was stamped with the time it was minted, not with the "
            "end of the event it points at"
        )



class TestAMergeAliasesEveryIdItConsumes:
    def test_two_events_merging_leave_two_aliases(self, es, folder):
        """A merge absorbs more than one stored record.

        Aliases used to be emitted from the ``createdAt`` match, which picks a
        single predecessor per recomputed event — so a merge of two stored
        events produced one alias and orphaned the other id, in exactly the
        case the alias exists for.
        """
        # 0..30 and 120..150 — a 90 s gap, wider than the 60 s bound.
        es.client.raw = [chunk(0, offset_s=0), chunk(2, offset_s=120)]
        folder.run_once(now=BASE + timedelta(seconds=300))
        before = set(stored_events(es))
        assert len(before) == 2, "the fixture must start as two separate events"

        # 60..90 closes both gaps to 30 s, so all three become one event.
        es.client.raw.append(chunk(1, offset_s=60))
        folder.run_once(now=BASE + timedelta(seconds=320))

        after = set(stored_events(es))
        assert len(after) == 1, f"the two events did not merge: {after}"
        survivor = next(iter(after))

        docs = es.client.docs[folder._store.index]
        aliased = {v["from"]: v["to"] for v in docs.values() if v.get("_docKind") == "alias"}
        gone = before - after
        assert gone <= set(aliased), (
            f"ids consumed by the merge with no alias: {sorted(gone - set(aliased))}"
        )
        for old_id in gone:
            assert aliased[old_id] == survivor

    def test_an_alias_points_at_the_event_holding_most_of_its_evidence(self, es, folder):
        es.client.raw = [chunk(i, offset_s=i * 30) for i in range(3)]
        folder.run_once(now=BASE + timedelta(seconds=200))
        original = next(iter(stored_events(es)))

        es.client.raw.insert(0, chunk(9, offset_s=-60))
        folder.run_once(now=BASE + timedelta(seconds=220))

        docs = es.client.docs[folder._store.index]
        aliases = {v["from"]: v["to"] for v in docs.values() if v.get("_docKind") == "alias"}
        assert original in aliases
        target = docs[aliases[original]]
        assert set(target["chunk_ids"]) >= {"cam-1-alert-0", "cam-1-alert-1", "cam-1-alert-2"}


class TestSettlesAtIsTheLifecycleContract:
    """The persisted document has to let its consumer answer "can this still
    change?" without being handed the fold's configuration."""

    def test_every_written_event_carries_settles_at(self, es, folder):
        es.client.raw = [chunk(0, offset_s=0)]
        folder.run_once(now=BASE + timedelta(seconds=120))

        event = next(iter(stored_events(es).values()))
        assert event.get("settlesAt"), (
            "the document offers no way to tell whether it can still change"
        )

    def test_settles_at_is_the_newest_instant_plus_the_reach_of_a_cycle(self, es, folder):
        es.client.raw = [chunk(0, offset_s=0), chunk(1, offset_s=30)]
        folder.run_once(now=BASE + timedelta(seconds=120))

        event = next(iter(stored_events(es).values()))
        expected = BASE + timedelta(seconds=60 + folder.rewrite_horizon_seconds)
        assert event["settlesAt"] == iso(expected), (
            f"expected {iso(expected)} (newest member ends at +60s, horizon "
            f"{folder.rewrite_horizon_seconds}s), got {event['settlesAt']}"
        )

    def test_settles_at_moves_when_the_event_grows(self, es, folder):
        es.client.raw = [chunk(0, offset_s=0)]
        folder.run_once(now=BASE + timedelta(seconds=100))
        first = next(iter(stored_events(es).values()))["settlesAt"]

        es.client.raw.append(chunk(1, offset_s=40))
        folder.run_once(now=BASE + timedelta(seconds=120))

        second = next(iter(stored_events(es).values()))["settlesAt"]
        assert second > first, (
            "later evidence did not push the settle instant out, so a consumer "
            "would treat a still-growing event as final"
        )

    def test_a_settled_event_is_never_touched_again(self, es, folder):
        """The promise settlesAt makes, checked against the folder itself.

        Whatever the field says, the folder must not rewrite an event after
        that instant — otherwise a consumer that cached on it is wrong.
        """
        es.client.raw = [chunk(0, offset_s=0)]
        folder.run_once(now=BASE + timedelta(seconds=100))
        stored = dict(stored_events(es))
        settles = _parse_iso_for_test(next(iter(stored.values()))["settlesAt"])

        # Keep the group alive well past that instant with unrelated evidence,
        # so cycles keep running and would touch it if they could reach it.
        es.client.raw.append(chunk(9, offset_s=2000))
        folder.run_once(now=settles + timedelta(seconds=1))
        folder.run_once(now=settles + timedelta(seconds=60))

        after = stored_events(es)
        for event_id, before in stored.items():
            assert event_id in after, f"{event_id} vanished after it had settled"
            assert after[event_id] == before, f"{event_id} changed after it had settled"

    def test_the_mapping_declares_settles_at(self, es):
        from realtime.services.event_store import _MAPPING

        assert _MAPPING["properties"]["settlesAt"]["type"] == "date"
        assert "status" not in _MAPPING["properties"], (
            "a stored status would be pinned at 'open' for the record's life"
        )


def _parse_iso_for_test(value):
    from datetime import datetime
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
