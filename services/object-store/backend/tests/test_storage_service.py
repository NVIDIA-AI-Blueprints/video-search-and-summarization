"""StorageService listing and batch delete, used by the ITS retention pass."""

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from backend.config import settings
from backend.shared.storage import StorageService

WHEN = datetime(2026, 9, 1, tzinfo=timezone.utc)


def _service():
    svc = StorageService.__new__(StorageService)
    svc._client = MagicMock()
    return svc


@pytest.mark.asyncio
async def test_list_page_returns_objects_and_the_next_token():
    svc = _service()
    svc._client.list_objects_v2.return_value = {
        "Contents": [{"Key": "deft-its/a/1.jpg", "LastModified": WHEN, "Size": 5}],
        "IsTruncated": True,
        "NextContinuationToken": "t2",
    }

    objects, token = await svc.list_page("deft-its/", "t1")

    assert objects == [{"key": "deft-its/a/1.jpg", "last_modified": WHEN, "size": 5}]
    assert token == "t2"
    svc._client.list_objects_v2.assert_called_once_with(
        Bucket=settings.aws_bucket, Prefix="deft-its/", ContinuationToken="t1"
    )


@pytest.mark.asyncio
async def test_list_page_last_page_has_no_token():
    svc = _service()
    svc._client.list_objects_v2.return_value = {"IsTruncated": False, "KeyCount": 0}

    assert await svc.list_page("deft-its/") == ([], None)
    assert "ContinuationToken" not in svc._client.list_objects_v2.call_args.kwargs


@pytest.mark.asyncio
async def test_delete_many_returns_the_keys_that_failed():
    svc = _service()
    svc._client.delete_objects.return_value = {
        "Errors": [{"Key": "b", "Code": "AccessDenied"}]
    }

    assert await svc.delete_many(["a", "b"]) == ["b"]
    svc._client.delete_objects.assert_called_once_with(
        Bucket=settings.aws_bucket,
        Delete={"Objects": [{"Key": "a"}, {"Key": "b"}], "Quiet": True},
    )


@pytest.mark.asyncio
async def test_delete_many_rejects_more_than_one_request_can_carry():
    svc = _service()
    with pytest.raises(ValueError):
        await svc.delete_many([str(i) for i in range(1001)])
    svc._client.delete_objects.assert_not_called()


@pytest.mark.asyncio
async def test_delete_many_with_no_keys_sends_nothing():
    svc = _service()
    assert await svc.delete_many([]) == []
    svc._client.delete_objects.assert_not_called()


@pytest.mark.asyncio
async def test_last_modified_of_a_present_object():
    svc = _service()
    svc._client.head_object.return_value = {"LastModified": WHEN}

    assert await svc.last_modified("deft-its/a/1.jpg") == WHEN


@pytest.mark.asyncio
async def test_last_modified_of_a_missing_object_is_none():
    svc = _service()
    err = Exception("not found")
    err.response = {"Error": {"Code": "404"}}
    svc._client.head_object.side_effect = err

    assert await svc.last_modified("deft-its/a/1.jpg") is None


@pytest.mark.asyncio
async def test_last_modified_other_errors_propagate():
    svc = _service()
    err = Exception("denied")
    err.response = {"Error": {"Code": "403"}}
    svc._client.head_object.side_effect = err

    with pytest.raises(Exception, match="denied"):
        await svc.last_modified("deft-its/a/1.jpg")


@pytest.mark.asyncio
@pytest.mark.parametrize("tagging", [None, "deft-retention=its-frame"])
async def test_upload_sends_a_tag_set_only_when_given(monkeypatch, tagging):
    monkeypatch.setattr(settings, "aws_bucket", "b")
    svc = _service()

    await svc.upload("deft-its/a/1.jpg", b"x", "image/jpeg", {"k": 1}, tagging=tagging)

    kwargs = svc._client.put_object.call_args.kwargs
    assert kwargs.get("Tagging") == tagging
    assert ("Tagging" in kwargs) == (tagging is not None)


@pytest.mark.asyncio
async def test_get_lifecycle_rules_of_a_bucket_without_any_is_empty():
    svc = _service()
    error = Exception("no lifecycle")
    error.response = {"Error": {"Code": "NoSuchLifecycleConfiguration"}}
    svc._client.get_bucket_lifecycle_configuration.side_effect = error

    assert await svc.get_lifecycle_rules() == []


@pytest.mark.asyncio
async def test_get_lifecycle_rules_other_errors_propagate():
    svc = _service()
    error = Exception("denied")
    error.response = {"Error": {"Code": "AccessDenied"}}
    svc._client.get_bucket_lifecycle_configuration.side_effect = error

    with pytest.raises(Exception, match="denied"):
        await svc.get_lifecycle_rules()


@pytest.mark.asyncio
async def test_put_lifecycle_rules_replaces_or_deletes_the_configuration(monkeypatch):
    monkeypatch.setattr(settings, "aws_bucket", "b")
    svc = _service()

    await svc.put_lifecycle_rules([{"ID": "r"}])
    svc._client.put_bucket_lifecycle_configuration.assert_called_once_with(
        Bucket="b", LifecycleConfiguration={"Rules": [{"ID": "r"}]}
    )
    await svc.put_lifecycle_rules([])
    svc._client.delete_bucket_lifecycle.assert_called_once_with(Bucket="b")
