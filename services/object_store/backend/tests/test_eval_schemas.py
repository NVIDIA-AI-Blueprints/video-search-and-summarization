import pytest
from pydantic import ValidationError

from backend.api.eval_schemas import (
    EvalRequest,
    EvalSelection,
    EvalStatusResponse,
    SetManifest,
)


def test_selection_requires_model_version_role():
    with pytest.raises(ValidationError):
        EvalSelection(model="m", version="v")  # role missing


def test_selection_from_alias_roundtrips():
    s = EvalSelection.model_validate(
        {"model": "m", "version": "v", "role": "r", "from": "2026-06-01T00:00:00Z"}
    )
    assert s.from_ == "2026-06-01T00:00:00Z"
    dumped = s.model_dump(by_alias=True)
    assert dumped["from"] == "2026-06-01T00:00:00Z"
    assert dumped["model"] == "m" and dumped["role"] == "r"


def test_request_rejects_caller_eval_id():
    # Contract additionalProperties=false — no caller-supplied eval_id.
    with pytest.raises(ValidationError):
        EvalRequest(selection={"model": "m", "version": "v", "role": "r"}, eval_id="x")


def test_selection_rejects_extra_keys():
    with pytest.raises(ValidationError):
        EvalSelection(model="m", version="v", role="r", created_after="x")


def test_status_response_defaults():
    r = EvalStatusResponse(eval_id="e1", status="creating", created_at="t0")
    assert r.total_records == 0 and r.manifests == []


def test_status_response_with_manifests():
    r = EvalStatusResponse(
        eval_id="e1", status="ready", total_records=5, created_at="t0",
        manifests=[{"format": "jsonl", "uri": "s3://b/k", "size_bytes": 1, "sha256": "a"}],
    )
    assert r.manifests[0].format == "jsonl"
    assert r.manifests[0].uri == "s3://b/k"


def test_set_manifest_rejects_bad_format():
    with pytest.raises(ValidationError):
        SetManifest(format="parquet", uri="s3://b/k", size_bytes=1, sha256="a")
