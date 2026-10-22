"""Object-store writes for the snapshot worker.

- upload_file uses boto3's managed transfer (multipart): it streams a manifest
  from a local temp file to S3 in parts, so worker memory stays constant no
  matter how large the slice is. The object appears atomically on completion.
- put_bytes is a single put_object for the small in-memory snapshot.json.
"""

import asyncio
from functools import partial

import boto3
from botocore.config import Config

from snapshot_worker.config import settings

_S3_CONFIG = Config(
    connect_timeout=5,
    read_timeout=15,
    retries={"max_attempts": 3, "mode": "standard"},
    request_checksum_calculation="when_required",
    response_checksum_validation="when_required",
)

class StorageService:
    def __init__(self, client=None) -> None:
        self._client = client or boto3.client(
            "s3",
            endpoint_url=settings.aws_endpoint,
            aws_access_key_id=settings.aws_access_key_id,
            aws_secret_access_key=settings.aws_secret_access_key,
            config=_S3_CONFIG
        )

    async def upload_file(
        self, key: str, filepath: str, content_type: str = "application/x-ndjson"
    ) -> None:
        # Managed transfer: multipart upload streamed from disk (constant memory).
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            partial(
                self._client.upload_file,
                filepath,
                settings.aws_bucket,
                key,
                ExtraArgs={"ContentType": content_type},
            ),
        )

    async def put_bytes(
        self, key: str, data: bytes, content_type: str = "application/json"
    ) -> None:
        # Single put of an in-memory payload — used for status markers and
        # snapshot.json.
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            partial(
                self._client.put_object,
                Bucket=settings.aws_bucket,
                Key=key,
                Body=data,
                ContentType=content_type,
            ),
        )

    async def get_bytes(self, key: str) -> bytes:
        loop = asyncio.get_event_loop()

        def _get() -> bytes:
            resp = self._client.get_object(Bucket=settings.aws_bucket, Key=key)
            return resp["Body"].read()

        return await loop.run_in_executor(None, _get)

    async def list_keys(self, prefix: str) -> list[str]:
        loop = asyncio.get_event_loop()

        def _list() -> list[str]:
            resp = self._client.list_objects_v2(Bucket=settings.aws_bucket, Prefix=prefix)
            return [obj["Key"] for obj in resp.get("Contents", [])]

        return await loop.run_in_executor(None, _list)

    async def exists(self, key: str) -> bool:
        loop = asyncio.get_event_loop()

        def _head() -> bool:
            try:
                self._client.head_object(Bucket=settings.aws_bucket, Key=key)
                return True
            except Exception:
                return False

        return await loop.run_in_executor(None, _head)
