# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for the search_core error hierarchy."""

from __future__ import annotations

from lib.search_core.errors import BackendUnreachableError
from lib.search_core.errors import ConfigurationError
from lib.search_core.errors import IndexNotFoundError
from lib.search_core.errors import InvalidInputError
from lib.search_core.errors import SearchError


def test_backend_unreachable_backend_and_message():
    err = BackendUnreachableError("elasticsearch", "connection refused")
    assert err.backend == "elasticsearch"
    assert str(err) == "elasticsearch: connection refused"


def test_backend_unreachable_preserves_cause():
    cause = ConnectionError("boom")
    err = BackendUnreachableError("vst", "unreachable", cause)
    assert err.__cause__ is cause


def test_index_not_found_message_for_str():
    err = IndexNotFoundError("my_index")
    assert err.index == "my_index"
    assert "'my_index'" in str(err)
    assert "does not exist" in str(err)


def test_index_not_found_message_for_list():
    err = IndexNotFoundError(["idx_a", "idx_b"])
    assert err.index == ["idx_a", "idx_b"]
    assert "'idx_a, idx_b'" in str(err)


def test_index_not_found_carries_backend_and_cause():
    cause = RuntimeError("404")
    err = IndexNotFoundError("idx", cause)
    assert err.backend == "elasticsearch"
    assert err.__cause__ is cause


def test_index_not_found_keeps_target_and_available_diagnostics():
    target = ["mdx-embed-filtered-*", "-mdx-embed-filtered-2025-01-01"]
    err = IndexNotFoundError(target, available_indices=["mdx-embed-filtered-2025-02-01"])

    assert err.index == target
    assert err.available_indices == ("mdx-embed-filtered-2025-02-01",)
    assert "Available MDX embed indexes: mdx-embed-filtered-2025-02-01" in str(err)


def test_isinstance_hierarchy():
    err = IndexNotFoundError("idx")
    assert isinstance(err, IndexNotFoundError)
    assert isinstance(err, BackendUnreachableError)
    assert isinstance(err, SearchError)
    assert isinstance(err, Exception)


def test_index_not_found_caught_before_generic_backend():
    # A handler distinguishing the two must catch IndexNotFoundError first, since
    # it is a subclass of BackendUnreachableError.
    try:
        raise IndexNotFoundError("idx")
    except IndexNotFoundError:
        caught = "index"
    except BackendUnreachableError:  # pragma: no cover - must not be reached
        caught = "backend"
    assert caught == "index"


def test_other_errors_are_search_errors():
    for err in (ConfigurationError("x"), InvalidInputError("x")):
        assert isinstance(err, SearchError)


def test_invalid_input_not_backend_unreachable():
    assert not isinstance(InvalidInputError("x"), BackendUnreachableError)
