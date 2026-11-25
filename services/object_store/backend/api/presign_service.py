"""Resolve object keys and mint presigned GET URLs for object-store downloads."""

from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from backend.api.presign_schemas import (
    PresignFailureItem,
    PresignPostRequest,
    PresignPostResponse,
    PresignRequest,
    PresignResponse,
    PresignedUrlItem,
)
from backend.config import settings
from backend.shared.storage import StorageService

# Keys under these prefixes may be presigned: board images (deft-aoi/), catalog
# snapshots (snapshots/), direct uploads (upload/), the eval-set export tree
# (evals/ — settings.eval_prefix, so eval manifest s3:// URIs resolve), the KPI
# set manifest tree (kpi/ — settings.kpi_prefix), and durable model checkpoints
# (models/) consumed by DEFT and model-management workflows.
_ALLOWED_KEY_PREFIXES = (
    "deft-aoi/",
    f"{settings.its_object_prefix}/",
    "snapshots/",
    "upload/",
    "models/",
    f"{settings.eval_prefix}/",
    f"{settings.kpi_prefix}/",
)


class PresignError(ValueError):
    """Caller supplied a URL/key that cannot be presigned."""


class PresignConflictError(PresignError):
    """The target upload object already exists."""


def resolve_object_key(url_or_key: str) -> str:
    """Normalize an objStorePath or object-store URL to an S3 key in our bucket."""
    raw = url_or_key.strip()
    if not raw:
        raise PresignError("empty url")

    if raw.startswith(("http://", "https://", "s3://")):
        parsed = urlparse(raw)
        # http(s): key lives in the path after the bucket segment.
        # s3://<...>: the whole authority+path is the object location — either
        #   s3://<bucket>/<key> or s3://<objStorePath> (bucket implicit).
        candidate = (
            f"{parsed.netloc}{parsed.path}" if parsed.scheme == "s3" else parsed.path
        ).lstrip("/")
        bucket_prefix = f"{settings.aws_bucket}/"
        key = (
            candidate[len(bucket_prefix) :]
            if candidate.startswith(bucket_prefix)
            else candidate
        )
    else:
        key = raw.lstrip("/")

    if not key or ".." in key.split("/"):
        raise PresignError("invalid object key")
    if not any(key.startswith(prefix) for prefix in _ALLOWED_KEY_PREFIXES):
        raise PresignError(f"key must start with one of {_ALLOWED_KEY_PREFIXES}")
    return key


async def create_presigned_urls(
    storage: StorageService, request: PresignRequest
) -> PresignResponse:
    expires_in = request.expires_in or settings.presign_expires_s
    expires_at = (
        datetime.now(timezone.utc) + timedelta(seconds=expires_in)
    ).strftime("%Y-%m-%dT%H:%M:%S.%fZ")

    items: list[PresignedUrlItem] = []
    failed: list[PresignFailureItem] = []

    for raw in request.urls:
        try:
            key = resolve_object_key(raw)
        except PresignError as exc:
            failed.append(PresignFailureItem(input=raw, error=str(exc)))
            continue

        if not await storage.exists(key):
            failed.append(PresignFailureItem(input=raw, error=f"object not found: {key}"))
            continue

        try:
            presigned_url = await storage.generate_presigned_get_url(key, expires_in)
        except Exception as exc:  # noqa: BLE001
            failed.append(PresignFailureItem(input=raw, error=str(exc)))
            continue

        items.append(
            PresignedUrlItem(
                input=raw,
                key=key,
                presigned_url=presigned_url,
                expires_at=expires_at,
            )
        )

    return PresignResponse(items=items, failed=failed)


async def create_presigned_post(
    storage: StorageService, request: PresignPostRequest
) -> PresignPostResponse:
    """Mint a presigned POST for <folder>/<file_name>.

    folder/file_name are already charset-validated by the schema, so the key
    cannot escape the prefix. Rejects the upload if the object already exists.
    """
    key = f"{settings.upload_folder}/{request.folder}/{request.file_name}"
    if await storage.exists(key):
        raise PresignConflictError(f"object already exists: {key}")

    result = await storage.generate_presigned_post(
        key,
        request.content_type,
        settings.presign_post_max_bytes,
        settings.presign_expires_s,
    )
    return PresignPostResponse(url=result["url"], fields=result["fields"])
