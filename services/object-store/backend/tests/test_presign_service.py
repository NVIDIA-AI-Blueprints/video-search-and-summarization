import re
from urllib.parse import urlparse

import pytest
from pydantic import ValidationError
from unittest.mock import AsyncMock, MagicMock

from backend.api.presign_schemas import PresignPostRequest, PresignRequest
from backend.api.presign_service import (
    PresignConflictError,
    PresignError,
    create_presigned_post,
    create_presigned_urls,
    resolve_object_key,
)
from backend.config import settings
from backend.shared.storage import StorageService


def _ok_storage(url: str = "http://signed.example/obj?X-Amz-Signature=abc") -> MagicMock:
    storage = MagicMock()
    storage.exists = AsyncMock(return_value=True)
    storage.generate_presigned_get_url = AsyncMock(return_value=url)
    return storage


def test_resolve_snapshot_prefix():
    assert resolve_object_key("snapshots/eval1/manifest0.jsonl") == (
        "snapshots/eval1/manifest0.jsonl"
    )


def test_resolve_evals_prefix():
    # Track 11: eval manifests live under evals/ (settings.eval_prefix) and must presign.
    assert resolve_object_key("evals/e1/manifest0.jsonl") == "evals/e1/manifest0.jsonl"


def test_resolve_s3_uri_evals_prefix():
    # Contract manifest uri: s3://<bucket>/evals/<id>/manifest<i>.jsonl
    assert resolve_object_key(
        f"s3://{settings.aws_bucket}/evals/e1/manifest0.jsonl"
    ) == "evals/e1/manifest0.jsonl"


def test_resolve_upload_prefix():
    assert resolve_object_key("upload/boards/b1.png") == "upload/boards/b1.png"


def test_resolve_s3_uri_upload_prefix():
    assert resolve_object_key("s3://upload/boards/b1.png") == "upload/boards/b1.png"


def test_resolve_s3_uri_models_prefix():
    assert resolve_object_key(
        f"s3://{settings.aws_bucket}/models/baseline/1.0.0/model.pth"
    ) == "models/baseline/1.0.0/model.pth"


def test_resolve_rejects_unknown_prefix():
    with pytest.raises(PresignError):
        resolve_object_key("other/path.png")


# ---- s3:// URI support (reviewer request: keys arrive as s3://deft-aoi/...) ---


def test_resolve_s3_uri_bucket_implicit():
    assert resolve_object_key("s3://deft-aoi/abc/board.png") == "deft-aoi/abc/board.png"


def test_resolve_s3_uri_bucket_qualified():
    assert (
        resolve_object_key(f"s3://{settings.aws_bucket}/deft-aoi/abc/board.png")
        == "deft-aoi/abc/board.png"
    )


def test_resolve_s3_uri_snapshots_prefix():
    assert resolve_object_key("s3://snapshots/eval1/manifest0.jsonl") == (
        "snapshots/eval1/manifest0.jsonl"
    )


def test_resolve_s3_uri_rejects_traversal():
    with pytest.raises(PresignError):
        resolve_object_key("s3://deft-aoi/../etc/passwd")


def test_resolve_s3_uri_rejects_unknown_prefix():
    with pytest.raises(PresignError):
        resolve_object_key("s3://other/path.png")


# ---- (b) batch cap: single source of truth is the schema max_length ----------


def test_batch_limit_enforced_by_schema():
    urls = [
        f"deft-aoi/x/{i}.png" for i in range(settings.max_presign_urls_per_request + 1)
    ]
    with pytest.raises(ValidationError):
        PresignRequest(urls=urls)


def test_batch_limit_boundary_ok():
    urls = [f"deft-aoi/x/{i}.png" for i in range(settings.max_presign_urls_per_request)]
    assert len(PresignRequest(urls=urls).urls) == settings.max_presign_urls_per_request


# ---- (a) previously-uncovered branches ---------------------------------------


@pytest.mark.asyncio
async def test_custom_expires_in_passed_to_storage():
    storage = _ok_storage()
    resp = await create_presigned_urls(
        storage, PresignRequest(urls=["deft-aoi/a/b.png"], expires_in=120)
    )
    assert len(resp.items) == 1
    storage.generate_presigned_get_url.assert_awaited_once_with("deft-aoi/a/b.png", 120)


@pytest.mark.asyncio
async def test_default_expires_in_uses_setting():
    storage = _ok_storage()
    await create_presigned_urls(storage, PresignRequest(urls=["deft-aoi/a/b.png"]))
    storage.generate_presigned_get_url.assert_awaited_once_with(
        "deft-aoi/a/b.png", settings.presign_expires_s
    )


@pytest.mark.asyncio
async def test_signing_error_goes_to_failed():
    storage = _ok_storage()
    storage.generate_presigned_get_url = AsyncMock(side_effect=RuntimeError("sign boom"))
    resp = await create_presigned_urls(
        storage, PresignRequest(urls=["deft-aoi/a/b.png"])
    )
    assert resp.items == []
    assert len(resp.failed) == 1
    assert resp.failed[0].input == "deft-aoi/a/b.png"
    assert "sign boom" in resp.failed[0].error


@pytest.mark.asyncio
async def test_partial_success_full_url_and_bad_prefix():
    storage = _ok_storage()
    full_url = f"{settings.aws_endpoint}/{settings.aws_bucket}/deft-aoi/a/b.png"
    resp = await create_presigned_urls(
        storage, PresignRequest(urls=[full_url, "other/x.png"])
    )
    assert len(resp.items) == 1
    assert resp.items[0].input == full_url
    assert resp.items[0].key == "deft-aoi/a/b.png"  # bucket prefix stripped from URL
    assert len(resp.failed) == 1
    assert resp.failed[0].input == "other/x.png"


@pytest.mark.asyncio
async def test_expires_at_iso_format():
    storage = _ok_storage()
    resp = await create_presigned_urls(
        storage, PresignRequest(urls=["deft-aoi/a/b.png"])
    )
    assert re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z", resp.items[0].expires_at
    )


# ---- presigned URL host is client-reachable (Docker-internal-host fix) --------


@pytest.mark.asyncio
async def test_presign_signs_against_public_endpoint(monkeypatch):
    monkeypatch.setattr(settings, "aws_public_endpoint", "http://10.57.234.46:8333")
    storage = StorageService()
    url = await storage.generate_presigned_get_url("deft-aoi/a/b.png", 3600)
    assert urlparse(url).netloc == "10.57.234.46:8333"  # public host, not internal
    assert "Signature" in url  # X-Amz-Signature (SigV4) or Signature (SigV2)


@pytest.mark.asyncio
async def test_presign_defaults_to_internal_endpoint(monkeypatch):
    monkeypatch.setattr(settings, "aws_public_endpoint", None)
    storage = StorageService()
    url = await storage.generate_presigned_get_url("deft-aoi/a/b.png", 3600)
    assert urlparse(url).netloc == urlparse(settings.aws_endpoint).netloc  # no regression
    assert "Signature" in url


@pytest.mark.asyncio
async def test_presign_empty_public_endpoint_falls_back(monkeypatch):
    # docker-compose passes AWS_PUBLIC_ENDPOINT="" when unset -> must fall back.
    monkeypatch.setattr(settings, "aws_public_endpoint", "")
    storage = StorageService()
    url = await storage.generate_presigned_get_url("deft-aoi/a/b.png", 3600)
    assert urlparse(url).netloc == urlparse(settings.aws_endpoint).netloc


# ---- presigned POST (upload) -------------------------------------------------


def _post_storage() -> MagicMock:
    storage = MagicMock()
    storage.exists = AsyncMock(return_value=False)
    storage.generate_presigned_post = AsyncMock(
        return_value={"url": "http://signed.example/", "fields": {"key": "boards/b1"}}
    )
    return storage


@pytest.mark.asyncio
async def test_presigned_post_builds_key_and_passes_limits():
    storage = _post_storage()
    req = PresignPostRequest(content_type="image/png", folder="boards", file_name="b1")
    resp = await create_presigned_post(storage, req)

    expected_key = f"{settings.upload_folder}/boards/b1"
    assert resp.url == "http://signed.example/"
    assert resp.fields == {"key": "boards/b1"}  # returned verbatim from storage
    storage.exists.assert_awaited_once_with(expected_key)
    storage.generate_presigned_post.assert_awaited_once_with(
        expected_key,
        "image/png",
        settings.presign_post_max_bytes,
        settings.presign_expires_s,
    )


@pytest.mark.asyncio
async def test_presigned_post_conflict_when_exists():
    storage = _post_storage()
    storage.exists = AsyncMock(return_value=True)
    req = PresignPostRequest(content_type="image/png", folder="boards", file_name="b1")
    with pytest.raises(PresignConflictError):
        await create_presigned_post(storage, req)
    storage.generate_presigned_post.assert_not_awaited()


@pytest.mark.parametrize(
    "folder,file_name",
    [
        ("a/b", "n"),        # nested folder
        ("..", "n"),         # traversal folder
        ("f.x", "n"),        # dot not allowed in folder
        ("f", "a/b"),        # slash in file_name
        ("f", ".."),         # traversal file_name
        ("f", "a..b"),       # '..' anywhere in file_name
        ("", "n"),           # empty folder
        ("f", ""),           # empty file_name
    ],
)
def test_presigned_post_rejects_bad_segments(folder, file_name):
    with pytest.raises(ValidationError):
        PresignPostRequest(content_type="image/png", folder=folder, file_name=file_name)


def test_presigned_post_allows_dotted_file_name():
    req = PresignPostRequest(content_type="image/png", folder="boards", file_name="board.jpg")
    assert req.file_name == "board.jpg"
