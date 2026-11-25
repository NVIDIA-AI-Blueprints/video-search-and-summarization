import asyncio
import json
from datetime import datetime
from functools import partial
from typing import Any

import boto3
from botocore.client import BaseClient
from botocore.config import Config

from backend.config import settings


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
    def __init__(self) -> None:
        self._client: BaseClient = boto3.client(
            "s3",
            endpoint_url=settings.aws_endpoint,
            aws_access_key_id=settings.aws_access_key_id,
            aws_secret_access_key=settings.aws_secret_access_key,
            config=_S3_CONFIG,
        )
        # Presigned URLs must be reachable by external clients, so sign them
        # against the public endpoint. SigV4 puts the host in the signature, so
        # we sign directly with a client bound to the public host rather than
        # string-replacing the host afterwards (which would break the signature).
        # Internal read/write keeps using self._client (aws_endpoint) unchanged.
        public_endpoint = settings.aws_public_endpoint or settings.aws_endpoint
        self._presign_client: BaseClient = (
            self._client
            if public_endpoint == settings.aws_endpoint
            else boto3.client(
                "s3",
                endpoint_url=public_endpoint,
                aws_access_key_id=settings.aws_access_key_id,
                aws_secret_access_key=settings.aws_secret_access_key,
            )
        )
        # Added after signing (before-send), so it does not affect SigV4.
        self._client.meta.events.register("before-send.s3", _force_connection_close)

    async def upload(
        self,
        key: str,
        data: bytes,
        content_type: str,
        metadata: dict[str, Any],
        tagging: str | None = None,
    ) -> str:
        """Write an image with its metadata; tagging is an S3 tag set
        ("key=value&..."), sent only when given."""
        extra: dict[str, Any] = {"Tagging": tagging} if tagging else {}
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            partial(
                self._client.put_object,
                Bucket=settings.aws_bucket,
                Key=key,
                Body=data,
                ContentType=content_type,
                Metadata={"x-image-metadata": json.dumps(metadata)},
                **extra,
            ),
        )
        return f"{settings.aws_public_endpoint}/{settings.aws_bucket}/{key}"

    async def get_lifecycle_rules(self) -> list[dict[str, Any]]:
        """The bucket's lifecycle rules; empty when it has none."""
        loop = asyncio.get_event_loop()

        def _get() -> list[dict[str, Any]]:
            try:
                resp = self._client.get_bucket_lifecycle_configuration(Bucket=settings.aws_bucket)
            except Exception as exc:  # noqa: BLE001 — "no configuration" is empty, re-raise rest
                code = (getattr(exc, "response", None) or {}).get("Error", {}).get("Code")
                if code == "NoSuchLifecycleConfiguration":
                    return []
                raise
            return resp.get("Rules", [])

        return await loop.run_in_executor(None, _get)

    async def put_lifecycle_rules(self, rules: list[dict[str, Any]]) -> None:
        """Replace the bucket's lifecycle configuration with these rules, or
        delete it when there are none."""
        loop = asyncio.get_event_loop()

        def _put() -> None:
            if rules:
                self._client.put_bucket_lifecycle_configuration(
                    Bucket=settings.aws_bucket, LifecycleConfiguration={"Rules": rules}
                )
            else:
                self._client.delete_bucket_lifecycle(Bucket=settings.aws_bucket)

        await loop.run_in_executor(None, _put)

    async def delete(self, key: str) -> None:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            partial(
                self._client.delete_object,
                Bucket=settings.aws_bucket,
                Key=key,
            ),
        )

    async def list_page(
        self, prefix: str, continuation_token: str | None = None
    ) -> tuple[list[dict[str, Any]], str | None]:
        """One page of the objects under a prefix, and the token for the next.

        Each entry carries the key, its LastModified (timezone-aware) and size.
        The token is None on the last page.
        """
        loop = asyncio.get_event_loop()

        def _list() -> tuple[list[dict[str, Any]], str | None]:
            kwargs: dict[str, Any] = {"Bucket": settings.aws_bucket, "Prefix": prefix}
            if continuation_token:
                kwargs["ContinuationToken"] = continuation_token
            resp = self._client.list_objects_v2(**kwargs)
            objects = [
                {"key": o["Key"], "last_modified": o["LastModified"], "size": o.get("Size", 0)}
                for o in resp.get("Contents", [])
            ]
            token = resp.get("NextContinuationToken") if resp.get("IsTruncated") else None
            return objects, token

        return await loop.run_in_executor(None, _list)

    async def last_modified(self, key: str) -> datetime | None:
        """LastModified of an object (timezone-aware), or None if it is absent."""
        loop = asyncio.get_event_loop()

        def _head() -> datetime | None:
            try:
                return self._client.head_object(Bucket=settings.aws_bucket, Key=key)[
                    "LastModified"
                ]
            except Exception as exc:  # noqa: BLE001 — map 404-ish to None, re-raise rest
                code = (getattr(exc, "response", None) or {}).get("Error", {}).get("Code")
                if code in ("NoSuchKey", "404", "NotFound"):
                    return None
                raise

        return await loop.run_in_executor(None, _head)

    async def delete_many(self, keys: list[str]) -> list[str]:
        """Delete up to 1000 keys in one request; return the keys that failed.

        Deleting a key that does not exist counts as success, as in S3.
        """
        if not keys:
            return []
        if len(keys) > 1000:
            raise ValueError("DeleteObjects accepts at most 1000 keys per request")
        loop = asyncio.get_event_loop()

        def _delete() -> list[str]:
            resp = self._client.delete_objects(
                Bucket=settings.aws_bucket,
                Delete={"Objects": [{"Key": k} for k in keys], "Quiet": True},
            )
            return [e["Key"] for e in resp.get("Errors", [])]

        return await loop.run_in_executor(None, _delete)

    async def put_bytes(
        self, key: str, data: bytes, content_type: str = "application/json"
    ) -> None:
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

    async def exists(self, key: str) -> bool:
        loop = asyncio.get_event_loop()

        def _head() -> bool:
            try:
                self._client.head_object(Bucket=settings.aws_bucket, Key=key)
                return True
            except Exception:
                return False

        return await loop.run_in_executor(None, _head)

    async def get_metadata(self, key: str) -> dict[str, Any]:
        """Return the nested AOI metadata JSON stored at upload time."""
        loop = asyncio.get_event_loop()

        def _head() -> dict[str, Any]:
            resp = self._client.head_object(Bucket=settings.aws_bucket, Key=key)
            raw = resp.get("Metadata", {}).get("x-image-metadata")
            if not raw:
                raise KeyError(f"no x-image-metadata for key={key}")
            return json.loads(raw)

        return await loop.run_in_executor(None, _head)

    async def generate_presigned_get_url(self, key: str, expires_in: int) -> str:
        loop = asyncio.get_event_loop()

        def _presign() -> str:
            return self._presign_client.generate_presigned_url(
                "get_object",
                Params={"Bucket": settings.aws_bucket, "Key": key},
                ExpiresIn=expires_in,
            )

        return await loop.run_in_executor(None, _presign)

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
    async def generate_presigned_post(
        self, key: str, content_type: str, max_bytes: int, expires_in: int
    ) -> dict[str, Any]:
        """Presigned POST for a direct client upload to `key`.

        The content-length-range condition caps the upload size; the exact Key
        means the client can only upload to that one path. Returns boto3's
        {"url": ..., "fields": {...}} verbatim. Signed against the public
        endpoint so the URL is reachable by external clients.
        """
        loop = asyncio.get_event_loop()

        def _presign() -> dict[str, Any]:
            return self._presign_client.generate_presigned_post(
                Bucket=settings.aws_bucket,
                Key=key,
                Fields={"Content-Type": content_type},
                Conditions=[
                    {"Content-Type": content_type},
                    ["content-length-range", 0, max_bytes],
                ],
                ExpiresIn=expires_in,
            )
        return await loop.run_in_executor(None, _presign)
