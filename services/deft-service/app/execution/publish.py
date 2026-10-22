"""Publish real-mode run artifacts to the object store (DFW handoff experiment).

The symmetric other half of app/execution/materialize.py: after a successful
real-mode run, the best checkpoint (and the training spec used) is uploaded
under ``<folder>/<run_id>/`` and the results carry the resulting ``s3://`` URIs
instead of ``file://`` host paths — so every downstream consumer (DFW's Model
Manager pull, MLflow registration, the NEXT run's baseline-checkpoint
resolution) receives a reference that is valid on any host, not just this one.

Two write-back mechanisms, selected by config:

* **Presigned POST (preferred, credential-free).** When
  ``DEFT_PUBLISH_PRESIGN_URL`` is set, DEFT asks the Storage Backend for an S3
  *presigned POST* per artifact and streams the file there — no object-store
  credentials on DEFT (symmetric with the presigned-GET read path). Takes
  precedence when both are configured.
* **boto3 (fallback).** When only ``DEFT_PUBLISH_PREFIX`` is set, upload via
  boto3 using the request's ``storage_access`` temporary credentials if still
  valid at run end, else boto3's default provider chain.

Disabled unless one of the two is configured; without either, results keep
today's ``file://`` behavior.

Publish failures FAIL the run loudly (step ``publish``): a completed training
whose artifact is unreachable would only fail later and more confusingly at the
Model Manager pull.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

import httpx

from app.config import (
    PUBLISH_CONTENT_TYPE,
    PUBLISH_PREFIX,
    PUBLISH_PRESIGN_TIMEOUT_S,
    PUBLISH_PRESIGN_TOKEN,
    PUBLISH_PRESIGN_URL,
    PUBLISH_UPLOAD_TIMEOUT_S,
)
from app.execution.materialize import MaterializeError, _client, _S3Ref
from app.schemas.fine_tune_models import TemporaryCredentialsStorageAccess

logger = logging.getLogger("deft.publish")


class PublishError(RuntimeError):
    pass


def publish_enabled() -> bool:
    return bool(PUBLISH_PRESIGN_URL or PUBLISH_PREFIX)


def publish_artifacts(
    run_id: str,
    files: dict[str, Path],
    storage_access: TemporaryCredentialsStorageAccess | None,
) -> dict[str, str]:
    """Upload ``files`` (logical name -> local path) for a run.

    Returns logical name -> ``s3://`` URI. Missing local files are skipped with
    a warning (e.g. no backbone); an upload error raises PublishError, and a
    run that publishes no best checkpoint fails loudly.

    Dispatch: the presigned-POST path wins when ``DEFT_PUBLISH_PRESIGN_URL`` is
    set (DEFT needs no credentials); otherwise the boto3 ``DEFT_PUBLISH_PREFIX``
    path runs.
    """
    if PUBLISH_PRESIGN_URL:
        return _publish_via_presigned_post(run_id, files)
    if PUBLISH_PREFIX:
        return _publish_via_boto3(run_id, files, storage_access)
    raise PublishError("neither DEFT_PUBLISH_PRESIGN_URL nor DEFT_PUBLISH_PREFIX is configured")


# ---------------------------------------------------------------------------
# Presigned-POST path (credential-free)
# ---------------------------------------------------------------------------


def _publish_via_presigned_post(run_id: str, files: dict[str, Path]) -> dict[str, str]:
    # The Storage Backend accepts a single-segment ``folder`` (^[A-Za-z0-9_-]+$)
    # and owns the upload prefix — the object lands at
    # ``<upload prefix>/<run_id>/<file_name>``. run_id is a uuid4 string
    # (store.RunRecord.id), which satisfies that pattern, so it IS the folder.
    folder = run_id

    published: dict[str, str] = {}
    for name, path in files.items():
        if path is None or not Path(path).is_file():
            logger.warning("publish: skipping %s (no local file at %r)", name, str(path))
            continue
        file_name = Path(path).name
        presign = _request_presigned_post(PUBLISH_CONTENT_TYPE, folder, file_name)
        url = presign.get("url")
        fields = presign.get("fields")
        if not url or not isinstance(fields, dict):
            raise PublishError(f"presign response for {name} missing url/fields: {presign!r}")
        try:
            status = _upload_multipart(url, fields, Path(path), file_name, PUBLISH_CONTENT_TYPE)
        except (httpx.HTTPError, OSError) as exc:
            raise PublishError(f"presigned upload of {name} ({path}) failed: {exc}") from exc
        if status // 100 != 2:
            raise PublishError(f"presigned upload of {name} returned HTTP {status}")
        uri = _resolve_published_uri(presign, url, fields, file_name)
        published[name] = uri
        logger.info("published %s -> %s (presigned POST)", name, uri)

    if "best_checkpoint" not in published:
        raise PublishError("best checkpoint was not published (missing local file?)")
    return published


def _request_presigned_post(content_type: str, folder: str, file_name: str) -> dict:
    """Ask the Storage Backend for an S3 presigned POST for one object."""
    headers = {"Accept": "application/json"}
    if PUBLISH_PRESIGN_TOKEN:
        headers["Authorization"] = f"Bearer {PUBLISH_PRESIGN_TOKEN}"
    try:
        resp = httpx.post(
            PUBLISH_PRESIGN_URL,
            json={"content_type": content_type, "folder": folder, "file_name": file_name},
            headers=headers,
            timeout=PUBLISH_PRESIGN_TIMEOUT_S,
        )
    except httpx.HTTPError as exc:
        raise PublishError(f"presign request to {PUBLISH_PRESIGN_URL} failed: {exc}") from exc
    if resp.status_code // 100 != 2:
        raise PublishError(
            f"presign request to {PUBLISH_PRESIGN_URL} returned HTTP {resp.status_code}"
        )
    try:
        data = resp.json()
    except ValueError as exc:
        raise PublishError(f"presign response was not JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise PublishError(f"presign response was not a JSON object: {data!r}")
    return data


def _resolve_published_uri(presign: dict, url: str, fields: dict, file_name: str) -> str:
    """The durable ``s3://bucket/key`` the object landed at.

    Prefers an explicit ``uri`` from the Storage Backend; else derives it from
    the POST ``key`` field (substituting the S3 ``${filename}`` variable) and
    the bucket — an explicit ``bucket``, or the path-style POST url's first path
    segment (``http(s)://host[:port]/<bucket>``).
    """
    if presign.get("uri"):
        return str(presign["uri"])
    key = str(fields.get("key", "")).replace("${filename}", file_name)
    bucket = presign.get("bucket") or urlparse(url).path.strip("/").split("/", 1)[0]
    if not bucket or not key:
        raise PublishError(f"cannot derive published s3:// uri (bucket={bucket!r}, key={key!r})")
    return f"s3://{bucket}/{key}"


def _upload_multipart(
    url: str, fields: dict, file_path: Path, file_name: str, content_type: str
) -> int:
    """POST ``file_path`` to a presigned-POST ``url`` as multipart/form-data.

    Uses httpx (already a service dependency), which builds the multipart
    boundary itself and streams the file object — so the body is never buffered
    whole. The S3 POST contract requires the ``file`` part LAST, after every
    policy field: httpx renders ``data`` (the signed policy fields) before
    ``files``, so passing the file in ``files`` places it last. The object's
    content type rides the presigned ``Content-Type`` policy field (already in
    ``fields``) and is also set on the file part. Returns the HTTP status
    (httpx does not raise on 4xx/5xx, so the code is returned as-is).
    """
    with open(file_path, "rb") as fh:
        resp = httpx.post(
            url,
            data=fields,  # signed policy fields first
            files={"file": (file_name, fh, content_type)},  # the file part LAST
            timeout=PUBLISH_UPLOAD_TIMEOUT_S,
        )
    return resp.status_code


# ---------------------------------------------------------------------------
# boto3 path (fallback — temporary credentials / default provider chain)
# ---------------------------------------------------------------------------


def _publish_client(storage_access: TemporaryCredentialsStorageAccess | None):
    """storage_access if still valid, else the default provider chain."""
    if storage_access is not None and storage_access.expires_at > datetime.now(UTC):
        return _client(storage_access)
    if storage_access is not None:
        logger.info("storage_access expired before publish; using the default credential chain")
    return _client(None)


def _publish_via_boto3(
    run_id: str,
    files: dict[str, Path],
    storage_access: TemporaryCredentialsStorageAccess | None,
) -> dict[str, str]:
    base = _S3Ref.parse(PUBLISH_PREFIX if PUBLISH_PREFIX.endswith("/") else PUBLISH_PREFIX + "/")

    try:
        s3 = _publish_client(storage_access)
    except MaterializeError as exc:
        raise PublishError(str(exc)) from exc

    published: dict[str, str] = {}
    for name, path in files.items():
        if path is None or not Path(path).is_file():
            logger.warning("publish: skipping %s (no local file at %r)", name, str(path))
            continue
        key = f"{base.key}{run_id}/{Path(path).name}"
        try:
            s3.upload_file(str(path), base.bucket, key)
        except Exception as exc:
            raise PublishError(f"upload of {name} ({path}) failed: {exc}") from exc
        uri = f"s3://{base.bucket}/{key}"
        published[name] = uri
        logger.info("published %s -> %s", name, uri)
    if "best_checkpoint" not in published:
        raise PublishError("best checkpoint was not published (missing local file?)")
    return published
