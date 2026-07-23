# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""
Unit tests for src/rag_adapter.py

Tests RagAdapter delegation, exception wrapping, and property forwarding
using a plain MagicMock as the underlying ContextManager — no vss_ctx_rag
wheel required.
"""

from unittest.mock import MagicMock, PropertyMock

import pytest

from rag_adapter import RagAdapter
from via_exception import ViaException

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_adapter():
    """Return a RagAdapter wrapping a fresh MagicMock ctx_mgr."""
    ctx_mgr = MagicMock()
    return RagAdapter(ctx_mgr), ctx_mgr


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRagAdapterInit:
    def test_stores_ctx_mgr(self):
        ctx_mgr = MagicMock()
        adapter = RagAdapter(ctx_mgr)
        assert adapter._ctx_mgr is ctx_mgr


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRagAdapterProperties:
    def test_process_delegates_to_ctx_mgr(self):
        ctx_mgr = MagicMock()
        sentinel = object()
        type(ctx_mgr).process = PropertyMock(return_value=sentinel)
        adapter = RagAdapter(ctx_mgr)
        assert adapter.process is sentinel

    def test_process_index_delegates_to_ctx_mgr(self):
        ctx_mgr = MagicMock()
        type(ctx_mgr)._process_index = PropertyMock(return_value=42)
        adapter = RagAdapter(ctx_mgr)
        assert adapter._process_index == 42


# ---------------------------------------------------------------------------
# add_doc
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRagAdapterAddDoc:
    def test_delegates_all_args(self):
        adapter, ctx_mgr = _make_adapter()
        cb = MagicMock()
        adapter.add_doc("caption text", doc_i=3, doc_meta={"ts": 1.0}, callback=cb)
        ctx_mgr.add_doc.assert_called_once_with(
            "caption text", doc_i=3, doc_meta={"ts": 1.0}, callback=cb
        )

    def test_callback_defaults_to_none(self):
        adapter, ctx_mgr = _make_adapter()
        adapter.add_doc("text", doc_i=0, doc_meta={})
        ctx_mgr.add_doc.assert_called_once_with("text", doc_i=0, doc_meta={}, callback=None)

    def test_wraps_exception_as_via_exception(self):
        adapter, ctx_mgr = _make_adapter()
        ctx_mgr.add_doc.side_effect = RuntimeError("db unavailable")
        with pytest.raises(ViaException) as exc_info:
            adapter.add_doc("text", doc_i=0, doc_meta={})
        assert "RAG add_doc failed" in str(exc_info.value)
        assert exc_info.value.status_code == 500
        assert exc_info.value.code == "RagAdapterError"

    def test_original_exception_chained(self):
        adapter, ctx_mgr = _make_adapter()
        original = RuntimeError("root cause")
        ctx_mgr.add_doc.side_effect = original
        with pytest.raises(ViaException) as exc_info:
            adapter.add_doc("text", doc_i=0, doc_meta={})
        assert exc_info.value.__cause__ is original


# ---------------------------------------------------------------------------
# configure
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRagAdapterConfigure:
    def test_delegates_config(self):
        adapter, ctx_mgr = _make_adapter()
        cfg = {"uuid": "stream-1", "summarization": {}}
        adapter.configure(cfg)
        ctx_mgr.configure.assert_called_once_with(cfg)

    def test_wraps_exception_as_via_exception(self):
        adapter, ctx_mgr = _make_adapter()
        ctx_mgr.configure.side_effect = ValueError("bad config")
        with pytest.raises(ViaException) as exc_info:
            adapter.configure({})
        assert "RAG configure failed" in str(exc_info.value)
        assert exc_info.value.status_code == 500
        assert exc_info.value.code == "RagAdapterError"

    def test_original_exception_chained(self):
        adapter, ctx_mgr = _make_adapter()
        original = ValueError("root cause")
        ctx_mgr.configure.side_effect = original
        with pytest.raises(ViaException) as exc_info:
            adapter.configure({})
        assert exc_info.value.__cause__ is original


# ---------------------------------------------------------------------------
# call
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRagAdapterCall:
    def test_delegates_config_and_returns_result(self):
        adapter, ctx_mgr = _make_adapter()
        expected = {"summarization": {"result": "summary text", "metadata": {}}}
        ctx_mgr.call.return_value = expected
        result = adapter.call({"summarization": {"start_index": 0}})
        ctx_mgr.call.assert_called_once_with({"summarization": {"start_index": 0}})
        assert result is expected

    def test_returns_none_when_ctx_mgr_returns_none(self):
        adapter, ctx_mgr = _make_adapter()
        ctx_mgr.call.return_value = None
        assert adapter.call({}) is None

    def test_wraps_exception_as_via_exception(self):
        adapter, ctx_mgr = _make_adapter()
        ctx_mgr.call.side_effect = ConnectionError("milvus down")
        with pytest.raises(ViaException) as exc_info:
            adapter.call({"summarization": {}})
        assert "RAG call failed" in str(exc_info.value)
        assert exc_info.value.status_code == 500
        assert exc_info.value.code == "RagAdapterError"

    def test_original_exception_chained(self):
        adapter, ctx_mgr = _make_adapter()
        original = ConnectionError("root cause")
        ctx_mgr.call.side_effect = original
        with pytest.raises(ViaException) as exc_info:
            adapter.call({})
        assert exc_info.value.__cause__ is original


# ---------------------------------------------------------------------------
# reset
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRagAdapterReset:
    def test_reset_with_expr_delegates_expr(self):
        adapter, ctx_mgr = _make_adapter()
        expr = {"summarization": {"uuid": "abc"}}
        adapter.reset(expr=expr)
        ctx_mgr.reset.assert_called_once_with(expr)

    def test_reset_without_expr_calls_reset_no_args(self):
        adapter, ctx_mgr = _make_adapter()
        adapter.reset()
        ctx_mgr.reset.assert_called_once_with()

    def test_reset_skipped_when_ctx_mgr_has_no_reset(self):
        ctx_mgr = MagicMock(spec=[])  # no attributes at all
        adapter = RagAdapter(ctx_mgr)
        # Should not raise — hasattr check guards the call
        adapter.reset()

    def test_reset_with_expr_skipped_when_no_reset_attr(self):
        ctx_mgr = MagicMock(spec=[])
        adapter = RagAdapter(ctx_mgr)
        adapter.reset(expr={"summarization": {}})  # should not raise

    def test_wraps_exception_as_via_exception(self):
        adapter, ctx_mgr = _make_adapter()
        ctx_mgr.reset.side_effect = OSError("cannot reset")
        with pytest.raises(ViaException) as exc_info:
            adapter.reset()
        assert "RAG reset failed" in str(exc_info.value)
        assert exc_info.value.status_code == 500
        assert exc_info.value.code == "RagAdapterError"

    def test_original_exception_chained(self):
        adapter, ctx_mgr = _make_adapter()
        original = OSError("root cause")
        ctx_mgr.reset.side_effect = original
        with pytest.raises(ViaException) as exc_info:
            adapter.reset()
        assert exc_info.value.__cause__ is original


# ---------------------------------------------------------------------------
# Spec compatibility — MagicMock(spec=RagAdapter) usable in tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRagAdapterMockSpec:
    """Verify that MagicMock(spec=RagAdapter) exposes the expected interface."""

    def test_mock_has_add_doc(self):
        mock = MagicMock(spec=RagAdapter)
        assert hasattr(mock, "add_doc")

    def test_mock_has_call(self):
        mock = MagicMock(spec=RagAdapter)
        assert hasattr(mock, "call")

    def test_mock_has_configure(self):
        mock = MagicMock(spec=RagAdapter)
        assert hasattr(mock, "configure")

    def test_mock_has_reset(self):
        mock = MagicMock(spec=RagAdapter)
        assert hasattr(mock, "reset")

    def test_mock_call_returns_configured_value(self):
        mock = MagicMock(spec=RagAdapter)
        mock.call.return_value = {"summarization": {"result": "ok", "metadata": {}}}
        result = mock.call({"summarization": {}})
        assert result["summarization"]["result"] == "ok"
