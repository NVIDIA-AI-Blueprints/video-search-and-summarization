"""Object-store writes for the KPI worker.

- upload_file uses boto3's managed transfer (multipart): it streams a manifest
  from a local temp file to S3 in parts, so worker memory stays constant no
  matter how large the slice is. The object appears atomically on completion.
- put_bytes is a single put_object for small in-memory payloads (status markers
  and kpi.json).
"""

import asyncio
from functools import partial
from typing import Any

import boto3
from botocore.config import Config

from kpi_worker.config import settings


# SeaweedFS's S3 gateway trips urllib3's header parser (MissingHeaderBodySeparatorDefect)
# on two fronts, both handled here:
#   1) boto3 >=1.36 adds a CRC32 checksum + aws-chunked trailer to uploads by
#      default; SeaweedFS answers that framing in a way urllib3 can't parse. We
#      only checksum when the API actually requires it, which removes the
#      malformed response (and the warning) at the source.
#   2) Any stray malformed response still poisons the pooled keep-alive socket,
#      so a later request reusing it would hang forever — fatal for a long-lived
#      consumer. Force Connection: close so a poisoned socket is never reused,
#      and bound connect/read with timeouts + retries so any stall fails fast on
#      a fresh connection instead of hanging.
_S3_CONFIG = Config(
    connect_timeout=5,
    read_timeout=15,
    retries={"max_attempts": 3, "mode": "standard"},
    request_checksum_calculation="when_required",
    response_checksum_validation="when_required",
)


def _force_connection_close(request: Any, **_: Any) -> None:
    request.headers["Connection"] = "close"


class StorageService:
    def __init__(self, client=None) -> None:
        self._client = client or boto3.client(
            "s3",
            endpoint_url=settings.aws_endpoint,
            aws_access_key_id=settings.aws_access_key_id,
            aws_secret_access_key=settings.aws_secret_access_key,
            config=_S3_CONFIG,
        )
        # Added after signing (before-send), so it does not affect SigV4.
        self._client.meta.events.register("before-send.s3", _force_connection_close)

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
        # Single put of an in-memory payload — used for status markers and kpi.json.
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
