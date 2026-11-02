# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Content-addressed, append-only artifacts and sealed runs."""

from __future__ import annotations

import pytest

from evalv2.core.artifacts import ArtifactError, ArtifactRef, ArtifactStore, SealedRunError
from evalv2.core.hashing import canonical_json, sha256_json


def test_canonical_json_is_order_independent_and_refuses_nan():
    assert canonical_json({"b": 1, "a": [1, 2]}) == canonical_json({"a": [1, 2], "b": 1}) == b'{"a":[1,2],"b":1}'
    assert sha256_json({"x": "é"}) == sha256_json({"x": "é"})
    with pytest.raises(ValueError):
        canonical_json({"x": float("nan")})


def test_same_content_gives_the_same_reference_and_round_trips(tmp_path):
    store = ArtifactStore(tmp_path)
    first = store.put("prediction", {"incident": {"type": "assault"}, "n": 1})
    again = store.put("prediction", {"n": 1, "incident": {"type": "assault"}})
    assert first == again and store.get(first) == {"incident": {"type": "assault"}, "n": 1}
    assert store.put("prediction", {"n": 2}) != first
    assert ArtifactRef.parse(str(first)) == first


def test_kind_is_part_of_the_address(tmp_path):
    store = ArtifactStore(tmp_path)
    assert store.put("request", {"a": 1}).sha256 != store.put("response", {"a": 1}).sha256


def test_a_corrupted_file_is_detected_not_returned(tmp_path):
    store = ArtifactStore(tmp_path)
    ref = store.put("prediction", {"n": 1})
    path = store._path(ref, ".json")
    path.write_bytes(path.read_bytes().replace(b"1", b"2"))
    with pytest.raises(ArtifactError, match="corrupt"):
        store.get(ref)
    with pytest.raises(ArtifactError, match="corrupt"):
        store.put("prediction", {"n": 1})  # rewriting cannot paper over corruption


def test_blobs_are_addressed_by_their_bytes(tmp_path):
    store = ArtifactStore(tmp_path)
    ref = store.put_blob("blob.jpeg", b"\xff\xd8jpeg")
    assert store.get_blob(ref) == b"\xff\xd8jpeg" and store.exists(ref)


@pytest.mark.parametrize("kind", ["", "Upper", "has space", "x" * 65])
def test_invalid_kinds_are_refused(tmp_path, kind):
    with pytest.raises(ArtifactError):
        ArtifactStore(tmp_path).put(kind, {})


def test_missing_artifact_is_an_error(tmp_path):
    with pytest.raises(ArtifactError, match="missing"):
        ArtifactStore(tmp_path).get(ArtifactRef("prediction", "0" * 64))


def test_a_sealed_run_is_read_only_and_verifiable(tmp_path):
    store = ArtifactStore(tmp_path)
    run = store.run("v2-dev-0001")
    ref = store.put("prediction", {"n": 1})
    run.record("item-1", ref, outcome="ok")
    manifest_ref = run.seal({"manifest_version": "test"})
    assert run.sealed and run.manifest_ref() == manifest_ref
    assert store.get(manifest_ref)["run_id"] == "v2-dev-0001"
    assert run.verify() == []
    with pytest.raises(SealedRunError):
        run.record("item-2", ref, outcome="ok")
    with pytest.raises(SealedRunError):
        run.seal({})


def test_verify_finds_a_changed_index_and_a_corrupt_artifact(tmp_path):
    store = ArtifactStore(tmp_path)
    run = store.run("r1")
    ref = store.put("prediction", {"n": 1})
    run.record("item-1", ref, outcome="ok")
    run.seal({})
    run.index_path.write_text(run.index_path.read_text() + '{"key":"forged","ref":"' + str(ref) + '"}\n')
    store._path(ref, ".json").write_text("{}")
    problems = run.verify()
    assert any("index.jsonl changed" in p for p in problems) and any("corrupt" in p for p in problems)


def test_an_unsealed_run_does_not_verify(tmp_path):
    assert ArtifactStore(tmp_path).run("r2").verify() == ["run r2 is not sealed"]


@pytest.mark.parametrize("run_id", ["", "../escape", "a/b", " x"])
def test_invalid_run_ids_are_refused(tmp_path, run_id):
    with pytest.raises(ArtifactError):
        ArtifactStore(tmp_path).run(run_id)
