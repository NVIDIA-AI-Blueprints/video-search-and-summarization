# SPDX-FileCopyrightText: Copyright (c) 2024-2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""Thin adapter around ``vss_ctx_rag.context_manager.ContextManager``.

Isolates the via-ctx-rag wheel integration from ViaStreamHandler so that the
RAG boundary can be mocked independently in integration and unit tests without
importing the wheel at test-collection time.

Usage (production)::

    from rag_adapter import RagAdapter
    from vss_ctx_rag.context_manager import ContextManager

    ctx_mgr = ContextManager(config=cfg, process_index=idx)
    adapter = RagAdapter(ctx_mgr)
    adapter.add_doc(caption, doc_i=0, doc_meta={...})
    result = adapter.call({"summarization": {...}})

Usage (tests)::

    adapter = MagicMock(spec=RagAdapter)
    adapter.call.return_value = {"summarization": {"result": "summary", "metadata": {}}}
"""

from via_exception import ViaException


class RagAdapter:
    """Wraps a ``ContextManager`` instance, converting its exceptions to :class:`ViaException`.

    All public methods preserve the exact call signatures used by
    ``ViaStreamHandler`` so that swapping ``ContextManager`` for ``RagAdapter``
    is a pure refactor with no behaviour change.
    """

    def __init__(self, ctx_mgr) -> None:
        self._ctx_mgr = ctx_mgr

    # ------------------------------------------------------------------
    # Document ingestion
    # ------------------------------------------------------------------

    @property
    def process(self):
        """Expose the underlying ContextManager process for lifecycle management."""
        return self._ctx_mgr.process

    @property
    def _process_index(self):
        """Expose the underlying ContextManager process index for logging.

        Accesses the private ``_process_index`` attribute of ContextManager
        because no public API exists for it.  Track upstream for a public
        alternative.
        """
        return self._ctx_mgr._process_index

    def add_doc(self, doc, doc_i, doc_meta, callback=None):
        """Ingest a caption or transcript chunk into the RAG store."""
        try:
            self._ctx_mgr.add_doc(doc, doc_i=doc_i, doc_meta=doc_meta, callback=callback)
        except Exception as exc:
            raise ViaException(f"RAG add_doc failed: {exc}", "RagAdapterError", 500) from exc

    # ------------------------------------------------------------------
    # RAG operations (summarize / ingest / retrieve)
    # ------------------------------------------------------------------

    def configure(self, config):
        """Configure the underlying context manager for a new request.

        Args:
            config: Full CA-RAG config dict with uuid set to the stream ID.

        Raises:
            ViaException: If the underlying ContextManager raises any exception.
        """
        try:
            self._ctx_mgr.configure(config)
        except Exception as exc:
            raise ViaException(f"RAG configure failed: {exc}", "RagAdapterError", 500) from exc

    def call(self, config):
        """Execute a RAG operation (summarize, ingest, or retrieve).

        Args:
            config: Operation config dict, e.g.
                ``{"summarization": {"start_index": 0, "end_index": 5}}``.

        Returns:
            dict: Result dict from the underlying ContextManager.

        Raises:
            ViaException: If the underlying ContextManager raises any exception.
        """
        try:
            return self._ctx_mgr.call(config)
        except Exception as exc:
            raise ViaException(f"RAG call failed: {exc}", "RagAdapterError", 500) from exc

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def reset(self, expr=None):
        """Reset the underlying context manager.

        Args:
            expr: Reset expression dict, e.g.
                ``{"summarization": {"uuid": "..."}, "retriever_function": {"uuid": "..."}}``.

        Raises:
            ViaException: If the underlying ContextManager raises any exception.
        """
        try:
            if hasattr(self._ctx_mgr, "reset"):
                if expr is not None:
                    self._ctx_mgr.reset(expr)
                else:
                    self._ctx_mgr.reset()
        except Exception as exc:
            raise ViaException(f"RAG reset failed: {exc}", "RagAdapterError", 500) from exc

    def drop_collection(self):
        """Drop the Elasticsearch / vector / graph collection associated with
        the underlying ContextManager's currently-configured uuid.

        Used by ``ViaStreamHandler.drop_collection_for_asset`` so that
        completed file summarize requests can return their per-file
        ``default_<file_id>`` index to the cluster shard pool. Idempotent
        on a missing collection.

        Raises:
            ViaException: If the underlying ContextManager raises any exception.
        """
        try:
            return self._ctx_mgr.drop_collection()
        except Exception as exc:
            raise ViaException(
                f"RAG drop_collection failed: {exc}", "RagAdapterError", 500
            ) from exc

    def __repr__(self) -> str:  # pragma: no cover
        return f"RagAdapter({self._ctx_mgr!r})"
