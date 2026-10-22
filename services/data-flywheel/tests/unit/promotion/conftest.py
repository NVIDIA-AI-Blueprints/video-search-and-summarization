# SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""Shared fakes for promotion controller/beat/service tests.

No mongomock is available in the environment, so we use a tiny in-memory Mongo
double supporting exactly the operations the promotion code uses (insert_one /
find_one / find / update_one with equality + ``$in`` predicates).
"""

from __future__ import annotations

import copy

import pytest


class FakeCollection:
    def __init__(self) -> None:
        self.docs: list[dict] = []

    @staticmethod
    def _match(doc: dict, query: dict) -> bool:
        for key, cond in query.items():
            if isinstance(cond, dict) and "$in" in cond:
                if doc.get(key) not in cond["$in"]:
                    return False
            elif isinstance(cond, dict) and "$nin" in cond:
                if doc.get(key) in cond["$nin"]:
                    return False
            elif doc.get(key) != cond:
                return False
        return True

    def insert_one(self, doc: dict):
        self.docs.append(copy.deepcopy(doc))
        return type("R", (), {"inserted_id": len(self.docs)})()

    def find_one(self, query: dict, sort: list[tuple[str, int]] | None = None):
        matches = [doc for doc in self.docs if self._match(doc, query)]
        if sort:
            # Right-to-left and stable, so the first key is the primary order.
            # Missing/``None`` fields sort LAST in either direction, so a
            # descending sort cannot pick a doc that lacks the field.
            for field, direction in reversed(sort):
                if direction < 0:
                    matches.sort(
                        key=lambda d, f=field: (d.get(f) is not None, d.get(f)),
                        reverse=True,
                    )
                else:
                    matches.sort(
                        key=lambda d, f=field: (d.get(f) is None, d.get(f)),
                    )
        return copy.deepcopy(matches[0]) if matches else None

    def find(self, query: dict | None = None, projection: dict | None = None):
        # ``projection`` is accepted for pymongo parity and ignored: the full
        # document is a superset.
        query = query or {}
        return [copy.deepcopy(d) for d in self.docs if self._match(d, query)]

    def update_one(self, query: dict, update):
        # ``update`` may be an operator dict OR a pipeline: the controller writes a
        # terminal state and its ``settled_at`` stamp as one pipeline update, which
        # a ``$set``-only fake would drop on the floor and still pass.
        matched = 0
        for doc in self.docs:
            if self._match(doc, query):
                matched = 1
                self._apply_update(doc, update)
                break
        return type("R", (), {"matched_count": matched, "modified_count": matched})()

    def update_many(self, query: dict, update):
        matched = 0
        for doc in self.docs:
            if self._match(doc, query):
                matched += 1
                self._apply_update(doc, update)
        return type("R", (), {"matched_count": matched, "modified_count": matched})()

    def find_one_and_update(self, query: dict, update, upsert: bool = False, return_document=None):
        # The serving-version counter shim: dict updates (``$inc`` + ``$set``) and
        # pipeline updates, with upsert, returning the AFTER document.
        # ``return_document`` is accepted for signature parity.
        for doc in self.docs:
            if self._match(doc, query):
                self._apply_update(doc, update)
                return copy.deepcopy(doc)
        if upsert:
            # Seed from the equality predicates (e.g. {"_id": model_name}).
            new_doc = {k: v for k, v in query.items() if not isinstance(v, dict)}
            self._apply_update(new_doc, update)
            self.docs.append(new_doc)
            return copy.deepcopy(new_doc)
        return None

    @staticmethod
    def _apply_update(doc: dict, update) -> None:
        if isinstance(update, list):  # aggregation-pipeline update
            for stage in update:
                # Every expression in a ``$set`` sees the stage's INPUT document,
                # never a field set beside it: the settle stamp compares against
                # the PRE-update ``state``, which the state write would shadow.
                before = dict(doc)
                for field, expr in stage.get("$set", {}).items():
                    doc[field] = FakeCollection._eval_expr(expr, before)
            return
        for key, amount in update.get("$inc", {}).items():
            doc[key] = doc.get(key, 0) + amount
        # The eviction stamp appends: a record can evict twice.
        for key, value in update.get("$addToSet", {}).items():
            existing = doc.setdefault(key, [])
            if value not in existing:
                existing.append(value)
        doc.update(update.get("$set", {}))
        for key in update.get("$unset", {}):
            doc.pop(key, None)

    @staticmethod
    def _eval_expr(expr, doc: dict):
        if isinstance(expr, dict):
            if "$add" in expr:
                return sum(FakeCollection._eval_expr(e, doc) for e in expr["$add"])
            if "$max" in expr:
                # Mongo's $max ignores missing/null operands; without that a
                # first-ever mint would compare int to None.
                values = [FakeCollection._eval_expr(e, doc) for e in expr["$max"]]
                present = [v for v in values if v is not None]
                return max(present) if present else None
            if "$literal" in expr:
                # Unevaluated: how the controller passes a value through a pipeline
                # ``$set`` without it reading as a field path.
                return expr["$literal"]
            if "$ifNull" in expr:
                target, fallback = expr["$ifNull"]
                val = FakeCollection._eval_expr(target, doc)
                return val if val is not None else FakeCollection._eval_expr(fallback, doc)
            if "$eq" in expr:
                left, right = (FakeCollection._eval_expr(e, doc) for e in expr["$eq"])
                return left == right
            if "$cond" in expr:
                # How the terminal write re-stamps ``settled_at`` on a state
                # CHANGE but not on a repeat of the same state.
                test, then, otherwise = expr["$cond"]
                branch = then if FakeCollection._eval_expr(test, doc) else otherwise
                return FakeCollection._eval_expr(branch, doc)
        if isinstance(expr, str) and expr.startswith("$"):
            return doc.get(expr[1:])
        return expr


class FakeDB:
    def __init__(self) -> None:
        self._cols: dict[str, FakeCollection] = {}

    def __getitem__(self, name: str) -> FakeCollection:
        return self._cols.setdefault(name, FakeCollection())

    def __getattr__(self, name: str) -> FakeCollection:
        # Like pymongo's ``Database``: collections are reachable by attribute as
        # well as by subscript. Dunder and private names must still raise.
        if name.startswith("_"):
            raise AttributeError(name)
        return self[name]


@pytest.fixture
def fake_db() -> FakeDB:
    return FakeDB()
