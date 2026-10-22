"""Object-store materialization for real-mode runs (DFW handoff experiment).

The DFW <-> DEFT contract moves data BY REFERENCE: the fine-tune request
carries object-store URIs (a Storage Backend snapshot as ``data_pool.path``,
a registry checkpoint as ``model.checkpoint``, optionally a KPI set as
``kpi.path``) plus ``storage_access`` — temporary S3-style credentials scoped
to those prefixes. The docker backend, however, operates on a LOCAL workspace
(``Workspace.resolve`` rejects ``s3://``).

This module bridges the two: before workspace resolution, any ``s3://``
reference in the resolved config is downloaded into a per-run local root and
the config is rewritten to the local paths:

  * ``data_pool.path``   s3://bucket/prefix/  -> <root>/<run>/workspace/
    (prefix mirrored recursively — the snapshot is expected to already BE in
    the DEFT workspace layout: kpi/, train/base/, augmentation/, specs/)
  * ``model.checkpoint`` s3://bucket/key      -> workspace/checkpoints/<name>
    (placed INSIDE the workspace so the container mount can see it; the
    config value becomes the workspace-relative path). A TRAILING-SLASH
    URI (``s3://bucket/models/x/``) is treated as a MULTI-FILE model
    directory (HF-style: config + weight shards) and mirrored recursively
    to ``workspace/checkpoints/<dirname>/``.
  * ``kpi.path``         s3://bucket/key      -> workspace/kpi_sets/<name>

References may also be **presigned http(s) GET URLs** (the Storage Backend's
``POST /v1/get_presigned_urls`` — no credentials needed, the signature is in the
query string). ``model.checkpoint`` and ``kpi.path`` are single objects and are
downloaded directly via a plain GET. The **snapshot** is many objects, which a
single presigned URL cannot represent, so it is delivered as a **per-object
presigned manifest** (``data_pool.files`` = a list of ``{path, url}``): every
data point is downloaded to ``workspace/<path>`` (in parallel), reconstructing
the DEFT workspace layout. Snapshot downloads are **fail-soft** — a per-object
failure is logged and skipped (the run trains on what downloaded; the preflight
gates usability), unlike the single ``model.checkpoint`` / ``kpi.path`` which are
fatal if they fail. A single presigned ``data_pool.path`` is rejected.

Credentials: ``storage_access`` (temporary_credentials) when provided,
otherwise boto3's default provider chain. ``expires_at`` is checked up front
so a run fails at submit-side materialization, not minutes into a download.

boto3 is imported lazily — simulate-mode and pure-presigned runs don't need it.
"""

from __future__ import annotations

import json
import logging
import shutil
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

# A per-object download budget for presigned http(s) GETs.
_HTTP_DOWNLOAD_TIMEOUT_S = 300
# Concurrency for downloading a presigned snapshot manifest (many objects).
_MANIFEST_DOWNLOAD_WORKERS = 8

from app.config import (
    MATERIALIZE_PRESIGN_EXPIRES_S,
    MATERIALIZE_PRESIGN_TOKEN,
    MATERIALIZE_PRESIGN_URL,
    MATERIALIZE_ROOT,
    MATERIALIZE_STORAGE_URL,
)
from app.schemas.config_models import ExperimentConfig
from app.schemas.fine_tune_models import TemporaryCredentialsStorageAccess

# The Storage Backend presign endpoint accepts at most this many URLs per call.
_PRESIGN_BATCH = 50

logger = logging.getLogger("deft.materialize")


class MaterializeError(RuntimeError):
    pass


_REMOTE_SCHEMES = ("s3://", "http://", "https://")


def _is_remote(v: object) -> bool:
    return isinstance(v, str) and v.startswith(_REMOTE_SCHEMES)


def _is_http(v: object) -> bool:
    return isinstance(v, str) and v.startswith(("http://", "https://"))


def needs_materialization(cfg: ExperimentConfig) -> bool:
    # A presigned snapshot manifest (data_pool.files) always needs pulling.
    # http(s) references are presigned GET URLs (no creds needed); s3:// needs
    # storage_access. Either kind must be pulled to the local workspace first.
    if cfg.data_pool.files:
        return True
    return any(
        _is_remote(v)
        for v in (
            cfg.data_pool.path,
            cfg.model.checkpoint,
            cfg.kpi.path if cfg.kpi else None,
        )
    )


@dataclass
class _S3Ref:
    bucket: str
    key: str  # no leading slash; may be a prefix (trailing / semantics by caller)

    @staticmethod
    def parse(uri: str) -> "_S3Ref":
        parsed = urlparse(uri)
        if parsed.scheme != "s3" or not parsed.netloc:
            raise MaterializeError(f"not an s3 URI: {uri!r}")
        return _S3Ref(bucket=parsed.netloc, key=parsed.path.lstrip("/"))


def _client(storage_access: TemporaryCredentialsStorageAccess | None):
    try:
        import boto3  # lazy: only real-mode + object-store deployments need it
    except ImportError as exc:  # pragma: no cover - environment guard
        raise MaterializeError(
            "boto3 is required to materialize s3:// references "
            "(pip install boto3, or stage the workspace locally)"
        ) from exc

    if storage_access is None:
        return boto3.client("s3")
    if storage_access.expires_at <= datetime.now(UTC):
        raise MaterializeError(
            f"storage_access credentials expired at {storage_access.expires_at.isoformat()}"
        )
    creds = storage_access.credentials
    return boto3.client(
        "s3",
        endpoint_url=storage_access.endpoint_url,
        region_name=storage_access.region,
        aws_access_key_id=creds.access_key_id,
        aws_secret_access_key=creds.secret_access_key,
        aws_session_token=creds.session_token,
    )


def _download_prefix(s3, ref: _S3Ref, dest: Path) -> int:
    """Mirror every object under ``ref`` (a prefix) into ``dest``; return count."""
    prefix = ref.key if ref.key.endswith("/") or not ref.key else ref.key + "/"
    paginator = s3.get_paginator("list_objects_v2")
    count = 0
    for page in paginator.paginate(Bucket=ref.bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            rel = obj["Key"][len(prefix):]
            if not rel or rel.endswith("/"):
                continue  # zero-byte "directory" markers
            target = (dest / rel).resolve()
            if not target.is_relative_to(dest.resolve()):
                raise MaterializeError(f"object key escapes destination: {obj['Key']!r}")
            target.parent.mkdir(parents=True, exist_ok=True)
            s3.download_file(ref.bucket, obj["Key"], str(target))
            count += 1
    if count == 0:
        raise MaterializeError(f"no objects under s3://{ref.bucket}/{prefix}")
    return count


def _download_object(s3, ref: _S3Ref, dest_dir: Path) -> Path:
    name = Path(ref.key).name
    if not name:
        raise MaterializeError(f"object URI has no basename: s3://{ref.bucket}/{ref.key}")
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / name
    s3.download_file(ref.bucket, ref.key, str(target))
    return target


def _stream_url_to(url: str, target: Path) -> None:
    """Stream a presigned http(s) GET URL to an EXACT target path.

    Presigned URLs carry their own SigV2/SigV4 auth in the query string, so no
    credentials are needed — a plain GET. Streamed to disk so multi-GB objects
    don't buffer in memory. Downloads to a ``.partial`` temp and atomically
    renames on success, so a failure NEVER leaves a corrupt/partial file behind.
    Raises ``MaterializeError`` on any failure (the caller decides fatal vs. skip).
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".partial")
    try:
        # nosec B310 - the URL is a presigned GET from the trusted fine-tune request.
        with urllib.request.urlopen(url, timeout=_HTTP_DOWNLOAD_TIMEOUT_S) as resp:  # noqa: S310
            status = getattr(resp, "status", 200)
            if status // 100 != 2:
                raise MaterializeError(f"download failed ({status}) for {_redact(url)}")
            with open(tmp, "wb") as fh:
                shutil.copyfileobj(resp, fh)
        tmp.replace(target)
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        raise MaterializeError(f"failed to download {_redact(url)}: {exc}") from exc
    except MaterializeError:
        tmp.unlink(missing_ok=True)
        raise


def _download_url(url: str, dest_dir: Path) -> Path:
    """Download one presigned object into ``dest_dir`` (filename = URL basename).

    The name is the URL PATH's basename (the signature query string is ignored).
    """
    name = Path(urlparse(url).path).name
    if not name:
        raise MaterializeError(f"presigned URL has no path basename: {_redact(url)}")
    target = dest_dir / name
    _stream_url_to(url, target)
    return target


def _download_manifest(files, dest: Path) -> int:
    """Download every object in a presigned manifest to ``dest/<path>``.

    This is how the FULL snapshot (all data points) is pulled when it is delivered
    as per-object presigned URLs rather than an ``s3://`` prefix — each entry maps
    a workspace-relative ``path`` to a presigned ``url``. Downloads run in a
    bounded thread pool.

    FAIL-SOFT by design: a per-object failure (a bad URL, an expired signature, a
    missing object) is **logged and skipped**, not raised — the run trains on
    whatever downloaded, and the downstream preflight (``validate_training_pool``)
    is the single gate on whether the resulting workspace is usable. Returns the
    count of objects successfully downloaded. Path-traversal entries are skipped
    (never written outside the workspace).
    """
    if not files:
        logger.warning("data_pool.files (presigned snapshot manifest) is empty")
        return 0
    resolved_root = dest.resolve()
    targets: list[tuple[str, Path]] = []
    for f in files:
        target = (dest / f.path).resolve()
        if not target.is_relative_to(resolved_root):
            logger.warning("manifest entry skipped — path escapes the workspace: %r", f.path)
            continue
        targets.append((f.url, target))

    ok = 0
    failed = 0
    with ThreadPoolExecutor(max_workers=_MANIFEST_DOWNLOAD_WORKERS) as ex:
        futures = [ex.submit(_stream_url_to, url, tgt) for url, tgt in targets]
        for fut in as_completed(futures):
            try:
                fut.result()
                ok += 1
            except MaterializeError as exc:
                failed += 1
                logger.warning("snapshot object failed to download (skipped): %s", exc)
    if failed:
        logger.warning(
            "snapshot manifest: %d/%d objects failed and were SKIPPED; training "
            "proceeds on the %d that downloaded (preflight will gate usability)",
            failed,
            len(targets),
            ok,
        )
    return ok


def _redact(url: str) -> str:
    """Drop the query string (presigned signature) before logging a URL."""
    return url.split("?", 1)[0]


# ---------------------------------------------------------------------------
# Credential-free read via the Storage Backend presign endpoint.
#
# When a run carries no storage_access AND DEFT_MATERIALIZE_PRESIGN_URL is set,
# deft resolves its OWN s3:// references to presigned GET URLs — each object is
# deft's own responsibility to pull, no object-store credentials required. This
# is the read-side twin of the presigned PUBLISH path.
# ---------------------------------------------------------------------------


def _presign_mode(storage_access: TemporaryCredentialsStorageAccess | None) -> bool:
    """Use the Storage Backend presign endpoint instead of boto3 for s3://."""
    return storage_access is None and bool(MATERIALIZE_PRESIGN_URL)


def _presign_headers() -> dict[str, str]:
    h = {"Content-Type": "application/json", "Accept": "application/json"}
    if MATERIALIZE_PRESIGN_TOKEN:
        h["Authorization"] = f"Bearer {MATERIALIZE_PRESIGN_TOKEN}"
    return h


def _http_json(url: str, method: str, payload: dict | None = None) -> dict:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    for k, v in _presign_headers().items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=_HTTP_DOWNLOAD_TIMEOUT_S) as resp:
            body = resp.read()
    except (urllib.error.URLError, OSError) as exc:
        raise MaterializeError(f"{method} {url} failed: {exc}") from exc
    try:
        out = json.loads(body)
    except ValueError as exc:
        raise MaterializeError(f"{method} {url} returned non-JSON: {exc}") from exc
    if not isinstance(out, dict):
        raise MaterializeError(f"{method} {url} returned a non-object: {out!r}")
    return out


def _presign_uris(uris: list[str]) -> dict[str, str]:
    """Resolve s3:// URIs to presigned GET URLs via ``POST /v1/get_presigned_urls``.

    Batched at ``_PRESIGN_BATCH`` (the endpoint's per-call cap). Returns
    ``{s3_uri: presigned_url}`` for the ones that succeeded; failures are logged
    and omitted (the caller decides fail-hard vs fail-soft).
    """
    if not MATERIALIZE_PRESIGN_URL:
        raise MaterializeError("DEFT_MATERIALIZE_PRESIGN_URL is not configured")
    out: dict[str, str] = {}
    for i in range(0, len(uris), _PRESIGN_BATCH):
        batch = uris[i : i + _PRESIGN_BATCH]
        data = _http_json(
            MATERIALIZE_PRESIGN_URL,
            "POST",
            {"urls": batch, "expires_in": MATERIALIZE_PRESIGN_EXPIRES_S},
        )
        for item in data.get("items") or []:
            if item.get("input") and item.get("presigned_url"):
                out[item["input"]] = item["presigned_url"]
        for f in data.get("failed") or []:
            logger.warning("presign failed for %s: %s", f.get("input"), f.get("error"))
    return out


def _presign_one(uri: str) -> str:
    """Presign a single s3:// object; raise if the backend could not sign it."""
    urls = _presign_uris([uri])
    if uri not in urls:
        raise MaterializeError(f"could not presign {uri} (Storage Backend returned no URL)")
    return urls[uri]


def _storage_base_url() -> str:
    """Storage Backend origin for GET /v1/snapshot/{id} (defaults to the presign origin)."""
    base = MATERIALIZE_STORAGE_URL
    if not base and MATERIALIZE_PRESIGN_URL:
        p = urlparse(MATERIALIZE_PRESIGN_URL)
        base = f"{p.scheme}://{p.netloc}"
    if not base:
        raise MaterializeError("no Storage Backend URL for snapshot enumeration")
    return base.rstrip("/")


def _snapshot_object_uris(prefix: str) -> list[str]:
    """Enumerate the s3:// objects under a snapshot/KPI ``prefix`` WITHOUT creds.

    The resource id is read from the prefix; GET ``/v1/snapshot/<id>`` (or
    ``/v1/kpi/<id>`` for a KPI set — same ``{manifests[]}`` shape) returns the
    manifest object URIs. The manifests are presigned + downloaded, and every
    s3:// URI referenced inside them (the data objects) is collected. Returns the
    manifest URIs + the data-object URIs.
    """
    ref = _S3Ref.parse(prefix)
    parts = [p for p in ref.key.split("/") if p]
    detail_path, res_id = "/v1/snapshot", None
    for marker, path in (("snapshots", "/v1/snapshot"), ("kpi", "/v1/kpi")):
        if marker in parts and parts.index(marker) + 1 < len(parts):
            res_id, detail_path = parts[parts.index(marker) + 1], path
            break
    if res_id is None:
        res_id = parts[-1] if parts else None
    if not res_id:
        raise MaterializeError(f"cannot derive snapshot/kpi id from prefix {prefix!r}")
    snap_id = res_id

    detail = _http_json(f"{_storage_base_url()}{detail_path}/{snap_id}", "GET")
    manifest_uris = [m["uri"] for m in (detail.get("manifests") or []) if m.get("uri")]
    if not manifest_uris:
        logger.warning("snapshot %s has no manifests; nothing to enumerate", snap_id)
        return []

    # Presign + read the manifests to discover the data-object URIs they list.
    signed = _presign_uris(manifest_uris)
    data_uris: set[str] = set()
    for muri in manifest_uris:
        purl = signed.get(muri)
        if not purl:
            continue
        try:
            with urllib.request.urlopen(purl, timeout=_HTTP_DOWNLOAD_TIMEOUT_S) as resp:
                text = resp.read().decode("utf-8", "replace")
        except (urllib.error.URLError, OSError) as exc:
            logger.warning("manifest %s could not be read: %s", _redact(muri), exc)
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            for v in _iter_s3_uris(rec, ref.bucket):
                data_uris.add(v)
            for key in _manifest_board_keys(rec):
                data_uris.add(f"s3://{ref.bucket}/{key}")
    return manifest_uris + sorted(data_uris)


def _manifest_board_keys(rec: dict) -> list[str]:
    """Bare object-store keys for the board images a manifest record references
    (``file_path`` / ``metadata.objStorePath``). These live outside the snapshot
    prefix and are plain keys — not ``s3://`` URIs or ``{bucket,key}`` pairs — so
    ``_iter_s3_uris`` misses them; the training-pool builder needs the image bytes.
    """
    keys: list[str] = []
    meta = rec.get("metadata") or {}
    for cand in (rec.get("file_path"), meta.get("objStorePath")):
        cand = str(cand or "").strip()
        if cand and not cand.startswith(("s3://", "http://", "https://")):
            keys.append(cand.lstrip("/"))
    return keys


def _iter_s3_uris(obj: object, bucket: str):
    """Yield s3:// URIs referenced anywhere in a manifest record (tolerant of
    schema): explicit ``s3://…`` string values, or ``{bucket,key}`` pairs."""
    if isinstance(obj, str):
        if obj.startswith("s3://"):
            yield obj
    elif isinstance(obj, dict):
        if obj.get("key") and (obj.get("bucket") or bucket):
            yield f"s3://{obj.get('bucket') or bucket}/{str(obj['key']).lstrip('/')}"
        for v in obj.values():
            yield from _iter_s3_uris(v, bucket)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _iter_s3_uris(v, bucket)


def _materialize_prefix_via_presign(prefix: str, dest: Path) -> int:
    """Pull every object under an s3:// snapshot ``prefix`` via presigned GETs.

    Enumerates objects with ``_snapshot_object_uris`` (creds-free), presigns them,
    and downloads each to ``dest/<key-relative-to-prefix>`` (fail-soft per object,
    like the presigned-manifest path). Returns the count downloaded.
    """
    ref = _S3Ref.parse(prefix)
    base_key = ref.key if ref.key.endswith("/") or not ref.key else ref.key + "/"
    uris = _snapshot_object_uris(prefix)
    if not uris:
        raise MaterializeError(f"no objects enumerated for snapshot prefix {prefix}")
    signed = _presign_uris(uris)

    from app.schemas.config_models import PresignedFile

    files: list[PresignedFile] = []
    for uri in uris:
        purl = signed.get(uri)
        if not purl:
            logger.warning("no presigned URL for %s (skipped)", uri)
            continue
        key = _S3Ref.parse(uri).key
        # Objects under the snapshot prefix are stored relative to it; objects
        # outside it (board images referenced by objStorePath) keep their full
        # key so the pool builder finds them at <workspace>/<objStorePath>.
        rel = key[len(base_key):] if key.startswith(base_key) else key.lstrip("/")
        files.append(PresignedFile(path=rel, url=purl))
    return _download_manifest(files, dest)


def materialize(
    cfg: ExperimentConfig,
    storage_access: TemporaryCredentialsStorageAccess | None,
    run_id: str,
) -> ExperimentConfig:
    """Download every s3:// reference locally; return the rewritten config.

    Idempotent per run (dedicated ``<MATERIALIZE_ROOT>/<run_id>`` root). The
    original config object is not mutated.
    """
    updated = cfg.model_copy(deep=True)
    if not needs_materialization(updated):
        return updated

    # Build the boto3 client LAZILY — a pure-presigned (http(s)-only) run needs
    # no S3 credentials or boto3 at all.
    _s3_cache: list = []

    def s3():
        if not _s3_cache:
            _s3_cache.append(_client(storage_access))
        return _s3_cache[0]

    # No temp creds + a presign endpoint configured -> resolve s3:// through the
    # Storage Backend (credential-free); otherwise use boto3.
    use_presign = _presign_mode(storage_access)

    run_root = Path(MATERIALIZE_ROOT) / str(run_id)

    # 1. Snapshot -> local workspace root. Three shapes, in priority order:
    #    (a) a presigned per-object manifest (data_pool.files) — every data point
    #        downloaded to workspace/<path>; (b) an s3:// prefix — mirrored; (c) a
    #        presigned http(s) `path`, which is REJECTED because a single URL can
    #        not represent a whole prefix (use `files` instead).
    if updated.data_pool.files:
        ws_dir = run_root / "workspace"
        n = _download_manifest(updated.data_pool.files, ws_dir)
        logger.info("materialized snapshot manifest -> %s (%d data points)", ws_dir, n)
        updated.data_pool.path = str(ws_dir)
        updated.data_pool.files = None  # consumed; the config now points at the local workspace
    elif _is_http(updated.data_pool.path):
        raise MaterializeError(
            "data_pool.path is a single presigned http(s) URL, but a snapshot is a "
            "directory of many objects — one presigned URL cannot mirror a prefix. "
            "Pass an s3:// prefix (with storage_access), or the per-object presigned "
            "manifest as data_pool.files."
        )
    elif updated.data_pool.path.startswith("s3://"):
        ws_dir = run_root / "workspace"
        if use_presign:
            n = _materialize_prefix_via_presign(updated.data_pool.path, ws_dir)
            logger.info(
                "materialized snapshot %s -> %s (%d objects, presigned)",
                updated.data_pool.path,
                ws_dir,
                n,
            )
        else:
            n = _download_prefix(s3(), _S3Ref.parse(updated.data_pool.path), ws_dir)
            logger.info(
                "materialized snapshot %s -> %s (%d objects)", updated.data_pool.path, ws_dir, n
            )
        updated.data_pool.path = str(ws_dir)

    workspace_root = Path(updated.data_pool.path)

    # 2. Baseline checkpoint -> INSIDE the workspace (containers only mount the
    #    workspace, so the weights must live under it). s3:// with a trailing
    #    slash marks a multi-file model DIRECTORY (mirrored); a plain s3:// key
    #    or a presigned http(s) URL is a single checkpoint object.
    ckpt = updated.model.checkpoint
    if _is_http(ckpt):
        target = _download_url(ckpt, workspace_root / "checkpoints")
        logger.info("materialized checkpoint %s -> %s", _redact(ckpt), target)
        updated.model.checkpoint = str(target.relative_to(workspace_root))
    elif isinstance(ckpt, str) and ckpt.startswith("s3://"):
        ckpt_ref = _S3Ref.parse(ckpt)
        if ckpt.endswith("/"):
            dirname = Path(ckpt_ref.key.rstrip("/")).name or "model"
            target = workspace_root / "checkpoints" / dirname
            if use_presign:
                n = _materialize_prefix_via_presign(ckpt, target)
            else:
                n = _download_prefix(s3(), ckpt_ref, target)
            logger.info("materialized model directory %s -> %s (%d files)", ckpt, target, n)
        elif use_presign:
            target = _download_url(_presign_one(ckpt), workspace_root / "checkpoints")
            logger.info("materialized checkpoint %s -> %s (presigned)", ckpt, target)
        else:
            target = _download_object(s3(), ckpt_ref, workspace_root / "checkpoints")
            logger.info("materialized checkpoint %s -> %s", ckpt, target)
        updated.model.checkpoint = str(target.relative_to(workspace_root))

    # 3. KPI set -> inside the workspace. Two shapes:
    #    (a) an s3:// PREFIX (trailing slash) = a manifest-form KPI set, mirrored to
    #        `kpi_source/`; build_training_pool converts it into kpi/testing_set.csv,
    #        so kpi.path is cleared (the pipeline falls back to that default);
    #    (b) a single CSV object (presigned http(s) or plain s3:// key) -> kpi_sets/.
    kpi = updated.kpi.path if updated.kpi else None
    if updated.kpi and isinstance(kpi, str) and kpi.startswith("s3://") and kpi.endswith("/"):
        kpi_src = workspace_root / "kpi_source"
        if use_presign:
            n = _materialize_prefix_via_presign(kpi, kpi_src)
        else:
            n = _download_prefix(s3(), _S3Ref.parse(kpi), kpi_src)
        logger.info("materialized KPI-set prefix %s -> %s (%d objects)", kpi, kpi_src, n)
        updated.kpi.path = None  # converter emits kpi/testing_set.csv
    elif _is_http(kpi):
        target = _download_url(kpi, workspace_root / "kpi_sets")
        logger.info("materialized KPI set %s -> %s", _redact(kpi), target)
        updated.kpi.path = str(target.relative_to(workspace_root))
    elif updated.kpi and isinstance(kpi, str) and kpi.startswith("s3://"):
        if use_presign:
            target = _download_url(_presign_one(kpi), workspace_root / "kpi_sets")
            logger.info("materialized KPI set %s -> %s (presigned)", kpi, target)
        else:
            target = _download_object(s3(), _S3Ref.parse(kpi), workspace_root / "kpi_sets")
            logger.info("materialized KPI set %s -> %s", kpi, target)
        updated.kpi.path = str(target.relative_to(workspace_root))

    return updated
