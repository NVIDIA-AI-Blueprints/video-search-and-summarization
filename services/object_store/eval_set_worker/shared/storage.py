"""Object-store reads/writes for the eval-set worker.

Whole-object PUTs (manifests, eval.json) are atomic, so a redelivered write
of identical bytes is idempotent.
"""

import asyncio
from functools import partial
from typing import Any

import boto3
from botocore.config import Config

from eval_set_worker.config import settings

# SeaweedFS occasionally returns a response whose headers lack the blank-line
# separator (urllib3 logs MissingHeaderBodySeparatorDefect). That poisons the
# pooled keep-alive connection, so a later request reusing it hangs forever —
# which stalls a long-lived consumer indefinitely. Two guards:
#   1) force Connection: close so a poisoned socket is never reused, and
#   2) bound connect/read with timeouts + retries so any stall fails fast and
#      retries on a fresh connection instead of hanging.
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

    async def put_object(
        self, key: str, data: bytes, content_type: str = "application/json"
    ) -> str:
        """Write raw bytes to a key (whole-object PUT is atomic — a redelivered
        write of identical bytes is idempotent). Used for manifests / summaries."""
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
        return f"{settings.aws_endpoint}/{settings.aws_bucket}/{key}"

    async def upload_file(
        self, key: str, filepath: str, content_type: str = "application/x-ndjson"
    ) -> str:
        """Managed (multipart) transfer streamed from a local file — constant
        memory regardless of manifest size; the object appears atomically."""
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
        return f"{settings.aws_endpoint}/{settings.aws_bucket}/{key}"

    async def get_object(self, key: str) -> bytes | None:
        """Read an object's bytes, or None if it does not exist."""
        loop = asyncio.get_event_loop()

        def _get() -> bytes | None:
            try:
                resp = self._client.get_object(Bucket=settings.aws_bucket, Key=key)
            except self._client.exceptions.NoSuchKey:
                return None
            except Exception as exc:  # noqa: BLE001 — map 404-ish to None, re-raise rest
                code = (getattr(exc, "response", None) or {}).get("Error", {}).get("Code")
                if code in ("NoSuchKey", "404", "NotFound"):
                    return None
                raise
            return resp["Body"].read()

        return await loop.run_in_executor(None, _get)

    async def list_keys(self, prefix: str) -> list[str]:
        """List object keys under a prefix (used to count per-slice markers)."""
        loop = asyncio.get_event_loop()

        def _list() -> list[str]:
            resp = self._client.list_objects_v2(
                Bucket=settings.aws_bucket, Prefix=prefix
            )
            return [obj["Key"] for obj in resp.get("Contents", [])]

        return await loop.run_in_executor(None, _list)

    async def delete(self, key: str) -> None:
        """Delete an object (used to GC internal per-slice markers on finalise)."""
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            partial(self._client.delete_object, Bucket=settings.aws_bucket, Key=key),
        )
