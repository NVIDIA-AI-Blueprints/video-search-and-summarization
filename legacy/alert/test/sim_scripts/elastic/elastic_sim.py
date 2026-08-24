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

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, Optional, Any

from flask import Flask, jsonify, request


@dataclass
class StoredDocument:
    index: str
    document: Dict[str, Any]
    id: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    version: int = 1


class ElasticSimState:
    def __init__(self) -> None:
        self.indices: Dict[str, Dict[str, Any]] = {}
        self.documents: Dict[str, Dict[str, StoredDocument]] = {}
        self.lock = threading.Lock()

    def ensure_index(self, index: str, settings: Optional[Dict[str, Any]], mappings: Optional[Dict[str, Any]]) -> bool:
        with self.lock:
            already_exists = index in self.indices
            if not already_exists:
                self.indices[index] = {
                    "settings": settings or {},
                    "mappings": mappings or {},
                    "created_at": time.time(),
                }
                self.documents[index] = {}
            return already_exists

    def has_index(self, index: str) -> bool:
        with self.lock:
            return index in self.indices

    def delete_index(self, index: str) -> bool:
        with self.lock:
            if index in self.indices:
                del self.indices[index]
                self.documents.pop(index, None)
                return True
            return False

    def store_document(
        self,
        index: str,
        doc_id: Optional[str],
        document: Dict[str, Any],
        op_type: Optional[str] = None,
    ) -> StoredDocument:
        """Store or overwrite a document.

        ``op_type="create"`` enforces create-only semantics: raises
        ``ValueError`` when the document already exists so the caller
        can return a 409 to match real Elasticsearch (the alert-config ES hydration).
        """
        with self.lock:
            if index not in self.indices:
                raise KeyError(index)
            idx_docs = self.documents.setdefault(index, {})
            if not doc_id:
                doc_id = f"auto-{len(idx_docs) + 1}"
            if op_type == "create" and doc_id in idx_docs:
                raise ValueError(doc_id)
            previous = idx_docs.get(doc_id)
            next_version = (previous.version + 1) if previous is not None else 1
            stored = StoredDocument(
                index=index,
                document=document,
                id=doc_id,
                version=next_version,
            )
            idx_docs[doc_id] = stored
            return stored

    def get_document(self, index: str, doc_id: str) -> Optional[StoredDocument]:
        with self.lock:
            return self.documents.get(index, {}).get(doc_id)

    def delete_document(self, index: str, doc_id: str) -> bool:
        with self.lock:
            idx_docs = self.documents.get(index, {})
            if doc_id in idx_docs:
                del idx_docs[doc_id]
                return True
            return False

    def list_indices(self) -> Dict[str, Any]:
        with self.lock:
            return {
                name: {
                    "settings": meta["settings"],
                    "mappings": meta["mappings"],
                    "documents": len(self.documents.get(name, {})),
                    "created_at": meta["created_at"],
                }
                for name, meta in self.indices.items()
            }


def _resolve_field(source: Dict[str, Any], field: str) -> Any:
    """Resolve a dotted or ``.keyword``-suffixed field path in a document."""
    field = field.removesuffix(".keyword")
    parts = field.split(".")
    cur: Any = source
    for p in parts:
        if isinstance(cur, dict):
            cur = cur.get(p)
        else:
            return None
    return cur


def _doc_matches_term(doc: Dict[str, Any], term: Dict[str, Any]) -> bool:
    source = doc.get("_source", {})
    for field, value in term.items():
        if _resolve_field(source, field) != value:
            return False
    return True


def _apply_query(docs: list, query: Dict[str, Any]) -> list:
    """Apply a minimal subset of ES query DSL (term, bool.must)."""
    if not query:
        return docs

    if "term" in query:
        return [d for d in docs if _doc_matches_term(d, query["term"])]

    if "bool" in query:
        must = query["bool"].get("must", [])
        result = docs
        for clause in must:
            result = _apply_query(result, clause)
        return result

    return docs


def create_app(state: Optional[ElasticSimState] = None) -> Flask:
    app = Flask(__name__)
    sim_state = state or ElasticSimState()

    @app.route("/", methods=["GET", "HEAD"])
    def root() -> Any:
        return (
            "OK",
            200,
            {
                "X-Elastic-Sim": "true",
                "X-Elastic-Product": "Elasticsearch",
            },
        )

    @app.route("/health", methods=["GET"])
    def health() -> Any:
        return jsonify({
            "status": "healthy",
            "indices": len(sim_state.indices),
        })

    @app.route("/status", methods=["GET"])
    def status() -> Any:
        return jsonify({
            "service": "Elastic Simulator",
            "indices": sim_state.list_indices(),
        })

    @app.route("/<index>", methods=["HEAD"])
    def index_head(index: str) -> Any:
        if sim_state.has_index(index):
            return ("", 200)
        return ("", 404)

    @app.route("/<index>", methods=["PUT"])
    def index_put(index: str) -> Any:
        body = request.get_json(silent=True) or {}
        settings = body.get("settings") if isinstance(body, dict) else {}
        mappings = body.get("mappings") if isinstance(body, dict) else {}

        existed = sim_state.ensure_index(index, settings, mappings)
        status_code = 200 if existed else 201
        return jsonify({
            "acknowledged": True,
            "shards_acknowledged": True,
            "index": index,
            "created": not existed,
        }), status_code

    @app.route("/<index>", methods=["DELETE"])
    def index_delete(index: str) -> Any:
        deleted = sim_state.delete_index(index)
        if deleted:
            return jsonify({"acknowledged": True})
        return jsonify({"error": "index_not_found", "index": index}), 404

    @app.route("/<index>/_all", methods=["GET"])
    def index_get_all(index: str) -> Any:
        """Return all documents in an index (test verification endpoint)."""
        with sim_state.lock:
            if index not in sim_state.documents:
                return jsonify({"error": "index_not_found", "index": index}), 404
            docs = sim_state.documents.get(index, {})
            documents = [
                {"_id": doc.id, "_source": doc.document}
                for doc in docs.values()
            ]
            return jsonify({"documents": documents})

    @app.route("/<index>/_update/<doc_id>", methods=["POST"])
    def update_document(index: str, doc_id: str) -> Any:
        """Update a document (partial update via script or doc)."""
        body = request.get_json(force=True, silent=True)
        if not isinstance(body, dict):
            return jsonify({"error": "invalid_request"}), 400

        with sim_state.lock:
            if index not in sim_state.documents:
                return jsonify({"error": "index_not_found", "index": index}), 404
            
            idx_docs = sim_state.documents.get(index, {})
            if doc_id not in idx_docs:
                return jsonify({"error": "document_not_found", "index": index, "id": doc_id}), 404
            
            stored = idx_docs[doc_id]
            
            if "doc" in body:
                stored.document.update(body["doc"])
            elif "script" in body:
                pass
            
            app.logger.info(
                "ElasticSim: updated document for index=%s id=%s\n%s",
                index,
                doc_id,
                json.dumps(stored.document, indent=2, sort_keys=True),
            )

        return jsonify({
            "_index": index,
            "_id": doc_id,
            "result": "updated",
        }), 200

    @app.route("/<index>/_count", methods=["GET", "POST"])
    def index_count(index: str) -> Any:
        """Return document count for an index."""
        with sim_state.lock:
            idx_docs = sim_state.documents.get(index, {})
            count = len(idx_docs)
        return jsonify({"count": count})

    @app.route("/<index>/_search", methods=["GET", "POST"])
    def index_search(index: str) -> Any:
        """ES-compatible _search endpoint for IncidentService queries."""
        with sim_state.lock:
            matching_indices = []
            if "*" in index:
                prefix = index.replace("*", "")
                matching_indices = [
                    name for name in sim_state.documents if name.startswith(prefix)
                ]
            elif index in sim_state.documents:
                matching_indices = [index]

            all_docs = []
            for idx_name in matching_indices:
                for doc in sim_state.documents.get(idx_name, {}).values():
                    all_docs.append({
                        "_index": idx_name,
                        "_id": doc.id,
                        "_source": doc.document,
                    })

        body = request.get_json(silent=True) or {}
        from_ = body.get("from", request.args.get("from", 0, type=int))
        size = body.get("size", request.args.get("size", 10, type=int))

        query = body.get("query", {})
        all_docs = _apply_query(all_docs, query)

        sort_spec = body.get("sort", [])
        if sort_spec and isinstance(sort_spec, list):
            for s in reversed(sort_spec):
                if isinstance(s, dict):
                    for field_name, opts in s.items():
                        reverse = (opts.get("order", "asc") == "desc") if isinstance(opts, dict) else False
                        all_docs.sort(
                            key=lambda d, f=field_name: d["_source"].get(f, ""),
                            reverse=reverse,
                        )

        total = len(all_docs)
        page = all_docs[from_:from_ + size]

        return jsonify({
            "hits": {
                "total": {"value": total, "relation": "eq"},
                "hits": page,
            }
        })

    @app.route("/<index>/_doc", methods=["POST"])
    @app.route("/<index>/_doc/<doc_id>", methods=["GET", "POST", "PUT", "DELETE"])
    def index_document(index: str, doc_id: Optional[str] = None) -> Any:
        if request.method == "GET" and doc_id:
            with sim_state.lock:
                idx_docs = sim_state.documents.get(index, {})
                stored = idx_docs.get(doc_id)
            if stored is None:
                return jsonify({"_index": index, "_id": doc_id, "found": False}), 404
            return jsonify({
                "_index": index, "_id": doc_id, "found": True,
                "_source": stored.document,
                "_seq_no": 1, "_primary_term": 1, "_version": 1,
            })

        if request.method == "DELETE" and doc_id:
            with sim_state.lock:
                idx_docs = sim_state.documents.get(index, {})
                removed = idx_docs.pop(doc_id, None)
            if removed is None:
                return jsonify({"_index": index, "_id": doc_id, "result": "not_found"}), 404
            return jsonify({"_index": index, "_id": doc_id, "result": "deleted"})

        body = request.get_json(force=True, silent=True)
        if not isinstance(body, dict):
            return jsonify({"error": "invalid_document"}), 400

        pretty_body = json.dumps(body, indent=2, sort_keys=True)
        app.logger.info(
            "ElasticSim: incoming document for index=%s id=%s\n%s",
            index,
            doc_id or "<auto>",
            pretty_body,
        )

        sim_state.ensure_index(index, None, None)
        op_type = request.args.get("op_type")
        try:
            stored = sim_state.store_document(index, doc_id, body, op_type=op_type)
        except KeyError:
            return jsonify({"error": "index_not_found", "index": index}), 404
        except ValueError as exc:
            # op_type=create on an existing doc → ES returns 409
            return jsonify({
                "error": {
                    "type": "version_conflict_engine_exception",
                    "reason": f"[{exc}]: version conflict, document already exists",
                },
                "status": 409,
            }), 409

        refresh = request.args.get("refresh")
        if refresh and refresh not in {"wait_for", "true", "false"}:
            return jsonify({"error": "unsupported_refresh", "value": refresh}), 400

        response_body = {
            "_index": index,
            "_id": stored.id,
            "result": "created",
            "refresh": refresh or "wait_for",
            "_seq_no": 1,
            "_primary_term": 1,
        }
        return jsonify(response_body), 201

    @app.route("/<index>/_doc/<doc_id>", methods=["GET"])
    def get_document(index: str, doc_id: str) -> Any:
        stored = sim_state.get_document(index, doc_id)
        if stored is None:
            return jsonify({
                "_index": index,
                "_id": doc_id,
                "found": False,
            }), 404
        return jsonify({
            "_index": index,
            "_id": stored.id,
            "_version": stored.version,
            "_seq_no": stored.version - 1,
            "_primary_term": 1,
            "found": True,
            "_source": stored.document,
        })

    @app.route("/<index>/_doc/<doc_id>", methods=["DELETE"])
    def delete_document(index: str, doc_id: str) -> Any:
        deleted = sim_state.delete_document(index, doc_id)
        if not deleted:
            return jsonify({
                "_index": index,
                "_id": doc_id,
                "result": "not_found",
            }), 404
        return jsonify({
            "_index": index,
            "_id": doc_id,
            "result": "deleted",
        })

    return app


def main() -> None:
    app = create_app()
    port = int(os.getenv("ELASTIC_SIM_PORT", "9200"))
    app.run(host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()


