# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Content contracts do not assume cohort size, one chunk or one vector."""

from copy import deepcopy

import pytest

from vss_core.search_core.content_validation import ContentInspector
from vss_core.search_core.content_validation import validate_content_contract
from vss_core.search_core.errors import InvalidInputError


def contract():
    return {"schema_version": 1, "embedding": {"model": "chosen-model", "dimensions": 2, "unit_normalized": True},
            "frame_counts": {"clip": 12}, "document_limit": 2000}


def embedding(vectors=None):
    return {"_index": "embed", "_id": "chunk1", "_source": {"sensor": {"id": "uuid"}, "llm": {
        "info": {"requestedModel": "chosen-model", "modelId": "loaded-model"},
        "visionEmbeddings": [{"vector": v} for v in (vectors if vectors is not None else [[1., 0.]])]}}}


def inspector(kind="embedding", value=None):
    return ContentInspector(validate_content_contract(value or contract(), ["clip"], True), {"clip": ["clip", "uuid"]}, kind)


def test_multiple_chunks_and_nested_embedding_records():
    check = inspector()
    check.visit(embedding([[1., 0.], [0., 1.]]))
    check.visit(embedding())
    assert check.result["documents_checked"] == 2
    assert check.result["vectors_checked"] == 3
    assert check.result["violation_count"] == 0
    assert check.result["per_source"]["clip"] == {"documents": 2, "vectors": 3, "violations": 0}


@pytest.mark.parametrize("vector", [[], [1], [float("nan"), 0], [float("inf"), 0], [True, 0], ["1", 0], [[1], [0]], [0, 0], [2, 0], [10**400, 0]])
def test_invalid_vectors(vector):
    check = inspector()
    check.visit(embedding([vector]))
    assert check.result["violation_count"] == 1


def test_wrong_requested_model_does_not_pass_matching_model_id():
    hit = embedding()
    hit["_source"]["llm"]["info"] = {"requestedModel": "wrong", "modelId": "chosen-model"}
    check = inspector()
    check.visit(hit)
    assert check.result["violation_count"] == 1


def test_norm_contract_is_explicit():
    value = contract()
    value["embedding"]["unit_normalized"] = False
    check = inspector(value=value)
    check.visit(embedding([[2, 0]]))
    assert not check.result["violation_count"]
    check.visit(embedding([[0, 0]]))
    assert check.result["violation_count"] == 1
    value["embedding"]["unit_normalized"] = True
    value["embedding"]["norm_tolerance"] = 0.01
    check = inspector(value=value)
    check.visit(embedding([[1.005, 0]]))
    assert not check.result["violation_count"]


@pytest.mark.parametrize("frame,valid", [(0, True), ("0", True), (11, True), ("00011", True), (12, False), (-1, False), (True, False), (1.0, False), ("1.0", False), ("+1", False), ("9"*100, False), (None, False)])
def test_frame_id_bounds_and_strict_types(frame, valid):
    check = inspector("raw")
    check.visit({"_index": "raw", "_id": "f", "_source": {"sensorId": "clip", "id": frame}})
    assert (check.result["violation_count"] == 0) is valid


@pytest.mark.parametrize("mutation", [
    lambda c: c.update(extra=True), lambda c: c.update(schema_version=True),
    lambda c: c["embedding"].pop("model"), lambda c: c["embedding"].update(dimensions=True),
    lambda c: c["embedding"].update(unit_normalized=1), lambda c: c["embedding"].update(norm_tolerance=float("nan")),
    lambda c: c.update(frame_counts={}), lambda c: c.update(frame_counts={"clip": True}),
    lambda c: c.update(document_limit=True), lambda c: c.update(frame_counts={"other": 12}),
])
def test_contract_rejects_ambiguous_or_invalid_expectations(mutation):
    value = deepcopy(contract())
    mutation(value)
    with pytest.raises(InvalidInputError):
        validate_content_contract(value, ["clip"], True)


def test_embed_only_omits_frame_bounds_and_examples_are_bounded():
    value = contract()
    del value["frame_counts"]
    validate_content_contract(value, ["clip"], False)
    check = inspector()
    for _ in range(100):
        check.visit(embedding([[2, 0]]))
    assert check.result["violation_count"] == 100
    assert len(check.result["examples"]) == 50
    assert "vector" not in str(check.result["examples"])
